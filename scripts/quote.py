"""Print the latest quote (price, volume, key metrics) for tickers as JSON on stdout.

Usage:
    uv run scripts/quote.py AAPL MSFT 7203.T
    uv run scripts/quote.py AAPL --provider yfinance

Envelope: stdout is a single JSON document produced by `_common.wrap()` with
`collected_at`, `source="marketdesk-for-ai-agents"`, `tool="quote"`, and a `data.results`
list of per-symbol entries. Entries whose provider payload signals a delisted
or missing symbol (e.g., `last_price` is 0 or null) are flagged with
`status="missing"` inside `records[*]` so downstream agents do not confuse an
empty-price tape with a genuine zero price.
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


_MISSING_PRICE_VALUES: tuple[Any, ...] = (None, 0, 0.0)


def _flag_missing(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged: list[dict[str, Any]] = []
    for record in records:
        if "last_price" in record and record["last_price"] in _MISSING_PRICE_VALUES:
            flagged.append({**record, "status": "missing"})
        else:
            flagged.append(record)
    return flagged


def fetch_quote(symbol: str, provider: str) -> dict[str, Any]:
    call_result = safe_call(obb.equity.price.quote, symbol=symbol, provider=provider)
    entry: dict[str, Any] = {"symbol": symbol, "provider": provider, **call_result}
    if call_result.get("ok"):
        entry["records"] = _flag_missing(call_result["records"])
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="+", help="One or more tickers. Use the .T suffix for Japanese equities")
    parser.add_argument("--provider", default="yfinance", choices=["yfinance", "fmp"])
    args = parser.parse_args()

    results = [fetch_quote(symbol, args.provider) for symbol in args.symbols]
    return aggregate_emit(
        results,
        tool="quote",
        query_meta={"provider": args.provider},
    )


if __name__ == "__main__":
    raise SystemExit(main())
