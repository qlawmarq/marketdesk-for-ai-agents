"""Sector ETF composite scoring — core of the mid-term sector-rotation strategy.

Blends multiple signals across ETFs and produces a 0-100 composite score ranking.
Implements the capability that the analyst's instruction §5 "mid-term strategy"
assumes but did not previously exist, using OpenBB end-to-end.

Signals aggregated:
  1. Price momentum (3m / 6m / 12m returns, from Finviz price_performance)
  2. Clenow exponential-regression momentum factor (90-day / 180-day period)
  3. Return vs. dispersion (one_month / volatility_month)
  4. Relative strength (vs. benchmark; defaults to S&P 500)

Each signal is ranked within the universe, converted to a z-score, and combined
with weights.
Default weights:
  - clenow_90 / clenow_180  25% each
  - 6m return              20%
  - 3m return              15%
  - 12m return             10%
  - risk_adj (1m / vol_m)   5%

Provider fallback:
  Finviz only covers US equities and ETFs. Japanese tickers (.T) are computed
  from yfinance history directly.

Usage:
    uv run scripts/sector_score.py --universe sector-spdr
    uv run scripts/sector_score.py --universe jp-sector
    uv run scripts/sector_score.py --tickers XLK,XLF,XLE,XLV,XLI,XLP,XLY,XLU,XLB,XLRE,XLC
    uv run scripts/sector_score.py --universe global-factor

Preset universes:
  sector-spdr   : SPDR sector 11 (XLK, XLF, XLE, XLV, XLI, XLP, XLY, XLU, XLB, XLRE, XLC)
  theme-ark     : ARK / growth-theme ETFs
  global-factor : iShares factor ETFs (QUAL, MTUM, USMV, VLUE, SIZE, HDV)
  jp-sector     : Japanese sector ETFs (1615, 1617-1629)

Output: same shape as screens/sector-etf/{date}.json, printed to stdout.
Per-ticker rows live under ``data.results``; envelope-top ``warnings``
carries per-row failures only (one entry per failing ticker), while
provider-stage failures are surfaced under ``data.provider_diagnostics``
with ``{provider, stage, error, error_category[, symbol]}``. Exit code 2
is returned only when every ticker failed for the same fatal category
(``credential`` or ``plan_insufficient``); partial / mixed failures keep
exit code 0.
"""

from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from statistics import mean, stdev
from typing import Any

from _common import (
    ErrorCategory,
    aggregate_emit,
    safe_call,
)
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


UNIVERSES: dict[str, list[str]] = {
    "sector-spdr": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC"],
    "theme-ark": ["ARKK", "ARKW", "ARKG", "ARKQ", "ARKF", "SOXX", "SMH", "IBB", "XBI", "KWEB"],
    "global-factor": ["QUAL", "MTUM", "USMV", "VLUE", "SIZE", "HDV", "DGRO", "SPHD"],
    "jp-sector": ["1615.T", "1617.T", "1618.T", "1619.T", "1620.T", "1621.T", "1622.T", "1623.T", "1624.T", "1625.T", "1626.T", "1627.T", "1628.T", "1629.T"],
}


_FINVIZ_PERF_RENAME_MAP: dict[str, str] = {
    "Perf 3Y": "perf_3y",
    "Perf 5Y": "perf_5y",
    "Perf 10Y": "perf_10y",
}


