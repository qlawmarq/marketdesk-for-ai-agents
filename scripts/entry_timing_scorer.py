"""Entry-timing scorer — per-ticker entry-timing analytics for the daily monitoring loop.

Accepts a short list of tickers (typically 5-10 names already selected by the long /
mid-term strategy) and emits per-ticker entry-timing analytics built from five
already-live OpenBB primitives (``equity.price.quote``, ``equity.price.historical``,
``technical.{clenow,rsi,macd}``, ``equity.calendar.earnings``). Produces a two-axis
output (``trend_score_0_100`` + ``mean_reversion_score_0_100``) with an opt-in blend,
an earnings-proximity flag deliberately kept outside the composite, and a
``volume_avg_window: "20d_real"`` tag that permanently resolves the reviewer R1
FLXS volume-label ambiguity.

Usage:
    uv run scripts/entry_timing_scorer.py --tickers ASC,CMCL,FLXS,SM,TLT,LQD
    uv run scripts/entry_timing_scorer.py --portfolio-file portfolio.yaml
    uv run scripts/entry_timing_scorer.py --tickers ASC,CMCL --context watchlist --blend-profile balanced
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Literal, Mapping

import yaml

from _common import (
    ErrorCategory,
    aggregate_emit,
    emit_error,
    safe_call,
    sanitize_for_json,
)
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# ---------------------------------------------------------------------------
# Module-scope constants (Req 3.7, 4.2, 7.1, 9.9, 9.10)
# ---------------------------------------------------------------------------

HISTORICAL_LOOKBACK_DAYS = 210
DEFAULT_EARNINGS_WINDOW_DAYS = 45
DEFAULT_EARNINGS_PROXIMITY_DAYS = 5

ANALYTICAL_CAVEATS: tuple[str, str, str] = (
    "scores_are_basket_internal_ranks_not_absolute_strength",
    "trend_and_mean_reversion_are_separate_axes",
    "earnings_proximity_is_flag_not_score_component",
)

SCORER_SIGNAL_KEYS: tuple[str, str, str, str, str] = (
    "clenow_126",
    "macd_histogram",
    "volume_z_20d",
    "inv_range_pct_52w",
    "oversold_rsi_14",
)

# Flag strings use the *original* signal names that agents recognise from `signals.*`.
_SIGNAL_FLAG_NAME: dict[str, str] = {
    "clenow_126": "clenow_126",
    "macd_histogram": "macd_histogram",
    "volume_z_20d": "volume_z_20d",
    "inv_range_pct_52w": "range_pct_52w",
    "oversold_rsi_14": "rsi_14",
}

# Closed enumeration of per-row `data_quality_flags[]` entries (Req 9.10).
# Every flag appended to a per-ticker row is validated against this catalog
# at append time via ``append_quality_flag``; unknown strings raise at
# development time so the contract holds structurally.
DATA_QUALITY_FLAGS: frozenset[str] = frozenset(
    {
        "rsi_oversold_lt_20",
        "basket_too_small_for_z",
        "basket_too_small_for_z(clenow_126)",
        "basket_too_small_for_z(macd_histogram)",
        "basket_too_small_for_z(volume_z_20d)",
        "basket_too_small_for_z(range_pct_52w)",
        "basket_too_small_for_z(rsi_14)",
        "volume_window_too_short",
        "volume_zero_dispersion",
        "volume_non_positive",
        "volume_reference_unavailable_on_provider",
        "last_price_from_prev_close",
        "last_price_from_historical_close",
        "last_price_unavailable",
        "context_duplicate_positions_and_watchlist",
    }
)

# Per-context reading string (Req 9.4). The absence of the
# scalar named in Req 9.5 is that requirement's negative invariant —
# enforced by construction here and locked by a source-text assertion
# in the flags unit tests.
_READING_BY_CONTEXT: dict[str, str] = {
    "watchlist": "entry_candidate_if_high_scores",
    "holding": "hold_or_add_if_high_trend,reconsider_if_high_mean_reversion",
    "unknown": "ambiguous_without_context",
}


# ---------------------------------------------------------------------------
# CLI config model (Req 1.x / 2.x / 3.7 / 5.4 / 6.3 / 6.5 / 7.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendWeights:
    clenow: float
    macd: float
    volume: float


@dataclass(frozen=True)
class MeanReversionWeights:
    range: float
    rsi: float


@dataclass(frozen=True)
class ScorerConfig:
    tickers: list[str]
    contexts: dict[str, str]
    provider: str
    calendar_provider: str
    earnings_window_days: int
    earnings_proximity_days: int
    volume_z_estimator: str
    blend_profile: str
    trend_weights: TrendWeights
    mean_reversion_weights: MeanReversionWeights
    context_duplicate_flags: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider router (Req 2.1, 2.2, 2.3)
# ---------------------------------------------------------------------------


def resolve_providers(cli_provider: str) -> tuple[str, str]:
    """Map ``--provider`` to the ``(equity_provider, calendar_provider)`` pair.

    ``yfinance`` routes the earnings-calendar call to ``nasdaq`` (the keyless
    default path); ``fmp`` routes every call (including calendar) to ``fmp``
    so FMP-credentialled callers reach the clean +90d earnings window without
    the nasdaq 403 ceiling. No other provider is accepted — adding one
    requires extending the map explicitly.
    """

    if cli_provider == "yfinance":
        return ("yfinance", "nasdaq")
    if cli_provider == "fmp":
        return ("fmp", "fmp")
    raise ValueError(f"unknown --provider value: {cli_provider!r}")


# ---------------------------------------------------------------------------
# Earnings calendar fetcher (Req 3.1, 3.2, 3.3, 3.4, 3.5, 13.3, 13.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EarningsIndex:
    """Symbol-indexed earnings view returned by ``fetch_earnings_window``.

    ``by_symbol`` holds the earliest ``report_date >= today`` per input
    ticker; tickers with no surviving row are simply absent from the
    dict. ``diagnostic`` is ``None`` on success and a single
    ``{provider, stage, error, error_category}`` record on failure so
    the envelope assembler can surface it under
    ``data.provider_diagnostics`` (Req 3.5).
    """

    by_symbol: dict[str, date]
    diagnostic: dict[str, str] | None


def _coerce_report_date(value: Any) -> date | None:
    """Coerce a provider ``report_date`` into a ``date`` or ``None``.

    Accepts ``date`` / ``datetime`` instances verbatim (stripping the
    time component) and ISO ``YYYY-MM-DD`` strings (with or without a
    ``T``-suffixed time tail). Anything unparseable — unexpected types,
    malformed strings, ``None`` — collapses to ``None`` so the indexer
    can skip the row defensively (research Decision 10). Never raises.
    """

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        head = stripped.split("T", 1)[0]
        try:
            return date.fromisoformat(head)
        except ValueError:
            return None
    return None


def _index_earnings_rows(
    rows: list[Any],
    tickers: list[str],
    today: date,
) -> dict[str, date]:
    """Pick the earliest ``report_date >= today`` per input ticker.

    Filters to the input-ticker set before indexing (Req 3.2) so a
    provider that returns rows for unrelated symbols does not bloat
    the result. Skips rows that are not dicts or are missing ``symbol``
    / ``report_date`` rather than raising, so a single malformed row
    cannot kill a full basket (research Decision 10).
    """

    input_set = set(tickers)
    best: dict[str, date] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        if symbol not in input_set:
            continue
        report_date = _coerce_report_date(row.get("report_date"))
        if report_date is None:
            continue
        if report_date < today:
            continue
        current = best.get(symbol)
        if current is None or report_date < current:
            best[symbol] = report_date
    return best


def fetch_earnings_window(
    tickers: list[str],
    *,
    calendar_provider: str,
    window_days: int,
    today: date,
) -> EarningsIndex:
    """Issue exactly one ``obb.equity.calendar.earnings`` call and index the rows.

    Guarded by ``safe_call`` so provider exceptions become a structured
    record rather than raising (Req 4.6-style pattern applied to the
    calendar stage). No in-process retry on failure (Req 13.4) — the
    nasdaq 403 poisons the process, and FMP is held to the same rule
    for provider-agnostic behavior; transient failures surface via
    ``error_category: "transient"`` so operators retry at the CLI
    level.

    On success returns ``EarningsIndex(by_symbol, diagnostic=None)``
    with the earliest ``report_date >= today`` per input ticker
    (Req 3.3). On failure returns an empty ``by_symbol`` plus a
    ``{provider, stage, error, error_category}`` diagnostic so the
    envelope layer can route it into ``data.provider_diagnostics``
    while every per-ticker row still emits with
    ``next_earnings_date: null`` (Req 3.5).
    """

    start_date = today.isoformat()
    end_date = (today + timedelta(days=window_days)).isoformat()

    call = safe_call(
        obb.equity.calendar.earnings,
        start_date=start_date,
        end_date=end_date,
        provider=calendar_provider,
    )

    if not call.get("ok"):
        diagnostic = {
            "provider": calendar_provider,
            "stage": "earnings_calendar",
            "error": call.get("error") or "",
            "error_category": call.get("error_category")
            or ErrorCategory.OTHER.value,
        }
        return EarningsIndex(by_symbol={}, diagnostic=diagnostic)

    records = call.get("records") or []
    by_symbol = _index_earnings_rows(records, tickers, today)
    return EarningsIndex(by_symbol=by_symbol, diagnostic=None)


# ---------------------------------------------------------------------------
# Ticker resolver + context tagger (Req 1.1, 1.2, 1.5, 1.6, 1.7, 1.9, 10.1, 10.2)
# ---------------------------------------------------------------------------


def _parse_ticker_csv(raw: str) -> list[str]:
    """Split a comma-separated ticker string, preserving first-seen order and dedup.

    Whitespace around individual tokens is stripped; empty tokens are dropped.
    ``.T``-suffixed symbols are left untouched so JP equities work without a
    provider override (Req 1.5).
    """

    seen: dict[str, None] = {}
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token:
            continue
        if token not in seen:
            seen[token] = None
    return list(seen.keys())


@dataclass(frozen=True)
class PortfolioResolution:
    tickers: list[str]
    contexts: dict[str, str]
    duplicate_flags: dict[str, list[str]]


def load_portfolio_file(path: Path) -> PortfolioResolution:
    """Parse the YAML subset ``positions[].ticker`` + ``watchlist[].ticker``.

    Uses ``yaml.safe_load`` exclusively to keep the code-execution vector
    closed (Req 10.1 + research Decision 1). Documents that do not parse
    to a mapping (e.g. ``None``, list, scalar) raise ``ValueError`` so the
    caller can surface the failure as ``error_category: "validation"``.

    Duplicate tickers appearing in both ``positions[]`` and ``watchlist[]``
    resolve to ``"holding"`` and collect the
    ``"context_duplicate_positions_and_watchlist"`` data-quality flag
    (Req 1.9). No other YAML fields are read (Req 10.1 / 10.2).
    """

    raw = path.read_text()
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise ValueError(
            "portfolio file must parse to a YAML mapping with top-level "
            "`positions` and/or `watchlist` keys"
        )

    ordered: dict[str, str] = {}
    duplicate_flags: dict[str, list[str]] = {}

    # `positions[]` first so `watchlist[]` cannot overwrite `holding`.
    for section, context in (("positions", "holding"), ("watchlist", "watchlist")):
        entries = parsed.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ticker = entry.get("ticker")
            if not isinstance(ticker, str) or not ticker.strip():
                continue
            symbol = ticker.strip()
            if symbol in ordered:
                if ordered[symbol] == "holding" and context == "watchlist":
                    duplicate_flags.setdefault(symbol, []).append(
                        "context_duplicate_positions_and_watchlist"
                    )
                continue
            ordered[symbol] = context

    return PortfolioResolution(
        tickers=list(ordered.keys()),
        contexts=dict(ordered),
        duplicate_flags=duplicate_flags,
    )


# ---------------------------------------------------------------------------
# Argparse wiring (Req 1.3, 1.4, 1.6, 1.8, 2.3, 3.7, 5.4, 6.3, 6.5, 7.2, 7.6)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker symbols (e.g. 'ASC,CMCL,FLXS'). "
        "Mutually exclusive with --portfolio-file.",
    )
    source.add_argument(
        "--portfolio-file",
        default=None,
        help="Path to a YAML file with `positions[].ticker` and/or "
        "`watchlist[].ticker`. Mutually exclusive with --tickers.",
    )

    parser.add_argument(
        "--context",
        default=None,
        choices=("watchlist", "holding", "unknown"),
        help="Context for every --tickers entry (default 'unknown'). "
        "Rejected under --portfolio-file.",
    )
    parser.add_argument(
        "--provider",
        default="yfinance",
        choices=("yfinance", "fmp"),
        help="OpenBB provider (default yfinance). fmp routes every call "
        "including the earnings-calendar fetch to FMP.",
    )
    parser.add_argument(
        "--earnings-window-days",
        type=int,
        default=DEFAULT_EARNINGS_WINDOW_DAYS,
        help="Calendar-day window for the single earnings-calendar fetch "
        "(default 45, bounded [1, 90]).",
    )
    parser.add_argument(
        "--earnings-proximity-days",
        type=int,
        default=DEFAULT_EARNINGS_PROXIMITY_DAYS,
        help="Calendar-day threshold for earnings_proximity_warning=true "
        "(default 5, must be non-negative).",
    )
    parser.add_argument(
        "--volume-z-estimator",
        default="robust",
        choices=("robust", "classical"),
        help="Estimator for the 20-day volume z-score (default robust: "
        "log-median-MAD).",
    )
    parser.add_argument(
        "--blend-profile",
        default="none",
        choices=("trend", "mean_reversion", "balanced", "none"),
        help="Blended score profile (default 'none' omits blended_score_0_100).",
    )

    parser.add_argument("--weight-trend-clenow", type=float, default=0.50)
    parser.add_argument("--weight-trend-macd", type=float, default=0.25)
    parser.add_argument("--weight-trend-volume", type=float, default=0.25)
    parser.add_argument("--weight-meanrev-range", type=float, default=0.60)
    parser.add_argument("--weight-meanrev-rsi", type=float, default=0.40)

    return parser


class _ConfigError(ValueError):
    """Raised when argv validation fails after argparse accepted the raw shape."""


def _validate_bounds(args: argparse.Namespace) -> None:
    """Second-stage validation for flags argparse's `type=int` cannot gate.

    argparse already enforces the provider / estimator / blend-profile /
    context choice sets and the integer types; the bounds on
    ``--earnings-window-days`` ([1, 90]) and ``--earnings-proximity-days``
    (>= 0) are checked here so the failure surfaces as
    ``error_category: "validation"`` with a descriptive message before any
    OpenBB call is issued (Req 3.7, 7.6).
    """

    if not (1 <= args.earnings_window_days <= 90):
        raise _ConfigError(
            f"--earnings-window-days must be in [1, 90]; got {args.earnings_window_days}"
        )
    if args.earnings_proximity_days < 0:
        raise _ConfigError(
            f"--earnings-proximity-days must be non-negative; got {args.earnings_proximity_days}"
        )


def build_config(argv: list[str] | None = None) -> ScorerConfig:
    """Parse argv, validate, and resolve the ticker + context universe.

    On any validation failure, raises ``_ConfigError`` so the caller can
    route the failure through ``emit_error`` with a non-zero exit code
    before any OpenBB call is issued.
    """

    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_bounds(args)

    if args.portfolio_file is not None and args.context is not None:
        raise _ConfigError(
            "--context is only valid with --tickers; under --portfolio-file "
            "the context comes from the YAML structure (positions → holding, "
            "watchlist → watchlist)"
        )

    duplicate_flags: dict[str, list[str]] = {}
    if args.tickers is not None:
        tickers = _parse_ticker_csv(args.tickers)
        context_value = args.context or "unknown"
        contexts = {t: context_value for t in tickers}
    else:
        path = Path(args.portfolio_file)
        if not path.exists():
            raise _ConfigError(f"--portfolio-file path does not exist: {path}")
        resolution = load_portfolio_file(path)
        tickers = resolution.tickers
        contexts = resolution.contexts
        duplicate_flags = resolution.duplicate_flags

    if not tickers:
        raise _ConfigError(
            "no tickers resolved; supply at least one symbol via --tickers "
            "or a non-empty portfolio file"
        )

    equity_provider, calendar_provider = resolve_providers(args.provider)

    return ScorerConfig(
        tickers=tickers,
        contexts=contexts,
        provider=equity_provider,
        calendar_provider=calendar_provider,
        earnings_window_days=args.earnings_window_days,
        earnings_proximity_days=args.earnings_proximity_days,
        volume_z_estimator=args.volume_z_estimator,
        blend_profile=args.blend_profile,
        trend_weights=TrendWeights(
            clenow=args.weight_trend_clenow,
            macd=args.weight_trend_macd,
            volume=args.weight_trend_volume,
        ),
        mean_reversion_weights=MeanReversionWeights(
            range=args.weight_meanrev_range,
            rsi=args.weight_meanrev_rsi,
        ),
        context_duplicate_flags=duplicate_flags,
    )


# ---------------------------------------------------------------------------
# Quote field resolver (Task 3.2 — Req 4.1, 5.7)
# ---------------------------------------------------------------------------


_QUOTE_FIELD_MAP: Mapping[str, Mapping[str, str | None]] = {
    "yfinance": {
        "ma_200d": "ma_200d",
        "ma_50d": "ma_50d",
        "volume_average": "volume_average",
        "volume_average_10d": "volume_average_10d",
    },
    "fmp": {
        "ma_200d": "ma200",
        "ma_50d": "ma50",
        "volume_average": None,
        "volume_average_10d": None,
    },
}


@dataclass(frozen=True)
class QuoteFields:
    """Provider-neutral view of a quote record.

    The per-field values are emitted downstream under these *logical*
    names regardless of which provider supplied them. Missing fields
    resolve to ``None`` so partial quote payloads (bond ETFs, halted
    sessions) survive without special-casing each provider.
    """

    last_price: float | None
    prev_close: float | None
    year_high: float | None
    year_low: float | None
    ma_200d: float | None
    ma_50d: float | None
    volume_average: float | None
    volume_average_10d: float | None


def resolve_quote_fields(quote_row: dict[str, Any], provider: str) -> QuoteFields:
    """Translate a raw quote record into logical ``QuoteFields`` per provider.

    Adding a provider requires extending ``_QUOTE_FIELD_MAP`` — omission
    raises ``KeyError`` at resolution time rather than silently falling
    through an ``or``-chain (Req 4.1 "closed-choice map"). Under
    ``fmp`` the ``volume_average*`` keys deliberately resolve to
    ``None`` (FMP does not populate them); the Quality Flag Emitter
    downstream appends ``volume_reference_unavailable_on_provider``.
    """

    field_map = _QUOTE_FIELD_MAP[provider]  # KeyError on unknown provider.

    def _read(logical: str) -> float | None:
        native = field_map.get(logical, logical)
        if native is None:
            return None
        return _to_float(quote_row.get(native))

    return QuoteFields(
        last_price=_to_float(quote_row.get("last_price")),
        prev_close=_to_float(quote_row.get("prev_close")),
        year_high=_to_float(quote_row.get("year_high")),
        year_low=_to_float(quote_row.get("year_low")),
        ma_200d=_read("ma_200d"),
        ma_50d=_read("ma_50d"),
        volume_average=_read("volume_average"),
        volume_average_10d=_read("volume_average_10d"),
    )


# ---------------------------------------------------------------------------
# Last-price fallback + technical-indicator extraction (Task 3.3 — Req 4.3-4.7)
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    """Coerce numeric-like values to ``float``; ``None`` / NaN / parse-errors become ``None``.

    Mirrors the helper copied across ``scripts/sector_score.py`` and
    ``scripts/momentum.py`` — hosted inline here rather than promoted
    to ``_common.py`` while it is only used by three wrappers.
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


