"""Print historical prices as JSON on stdout.

Usage:
    uv run scripts/historical.py AAPL --start 2025-01-01 --end 2026-04-21
    uv run scripts/historical.py 7203.T --start 2025-01-01 --interval 1d

Envelope: stdout is a single JSON document produced by `_common.wrap()` with
`collected_at`, `source="marketdesk-for-ai-agents"`, `tool="historical"`, and a
`data.results` list containing a single per-symbol entry. Rows inside
`records` are sorted by their date field ascending so downstream consumers see
a provider-independent canonical order.
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


_DATE_KEYS: tuple[str, ...] = ("date", "Date", "timestamp", "datetime", "index")


def _sort_by_date_ascending(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return records
    date_key = next((k for k in _DATE_KEYS if k in records[0]), None)
    if date_key is None:
        return records
    return sorted(records, key=lambda r: str(r.get(date_key, "")))


def fetch_history(symbol: str, provider: str, start: str, end: str | None, interval: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider, "start_date": start, "interval": interval}
    if end:
        kwargs["end_date"] = end
    call_result = safe_call(obb.equity.price.historical, **kwargs)
    entry: dict[str, Any] = {"symbol": symbol, "provider": provider, "interval": interval, **call_result}
    if call_result.get("ok"):
        records = _sort_by_date_ascending(call_result["records"])
        entry["rows"] = len(records)
        entry["records"] = records
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--provider", default="yfinance", choices=["yfinance", "fmp"])
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (defaults to today)")
    parser.add_argument("--interval", default="1d", help="1d, 1w, 1mo, etc.")
    args = parser.parse_args()

    result = fetch_history(args.symbol, args.provider, args.start, args.end, args.interval)
    return aggregate_emit(
        [result],
        tool="historical",
        query_meta={"provider": args.provider},
    )


if __name__ == "__main__":
    raise SystemExit(main())