def _parse_finviz_percent(value: Any) -> float | None:
    """Convert a Finviz percent cell (e.g. ``"71.87%"``) to a decimal float.

    Numeric inputs pass through as-is (interpreted as already-decimal),
    and anything unparseable collapses to ``None`` so downstream callers
    can treat the absence of data uniformly as a JSON ``null``.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        has_percent = stripped.endswith("%")
        if has_percent:
            stripped = stripped[:-1].strip()
        try:
            f = float(stripped)
        except ValueError:
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return f / 100.0 if has_percent else f
    return None


def _normalize_finviz_perf_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rename ``"Perf {3,5,10}Y"`` keys to snake_case and percent→decimal.

    Keys outside the rename map pass through unchanged. Unparseable values
    for the renamed fields become ``None`` so the JSON envelope never
    leaks Finviz's raw percent strings (Req 5.1 / 5.2).
    """

    if not isinstance(row, dict):
        return row
    out: dict[str, Any] = {}
    for key, value in row.items():
        new_key = _FINVIZ_PERF_RENAME_MAP.get(key)
        if new_key is None:
            out[key] = value
            continue
        out[new_key] = _parse_finviz_percent(value)
    return out


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _compute_performance_from_history(ticker: str) -> dict[str, Any]:
    """Compute Finviz-equivalent multi-period returns from yfinance history.

    Returns ``{"ok": True, "record": dict | None}`` on success (the record
    is ``None`` when the provider returned empty data, which is "no data"
    rather than an error — per Req 2: keep data vs. error separate). On
    provider exception returns the ``safe_call`` failure record verbatim
    (``{ok: False, error, error_type, error_category}``).
    """

    start = (date.today() - timedelta(days=420)).isoformat()

    def _call() -> list[dict[str, Any]]:
        hist = obb.equity.price.historical(
            symbol=ticker, start_date=start, provider="yfinance"
        )
        df = hist.to_df()
        if df.empty or "close" not in df.columns:
            return []
        df = df.sort_index()
        closes = df["close"].astype(float)
        last_price = float(closes.iloc[-1])

        def ret(days: int) -> float | None:
            if len(closes) < days + 1:
                return None
            past = float(closes.iloc[-(days + 1)])
            if past == 0:
                return None
            return last_price / past - 1.0

        monthly = closes.pct_change().dropna()
        vol_month = float(monthly.tail(21).std()) if len(monthly) >= 5 else None

        return [
            {
                "symbol": ticker,
                "one_day": ret(1),
                "one_week": ret(5),
                "one_month": ret(21),
                "three_month": ret(63),
                "six_month": ret(126),
                "ytd": None,
                "one_year": ret(252),
                "volatility_month": vol_month,
                "price": last_price,
                "source": "yfinance-computed",
            }
        ]

    call = safe_call(_call)
    if not call.get("ok"):
        return call
    records = call.get("records") or []
    return {"ok": True, "record": records[0] if records else None}