@dataclass(frozen=True)
class LastPriceResolution:
    """Result of the three-rung last-price fallback chain (Req 4.7).

    ``flag`` is ``None`` iff rung 1 succeeded (quote's own ``last_price``
    carried usable data). Otherwise it carries the closed-enumeration
    flag string from ``DATA_QUALITY_FLAGS`` that identifies which rung
    supplied the value (or that no rung did).
    """

    value: float | None
    flag: Literal[
        None,
        "last_price_from_prev_close",
        "last_price_from_historical_close",
        "last_price_unavailable",
    ]


def resolve_last_price(
    quote: QuoteFields,
    history_rows: list[dict[str, Any]],
) -> LastPriceResolution:
    """Resolve ``last_price`` via ``quote.last_price → prev_close → historical[-1].close``.

    Applied universally regardless of ``--provider`` — on FMP the first
    rung succeeds so the fallback is a no-op, on yfinance it keeps
    bond ETFs usable (Live finding L3). The three fallback flag
    strings (``"last_price_from_prev_close"``,
    ``"last_price_from_historical_close"``, ``"last_price_unavailable"``)
    are members of ``DATA_QUALITY_FLAGS`` so the downstream Quality
    Flag Emitter can append them without a validation error.
    """

    if quote.last_price is not None:
        return LastPriceResolution(value=quote.last_price, flag=None)

    if quote.prev_close is not None:
        return LastPriceResolution(
            value=quote.prev_close, flag="last_price_from_prev_close"
        )

    if history_rows:
        last_close = _to_float(history_rows[-1].get("close"))
        if last_close is not None:
            return LastPriceResolution(
                value=last_close, flag="last_price_from_historical_close"
            )

    return LastPriceResolution(value=None, flag="last_price_unavailable")


