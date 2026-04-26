"""Fetch commodity spot prices and EIA structured energy reports.

Surfaces the energy / commodity macro inputs the analyst cites for
inflation- and energy-quadrant calls. ``--type price`` returns FRED-backed
commodity spot prices (WTI, Brent, Henry Hub, etc.); ``--type weekly_report``
returns the EIA Weekly Petroleum Status Report; ``--type steo`` returns the
EIA Short-Term Energy Outlook (18-month projections).

Usage:
    uv run scripts/commodity.py --symbol wti --start 2024-01-01
    uv run scripts/commodity.py --symbol brent --type price
    uv run scripts/commodity.py --type weekly_report --start 2025-01-01
    uv run scripts/commodity.py --type steo

Types and providers (key-free / free-tier):
  - price (default): provider fred (requires FRED_API_KEY)
  - weekly_report: provider eia (no key required; OpenBB downloads the
    public Weekly Petroleum Status Excel file directly, so EIA_API_KEY
    is ignored on this sub-mode even when set)
  - steo: provider eia (requires EIA_API_KEY; hits the EIA v2 API)

Symbol scope: ``--symbol`` choices are restricted to FRED commodity literals.

When ``--symbol`` is combined with a non-price ``--type`` it is ignored with
a non-fatal validation warning (mirrors the calendars / macro_survey
dispatch precedent).
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import safe_call, single_emit
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# Live provider literal probe 2026-04-25:
#   obb.commodity.price.spot              -> ['fred']
#   obb.commodity.petroleum_status_report -> ['eia']
#   obb.commodity.short_term_energy_outlook -> ['eia']
TYPE_TO_CALLABLE = {
    "price": lambda **kw: obb.commodity.price.spot(**kw),
    "weekly_report": lambda **kw: obb.commodity.petroleum_status_report(**kw),
    "steo": lambda **kw: obb.commodity.short_term_energy_outlook(**kw),
}

TYPE_PROVIDERS: dict[str, frozenset[str]] = {
    "price": frozenset({"fred"}),
    "weekly_report": frozenset({"eia"}),
    "steo": frozenset({"eia"}),
}

DEFAULT_PROVIDERS = {"price": "fred", "weekly_report": "eia", "steo": "eia"}

TYPE_CHOICES = list(TYPE_TO_CALLABLE.keys())
PROVIDER_CHOICES = sorted(set().union(*TYPE_PROVIDERS.values()))

# obb.commodity.price.spot literal probe 2026-04-25:
#   commodity: 'wti', 'brent', 'natural_gas', 'jet_fuel', 'propane',
#              'heating_oil', 'diesel_gulf_coast', 'diesel_ny_harbor',
#              'diesel_la', 'gasoline_ny_harbor', 'gasoline_gulf_coast',
#              'rbob', 'all'
SYMBOL_CHOICES = [
    "wti",
    "brent",
    "natural_gas",
    "jet_fuel",
    "propane",
    "heating_oil",
    "diesel_gulf_coast",
    "diesel_ny_harbor",
    "diesel_la",
    "gasoline_ny_harbor",
    "gasoline_gulf_coast",
    "rbob",
    "all",
]
DEFAULT_SYMBOL = "wti"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--type",
        default="price",
        choices=TYPE_CHOICES,
    )
    parser.add_argument(
        "--symbol",
        default=None,
        choices=SYMBOL_CHOICES,
        help=f"Commodity literal; defaults to {DEFAULT_SYMBOL!r} for --type price, ignored otherwise",
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=PROVIDER_CHOICES,
        help="Defaults: price → fred, weekly_report → eia, steo → eia",
    )
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.type]

    extra_warnings: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"provider": provider}

    if args.type == "price":
        symbol = args.symbol or DEFAULT_SYMBOL
        kwargs["commodity"] = symbol
        query_meta_symbol: str | None = symbol
    else:
        query_meta_symbol = None
        if args.symbol:
            extra_warnings.append(
                {
                    "symbol": None,
                    "error": (
                        f"--symbol {args.symbol!r} ignored by --type {args.type!r}; "
                        "structured EIA reports do not accept a commodity literal"
                    ),
                    "error_category": "validation",
                }
            )

    if args.start:
        kwargs["start_date"] = args.start
    if args.end:
        kwargs["end_date"] = args.end

    fn = TYPE_TO_CALLABLE[args.type]
    call_result = safe_call(fn, **kwargs)

    query_meta: dict[str, Any] = {"type": args.type, "provider": provider}
    if query_meta_symbol is not None:
        query_meta["symbol"] = query_meta_symbol
    if args.start:
        query_meta["start"] = args.start
    if args.end:
        query_meta["end"] = args.end

    return single_emit(
        call_result,
        tool="commodity",
        query_meta=query_meta,
        extra_warnings=extra_warnings,
    )


if __name__ == "__main__":
    raise SystemExit(main())