def fetch_performance(
    tickers: list[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Fetch multi-period returns, preferring Finviz and falling back to yfinance-computed.

    Returns ``(out, warnings)``. ``warnings`` carries provider-level failures
    as ``{provider, stage, error, error_category}`` entries; ``stage`` ∈
    ``{etf_price_performance_finviz, equity_price_performance_finviz,
    yfinance_history}``. A provider that returned data is never warned
    about, even if some tickers are absent from its response.
    """

    out: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []

    call = safe_call(
        lambda: obb.etf.price_performance(",".join(tickers), provider="finviz")
    )
    if call.get("ok"):
        for row in call.get("records") or []:
            normalized = _normalize_finviz_perf_row(row)
            sym = normalized.get("symbol")
            if sym and normalized.get("one_year") is not None:
                out[sym] = normalized
    else:
        warnings.append(
            {
                "provider": "finviz",
                "stage": "etf_price_performance_finviz",
                "error": call.get("error"),
                "error_category": call.get("error_category"),
            }
        )

    missing = [t for t in tickers if t not in out]
    if missing:
        call = safe_call(
            lambda: obb.equity.price.performance(",".join(missing), provider="finviz")
        )
        if call.get("ok"):
            for row in call.get("records") or []:
                normalized = _normalize_finviz_perf_row(row)
                sym = normalized.get("symbol")
                if sym and normalized.get("one_year") is not None:
                    out[sym] = normalized
        else:
            warnings.append(
                {
                    "provider": "finviz",
                    "stage": "equity_price_performance_finviz",
                    "error": call.get("error"),
                    "error_category": call.get("error_category"),
                }
            )

    still_missing = [t for t in tickers if t not in out]
    for t in still_missing:
        result = _compute_performance_from_history(t)
        if result.get("ok"):
            record = result.get("record")
            if record is not None:
                out[t] = record
        else:
            warnings.append(
                {
                    "provider": "yfinance",
                    "stage": "yfinance_history",
                    "symbol": t,
                    "error": result.get("error"),
                    "error_category": result.get("error_category"),
                }
            )

    return out, warnings


def fetch_clenow(ticker: str, period: int, lookback_days: int) -> dict[str, Any]:
    """Compute Clenow momentum for a single ticker via ``safe_call``.

    Success: ``{"ok": True, "factor", "r_squared", "fit_coef"}`` (values may
    be ``None`` when OpenBB returned an empty DataFrame — still "no data",
    not an error). Failure: ``safe_call``'s ``{"ok": False, "error",
    "error_type", "error_category"}``.
    """

    start = (date.today() - timedelta(days=lookback_days)).isoformat()

    def _call() -> list[dict[str, Any]]:
        hist = obb.equity.price.historical(
            symbol=ticker, start_date=start, provider="yfinance"
        )
        r = obb.technical.clenow(
            data=hist.results, target="close", period=period
        )
        df = r.to_df()
        if df.empty:
            return []
        last = df.iloc[-1].to_dict()
        return [
            {
                "factor": _to_float(last.get("factor")),
                "r_squared": _to_float(last.get("r^2")),
                "fit_coef": _to_float(last.get("fit_coef")),
            }
        ]

    call = safe_call(_call)
    if not call.get("ok"):
        return {
            "ok": False,
            "error": call.get("error"),
            "error_type": call.get("error_type"),
            "error_category": call.get("error_category"),
        }
    records = call.get("records") or []
    if not records:
        return {"ok": True, "factor": None, "r_squared": None, "fit_coef": None}
    row = records[0]
    return {
        "ok": True,
        "factor": row.get("factor"),
        "r_squared": row.get("r_squared"),
        "fit_coef": row.get("fit_coef"),
    }


def zscore(values: list[float | None]) -> list[float | None]:
    """Z-score, ignoring None entries; missing values stay None."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return [None] * len(values)
    m = mean(clean)
    sd = stdev(clean)
    if sd == 0:
        return [0.0 if v is not None else None for v in values]
    return [(v - m) / sd if v is not None else None for v in values]


def rank_desc(values: list[float | None]) -> list[int | None]:
    """Descending rank (larger value -> rank 1). None stays None."""
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    indexed.sort(key=lambda x: x[1], reverse=True)
    ranks: list[int | None] = [None] * len(values)
    for rank, (i, _) in enumerate(indexed, 1):
        ranks[i] = rank
    return ranks


def build_scores(
    tickers: list[str],
    perf: dict[str, dict[str, Any]],
    clenow_90: dict[str, dict[str, Any]],
    clenow_180: dict[str, dict[str, Any]],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    def signal(t: str, key: str) -> float | None:
        row = perf.get(t)
        if not row:
            return None
        return _to_float(row.get(key))

    def risk_adj(t: str) -> float | None:
        row = perf.get(t) or {}
        r = _to_float(row.get("one_month"))
        v = _to_float(row.get("volatility_month"))
        if r is None or v is None or v == 0:
            return None
        return r / v

    three_m = [signal(t, "three_month") for t in tickers]
    six_m = [signal(t, "six_month") for t in tickers]
    one_y = [signal(t, "one_year") for t in tickers]
    c90 = [_to_float(clenow_90.get(t, {}).get("factor")) for t in tickers]
    c180 = [_to_float(clenow_180.get(t, {}).get("factor")) for t in tickers]
    risk = [risk_adj(t) for t in tickers]

    z_3m = zscore(three_m)
    z_6m = zscore(six_m)
    z_12m = zscore(one_y)
    z_c90 = zscore(c90)
    z_c180 = zscore(c180)
    z_risk = zscore(risk)

    def weighted(i: int) -> float | None:
        parts: list[tuple[float | None, float]] = [
            (z_c90[i], weights["clenow_90"]),
            (z_c180[i], weights["clenow_180"]),
            (z_6m[i], weights["return_6m"]),
            (z_3m[i], weights["return_3m"]),
            (z_12m[i], weights["return_12m"]),
            (z_risk[i], weights["risk_adj"]),
        ]
        total_w = 0.0
        total = 0.0
        for val, w in parts:
            if val is not None:
                total += val * w
                total_w += w
        if total_w == 0:
            return None
        return total / total_w

    composite_z = [weighted(i) for i in range(len(tickers))]

    def to_100(z: float | None) -> float | None:
        if z is None:
            return None
        return max(0.0, min(100.0, 50.0 + z * 25.0))

    composite_score = [to_100(z) for z in composite_z]
    final_rank = rank_desc(composite_score)

    records = []
    for i, t in enumerate(tickers):
        records.append(
            {
                "ticker": t,
                "rank": final_rank[i],
                "composite_score_0_100": composite_score[i],
                "composite_z": composite_z[i],
                "signals": {
                    "return_3m": three_m[i],
                    "return_6m": six_m[i],
                    "return_12m": one_y[i],
                    "clenow_90": c90[i],
                    "clenow_180": c180[i],
                    "risk_adj_1m": risk[i],
                },
                "z_scores": {
                    "return_3m": z_3m[i],
                    "return_6m": z_6m[i],
                    "return_12m": z_12m[i],
                    "clenow_90": z_c90[i],
                    "clenow_180": z_c180[i],
                    "risk_adj_1m": z_risk[i],
                },
            }
        )
    records.sort(key=lambda r: (r["rank"] if r["rank"] is not None else 10**9))
    return records


def _classify_ticker_failure(
    ticker: str,
    perf: dict[str, dict[str, Any]],
    clenow_90: dict[str, dict[str, Any]],
    clenow_180: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Return a failure record for a ticker with no usable data, else None.

    A ticker is "all-failed" only when every provider path yielded no data:
    no Finviz/yfinance perf row AND both Clenow calls either raised or
    returned an empty DataFrame. Partial data (one Clenow window succeeded,
    perf succeeded, etc.) keeps the row classified as ok=True so aggregate
    gating does not mistake partial success for a credential rotation.
    """

    has_perf = ticker in perf
    c90r = clenow_90.get(ticker) or {}
    c180r = clenow_180.get(ticker) or {}
    has_clenow_90 = c90r.get("ok") and c90r.get("factor") is not None
    has_clenow_180 = c180r.get("ok") and c180r.get("factor") is not None
    if has_perf or has_clenow_90 or has_clenow_180:
        return None

    fails = [c for c in (c90r, c180r) if c.get("ok") is False]
    if not fails:
        return {
            "error": "no data available",
            "error_type": "NoData",
            "error_category": ErrorCategory.OTHER.value,
        }
    cats = [f.get("error_category") for f in fails]
    # If every underlying failure shares a fatal category (credential or
    # plan_insufficient), keep the ticker row tagged with it so the
    # batch-level gate in `main` can promote to exit 2. Mixed / other
    # cases carry the first available category so the warnings channel
    # keeps the first-seen diagnosis.
    for fatal in (ErrorCategory.CREDENTIAL, ErrorCategory.PLAN_INSUFFICIENT):
        if all(c == fatal.value for c in cats):
            category = fatal.value
            break
    else:
        category = next((c for c in cats if c), ErrorCategory.OTHER.value)
    return {
        "error": fails[0].get("error"),
        "error_type": fails[0].get("error_type"),
        "error_category": category,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default=None, choices=list(UNIVERSES.keys()))
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers. Mutually exclusive with --universe")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark for relative strength (informational)")
    parser.add_argument("--weight-clenow-90", type=float, default=0.25)
    parser.add_argument("--weight-clenow-180", type=float, default=0.25)
    parser.add_argument("--weight-return-6m", type=float, default=0.20)
    parser.add_argument("--weight-return-3m", type=float, default=0.15)
    parser.add_argument("--weight-return-12m", type=float, default=0.10)
    parser.add_argument("--weight-risk-adj", type=float, default=0.05)
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif args.universe:
        tickers = UNIVERSES[args.universe]
    else:
        tickers = UNIVERSES["sector-spdr"]

    weights = {
        "clenow_90": args.weight_clenow_90,
        "clenow_180": args.weight_clenow_180,
        "return_6m": args.weight_return_6m,
        "return_3m": args.weight_return_3m,
        "return_12m": args.weight_return_12m,
        "risk_adj": args.weight_risk_adj,
    }

    perf, perf_warnings = fetch_performance(tickers)
    clenow_90 = {t: fetch_clenow(t, period=90, lookback_days=240) for t in tickers}
    clenow_180 = {t: fetch_clenow(t, period=180, lookback_days=420) for t in tickers}

    benchmark_perf_map, benchmark_warnings = fetch_performance([args.benchmark])
    benchmark_perf = benchmark_perf_map.get(args.benchmark, {})

    records = build_scores(tickers, perf, clenow_90, clenow_180, weights)

    for row in records:
        ticker = row["ticker"]
        row["symbol"] = ticker
        failure = _classify_ticker_failure(ticker, perf, clenow_90, clenow_180)
        if failure is None:
            row["ok"] = True
        else:
            row["ok"] = False
            row.update(failure)

    # `missing_tickers` lists tickers whose every provider path failed — i.e.
    # the row was classified `ok: False` by `_classify_ticker_failure`. A row
    # with partial data (e.g. perf succeeded but z-score could not be
    # computed because the universe was too small) stays `ok: True` with a
    # null `composite_score_0_100` and must NOT be flagged here.
    missing = [r["ticker"] for r in records if not r.get("ok")]

    provider_warnings: list[dict[str, Any]] = list(perf_warnings + benchmark_warnings)
    for t, c in clenow_90.items():
        if c.get("ok") is False:
            provider_warnings.append(
                {
                    "provider": "yfinance",
                    "stage": "clenow_90",
                    "symbol": t,
                    "error": c.get("error"),
                    "error_category": c.get("error_category"),
                }
            )
    for t, c in clenow_180.items():
        if c.get("ok") is False:
            provider_warnings.append(
                {
                    "provider": "yfinance",
                    "stage": "clenow_180",
                    "symbol": t,
                    "error": c.get("error"),
                    "error_category": c.get("error_category"),
                }
            )

    query_meta: dict[str, Any] = {
        "universe": args.universe or "custom",
        "tickers": tickers,
        "benchmark": {"ticker": args.benchmark, "performance": benchmark_perf},
        "weights": weights,
        "missing_tickers": missing,
        "notes": "Composite z-score from Finviz price_performance + Clenow momentum. Scored 0-100 (mean=50, ±2σ→0/100).",
    }
    if provider_warnings:
        query_meta["provider_diagnostics"] = provider_warnings

    return aggregate_emit(records, tool="sector_score", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
