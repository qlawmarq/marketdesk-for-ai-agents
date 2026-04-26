"""Fetch ETF info, holdings, or sector breakdown.

Supported types:
  --type info            ETF basic info (expense ratio, AUM, inception)
  --type holdings        ETF holdings (full list) — requires FMP_API_KEY
  --type sectors         ETF sector allocation — requires FMP_API_KEY

Usage:
    uv run scripts/etf.py SPY QQQ --type info
    uv run scripts/etf.py XLK --type holdings
    uv run scripts/etf.py XLK XLF --type sectors

Providers:
  - info: yfinance by default (fmp / tmx also work)
  - holdings: fmp only. Attempted free-tier routes
    (``obb.etf.nport_disclosure`` via sec, tmx) were unviable — the sec
    route crashes on upstream data-model validation for most US ETFs, and
    tmx returns empty results for non-Canadian tickers.
  - sectors: fmp only (no free-tier alternative for US sectors).
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


TYPE_TO_CALLABLE = {
    "info": lambda **kw: obb.etf.info(**kw),
    "holdings": lambda **kw: obb.etf.holdings(**kw),
    "sectors": lambda **kw: obb.etf.sectors(**kw),
}

DEFAULT_PROVIDERS = {
    "info": "yfinance",
    "holdings": "fmp",
    "sectors": "fmp",
}


def fetch(symbol: str, etf_type: str, provider: str) -> dict[str, Any]:
    fn = TYPE_TO_CALLABLE[etf_type]
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    result = safe_call(fn, **kwargs)
    return {"symbol": symbol, "type": etf_type, "provider": provider, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--type", required=True, choices=list(TYPE_TO_CALLABLE.keys()))
    parser.add_argument("--provider", default=None)
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.type]
    results = [fetch(s, args.type, provider) for s in args.symbols]
    return aggregate_emit(
        results,
        tool="etf",
        query_meta={"provider": provider, "type": args.type},
    )


if __name__ == "__main__":
    raise SystemExit(main())