def extract_clenow_factor(row: dict[str, Any] | None) -> float | None:
    """Coerce the Clenow ``factor`` (emitted as a stringified float) to numeric.

    Follows ``scripts/sector_score.py::fetch_clenow`` — a non-numeric or
    missing ``factor`` collapses to ``None`` so ``signals.clenow_126``
    emits as JSON ``null`` rather than leaking a raw string.
    """

    if not row:
        return None
    return _to_float(row.get("factor"))


def _extract_suffix_value(
    records: list[dict[str, Any]],
    *,
    key_test: callable,  # type: ignore[valid-type]
) -> float | None:
    """Walk records in reverse and return the first numeric cell whose column passes ``key_test``."""

    for row in reversed(records):
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if not isinstance(key, str):
                continue
            if not key_test(key):
                continue
            f = _to_float(value)
            if f is not None:
                return f
    return None


def extract_rsi_14(records: list[dict[str, Any]]) -> float | None:
    """Extract RSI(14) from ``obb.technical.rsi`` records.

    Case-insensitive suffix / substring match on ``"rsi"`` (Req 4.4) —
    the live-verified column name is ``close_RSI_14`` but the
    case-insensitive match tolerates minor OpenBB column-naming drift.
    """

    return _extract_suffix_value(records, key_test=lambda k: "rsi" in k.lower())


def extract_macd_histogram(records: list[dict[str, Any]]) -> float | None:
    """Extract MACD histogram from ``obb.technical.macd`` records.

    Case-sensitive substring match on ``"MACDh"`` (Req 4.5) — the
    live-verified column name is ``close_MACDh_12_26_9``. The
    case-sensitivity is load-bearing: a case-insensitive match would
    collide with the ``MACD`` / ``MACDs`` columns that share the
    lowercase form.
    """

    return _extract_suffix_value(records, key_test=lambda k: "MACDh" in k)


# ---------------------------------------------------------------------------
# Per-ticker data fetcher (Task 3.1 — Req 4.2-4.8, 13.3, 13.4)
# ---------------------------------------------------------------------------


_FATAL_CATEGORY_VALUES: frozenset[str] = frozenset(
    {ErrorCategory.CREDENTIAL.value, ErrorCategory.PLAN_INSUFFICIENT.value}
)


@dataclass(frozen=True)
class TickerBundle:
    """Typed bundle returned by ``fetch_ticker_bundle`` for one ticker.

    Mirrors ``sector_score._classify_ticker_failure`` partial-success
    handling: ``ok`` is ``True`` iff *any* primary artefact carries
    usable data even when some stages failed. ``fatal_category`` is
    set (and ``ok`` is ``False``) iff a fatal-category error
    (``credential`` / ``plan_insufficient``) short-circuited the
    bundle — the caller uses this to skip downstream work for that
    ticker without burning budget.
    """

    symbol: str
    provider: str
    ok: bool
    quote_row: dict[str, Any] | None
    history_rows: list[dict[str, Any]]
    clenow_row: dict[str, Any] | None
    rsi_rows: list[dict[str, Any]]
    macd_rows: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    fatal_category: str | None


_EMPTY_HISTORY_FAILURE: dict[str, Any] = {
    "error": "empty_history",
    "error_type": "EmptyResult",
    "error_category": ErrorCategory.OTHER.value,
}


def _failure_from_call(
    call: dict[str, Any],
    *,
    stage: str,
    provider: str,
    symbol: str,
) -> dict[str, Any]:
    """Build a per-stage failure record from a ``safe_call`` failure dict."""

    return {
        "stage": stage,
        "provider": provider,
        "symbol": symbol,
        "error": call.get("error"),
        "error_type": call.get("error_type"),
        "error_category": call.get("error_category"),
    }


def _empty_history_failure(stage: str, provider: str, symbol: str) -> dict[str, Any]:
    """Build a per-stage failure record for a technical skipped on empty history."""

    return {"stage": stage, "provider": provider, "symbol": symbol, **_EMPTY_HISTORY_FAILURE}


