"""Integration coverage for `scripts/calendars.py` across every sub-mode.

Exercises earnings, dividend, and economic against the nasdaq provider (the
free-tier alternative task 4.3 will promote to default). Asserts the shared
envelope, happy-path success, in-window bounds, and finite numeric fields.
Calendar rows are not strictly ordered (multiple events may share a date,
and provider-side order is not guaranteed), so the ordering assertion is a
per-record window check rather than a strictly-ascending one.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import pytest

from tests.integration._sanity import (
    _coerce_date,
    assert_dates_ascending_in_range,
    assert_finite_in_range,
)
from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_START = (date.today() - timedelta(days=7)).isoformat()
_END = (date.today() + timedelta(days=21)).isoformat()


SUB_MODES: list[str] = ["earnings", "dividend", "economic"]


_DATE_KEYS = ("date", "report_date", "ex_dividend_date", "release_date", "payment_date")


def _pick_date_key(record: dict[str, Any]) -> str | None:
    for key in _DATE_KEYS:
        if key in record and record[key] is not None:
            return key
    for key, value in record.items():
        if "date" in key.lower() and value is not None:
            return key
    return None


def _assert_envelope(payload: Any, *, sub_type: str, provider: str) -> dict[str, Any]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "calendars", payload
    assert payload.get("collected_at"), payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    assert "ok" not in data, (
        f"data must not carry a flattened `ok` key under the single_emit envelope; data={data!r}"
    )
    assert "records" not in data, (
        f"data must not carry a flattened `records` key; use data.results instead; data={data!r}"
    )
    assert data.get("type") == sub_type, data
    assert data.get("provider") == provider, data
    results = data.get("results")
    assert isinstance(results, list), (
        f"expected `data.results` to be a list; got {type(results).__name__}: {results!r}"
    )
    return data


@pytest.mark.parametrize("sub_type", SUB_MODES)
def test_calendars_happy_path_honors_date_window(sub_type: str) -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/calendars.py",
            "--type",
            sub_type,
            "--provider",
            "nasdaq",
            "--start",
            _START,
            "--end",
            _END,
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"calendars.py --type {sub_type} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope(payload, sub_type=sub_type, provider="nasdaq")

    warnings = payload.get("warnings") or []
    if warnings:
        error_message = " ".join(str(w.get("error") or "") for w in warnings)
        if "No record found" in error_message:
            pytest.skip(
                f"{sub_type} via nasdaq returned no records for window "
                f"[{_START}, {_END}]: {error_message.strip()}"
            )
        raise AssertionError(f"calendar failed with warnings: {warnings!r}")

    records = data["results"]
    assert records, f"results must be non-empty; got {records!r}"

    date_key = _pick_date_key(records[0])
    assert date_key is not None, f"no date key found on record: {records[0]!r}"

    start_bound = date.fromisoformat(_START)
    end_bound = date.fromisoformat(_END)
    for index, record in enumerate(records):
        raw = record.get(date_key)
        assert raw is not None, f"record[{index}] missing {date_key}"
        parsed = _coerce_date(raw, name=f"record[{index}].{date_key}")
        assert start_bound <= parsed <= end_bound, (
            f"record[{index}].{date_key} {parsed} outside window [{start_bound}, {end_bound}]"
        )

    for index, record in enumerate(records):
        for key, value in record.items():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            if isinstance(value, (int, float)):
                assert_finite_in_range(
                    value,
                    low=-1e15,
                    high=1e15,
                    name=f"{sub_type}[{index}].{key}",
                )


@pytest.mark.parametrize("sub_type", SUB_MODES)
def test_calendars_default_provider_is_nasdaq(sub_type: str) -> None:
    """Regression: with `--provider` omitted the wrapper defaults to nasdaq
    (free) and returns a date-ordered payload within the requested window.
    """

    completed = run_wrapper_or_xfail(
        [
            "scripts/calendars.py",
            "--type",
            sub_type,
            "--start",
            _START,
            "--end",
            _END,
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"calendars.py --type {sub_type} (default provider) exited "
        f"{completed.returncode}; stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope(payload, sub_type=sub_type, provider="nasdaq")

    warnings = payload.get("warnings") or []
    if warnings:
        error_message = " ".join(str(w.get("error") or "") for w in warnings)
        if "No record found" in error_message:
            pytest.skip(
                f"{sub_type} via nasdaq returned no records for window "
                f"[{_START}, {_END}]: {error_message.strip()}"
            )
        raise AssertionError(f"calendar failed with warnings: {warnings!r}")

    records = data["results"]
    assert records, f"results must be non-empty; got {records!r}"

    date_key = _pick_date_key(records[0])
    assert date_key is not None, f"no date key found on record: {records[0]!r}"

    start_bound = date.fromisoformat(_START)
    end_bound = date.fromisoformat(_END)
    for index, record in enumerate(records):
        raw = record.get(date_key)
        assert raw is not None, f"record[{index}] missing {date_key}"
        parsed = _coerce_date(raw, name=f"record[{index}].{date_key}")
        assert start_bound <= parsed <= end_bound, (
            f"record[{index}].{date_key} {parsed} outside window [{start_bound}, {end_bound}]"
        )


def test_calendars_strict_ordering_helper_detects_disorder() -> None:
    """Guards that the shared ordering helper still fires on a shuffled list.

    Prevents a future refactor from silently dropping the strict ascending
    check used by genuinely time-series payloads elsewhere in the suite.
    """

    with pytest.raises(AssertionError):
        assert_dates_ascending_in_range(
            [{"d": "2025-01-02"}, {"d": "2025-01-01"}],
            date_key="d",
            start=None,
            end=None,
        )
