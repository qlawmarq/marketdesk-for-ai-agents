"""Fetch FRED-backed structured surveys and public economic indicators.

OpenBB's `obb.economy.survey.*`, by contrast, ships **structured wrappers**
that bundle multiple series (SLOOS, regional Fed surveys, etc.). Using them
here meaningfully sharpens the analyst's macro four-quadrant regime calls.

Supported series (via --series):
  sloos              Senior Loan Officer Opinion Survey (bank lending standards)
  ny_manufacturing   Empire State Manufacturing Survey (NY Fed)
  tx_manufacturing   Dallas Fed Manufacturing Outlook
  michigan           University of Michigan Consumer Sentiment
  inflation_exp      Inflation expectations (Michigan)
  chicago_conditions Chicago Fed Activity Index / financial conditions
  nonfarm_payrolls   Nonfarm payrolls (detailed breakdown)
  fomc_documents     FOMC statements / minutes (URLs and dates)
  cli                OECD Composite Leading Indicator
  dealer_positioning Primary Dealer Positioning (Fed net positions)

Usage:
    uv run scripts/macro_survey.py --series sloos --start 2020-01-01
    uv run scripts/macro_survey.py --series fomc_documents
    uv run scripts/macro_survey.py --series michigan --start 2024-01-01

Notes:
  - sloos / ny_manufacturing / tx_manufacturing / michigan /
    chicago_conditions / nonfarm_payrolls depend on the FRED provider → FRED_API_KEY required
  - inflation_exp uses the federal_reserve provider (no key); OpenBB's
    inflation_expectations endpoint does not accept fred as a provider
  - fomc_documents / cli / dealer_positioning use free official sources (Fed / OECD)
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import safe_call, single_emit
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# series name -> (callable, whether it accepts start_date/end_date, default provider, unit_note)
# unit_note is a one-line scale hint emitted to data.unit_note so AI agents can
# disambiguate the numeric meaning of each series' representative field(s)
# without memorising FRED conventions. See requirements §Req 1 observation table.
SERIES: dict[str, tuple[Any, bool, str | None, str]] = {
    "sloos": (
        lambda **kw: obb.economy.survey.sloos(**kw),
        True,
        "fred",
        "FRED value normalized to decimal (0.306 == 30.6%)",
    ),
    "ny_manufacturing": (
        lambda **kw: obb.economy.survey.manufacturing_outlook_ny(**kw),
        True,
        "fred",
        "diffusion index; raw points from the regional Fed survey",
    ),
    "tx_manufacturing": (
        lambda **kw: obb.economy.survey.manufacturing_outlook_texas(**kw),
        True,
        "fred",
        "diffusion index; raw points from the regional Fed survey",
    ),
    "michigan": (
        lambda **kw: obb.economy.survey.university_of_michigan(**kw),
        True,
        "fred",
        "consumer_sentiment is index points; inflation_expectation fields are decimal (0.029 == 2.9%)",
    ),
    "inflation_exp": (
        lambda **kw: obb.economy.survey.inflation_expectations(**kw),
        False,
        "federal_reserve",
        "federal_reserve provider emits raw percent (2.9851 == 2.99%)",
    ),
    "chicago_conditions": (
        lambda **kw: obb.economy.survey.economic_conditions_chicago(**kw),
        True,
        "fred",
        "index value; not percent",
    ),
    "nonfarm_payrolls": (
        lambda **kw: obb.economy.survey.nonfarm_payrolls(**kw),
        False,
        "fred",
        "value is absolute headcount",
    ),
    "fomc_documents": (
        lambda **kw: obb.economy.fomc_documents(**kw),
        False,
        None,
        "document metadata only (url, date); no numeric values",
    ),
    "cli": (
        lambda **kw: obb.economy.composite_leading_indicator(**kw),
        True,
        "oecd",
        "OECD composite leading indicator; index (100 = long-run trend)",
    ),
    "dealer_positioning": (
        lambda **kw: obb.economy.primary_dealer_positioning(**kw),
        True,
        None,
        "value is net position in millions of USD",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--series", required=True, choices=list(SERIES.keys()))
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument(
        "--provider", default=None, help="Defaults per series if omitted"
    )
    args = parser.parse_args()

    fn, takes_dates, default_provider, unit_note = SERIES[args.series]
    provider = args.provider or default_provider

    kwargs: dict[str, Any] = {}
    if provider:
        kwargs["provider"] = provider
    if takes_dates:
        if args.start:
            kwargs["start_date"] = args.start
        if args.end:
            kwargs["end_date"] = args.end

    result = safe_call(fn, **kwargs)
    return single_emit(
        result,
        tool="macro_survey",
        query_meta={
            "series": args.series,
            "provider": provider,
            "unit_note": unit_note,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