def fetch_ticker_bundle(
    ticker: str,
    *,
    provider: str,
    historical_lookback_days: int = HISTORICAL_LOOKBACK_DAYS,
) -> TickerBundle:
    """Issue the five OpenBB calls for one ticker and return a typed bundle.

    Call sequence is fixed (Req 4.8 / Req 13.3): ``quote``,
    ``historical(start=today-N)``, ``clenow(data=history.results,
    period=126)``, ``rsi(data=history.results, length=14)``,
    ``macd(data=history.results, fast=12, slow=26, signal=9)``. Every
    call is guarded by ``_common.safe_call``; no retry loop is
    introduced (Req 13.4). The three technicals consume the **same**
    ``history.results`` reference — no re-fetch.

    Short-circuit rules:

    - On quote ``credential`` / ``plan_insufficient`` failure, skip
      the remaining four calls (avoid burning budget needlessly);
      ``fatal_category`` is set and ``ok`` is ``False``.
    - On historical failure *or* empty ``history.results``, skip the
      three technicals and record an ``empty_history`` failure for
      each, so the per-ticker call count stays ≤ 5.
    - On non-fatal failures (``transient`` / ``other``), keep
      fetching; partial data keeps the row usable.
    """

    start = (date.today() - timedelta(days=historical_lookback_days)).isoformat()
    failures: list[dict[str, Any]] = []
    fatal_category: str | None = None

    # ---- 1. quote ----
    quote_call = safe_call(
        obb.equity.price.quote,
        symbol=ticker,
        provider=provider,
    )
    quote_row: dict[str, Any] | None = None
    if quote_call.get("ok"):
        quote_records = quote_call.get("records") or []
        quote_row = quote_records[0] if quote_records else None
    else:
        failures.append(
            _failure_from_call(quote_call, stage="quote", provider=provider, symbol=ticker)
        )
        if quote_call.get("error_category") in _FATAL_CATEGORY_VALUES:
            fatal_category = quote_call.get("error_category")
            return TickerBundle(
                symbol=ticker,
                provider=provider,
                ok=False,
                quote_row=None,
                history_rows=[],
                clenow_row=None,
                rsi_rows=[],
                macd_rows=[],
                failures=failures,
                fatal_category=fatal_category,
            )

    # ---- 2. historical (capture the OBBject so technicals can share .results) ----
    hist_capture: dict[str, Any] = {"obj": None}

    def _historical_call() -> Any:
        obj = obb.equity.price.historical(
            symbol=ticker, start_date=start, provider=provider
        )
        hist_capture["obj"] = obj
        return obj

    hist_call = safe_call(_historical_call)
    history_rows: list[dict[str, Any]] = []
    if hist_call.get("ok"):
        history_rows = hist_call.get("records") or []
    else:
        failures.append(
            _failure_from_call(
                hist_call, stage="historical", provider=provider, symbol=ticker
            )
        )
        if hist_call.get("error_category") in _FATAL_CATEGORY_VALUES:
            fatal_category = hist_call.get("error_category")

    # ---- 3-5. technicals — skipped when history is empty or historical failed ----
    results_ref: Any = None
    if hist_capture["obj"] is not None and history_rows:
        results_ref = hist_capture["obj"].results

    clenow_row: dict[str, Any] | None = None
    rsi_rows: list[dict[str, Any]] = []
    macd_rows: list[dict[str, Any]] = []

    if fatal_category is None and results_ref is not None:
        # ---- 3. clenow ----
        clenow_call = safe_call(
            obb.technical.clenow,
            data=results_ref,
            target="close",
            period=126,
        )
        if clenow_call.get("ok"):
            clenow_records = clenow_call.get("records") or []
            if clenow_records:
                clenow_row = clenow_records[-1]
            else:
                failures.append(_empty_history_failure("clenow", provider, ticker))
        else:
            failures.append(
                _failure_from_call(
                    clenow_call, stage="clenow", provider=provider, symbol=ticker
                )
            )

        # ---- 4. rsi ----
        rsi_call = safe_call(
            obb.technical.rsi,
            data=results_ref,
            target="close",
            length=14,
        )
        if rsi_call.get("ok"):
            rsi_rows = rsi_call.get("records") or []
            if not rsi_rows:
                failures.append(_empty_history_failure("rsi", provider, ticker))
        else:
            failures.append(
                _failure_from_call(
                    rsi_call, stage="rsi", provider=provider, symbol=ticker
                )
            )

        # ---- 5. macd ----
        macd_call = safe_call(
            obb.technical.macd,
            data=results_ref,
            target="close",
            fast=12,
            slow=26,
            signal=9,
        )
        if macd_call.get("ok"):
            macd_rows = macd_call.get("records") or []
            if not macd_rows:
                failures.append(_empty_history_failure("macd", provider, ticker))
        else:
            failures.append(
                _failure_from_call(
                    macd_call, stage="macd", provider=provider, symbol=ticker
                )
            )
    else:
        # History failed or empty — record an empty-history failure for each
        # skipped technical so the caller still sees per-stage accountability.
        for stage in ("clenow", "rsi", "macd"):
            failures.append(_empty_history_failure(stage, provider, ticker))

    has_usable_data = any(
        (
            quote_row is not None,
            bool(history_rows),
            clenow_row is not None,
            bool(rsi_rows),
            bool(macd_rows),
        )
    )

    return TickerBundle(
        symbol=ticker,
        provider=provider,
        ok=has_usable_data and fatal_category is None,
        quote_row=quote_row,
        history_rows=history_rows,
        clenow_row=clenow_row,
        rsi_rows=rsi_rows,
        macd_rows=macd_rows,
        failures=failures,
        fatal_category=fatal_category,
    )


# ---------------------------------------------------------------------------
# Derived indicators (Task 4 — Req 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7)
# ---------------------------------------------------------------------------


def compute_range_pct_52w(
    last_price: float | None,
    year_high: float | None,
    year_low: float | None,
) -> float | None:
    """``(last - year_low) / (year_high - year_low)`` when all inputs are non-null
    and the denominator is non-zero; ``None`` otherwise (Req 5.1)."""

    if last_price is None or year_high is None or year_low is None:
        return None
    denom = year_high - year_low
    if denom == 0:
        return None
    return (last_price - year_low) / denom


def compute_ma200_distance(
    last_price: float | None,
    ma_200d: float | None,
) -> float | None:
    """``(last - ma_200d) / ma_200d`` when both inputs are non-null and
    ``ma_200d != 0``; ``None`` otherwise (Req 5.2)."""

    if last_price is None or ma_200d is None or ma_200d == 0:
        return None
    return (last_price - ma_200d) / ma_200d


_VolumeZFlag = Literal[
    "volume_window_too_short",
    "volume_non_positive",
    "volume_zero_dispersion",
]


def compute_volume_z_20d(
    history_rows: list[dict[str, Any]],
    estimator: Literal["robust", "classical"],
) -> tuple[float | None, _VolumeZFlag | None, float | None]:
    """Compute the 20-session volume z-score on ``history_rows[-1].volume``.

    Reference window: ``history_rows[-21:-1]`` — the 20 sessions
    *excluding* the latest session (Req 5.3). Requires ≥21 rows.

    Narrowest-gate ordering for degenerate input (Req 5.5 / design
    "degenerate-input flag order"):

      1. ``volume_window_too_short`` — fewer than 21 history rows, or
         any reference / latest value is null.
      2. ``volume_non_positive`` — under ``robust`` only, any reference
         or latest value is ≤ 0 (log transform is undefined).
      3. ``volume_zero_dispersion`` — reference dispersion (MAD or
         stdev, depending on estimator) is zero, so the denominator
         would be zero.

    Returns ``(z, flag, latest_volume)``. ``latest_volume`` is sourced
    from ``history_rows[-1].volume`` so the emitted scalar and the
    z-score computation see the same session; this also keeps the
    path provider-shape-invariant (design §Derived Indicators: "taken
    from ``history_rows[-1].volume``, not from the quote record").
    """

    # Gate 1: window length — narrowest gate fires first.
    if len(history_rows) < 21:
        return None, "volume_window_too_short", None

    ref_rows = history_rows[-21:-1]
    latest_row = history_rows[-1]
    latest_volume = _to_float(latest_row.get("volume"))
    ref_volumes: list[float] = []
    any_ref_null = False
    for row in ref_rows:
        v = _to_float(row.get("volume"))
        if v is None:
            any_ref_null = True
            break
        ref_volumes.append(v)

    if any_ref_null or latest_volume is None or len(ref_volumes) != 20:
        return None, "volume_window_too_short", latest_volume

    if estimator == "robust":
        # Gate 2: robust path cannot log(≤ 0).
        if latest_volume <= 0 or any(v <= 0 for v in ref_volumes):
            return None, "volume_non_positive", latest_volume
        log_ref = [math.log(v) for v in ref_volumes]
        med = median(log_ref)
        mad = median(abs(x - med) for x in log_ref)
        if mad == 0:
            return None, "volume_zero_dispersion", latest_volume
        z = (math.log(latest_volume) - med) / (1.4826 * mad)
        return z, None, latest_volume

    # Classical path: mean / stdev.
    sd = stdev(ref_volumes)
    if sd == 0:
        return None, "volume_zero_dispersion", latest_volume
    z = (latest_volume - mean(ref_volumes)) / sd
    return z, None, latest_volume


