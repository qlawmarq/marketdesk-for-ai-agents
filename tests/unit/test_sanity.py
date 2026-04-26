"""Offline coverage for the integration-tier sanity helpers.

Every helper is exercised with at least one passing and one failing
case per failure mode enumerated in the design: NaN/inf, out-of-range
numbers, unordered dates, bound-escaping dates, missing symbols, and
duplicated symbols. Runs under the `unit` marker — no network, no
credentials, deterministic.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from tests.integration._sanity import (
    assert_dates_ascending_in_range,
    assert_finite_in_range,
    assert_symbols_present,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# assert_finite_in_range
# ---------------------------------------------------------------------------


def test_finite_in_range_passes_for_value_inside_inclusive_bounds() -> None:
    assert_finite_in_range(0.5, low=0.0, high=1.0, name="weight")
    assert_finite_in_range(0.0, low=0.0, high=1.0, name="weight_low")
    assert_finite_in_range(1.0, low=0.0, high=1.0, name="weight_high")
    assert_finite_in_range(42, low=-100, high=100, name="count")


def test_finite_in_range_rejects_nan() -> None:
    with pytest.raises(AssertionError, match="price"):
        assert_finite_in_range(math.nan, low=0.0, high=1e9, name="price")


def test_finite_in_range_rejects_positive_infinity() -> None:
    with pytest.raises(AssertionError, match="price"):
        assert_finite_in_range(math.inf, low=0.0, high=1e9, name="price")


def test_finite_in_range_rejects_negative_infinity() -> None:
    with pytest.raises(AssertionError, match="price"):
        assert_finite_in_range(-math.inf, low=0.0, high=1e9, name="price")


def test_finite_in_range_rejects_value_below_low() -> None:
    with pytest.raises(AssertionError, match=r"rsi.*\[0, 100\]"):
        assert_finite_in_range(-0.1, low=0, high=100, name="rsi")


def test_finite_in_range_rejects_value_above_high() -> None:
    with pytest.raises(AssertionError, match=r"rsi.*\[0, 100\]"):
        assert_finite_in_range(100.1, low=0, high=100, name="rsi")


def test_finite_in_range_rejects_non_numeric_type() -> None:
    with pytest.raises(AssertionError, match="price"):
        assert_finite_in_range("1.0", low=0, high=10, name="price")  # type: ignore[arg-type]


def test_finite_in_range_rejects_bool_even_though_bool_is_int_subclass() -> None:
    with pytest.raises(AssertionError, match="flag"):
        assert_finite_in_range(True, low=0, high=10, name="flag")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# assert_dates_ascending_in_range
# ---------------------------------------------------------------------------


def test_dates_ascending_passes_for_strictly_increasing_records_within_bounds() -> None:
    records = [
        {"date": "2026-01-02", "value": 1},
        {"date": "2026-01-03", "value": 2},
        {"date": "2026-01-06", "value": 3},
    ]
    assert_dates_ascending_in_range(
        records,
        date_key="date",
        start=date(2026, 1, 1),
        end=date(2026, 1, 10),
    )


def test_dates_ascending_passes_without_bounds() -> None:
    records = [
        {"date": "2000-01-01"},
        {"date": "2100-01-01"},
    ]
    assert_dates_ascending_in_range(
        records,
        date_key="date",
        start=None,
        end=None,
    )


def test_dates_ascending_rejects_unordered_records() -> None:
    records = [
        {"date": "2026-01-03"},
        {"date": "2026-01-02"},
    ]
    with pytest.raises(AssertionError, match="not strictly after previous"):
        assert_dates_ascending_in_range(
            records,
            date_key="date",
            start=None,
            end=None,
        )


def test_dates_ascending_rejects_equal_consecutive_dates() -> None:
    records = [
        {"date": "2026-01-03"},
        {"date": "2026-01-03"},
    ]
    with pytest.raises(AssertionError, match="not strictly after previous"):
        assert_dates_ascending_in_range(
            records,
            date_key="date",
            start=None,
            end=None,
        )


def test_dates_ascending_rejects_date_before_start_bound() -> None:
    records = [
        {"date": "2025-12-31"},
        {"date": "2026-01-02"},
    ]
    with pytest.raises(AssertionError, match="precedes start"):
        assert_dates_ascending_in_range(
            records,
            date_key="date",
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
        )


def test_dates_ascending_rejects_date_after_end_bound() -> None:
    records = [
        {"date": "2026-01-02"},
        {"date": "2027-01-01"},
    ]
    with pytest.raises(AssertionError, match="exceeds end"):
        assert_dates_ascending_in_range(
            records,
            date_key="date",
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
        )


def test_dates_ascending_rejects_missing_date_key() -> None:
    records = [
        {"date": "2026-01-02"},
        {"value": 2},
    ]
    with pytest.raises(AssertionError, match="missing date key"):
        assert_dates_ascending_in_range(
            records,
            date_key="date",
            start=None,
            end=None,
        )


# ---------------------------------------------------------------------------
# assert_symbols_present
# ---------------------------------------------------------------------------


def test_symbols_present_passes_when_every_expected_symbol_appears_once() -> None:
    payload = [
        {"symbol": "AAPL", "price": 1.0},
        {"symbol": "MSFT", "price": 2.0},
        {"symbol": "NVDA", "price": 3.0},
    ]
    assert_symbols_present(payload, expected=["AAPL", "MSFT", "NVDA"])


def test_symbols_present_honors_custom_symbol_key() -> None:
    payload = [
        {"ticker": "AAPL"},
        {"ticker": "MSFT"},
    ]
    assert_symbols_present(
        payload,
        expected=["AAPL", "MSFT"],
        symbol_key="ticker",
    )


def test_symbols_present_rejects_missing_symbol() -> None:
    payload = [{"symbol": "AAPL"}]
    with pytest.raises(AssertionError, match="missing expected symbols"):
        assert_symbols_present(payload, expected=["AAPL", "MSFT"])


def test_symbols_present_rejects_duplicate_symbol() -> None:
    payload = [
        {"symbol": "AAPL"},
        {"symbol": "AAPL"},
    ]
    with pytest.raises(AssertionError, match="duplicate symbol"):
        assert_symbols_present(payload, expected=["AAPL"])


def test_symbols_present_rejects_row_missing_symbol_key() -> None:
    payload = [
        {"symbol": "AAPL"},
        {"price": 10.0},
    ]
    with pytest.raises(AssertionError, match="missing symbol key"):
        assert_symbols_present(payload, expected=["AAPL"])
