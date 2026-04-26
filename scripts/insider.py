"""Fetch Form 4 insider-trading records per ticker.

Surfaces officer/director/10%-owner transactions so the analyst can
read capital-structure and disclosure signals without a Python REPL.

Usage:
    uv run scripts/insider.py AAPL MSFT
    uv run scripts/insider.py AAPL --days 30 --provider sec
    uv run scripts/insider.py AAPL --provider fmp --limit 100

Providers (key-free first):
  - sec (default): no key beyond SEC_USER_AGENT
  - fmp: requires FMP_API_KEY
  - intrinio: requires intrinio key
  - tmx: Canadian listings

Day-window semantics: the upstream endpoint accepts a row `limit`, not a
date window, so `--days N` is applied client-side on each record's
`transaction_date` (or `filing_date` when transaction_date is missing).
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


PROVIDER_CHOICES = ["sec", "fmp", "intrinio", "tmx"]
DEFAULT_PROVIDER = "sec"


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def _filter_by_days(records: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=days)
    kept: list[dict[str, Any]] = []
    for record in records:
        record_date = _coerce_date(record.get("transaction_date")) or _coerce_date(
            record.get("filing_date")
        )
        if record_date is None or record_date >= cutoff:
            kept.append(record)
    return kept


def fetch(symbol: str, provider: str, days: int, limit: int | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    if limit is not None:
        kwargs["limit"] = limit
    call_result = safe_call(obb.equity.ownership.insider_trading, **kwargs)
    entry: dict[str, Any] = {"symbol": symbol, "provider": provider, **call_result}
    if call_result.get("ok"):
        entry["records"] = _filter_by_days(call_result["records"], days)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbols", nargs="+", help="One or more tickers")
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=PROVIDER_CHOICES,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Client-side window applied to transaction_date (default: 90)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Upstream row limit passed through to the provider",
    )
    args = parser.parse_args()

    results = [
        fetch(symbol, args.provider, args.days, args.limit)
        for symbol in args.symbols
    ]
    query_meta: dict[str, Any] = {"provider": args.provider, "days": args.days}
    if args.limit is not None:
        query_meta["limit"] = args.limit
    return aggregate_emit(results, tool="insider", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
