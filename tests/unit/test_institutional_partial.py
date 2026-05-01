"""Offline unit coverage for partial-filing-warning helpers in
``scripts/institutional.py``.

Covers Tasks 1.1-1.3 and 2 from
``docs/tasks/todo/institutional-partial-filing-warning/tasks.md``:

- ``_build_partial_summary``: per-ticker `{date, filing_deadline}` aggregation
- ``_MASKED_FIELDS`` / ``_apply_hide_partial``: current-period numeric
  masking on partial=true records, identity pass-through elsewhere
- ``_escape_md_cell``: pipe / newline / None handling (copied from
  ``scripts/insider.py``)
- ``_render_institutional_markdown``: per-ticker md section with `⚠`
  prefix, `notes` column, error / empty branches
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from institutional import (  # type: ignore[import-not-found]
    _MASKED_FIELDS,
    _apply_hide_partial,
    _build_partial_summary,
    _build_partial_warnings,
    _escape_md_cell,
    _is_partial_filing_window,
    _render_institutional_markdown,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Task 1.1 — _build_partial_summary
# ---------------------------------------------------------------------------


class TestBuildPartialSummary:
    def test_all_partial_yields_one_entry_per_record(self) -> None:
        records = [
            {"date": "2026-03-31", "partial_filing_window": True},
            {"date": "2025-12-31", "partial_filing_window": True},
        ]
        result = _build_partial_summary(records)
        assert result == [
            {"date": "2026-03-31", "filing_deadline": "2026-05-15"},
            {"date": "2025-12-31", "filing_deadline": "2026-02-14"},
        ]

    def test_no_partial_yields_empty_list(self) -> None:
        records = [
            {"date": "2024-12-31", "partial_filing_window": False},
            {"date": "2024-09-30", "partial_filing_window": False},
        ]
        assert _build_partial_summary(records) == []

    def test_mixed_keeps_only_partial_entries(self) -> None:
        records = [
            {"date": "2026-03-31", "partial_filing_window": True},
            {"date": "2024-12-31", "partial_filing_window": False},
            {"date": "2025-12-31", "partial_filing_window": True},
        ]
        result = _build_partial_summary(records)
        assert [e["date"] for e in result] == ["2026-03-31", "2025-12-31"]

    def test_empty_records_yields_empty_list(self) -> None:
        assert _build_partial_summary([]) == []

    def test_datetime_date_input_is_normalized_to_iso_string(self) -> None:
        records = [
            {"date": date(2026, 3, 31), "partial_filing_window": True},
            {"date": datetime(2025, 12, 31, 23, 59), "partial_filing_window": True},
        ]
        result = _build_partial_summary(records)
        assert result[0] == {"date": "2026-03-31", "filing_deadline": "2026-05-15"}
        assert result[1] == {"date": "2025-12-31", "filing_deadline": "2026-02-14"}

    def test_invalid_date_is_skipped_fail_open(self) -> None:
        records = [
            {"date": "not-a-date", "partial_filing_window": True},
            {"date": "2026-03-31", "partial_filing_window": True},
        ]
        assert _build_partial_summary(records) == [
            {"date": "2026-03-31", "filing_deadline": "2026-05-15"}
        ]

    def test_input_is_not_mutated(self) -> None:
        records = [{"date": "2026-03-31", "partial_filing_window": True}]
        snapshot = [dict(r) for r in records]
        _build_partial_summary(records)
        assert records == snapshot


# ---------------------------------------------------------------------------
# Task 1.2 — _MASKED_FIELDS / _apply_hide_partial
# ---------------------------------------------------------------------------


def _sample_record(partial: bool) -> dict:
    return {
        "symbol": "DXC",
        "cik": "0000..",
        "date": "2026-03-31",
        "partial_filing_window": partial,
        "investors_holding": 122,
        "investors_holding_change": -293,
        "ownership_percent": 0.032143,
        "ownership_percent_change": -0.868840,
        "number_of_13f_shares": 5696015,
        "number_of_13f_shares_change": -153967209,
        "total_invested": 71214207.0,
        "total_invested_change": -2268272726.0,
        "new_positions": 0,
        "new_positions_change": -10,
        "increased_positions": 0,
        "increased_positions_change": -100,
        "closed_positions": 0,
        "closed_positions_change": -20,
        "reduced_positions": 0,
        "reduced_positions_change": -30,
        "total_calls": 0,
        "total_calls_change": -5,
        "total_puts": 0,
        "total_puts_change": -5,
        "put_call_ratio": 0.0,
        "put_call_ratio_change": -0.1,
        "last_ownership_percent": 0.900983,
        "last_investors_holding": 415,
        "last_total_invested": 2339486933.0,
    }


class TestMaskedFields:
    def test_contains_all_22_current_period_numeric_fields(self) -> None:
        expected = {
            "investors_holding",
            "investors_holding_change",
            "ownership_percent",
            "ownership_percent_change",
            "number_of_13f_shares",
            "number_of_13f_shares_change",
            "total_invested",
            "total_invested_change",
            "new_positions",
            "new_positions_change",
            "increased_positions",
            "increased_positions_change",
            "closed_positions",
            "closed_positions_change",
            "reduced_positions",
            "reduced_positions_change",
            "total_calls",
            "total_calls_change",
            "total_puts",
            "total_puts_change",
            "put_call_ratio",
            "put_call_ratio_change",
        }
        assert _MASKED_FIELDS == expected

    def test_is_frozen(self) -> None:
        assert isinstance(_MASKED_FIELDS, frozenset)


class TestApplyHidePartial:
    def test_partial_record_masks_all_target_fields_to_none(self) -> None:
        records = [_sample_record(partial=True)]
        result = _apply_hide_partial(records)
        for key in _MASKED_FIELDS:
            assert result[0][key] is None, f"{key} should be None"

    def test_partial_record_preserves_meta_and_last_fields(self) -> None:
        records = [_sample_record(partial=True)]
        result = _apply_hide_partial(records)
        assert result[0]["symbol"] == "DXC"
        assert result[0]["cik"] == "0000.."
        assert result[0]["date"] == "2026-03-31"
        assert result[0]["partial_filing_window"] is True
        assert result[0]["last_ownership_percent"] == 0.900983
        assert result[0]["last_investors_holding"] == 415
        assert result[0]["last_total_invested"] == 2339486933.0

    def test_non_partial_record_is_identity_passthrough(self) -> None:
        records = [_sample_record(partial=False)]
        result = _apply_hide_partial(records)
        assert result[0] == _sample_record(partial=False)

    def test_input_list_and_dicts_not_mutated(self) -> None:
        records = [_sample_record(partial=True), _sample_record(partial=False)]
        snapshot = [dict(r) for r in records]
        _apply_hide_partial(records)
        assert records == snapshot

    def test_returns_new_list_object(self) -> None:
        records = [_sample_record(partial=True)]
        result = _apply_hide_partial(records)
        assert result is not records

    def test_empty_records_returns_empty_list(self) -> None:
        assert _apply_hide_partial([]) == []


# ---------------------------------------------------------------------------
# Task 1.3 — _escape_md_cell
# ---------------------------------------------------------------------------


class TestEscapeMdCell:
    def test_none_becomes_empty_string(self) -> None:
        assert _escape_md_cell(None) == ""

    def test_pipe_is_escaped(self) -> None:
        assert _escape_md_cell("a|b") == "a\\|b"

    def test_newline_collapses_to_space(self) -> None:
        assert _escape_md_cell("a\nb") == "a b"

    def test_carriage_return_stripped(self) -> None:
        assert _escape_md_cell("a\r\nb") == "a b"

    def test_integer_passes_through_default_str(self) -> None:
        assert _escape_md_cell(122) == "122"

    def test_float_passes_through_default_str(self) -> None:
        assert _escape_md_cell(0.032143) == "0.032143"


# ---------------------------------------------------------------------------
# Task 2 — _render_institutional_markdown
# ---------------------------------------------------------------------------


_EXPECTED_HEADER = (
    "date | investors_holding | ownership_percent | "
    "number_of_13f_shares | total_invested | put_call_ratio | notes"
)


def _ok_row(records: list[dict]) -> dict:
    return {"symbol": "DXC", "provider": "fmp", "ok": True, "records": records}


class TestRenderInstitutionalMarkdown:
    def test_column_header_order_fixed(self) -> None:
        rendered = _render_institutional_markdown([_ok_row([])], {})
        assert _EXPECTED_HEADER not in rendered  # empty records = no header
        full = _ok_row(
            [
                {
                    "date": "2025-12-31",
                    "partial_filing_window": False,
                    "investors_holding": 415,
                    "ownership_percent": 0.900983,
                    "number_of_13f_shares": 159663224,
                    "total_invested": 2339486933.0,
                    "put_call_ratio": 0.9196,
                }
            ]
        )
        rendered_full = _render_institutional_markdown([full], {})
        assert _EXPECTED_HEADER in rendered_full

    def test_partial_row_has_warning_prefix_and_notes(self) -> None:
        row = _ok_row(
            [
                {
                    "date": "2026-03-31",
                    "partial_filing_window": True,
                    "investors_holding": 122,
                    "ownership_percent": 0.032143,
                    "number_of_13f_shares": 5696015,
                    "total_invested": 71214207.0,
                    "put_call_ratio": 0.0,
                }
            ]
        )
        rendered = _render_institutional_markdown([row], {})
        assert "⚠ 2026-03-31 | 122 | 0.032143 | 5696015 | 71214207.0 | 0.0 | filing window: deadline 2026-05-15" in rendered

    def test_non_partial_row_has_no_prefix_and_empty_notes(self) -> None:
        row = _ok_row(
            [
                {
                    "date": "2025-12-31",
                    "partial_filing_window": False,
                    "investors_holding": 415,
                    "ownership_percent": 0.900983,
                    "number_of_13f_shares": 159663224,
                    "total_invested": 2339486933.0,
                    "put_call_ratio": 0.9196,
                }
            ]
        )
        rendered = _render_institutional_markdown([row], {})
        assert "2025-12-31 | 415 | 0.900983 | 159663224 | 2339486933.0 | 0.9196 | " in rendered
        assert "⚠ 2025-12-31" not in rendered

    def test_error_row_emits_error_category_line_no_table(self) -> None:
        error_row = {
            "symbol": "AAPL",
            "provider": "fmp",
            "ok": False,
            "error": "bad creds",
            "error_category": "credential",
        }
        rendered = _render_institutional_markdown([error_row], {})
        assert "## AAPL" in rendered
        assert "_error_category_: credential — bad creds" in rendered
        assert "date | investors_holding" not in rendered

    def test_empty_records_emits_no_records_marker(self) -> None:
        rendered = _render_institutional_markdown([_ok_row([])], {})
        assert "_no records in quarter_" in rendered

    def test_trailing_newline_appended(self) -> None:
        rendered = _render_institutional_markdown([_ok_row([])], {})
        assert rendered.endswith("\n")

    def test_hide_partial_masked_cells_render_as_empty(self) -> None:
        row = _ok_row(
            [
                {
                    "date": "2026-03-31",
                    "partial_filing_window": True,
                    "investors_holding": None,
                    "ownership_percent": None,
                    "number_of_13f_shares": None,
                    "total_invested": None,
                    "put_call_ratio": None,
                }
            ]
        )
        rendered = _render_institutional_markdown([row], {})
        assert "⚠ 2026-03-31 |  |  |  |  |  | filing window: deadline 2026-05-15" in rendered

    def test_per_ticker_heading_emitted(self) -> None:
        row = _ok_row([])
        rendered = _render_institutional_markdown([row], {})
        assert "## DXC" in rendered


# ---------------------------------------------------------------------------
# Envelope top-level warnings[] — _build_partial_warnings
# ---------------------------------------------------------------------------


class TestBuildPartialWarnings:
    def test_ok_row_with_partials_emits_one_entry_per_partial(self) -> None:
        results = [
            {
                "symbol": "DXC",
                "ok": True,
                "partial_filing_window_records": [
                    {"date": "2026-03-31", "filing_deadline": "2026-05-15"},
                ],
            }
        ]
        assert _build_partial_warnings(results) == [
            {
                "symbol": "DXC",
                "warning_type": "partial_filing_window",
                "date": "2026-03-31",
                "filing_deadline": "2026-05-15",
            }
        ]

    def test_ok_row_with_empty_summary_emits_nothing(self) -> None:
        results = [
            {"symbol": "AAPL", "ok": True, "partial_filing_window_records": []}
        ]
        assert _build_partial_warnings(results) == []

    def test_failure_row_is_skipped(self) -> None:
        results = [
            {"symbol": "ZZZZ", "ok": False, "error": "bad", "error_category": "other"},
            {
                "symbol": "DXC",
                "ok": True,
                "partial_filing_window_records": [
                    {"date": "2026-03-31", "filing_deadline": "2026-05-15"},
                ],
            },
        ]
        warnings = _build_partial_warnings(results)
        assert [w["symbol"] for w in warnings] == ["DXC"]

    def test_multi_ticker_preserves_order(self) -> None:
        results = [
            {
                "symbol": "DXC",
                "ok": True,
                "partial_filing_window_records": [
                    {"date": "2026-03-31", "filing_deadline": "2026-05-15"},
                ],
            },
            {
                "symbol": "AAPL",
                "ok": True,
                "partial_filing_window_records": [
                    {"date": "2026-03-31", "filing_deadline": "2026-05-15"},
                ],
            },
        ]
        warnings = _build_partial_warnings(results)
        assert [w["symbol"] for w in warnings] == ["DXC", "AAPL"]

    def test_warning_type_marker_constant(self) -> None:
        results = [
            {
                "symbol": "DXC",
                "ok": True,
                "partial_filing_window_records": [
                    {"date": "2026-03-31", "filing_deadline": "2026-05-15"},
                ],
            }
        ]
        warning = _build_partial_warnings(results)[0]
        assert warning["warning_type"] == "partial_filing_window"
        # Non-error signal: must not carry `error` / `error_category` to
        # avoid colliding with aggregate_emit's row-failure warnings.
        assert "error" not in warning
        assert "error_category" not in warning


# ---------------------------------------------------------------------------
# Task 5 — _is_partial_filing_window boundary values
# ---------------------------------------------------------------------------


_TODAY = date(2026, 4, 25)


class TestIsPartialFilingWindowBoundary:
    def test_exactly_45_days_old_is_false(self) -> None:
        # deadline == today; the check is strictly `>`, so not partial.
        assert _is_partial_filing_window(_TODAY - timedelta(days=45), _TODAY) is False

    def test_44_days_old_is_true(self) -> None:
        assert _is_partial_filing_window(_TODAY - timedelta(days=44), _TODAY) is True

    def test_46_days_old_is_false(self) -> None:
        assert _is_partial_filing_window(_TODAY - timedelta(days=46), _TODAY) is False

    def test_today_is_true(self) -> None:
        assert _is_partial_filing_window(_TODAY, _TODAY) is True

    def test_non_str_non_date_input_is_false(self) -> None:
        assert _is_partial_filing_window(12345, _TODAY) is False
        assert _is_partial_filing_window(None, _TODAY) is False
        assert _is_partial_filing_window([], _TODAY) is False

    def test_malformed_iso_string_is_false(self) -> None:
        assert _is_partial_filing_window("not-a-date", _TODAY) is False
        assert _is_partial_filing_window("2026-13-01", _TODAY) is False
