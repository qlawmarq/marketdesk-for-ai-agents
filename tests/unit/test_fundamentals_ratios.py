"""Unit coverage for `scripts/fundamentals.py::normalize_ratio_records`.

The normalization step tags every ratio field emitted by a provider with
a `{value, unit}` pair so downstream AI agents do not have to consult
`--help` to know whether a field is a decimal fraction or a multiple.
Per-share currency amounts pass through untagged — they are not ratios.

Runs under the `unit` marker — no network, no credentials, deterministic.
The pre-collection guard in `tests/unit/conftest.py` installs a fake
`openbb` module so importing `fundamentals` is side-effect free.
"""

from __future__ import annotations

import math

import pytest

from _schema import classify_ratio_unit  # type: ignore[import-not-found]
from fundamentals import (  # type: ignore[import-not-found]
    flag_suspicious_decimals,
    normalize_metric_records,
    normalize_ratio_records,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# classify_ratio_unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "gross_profit_margin",
        "ebit_margin",
        "ebitda_margin",
        "operating_profit_margin",
        "net_profit_margin",
        "pretax_profit_margin",
        "bottom_line_profit_margin",
        "continuous_operations_profit_margin",
        "dividend_yield",
        "effective_tax_rate",
        "dividend_payout_ratio",
        "debt_to_equity",
        "debt_to_assets",
        "debt_to_capital",
        "long_term_debt_to_capital",
        "debt_to_market_cap",
        "return_on_equity",
        "return_on_assets",
        "roe",
        "roa",
        "roic",
    ],
)
def test_classify_ratio_unit_tags_known_decimal_fields_as_decimal(field: str) -> None:
    assert classify_ratio_unit(field) == "decimal"


@pytest.mark.parametrize(
    "field",
    [
        "current_ratio",
        "quick_ratio",
        "cash_ratio",
        "solvency_ratio",
        "financial_leverage_ratio",
        "asset_turnover",
        "inventory_turnover",
        "receivables_turnover",
        "payables_turnover",
        "fixed_asset_turnover",
        "interest_coverage_ratio",
        "debt_service_coverage_ratio",
        "price_to_earnings",
        "price_to_book",
        "price_to_sales",
        "price_to_free_cash_flow",
        "price_to_operating_cash_flow",
        # PEG is a multiple, not a decimal fraction — the old substring
        # classifier wrongly tagged this as decimal because of "growth".
        "price_to_earnings_growth",
        "forward_price_to_earnings_growth",
        "enterprise_value_multiple",
        "net_income_per_ebt",
        "ebt_per_ebit",
        "price_to_fair_value",
    ],
)
def test_classify_ratio_unit_defaults_unknown_and_multiples_to_ratio(field: str) -> None:
    assert classify_ratio_unit(field) == "ratio"


# ---------------------------------------------------------------------------
# normalize_ratio_records
# ---------------------------------------------------------------------------


def test_normalize_ratio_records_tags_decimal_fields_with_value_and_unit() -> None:
    records = [{"symbol": "AAPL", "gross_profit_margin": 0.46}]
    out = normalize_ratio_records(records)

    assert out[0]["symbol"] == "AAPL"
    assert out[0]["gross_profit_margin"] == {"value": 0.46, "unit": "decimal"}


def test_normalize_ratio_records_tags_multiples_as_ratio() -> None:
    records = [{"symbol": "AAPL", "price_to_earnings": 28.4}]
    out = normalize_ratio_records(records)

    assert out[0]["price_to_earnings"] == {"value": 28.4, "unit": "ratio"}


def test_normalize_ratio_records_tags_current_ratio_as_ratio() -> None:
    records = [{"symbol": "AAPL", "current_ratio": 1.2}]
    out = normalize_ratio_records(records)

    assert out[0]["current_ratio"] == {"value": 1.2, "unit": "ratio"}


def test_normalize_ratio_records_passes_through_per_share_currency_fields() -> None:
    """Per-share fields are currency, not ratios — they must not carry a unit tag
    that would mislead an AI agent into treating them as dimensionless."""

    records = [
        {
            "symbol": "AAPL",
            "revenue_per_share": 5.23,
            "book_value_per_share": 3.9,
            "dividend_per_share": 0.24,
            "free_cash_flow_per_share": 4.1,
        }
    ]
    out = normalize_ratio_records(records)

    assert out[0]["revenue_per_share"] == 5.23
    assert out[0]["book_value_per_share"] == 3.9
    assert out[0]["dividend_per_share"] == 0.24
    assert out[0]["free_cash_flow_per_share"] == 4.1


def test_normalize_ratio_records_passes_through_identifier_and_date_fields() -> None:
    records = [
        {
            "symbol": "AAPL",
            "period_ending": "2025-09-28",
            "fiscal_period": "FY",
            "calendar_year": 2025,
            "current_ratio": 1.1,
        }
    ]
    out = normalize_ratio_records(records)

    assert out[0]["symbol"] == "AAPL"
    assert out[0]["period_ending"] == "2025-09-28"
    assert out[0]["fiscal_period"] == "FY"
    assert out[0]["calendar_year"] == 2025
    assert out[0]["current_ratio"] == {"value": 1.1, "unit": "ratio"}