def build_volume_reference(
    quote: QuoteFields,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Build the ``volume_reference`` sibling block with fixed window labels.

    Emits ``{"window": "3m_rolling", "value": ...}`` and
    ``{"window": "10d", "value": ...}`` verbatim; when either value is
    ``None`` (typically FMP) the ``window`` label is preserved and
    ``"volume_reference_unavailable_on_provider"`` fires exactly once
    per row even if both values are null (Req 5.7).
    """

    reference = {
        "volume_average": {"window": "3m_rolling", "value": quote.volume_average},
        "volume_average_10d": {"window": "10d", "value": quote.volume_average_10d},
    }
    flags: list[str] = []
    if quote.volume_average is None or quote.volume_average_10d is None:
        flags.append("volume_reference_unavailable_on_provider")
    return reference, flags


@dataclass(frozen=True)
class DerivedIndicators:
    """Bundle of per-ticker derived indicators (Req 5.1-5.7).

    ``volume_avg_window`` is ``"20d_real"`` iff ``volume_z_20d`` is
    non-null (Req 5.6: the tag must only appear for rows that actually
    carry a z-score), else ``None`` so consumers never see the label
    without the matching scalar.

    ``extra_flags`` carries every volume-side ``data_quality_flags``
    entry that this layer is responsible for — the z-score degenerate
    gate and the reference-unavailable flag. Other flags (last-price
    fallback, rsi_oversold_lt_20, basket_too_small_for_z) belong to
    other layers.
    """

    range_pct_52w: float | None
    ma200_distance: float | None
    volume_z_20d: float | None
    volume_avg_window: Literal["20d_real"] | None
    volume_z_estimator: Literal["robust", "classical"]
    latest_volume: float | None
    volume_reference: dict[str, dict[str, Any]]
    extra_flags: list[str]


def compute_derived_indicators(
    quote: QuoteFields,
    last_price: LastPriceResolution,
    history_rows: list[dict[str, Any]],
    estimator: Literal["robust", "classical"],
) -> DerivedIndicators:
    """Compose ``range_pct_52w``, ``ma200_distance``, ``volume_z_20d``,
    and the ``volume_reference`` block into a single typed bundle.

    Does not issue any OpenBB call — consumes only the resolved
    ``QuoteFields`` / ``LastPriceResolution`` / ``history_rows`` from
    upstream fetchers.
    """

    range_pct = compute_range_pct_52w(last_price.value, quote.year_high, quote.year_low)
    ma_distance = compute_ma200_distance(last_price.value, quote.ma_200d)
    volume_z, volume_flag, latest_volume = compute_volume_z_20d(history_rows, estimator)
    reference, reference_flags = build_volume_reference(quote)

    extra_flags: list[str] = []
    if volume_flag is not None:
        extra_flags.append(volume_flag)
    extra_flags.extend(reference_flags)

    return DerivedIndicators(
        range_pct_52w=range_pct,
        ma200_distance=ma_distance,
        volume_z_20d=volume_z,
        volume_avg_window="20d_real" if volume_z is not None else None,
        volume_z_estimator=estimator,
        latest_volume=latest_volume,
        volume_reference=reference,
        extra_flags=extra_flags,
    )


# ---------------------------------------------------------------------------
# Cross-sectional scoring (Task 5 — Req 6.1-6.11, 7.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalBundle:
    """Per-ticker signal inputs for cross-sectional normalization.

    The last two fields use the *transformed* sign convention:
    ``inv_range_pct_52w = (1 - range_pct_52w)`` and
    ``oversold_rsi_14 = (50 - rsi_14)``, so a higher value
    consistently means "more mean-reverting". ``ok`` is ``False``
    when the upstream ``TickerBundle`` collapsed — such rows are
    excluded from ``basket_size`` even if they carry non-null signal
    values.
    """

    symbol: str
    ok: bool
    clenow_126: float | None
    macd_histogram: float | None
    volume_z_20d: float | None
    inv_range_pct_52w: float | None
    oversold_rsi_14: float | None


@dataclass(frozen=True)
class ScoredRow:
    """Per-ticker cross-sectional output (Req 6.2 / 6.4 / 6.5 / 6.10)."""

    symbol: str
    trend_z: float | None
    mean_reversion_z: float | None
    trend_score_0_100: float | None
    mean_reversion_score_0_100: float | None
    blended_score_0_100: float | None
    z_scores: dict[str, float | None]
    basket_size: int
    basket_size_sufficient: bool
    per_signal_small_basket_flags: list[str]


@dataclass(frozen=True)
class CrossSectionalResult:
    """Aggregate cross-sectional output.

    ``basket_warning`` is non-None when the eligible-row count drops
    below three (Req 6.8) — the envelope assembler routes it into
    ``aggregate_emit``'s ``extra_warnings`` so the JSON contract
    carries a top-level ``{symbol: null, error, error_category}``
    entry without the per-row z-scores masking the degradation.
    """

    rows: list[ScoredRow]
    basket_warning: dict[str, Any] | None


_MIN_BASKET_SIZE: int = 3


def _zscore_min_basket(values: list[float | None]) -> list[float | None]:
    """Per-signal cross-sectional z-score with ``min_basket=3``.

    Stricter than ``sector_score.zscore`` (which uses ``min_basket=2``)
    — entry-timing baskets are typically n=5-10, where a 2-point
    population makes the z-score a pure sign indicator and the
    per-signal flag ``basket_too_small_for_z(<signal>)`` is more
    informative than a collapsed value.
    """

    clean = [v for v in values if v is not None]
    if len(clean) < _MIN_BASKET_SIZE:
        return [None] * len(values)
    m = mean(clean)
    sd = stdev(clean)
    if sd == 0:
        return [0.0 if v is not None else None for v in values]
    return [(v - m) / sd if v is not None else None for v in values]


def _weighted_subscore(
    z_by_key: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    """Sum-of-available-weights composition matching ``sector_score.build_scores``.

    Missing z-scores drop out of both the numerator and the
    denominator so a signal collapsing to ``None`` degrades the
    sub-score gracefully rather than inverting its sign. Returns
    ``None`` when every weight drops out (Req 6.2).
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
    """``clip(50 + z * 25, 0, 100)`` transform (Req 6.4)."""

    if z is None:
        return None
    return max(0.0, min(100.0, 50.0 + z * 25.0))


def _compute_basket_size(bundles: list[SignalBundle]) -> int:
    """Row-level basket size: count of ``ok: true`` rows with at least
    one non-null SCORER_SIGNAL_KEYS value (Req 6.11)."""

    count = 0
    for b in bundles:
        if not b.ok:
            continue
        values = [getattr(b, key) for key in SCORER_SIGNAL_KEYS]
        if any(v is not None for v in values):
            count += 1
    return count


def _blend(
    profile: str,
    trend_z: float | None,
    mean_reversion_z: float | None,
    trend_score: float | None,
    mean_reversion_score: float | None,
) -> float | None:
    """Resolve ``blended_score_0_100`` by ``--blend-profile`` (Req 6.5 / 6.6)."""

    if profile == "none":
        return None
    if profile == "trend":
        return trend_score
    if profile == "mean_reversion":
        return mean_reversion_score
    if profile == "balanced":
        # Sum-of-available-weights blend: when one sub-score is null,
        # degrade to the other instead of collapsing to null. The
        # equal 0.5 / 0.5 weights become 1.0 / 1.0 on the single
        # surviving side, which is equivalent to mirroring the
        # sub-score — mirroring the "missing signal drops out of the
        # denominator" pattern from ``_weighted_subscore``.
        blended_z = _weighted_subscore(
            {"trend": trend_z, "mean_reversion": mean_reversion_z},
            {"trend": 0.5, "mean_reversion": 0.5},
        )
        return _to_100(blended_z)
    raise ValueError(f"unknown --blend-profile value: {profile!r}")


def compute_cross_sectional(
    bundles: list[SignalBundle],
    *,
    trend_weights: TrendWeights,
    mean_reversion_weights: MeanReversionWeights,
    blend_profile: str,
) -> CrossSectionalResult:
    """Normalize signals across the basket, compose sub-scores, and blend.

    Iterates ``SCORER_SIGNAL_KEYS`` only — the constant structurally
    prevents earnings fields from ever being mixed into composites
    (Req 7.1). The per-signal z-score uses ``min_basket=3``; when
    fewer than three rows carry a non-null value for a signal, the
    signal collapses to ``None`` on every row and each row that had
    a non-null input receives a ``basket_too_small_for_z(<signal>)``
    flag translated via ``_SIGNAL_FLAG_NAME`` so agents see the
    original signal name they recognise from ``signals.*``.

    When the eligible-row count (``basket_size``) drops below three
    (Req 6.8), every score is set to ``None`` and ``basket_warning``
    is populated so the envelope assembler can surface the
    validation warning at the top level.
    """

    # Pre-compute per-signal value arrays in SCORER_SIGNAL_KEYS order.
    signal_values: dict[str, list[float | None]] = {
        key: [getattr(b, key) for b in bundles] for key in SCORER_SIGNAL_KEYS
    }

    # Per-signal z-scores with min_basket=3.
    signal_z: dict[str, list[float | None]] = {
        key: _zscore_min_basket(values) for key, values in signal_values.items()
    }

    # Which signals collapsed for lack of basket? One flag per affected
    # row per collapsed signal, emitted with the original-name
    # translation from `_SIGNAL_FLAG_NAME`.
    collapsed_signals: set[str] = {
        key for key, zs in signal_z.items() if all(z is None for z in zs)
    }

    basket_size = _compute_basket_size(bundles)
    basket_size_sufficient = basket_size >= _MIN_BASKET_SIZE
    basket_warning: dict[str, Any] | None = None
    if not basket_size_sufficient:
        basket_warning = {
            "symbol": None,
            "error": "insufficient basket size for cross-sectional z-score",
            "error_category": ErrorCategory.VALIDATION.value,
        }

    trend_weight_map = {
        "clenow_126": trend_weights.clenow,
        "macd_histogram": trend_weights.macd,
        "volume_z_20d": trend_weights.volume,
    }
    mr_weight_map = {
        "inv_range_pct_52w": mean_reversion_weights.range,
        "oversold_rsi_14": mean_reversion_weights.rsi,
    }

    rows: list[ScoredRow] = []
    for i, bundle in enumerate(bundles):
        z_scores: dict[str, float | None] = {
            key: signal_z[key][i] for key in SCORER_SIGNAL_KEYS
        }

        # Compose sub-scores from the per-row z values.
        trend_z = _weighted_subscore(z_scores, trend_weight_map)
        mean_reversion_z = _weighted_subscore(z_scores, mr_weight_map)
        z_scores["trend_z"] = trend_z
        z_scores["mean_reversion_z"] = mean_reversion_z

        # Small-basket flags: fire only for signals that collapsed AND
        # for which this row had a non-null input — otherwise the row
        # already had null for this signal regardless of basket size.
        small_basket_flags: list[str] = []
        for key in SCORER_SIGNAL_KEYS:
            if key not in collapsed_signals:
                continue
            if signal_values[key][i] is None:
                continue
            small_basket_flags.append(
                f"basket_too_small_for_z({_SIGNAL_FLAG_NAME[key]})"
            )

        if basket_size_sufficient:
            trend_score = _to_100(trend_z)
            mean_reversion_score = _to_100(mean_reversion_z)
            blended = _blend(
                blend_profile, trend_z, mean_reversion_z, trend_score, mean_reversion_score
            )
        else:
            trend_score = None
            mean_reversion_score = None
            blended = None

        rows.append(
            ScoredRow(
                symbol=bundle.symbol,
                trend_z=trend_z,
                mean_reversion_z=mean_reversion_z,
                trend_score_0_100=trend_score,
                mean_reversion_score_0_100=mean_reversion_score,
                blended_score_0_100=blended,
                z_scores=z_scores,
                basket_size=basket_size,
                basket_size_sufficient=basket_size_sufficient,
                per_signal_small_basket_flags=small_basket_flags,
            )
        )

    return CrossSectionalResult(rows=rows, basket_warning=basket_warning)


# ---------------------------------------------------------------------------
# Earnings-proximity flag (Task 6.1 — Req 7.1, 7.3, 7.4, 7.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EarningsFlagFields:
    """Per-ticker earnings-proximity fields (Req 7.5).

    ``next_earnings_date`` is an ISO ``YYYY-MM-DD`` string or ``None``;
    ``days_to_next_earnings`` is a calendar-day ``int`` or ``None``;
    ``earnings_proximity_warning`` is always a boolean — ``True`` when
    a date is available and within the threshold, ``False`` otherwise
    (including the null path). Earnings fields are deliberately kept
    outside every composite (Req 7.1) and remain a standalone gate.
    """

    next_earnings_date: str | None
    days_to_next_earnings: int | None
    earnings_proximity_warning: bool


def compute_proximity_flag(
    symbol: str,
    earnings_index: EarningsIndex,
    *,
    today: date,
    threshold_days: int,
) -> EarningsFlagFields:
    """Resolve the earnings-proximity fields for one ticker.

    Looks up ``symbol`` in ``earnings_index.by_symbol``; when absent
    every field collapses to ``None`` except the warning which stays
    ``False`` (Req 7.4). When present, emits the ISO date string, the
    integer calendar-day delta, and ``warning = days <= threshold``
    (Req 7.3, inclusive boundary).
    """

    report_date = earnings_index.by_symbol.get(symbol)
    if report_date is None:
        return EarningsFlagFields(
            next_earnings_date=None,
            days_to_next_earnings=None,
            earnings_proximity_warning=False,
        )

    delta = (report_date - today).days
    return EarningsFlagFields(
        next_earnings_date=report_date.isoformat(),
        days_to_next_earnings=delta,
        earnings_proximity_warning=delta <= threshold_days,
    )


# ---------------------------------------------------------------------------
# Interpretation builder (Task 6.2 — Req 9.4, 9.5, 11.7)
# ---------------------------------------------------------------------------


def build_interpretation(context: str) -> dict[str, str]:
    """Build the per-row ``interpretation`` object with exactly five keys.

    Literal strings for ``score_meaning`` / ``trend_polarity`` /
    ``mean_reversion_polarity``; ``reading_for_context`` is keyed off
    the per-ticker context via ``_READING_BY_CONTEXT``. The function
    never constructs the scalar named in Req 9.5 — the negative
    invariant is enforced by absence and locked by a source-text
    assertion in the flags unit tests against regression.
    """

    return {
        "score_meaning": "basket_internal_rank",
        "trend_polarity": "high=stronger_trend",
        "mean_reversion_polarity": "high=more_oversold",
        "context": context,
        "reading_for_context": _READING_BY_CONTEXT[context],
    }


# ---------------------------------------------------------------------------
# Quality flag catalog helpers (Task 6.3 — Req 9.6, 9.7, 9.10)
# ---------------------------------------------------------------------------


def append_quality_flag(flags: list[str], flag: str) -> None:
    """Append ``flag`` to ``flags`` after validating catalog membership.

    Unknown strings raise ``ValueError`` at append time so the
    closed-enumeration contract holds structurally rather than by
    convention (Req 9.10). Callers that want the membership check
    without mutation should test ``flag in DATA_QUALITY_FLAGS``
    directly.
    """

    if flag not in DATA_QUALITY_FLAGS:
        raise ValueError(
            f"{flag!r} is not a member of DATA_QUALITY_FLAGS; adding a new "
            "flag requires updating the closed enumeration per Req 9.10"
        )
    flags.append(flag)


def collect_data_quality_flags(
    *,
    rsi_14: float | None,
    basket_size_sufficient: bool,
    upstream_flags: list[str],
) -> list[str]:
    """Aggregate per-row ``data_quality_flags[]`` in emission order.

    Copies upstream-produced flags (last-price fallback, volume gates,
    context duplicates, per-signal small-basket) verbatim then appends
    the row-level flags this layer owns:

    - ``rsi_oversold_lt_20`` when ``rsi_14 < 20`` (Req 9.6; strict
      less-than — equality does not fire).
    - ``basket_too_small_for_z`` when ``basket_size_sufficient`` is
      ``False`` (Req 9.7). Per-signal variants
      (``basket_too_small_for_z(<signal>)``) are produced by the
      cross-sectional scorer and travel in ``upstream_flags``.

    Every entry — upstream or freshly appended — is validated against
    ``DATA_QUALITY_FLAGS`` via ``append_quality_flag`` so a bogus
    string cannot silently leak into the row.
    """

    flags: list[str] = []
    for f in upstream_flags:
        append_quality_flag(flags, f)
    if rsi_14 is not None and rsi_14 < 20:
        append_quality_flag(flags, "rsi_oversold_lt_20")
    if not basket_size_sufficient:
        append_quality_flag(flags, "basket_too_small_for_z")
    return flags


# ---------------------------------------------------------------------------
# Envelope assembly helpers (Task 7 — Req 3.5, 3.6, 6.6, 6.9, 8.2, 9.1, 9.2,
# 9.3, 9.8, 9.9)
# ---------------------------------------------------------------------------


def build_signals_block(
    *,
    clenow_126: float | None,
    range_pct_52w: float | None,
    rsi_14: float | None,
    macd_histogram: float | None,
    volume_z_20d: float | None,
    ma200_distance: float | None,
    last_price: float | None,
    year_high: float | None,
    year_low: float | None,
    ma_200d: float | None,
    ma_50d: float | None,
    latest_volume: float | None,
) -> dict[str, float | None]:
    """Assemble the twelve-field per-ticker ``signals`` block (Req 9.3).

    Keyword-only to force call-site clarity — the twelve positional
    floats are otherwise easy to transpose silently. Values pass through
    verbatim; sanitisation of NaN / ±Inf happens downstream in
    ``_common.sanitize_for_json`` at emit time (Req 8.6).
    """

    return {
        "clenow_126": clenow_126,
        "range_pct_52w": range_pct_52w,
        "rsi_14": rsi_14,
        "macd_histogram": macd_histogram,
        "volume_z_20d": volume_z_20d,
        "ma200_distance": ma200_distance,
        "last_price": last_price,
        "year_high": year_high,
        "year_low": year_low,
        "ma_200d": ma_200d,
        "ma_50d": ma_50d,
        "latest_volume": latest_volume,
    }


def build_ok_row(
    *,
    symbol: str,
    provider: str,
    context: str,
    signals: dict[str, float | None],
    z_scores: dict[str, float | None],
    trend_score_0_100: float | None,
    mean_reversion_score_0_100: float | None,
    blended_score_0_100: float | None,
    blend_profile: str,
    basket_size: int,
    basket_size_sufficient: bool,
    next_earnings_date: str | None,
    days_to_next_earnings: int | None,
    earnings_proximity_warning: bool,
    volume_avg_window: str | None,
    volume_z_estimator: str,
    volume_reference: dict[str, dict[str, Any]],
    data_quality_flags: list[str],
    interpretation: dict[str, str],
) -> dict[str, Any]:
    """Assemble an ``ok: true`` per-ticker row with the minimum schema
    from Req 9.1 plus conditional blend fields (Req 6.6 / 9.2).

    ``rank`` is left as ``None`` here; ``sort_and_rank_rows`` assigns
    the 1-based dense rank after the rows are sorted.
    """

    row: dict[str, Any] = {
        "symbol": symbol,
        "provider": provider,
        "ok": True,
        "context": context,
        "rank": None,
        "trend_score_0_100": trend_score_0_100,
        "mean_reversion_score_0_100": mean_reversion_score_0_100,
        "signals": signals,
        "z_scores": z_scores,
        "basket_size": basket_size,
        "basket_size_sufficient": basket_size_sufficient,
        "next_earnings_date": next_earnings_date,
        "days_to_next_earnings": days_to_next_earnings,
        "earnings_proximity_warning": earnings_proximity_warning,
        "volume_avg_window": volume_avg_window,
        "volume_z_estimator": volume_z_estimator,
        "volume_reference": volume_reference,
        "data_quality_flags": data_quality_flags,
        "interpretation": interpretation,
    }
    if blend_profile != "none":
        row["blended_score_0_100"] = blended_score_0_100
        row["blend_profile"] = blend_profile
    return row


def build_failure_row(
    *,
    symbol: str,
    provider: str,
    context: str,
    error: str | None,
    error_type: str | None,
    error_category: str | None,
) -> dict[str, Any]:
    """Assemble an ``ok: false`` per-ticker row (Req 9.8 / 8.3).

    Score / z_score blocks are deliberately absent so the row carries
    only the envelope-and-failure minimum: ``{symbol, provider, ok,
    context, rank, error, error_type, error_category}``.
    """

    return {
        "symbol": symbol,
        "provider": provider,
        "ok": False,
        "context": context,
        "rank": None,
        "error": error,
        "error_type": error_type,
        "error_category": error_category,
    }


def primary_score_of_row(row: dict[str, Any], blend_profile: str) -> float | None:
    """Return the sort key for ``row`` under the active ``blend_profile``
    (Req 6.9).

    - ``none`` / ``trend``  → ``trend_score_0_100``
    - ``mean_reversion``    → ``mean_reversion_score_0_100``
    - ``balanced``          → ``blended_score_0_100``

    ``ok: false`` rows and rows whose primary score is ``None`` (e.g.
    whole-basket short-circuit per Req 6.8) return ``None`` — the
    caller sinks those to the bottom and assigns ``rank: None``.
    """

    if blend_profile in ("none", "trend"):
        return row.get("trend_score_0_100")
    if blend_profile == "mean_reversion":
        return row.get("mean_reversion_score_0_100")
    if blend_profile == "balanced":
        return row.get("blended_score_0_100")
    raise ValueError(f"unknown blend_profile: {blend_profile!r}")


def sort_and_rank_rows(
    rows: list[dict[str, Any]], blend_profile: str
) -> list[dict[str, Any]]:
    """Sort rows by the active primary score and assign 1-based dense rank.

    Sort rule (Req 6.9, Design §Envelope Assembler / Decision 4):

    - Primary key: descending by ``primary_score_of_row`` with ``None``
      mapped to ``-inf`` so null-scored / ``ok: false`` rows sink.
    - Tie-break: ascending by ``symbol`` so tied rows emit in a stable,
      alphabetical order (prevents dict-insertion-order drift).

    Rank rule (Design §Envelope Assembler, switching from competition-
    rank to dense-rank because entry-timing's small baskets (n=5-10)
    make competition-rank gaps visually misleading): tied rows share a
    rank, and the next distinct score increments by 1 (not by the tie
    count). Null-score / ``ok: false`` rows receive ``rank: None`` and
    never participate in the rank sequence.
    """

    def sort_key(row: dict[str, Any]) -> tuple[float, str]:
        score = primary_score_of_row(row, blend_profile)
        # Descending by score → negate; nulls sink → -inf → negate to +inf.
        primary = -score if score is not None else math.inf
        return (primary, row.get("symbol") or "")

    sorted_rows = sorted(rows, key=sort_key)

    # Dense-rank assignment.
    current_rank: int = 0
    previous_score: float | None = None
    has_started_ranking = False
    for row in sorted_rows:
        score = primary_score_of_row(row, blend_profile)
        if score is None:
            row["rank"] = None
            continue
        if not has_started_ranking or score != previous_score:
            current_rank += 1
            previous_score = score
            has_started_ranking = True
        row["rank"] = current_rank

    return sorted_rows


def build_data_namespace(
    *,
    config: ScorerConfig,
    missing_tickers: list[str],
    provider_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the ``data`` namespace siblings of ``results`` (Req 8.2).

    ``provider_diagnostics`` is emitted only when at least one stage
    failed (Design §Envelope Assembler), so agents reading
    ``data.provider_diagnostics`` know the field's presence is
    load-bearing — not always-empty boilerplate.

    ``analytical_caveats`` emits a list (not a tuple) so the JSON
    payload carries the three required strings in a standard
    JSON-array shape consumers can iterate without tuple/list branching
    (Req 9.9).

    Reserved envelope keys (``source``, ``collected_at``, ``tool``,
    ``results``) are owned by ``_common.aggregate_emit`` and
    deliberately absent here so the two layers cannot collide
    (enforced by ``_common._normalize_query_meta``).
    """

    data: dict[str, Any] = {
        "provider": config.provider,
        "tickers": list(config.tickers),
        "weights": {
            "trend": {
                "clenow": config.trend_weights.clenow,
                "macd": config.trend_weights.macd,
                "volume": config.trend_weights.volume,
            },
            "mean_reversion": {
                "range": config.mean_reversion_weights.range,
                "rsi": config.mean_reversion_weights.rsi,
            },
        },
        "days_to_next_earnings_unit": "calendar_days",
        "earnings_window_days": config.earnings_window_days,
        "earnings_proximity_days_threshold": config.earnings_proximity_days,
        "missing_tickers": list(missing_tickers),
        "analytical_caveats": list(ANALYTICAL_CAVEATS),
    }
    if provider_diagnostics:
        data["provider_diagnostics"] = list(provider_diagnostics)
    return data


# ---------------------------------------------------------------------------
# Pipeline runner (Task 7 — wires the layers into a single envelope call)
# ---------------------------------------------------------------------------


def _build_signal_bundle(
    symbol: str,
    *,
    ok: bool,
    clenow_126: float | None,
    macd_histogram: float | None,
    volume_z_20d: float | None,
    range_pct_52w: float | None,
    rsi_14: float | None,
) -> SignalBundle:
    """Translate raw signals into the transformed-sign bundle the scorer
    consumes (Req 6.1).

    ``inv_range_pct_52w = (1 - range_pct_52w)`` and
    ``oversold_rsi_14 = (50 - rsi_14)`` encode the
    "higher = more mean-reverting" convention shared across the scorer
    math and the ``z_scores`` block.
    """

    inv_range = None if range_pct_52w is None else (1.0 - range_pct_52w)
    oversold_rsi = None if rsi_14 is None else (50.0 - rsi_14)
    return SignalBundle(
        symbol=symbol,
        ok=ok,
        clenow_126=clenow_126,
        macd_histogram=macd_histogram,
        volume_z_20d=volume_z_20d,
        inv_range_pct_52w=inv_range,
        oversold_rsi_14=oversold_rsi,
    )


def run_pipeline(config: ScorerConfig) -> int:
    """Run the full fetch → derive → score → assemble → emit pipeline.

    Stage order matches the sequence diagram in design.md: one
    earnings-calendar call, then per-ticker (quote + historical + three
    technicals) bundles, then derived indicators per row, then
    cross-sectional normalization, then envelope assembly.
    """

    today = date.today()

    # ---- 1. Earnings calendar (single call, no retry) ----
    earnings_index = fetch_earnings_window(
        config.tickers,
        calendar_provider=config.calendar_provider,
        window_days=config.earnings_window_days,
        today=today,
    )

    # ---- 2. Per-ticker fetch + derive ----
    bundles: dict[str, TickerBundle] = {}
    quote_fields: dict[str, QuoteFields | None] = {}
    last_prices: dict[str, LastPriceResolution] = {}
    derived_by_symbol: dict[str, DerivedIndicators] = {}
    raw_rsi_by_symbol: dict[str, float | None] = {}
    raw_clenow_by_symbol: dict[str, float | None] = {}
    raw_macd_by_symbol: dict[str, float | None] = {}
    provider_diagnostics: list[dict[str, Any]] = []

    if earnings_index.diagnostic is not None:
        provider_diagnostics.append(earnings_index.diagnostic)

    for ticker in config.tickers:
        bundle = fetch_ticker_bundle(ticker, provider=config.provider)
        bundles[ticker] = bundle

        if bundle.quote_row is not None:
            quote = resolve_quote_fields(bundle.quote_row, config.provider)
        else:
            quote = QuoteFields(
                last_price=None,
                prev_close=None,
                year_high=None,
                year_low=None,
                ma_200d=None,
                ma_50d=None,
                volume_average=None,
                volume_average_10d=None,
            )
        quote_fields[ticker] = quote

        last_price = resolve_last_price(quote, bundle.history_rows)
        last_prices[ticker] = last_price

        derived = compute_derived_indicators(
            quote,
            last_price,
            bundle.history_rows,
            config.volume_z_estimator,  # type: ignore[arg-type]
        )
        derived_by_symbol[ticker] = derived

        raw_clenow_by_symbol[ticker] = extract_clenow_factor(bundle.clenow_row)
        raw_rsi_by_symbol[ticker] = extract_rsi_14(bundle.rsi_rows)
        raw_macd_by_symbol[ticker] = extract_macd_histogram(bundle.macd_rows)

        for failure in bundle.failures:
            provider_diagnostics.append(
                {
                    "provider": failure.get("provider"),
                    "stage": failure.get("stage"),
                    "symbol": failure.get("symbol"),
                    "error": failure.get("error"),
                    "error_category": failure.get("error_category"),
                }
            )

    # ---- 3. Cross-sectional scoring ----
    signal_bundles: list[SignalBundle] = [
        _build_signal_bundle(
            t,
            ok=bundles[t].ok,
            clenow_126=raw_clenow_by_symbol[t],
            macd_histogram=raw_macd_by_symbol[t],
            volume_z_20d=derived_by_symbol[t].volume_z_20d,
            range_pct_52w=derived_by_symbol[t].range_pct_52w,
            rsi_14=raw_rsi_by_symbol[t],
        )
        for t in config.tickers
    ]
    cross_sectional = compute_cross_sectional(
        signal_bundles,
        trend_weights=config.trend_weights,
        mean_reversion_weights=config.mean_reversion_weights,
        blend_profile=config.blend_profile,
    )
    scored_by_symbol = {row.symbol: row for row in cross_sectional.rows}

    # ---- 4. Per-ticker row assembly ----
    rows: list[dict[str, Any]] = []
    missing_tickers: list[str] = []
    for ticker in config.tickers:
        context = config.contexts[ticker]
        bundle = bundles[ticker]
        if not bundle.ok:
            missing_tickers.append(ticker)
            first_failure = bundle.failures[0] if bundle.failures else {}
            rows.append(
                build_failure_row(
                    symbol=ticker,
                    provider=config.provider,
                    context=context,
                    error=first_failure.get("error") or "no data available",
                    error_type=first_failure.get("error_type") or "NoData",
                    error_category=(
                        bundle.fatal_category
                        or first_failure.get("error_category")
                        or ErrorCategory.OTHER.value
                    ),
                )
            )
            continue

        quote = quote_fields[ticker]
        derived = derived_by_symbol[ticker]
        last_price = last_prices[ticker]
        scored = scored_by_symbol[ticker]

        proximity = compute_proximity_flag(
            ticker,
            earnings_index,
            today=today,
            threshold_days=config.earnings_proximity_days,
        )

        reference_block, reference_flags = build_volume_reference(quote)

        upstream_flags: list[str] = []
        if last_price.flag is not None:
            upstream_flags.append(last_price.flag)
        upstream_flags.extend(derived.extra_flags)
        upstream_flags.extend(config.context_duplicate_flags.get(ticker, []))
        upstream_flags.extend(scored.per_signal_small_basket_flags)

        data_quality_flags = collect_data_quality_flags(
            rsi_14=raw_rsi_by_symbol[ticker],
            basket_size_sufficient=scored.basket_size_sufficient,
            upstream_flags=upstream_flags,
        )

        signals = build_signals_block(
            clenow_126=raw_clenow_by_symbol[ticker],
            range_pct_52w=derived.range_pct_52w,
            rsi_14=raw_rsi_by_symbol[ticker],
            macd_histogram=raw_macd_by_symbol[ticker],
            volume_z_20d=derived.volume_z_20d,
            ma200_distance=derived.ma200_distance,
            last_price=last_price.value,
            year_high=quote.year_high,
            year_low=quote.year_low,
            ma_200d=quote.ma_200d,
            ma_50d=quote.ma_50d,
            latest_volume=derived.latest_volume,
        )

        rows.append(
            build_ok_row(
                symbol=ticker,
                provider=config.provider,
                context=context,
                signals=signals,
                z_scores=scored.z_scores,
                trend_score_0_100=scored.trend_score_0_100,
                mean_reversion_score_0_100=scored.mean_reversion_score_0_100,
                blended_score_0_100=scored.blended_score_0_100,
                blend_profile=config.blend_profile,
                basket_size=scored.basket_size,
                basket_size_sufficient=scored.basket_size_sufficient,
                next_earnings_date=proximity.next_earnings_date,
                days_to_next_earnings=proximity.days_to_next_earnings,
                earnings_proximity_warning=proximity.earnings_proximity_warning,
                volume_avg_window=derived.volume_avg_window,
                volume_z_estimator=derived.volume_z_estimator,
                volume_reference=reference_block,
                data_quality_flags=data_quality_flags,
                interpretation=build_interpretation(context),
            )
        )

    # ---- 5. Sort + rank + emit ----
    rows = sort_and_rank_rows(rows, config.blend_profile)

    data_namespace = build_data_namespace(
        config=config,
        missing_tickers=missing_tickers,
        provider_diagnostics=provider_diagnostics,
    )

    extra_warnings: list[dict[str, Any]] = []
    if cross_sectional.basket_warning is not None:
        extra_warnings.append(cross_sectional.basket_warning)

    return aggregate_emit(
        rows,
        tool="entry_timing_scorer",
        query_meta=data_namespace,
        extra_warnings=extra_warnings,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        config = build_config(argv)
    except _ConfigError as exc:
        return emit_error(
            str(exc),
            tool="entry_timing_scorer",
            error_category=ErrorCategory.VALIDATION.value,
        )

    _ = sanitize_for_json  # referenced transitively via `aggregate_emit → emit`.
    return run_pipeline(config)


if __name__ == "__main__":
    raise SystemExit(main())
