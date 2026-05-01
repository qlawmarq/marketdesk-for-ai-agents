"""Sector stock screener — mid-term individual-stock candidate generator.

Takes a sector / theme / factor ETF universe, selects the top-ranked sectors
using ``scripts/sector_score.py``'s pure composite helpers, expands each top
sector ETF into its FMP-provided constituent list, and emits a ranked
multi-factor stock table (momentum x value x quality x forward-looking
consensus). Value and quality are z-scored within GICS sector; momentum,
trend, range position, and analyst upside stay cross-sectional across the
whole resolved basket. Every OpenBB call is pinned to ``provider="fmp"``
(Starter+ tier).

Usage:
    uv run scripts/sector_stock_screener.py --universe sector-spdr
    uv run scripts/sector_stock_screener.py --universe sector-spdr --top-sectors 3 --top-stocks-per-sector 20
"""

from __future__ import annotations

import argparse
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean, stdev
from typing import Any, Literal

from _common import ErrorCategory, aggregate_emit, emit_error, safe_call
from _env import apply_to_openbb
from openbb import obb
from sector_score import (
    UNIVERSES,
    _classify_ticker_failure,
    build_scores,
)

apply_to_openbb()


# ---------------------------------------------------------------------------
# Single-provider literal (Req 2.1)
# ---------------------------------------------------------------------------

_FMP: Literal["fmp"] = "fmp"


# ---------------------------------------------------------------------------
# FMP-native metric-name aliases (Req 5.2)
# ---------------------------------------------------------------------------

_FMP_METRIC_ALIASES: dict[str, str] = {
    "ev_to_ebitda": "enterprise_to_ebitda",
    "return_on_equity": "roe",
    "free_cash_flow_yield": "fcf_yield",
}


# ---------------------------------------------------------------------------
# SPDR sector ETF → GICS sector map (Req 5.7)
# ---------------------------------------------------------------------------

_SPDR_GICS_SECTOR: dict[str, str] = {
    "XLK": "Information Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}


# ---------------------------------------------------------------------------
# Non-US exchange-suffix filter (Req 4.8)
#
# Rationale: the original ``r"\.[A-Z]{1,3}$"`` catch-all pattern would
# drop US-listed class shares whose tickers carry a letter suffix
# (``BRK.A``, ``BRK.B``, ``BF.B``, ``GEF.B``, ``LEN.B``, …), silently
# corrupting the Financials / Materials / Consumer basket z-scores the
# wrapper is supposed to produce. Enumerating the actual non-US exchange
# suffixes that FMP Starter+ rejects with HTTP 402 lets class shares pass
# through while still filtering the HK / TSE / LSE / Euronext / ASX / SIX
# listings that caused the 402s in the first place.
#
# Sources for the suffix list: FMP "Market Identifier Codes" doc + OpenBB
# 4.x ``etf.holdings`` empirical output across XLK/XLF/XLV/XLI/XLP/XLY
# holdings under the theme-ark and global-factor universes.
# ---------------------------------------------------------------------------

_NON_US_EXCHANGE_SUFFIXES: frozenset[str] = frozenset(
    {
        # East Asia
        "HK",   # Hong Kong
        "T",    # Tokyo
        "TYO",  # Tokyo (alternate)
        "KS",   # Korea Exchange KOSPI
        "KQ",   # Korea Exchange KOSDAQ
        "SS",   # Shanghai
        "SZ",   # Shenzhen
        "TW",   # Taiwan
        "SI",   # Singapore
        "JK",   # Jakarta
        "BK",   # Bangkok
        "KL",   # Kuala Lumpur
        # South Asia
        "NS",   # NSE India
        "BO",   # BSE India
        # Oceania
        "AX",   # ASX Australia
        "NZ",   # NZX
        # Europe
        "L",    # LSE
        "PA",   # Euronext Paris
        "AS",   # Euronext Amsterdam
        "BR",   # Euronext Brussels
        "LS",   # Euronext Lisbon
        "MI",   # Borsa Italiana
        "DE",   # XETRA / Deutsche Börse
        "F",    # Frankfurt
        "MU",   # Munich
        "VI",   # Vienna
        "SW",   # SIX Swiss
        "ST",   # Stockholm
        "CO",   # Copenhagen
        "HE",   # Helsinki
        "OL",   # Oslo
        "IR",   # Dublin (ISE)
        "MC",   # Madrid
        "WA",   # Warsaw
        "PR",   # Prague
        "BU",   # Budapest
        "AT",   # Athens
        "IS",   # Istanbul
        # Americas (non-US)
        "TO",   # Toronto
        "V",    # Toronto Venture
        "SA",   # B3 São Paulo
        "MX",   # Bolsa Mexicana
        "SN",   # Santiago
        "BA",   # Buenos Aires
        # Middle East / Africa
        "TA",   # Tel Aviv
        "JO",   # Johannesburg
        "SAU",  # Saudi Tadawul (FMP uses both ``.SAU`` and ``.SR``)
        "SR",   # Saudi Tadawul alternate
    }
)

_NON_US_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"\.(?:" + "|".join(sorted(_NON_US_EXCHANGE_SUFFIXES, key=len, reverse=True)) + r")$"
)


def _has_non_us_suffix(symbol: str) -> bool:
    """Return True iff ``symbol`` carries a known non-US exchange suffix.

    Symbols without any ``.`` suffix (the common US case) return False.
    Class-share suffixes (``BRK.A``, ``BRK.B``, ``BF.B``, ``GEF.B``, …)
    also return False because their single-letter suffix is not in the
    non-US suffix allowlist — the primary regression driver the previous
    catch-all pattern produced (Task 10.2 audit of Req 4.8).
    """

    return _NON_US_SUFFIX_RE.search(symbol) is not None


# ---------------------------------------------------------------------------
# Analyst-coverage window and threshold (Req 5.4, 6.4)
# ---------------------------------------------------------------------------

_ANALYST_COUNT_WINDOW_DAYS = 90
_ANALYST_COVERAGE_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Cross-sectional sample-size floors and default CLI echoes (Req 7.7, 7.8,
# 3.2, 4.2, 14.3)
# ---------------------------------------------------------------------------

_MIN_BASKET_SIZE = 3
DEFAULT_TOP_SECTORS = 3
DEFAULT_TOP_STOCKS_PER_SECTOR = 20
DEFAULT_PRICE_TARGET_LIMIT = 200


# ---------------------------------------------------------------------------
# Sub-score signal keys — four entries, no analyst-revision-momentum key
# is structurally reachable (Req 11.5).
# ---------------------------------------------------------------------------

SUBSCORE_SIGNAL_KEYS: dict[str, tuple[str, ...]] = {
    "momentum_z": ("clenow_90", "ma200_distance"),
    "value_z": ("ev_ebitda_yield", "inv_range_pct_52w"),
    "quality_z": ("roe",),
    "forward_z": ("target_upside",),
}


# ---------------------------------------------------------------------------
# Factor → normalization-scope map (Req 7.1, 7.2)
# ---------------------------------------------------------------------------

_NORMALIZATION_SCOPE: dict[str, Literal["sector_neutral", "basket"]] = {
    "ev_ebitda_yield": "sector_neutral",
    "roe": "sector_neutral",
    "clenow_90": "basket",
    "inv_range_pct_52w": "basket",
    "ma200_distance": "basket",
    "target_upside": "basket",
}


# ---------------------------------------------------------------------------
# Fixed sub-score internal weights (Req 7.4)
# ---------------------------------------------------------------------------

_SUBSCORE_INTERNAL_WEIGHTS: dict[str, dict[str, float]] = {
    "momentum_z": {"clenow_90": 0.5, "ma200_distance": 0.5},
    "value_z": {"ev_ebitda_yield": 0.5, "inv_range_pct_52w": 0.5},
    "quality_z": {"roe": 1.0},
    "forward_z": {"target_upside": 1.0},
}


# ---------------------------------------------------------------------------
# Base analytical caveats (Req 10.6). The
# ``"non_us_tickers_filtered_from_pool"`` string is appended conditionally
# by the envelope assembler when Req 4.8's non-US filter actually dropped
# a row, and is intentionally absent from this base tuple.
# ---------------------------------------------------------------------------

ANALYTICAL_CAVEATS_BASE: tuple[str, ...] = (
    "scores_are_basket_internal_ranks_not_absolute_strength",
    "value_and_quality_are_sector_neutral_z_scores",
    "momentum_and_forward_are_basket_wide_z_scores",
    "etf_holdings_may_lag_spot_by_up_to_one_week",
    "forward_score_requires_number_of_analysts_ge_5",
    "number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions",
)


# ---------------------------------------------------------------------------
# Closed catalog of per-row ``data_quality_flags[]`` entries (Req 10.7).
# ``"non_us_tickers_filtered_from_pool"`` is deliberately absent — that
# string lives on ``data.analytical_caveats`` only (Req 10.7 last sentence).
# ---------------------------------------------------------------------------

DATA_QUALITY_FLAGS: frozenset[str] = frozenset(
    {
        "last_price_from_prev_close",
        "last_price_unavailable",
        "ev_ebitda_non_positive",
        "analyst_coverage_too_thin",
        "sector_group_too_small_for_neutral_z(ev_ebitda_yield)",
        "sector_group_too_small_for_neutral_z(roe)",
        "basket_too_small_for_z(clenow_90)",
        "basket_too_small_for_z(range_pct_52w)",
        "basket_too_small_for_z(ma200_distance)",
        "basket_too_small_for_z(target_upside)",
        "stock_appears_in_multiple_top_sectors",
    }
)


# ---------------------------------------------------------------------------
# Quality-flag append with closed-catalog validation (Task 1.2 — Req 10.7)
# ---------------------------------------------------------------------------


def append_quality_flag(flags: list[str], flag: str) -> None:
    """Append ``flag`` to ``flags`` after validating catalog membership.

    Unknown strings — including ``"non_us_tickers_filtered_from_pool"``,
    which lives on ``data.analytical_caveats`` only — raise
    ``ValueError`` at append time so the closed-enumeration contract
    holds structurally rather than by convention.
    """

    if flag not in DATA_QUALITY_FLAGS:
        raise ValueError(
            f"{flag!r} is not a member of DATA_QUALITY_FLAGS; adding a new "
            "flag requires updating the closed enumeration per Req 10.7"
        )
    flags.append(flag)


# ---------------------------------------------------------------------------
# Task 2.1 — CLI input resolver + validated configuration dataclass
# (Req 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 3.2, 3.5, 4.2, 7.6)
# ---------------------------------------------------------------------------


_TOOL = "sector_stock_screener"
_JP_SECTOR_KEY = "jp-sector"
_TOP_SECTORS_MIN = 1
_TOP_SECTORS_MAX = 11
_TOP_STOCKS_MIN = 1
_TOP_STOCKS_MAX = 100


@dataclass(frozen=True)
class SectorRankWeights:
    clenow_90: float
    clenow_180: float
    return_6m: float
    return_3m: float
    return_12m: float
    risk_adj: float


@dataclass(frozen=True)
class TopLevelWeights:
    momentum: float
    value: float
    quality: float
    forward: float


@dataclass(frozen=True)
class ScreenerConfig:
    universe_key: str
    etfs: list[str]
    top_sectors: int
    top_stocks_per_sector: int
    sector_weights: SectorRankWeights
    subscore_weights: TopLevelWeights


class _ConfigError(ValueError):
    """Raised when argv validation fails after argparse accepted the raw shape."""


def _parse_ticker_csv(csv: str) -> list[str]:
    """Split a comma-separated ticker string preserving input order and
    deduplicating on first-seen (Req 1.2). Tokens are stripped and
    upper-cased; empty / whitespace-only tokens are dropped."""

    seen: set[str] = set()
    out: list[str] = []
    for raw in csv.split(","):
        token = raw.strip().upper()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--universe",
        default=None,
        choices=list(UNIVERSES.keys()),
        help="ETF universe key resolved via sector_score.UNIVERSES.",
    )
    source.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ETF tickers. Mutually exclusive with --universe.",
    )

    parser.add_argument(
        "--top-sectors",
        type=int,
        default=DEFAULT_TOP_SECTORS,
        help=f"Number of top sectors to expand (default {DEFAULT_TOP_SECTORS}, "
        f"bounded [{_TOP_SECTORS_MIN}, {_TOP_SECTORS_MAX}]).",
    )
    parser.add_argument(
        "--top-stocks-per-sector",
        type=int,
        default=DEFAULT_TOP_STOCKS_PER_SECTOR,
        help=f"Number of top constituents per sector ETF (default "
        f"{DEFAULT_TOP_STOCKS_PER_SECTOR}, bounded [{_TOP_STOCKS_MIN}, "
        f"{_TOP_STOCKS_MAX}]).",
    )

    # Sector-score weights — defaults match sector_score.py (Req 3.5).
    parser.add_argument("--weight-clenow-90", type=float, default=0.25)
    parser.add_argument("--weight-clenow-180", type=float, default=0.25)
    parser.add_argument("--weight-return-6m", type=float, default=0.20)
    parser.add_argument("--weight-return-3m", type=float, default=0.15)
    parser.add_argument("--weight-return-12m", type=float, default=0.10)
    parser.add_argument("--weight-risk-adj", type=float, default=0.05)

    # Top-level sub-score weights — 0.25 each by default (Req 7.6).
    parser.add_argument("--weight-sub-momentum", type=float, default=0.25)
    parser.add_argument("--weight-sub-value", type=float, default=0.25)
    parser.add_argument("--weight-sub-quality", type=float, default=0.25)
    parser.add_argument("--weight-sub-forward", type=float, default=0.25)

    return parser


