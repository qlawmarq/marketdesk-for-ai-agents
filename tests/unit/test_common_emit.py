"""Unit tests for `_common.aggregate_emit` and `_common.single_emit`.

Both emitters share the envelope root allowlist, the `query_meta`
collision gate, the `extra_warnings` merge order, and the exit-code
decision matrix for credential / plan-insufficient fatal paths. Covering
them in the same file keeps the two shapes visible side-by-side.

Stdout is captured via `capsys`; no subprocess is spawned.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from _common import (  # type: ignore[import-not-found]
    CREDENTIAL_PREFIX,
    PLAN_PREFIX,
    ErrorCategory,
    aggregate_emit,
    is_fatal_aggregate,
    single_emit,
)

pytestmark = pytest.mark.unit


def _read_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr().out
    assert captured.endswith("\n")
    return json.loads(captured)


# ---------------------------------------------------------------------------
# aggregate_emit
# ---------------------------------------------------------------------------


def _ok(symbol: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"symbol": symbol, "ok": True, "records": records or []}


def _failed(
    symbol: str,
    *,
    category: ErrorCategory,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "ok": False,
        "error": error if error is not None else f"{category.value} failure for {symbol}",
        "error_type": "StubError",
        "error_category": category.value,
    }


# --- (i) empty rows → exit 0, no warnings key ---


def test_empty_rows_exits_zero_with_no_warnings_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = aggregate_emit(
        [], tool="etf", query_meta={"provider": "fmp", "type": "holdings"}
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["tool"] == "etf"
    assert envelope["data"]["results"] == []
    assert "warnings" not in envelope
    assert "warnings" not in envelope["data"]


# --- (ii) all ok → exit 0, no warnings key ---


def test_all_success_exits_zero_with_no_warnings_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [_ok("AAPL"), _ok("MSFT")]

    rc = aggregate_emit(
        rows,
        tool="fundamentals",
        query_meta={"type": "ratios", "provider": "fmp"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["data"]["results"] == rows
    assert "warnings" not in envelope
    assert "warnings" not in envelope["data"]


# --- (iii) all rows failed, mixed categories incl. one credential
#          → exit 0 + warnings per failed row, each tagged with category
#          (exit 2 branch requires EVERY failure to be credential) ---


def test_all_failed_mixed_categories_stays_exit_zero_with_per_row_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _failed("AAA", category=ErrorCategory.CREDENTIAL),
        _failed("BBB", category=ErrorCategory.TRANSIENT),
        _failed("CCC", category=ErrorCategory.VALIDATION),
    ]

    rc = aggregate_emit(rows, tool="etf", query_meta={"provider": "fmp"})

    assert rc == 0
    envelope = _read_json(capsys)
    assert "error" not in envelope  # not the emit_error path
    warnings = envelope["warnings"]
    assert "warnings" not in envelope["data"]
    assert [w["symbol"] for w in warnings] == ["AAA", "BBB", "CCC"]
    assert [w["error_category"] for w in warnings] == [
        ErrorCategory.CREDENTIAL.value,
        ErrorCategory.TRANSIENT.value,
        ErrorCategory.VALIDATION.value,
    ]
    assert len(warnings) == len([r for r in rows if not r["ok"]])


# --- (iv) all rows failed AND all CREDENTIAL → exit 2 + top-level error ---


def test_all_rows_credential_failed_exits_two_with_credential_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _failed("AAA", category=ErrorCategory.CREDENTIAL, error="CredentialError: 401 unauthorized"),
        _failed("BBB", category=ErrorCategory.CREDENTIAL, error="CredentialError: 401 unauthorized"),
    ]

    rc = aggregate_emit(rows, tool="etf", query_meta={"provider": "fmp"})

    assert rc == 2
    envelope = _read_json(capsys)
    assert envelope["error"].startswith(CREDENTIAL_PREFIX)
    assert envelope["error_category"] == ErrorCategory.CREDENTIAL.value
    assert envelope["tool"] == "etf"
    assert envelope["details"] == [r["error"] for r in rows]
    # emit_error path does not emit the data/results envelope
    assert "data" not in envelope


def test_all_rows_plan_insufficient_failed_exits_two_with_plan_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _failed(
            "AAA",
            category=ErrorCategory.PLAN_INSUFFICIENT,
            error="PlanError: 402 Restricted Endpoint",
        ),
        _failed(
            "BBB",
            category=ErrorCategory.PLAN_INSUFFICIENT,
            error="PlanError: 402 Restricted Endpoint",
        ),
    ]

    rc = aggregate_emit(rows, tool="etf", query_meta={"provider": "fmp"})

    assert rc == 2
    envelope = _read_json(capsys)
    assert envelope["error"].startswith(PLAN_PREFIX)
    # The two fatal prefixes are parallel, never both set on one envelope.
    assert not envelope["error"].startswith(CREDENTIAL_PREFIX)
    assert envelope["error_category"] == ErrorCategory.PLAN_INSUFFICIENT.value
    assert envelope["tool"] == "etf"
    assert envelope["details"] == [r["error"] for r in rows]
    assert "data" not in envelope


def test_mixed_credential_and_plan_insufficient_stays_exit_zero_with_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Mixed fatal categories (no single category covers every row) must
    # not promote to exit 2 — the top-level prefix would need to pick a
    # side and bury the other. Fall back to the warnings channel so both
    # diagnoses survive.
    rows = [
        _failed("AAA", category=ErrorCategory.CREDENTIAL, error="CredentialError: 401"),
        _failed("BBB", category=ErrorCategory.PLAN_INSUFFICIENT, error="PlanError: 402"),
    ]

    rc = aggregate_emit(rows, tool="etf", query_meta={"provider": "fmp"})

    assert rc == 0
    envelope = _read_json(capsys)
    assert "error" not in envelope
    categories = [w["error_category"] for w in envelope["warnings"]]
    assert categories == [
        ErrorCategory.CREDENTIAL.value,
        ErrorCategory.PLAN_INSUFFICIENT.value,
    ]


# --- (v) mixed success + credential failure → exit 0 + warnings entry tagged credential ---


def test_mixed_success_and_credential_failure_surfaces_credential_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _ok("AAPL"),
        _failed("ZZZZ", category=ErrorCategory.CREDENTIAL, error="CredentialError: 402"),
    ]

    rc = aggregate_emit(
        rows,
        tool="fundamentals",
        query_meta={"type": "ratios", "provider": "fmp"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    warnings = envelope["warnings"]
    assert "warnings" not in envelope["data"]
    assert warnings == [
        {
            "symbol": "ZZZZ",
            "error": "CredentialError: 402",
            "error_category": ErrorCategory.CREDENTIAL.value,
        }
    ]
    assert len(warnings) == len([r for r in rows if not r["ok"]])


# --- (vi) mixed success + transient failure → exit 0 + warnings entry tagged transient ---


def test_mixed_success_and_transient_failure_surfaces_transient_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _ok("AAPL"),
        _failed("BBBB", category=ErrorCategory.TRANSIENT, error="upstream 503"),
    ]

    rc = aggregate_emit(
        rows,
        tool="fundamentals",
        query_meta={"type": "ratios", "provider": "fmp"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    warnings = envelope["warnings"]
    assert "warnings" not in envelope["data"]
    assert warnings == [
        {
            "symbol": "BBBB",
            "error": "upstream 503",
            "error_category": ErrorCategory.TRANSIENT.value,
        }
    ]
    assert len(warnings) == len([r for r in rows if not r["ok"]])


# --- (vii) all rows failed, none CREDENTIAL → exit 0 + warnings (NOT exit 2) ---


def test_all_rows_failed_no_credential_stays_exit_zero_with_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _failed("AAA", category=ErrorCategory.TRANSIENT),
        _failed("BBB", category=ErrorCategory.TRANSIENT),
    ]

    rc = aggregate_emit(rows, tool="etf", query_meta={"provider": "fmp"})

    assert rc == 0
    envelope = _read_json(capsys)
    assert "error" not in envelope
    warnings = envelope["warnings"]
    assert "warnings" not in envelope["data"]
    assert [w["symbol"] for w in warnings] == ["AAA", "BBB"]
    assert all(w["error_category"] == ErrorCategory.TRANSIENT.value for w in warnings)
    assert len(warnings) == len([r for r in rows if not r["ok"]])


# --- (viii) extra_warnings only (no row failures) → exit 0 + warnings == extras ---


def test_extra_warnings_only_passes_through_verbatim(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [_ok("AAPL")]
    extras = [
        {
            "field": "net_profit_margin",
            "symbol": "AAPL",
            "value": 12.3,
            "reason": "|value| exceeds DECIMAL_SANITY_MAX",
        }
    ]

    rc = aggregate_emit(
        rows,
        tool="fundamentals",
        query_meta={"type": "ratios", "provider": "fmp"},
        extra_warnings=extras,
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["warnings"] == extras
    assert "warnings" not in envelope["data"]


# --- (ix) extra_warnings merged after row warnings, order preserved ---


def test_row_warnings_then_extra_warnings_merged_in_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        _ok("AAPL"),
        _failed("ZZZZ", category=ErrorCategory.CREDENTIAL, error="CredentialError: 402"),
    ]
    extras = [
        {
            "field": "net_profit_margin",
            "symbol": "AAPL",
            "value": 7.5,
            "reason": "|value| exceeds DECIMAL_SANITY_MAX",
        }
    ]

    rc = aggregate_emit(
        rows,
        tool="fundamentals",
        query_meta={"type": "ratios", "provider": "fmp"},
        extra_warnings=extras,
    )

    assert rc == 0
    envelope = _read_json(capsys)
    warnings = envelope["warnings"]
    assert "warnings" not in envelope["data"]
    assert warnings[0] == {
        "symbol": "ZZZZ",
        "error": "CredentialError: 402",
        "error_category": ErrorCategory.CREDENTIAL.value,
    }
    assert warnings[1] == extras[0]
    assert len(warnings) == len([r for r in rows if not r["ok"]]) + len(extras)


# --- (x) envelope root allowlist and query_meta collision gate ---


def test_envelope_root_stays_within_allowlist_and_query_meta_lives_under_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = aggregate_emit(
        [_ok("AAPL")],
        tool="quote",
        query_meta={"provider": "fmp", "type": "equity"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert set(envelope.keys()) == {"source", "collected_at", "tool", "data"}
    assert envelope["data"]["provider"] == "fmp"
    assert envelope["data"]["type"] == "equity"


@pytest.mark.parametrize(
    "collision_key",
    ["source", "collected_at", "tool", "results"],
)
def test_query_meta_rejects_reserved_envelope_and_data_keys(
    collision_key: str,
) -> None:
    # `aggregate_emit` and `single_emit` share `_normalize_query_meta`; one
    # emitter suffices to pin the collision gate.
    with pytest.raises(ValueError, match="query_meta keys collide"):
        aggregate_emit(
            [_ok("AAPL")],
            tool="quote",
            query_meta={collision_key: "clobber"},
        )


# ---------------------------------------------------------------------------
# single_emit
#
# Covers the three exit-code / envelope branches of the single-query
# emitter used by `calendars` / `macro_survey`: success (records under
# `data.results`), credential failure (top-level `CredentialError:` +
# exit 2), and non-credential failure (`data.results=[]` + top-level
# `warnings` entry + exit 0).
# ---------------------------------------------------------------------------


# --- (i) success branch → data.results holds records, query_meta sits alongside ---


def test_success_places_records_in_data_results_with_query_meta_sibling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    records = [{"date": "2026-04-23", "value": 1.2}, {"date": "2026-04-24", "value": 1.3}]
    call_result = {"ok": True, "records": records}

    rc = single_emit(
        call_result,
        tool="calendars",
        query_meta={"type": "economic", "provider": "fmp", "start": "2026-04-23"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["tool"] == "calendars"
    assert envelope["data"]["results"] == records
    assert envelope["data"]["type"] == "economic"
    assert envelope["data"]["provider"] == "fmp"
    assert envelope["data"]["start"] == "2026-04-23"
    assert "warnings" not in envelope
    assert "error" not in envelope


def test_success_with_empty_records_still_has_results_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = single_emit(
        {"ok": True, "records": []},
        tool="macro_survey",
        query_meta={"series": "GDP", "provider": "fred"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["data"]["results"] == []
    assert envelope["data"]["series"] == "GDP"


# --- (ii) credential failure → emit_error path, exit 2, CredentialError prefix ---


def test_credential_failure_emits_error_with_prefix_and_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_result = {
        "ok": False,
        "error": "CredentialError: 402 restricted endpoint",
        "error_type": "UnauthorizedError",
        "error_category": ErrorCategory.CREDENTIAL.value,
    }

    rc = single_emit(
        call_result,
        tool="calendars",
        query_meta={"type": "economic"},
    )

    assert rc == 2
    envelope = _read_json(capsys)
    assert envelope["error"].startswith(CREDENTIAL_PREFIX)
    assert envelope["tool"] == "calendars"
    # emit_error path does not carry data/results; single_emit mirrors aggregate_emit
    assert "data" not in envelope


def test_credential_failure_without_prefix_gets_prefix_added(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_result = {
        "ok": False,
        "error": "missing api key",
        "error_type": "UnauthorizedError",
        "error_category": ErrorCategory.CREDENTIAL.value,
    }

    rc = single_emit(call_result, tool="macro_survey")

    assert rc == 2
    envelope = _read_json(capsys)
    assert envelope["error"].startswith(CREDENTIAL_PREFIX)
    assert "missing api key" in envelope["error"]


def test_plan_insufficient_failure_emits_error_with_plan_prefix_and_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_result = {
        "ok": False,
        "error": "PlanError: 402 Restricted Endpoint",
        "error_type": "UnauthorizedError",
        "error_category": ErrorCategory.PLAN_INSUFFICIENT.value,
    }

    rc = single_emit(
        call_result,
        tool="calendars",
        query_meta={"type": "economic"},
    )

    assert rc == 2
    envelope = _read_json(capsys)
    assert envelope["error"].startswith(PLAN_PREFIX)
    assert not envelope["error"].startswith(CREDENTIAL_PREFIX)
    assert envelope["error_category"] == ErrorCategory.PLAN_INSUFFICIENT.value
    assert envelope["tool"] == "calendars"
    assert "data" not in envelope


def test_plan_insufficient_failure_without_prefix_gets_prefix_added(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_result = {
        "ok": False,
        "error": "402 restricted",
        "error_type": "UnauthorizedError",
        "error_category": ErrorCategory.PLAN_INSUFFICIENT.value,
    }

    rc = single_emit(call_result, tool="macro_survey")

    assert rc == 2
    envelope = _read_json(capsys)
    assert envelope["error"].startswith(PLAN_PREFIX)
    assert "402 restricted" in envelope["error"]


# --- (iii) non-credential failure → empty results + top-level warning, exit 0 ---


def test_transient_failure_emits_empty_results_and_top_level_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_result = {
        "ok": False,
        "error": "upstream 503",
        "error_type": "TimeoutError",
        "error_category": ErrorCategory.TRANSIENT.value,
    }

    rc = single_emit(
        call_result,
        tool="macro_survey",
        query_meta={"series": "GDP", "provider": "fred"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["data"]["results"] == []
    assert envelope["data"]["series"] == "GDP"
    assert envelope["warnings"] == [
        {
            "symbol": None,
            "error": "upstream 503",
            "error_category": ErrorCategory.TRANSIENT.value,
        }
    ]
    assert "error" not in envelope


def test_validation_failure_is_non_credential_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    call_result = {
        "ok": False,
        "error": "unknown series code",
        "error_type": "KeyError",
        "error_category": ErrorCategory.VALIDATION.value,
    }

    rc = single_emit(call_result, tool="macro_survey", query_meta={"series": "XXX"})

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["data"]["results"] == []
    assert envelope["warnings"][0]["error_category"] == ErrorCategory.VALIDATION.value


# --- (iv) extra_warnings merging semantics ---


def test_success_with_extra_warnings_surfaces_them_at_envelope_top_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    extras = [{"note": "partial fed minutes missing", "error_category": "other"}]
    rc = single_emit(
        {"ok": True, "records": [{"series": "GDP", "value": 1.0}]},
        tool="macro_survey",
        query_meta={"series": "GDP"},
        extra_warnings=extras,
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert envelope["warnings"] == extras


def test_failure_extras_are_appended_after_failure_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    extras = [{"note": "second pass skipped", "error_category": "other"}]
    rc = single_emit(
        {
            "ok": False,
            "error": "upstream 503",
            "error_type": "TimeoutError",
            "error_category": ErrorCategory.TRANSIENT.value,
        },
        tool="macro_survey",
        extra_warnings=extras,
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert len(envelope["warnings"]) == 2
    assert envelope["warnings"][0]["error_category"] == ErrorCategory.TRANSIENT.value
    assert envelope["warnings"][1] == extras[0]


# --- (v) results key invariant — always present, always a list ---


@pytest.mark.parametrize(
    "call_result",
    [
        {"ok": True, "records": []},
        {
            "ok": False,
            "error": "no data",
            "error_type": "KeyError",
            "error_category": ErrorCategory.VALIDATION.value,
        },
        {
            "ok": False,
            "error": "network blip",
            "error_type": "ConnectionError",
            "error_category": ErrorCategory.TRANSIENT.value,
        },
    ],
    ids=["success-empty", "validation-failure", "transient-failure"],
)
def test_data_results_key_always_present_except_credential_path(
    capsys: pytest.CaptureFixture[str],
    call_result: dict[str, Any],
) -> None:
    rc = single_emit(call_result, tool="calendars", query_meta={"type": "economic"})

    assert rc == 0
    envelope = _read_json(capsys)
    assert "data" in envelope
    assert "results" in envelope["data"]
    assert isinstance(envelope["data"]["results"], list)


# --- (vi) envelope root allowlist and query_meta collision gate ---


def test_envelope_root_stays_within_allowlist(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = single_emit(
        {"ok": True, "records": []},
        tool="macro_survey",
        query_meta={"series": "GDP", "provider": "fred"},
    )

    assert rc == 0
    envelope = _read_json(capsys)
    assert set(envelope.keys()) == {"source", "collected_at", "tool", "data"}


# ---------------------------------------------------------------------------
# is_fatal_aggregate — public peek at the fatal-exit gate (Req 5.8)
# ---------------------------------------------------------------------------


def test_is_fatal_aggregate_empty_rows_returns_none() -> None:
    assert is_fatal_aggregate([]) is None


def test_is_fatal_aggregate_all_success_returns_none() -> None:
    rows = [_ok("AAPL"), _ok("MSFT")]
    assert is_fatal_aggregate(rows) is None


def test_is_fatal_aggregate_mixed_success_and_credential_returns_none() -> None:
    rows = [_ok("AAPL"), _failed("MSFT", category=ErrorCategory.CREDENTIAL)]
    assert is_fatal_aggregate(rows) is None


def test_is_fatal_aggregate_all_credential_returns_credential_category() -> None:
    rows = [
        _failed("AAPL", category=ErrorCategory.CREDENTIAL),
        _failed("MSFT", category=ErrorCategory.CREDENTIAL),
    ]
    assert is_fatal_aggregate(rows) is ErrorCategory.CREDENTIAL


def test_is_fatal_aggregate_all_plan_insufficient_returns_plan_category() -> None:
    rows = [
        _failed("AAPL", category=ErrorCategory.PLAN_INSUFFICIENT),
        _failed("MSFT", category=ErrorCategory.PLAN_INSUFFICIENT),
    ]
    assert is_fatal_aggregate(rows) is ErrorCategory.PLAN_INSUFFICIENT


def test_is_fatal_aggregate_mixed_credential_and_plan_returns_none() -> None:
    rows = [
        _failed("AAPL", category=ErrorCategory.CREDENTIAL),
        _failed("MSFT", category=ErrorCategory.PLAN_INSUFFICIENT),
    ]
    assert is_fatal_aggregate(rows) is None
