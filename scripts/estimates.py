"""Fetch analyst estimates and price targets.

Consensus shifts and analyst price-target revisions are among the most
effective signals for catalyst discovery and sector selection in mid-term
strategy.

Supported types:
  --type consensus       Analyst consensus (price target / recommendation)
  --type price_target    Per-analyst price target revision history

Usage:
    uv run scripts/estimates.py AAPL MSFT --type consensus
    uv run scripts/estimates.py AAPL --type price_target --provider finviz

Providers (free-first):
  - consensus: yfinance by default (free tier works)
  - price_target: finviz by default (free)
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


TYPE_TO_CALLABLE = {
    "consensus": lambda **kw: obb.equity.estimates.consensus(**kw),
    "price_target": lambda **kw: obb.equity.estimates.price_target(**kw),
}

DEFAULT_PROVIDERS = {
    "consensus": "yfinance",
    "price_target": "finviz",
}


def fetch(symbol: str, est_type: str, provider: str, extras: dict[str, Any]) -> dict[str, Any]:
    fn = TYPE_TO_CALLABLE[est_type]
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    kwargs.update(extras)
    result = safe_call(fn, **kwargs)
    return {"symbol": symbol, "type": est_type, "provider": provider, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--type", required=True, choices=list(TYPE_TO_CALLABLE.keys()))
    parser.add_argument("--provider", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.type]
    extras: dict[str, Any] = {}
    if args.limit is not None:
        extras["limit"] = args.limit

    results = [fetch(s, args.type, provider, extras) for s in args.symbols]
    return aggregate_emit(
        results,
        tool="estimates",
        query_meta={"type": args.type, "provider": provider},
    )


if __name__ == "__main__":
    raise SystemExit(main())
