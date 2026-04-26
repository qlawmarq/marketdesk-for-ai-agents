"""Offline unit coverage for ``scripts/institutional.py::_is_partial_filing_window``.

Pins the 13F filing-window judgement (SEC 17 CFR §240.13f-1, 45 calendar
days) against the design contract: ±1 day boundary semantics with
strict ``> today`` comparison, and defensive type-coercion of the
upstream ``record["date"]`` field (date / datetime / pandas.Timestamp /
ISO string / None / unsupported types) collapsing to a safe ``False``
fall-through when the value is uninterpretable.

Runs under the ``unit`` marker. The pre-collection guard in
``tests/unit/conftest.py`` installs a fake ``openbb`` module so importing
``institutional`` is side-effect free.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from institutional import _is_partial_filing_window  # type: ignore[import-not-found]

pytestmark = pytest.mark.unit


_TODAY = date(2026, 4, 25)


class TestBoundaries:
    """The 45-day window is open on the upper edge: only `record_date +
    45 days > today` flags partial. Exact 45-day-old records are
    treated as filed (`False`)."""

    def test_inside_window_flags_true(self) -> None:
        assert _is_partial_filing_window(date(2026, 3, 31), _TODAY) is True

    def test_exactly_45_days_old_flags_false(self) -> None:
        assert _is_partial_filing_window(date(2026, 3, 11), _TODAY) is False

    def test_46_days_old_flags_false(self) -> None:
        assert _is_partial_filing_window(date(2026, 3, 10), _TODAY) is False


class TestTypeCoercion:
    """Upstream FMP `to_records` may yield `record["date"]` as
    `datetime.date`, `datetime.datetime`, `pandas.Timestamp` or an ISO
    string depending on provider version; the helper absorbs the spread
    so callers never branch on type."""

    def test_iso_string_parses(self) -> None:
        assert _is_partial_filing_window("2026-03-31", _TODAY) is True

    def test_datetime_strips_time(self) -> None:
        assert (
            _is_partial_filing_window(datetime(2026, 3, 31, 23, 59), _TODAY)
            is True
        )

    def test_pandas_timestamp_absorbed_via_datetime_subclass(self) -> None:
        assert _is_partial_filing_window(pd.Timestamp("2026-03-31"), _TODAY) is True

    def test_invalid_string_returns_false(self) -> None:
        assert _is_partial_filing_window("not-a-date", _TODAY) is False

    def test_none_returns_false(self) -> None:
        assert _is_partial_filing_window(None, _TODAY) is False

    def test_unsupported_type_returns_false(self) -> None:
        assert _is_partial_filing_window(12345, _TODAY) is False
