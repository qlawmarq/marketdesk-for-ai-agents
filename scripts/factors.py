"""Fetch Fama-French factor returns from the famafrench public dataset.

Surfaces canonical risk factors (Mkt-RF / SMB / HML / RMW / CMA, plus
momentum and reversal portfolios) so the analyst can attribute regime-level
return drivers without leaving the JSON-over-stdout contract.

Usage:
    uv run scripts/factors.py
    uv run scripts/factors.py --region japan --frequency monthly
    uv run scripts/factors.py --factor momentum --start 2010-01-01
    uv run scripts/factors.py --frequency weekly --factor 3_factors

Defaults: ``--region america``, ``--frequency monthly``, ``--factor
5_factors`` so the canonical Mkt-RF / SMB / HML / RMW / CMA columns appear
without requiring the caller to enumerate them.

The provider's ``--region`` vocabulary is adopted verbatim instead of the
requirements' ``us|global|developed|emerging`` enumeration to avoid a
translation table prone to drift; see implementation-notes.md task 5.2 for
the rationale.

The famafrench provider is public; no key required.
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import safe_call, single_emit
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# Live provider literal probe 2026-04-25 (obb.famafrench.factors __doc__):
#   region:    america, north_america, europe, japan, asia_pacific_ex_japan,
#              developed, developed_ex_us, emerging
#   factor:    5_factors, 3_factors, momentum, st_reversal, lt_reversal
#   frequency: daily, weekly, monthly, annual
REGION_CHOICES = [
    "america",
    "north_america",
    "europe",
    "japan",
    "asia_pacific_ex_japan",
    "developed",
    "developed_ex_us",
    "emerging",
]
FREQUENCY_CHOICES = ["daily", "weekly", "monthly", "annual"]
FACTOR_CHOICES = ["5_factors", "3_factors", "momentum", "st_reversal", "lt_reversal"]

PROVIDER_CHOICES = ["famafrench"]
DEFAULT_PROVIDER = "famafrench"

DEFAULT_REGION = "america"
DEFAULT_FREQUENCY = "monthly"
DEFAULT_FACTOR = "5_factors"


def resolve_defaults(
    region: str | None,
    frequency: str | None,
    factor: str | None,
) -> tuple[str, str, str, list[str]]:
    """Project ``None``-valued args onto their defaults and report which
    fields were defaulted, preserving the canonical order
    ``["region", "frequency", "factor"]`` for the metadata echo.
    """

    defaults_applied: list[str] = []
    if region is None:
        region = DEFAULT_REGION
        defaults_applied.append("region")
    if frequency is None:
        frequency = DEFAULT_FREQUENCY
        defaults_applied.append("frequency")
    if factor is None:
        factor = DEFAULT_FACTOR
        defaults_applied.append("factor")
    return region, frequency, factor, defaults_applied


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--region", default=None, choices=REGION_CHOICES)
    parser.add_argument("--frequency", default=None, choices=FREQUENCY_CHOICES)
    parser.add_argument("--factor", default=None, choices=FACTOR_CHOICES)
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument(
        "--provider", default=DEFAULT_PROVIDER, choices=PROVIDER_CHOICES
    )
    args = parser.parse_args()

    region, frequency, factor, defaults_applied = resolve_defaults(
        args.region, args.frequency, args.factor
    )

    kwargs: dict[str, Any] = {
        "region": region,
        "frequency": frequency,
        "factor": factor,
        "provider": args.provider,
    }
    if args.start:
        kwargs["start_date"] = args.start
    if args.end:
        kwargs["end_date"] = args.end

    call_result = safe_call(obb.famafrench.factors, **kwargs)
    query_meta: dict[str, Any] = {
        "region": region,
        "frequency": frequency,
        "factor": factor,
        "defaults_applied": defaults_applied,
        "provider": args.provider,
    }
    if args.start:
        query_meta["start"] = args.start
    if args.end:
        query_meta["end"] = args.end

    return single_emit(call_result, tool="factors", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
