"""Unit coverage for ``scripts/_schema.py``.

The schema module is the single source of truth for ratio field
metadata and ``{value, unit}`` cell interpretation. These tests pin
down its three public helpers so downstream callers (fundamentals
normalizer, runtime sanity guard, one-shot audit) never silently
disagree on shape or thresholds.

Runs under the ``unit`` marker — stdlib only, deterministic.
"""

from __future__ import annotations

import math

import pytest

from _schema import (  # type: ignore[import-not-found]
    DECIMAL_RATIO_FIELDS,
    DECIMAL_SANITY_MAX,
    METRIC_UNIT_MAP,
    cell_value,
    classify_metric_unit,
    classify_ratio_unit,
    is_suspicious_decimal,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# classify_ratio_unit
# ---------------------------------------------------------------------------


def test_classify_ratio_unit_returns_decimal_for_allowlisted_field() -> None:
    assert classify_ratio_unit("net_profit_margin") == "decimal"


def test_classify_ratio_unit_returns_ratio_for_non_allowlisted_field() -> None:
    assert classify_ratio_unit("current_ratio") == "ratio"


# ---------------------------------------------------------------------------
# cell_value
# ---------------------------------------------------------------------------


def test_cell_value_reads_structured_cell() -> None:
    assert cell_value({"value": 0.12, "unit": "decimal"}) == 0.12


def test_cell_value_reads_bare_number_legacy_cell() -> None:
    assert cell_value(0.12) == 0.12


def test_cell_value_returns_default_for_none_with_default() -> None:
    assert cell_value(None, default=0.0) == 0.0


def test_cell_value_returns_none_for_none_without_default() -> None:
    assert cell_value(None) is None


# ---------------------------------------------------------------------------
# is_suspicious_decimal
# ---------------------------------------------------------------------------


def test_is_suspicious_decimal_below_threshold_is_false() -> None:
    assert is_suspicious_decimal({"value": 0.12, "unit": "decimal"}) is False


def test_is_suspicious_decimal_equal_to_threshold_is_false() -> None:
    assert is_suspicious_decimal({"value": DECIMAL_SANITY_MAX, "unit": "decimal"}) is False


def test_is_suspicious_decimal_just_above_threshold_is_true() -> None:
    assert is_suspicious_decimal({"value": DECIMAL_SANITY_MAX + 0.01, "unit": "decimal"}) is True


def test_is_suspicious_decimal_nan_value_is_false() -> None:
    assert is_suspicious_decimal({"value": math.nan, "unit": "decimal"}) is False


def test_is_suspicious_decimal_bool_value_is_false() -> None:
    assert is_suspicious_decimal({"value": True, "unit": "decimal"}) is False


def test_is_suspicious_decimal_non_dict_input_is_false() -> None:
    assert is_suspicious_decimal(42.0) is False


def test_is_suspicious_decimal_unit_ratio_never_suspicious_regardless_of_magnitude() -> None:
    assert is_suspicious_decimal({"value": 1_000.0, "unit": "ratio"}) is False


def test_is_suspicious_decimal_negative_above_threshold_is_true() -> None:
    assert is_suspicious_decimal({"value": -(DECIMAL_SANITY_MAX + 1.0), "unit": "decimal"}) is True


# ---------------------------------------------------------------------------
# classify_metric_unit (yfinance metrics, provider-scoped)
# ---------------------------------------------------------------------------


def test_classify_metric_unit_returns_currency_for_market_cap() -> None:
    assert classify_metric_unit("market_cap") == "currency"


def test_classify_metric_unit_returns_currency_for_enterprise_value() -> None:
    assert classify_metric_unit("enterprise_value") == "currency"


def test_classify_metric_unit_returns_percent_for_debt_to_equity() -> None:
    assert classify_metric_unit("debt_to_equity") == "percent"


def test_classify_metric_unit_returns_percent_for_dividend_yield() -> None:
    assert classify_metric_unit("dividend_yield") == "percent"


def test_classify_metric_unit_returns_decimal_for_gross_margin() -> None:
    assert classify_metric_unit("gross_margin") == "decimal"


def test_classify_metric_unit_returns_decimal_for_payout_ratio() -> None:
    assert classify_metric_unit("payout_ratio") == "decimal"


def test_classify_metric_unit_returns_decimal_for_return_on_equity() -> None:
    assert classify_metric_unit("return_on_equity") == "decimal"


def test_classify_metric_unit_returns_ratio_for_pe_ratio() -> None:
    assert classify_metric_unit("pe_ratio") == "ratio"


def test_classify_metric_unit_returns_ratio_for_beta() -> None:
    assert classify_metric_unit("beta") == "ratio"


def test_classify_metric_unit_returns_none_for_book_value_per_share() -> None:
    # `book_value` in yfinance metrics is a per-share USD amount, not a ratio,
    # so it must not be tagged with a unit — passthrough via None.
    assert classify_metric_unit("book_value") is None


def test_classify_metric_unit_returns_none_for_overall_risk() -> None:
    # Discrete yfinance governance score — intentionally unmapped.
    assert classify_metric_unit("overall_risk") is None


def test_classify_metric_unit_returns_none_for_unknown_field() -> None:
    # Fail-closed: unknown fields must fall through untagged rather than
    # be coerced to "ratio".
    assert classify_metric_unit("unknown_xyz") is None


def test_classify_metric_unit_is_case_insensitive() -> None:
    assert classify_metric_unit("Market_Cap") == "currency"


def test_metric_unit_map_and_decimal_ratio_fields_disagree_on_dividend_yield() -> None:
    # Provider-scoped: the same field name can carry different units across
    # providers. FMP ratios stores `dividend_yield` as decimal (0-1 form),
    # while yfinance metrics emits it in percent form (e.g. 0.38 == 0.38%).
    assert METRIC_UNIT_MAP["dividend_yield"] == "percent"
    assert "dividend_yield" in DECIMAL_RATIO_FIELDS
