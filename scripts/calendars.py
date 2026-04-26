"""Fetch earnings, dividend, and economic calendars.

Supported types:
  --type earnings   Earnings announcement calendar
  --type dividend   Ex-dividend / payment calendar
  --type economic   Economic indicator calendar (payrolls, CPI, FOMC, etc.)

Usage:
    uv run scripts/calendars.py --type earnings --start 2026-04-23 --end 2026-05-07
    uv run scripts/calendars.py --type economic --start 2026-04-23 --end 2026-04-30
    uv run scripts/calendars.py --type dividend --start 2026-04-23 --end 2026-05-07

Providers:
  - earnings / dividend / economic: nasdaq by default (no key). fmp / tmx /
    tradingeconomics / seeking_alpha remain selectable via ``--provider``.
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import safe_call, single_emit
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


TYPE_TO_CALLABLE = {
    "earnings": lambda **kw: obb.equity.calendar.earnings(**kw),
    "dividend": lambda **kw: obb.equity.calendar.dividend(**kw),
    "economic": lambda **kw: obb.economy.calendar(**kw),
}


DEFAULT_PROVIDERS = {
    "earnings": "nasdaq",
    "dividend": "nasdaq",
    "economic": "nasdaq",
}


PROVIDER_CHOICES = ["fmp", "fred", "nasdaq", "seeking_alpha", "tmx", "tradingeconomics"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", required=True, choices=list(TYPE_TO_CALLABLE.keys()))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument(
        "--provider",
        default=None,
        choices=PROVIDER_CHOICES,
        help="Provider (defaults per type if omitted)",
    )
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.type]
    kwargs: dict[str, Any] = {"start_date": args.start, "provider": provider}
    if args.end:
        kwargs["end_date"] = args.end

    fn = TYPE_TO_CALLABLE[args.type]
    result = safe_call(fn, **kwargs)
    return single_emit(
        result,
        tool="calendars",
        query_meta={
            "type": args.type,
            "provider": provider,
            "start": args.start,
            "end": args.end,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
