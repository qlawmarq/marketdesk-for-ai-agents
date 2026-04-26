"""Fetch company or world news articles.

Surfaces sentiment and catalyst signals so the analyst can incorporate
news flow into recommendations with cited URLs.

Usage:
    uv run scripts/news.py AAPL MSFT --days 7 --limit 5
    uv run scripts/news.py --scope world --provider fmp --days 3 --limit 20
    uv run scripts/news.py AAPL --scope company --provider yfinance

Scopes:
  - company (default): per-symbol news; multi-symbol input fetched sequentially
  - world: macro / world-news feed; positional symbols are accepted but
    ignored with a non-fatal validation warning

Providers (key-free first):
  - company default: yfinance (no key); also benzinga, fmp, intrinio,
    tiingo, tmx
  - world default: fmp (FMP free tier 250/day); also benzinga, biztoc,
    intrinio, tiingo

The `--days N` window is translated to the upstream `start_date`
(today - N). Per-item field shapes remain provider-native; only the
envelope is uniform across providers.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Any

from _common import aggregate_emit, safe_call, single_emit
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# Live provider literal probe 2026-04-25:
#   obb.news.company -> ['benzinga', 'fmp', 'intrinio', 'tiingo', 'tmx', 'yfinance']
#   obb.news.world   -> ['benzinga', 'biztoc', 'fmp', 'intrinio', 'tiingo']
SCOPE_PROVIDERS: dict[str, frozenset[str]] = {
    "company": frozenset({"benzinga", "fmp", "intrinio", "tiingo", "tmx", "yfinance"}),
    "world": frozenset({"benzinga", "biztoc", "fmp", "intrinio", "tiingo"}),
}

PROVIDER_CHOICES = sorted(SCOPE_PROVIDERS["company"] | SCOPE_PROVIDERS["world"])

DEFAULT_PROVIDERS = {"company": "yfinance", "world": "fmp"}

SCOPE_CHOICES = ["company", "world"]


def _days_to_start_date(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _company_fetch(
    symbol: str, provider: str, start_date: str, limit: int
) -> dict[str, Any]:
    call_result = safe_call(
        obb.news.company,
        symbol=symbol,
        provider=provider,
        start_date=start_date,
        limit=limit,
    )
    return {"symbol": symbol, "provider": provider, **call_result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="Tickers (required for --scope company; ignored with warning for --scope world)",
    )
    parser.add_argument("--scope", default="company", choices=SCOPE_CHOICES)
    parser.add_argument(
        "--provider",
        default=None,
        choices=PROVIDER_CHOICES,
        help="Defaults: company → yfinance, world → fmp",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window translated to start_date = today - N (default: 7)",
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.scope]
    start_date = _days_to_start_date(args.days)

    query_meta: dict[str, Any] = {
        "scope": args.scope,
        "provider": provider,
        "days": args.days,
        "limit": args.limit,
    }

    if args.scope == "company":
        if not args.symbols:
            parser.error("--scope company requires at least one positional ticker")
        results = [
            _company_fetch(sym, provider, start_date, args.limit)
            for sym in args.symbols
        ]
        return aggregate_emit(results, tool="news", query_meta=query_meta)

    # world scope: single call, no per-symbol fanout. Symbols are accepted
    # but flagged via a non-fatal warning so batch-invoking callers do not
    # have to special-case the scope on the argv side.
    extra_warnings: list[dict[str, Any]] = []
    if args.symbols:
        extra_warnings.append(
            {
                "symbol": None,
                "error": (
                    "symbol positional argv ignored by world scope; "
                    f"received {args.symbols!r}"
                ),
                "error_category": "validation",
            }
        )
    call_result = safe_call(
        obb.news.world,
        provider=provider,
        start_date=start_date,
        limit=args.limit,
    )
    return single_emit(
        call_result,
        tool="news",
        query_meta=query_meta,
        extra_warnings=extra_warnings,
    )


if __name__ == "__main__":
    raise SystemExit(main())
