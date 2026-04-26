"""Fetch FINRA short interest or SEC fails-to-deliver per ticker.

Surfaces contrarian / squeeze signals via the FINRA short-interest feed
(includes days_to_cover) and the SEC fails-to-deliver dataset.

Usage:
    uv run scripts/shorts.py AAPL MSFT
    uv run scripts/shorts.py AAPL --type short_interest --provider finra
    uv run scripts/shorts.py AAPL --type fails_to_deliver --provider sec

Types and providers (key-free / free-tier):
  - short_interest (default): provider finra (no key); returns short
    interest, days_to_cover, and reporting period.
  - fails_to_deliver: provider sec (requires SEC_USER_AGENT); returns
    daily settlement-failure rows.

The `short_volume` sub-mode (stockgrid upstream) is intentionally NOT
exposed: the endpoint has been broken upstream since 2026-04-25
(returns an empty body that decodes as JSONDecodeError). The omission
is documented in README so callers know the gap is upstream, not a
wrapper bug.
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# Live provider literal probe 2026-04-25:
#   obb.equity.shorts.short_interest    -> ['finra']
#   obb.equity.shorts.fails_to_deliver  -> ['sec']
TYPE_TO_CALLABLE = {
    "short_interest": lambda **kw: obb.equity.shorts.short_interest(**kw),
    "fails_to_deliver": lambda **kw: obb.equity.shorts.fails_to_deliver(**kw),
}

TYPE_PROVIDERS: dict[str, frozenset[str]] = {
    "short_interest": frozenset({"finra"}),
    "fails_to_deliver": frozenset({"sec"}),
}

DEFAULT_PROVIDERS = {"short_interest": "finra", "fails_to_deliver": "sec"}

TYPE_CHOICES = list(TYPE_TO_CALLABLE.keys())
PROVIDER_CHOICES = sorted(
    TYPE_PROVIDERS["short_interest"] | TYPE_PROVIDERS["fails_to_deliver"]
)


def fetch(symbol: str, type_: str, provider: str) -> dict[str, Any]:
    fn = TYPE_TO_CALLABLE[type_]
    call_result = safe_call(fn, symbol=symbol, provider=provider)
    return {"symbol": symbol, "type": type_, "provider": provider, **call_result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbols", nargs="+", help="One or more tickers")
    parser.add_argument(
        "--type",
        default="short_interest",
        choices=TYPE_CHOICES,
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=PROVIDER_CHOICES,
        help="Defaults: short_interest → finra, fails_to_deliver → sec",
    )
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.type]
    results = [fetch(sym, args.type, provider) for sym in args.symbols]

    return aggregate_emit(
        results,
        tool="shorts",
        query_meta={"type": args.type, "provider": provider},
    )


if __name__ == "__main__":
    raise SystemExit(main())