def test_normalize_ratio_records_passes_through_pandas_index_row_number() -> None:
    """pandas `reset_index()` injects an `index` column; must not be tagged.

    Without passthrough, a row number like 0 would be rendered as
    ``{"value": 0.0, "unit": "ratio"}`` — a bogus "ratio" field that
    would mislead downstream AI agents.
    """

    records = [
        {"index": 0, "symbol": "AAPL", "current_ratio": 1.1},
        {"index": 1, "symbol": "AAPL", "current_ratio": 1.2},
    ]
    out = normalize_ratio_records(records)

    assert out[0]["index"] == 0
    assert out[1]["index"] == 1
    assert out[0]["current_ratio"] == {"value": 1.1, "unit": "ratio"}


def test_normalize_ratio_records_passes_through_none_and_nan() -> None:
    records = [
        {
            "symbol": "AAPL",
            "gross_profit_margin": None,
            "current_ratio": math.nan,
        }
    ]
    out = normalize_ratio_records(records)

    assert out[0]["gross_profit_margin"] is None
    value = out[0]["current_ratio"]
    assert isinstance(value, float) and math.isnan(value)


def test_normalize_ratio_records_passes_through_bool_unchanged() -> None:
    records = [{"symbol": "AAPL", "is_preferred": True}]
    out = normalize_ratio_records(records)

    assert out[0]["is_preferred"] is True


def test_normalize_ratio_records_does_not_mutate_input() -> None:
    records = [{"symbol": "AAPL", "gross_profit_margin": 0.46}]
    snapshot = [dict(r) for r in records]

    normalize_ratio_records(records)

    assert records == snapshot


def test_normalize_ratio_records_preserves_negative_decimal_values() -> None:
    records = [{"symbol": "X", "operating_profit_margin": -0.1}]
    out = normalize_ratio_records(records)

    assert out[0]["operating_profit_margin"] == {"value": -0.1, "unit": "decimal"}


def test_normalize_ratio_records_preserves_peg_as_multiple_not_decimal() -> None:
    """Regression guard: a legitimate PEG of 1.8 is a multiple, not a
    decimal growth rate. The old substring classifier wrongly divided
    large values into percent form; the explicit classifier leaves it alone.
    """

    records = [{"symbol": "X", "price_to_earnings_growth": 1.8}]
    out = normalize_ratio_records(records)

    assert out[0]["price_to_earnings_growth"] == {"value": 1.8, "unit": "ratio"}


def test_normalize_ratio_records_preserves_large_peg_values() -> None:
    records = [{"symbol": "X", "price_to_earnings_growth": 5.2}]
    out = normalize_ratio_records(records)

    assert out[0]["price_to_earnings_growth"] == {"value": 5.2, "unit": "ratio"}


# ---------------------------------------------------------------------------
# flag_suspicious_decimals
# ---------------------------------------------------------------------------


def test_flag_suspicious_decimals_leaves_in_range_cells_untouched() -> None:
    records = normalize_ratio_records(
        [{"symbol": "AAPL", "net_profit_margin": 0.25}]
    )
    out, warnings = flag_suspicious_decimals(records)

    assert warnings == []
    assert out[0]["net_profit_margin"] == {"value": 0.25, "unit": "decimal"}


def test_flag_suspicious_decimals_flags_over_threshold_cells() -> None:
    records = normalize_ratio_records(
        [{"symbol": "AAPL", "net_profit_margin": 12.0}]
    )
    out, warnings = flag_suspicious_decimals(records)

    cell = out[0]["net_profit_margin"]
    assert cell["value"] == 12.0
    assert cell["unit"] == "decimal"
    assert cell["status"] == "suspicious"

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["field"] == "net_profit_margin"
    assert warning["symbol"] == "AAPL"
    assert warning["value"] == 12.0
    assert "DECIMAL_SANITY_MAX" in warning["reason"]


def test_flag_suspicious_decimals_flags_metrics_decimal_cell_over_threshold() -> None:
    """Req 5.1 / 5.4: metrics' decimal-tagged cells share the ratios sanity
    gate, so a `payout_ratio` arriving as 12.0 (percent-style mis-tag)
    surfaces the same `status=suspicious` / warnings contract as ratios.
    """

    records = normalize_metric_records(
        [{"symbol": "AAPL", "payout_ratio": 12.0}]
    )
    out, warnings = flag_suspicious_decimals(records)

    cell = out[0]["payout_ratio"]
    assert cell["value"] == 12.0
    assert cell["unit"] == "decimal"
    assert cell["status"] == "suspicious"

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["field"] == "payout_ratio"
    assert warning["symbol"] == "AAPL"
    assert warning["value"] == 12.0
    assert "DECIMAL_SANITY_MAX" in warning["reason"]


def test_flag_suspicious_decimals_ignores_nan_decimal_cells() -> None:
    records = normalize_ratio_records(
        [{"symbol": "AAPL", "net_profit_margin": math.nan}]
    )
    out, warnings = flag_suspicious_decimals(records)

    assert warnings == []
    # NaN passes through normalization as a bare float (not a {value, unit}
    # dict), so there is no `status` key to assert on; the absence of a
    # warning is the contract.
    value = out[0]["net_profit_margin"]
    assert isinstance(value, float) and math.isnan(value)


