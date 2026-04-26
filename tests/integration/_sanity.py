"""Sanity-assertion helpers for integration tests.

Three reusable assertions that guard the factual shape of wrapper
payloads: numeric bounds, strictly-ascending date ordering within
optional bounds, and multi-symbol integrity. Pure Python, stdlib only,
no network, no provider imports, no input mutation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any


def assert_finite_in_range(
    value: float | int,
    *,
    low: float,
    high: float,
    name: str,
) -> None:
    """Raise AssertionError if `value` is NaN/inf or outside [low, high]."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(
            f"{name}: expected a real number, got {type(value).__name__}={value!r}"
        )
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise AssertionError(f"{name}: expected a finite number, got {value!r}")
    if value < low or value > high:
        raise AssertionError(
            f"{name}: expected value in [{low}, {high}], got {value!r}"
        )


def assert_dates_ascending_in_range(
    records: Iterable[dict[str, Any]],
    *,
    date_key: str,
    start: date | None,
    end: date | None,
) -> None:
    """Raise AssertionError if dates are not strictly ascending or escape bounds."""

    previous: date | None = None
    for index, record in enumerate(records):
        if date_key not in record:
            raise AssertionError(
                f"record[{index}]: missing date key {date_key!r}"
            )
        parsed = _coerce_date(record[date_key], name=f"record[{index}].{date_key}")
        if start is not None and parsed < start:
            raise AssertionError(
                f"record[{index}].{date_key}: {parsed.isoformat()} "
                f"precedes start {start.isoformat()}"
            )
        if end is not None and parsed > end:
            raise AssertionError(
                f"record[{index}].{date_key}: {parsed.isoformat()} "
                f"exceeds end {end.isoformat()}"
            )
        if previous is not None and parsed <= previous:
            raise AssertionError(
                f"record[{index}].{date_key}: {parsed.isoformat()} "
                f"not strictly after previous {previous.isoformat()}"
            )
        previous = parsed


def assert_symbols_present(
    payload: list[dict[str, Any]],
    *,
    expected: Iterable[str],
    symbol_key: str = "symbol",
) -> None:
    """Raise AssertionError if any expected symbol is missing or duplicated."""

    expected_set = set(expected)
    seen: dict[str, int] = {}
    for index, row in enumerate(payload):
        if symbol_key not in row:
            raise AssertionError(
                f"payload[{index}]: missing symbol key {symbol_key!r}"
            )
        symbol = row[symbol_key]
        if symbol in seen:
            raise AssertionError(
                f"payload[{index}].{symbol_key}: duplicate symbol "
                f"{symbol!r} (first seen at index {seen[symbol]})"
            )
        seen[symbol] = index

    missing = expected_set - seen.keys()
    if missing:
        raise AssertionError(
            f"missing expected symbols: {sorted(missing)!r}"
        )


def _coerce_date(value: Any, *, name: str) -> date:
    """Coerce ISO-8601 strings, datetimes, or dates into a `date` value."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise AssertionError(
                f"{name}: unparseable ISO date {value!r}"
            ) from exc
    raise AssertionError(
        f"{name}: expected ISO date string or date, got {type(value).__name__}={value!r}"
    )
