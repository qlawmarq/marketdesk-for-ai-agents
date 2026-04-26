"""Compute momentum / technical indicators.

For any ticker or ETF, computes the following from historical prices:
  --indicator clenow       Clenow exponential-regression momentum (annualized * R^2)
  --indicator rsi          RSI(N)
  --indicator macd         MACD (fast/slow/signal)
  --indicator cones        Volatility cones (quantile distribution)
  --indicator adx          ADX (trend strength)

Usage:
    uv run scripts/momentum.py AAPL --indicator clenow --period 90
    uv run scripts/momentum.py XLK XLF --indicator clenow --period 90 --start 2025-01-01
    uv run scripts/momentum.py AAPL --indicator rsi --length 14 --start 2025-10-01

Return value: for each ticker, the final value / tail series of the indicator as records.
Clenow's `factor` is a unified momentum score usable for cross-sector / cross-ticker ranking.
"""

from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from typing import Any, Callable

from _common import (
    ErrorCategory,
    aggregate_emit,
    safe_call,
)
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


def _to_float(value: Any) -> float | None:
    """Coerce numeric-like values to float; map NaN / None / parse-errors to None.

    Numpy / pandas scalars that arrive from OpenBB DataFrames are not always
    `int`/`float` instances, so a plain `isinstance` check would leak strings
    into emit. Going through `float()` normalizes the type, and NaN is treated
    as missing data (per Req 1.2: "NaN / None / parse failure → null, not error").
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def _clean_numeric_cells(row: dict[str, Any]) -> dict[str, Any]:
    """Replace NaN floats in a record with None so JSON output stays strictly valid."""
    cleaned: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, float) and math.isnan(value):
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned


def _row_date(row: dict[str, Any]) -> str | None:
    for key in ("date", "Date", "index"):
        if key in row and row[key] is not None:
            return str(row[key])
    return None


def _last_with(records: list[dict[str, Any]], key_substring: str) -> tuple[dict[str, Any], dict[str, float]] | None:
    """Find the latest record carrying any finite value for a column whose name contains `key_substring`."""
    for row in reversed(records):
        finite_cells: dict[str, float] = {}
        for key, value in row.items():
            if not isinstance(key, str) or key_substring not in key.lower():
                continue
            f = _to_float(value)
            if f is not None:
                finite_cells[key] = f
        if finite_cells:
            return row, finite_cells
    return None


def _indicator_call(indicator: str, symbol: str, start: str, provider: str, args: argparse.Namespace) -> Callable[[], Any]:
    """Build a zero-arg callable that fetches history and computes the indicator.

    Both OpenBB calls happen inside a single `safe_call` — `safe_call`'s
    try/except classifies any provider failure (credential / transient /
    validation / other) into `error_category`, replacing the per-indicator
    hand-written try/except blocks.
    """

    def _call() -> Any:
        history = obb.equity.price.historical(
            symbol=symbol, start_date=start, provider=provider
        )
        if indicator == "clenow":
            return obb.technical.clenow(
                data=history.results, target="close", period=args.period
            )
        if indicator == "rsi":
            return obb.technical.rsi(
                data=history.results, target="close", length=args.length
            )
        if indicator == "macd":
            return obb.technical.macd(
                data=history.results,
                target="close",
                fast=args.fast,
                slow=args.slow,
                signal=args.signal,
            )
        if indicator == "cones":
            return obb.technical.cones(
                data=history.results, lower_q=args.lower_q, upper_q=args.upper_q
            )
        if indicator == "adx":
            return obb.technical.adx(data=history.results, length=args.length)
        raise ValueError(f"unknown indicator: {indicator}")

    return _call


_EMPTY_RESULT_FAILURE = {
    "ok": False,
    "error": "empty result",
    "error_type": "EmptyResult",
    "error_category": ErrorCategory.OTHER.value,
}


def _postprocess_clenow(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    if not records:
        return {**_EMPTY_RESULT_FAILURE, "period": args.period}
    last = records[-1]
    return {
        "ok": True,
        "period": args.period,
        "momentum_factor": _to_float(last.get("factor")),
        "r_squared": _to_float(last.get("r^2")),
        "fit_coef": _to_float(last.get("fit_coef")),
    }


def _postprocess_rsi(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    if not records:
        return {**_EMPTY_RESULT_FAILURE, "length": args.length}
    found = _last_with(records, "rsi")
    if found is None:
        return {
            "ok": False,
            "length": args.length,
            "error": "rsi not yet computable (need more data)",
            "error_type": "EmptyResult",
            "error_category": ErrorCategory.OTHER.value,
        }
    row, cells = found
    rsi_val = next(iter(cells.values()))
    return {
        "ok": True,
        "length": args.length,
        "rsi": rsi_val,
        "as_of": _row_date(row),
    }


def _postprocess_macd(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    if not records:
        return dict(_EMPTY_RESULT_FAILURE)
    found = _last_with(records, "macd")
    if found is None:
        return {
            "ok": False,
            "error": "macd not yet computable",
            "error_type": "EmptyResult",
            "error_category": ErrorCategory.OTHER.value,
        }
    row, cells = found
    return {"ok": True, "as_of": _row_date(row), **cells}


def _postprocess_cones(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": True,
        "lower_q": args.lower_q,
        "upper_q": args.upper_q,
        "records": [_clean_numeric_cells(r) for r in records],
    }


def _postprocess_adx(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    if not records:
        return {**_EMPTY_RESULT_FAILURE, "length": args.length}
    found = _last_with(records, "adx")
    if found is None:
        return {
            "ok": False,
            "length": args.length,
            "error": "adx not yet computable",
            "error_type": "EmptyResult",
            "error_category": ErrorCategory.OTHER.value,
        }
    row, _adx_cells = found
    numeric_fields: dict[str, float] = {}
    for key, value in row.items():
        if key in ("date", "Date", "index"):
            continue
        f = _to_float(value)
        if f is not None:
            numeric_fields[key] = f
    return {
        "ok": True,
        "length": args.length,
        "as_of": _row_date(row),
        **numeric_fields,
    }


_POSTPROCESSORS: dict[str, Callable[[list[dict[str, Any]], argparse.Namespace], dict[str, Any]]] = {
    "clenow": _postprocess_clenow,
    "rsi": _postprocess_rsi,
    "macd": _postprocess_macd,
    "cones": _postprocess_cones,
    "adx": _postprocess_adx,
}


def _compute_for_symbol(symbol: str, start: str, args: argparse.Namespace) -> dict[str, Any]:
    call = safe_call(_indicator_call(args.indicator, symbol, start, args.provider, args))
    base = {"symbol": symbol, "indicator": args.indicator}
    if not call.get("ok"):
        return {
            **base,
            "ok": False,
            "error": call.get("error"),
            "error_type": call.get("error_type"),
            "error_category": call.get("error_category"),
        }
    payload = _POSTPROCESSORS[args.indicator](call.get("records") or [], args)
    return {**base, **payload}


def _clenow_score(row: dict[str, Any]) -> float:
    """Sort key for Clenow ranking; missing factor sinks the row to the bottom."""
    value = row.get("momentum_factor")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else -1e9


def _apply_clenow_rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ok_rows = [r for r in rows if r.get("ok")]
    failed_rows = [r for r in rows if not r.get("ok")]
    ok_rows.sort(key=_clenow_score, reverse=True)
    for i, row in enumerate(ok_rows, 1):
        row["rank"] = i
    return ok_rows + failed_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--indicator", required=True, choices=list(_POSTPROCESSORS.keys()))
    parser.add_argument("--provider", default="yfinance")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (auto-selected if omitted)")
    parser.add_argument("--period", type=int, default=90, help="Clenow regression window in days")
    parser.add_argument("--length", type=int, default=14, help="rsi / adx period")
    parser.add_argument("--fast", type=int, default=12)
    parser.add_argument("--slow", type=int, default=26)
    parser.add_argument("--signal", type=int, default=9)
    parser.add_argument("--lower-q", type=float, default=0.25, help="cones lower quantile")
    parser.add_argument("--upper-q", type=float, default=0.75, help="cones upper quantile")
    args = parser.parse_args()

    if args.start is None:
        lookback_days = {
            "clenow": max(args.period * 2, 180),
            "rsi": args.length * 5 + 30,
            "macd": args.slow * 5,
            "cones": 365,
            "adx": args.length * 5 + 30,
        }[args.indicator]
        start = (date.today() - timedelta(days=lookback_days)).isoformat()
    else:
        start = args.start

    rows = [_compute_for_symbol(symbol, start, args) for symbol in args.symbols]

    if args.indicator == "clenow":
        rows = _apply_clenow_rank(rows)

    return aggregate_emit(
        rows,
        tool="momentum",
        query_meta={
            "indicator": args.indicator,
            "provider": args.provider,
            "start": start,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
