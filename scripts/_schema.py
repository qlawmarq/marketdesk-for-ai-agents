"""Ratio-output schema: the one place that names decimal-unit fields
and interprets ``{value, unit}`` cells.

Shared by:
- ``scripts/fundamentals.py`` (normalize + sanity-flag)
- ``tests/unit/test_schema.py`` (classifier + accessor coverage)
- ``tests/unit/test_fundamentals_ratios.py`` (existing normalizer tests)
- the one-shot downstream audit reads ``DECIMAL_RATIO_FIELDS`` as the
  grep target, so adding a field here automatically widens the audit
  scope on the next run.

Pure-data + pure-function; stdlib only; no side effects at import time.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


# Fields emitted by OpenBB's FMP ratios endpoint that represent a decimal
# fraction (0-1 form of a percentage or similar). FMP stores these already
# in decimal (`x-unit_measurement: percent, x-frontend_multiply: 100` in
# the schema means "display-time ×100", not stored ×100), so no numeric
# rescaling is needed — only the unit tag.
DECIMAL_RATIO_FIELDS: frozenset[str] = frozenset(
    {
        "gross_profit_margin",
        "ebit_margin",
        "ebitda_margin",
        "operating_profit_margin",
        "pretax_profit_margin",
        "continuous_operations_profit_margin",
        "net_profit_margin",
        "bottom_line_profit_margin",
        "dividend_yield",
        "effective_tax_rate",
        "dividend_payout_ratio",
        "debt_to_assets",
        "debt_to_equity",
        "debt_to_capital",
        "long_term_debt_to_capital",
        "debt_to_market_cap",
        # Common aliases that may appear under different providers.
        "return_on_equity",
        "return_on_assets",
        "return_on_invested_capital",
        "roe",
        "roa",
        "roic",
        "roi",
    }
)

# Currency-denominated per-share amounts; not ratios. Pass through
# untagged so downstream agents do not mistake them for dimensionless
# ratios.
PER_SHARE_FIELDS: frozenset[str] = frozenset(
    {
        "revenue_per_share",
        "net_income_per_share",
        "interest_debt_per_share",
        "cash_per_share",
        "book_value_per_share",
        "tangible_book_value_per_share",
        "shareholders_equity_per_share",
        "operating_cash_flow_per_share",
        "capex_per_share",
        "free_cash_flow_per_share",
        "dividend_per_share",
        "eps",
        "earnings_per_share",
    }
)

# Identifier / date-like / bookkeeping fields that must never be tagged.
# `index` carries the pandas row number produced by `_common.to_records`'
# `reset_index()` call — it is noise from the tabular pipeline rather
# than a ratio, so it must pass through without being wrapped into a
# `{value, unit}` cell.
RATIO_PASSTHROUGH_FIELDS: frozenset[str] = frozenset(
    {
        "symbol",
        "ticker",
        "cik",
        "date",
        "period",
        "period_ending",
        "period_end",
        "fiscal_period",
        "fiscal_year",
        "calendar_year",
        "reported_currency",
        "currency",
        "accepted_date",
        "filing_date",
        "report_date",
        "index",
    }
)

# A decimal-unit ratio whose absolute value exceeds this threshold is
# almost certainly a percent-style value mis-tagged as decimal (e.g.
# net_profit_margin arriving as 12.0 instead of 0.12).
DECIMAL_SANITY_MAX: float = 5.0


# yfinance `fundamentals --type metrics` emits numeric fields in a mix of
# unit conventions (decimal 0-1 form, percent 100-form, dimensionless
# multipliers, and currency totals). This map is the single source of
# truth that drives `{value, unit}` tagging for that provider.
#
# Intentionally provider-scoped and independent of `DECIMAL_RATIO_FIELDS`
# (FMP ratios): the same field name can carry a different unit under a
# different provider (e.g. `dividend_yield` is `decimal` for FMP ratios
# but `percent` for yfinance metrics).
METRIC_UNIT_MAP: Mapping[str, str] = {
    # decimal (0-1 fraction form)
    "gross_margin": "decimal",
    "operating_margin": "decimal",
    "ebitda_margin": "decimal",
    "profit_margin": "decimal",
    "return_on_assets": "decimal",
    "return_on_equity": "decimal",
    "payout_ratio": "decimal",
    "dividend_yield_5y_avg": "decimal",
    "earnings_growth": "decimal",
    "earnings_growth_quarterly": "decimal",
    "revenue_growth": "decimal",
    "price_return_1y": "decimal",
    # percent (100-form)
    "debt_to_equity": "percent",
    "dividend_yield": "percent",
    # ratio (dimensionless multiplier)
    "pe_ratio": "ratio",
    "forward_pe": "ratio",
    "peg_ratio": "ratio",
    "peg_ratio_ttm": "ratio",
    "enterprise_to_ebitda": "ratio",
    "enterprise_to_revenue": "ratio",
    "quick_ratio": "ratio",
    "current_ratio": "ratio",
    "price_to_book": "ratio",
    "beta": "ratio",
    # currency (total amount)
    "market_cap": "currency",
    "enterprise_value": "currency",
}


def classify_ratio_unit(field: str) -> str:
    """Classify a ratio field name as ``"decimal"`` or ``"ratio"``.

    Uses an explicit allowlist derived from the FMP ratios schema so
    unfamiliar field names fall back to ``"ratio"`` rather than being
    silently coerced.
    """

    key = field.lower()
    if key in DECIMAL_RATIO_FIELDS:
        return "decimal"
    return "ratio"


def classify_metric_unit(field: str) -> str | None:
    """Classify a yfinance metrics field name, or ``None`` when unknown.

    Returns one of ``"decimal"`` / ``"percent"`` / ``"ratio"`` /
    ``"currency"`` for fields in :data:`METRIC_UNIT_MAP`, else ``None``
    (fail-closed — unfamiliar fields pass through untagged rather than
    being silently coerced to ``"ratio"``).

    Provider-scoped: the yfinance metrics endpoint uses a different unit
    convention than FMP ratios, so this classifier is intentionally
    independent of :func:`classify_ratio_unit`.
    """

    return METRIC_UNIT_MAP.get(field.lower())


def cell_value(cell: Any, default: Any = None) -> Any:
    """Read a ratio cell uniformly.

    Tolerates both the structured ``{value, unit}`` form and legacy
    bare-number values so a caller transitioning between shapes never
    reads a dict where a number is expected. Returns ``default`` when
    the cell is ``None``.
    """

    if isinstance(cell, dict) and "value" in cell:
        return cell["value"]
    if cell is None:
        return default
    return cell


def is_suspicious_decimal(cell: Any) -> bool:
    """True when a decimal-tagged cell has implausibly large magnitude."""

    if not isinstance(cell, dict) or cell.get("unit") != "decimal":
        return False
    value = cell.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if math.isnan(value):
        return False
    return abs(value) > DECIMAL_SANITY_MAX
