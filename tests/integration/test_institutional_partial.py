"""Integration coverage for `scripts/institutional.py` partial-filing-window
warning surface (`institutional-partial-filing-warning` spec).

Exercises the four observer-visible surfaces added by the feature:

- stderr ``⚠ institutional:`` warning block (and its suppression via
  ``--no-stderr-warn``);
- JSON per-ticker ``partial_filing_window_records[]`` summary;
- ``--format md`` output with ``⚠ `` row-leading marker and
  ``filing window: deadline YYYY-MM-DD`` notes-column entry;
- ``--hide-partial`` masking of current-period numeric fields while
  preserving ``last_*`` prior-period values and the
  ``partial_filing_window`` flag.

FMP happy-paths are skip-gated on ``FMP_API_KEY``; the fatal fall-back
path is exercised with the key deliberately unset.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_DXC_ARGV: list[str] = ["scripts/institutional.py", "DXC", "--provider", "fmp"]


def _require_fmp_key() -> None:
    if not os.environ.get("FMP_API_KEY"):
        pytest.skip("institutional partial-filing-window live-path requires FMP_API_KEY")


def _partial_rows(payload: dict) -> list[dict]:
    """Flatten every partial record across all result rows in payload."""

    rows: list[dict] = []
    data = payload.get("data") or {}
    for outer in data.get("results") or []:
        if not outer.get("ok"):
            continue
        for rec in outer.get("records") or []:
            if rec.get("partial_filing_window") is True:
                rows.append(rec)
    return rows


# ---------------------------------------------------------------------------
# Task 6 case 1 — stderr warning on DXC current-quarter query (and suppression)
# ---------------------------------------------------------------------------


def test_institutional_stderr_warning_block_on_partial() -> None:
    """DXC default-quarter fetch prints a ``⚠ institutional:`` block to
    stderr when a partial-filing-window record is returned.

    Skipped if the upstream run returned no partial row (out-of-window
    calendar; nothing to warn about)."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(_DXC_ARGV, timeout=180)
    assert completed.returncode == 0, (
        f"institutional DXC exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    if not _partial_rows(payload):
        pytest.skip(
            "DXC returned no partial_filing_window=true row under current calendar; "
            "warning path unexercised"
        )
    assert "⚠ institutional:" in completed.stderr, (
        f"expected ⚠ institutional stderr block on partial DXC; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    assert "is in 13F filing window" in completed.stderr, completed.stderr[-2000:]
    assert "materially understated" in completed.stderr, completed.stderr[-2000:]


def test_institutional_no_stderr_warn_suppresses_block() -> None:
    """``--no-stderr-warn`` suppresses the ``⚠ institutional:`` block even
    when partial records are returned."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(
        _DXC_ARGV + ["--no-stderr-warn"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "⚠ institutional:" not in completed.stderr, (
        f"--no-stderr-warn must suppress the ⚠ block; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )


# ---------------------------------------------------------------------------
# Task 6 case 2 — JSON per-ticker partial_filing_window_records[]
# ---------------------------------------------------------------------------


def test_institutional_json_carries_partial_filing_window_records_list() -> None:
    """Every ``ok: True`` row carries ``partial_filing_window_records`` as
    a list (possibly empty). When a partial record exists, each entry
    exposes ISO ``date`` and ``filing_deadline`` keys."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(_DXC_ARGV, timeout=180)
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)

    outer_results = payload.get("data", {}).get("results")
    assert isinstance(outer_results, list) and outer_results, payload

    for outer in outer_results:
        if not outer.get("ok"):
            continue
        summary = outer.get("partial_filing_window_records")
        assert isinstance(summary, list), (
            f"`partial_filing_window_records` must always be a list on ok rows; "
            f"got {summary!r} (outer={outer!r})"
        )
        for entry in summary:
            assert isinstance(entry, dict), entry
            assert isinstance(entry.get("date"), str), entry
            assert isinstance(entry.get("filing_deadline"), str), entry
            assert len(entry["date"]) == 10, entry
            assert len(entry["filing_deadline"]) == 10, entry


# ---------------------------------------------------------------------------
# Task 6 case 3 — --format md with ⚠ + notes column
# ---------------------------------------------------------------------------


def test_institutional_format_md_has_warning_marker_and_deadline_note() -> None:
    """``--format md`` stdout is not valid JSON, contains a ``## DXC``
    heading, and — when a partial record exists — carries at least one
    row starting with ``⚠ `` and a ``filing window: deadline 2026-``
    note."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(
        _DXC_ARGV + ["--format", "md"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    stdout = completed.stdout
    assert stdout.strip(), "markdown stdout must be non-empty"
    with pytest.raises(json.JSONDecodeError):
        json.loads(stdout)
    assert "## DXC" in stdout, stdout[:500]

    # Re-run under JSON to decide whether to assert the partial markers.
    json_completed = run_wrapper_or_xfail(_DXC_ARGV, timeout=180)
    assert json_completed.returncode == 0, json_completed.stderr[-2000:]
    json_payload = assert_stdout_is_single_json(json_completed)
    if not _partial_rows(json_payload):
        pytest.skip(
            "DXC returned no partial_filing_window=true row under current calendar; "
            "⚠/deadline markers unexercised"
        )
    warning_lines = [line for line in stdout.splitlines() if line.startswith("⚠ ")]
    assert warning_lines, (
        f"expected at least one ⚠-prefixed md row on partial DXC; "
        f"stdout head:\n{stdout[:800]}"
    )
    assert "filing window: deadline 2026-" in stdout, stdout[:800]


# ---------------------------------------------------------------------------
# Task 6 case 4 — --hide-partial masks current-period numerics
# ---------------------------------------------------------------------------


def test_institutional_hide_partial_nulls_current_fields_and_preserves_last() -> None:
    """``--hide-partial`` replaces current-period numeric fields with
    ``None`` on partial rows while ``last_ownership_percent`` and the
    ``partial_filing_window`` flag are preserved."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(
        _DXC_ARGV + ["--hide-partial"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)

    partials = _partial_rows(payload)
    if not partials:
        pytest.skip(
            "DXC returned no partial_filing_window=true row under current calendar; "
            "--hide-partial mask unexercised"
        )

    for rec in partials:
        assert rec.get("ownership_percent") is None, rec
        assert rec.get("investors_holding") is None, rec
        assert rec.get("total_invested") is None, rec
        assert rec.get("number_of_13f_shares") is None, rec
        assert rec.get("partial_filing_window") is True, rec
        assert rec.get("last_ownership_percent") is not None, (
            f"last_ownership_percent must be preserved under --hide-partial; "
            f"got {rec!r}"
        )

    data = payload.get("data") or {}
    assert data.get("hide_partial") is True, (
        f"query_meta must echo hide_partial=true; got data={data!r}"
    )


# ---------------------------------------------------------------------------
# Task 6 case 5 — backward compatibility on completed-quarter happy argv
# ---------------------------------------------------------------------------


_EXPECTED_COMPLETED_RECORD_KEYS = frozenset(
    {
        "symbol",
        "cik",
        "date",
        "partial_filing_window",
        "investors_holding",
        "last_investors_holding",
        "investors_holding_change",
        "new_positions",
        "last_new_positions",
        "new_positions_change",
        "increased_positions",
        "last_increased_positions",
        "increased_positions_change",
        "closed_positions",
        "last_closed_positions",
        "closed_positions_change",
        "reduced_positions",
        "last_reduced_positions",
        "reduced_positions_change",
        "total_calls",
        "last_total_calls",
        "total_calls_change",
        "total_puts",
        "last_total_puts",
        "total_puts_change",
        "put_call_ratio",
        "last_put_call_ratio",
        "put_call_ratio_change",
        "number_of_13f_shares",
        "last_number_of_13f_shares",
        "number_of_13f_shares_change",
        "ownership_percent",
        "last_ownership_percent",
        "ownership_percent_change",
        "total_invested",
        "last_total_invested",
        "total_invested_change",
    }
)


def test_institutional_completed_quarter_preserves_legacy_37_field_schema() -> None:
    """A completed quarter (filing window closed >= 1 year ago) still
    emits the legacy 37-field record schema. ``partial_filing_window``
    is the 37th key (already present before this feature); the new
    ``partial_filing_window_records`` sibling lives on the outer row,
    not inside the record dict."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(
        [
            "scripts/institutional.py",
            "AAPL",
            "--provider",
            "fmp",
            "--year",
            "2024",
            "--quarter",
            "4",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    outer_results = payload.get("data", {}).get("results")
    assert isinstance(outer_results, list) and outer_results, payload

    for outer in outer_results:
        if not outer.get("ok"):
            continue
        records = outer.get("records") or []
        assert records, outer
        for rec in records:
            assert set(rec.keys()) == _EXPECTED_COMPLETED_RECORD_KEYS, (
                f"completed-quarter record key set diverges from legacy schema; "
                f"got {sorted(rec.keys())!r}"
            )


# ---------------------------------------------------------------------------
# Envelope top-level warnings[] — agent-facing partial marker
# ---------------------------------------------------------------------------


def test_institutional_envelope_warnings_contains_partial_entry() -> None:
    """When the DXC default-quarter fetch returns a partial record, the
    envelope top-level ``warnings[]`` carries at least one entry with
    ``warning_type == "partial_filing_window"`` so a JSON-only consumer
    sees the signal without having to traverse per-record flags."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(_DXC_ARGV, timeout=180)
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    if not _partial_rows(payload):
        pytest.skip(
            "DXC returned no partial_filing_window=true row under current calendar; "
            "envelope warnings[] path unexercised"
        )
    warnings = payload.get("warnings") or []
    partial_warnings = [
        w for w in warnings if w.get("warning_type") == "partial_filing_window"
    ]
    assert partial_warnings, (
        f"expected at least one partial_filing_window warning in envelope warnings[]; "
        f"got warnings={warnings!r}"
    )
    for entry in partial_warnings:
        assert entry.get("symbol") == "DXC", entry
        assert isinstance(entry.get("date"), str) and len(entry["date"]) == 10, entry
        assert (
            isinstance(entry.get("filing_deadline"), str)
            and len(entry["filing_deadline"]) == 10
        ), entry
        # Non-error signal: must not collide with aggregate_emit's
        # row-failure warning schema.
        assert "error" not in entry, entry
        assert "error_category" not in entry, entry


def test_institutional_envelope_warnings_absent_on_completed_quarter() -> None:
    """A completed quarter (filing window closed >= 1 year ago) must
    emit no ``partial_filing_window`` warnings in ``warnings[]``."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(
        [
            "scripts/institutional.py",
            "AAPL",
            "--provider",
            "fmp",
            "--year",
            "2024",
            "--quarter",
            "4",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    warnings = payload.get("warnings") or []
    assert not any(
        w.get("warning_type") == "partial_filing_window" for w in warnings
    ), f"completed quarter must not emit partial warnings; got warnings={warnings!r}"


def test_institutional_envelope_warnings_independent_of_no_stderr_warn() -> None:
    """``--no-stderr-warn`` suppresses the stderr channel only. The JSON
    envelope ``warnings[]`` surface is independent — consumers that rely
    on JSON alone still see the partial signal."""

    _require_fmp_key()
    completed = run_wrapper_or_xfail(
        _DXC_ARGV + ["--no-stderr-warn"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    if not _partial_rows(payload):
        pytest.skip(
            "DXC returned no partial_filing_window=true row under current calendar; "
            "--no-stderr-warn independence unexercised"
        )
    warnings = payload.get("warnings") or []
    partial_warnings = [
        w for w in warnings if w.get("warning_type") == "partial_filing_window"
    ]
    assert partial_warnings, (
        f"--no-stderr-warn must not suppress envelope warnings[]; "
        f"got warnings={warnings!r}"
    )
    assert "⚠ institutional:" not in completed.stderr, completed.stderr[-2000:]


# ---------------------------------------------------------------------------
# Task 6 case 6 — fatal fall-back on --format md + missing FMP key
# ---------------------------------------------------------------------------


def test_institutional_format_md_falls_back_to_json_on_fatal_credential() -> None:
    """``--format md`` with ``FMP_API_KEY`` unset is an all-rows-fatal
    credential path; stdout falls back to the JSON envelope and exits 2."""

    completed = run_wrapper_or_xfail(
        ["scripts/institutional.py", "AAPL", "--provider", "fmp", "--format", "md"],
        timeout=60,
        env_overrides={"FMP_API_KEY": ""},
    )
    assert completed.returncode == 2, (
        f"expected exit 2 on all-rows-fatal credential; got {completed.returncode}; "
        f"stdout:\n{completed.stdout[:500]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert isinstance(payload, dict), payload
    error = payload.get("error")
    assert isinstance(error, str) and error.startswith("CredentialError:"), (
        f"expected top-level CredentialError envelope on fatal markdown fall-back; "
        f"got error={error!r}"
    )
    assert payload.get("error_category") == "credential", payload
    assert payload.get("tool") == "institutional", payload