# ---------------------------------------------------------------------------
# normalize_metric_records (yfinance metrics, provider-scoped)
# ---------------------------------------------------------------------------


def test_normalize_metric_records_tags_currency_fields() -> None:
    records = [
        {
            "symbol": "AAPL",
            "market_cap": 3_000_000_000_000.0,
            "enterprise_value": 3_100_000_000_000.0,
        }
    ]
    out = normalize_metric_records(records)

    assert out[0]["market_cap"] == {"value": 3_000_000_000_000.0, "unit": "currency"}
    assert out[0]["enterprise_value"] == {
        "value": 3_100_000_000_000.0,
        "unit": "currency",
    }


def test_normalize_metric_records_tags_percent_fields() -> None:
    records = [{"symbol": "AAPL", "debt_to_equity": 102.63, "dividend_yield": 0.38}]
    out = normalize_metric_records(records)

    assert out[0]["debt_to_equity"] == {"value": 102.63, "unit": "percent"}
    assert out[0]["dividend_yield"] == {"value": 0.38, "unit": "percent"}


def test_normalize_metric_records_tags_decimal_fields() -> None:
    records = [
        {
            "symbol": "AAPL",
            "gross_margin": 0.47325,
            "payout_ratio": 0.1304,
            "return_on_equity": 1.5202,
        }
    ]
    out = normalize_metric_records(records)

    assert out[0]["gross_margin"] == {"value": 0.47325, "unit": "decimal"}
    assert out[0]["payout_ratio"] == {"value": 0.1304, "unit": "decimal"}
    assert out[0]["return_on_equity"] == {"value": 1.5202, "unit": "decimal"}


def test_normalize_metric_records_tags_ratio_fields() -> None:
    records = [{"symbol": "AAPL", "pe_ratio": 28.4, "beta": 1.25}]
    out = normalize_metric_records(records)

    assert out[0]["pe_ratio"] == {"value": 28.4, "unit": "ratio"}
    assert out[0]["beta"] == {"value": 1.25, "unit": "ratio"}


def test_normalize_metric_records_passes_through_unknown_fields_untagged() -> None:
    """Fail-closed: unknown numeric fields stay bare rather than mis-tagged.

    `book_value` in yfinance metrics is a per-share USD amount and must not
    be coerced into `{value, unit}` — the README + `METRIC_UNIT_MAP` are the
    single source of truth, not the normalizer's guess.
    """

    records = [
        {
            "symbol": "AAPL",
            "book_value": 4.31,
            "overall_risk": 1,
            "unknown_new_field": 3.14,
        }
    ]
    out = normalize_metric_records(records)

    assert out[0]["book_value"] == 4.31
    assert out[0]["overall_risk"] == 1
    assert out[0]["unknown_new_field"] == 3.14


def test_normalize_metric_records_passes_through_per_share_and_identifier_fields() -> None:
    records = [
        {
            "symbol": "AAPL",
            "currency": "USD",
            "period_ending": "2025-09-28",
            "book_value_per_share": 3.9,
            "market_cap": 3_000_000_000_000.0,
        }
    ]
    out = normalize_metric_records(records)

    assert out[0]["symbol"] == "AAPL"
    assert out[0]["currency"] == "USD"
    assert out[0]["period_ending"] == "2025-09-28"
    assert out[0]["book_value_per_share"] == 3.9
    assert out[0]["market_cap"] == {"value": 3_000_000_000_000.0, "unit": "currency"}


def test_normalize_metric_records_passes_through_none_nan_and_bool() -> None:
    records = [
        {
            "symbol": "AAPL",
            "market_cap": None,
            "pe_ratio": math.nan,
            "is_flag": True,
        }
    ]
    out = normalize_metric_records(records)

    assert out[0]["market_cap"] is None
    value = out[0]["pe_ratio"]
    assert isinstance(value, float) and math.isnan(value)
    assert out[0]["is_flag"] is True


def test_normalize_metric_records_does_not_mutate_input() -> None:
    records = [{"symbol": "AAPL", "market_cap": 3.0e12}]
    snapshot = [dict(r) for r in records]

    normalize_metric_records(records)

    assert records == snapshot


def test_normalize_metric_records_dividend_yield_disagrees_with_ratio_tagging() -> None:
    """Same field name, different provider → different unit.

    FMP `ratios` tags `dividend_yield` as `decimal`; yfinance `metrics`
    emits it in percent form. The two normalizers must not bleed.
    """

    ratios_out = normalize_ratio_records(
        [{"symbol": "AAPL", "dividend_yield": 0.004}]
    )
    metrics_out = normalize_metric_records(
        [{"symbol": "AAPL", "dividend_yield": 0.38}]
    )

    assert ratios_out[0]["dividend_yield"] == {"value": 0.004, "unit": "decimal"}
    assert metrics_out[0]["dividend_yield"] == {"value": 0.38, "unit": "percent"}