def _validate_bounds(args: argparse.Namespace) -> None:
    if not (_TOP_SECTORS_MIN <= args.top_sectors <= _TOP_SECTORS_MAX):
        raise _ConfigError(
            f"--top-sectors must be in [{_TOP_SECTORS_MIN}, {_TOP_SECTORS_MAX}]; "
            f"got {args.top_sectors}"
        )
    if not (_TOP_STOCKS_MIN <= args.top_stocks_per_sector <= _TOP_STOCKS_MAX):
        raise _ConfigError(
            f"--top-stocks-per-sector must be in [{_TOP_STOCKS_MIN}, "
            f"{_TOP_STOCKS_MAX}]; got {args.top_stocks_per_sector}"
        )


def build_config(argv: list[str] | None = None) -> ScreenerConfig:
    """Parse argv, validate, and resolve the ETF universe.

    On any validation failure raises ``_ConfigError`` so the caller can
    route the failure through ``emit_error`` with a non-zero exit code
    before any OpenBB call is issued (Req 1.3, 1.4, 1.5, 1.6, 2.1).
    """

    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_bounds(args)

    if args.universe is not None:
        if args.universe == _JP_SECTOR_KEY:
            raise _ConfigError(
                "--universe jp-sector is not supported in MVP; FMP Starter+ "
                "does not cover TSE-listed ETFs cleanly"
            )
        universe_key = args.universe
        etfs = list(UNIVERSES[args.universe])
    else:
        universe_key = "custom"
        etfs = _parse_ticker_csv(args.tickers or "")
        if not etfs:
            raise _ConfigError(
                "--tickers resolved to an empty ETF list after deduplication"
            )

    sector_weights = SectorRankWeights(
        clenow_90=args.weight_clenow_90,
        clenow_180=args.weight_clenow_180,
        return_6m=args.weight_return_6m,
        return_3m=args.weight_return_3m,
        return_12m=args.weight_return_12m,
        risk_adj=args.weight_risk_adj,
    )
    subscore_weights = TopLevelWeights(
        momentum=args.weight_sub_momentum,
        value=args.weight_sub_value,
        quality=args.weight_sub_quality,
        forward=args.weight_sub_forward,
    )

    return ScreenerConfig(
        universe_key=universe_key,
        etfs=etfs,
        top_sectors=args.top_sectors,
        top_stocks_per_sector=args.top_stocks_per_sector,
        sector_weights=sector_weights,
        subscore_weights=subscore_weights,
    )


# ---------------------------------------------------------------------------
# Task 2.2 — FMP credential gate (Req 2.1, 2.2)
# ---------------------------------------------------------------------------


def check_fmp_credential() -> int:
    """Fail fast when ``FMP_API_KEY`` is unset or empty.

    Returns ``0`` when the credential is present, or ``2`` after emitting
    a credential-category error envelope. The caller (``main``) issues
    this check after ``build_config`` returns and before any OpenBB call
    is attempted.
    """

    value = os.environ.get("FMP_API_KEY")
    if value is None or not value.strip():
        return emit_error(
            "FMP_API_KEY is required for sector-stock-screener "
            "(FMP Starter+ tier)",
            tool=_TOOL,
            error_category=ErrorCategory.CREDENTIAL.value,
        )
    return 0


# ---------------------------------------------------------------------------
# Task 3 — Sector-rank layer (FMP-native perf + Clenow → sector_score helpers)
# (Req 3.1, 3.2, 3.3, 3.4, 3.5, 15.3)
# ---------------------------------------------------------------------------


_SECTOR_LOOKBACK_DAYS = 420
_CLENOW_WINDOWS: tuple[int, ...] = (90, 180)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _split_history_by_symbol(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Split a stacked batched-historical frame by ``row["symbol"]``.

    Rows without a ``symbol`` field are dropped defensively (research G6:
    batched-row order / shape is not guaranteed).
    """

    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sym = row.get("symbol")
        if not sym:
            continue
        out.setdefault(str(sym), []).append(row)
    return out


def _compute_perf_record_from_rows(
    symbol: str, rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Build a ``sector_score``-shaped perf record from FMP historical rows.

    Produces ``{symbol, one_month, three_month, six_month, one_year,
    volatility_month, price, source}``. Returns ``None`` when the row list
    is empty — that is "no data", not an error (Req 2 data-vs-error
    separation).
    """

    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r.get("date") or "")
    closes: list[float] = []
    for r in ordered:
        c = _to_float(r.get("close"))
        if c is not None:
            closes.append(c)
    if not closes:
        return None
    last_price = closes[-1]

    def ret(days: int) -> float | None:
        if len(closes) < days + 1:
            return None
        past = closes[-(days + 1)]
        if past == 0:
            return None
        return last_price / past - 1.0

    monthly: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev == 0:
            continue
        monthly.append(closes[i] / prev - 1.0)
    tail = monthly[-21:]
    # Parity with ``sector_score.py:177-178``: sample stdev (ddof=1) of
    # the last 21 daily returns, no annualization. ``risk_adj`` in
    # ``build_scores`` consumes identical units on both paths — research
    # G7. Do not add annualization here without updating both files.
    vol_month = float(stdev(tail)) if len(tail) >= 2 else None

    return {
        "symbol": symbol,
        "one_month": ret(21),
        "three_month": ret(63),
        "six_month": ret(126),
        "one_year": ret(252),
        "volatility_month": vol_month,
        "price": last_price,
        "source": "fmp-computed",
    }


