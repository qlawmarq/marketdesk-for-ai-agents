"""Fetch 13F institutional holders per ticker.

Surfaces aggregate institutional ownership statistics so the analyst can
read concentration / drift signals from the most-recent 13F vintage.

Usage:
    uv run scripts/institutional.py AAPL MSFT
    uv run scripts/institutional.py AAPL --provider fmp

Providers:
  - fmp (default, only choice): requires FMP_API_KEY (free tier 250/day
    is sufficient — endpoint returns one row per ticker per quarter).

The upstream endpoint does not accept a row limit; the wrapper passes
through whatever rows the provider returns.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


PROVIDER_CHOICES = ["fmp"]
DEFAULT_PROVIDER = "fmp"

_FILING_WINDOW_DAYS = 45  # SEC 17 CFR §240.13f-1


def _is_partial_filing_window(record_date: Any, today: date) -> bool:
    # `datetime` is a subclass of `date`, so branch on it first to strip
    # time / tz; `pandas.Timestamp` is a `datetime` subclass and is
    # absorbed by the same branch.
    if isinstance(record_date, datetime):
        record_date = record_date.date()
    elif isinstance(record_date, str):
        try:
            record_date = date.fromisoformat(record_date)
        except ValueError:
            return False
    elif not isinstance(record_date, date):
        return False
    return record_date + timedelta(days=_FILING_WINDOW_DAYS) > today


def fetch(
    symbol: str,
    provider: str,
    year: int | None = None,
    quarter: int | None = None,
) -> dict[str, Any]:
    opt = {k: v for k, v in (("year", year), ("quarter", quarter)) if v is not None}
    call_result = safe_call(
        obb.equity.ownership.institutional, symbol=symbol, provider=provider, **opt
    )
    if call_result.get("ok"):
        today = date.today()
        for rec in call_result["records"]:
            rec["partial_filing_window"] = _is_partial_filing_window(
                rec.get("date"), today
            )
    return {"symbol": symbol, "provider": provider, **call_result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Upstream returns in-flight quarters (quarter end + 45 days) as "
            "partial aggregates. With --year/--quarter omitted the current "
            "calendar quarter is selected; records still inside the filing "
            "window carry partial_filing_window: true. For a stable snapshot "
            "pass --year/--quarter pointing to a quarter older than 1 year. "
            "--quarter accepts 1-4 (validated upstream)."
        ),
    )
    parser.add_argument("symbols", nargs="+", help="One or more tickers")
    parser.add_argument(
        "--provider", default=DEFAULT_PROVIDER, choices=PROVIDER_CHOICES
    )
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--quarter", type=int, default=None)
    args = parser.parse_args()

    results = [fetch(s, args.provider, year=args.year, quarter=args.quarter) for s in args.symbols]
    query_meta: dict[str, Any] = {"provider": args.provider}
    for k, v in (("year", args.year), ("quarter", args.quarter)):
        if v is not None:
            query_meta[k] = v
    return aggregate_emit(results, tool="institutional", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