def fetch_sector_performance_fmp(
    etfs: list[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Issue one batched FMP ``equity.price.historical`` across ``etfs``.

    The stacked long-form frame is split by ``row["symbol"]`` and reduced
    to the same ``sector_score``-compatible shape (``one_month`` /
    ``three_month`` / ``six_month`` / ``one_year`` / ``volatility_month``).
    Returns ``(perf_by_symbol, provider_diagnostics)`` where the
    diagnostics list carries ``{provider, stage: "sector_historical_fmp",
    error, error_category}`` entries when the batched call failed. A
    per-ETF absence from the response (partial data) is **not** a
    diagnostic — the row simply stays out of the map and
    ``sector_score._classify_ticker_failure`` handles the classification
    downstream (Req 15.3).
    """

    start = (date.today() - timedelta(days=_SECTOR_LOOKBACK_DAYS)).isoformat()
    call = safe_call(
        lambda: obb.equity.price.historical(
            symbol=",".join(etfs), start_date=start, provider=_FMP
        )
    )
    if not call.get("ok"):
        return (
            {},
            [
                {
                    "provider": _FMP,
                    "stage": "sector_historical_fmp",
                    "error": call.get("error"),
                    "error_category": call.get("error_category"),
                }
            ],
        )

    rows = call.get("records") or []
    by_symbol = _split_history_by_symbol(rows)
    perf: dict[str, dict[str, Any]] = {}
    for etf in etfs:
        record = _compute_perf_record_from_rows(etf, by_symbol.get(etf, []))
        if record is not None:
            perf[etf] = record
    return perf, []


def fetch_sector_clenow_fmp(
    etf: str, *, lookback_days: int = _SECTOR_LOOKBACK_DAYS
) -> dict[str, Any]:
    """Compute both Clenow windows for one ETF from one historical fetch.

    Issues one HTTP call (``equity.price.historical``) and runs
    ``technical.clenow`` locally twice (``period=90`` and ``period=180``)
    against the same fetched bars — no second HTTP round-trip.

    Success shape: ``{"ok": True, "clenow_90": float|None, "clenow_180":
    float|None}``. Failure shape mirrors ``safe_call``'s record with the
    additional ``stage`` discriminator so the caller can emit a stage-level
    diagnostic.
    """

    start = (date.today() - timedelta(days=lookback_days)).isoformat()

    hist_capture: dict[str, Any] = {"obj": None}

    def _historical_call() -> Any:
        obj = obb.equity.price.historical(
            symbol=etf, start_date=start, provider=_FMP
        )
        hist_capture["obj"] = obj
        return obj

    hist_call = safe_call(_historical_call)
    if not hist_call.get("ok"):
        return {
            "ok": False,
            "stage": "sector_clenow_historical",
            "error": hist_call.get("error"),
            "error_type": hist_call.get("error_type"),
            "error_category": hist_call.get("error_category"),
        }

    results_ref = (
        hist_capture["obj"].results if hist_capture["obj"] is not None else None
    )
    if results_ref is None or not hist_call.get("records"):
        return {"ok": True, "clenow_90": None, "clenow_180": None}

    factors: dict[str, float | None] = {}
    for period in _CLENOW_WINDOWS:
        call = safe_call(
            lambda period: obb.technical.clenow(
                data=results_ref, target="close", period=period
            ),
            period=period,
        )
        if not call.get("ok"):
            factors[f"clenow_{period}"] = None
            continue
        recs = call.get("records") or []
        if not recs:
            factors[f"clenow_{period}"] = None
            continue
        factors[f"clenow_{period}"] = _to_float(recs[-1].get("factor"))

    return {
        "ok": True,
        "clenow_90": factors.get("clenow_90"),
        "clenow_180": factors.get("clenow_180"),
    }


@dataclass(frozen=True)
class SectorRankRow:
    ticker: str
    rank: int | None
    composite_score_0_100: float | None
    composite_z: float | None
    ok: bool
    error: str | None = None
    error_type: str | None = None
    error_category: str | None = None


def _sector_weights_to_dict(w: SectorRankWeights) -> dict[str, float]:
    return {
        "clenow_90": w.clenow_90,
        "clenow_180": w.clenow_180,
        "return_6m": w.return_6m,
        "return_3m": w.return_3m,
        "return_12m": w.return_12m,
        "risk_adj": w.risk_adj,
    }


def run_sector_rank(
    cfg: ScreenerConfig,
) -> tuple[list[SectorRankRow], list[dict[str, Any]]]:
    """Fetch FMP-native perf + Clenow, delegate composite to ``build_scores``.

    Returns ``(ranked_rows, provider_diagnostics)``. Rows are ordered by
    ``rank`` ascending with null ranks sinking to the tail. Provider
    diagnostics carry stage-level failures (``sector_historical_fmp`` from
    the batched perf call, ``sector_clenow_historical`` from the per-ETF
    Clenow historical calls).
    """

    perf, diagnostics = fetch_sector_performance_fmp(cfg.etfs)

    clenow_90: dict[str, dict[str, Any]] = {}
    clenow_180: dict[str, dict[str, Any]] = {}
    for etf in cfg.etfs:
        result = fetch_sector_clenow_fmp(etf)
        if not result.get("ok"):
            diagnostics.append(
                {
                    "provider": _FMP,
                    "stage": "sector_clenow_historical",
                    "symbol": etf,
                    "error": result.get("error"),
                    "error_category": result.get("error_category"),
                }
            )
            failure = {
                "ok": False,
                "error": result.get("error"),
                "error_type": result.get("error_type"),
                "error_category": result.get("error_category"),
            }
            clenow_90[etf] = failure
            clenow_180[etf] = failure
            continue
        clenow_90[etf] = {"ok": True, "factor": result.get("clenow_90")}
        clenow_180[etf] = {"ok": True, "factor": result.get("clenow_180")}

    records = build_scores(
        cfg.etfs, perf, clenow_90, clenow_180, _sector_weights_to_dict(cfg.sector_weights)
    )

    rows: list[SectorRankRow] = []
    for rec in records:
        ticker = rec["ticker"]
        failure = _classify_ticker_failure(ticker, perf, clenow_90, clenow_180)
        if failure is None:
            rows.append(
                SectorRankRow(
                    ticker=ticker,
                    rank=rec.get("rank"),
                    composite_score_0_100=rec.get("composite_score_0_100"),
                    composite_z=rec.get("composite_z"),
                    ok=True,
                )
            )
        else:
            rows.append(
                SectorRankRow(
                    ticker=ticker,
                    rank=rec.get("rank"),
                    composite_score_0_100=rec.get("composite_score_0_100"),
                    composite_z=rec.get("composite_z"),
                    ok=False,
                    error=failure.get("error"),
                    error_type=failure.get("error_type"),
                    error_category=failure.get("error_category"),
                )
            )

    rows.sort(key=lambda r: (r.rank if r.rank is not None else 10**9))
    return rows, diagnostics


@dataclass(frozen=True)
class TopSectorSelection:
    selected: list[SectorRankRow]
    shortfall_note: str | None


def select_top_sectors(
    ranked: list[SectorRankRow], top_n: int
) -> TopSectorSelection:
    """Pick up to ``top_n`` rows with ``ok=True`` and non-null rank.

    When fewer than ``top_n`` rows qualify, the caller proceeds with the
    available subset and a ``top_sectors_shortfall`` note is surfaced to
    ``data.notes`` (Req 3.3).
    """

    available = [r for r in ranked if r.ok and r.rank is not None]
    available.sort(key=lambda r: r.rank)  # type: ignore[arg-type]
    selected = available[:top_n]
    shortfall_note: str | None = None
    if len(available) < top_n:
        shortfall_note = (
            f"top_sectors_shortfall: requested={top_n}, resolved={len(available)}"
        )
    return TopSectorSelection(selected=selected, shortfall_note=shortfall_note)


def sector_ranks_envelope_rows(
    ranked: list[SectorRankRow],
) -> list[dict[str, Any]]:
    """Shape ``ranked`` into the ``data.sector_ranks[]`` envelope block."""

    return [
        {
            "ticker": r.ticker,
            "rank": r.rank,
            "composite_score_0_100": r.composite_score_0_100,
            "composite_z": r.composite_z,
        }
        for r in ranked
    ]


# ---------------------------------------------------------------------------
# Task 4 — Pool build layer (Req 4.1, 4.3, 4.4, 4.5, 4.6, 4.8, 5.7, 10.6, 10.7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldingsRow:
    etf_ticker: str
    symbol: str
    name: str | None
    weight: float | None
    shares: float | None
    value: float | None
    updated: date | None


def _coerce_updated(value: Any) -> date | None:
    """Coerce FMP ``updated`` into a ``date``.

    Accepts ``datetime``, ``date``, or ISO-formatted string; returns
    ``None`` when the input cannot be parsed.
    """

    if value is None:
        return None
    # datetime is a subclass of date — narrow via ``.date()`` when a
    # ``datetime`` shows up.
    if isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.split("T", 1)[0]).date()
        except (TypeError, ValueError):
            return None
    return None


def fetch_etf_holdings(
    etf_tickers: list[str],
) -> tuple[list[HoldingsRow], list[dict[str, Any]]]:
    """Issue one ``obb.etf.holdings`` call per selected ETF.

    Each call is guarded by ``safe_call``. Per-sector failures surface as
    ``{provider: "fmp", stage: "etf_holdings", symbol: <etf_ticker>,
    error, error_category}`` entries in the returned diagnostics list so
    downstream expansion can continue with the surviving sectors (Req 4.5).
    """

    rows: list[HoldingsRow] = []
    diagnostics: list[dict[str, Any]] = []
    for etf in etf_tickers:
        call = safe_call(
            lambda etf=etf: obb.etf.holdings(symbol=etf, provider=_FMP)
        )
        if not call.get("ok"):
            diagnostics.append(
                {
                    "provider": _FMP,
                    "stage": "etf_holdings",
                    "symbol": etf,
                    "error": call.get("error"),
                    "error_category": call.get("error_category"),
                }
            )
            continue
        for record in call.get("records") or []:
            sym = record.get("symbol")
            if not sym:
                continue
            rows.append(
                HoldingsRow(
                    etf_ticker=etf,
                    symbol=str(sym).upper(),
                    name=record.get("name"),
                    weight=_to_float(record.get("weight")),
                    shares=_to_float(record.get("shares")),
                    value=_to_float(record.get("value")),
                    updated=_coerce_updated(record.get("updated")),
                )
            )
    return rows, diagnostics


@dataclass(frozen=True)
class StockPoolEntry:
    symbol: str
    sector_origins: list[dict[str, Any]]
    quality_flags: list[str]
    gics_sector: str | None = None


@dataclass(frozen=True)
class PoolBuildOutcome:
    pool: list[StockPoolEntry]
    etf_holdings_updated_max_age_days: int | None
    non_us_tickers_filtered: list[dict[str, str]]


def build_pool(
    holdings: list[HoldingsRow],
    cfg: ScreenerConfig,
    today: date,
) -> PoolBuildOutcome:
    """Apply non-US filter, top-M weight slice, dedup into ``sector_origins``.

    Also computes ``etf_holdings_updated_max_age_days`` across every row
    fetched (Task 4.3) and tags each pool entry with its GICS sector via
    ``sector_origins[0].etf_ticker`` (Task 4.4 / Req 5.7).
    """

    # Group rows by ETF (preserving input ETF order).
    by_etf: dict[str, list[HoldingsRow]] = {}
    for row in holdings:
        by_etf.setdefault(row.etf_ticker, []).append(row)

    non_us_filtered: list[dict[str, str]] = []
    per_etf_top: dict[str, list[HoldingsRow]] = {}
    for etf, rows in by_etf.items():
        filtered: list[HoldingsRow] = []
        for row in rows:
            if _NON_US_SUFFIX_RE.search(row.symbol):
                non_us_filtered.append(
                    {"symbol": row.symbol, "etf_ticker": etf}
                )
                continue
            filtered.append(row)
        # Sort by weight descending; null weights sink (keep deterministic).
        filtered.sort(
            key=lambda r: (
                -(r.weight if r.weight is not None else float("-inf"))
            )
        )
        per_etf_top[etf] = filtered[: cfg.top_stocks_per_sector]

    first_seen: dict[str, StockPoolEntry] = {}
    order: list[str] = []
    for etf, top_rows in per_etf_top.items():
        for row in top_rows:
            origin = {
                "etf_ticker": etf,
                "weight_in_etf": row.weight,
                "updated": row.updated,
            }
            if row.symbol not in first_seen:
                first_seen[row.symbol] = StockPoolEntry(
                    symbol=row.symbol,
                    sector_origins=[origin],
                    quality_flags=[],
                    gics_sector=_SPDR_GICS_SECTOR.get(etf),
                )
                order.append(row.symbol)
            else:
                entry = first_seen[row.symbol]
                entry.sector_origins.append(origin)
                if len(entry.sector_origins) == 2:
                    append_quality_flag(
                        entry.quality_flags,
                        "stock_appears_in_multiple_top_sectors",
                    )

    pool = [first_seen[sym] for sym in order]

    # Holdings-freshness: max age in days across every fetched row's
    # non-null ``updated`` (Req 4.6). When every row is null, emit null.
    ages = [
        (today - row.updated).days for row in holdings if row.updated is not None
    ]
    max_age = max(ages) if ages else None

    return PoolBuildOutcome(
        pool=pool,
        etf_holdings_updated_max_age_days=max_age,
        non_us_tickers_filtered=non_us_filtered,
    )


# ---------------------------------------------------------------------------
# Task 5.1 — Symbol-indexing helper (Req 5.5)
# ---------------------------------------------------------------------------


def _index_by_symbol(
    rows: list[dict[str, Any]] | None,
    *,
    multi: bool = False,
) -> dict[str, Any]:
    """Index batched provider rows by ``row["symbol"]``.

    ``multi=False`` returns ``{SYMBOL: row}`` (first-seen wins) for the
    single-row-per-symbol endpoints (quote, metrics, consensus).
    ``multi=True`` returns ``{SYMBOL: [row, ...]}`` preserving provider
    order for multi-row endpoints (price_target). Rows whose ``symbol``
    is missing / empty / whitespace-only are dropped defensively, and
    the key case is normalized to upper-case so it matches the pool
    index (research G6 — batched-row order is not guaranteed; no call
    site uses positional lookup).
    """

    out: dict[str, Any] = {}
    for row in rows or []:
        raw = row.get("symbol")
        if raw is None:
            continue
        key = str(raw).strip().upper()
        if not key:
            continue
        if multi:
            out.setdefault(key, []).append(row)
        elif key not in out:
            out[key] = row
    return out


# ---------------------------------------------------------------------------
# Task 5.2 — Batched per-stock fetchers + logical-field extractors
# (Req 2.1, 5.1, 5.2, 5.4, 14.1, 14.2, 14.3)
# ---------------------------------------------------------------------------


def _extract_quote_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the six logical quote fields.

    FMP-native ``ma200`` / ``ma50`` surface under the logical names
    ``ma_200d`` / ``ma_50d`` (Req 5.1). ``pe_ratio`` and
    ``recommendation_mean`` are never emitted.
    """

    if row is None:
        return {
            "last_price": None,
            "year_high": None,
            "year_low": None,
            "prev_close": None,
            "ma_200d": None,
            "ma_50d": None,
        }
    return {
        "last_price": _to_float(row.get("last_price")),
        "year_high": _to_float(row.get("year_high")),
        "year_low": _to_float(row.get("year_low")),
        "prev_close": _to_float(row.get("prev_close")),
        "ma_200d": _to_float(row.get("ma200")),
        "ma_50d": _to_float(row.get("ma50")),
    }


def _extract_metrics_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    """Extract ``market_cap`` plus the three aliased fields (Req 5.2).

    ``pe_ratio`` / ``gross_margin`` are intentionally not extracted —
    FMP's metrics endpoint does not expose them and no scoring path
    consumes them.
    """

    if row is None:
        return {
            "market_cap": None,
            "enterprise_to_ebitda": None,
            "roe": None,
            "fcf_yield": None,
        }
    out: dict[str, Any] = {"market_cap": _to_float(row.get("market_cap"))}
    for native, logical in _FMP_METRIC_ALIASES.items():
        out[logical] = _to_float(row.get(native))
    return out


def _extract_consensus_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    """Extract ``target_consensus`` and ``target_median`` (Req 5.4).

    ``number_of_analysts`` is deliberately absent — the FMP consensus
    endpoint does not populate it, so it is derived separately from
    ``price_target`` via ``derive_number_of_analysts``.
    """

    if row is None:
        return {"target_consensus": None, "target_median": None}
    return {
        "target_consensus": _to_float(row.get("target_consensus")),
        "target_median": _to_float(row.get("target_median")),
    }


def _batched_fetch(
    call_factory: Any,
    *,
    multi: bool = False,
) -> dict[str, Any]:
    """Shared wiring for the four batched per-stock fetchers.

    Returns ``{"ok": True, "by_symbol": dict}`` on success or the
    ``safe_call`` failure record verbatim. Callers pass a zero-argument
    factory that issues the OpenBB call.
    """

    call = safe_call(call_factory)
    if not call.get("ok"):
        return {
            "ok": False,
            "error": call.get("error"),
            "error_type": call.get("error_type"),
            "error_category": call.get("error_category"),
        }
    by_symbol = _index_by_symbol(call.get("records"), multi=multi)
    return {"ok": True, "by_symbol": by_symbol}


def fetch_quotes_batched(symbols: list[str]) -> dict[str, Any]:
    """One batched ``obb.equity.price.quote`` call (Req 5.1, 14.2)."""

    if not symbols:
        return {"ok": True, "by_symbol": {}}
    csv = ",".join(symbols)
    return _batched_fetch(
        lambda: obb.equity.price.quote(symbol=csv, provider=_FMP)
    )


def fetch_metrics_batched(symbols: list[str]) -> dict[str, Any]:
    """One batched ``obb.equity.fundamental.metrics`` call (Req 5.2, 14.2)."""

    if not symbols:
        return {"ok": True, "by_symbol": {}}
    csv = ",".join(symbols)
    return _batched_fetch(
        lambda: obb.equity.fundamental.metrics(symbol=csv, provider=_FMP)
    )


def fetch_consensus_batched(symbols: list[str]) -> dict[str, Any]:
    """One batched ``obb.equity.estimates.consensus`` call (Req 5.4, 14.1)."""

    if not symbols:
        return {"ok": True, "by_symbol": {}}
    csv = ",".join(symbols)
    return _batched_fetch(
        lambda: obb.equity.estimates.consensus(symbol=csv, provider=_FMP)
    )


def fetch_price_target_batched(
    symbols: list[str], *, limit: int = DEFAULT_PRICE_TARGET_LIMIT
) -> dict[str, Any]:
    """One batched ``obb.equity.estimates.price_target`` call (Req 5.4, 14.3).

    The response carries multiple rows per symbol (each analyst revision
    is a row), so the indexing helper is called with ``multi=True``.
    """

    if not symbols:
        return {"ok": True, "by_symbol": {}}
    csv = ",".join(symbols)
    return _batched_fetch(
        lambda: obb.equity.estimates.price_target(
            symbol=csv, provider=_FMP, limit=limit
        ),
        multi=True,
    )


# ---------------------------------------------------------------------------
# Task 5.3 — Per-symbol Clenow momentum (Req 2.1, 5.3, 14.4)
# ---------------------------------------------------------------------------


_STOCK_CLENOW_LOOKBACK_DAYS = 180


def fetch_stock_clenow_fmp(
    symbol: str, *, lookback_days: int = _STOCK_CLENOW_LOOKBACK_DAYS
) -> dict[str, Any]:
    """One historical fetch + one Clenow reduction per symbol.

    ``obb.technical.clenow`` rejects stacked-index batched historical
    frames (research G1), so the per-symbol loop is the only supported
    shape. Success returns ``{"ok": True, "clenow_90": float|None}``;
    failures on the historical leg carry the safe-call failure record
    with ``stage="stock_clenow_historical"``. Downstream callers treat
    a missing / non-numeric ``factor`` as ``clenow_90: null``.
    """

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    hist_capture: dict[str, Any] = {"obj": None}

    def _historical_call() -> Any:
        obj = obb.equity.price.historical(
            symbol=symbol, start_date=start, provider=_FMP
        )
        hist_capture["obj"] = obj
        return obj

    hist_call = safe_call(_historical_call)
    if not hist_call.get("ok"):
        return {
            "ok": False,
            "stage": "stock_clenow_historical",
            "error": hist_call.get("error"),
            "error_type": hist_call.get("error_type"),
            "error_category": hist_call.get("error_category"),
        }

    results_ref = (
        hist_capture["obj"].results if hist_capture["obj"] is not None else None
    )
    if results_ref is None or not hist_call.get("records"):
        return {"ok": True, "clenow_90": None}

    call = safe_call(
        lambda: obb.technical.clenow(
            data=results_ref, target="close", period=90
        )
    )
    if not call.get("ok"):
        return {"ok": True, "clenow_90": None}
    recs = call.get("records") or []
    if not recs:
        return {"ok": True, "clenow_90": None}
    return {"ok": True, "clenow_90": _to_float(recs[-1].get("factor"))}


# ---------------------------------------------------------------------------
# Task 5.4 — Derive number_of_analysts from price_target (Req 5.4, 6.4, 6.5)
# ---------------------------------------------------------------------------


def _coerce_published_date(value: Any) -> date | None:
    """Coerce a ``published_date`` cell into a ``date`` or return ``None``."""

    if value is None:
        return None
    if isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.split("T", 1)[0]).date()
        except (TypeError, ValueError):
            return None
    return None


def derive_number_of_analysts(
    price_target_rows: list[dict[str, Any]] | None,
    today: date,
) -> int | None:
    """Count distinct ``analyst_firm`` entries inside the 90-day window.

    Filters rows whose ``published_date`` lies in ``[today - 90d,
    today]``, excludes rows where ``analyst_firm`` is null, empty, or
    whitespace-only (research G5 — prevents empty-string inflation),
    and returns the count of the remaining distinct firms (whitespace-
    stripped). Returns ``None`` when the price-target fetch failed for
    the symbol (caller passes ``None``).
    """

    if price_target_rows is None:
        return None
    window_start = today - timedelta(days=_ANALYST_COUNT_WINDOW_DAYS)
    firms: set[str] = set()
    for row in price_target_rows:
        published = _coerce_published_date(row.get("published_date"))
        if published is None:
            continue
        if published < window_start or published > today:
            continue
        firm_raw = row.get("analyst_firm")
        if firm_raw is None:
            continue
        firm = str(firm_raw).strip()
        if not firm:
            continue
        firms.add(firm)
    return len(firms)


# ---------------------------------------------------------------------------
# Task 5.5 — Last-price fallback chain (Req 5.6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LastPriceResolution:
    """Two-rung fallback result.

    ``flag`` is ``None`` iff rung 1 (``quote.last_price``) succeeded;
    otherwise it carries the ``data_quality_flags[]`` string the caller
    should append. Both flag strings are members of ``DATA_QUALITY_FLAGS``
    so ``append_quality_flag`` accepts them without raising.
    """

    value: float | None
    flag: Literal[
        None, "last_price_from_prev_close", "last_price_unavailable"
    ]


def resolve_last_price(quote_row: dict[str, Any] | None) -> LastPriceResolution:
    """Resolve ``last_price`` via ``quote.last_price → prev_close → null``.

    Rung-2 success appends ``"last_price_from_prev_close"`` to the
    per-row flag list; both rungs missing appends
    ``"last_price_unavailable"`` and the returned value is ``None``.
    """

    if quote_row is None:
        return LastPriceResolution(value=None, flag="last_price_unavailable")
    last = _to_float(quote_row.get("last_price"))
    if last is not None:
        return LastPriceResolution(value=last, flag=None)
    prev = _to_float(quote_row.get("prev_close"))
    if prev is not None:
        return LastPriceResolution(value=prev, flag="last_price_from_prev_close")
    return LastPriceResolution(value=None, flag="last_price_unavailable")


# ---------------------------------------------------------------------------
# Task 6.1 — Derived indicators (Req 6.1, 6.2, 6.3, 6.4, 6.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedIndicators:
    range_pct_52w: float | None
    ma200_distance: float | None
    ev_ebitda_yield: float | None
    target_upside: float | None
    extra_flags: list[str]


def compute_derived_indicators(
    *,
    last_price: float | None,
    year_high: float | None,
    year_low: float | None,
    ma_200d: float | None,
    enterprise_to_ebitda: float | None,
    target_consensus: float | None,
    number_of_analysts: int | None,
) -> DerivedIndicators:
    """Derive the four indicators computed locally from fetched fields.

    - ``range_pct_52w`` requires all three 52-week inputs and a non-zero
      denominator (Req 6.1).
    - ``ma200_distance`` requires both inputs and ``ma_200d != 0`` (Req 6.2).
    - ``ev_ebitda_yield`` inverts ``enterprise_to_ebitda`` when strictly
      positive; zero / negative / missing emits ``None`` + the
      ``ev_ebitda_non_positive`` flag (Req 6.3).
    - ``target_upside`` requires ``target_consensus``, ``last_price``, and
      ``number_of_analysts >= 5`` (Req 6.4); thin coverage emits ``None`` +
      the ``analyst_coverage_too_thin`` flag (Req 6.5).
    """

    flags: list[str] = []

    # Req 6.1 — 52-week range position
    range_pct: float | None = None
    if (
        last_price is not None
        and year_high is not None
        and year_low is not None
    ):
        denom = year_high - year_low
        if denom != 0:
            range_pct = (last_price - year_low) / denom

    # Req 6.2 — 200-day trend distance
    ma200_dist: float | None = None
    if last_price is not None and ma_200d is not None and ma_200d != 0:
        ma200_dist = (last_price - ma_200d) / ma_200d

    # Req 6.3 — EV/EBITDA yield (inverted, positive only)
    ev_yield: float | None = None
    if enterprise_to_ebitda is None or enterprise_to_ebitda <= 0:
        flags.append("ev_ebitda_non_positive")
    else:
        ev_yield = 1.0 / enterprise_to_ebitda

    # Req 6.4 / 6.5 — analyst-coverage-gated upside
    upside: float | None = None
    if (
        target_consensus is not None
        and last_price is not None
        and number_of_analysts is not None
        and number_of_analysts >= _ANALYST_COVERAGE_THRESHOLD
        and last_price != 0
    ):
        upside = (target_consensus - last_price) / last_price
    elif (
        number_of_analysts is not None
        and number_of_analysts < _ANALYST_COVERAGE_THRESHOLD
    ):
        flags.append("analyst_coverage_too_thin")

    return DerivedIndicators(
        range_pct_52w=range_pct,
        ma200_distance=ma200_dist,
        ev_ebitda_yield=ev_yield,
        target_upside=upside,
        extra_flags=flags,
    )


# ---------------------------------------------------------------------------
# Task 6.2 — Minimum-basket z-score helpers (Req 7.1, 7.2, 7.7, 7.8, 4.7)
# ---------------------------------------------------------------------------


def _zscore_min_basket(values: list[float | None]) -> list[float | None]:
    """Cross-sectional z-score with ``min_basket=3`` (Req 7.8 / 4.7).

    Emits ``None`` on every row when the clean sample is below
    ``_MIN_BASKET_SIZE``; returns ``0.0`` on every non-null row when
    dispersion is zero; propagates ``None`` input through to ``None``
    output.
    """

    clean = [v for v in values if v is not None]
    if len(clean) < _MIN_BASKET_SIZE:
        return [None] * len(values)
    m = mean(clean)
    sd = stdev(clean)
    if sd == 0:
        return [0.0 if v is not None else None for v in values]
    return [(v - m) / sd if v is not None else None for v in values]


def _zscore_min_basket_sector_neutral(
    values: list[float | None],
    sector_tags: list[str | None],
    factor_name: str,
) -> tuple[list[float | None], list[float | None], list[bool]]:
    """Sector-neutral variant of ``_zscore_min_basket`` (Req 7.1, 7.7).

    Groups rows by ``sector_tags`` and applies ``_zscore_min_basket``
    within each group. Rows whose group has fewer than
    ``_MIN_BASKET_SIZE`` non-null values — including rows whose
    ``sector_tags`` entry is ``None`` — fall back to a basket-wide z
    computed once across the full input.

    Returns ``(z_sector_neutral, z_basket, fell_back_flags)`` — three
    parallel per-row lists. Exactly one of the first two is populated
    per row when a numeric z exists; the other is ``None``. The third
    is a ``bool`` per row: ``True`` when the row fell back to basket
    scope (caller appends ``sector_group_too_small_for_neutral_z(<F>)``).

    When the entire basket also has fewer than three non-null values,
    every row's z is ``None`` across both keys — the caller is
    responsible for appending ``basket_too_small_for_z(<F>)``; that
    bookkeeping is shared with plain basket-wide factors.
    """

    n = len(values)
    assert len(sector_tags) == n, "values and sector_tags must be same length"
    del factor_name  # consumed by caller for flag naming

    # Basket-wide z across the full input (used as fallback and also as the
    # _basket slot for rows that fell back).
    z_basket_full = _zscore_min_basket(values)

    # Group row indices by sector tag (None sector => immediate fallback).
    groups: dict[str, list[int]] = {}
    for i, tag in enumerate(sector_tags):
        if tag is None:
            continue
        groups.setdefault(tag, []).append(i)

    z_sector_neutral: list[float | None] = [None] * n
    z_basket_out: list[float | None] = [None] * n
    fell_back: list[bool] = [False] * n

    # Rows without a sector tag or in small groups → basket fallback.
    fallback_indices: set[int] = set()
    for i, tag in enumerate(sector_tags):
        if tag is None:
            fallback_indices.add(i)
            continue
        group_clean = [
            values[j] for j in groups[tag] if values[j] is not None
        ]
        if len(group_clean) < _MIN_BASKET_SIZE:
            fallback_indices.add(i)

    # Sector-neutral path: compute per-group z for rows not in fallback set.
    for tag, idxs in groups.items():
        remaining = [j for j in idxs if j not in fallback_indices]
        if not remaining:
            continue
        group_values = [values[j] for j in remaining]
        z_group = _zscore_min_basket(group_values)
        for pos, j in enumerate(remaining):
            z_sector_neutral[j] = z_group[pos]

    # Basket fallback path: copy basket-wide z into the _basket slot.
    for i in fallback_indices:
        z_basket_out[i] = z_basket_full[i]
        fell_back[i] = True

    return z_sector_neutral, z_basket_out, fell_back


# ---------------------------------------------------------------------------
# Task 6.4 — Sub-score composition, composite, 0–100 transforms (Req 7.3–7.6)
# ---------------------------------------------------------------------------


def _weighted_compose(
    z_by_key: dict[str, float | None], weights: dict[str, float]
) -> float | None:
    """Sum-of-available-weights composition (Req 7.3).

    Missing z-scores drop out of both the numerator and the denominator
    so a missing signal degrades the composition rather than inverting
    its sign. Returns ``None`` when every weight drops out.
    """

    total_w = 0.0
    total = 0.0
    for key, w in weights.items():
        z = z_by_key.get(key)
        if z is None:
            continue
        total += z * w
        total_w += w
    if total_w == 0:
        return None
    return total / total_w


def _to_100(z: float | None) -> float | None:
    """``clip(50 + z * 25, 0, 100)`` transform (Req 7.5)."""

    if z is None:
        return None
    return max(0.0, min(100.0, 50.0 + z * 25.0))


# ---------------------------------------------------------------------------
# Task 6 orchestrator — compute_cross_sectional (Req 6.x, 7.x, 4.7)
# ---------------------------------------------------------------------------


_SECTOR_NEUTRAL_FACTORS: tuple[str, ...] = ("ev_ebitda_yield", "roe")
_BASKET_WIDE_FACTORS: tuple[str, ...] = (
    "clenow_90",
    "inv_range_pct_52w",
    "ma200_distance",
    "target_upside",
)
# Map internal factor name → the flag-naming label used in
# ``basket_too_small_for_z(<label>)`` entries (Req 10.7 catalog).
_BASKET_FLAG_LABEL: dict[str, str] = {
    "clenow_90": "clenow_90",
    "inv_range_pct_52w": "range_pct_52w",
    "ma200_distance": "ma200_distance",
    "target_upside": "target_upside",
}


@dataclass(frozen=True)
class ScoredRow:
    symbol: str
    gics_sector: str | None
    momentum_z: float | None
    value_z: float | None
    quality_z: float | None
    forward_z: float | None
    composite_z: float | None
    momentum_score_0_100: float | None
    value_score_0_100: float | None
    quality_score_0_100: float | None
    forward_score_0_100: float | None
    composite_score_0_100: float | None
    z_scores: dict[str, float | None]
    basket_size: int
    sector_group_size: int
    basket_size_sufficient: bool
    per_row_quality_flags: list[str]


def compute_cross_sectional(
    signals_by_symbol: dict[str, dict[str, float | None]],
    gics_by_symbol: dict[str, str | None],
    *,
    subscore_weights: TopLevelWeights,
) -> list[ScoredRow]:
    """Score every row using the scope map, sub-score weights, composite.

    ``signals_by_symbol`` maps each symbol to a dict carrying the six
    normalization-scope factor values plus the per-row additive flags
    from derived-indicator computation; missing keys collapse to ``None``
    per factor. ``gics_by_symbol`` maps symbol to sector tag.
    """

    symbols = list(signals_by_symbol.keys())
    n = len(symbols)

    # Pre-extract per-factor value lists keyed by factor name.
    def _extract(factor: str) -> list[float | None]:
        return [
            signals_by_symbol.get(sym, {}).get(factor) for sym in symbols
        ]

    sector_tags = [gics_by_symbol.get(sym) for sym in symbols]

    # Per-row quality flags seeded from the signals dict.
    per_row_flags: list[list[str]] = [
        list(signals_by_symbol.get(sym, {}).get("_flags") or [])
        for sym in symbols
    ]

    # Initialize the fixed-shape z_scores per row (every key present).
    z_scores: list[dict[str, float | None]] = [
        {
            "z_ev_ebitda_yield_sector_neutral": None,
            "z_ev_ebitda_yield_basket": None,
            "z_roe_sector_neutral": None,
            "z_roe_basket": None,
            "z_clenow_90_basket": None,
            "z_inv_range_pct_52w_basket": None,
            "z_ma200_distance_basket": None,
            "z_target_upside_basket": None,
            "z_momentum_z_basket": None,
            "z_value_z_basket": None,
            "z_quality_z_basket": None,
            "z_forward_z_basket": None,
            "z_composite_z_basket": None,
        }
        for _ in range(n)
    ]

    # --- Sector-neutral factors (scope fallback handled inside helper).
    sector_neutral_z_by_factor: dict[str, list[float | None]] = {}
    for factor in _SECTOR_NEUTRAL_FACTORS:
        values = _extract(factor)
        z_sn, z_bk, fell_back = _zscore_min_basket_sector_neutral(
            values, sector_tags, factor
        )
        # Populate the two keys per row; exactly one is non-null per row
        # (or both ``None`` when the whole basket collapses).
        sn_key = f"z_{factor}_sector_neutral"
        bk_key = f"z_{factor}_basket"
        for i in range(n):
            z_scores[i][sn_key] = z_sn[i]
            z_scores[i][bk_key] = z_bk[i]
            if fell_back[i]:
                append_quality_flag(
                    per_row_flags[i],
                    f"sector_group_too_small_for_neutral_z({factor})",
                )
        # Collapsed representation for sub-score composition — prefer the
        # sector-neutral value, fall back to basket value (exactly one is
        # populated per row, modulo full-basket collapse which leaves
        # both ``None`` and is handled by the basket_too_small flag).
        collapsed: list[float | None] = []
        all_nulls = True
        for i in range(n):
            chosen = z_sn[i] if z_sn[i] is not None else z_bk[i]
            if chosen is not None:
                all_nulls = False
            collapsed.append(chosen)
        sector_neutral_z_by_factor[factor] = collapsed
        # Full-basket collapse → basket_too_small_for_z(<label>) where
        # label maps to the Req 10.7 catalog string. (For sector-neutral
        # factors the label == factor name.)
        if all_nulls and n > 0:
            label = factor  # catalog entries use the factor name verbatim
            flag = f"basket_too_small_for_z({label})"
            if flag in DATA_QUALITY_FLAGS:
                for i in range(n):
                    append_quality_flag(per_row_flags[i], flag)

    # --- Basket-wide factors.
    basket_z_by_factor: dict[str, list[float | None]] = {}
    for factor in _BASKET_WIDE_FACTORS:
        values = _extract(factor)
        z = _zscore_min_basket(values)
        basket_z_by_factor[factor] = z
        key = f"z_{factor}_basket"
        for i in range(n):
            z_scores[i][key] = z[i]
        # Basket-collapse flagging (Req 7.8).
        if all(v is None for v in z) and n > 0:
            label = _BASKET_FLAG_LABEL[factor]
            flag = f"basket_too_small_for_z({label})"
            for i in range(n):
                append_quality_flag(per_row_flags[i], flag)

    # --- Sub-score composition (Req 7.3, 7.6).
    sub_weights: dict[str, dict[str, float]] = _SUBSCORE_INTERNAL_WEIGHTS
    rows_out: list[ScoredRow] = []

    # Pre-compute per-sub-score z series for basket-wide z of the sub-score.
    momentum_zs: list[float | None] = []
    value_zs: list[float | None] = []
    quality_zs: list[float | None] = []
    forward_zs: list[float | None] = []
    for i in range(n):
        # Per-row collapsed z per signal for sub-score composition.
        row_signal_z = {
            "clenow_90": basket_z_by_factor["clenow_90"][i],
            "ma200_distance": basket_z_by_factor["ma200_distance"][i],
            "ev_ebitda_yield": sector_neutral_z_by_factor["ev_ebitda_yield"][i],
            "inv_range_pct_52w": basket_z_by_factor["inv_range_pct_52w"][i],
            "roe": sector_neutral_z_by_factor["roe"][i],
            "target_upside": basket_z_by_factor["target_upside"][i],
        }
        momentum_zs.append(
            _weighted_compose(row_signal_z, sub_weights["momentum_z"])
        )
        value_zs.append(
            _weighted_compose(row_signal_z, sub_weights["value_z"])
        )
        quality_zs.append(
            _weighted_compose(row_signal_z, sub_weights["quality_z"])
        )
        forward_zs.append(
            _weighted_compose(row_signal_z, sub_weights["forward_z"])
        )

    # Per-row composite uses sub-score z values weighted by top-level weights.
    top_weights: dict[str, float] = {
        "momentum_z": subscore_weights.momentum,
        "value_z": subscore_weights.value,
        "quality_z": subscore_weights.quality,
        "forward_z": subscore_weights.forward,
    }

    # Basket-wide z of each sub-score (for the z_scores block's
    # z_<sub>_z_basket keys). Note: these are z-scores computed over the
    # basket-wide distribution of the sub-score values themselves.
    z_mom_basket = _zscore_min_basket(momentum_zs)
    z_val_basket = _zscore_min_basket(value_zs)
    z_qual_basket = _zscore_min_basket(quality_zs)
    z_fwd_basket = _zscore_min_basket(forward_zs)

    composite_zs: list[float | None] = []
    for i in range(n):
        composite = _weighted_compose(
            {
                "momentum_z": momentum_zs[i],
                "value_z": value_zs[i],
                "quality_z": quality_zs[i],
                "forward_z": forward_zs[i],
            },
            top_weights,
        )
        composite_zs.append(composite)
    z_comp_basket = _zscore_min_basket(composite_zs)

    # Basket accounting (Req 7.10 / 6.5).
    sector_group_counts: dict[str | None, int] = {}
    for tag in sector_tags:
        sector_group_counts[tag] = sector_group_counts.get(tag, 0) + 1

    # basket_size = count of rows with at least one populated sub-score signal.
    def _has_populated_signal(sym: str) -> bool:
        row = signals_by_symbol.get(sym, {})
        for keys in SUBSCORE_SIGNAL_KEYS.values():
            for k in keys:
                if row.get(k) is not None:
                    return True
        return False

    basket_populated = [_has_populated_signal(sym) for sym in symbols]
    basket_size = sum(1 for v in basket_populated if v)

    for i, sym in enumerate(symbols):
        z_scores[i]["z_momentum_z_basket"] = z_mom_basket[i]
        z_scores[i]["z_value_z_basket"] = z_val_basket[i]
        z_scores[i]["z_quality_z_basket"] = z_qual_basket[i]
        z_scores[i]["z_forward_z_basket"] = z_fwd_basket[i]
        z_scores[i]["z_composite_z_basket"] = z_comp_basket[i]

        rows_out.append(
            ScoredRow(
                symbol=sym,
                gics_sector=sector_tags[i],
                momentum_z=momentum_zs[i],
                value_z=value_zs[i],
                quality_z=quality_zs[i],
                forward_z=forward_zs[i],
                composite_z=composite_zs[i],
                momentum_score_0_100=_to_100(momentum_zs[i]),
                value_score_0_100=_to_100(value_zs[i]),
                quality_score_0_100=_to_100(quality_zs[i]),
                forward_score_0_100=_to_100(forward_zs[i]),
                composite_score_0_100=_to_100(composite_zs[i]),
                z_scores=z_scores[i],
                basket_size=basket_size,
                sector_group_size=sector_group_counts.get(sector_tags[i], 0),
                basket_size_sufficient=basket_size >= _MIN_BASKET_SIZE,
                per_row_quality_flags=per_row_flags[i],
            )
        )

    return rows_out


# ---------------------------------------------------------------------------
# Task 7 — Per-stock failure classifier across five fetch axes
# (Req 15.1, 15.2, 15.3)
# ---------------------------------------------------------------------------


_STOCK_FETCH_AXES: tuple[str, ...] = (
    "quote",
    "metrics",
    "historical",
    "consensus",
    "price_target",
)

_FATAL_CATEGORIES: tuple[str, ...] = (
    ErrorCategory.CREDENTIAL.value,
    ErrorCategory.PLAN_INSUFFICIENT.value,
)


def _classify_stock_failure(
    symbol: str,
    fetches: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Roll up per-axis fetch records into a single stock-level verdict.

    ``fetches`` carries the ``{ok, error, error_type, error_category}``
    record for each of the five axes ``quote``, ``metrics``,
    ``historical``, ``consensus``, ``price_target``. Returns ``None``
    when any axis produced usable data (``ok: True``), and a
    ``{error, error_type, error_category}`` record when every axis failed.

    Fatal-category promotion (Req 15.2): when every failed axis shares a
    fatal category (``credential`` or ``plan_insufficient``), the
    returned record carries that category so ``aggregate_emit``'s
    exit-code gate can promote the batch. Mixed or other-category
    failures keep the first-seen category.

    Stays distinct from the sector-axis classifier imported from
    ``sector_score`` (Req 15.3).
    """

    del symbol  # kept for signature alignment with design.md §Service Interface
    if not fetches:
        return None

    any_ok = any(record.get("ok") for record in fetches.values())
    if any_ok:
        return None

    fails = [record for record in fetches.values() if record.get("ok") is False]
    if not fails:
        return None

    categories = [f.get("error_category") for f in fails]
    category: str = ErrorCategory.OTHER.value
    for fatal in _FATAL_CATEGORIES:
        if all(c == fatal for c in categories):
            category = fatal
            break
    else:
        category = next(
            (c for c in categories if c), ErrorCategory.OTHER.value
        )

    first = fails[0]
    return {
        "error": first.get("error"),
        "error_type": first.get("error_type"),
        "error_category": category,
    }


# ---------------------------------------------------------------------------
# Task 8.1 — Envelope row builders (Req 10.1, 10.2, 10.3, 10.5, 11.1)
# ---------------------------------------------------------------------------


_INTERPRETATION_SECTOR_NEUTRAL_FACTORS: list[str] = ["ev_ebitda_yield", "roe"]
_INTERPRETATION_BASKET_WIDE_FACTORS: list[str] = [
    "clenow_90",
    "range_pct_52w",
    "ma200_distance",
    "target_upside",
]


def build_signals_block(
    *,
    last_price: float | None,
    year_high: float | None,
    year_low: float | None,
    ma_200d: float | None,
    ma_50d: float | None,
    range_pct_52w: float | None,
    ma200_distance: float | None,
    market_cap: float | None,
    enterprise_to_ebitda: float | None,
    ev_ebitda_yield: float | None,
    roe: float | None,
    fcf_yield: float | None,
    clenow_90: float | None,
    target_consensus: float | None,
    target_median: float | None,
    target_upside: float | None,
    number_of_analysts: int | None,
) -> dict[str, Any]:
    """Seventeen-field per-ticker ``signals`` block (Req 10.2).

    ``pe_ratio``, ``gross_margin``, and ``recommendation_mean`` are
    intentionally absent — FMP's metrics endpoint does not expose the
    first two and no scoring path consumes the third.
    """

    return {
        "last_price": last_price,
        "year_high": year_high,
        "year_low": year_low,
        "ma_200d": ma_200d,
        "ma_50d": ma_50d,
        "range_pct_52w": range_pct_52w,
        "ma200_distance": ma200_distance,
        "market_cap": market_cap,
        "enterprise_to_ebitda": enterprise_to_ebitda,
        "ev_ebitda_yield": ev_ebitda_yield,
        "roe": roe,
        "fcf_yield": fcf_yield,
        "clenow_90": clenow_90,
        "target_consensus": target_consensus,
        "target_median": target_median,
        "target_upside": target_upside,
        "number_of_analysts": number_of_analysts,
    }


def build_interpretation() -> dict[str, Any]:
    """Fixed five-key per-row ``interpretation`` block (Req 10.3 / 10.4).

    The constructor does not create ``buy_signal`` / ``recommendation``
    keys — Req 10.4's negative invariant is enforced by absence.
    """

    return {
        "score_meaning": "basket_internal_rank",
        "composite_polarity": "high=better_candidate",
        "forward_looking_component_gated_on": "number_of_analysts>=5",
        "sector_neutral_factors": list(_INTERPRETATION_SECTOR_NEUTRAL_FACTORS),
        "basket_wide_factors": list(_INTERPRETATION_BASKET_WIDE_FACTORS),
    }


def build_ok_row(
    *,
    symbol: str,
    gics_sector: str | None,
    sector_origins: list[dict[str, Any]],
    signals: dict[str, Any],
    z_scores: dict[str, float | None],
    momentum_score_0_100: float | None,
    value_score_0_100: float | None,
    quality_score_0_100: float | None,
    forward_score_0_100: float | None,
    composite_score_0_100: float | None,
    basket_size: int,
    sector_group_size: int,
    basket_size_sufficient: bool,
    data_quality_flags: list[str],
) -> dict[str, Any]:
    """Assemble the minimum ``ok: true`` per-row shape (Req 10.1).

    ``rank`` is left as ``None`` here; ``sort_and_rank_rows`` assigns
    the 1-indexed rank after the sort. ``provider`` is not emitted
    per row (single-provider wrapper).
    """

    return {
        "symbol": symbol,
        "ok": True,
        "rank": None,
        "gics_sector": gics_sector,
        "sector_origins": list(sector_origins),
        "composite_score_0_100": composite_score_0_100,
        "momentum_score_0_100": momentum_score_0_100,
        "value_score_0_100": value_score_0_100,
        "quality_score_0_100": quality_score_0_100,
        "forward_score_0_100": forward_score_0_100,
        "signals": signals,
        "z_scores": z_scores,
        "basket_size": basket_size,
        "sector_group_size": sector_group_size,
        "basket_size_sufficient": basket_size_sufficient,
        "data_quality_flags": list(data_quality_flags),
        "interpretation": build_interpretation(),
    }


def build_failure_row(
    *,
    symbol: str,
    gics_sector: str | None,
    sector_origins: list[dict[str, Any]],
    error: str | None,
    error_type: str | None,
    error_category: str | None,
) -> dict[str, Any]:
    """Assemble an ``ok: false`` per-row shape (Req 10.5 / 9.3).

    Omits ``*_score_0_100`` and ``z_scores`` / ``signals`` / basket
    accounting; keeps only the envelope-plus-failure minimum.
    """

    return {
        "symbol": symbol,
        "ok": False,
        "rank": None,
        "gics_sector": gics_sector,
        "sector_origins": list(sector_origins),
        "error": error,
        "error_type": error_type,
        "error_category": error_category,
    }


# ---------------------------------------------------------------------------
# Task 8.2 — Analytical caveats, sort-and-rank (Req 10.6, 8.1, 8.2, 8.3)
# ---------------------------------------------------------------------------


_NON_US_CAVEAT: str = "non_us_tickers_filtered_from_pool"


def compose_analytical_caveats(*, non_us_filter_applied: bool) -> list[str]:
    """Return the caveats list for ``data.analytical_caveats``.

    Always carries the six-entry base (Req 10.6). Appends
    ``non_us_tickers_filtered_from_pool`` iff the non-US filter actually
    dropped at least one row during pool build (Req 4.8 / 10.6 extension).
    """

    caveats = list(ANALYTICAL_CAVEATS_BASE)
    if non_us_filter_applied:
        caveats.append(_NON_US_CAVEAT)
    return caveats


def sort_and_rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable-sort by ``composite_score_0_100`` desc; null sinks; 1-indexed rank.

    ``ok: false`` rows and ``ok: true`` rows with a null composite
    score sink to the bottom together. Every row receives a 1-indexed
    ``rank`` — including sunk rows — so consumers always have a
    deterministic ordering position (Req 8.2). No truncation (Req 8.3).
    """

    def sort_key(row: dict[str, Any]) -> float:
        score = row.get("composite_score_0_100")
        return -score if isinstance(score, (int, float)) else math.inf

    ordered = sorted(rows, key=sort_key)
    for idx, row in enumerate(ordered, start=1):
        row["rank"] = idx
    return ordered


# ---------------------------------------------------------------------------
# Task 8.1 — Data namespace assembly (Req 9.2, 3.4, 4.5, 4.6, 4.8, 10.6)
# ---------------------------------------------------------------------------


def _sector_weights_dict(w: SectorRankWeights) -> dict[str, float]:
    return {
        "clenow_90": w.clenow_90,
        "clenow_180": w.clenow_180,
        "return_6m": w.return_6m,
        "return_3m": w.return_3m,
        "return_12m": w.return_12m,
        "risk_adj": w.risk_adj,
    }


def _sub_score_weights_dict(w: TopLevelWeights) -> dict[str, float]:
    return {
        "momentum": w.momentum,
        "value": w.value,
        "quality": w.quality,
        "forward": w.forward,
    }


def build_data_namespace(
    *,
    config: ScreenerConfig,
    sector_ranks: list[dict[str, Any]],
    missing_tickers: list[str],
    non_us_tickers_filtered: list[dict[str, Any]],
    provider_diagnostics: list[dict[str, Any]],
    analytical_caveats: list[str],
    notes: list[str],
    etf_holdings_updated_max_age_days: int | None,
) -> dict[str, Any]:
    """Assemble the ``data`` namespace siblings of ``results`` (Req 9.2).

    Optional blocks (``non_us_tickers_filtered``, ``provider_diagnostics``)
    are omitted when empty so their presence is load-bearing for agents
    reading the envelope. ``provider`` is deliberately absent — the
    wrapper is pinned to FMP (Req 2.1 / 9.2).
    """

    data: dict[str, Any] = {
        "universe": config.universe_key,
        "tickers": list(config.etfs),
        "weights": {
            "sector": _sector_weights_dict(config.sector_weights),
            "sub_scores": _sub_score_weights_dict(config.subscore_weights),
            "sub_scores_internal": {
                sub: dict(inner)
                for sub, inner in _SUBSCORE_INTERNAL_WEIGHTS.items()
            },
        },
        "sector_ranks": list(sector_ranks),
        "top_sectors_requested": config.top_sectors,
        "top_stocks_per_sector_requested": config.top_stocks_per_sector,
        "etf_holdings_updated_max_age_days": etf_holdings_updated_max_age_days,
        "missing_tickers": list(missing_tickers),
        "analytical_caveats": list(analytical_caveats),
        "notes": list(notes),
    }
    if non_us_tickers_filtered:
        data["non_us_tickers_filtered"] = list(non_us_tickers_filtered)
    if provider_diagnostics:
        data["provider_diagnostics"] = list(provider_diagnostics)
    return data


# ---------------------------------------------------------------------------
# Task 8.3 — Assemble + delegate emission (Req 9.1, 9.2, 9.3, 2.3, 4.7)
# ---------------------------------------------------------------------------


_SPARSE_POOL_WARNING: dict[str, Any] = {
    "symbol": None,
    "error": "insufficient stock pool size for cross-sectional z-score",
    "error_category": ErrorCategory.VALIDATION.value,
}


def assemble_and_emit(
    *,
    config: ScreenerConfig,
    rows: list[dict[str, Any]],
    sector_ranks: list[dict[str, Any]],
    missing_tickers: list[str],
    non_us_tickers_filtered: list[dict[str, Any]],
    provider_diagnostics: list[dict[str, Any]],
    notes: list[str],
    etf_holdings_updated_max_age_days: int | None,
    pool_size: int,
) -> int:
    """Sort + rank + delegate stdout emission through ``aggregate_emit``.

    ``aggregate_emit`` owns the NaN/Inf sanitization, root-key
    composition, per-row failure mirroring into top-level ``warnings``,
    and the all-fatal-category exit-code gate. The sparse-pool warning
    (Req 4.7) is carried via ``extra_warnings`` so per-row rows still
    emit with null z-scores.
    """

    ordered = sort_and_rank_rows(rows)
    caveats = compose_analytical_caveats(
        non_us_filter_applied=bool(non_us_tickers_filtered)
    )
    data_namespace = build_data_namespace(
        config=config,
        sector_ranks=sector_ranks,
        missing_tickers=missing_tickers,
        non_us_tickers_filtered=non_us_tickers_filtered,
        provider_diagnostics=provider_diagnostics,
        analytical_caveats=caveats,
        notes=notes,
        etf_holdings_updated_max_age_days=etf_holdings_updated_max_age_days,
    )

    extra_warnings: list[dict[str, Any]] = []
    if pool_size < _MIN_BASKET_SIZE:
        extra_warnings.append(dict(_SPARSE_POOL_WARNING))

    return aggregate_emit(
        ordered,
        tool=_TOOL,
        query_meta=data_namespace,
        extra_warnings=extra_warnings or None,
    )


# ---------------------------------------------------------------------------
# Task 10.1 — Pipeline runner (Req 2.1, 2.3, 3.x, 4.x, 5.x, 6.x, 7.x, 8.x,
# 9.x, 15.x)
# ---------------------------------------------------------------------------


def _axis_record(
    *,
    batch_ok: bool,
    batch_failure: dict[str, Any] | None,
    row_present: bool,
) -> dict[str, Any]:
    """Per-symbol fetch-axis record consumed by ``_classify_stock_failure``.

    Mirrors the five-axis contract from design.md §Stock Failure
    Classifier — a batch that fails propagates that category to every
    row; a successful batch with no row for the symbol surfaces as an
    ``other/NoData`` no-data record (still per-axis "failed" but not
    fatal, so a single missing row never collapses the stock when any
    other axis produced data).
    """

    if not batch_ok and batch_failure is not None:
        return {
            "ok": False,
            "error": batch_failure.get("error"),
            "error_type": batch_failure.get("error_type"),
            "error_category": batch_failure.get("error_category"),
        }
    if row_present:
        return {"ok": True}
    return {
        "ok": False,
        "error": "no data available",
        "error_type": "NoData",
        "error_category": ErrorCategory.OTHER.value,
    }


def run_pipeline(config: ScreenerConfig) -> int:
    """Chain sector-rank → pool → per-stock fetch → score → emit.

    Stage order mirrors design.md §Per-invocation sequence:

    1. FMP-native sector-rank composite via imported ``build_scores``.
    2. Top-N sector selection with shortfall tolerance.
    3. One ``etf.holdings`` call per selected ETF; non-US filter +
       top-M weight slice + dedup into the stock pool.
    4. Four batched per-stock calls (quote / metrics / consensus /
       price_target) + per-symbol historical + Clenow reduction.
    5. Per-row derived indicators, 90-day distinct-firm analyst count,
       cross-sectional z-scores (sector-neutral where applicable).
    6. Per-axis failure classification for each pool symbol.
    7. Envelope assembly + stdout emission via ``aggregate_emit``.
    """

    today = date.today()

    # ---- 1. Sector rank ------------------------------------------------
    ranked, sector_diagnostics = run_sector_rank(config)
    selection = select_top_sectors(ranked, config.top_sectors)
    sector_ranks_block = sector_ranks_envelope_rows(ranked)
    notes: list[str] = []
    if selection.shortfall_note is not None:
        notes.append(selection.shortfall_note)
    provider_diagnostics: list[dict[str, Any]] = list(sector_diagnostics)

    selected_etfs = [sel.ticker for sel in selection.selected]

    # ---- 2. ETF holdings ----------------------------------------------
    if selected_etfs:
        holdings, holdings_diagnostics = fetch_etf_holdings(selected_etfs)
    else:
        holdings, holdings_diagnostics = [], []
    provider_diagnostics.extend(holdings_diagnostics)

    # ---- 3. Pool build -------------------------------------------------
    pool_outcome = build_pool(holdings, config, today)
    pool = pool_outcome.pool
    pool_symbols = [entry.symbol for entry in pool]

    if not pool_symbols:
        # Nothing to score; still emit the envelope so agents see the
        # sector ranks and any upstream diagnostics.
        return assemble_and_emit(
            config=config,
            rows=[],
            sector_ranks=sector_ranks_block,
            missing_tickers=[],
            non_us_tickers_filtered=pool_outcome.non_us_tickers_filtered,
            provider_diagnostics=provider_diagnostics,
            notes=notes,
            etf_holdings_updated_max_age_days=pool_outcome.etf_holdings_updated_max_age_days,
            pool_size=0,
        )

    # ---- 4. Batched per-stock fetches ---------------------------------
    quotes = fetch_quotes_batched(pool_symbols)
    metrics = fetch_metrics_batched(pool_symbols)
    consensus = fetch_consensus_batched(pool_symbols)
    price_targets = fetch_price_target_batched(pool_symbols)

    quote_rows: dict[str, Any] = (
        quotes.get("by_symbol", {}) if quotes.get("ok") else {}
    )
    metrics_rows: dict[str, Any] = (
        metrics.get("by_symbol", {}) if metrics.get("ok") else {}
    )
    consensus_rows: dict[str, Any] = (
        consensus.get("by_symbol", {}) if consensus.get("ok") else {}
    )
    price_target_rows_by_symbol: dict[str, Any] = (
        price_targets.get("by_symbol", {}) if price_targets.get("ok") else {}
    )

    # Record batch-level failures so fatal categories (credential /
    # plan_insufficient) can propagate to the per-stock classifier.
    for stage_key, stage_result in (
        ("quote", quotes),
        ("metrics", metrics),
        ("consensus", consensus),
        ("price_target", price_targets),
    ):
        if not stage_result.get("ok"):
            provider_diagnostics.append(
                {
                    "provider": _FMP,
                    "stage": f"stock_{stage_key}",
                    "error": stage_result.get("error"),
                    "error_category": stage_result.get("error_category"),
                }
            )

    # ---- 5. Per-symbol derived indicators, Clenow, scoring inputs -----
    signals_by_symbol: dict[str, dict[str, Any]] = {}
    gics_by_symbol: dict[str, str | None] = {}
    per_symbol_context: dict[str, dict[str, Any]] = {}
    axis_failure_by_symbol: dict[str, dict[str, dict[str, Any]]] = {}

    for entry in pool:
        sym = entry.symbol
        gics_by_symbol[sym] = entry.gics_sector

        quote_row = quote_rows.get(sym)
        metrics_row = metrics_rows.get(sym)
        consensus_row = consensus_rows.get(sym)
        price_target_rows = price_target_rows_by_symbol.get(sym)

        clenow_result = fetch_stock_clenow_fmp(sym)
        historical_ok = bool(clenow_result.get("ok"))
        clenow_90 = clenow_result.get("clenow_90") if historical_ok else None

        axis_failure_by_symbol[sym] = {
            "quote": _axis_record(
                batch_ok=bool(quotes.get("ok")),
                batch_failure=None if quotes.get("ok") else quotes,
                row_present=quote_row is not None,
            ),
            "metrics": _axis_record(
                batch_ok=bool(metrics.get("ok")),
                batch_failure=None if metrics.get("ok") else metrics,
                row_present=metrics_row is not None,
            ),
            "consensus": _axis_record(
                batch_ok=bool(consensus.get("ok")),
                batch_failure=None if consensus.get("ok") else consensus,
                row_present=consensus_row is not None,
            ),
            "price_target": _axis_record(
                batch_ok=bool(price_targets.get("ok")),
                batch_failure=None if price_targets.get("ok") else price_targets,
                row_present=price_target_rows is not None,
            ),
            "historical": (
                {"ok": True}
                if historical_ok
                else {
                    "ok": False,
                    "error": clenow_result.get("error"),
                    "error_type": clenow_result.get("error_type"),
                    "error_category": clenow_result.get("error_category"),
                }
            ),
        }
        if not historical_ok:
            provider_diagnostics.append(
                {
                    "provider": _FMP,
                    "stage": clenow_result.get("stage", "stock_clenow_historical"),
                    "symbol": sym,
                    "error": clenow_result.get("error"),
                    "error_category": clenow_result.get("error_category"),
                }
            )

        quote_fields = _extract_quote_fields(quote_row)
        metrics_fields = _extract_metrics_fields(metrics_row)
        consensus_fields = _extract_consensus_fields(consensus_row)

        last_price_resolution = resolve_last_price(quote_row)

        number_of_analysts = derive_number_of_analysts(price_target_rows, today)

        derived = compute_derived_indicators(
            last_price=last_price_resolution.value,
            year_high=quote_fields["year_high"],
            year_low=quote_fields["year_low"],
            ma_200d=quote_fields["ma_200d"],
            enterprise_to_ebitda=metrics_fields["enterprise_to_ebitda"],
            target_consensus=consensus_fields["target_consensus"],
            number_of_analysts=number_of_analysts,
        )

        inv_range_pct_52w: float | None = (
            1.0 - derived.range_pct_52w if derived.range_pct_52w is not None else None
        )

        seeded_flags: list[str] = list(entry.quality_flags)
        if last_price_resolution.flag is not None:
            seeded_flags.append(last_price_resolution.flag)
        seeded_flags.extend(derived.extra_flags)

        signals_by_symbol[sym] = {
            "clenow_90": clenow_90,
            "ma200_distance": derived.ma200_distance,
            "ev_ebitda_yield": derived.ev_ebitda_yield,
            "inv_range_pct_52w": inv_range_pct_52w,
            "roe": metrics_fields["roe"],
            "target_upside": derived.target_upside,
            "_flags": seeded_flags,
        }
        per_symbol_context[sym] = {
            "sector_origins": entry.sector_origins,
            "gics_sector": entry.gics_sector,
            "last_price": last_price_resolution.value,
            "number_of_analysts": number_of_analysts,
            "clenow_90": clenow_90,
            "quote": quote_fields,
            "metrics": metrics_fields,
            "consensus": consensus_fields,
            "derived": derived,
            "inv_range_pct_52w": inv_range_pct_52w,
        }

    # ---- 6. Classify per-stock failures -------------------------------
    stock_failure_by_symbol: dict[str, dict[str, Any] | None] = {}
    ok_symbols: list[str] = []
    for entry in pool:
        sym = entry.symbol
        failure = _classify_stock_failure(sym, axis_failure_by_symbol[sym])
        stock_failure_by_symbol[sym] = failure
        if failure is None:
            ok_symbols.append(sym)

    # ---- 7. Cross-sectional scoring (only on ok:true rows) ------------
    ok_signals = {sym: signals_by_symbol[sym] for sym in ok_symbols}
    ok_gics = {sym: gics_by_symbol[sym] for sym in ok_symbols}
    scored_rows = compute_cross_sectional(
        ok_signals, ok_gics, subscore_weights=config.subscore_weights
    )
    scored_by_symbol = {row.symbol: row for row in scored_rows}

    # ---- 8. Envelope-row assembly -------------------------------------
    envelope_rows: list[dict[str, Any]] = []
    missing_tickers: list[str] = []

    for entry in pool:
        sym = entry.symbol
        failure = stock_failure_by_symbol[sym]
        ctx = per_symbol_context[sym]
        if failure is not None:
            missing_tickers.append(sym)
            envelope_rows.append(
                build_failure_row(
                    symbol=sym,
                    gics_sector=ctx["gics_sector"],
                    sector_origins=ctx["sector_origins"],
                    error=failure.get("error"),
                    error_type=failure.get("error_type"),
                    error_category=failure.get("error_category"),
                )
            )
            continue

        scored = scored_by_symbol[sym]
        quote_fields = ctx["quote"]
        metrics_fields = ctx["metrics"]
        consensus_fields = ctx["consensus"]
        derived: DerivedIndicators = ctx["derived"]

        signals = build_signals_block(
            last_price=ctx["last_price"],
            year_high=quote_fields["year_high"],
            year_low=quote_fields["year_low"],
            ma_200d=quote_fields["ma_200d"],
            ma_50d=quote_fields["ma_50d"],
            range_pct_52w=derived.range_pct_52w,
            ma200_distance=derived.ma200_distance,
            market_cap=metrics_fields["market_cap"],
            enterprise_to_ebitda=metrics_fields["enterprise_to_ebitda"],
            ev_ebitda_yield=derived.ev_ebitda_yield,
            roe=metrics_fields["roe"],
            fcf_yield=metrics_fields["fcf_yield"],
            clenow_90=ctx["clenow_90"],
            target_consensus=consensus_fields["target_consensus"],
            target_median=consensus_fields["target_median"],
            target_upside=derived.target_upside,
            number_of_analysts=ctx["number_of_analysts"],
        )

        envelope_rows.append(
            build_ok_row(
                symbol=sym,
                gics_sector=ctx["gics_sector"],
                sector_origins=ctx["sector_origins"],
                signals=signals,
                z_scores=scored.z_scores,
                momentum_score_0_100=scored.momentum_score_0_100,
                value_score_0_100=scored.value_score_0_100,
                quality_score_0_100=scored.quality_score_0_100,
                forward_score_0_100=scored.forward_score_0_100,
                composite_score_0_100=scored.composite_score_0_100,
                basket_size=scored.basket_size,
                sector_group_size=scored.sector_group_size,
                basket_size_sufficient=scored.basket_size_sufficient,
                data_quality_flags=scored.per_row_quality_flags,
            )
        )

    # ---- 9. Emit -------------------------------------------------------
    return assemble_and_emit(
        config=config,
        rows=envelope_rows,
        sector_ranks=sector_ranks_block,
        missing_tickers=missing_tickers,
        non_us_tickers_filtered=pool_outcome.non_us_tickers_filtered,
        provider_diagnostics=provider_diagnostics,
        notes=notes,
        etf_holdings_updated_max_age_days=pool_outcome.etf_holdings_updated_max_age_days,
        pool_size=len(pool),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config = build_config(argv)
    except _ConfigError as exc:
        return emit_error(
            str(exc),
            tool=_TOOL,
            error_category=ErrorCategory.VALIDATION.value,
        )

    rc = check_fmp_credential()
    if rc != 0:
        return rc

    return run_pipeline(config)


if __name__ == "__main__":
    raise SystemExit(main())
