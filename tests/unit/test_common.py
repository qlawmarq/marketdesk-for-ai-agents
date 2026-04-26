"""Unit tests for `scripts/_common.py` shared helpers.

Covers every `_common` helper except the envelope emitters: `safe_call`,
`to_records`, `silence_stdout`, `now_iso`, `wrap`, `emit`, `emit_error`,
`sanitize_for_json`, and `classify_exception`. Envelope-emitter tests
(`aggregate_emit` / `single_emit`) live in `test_common_emit.py`.

pandas is used here as an implicit transitive of OpenBB; if that
transitive ever drops, the `to_records` DataFrame branch test must
be updated to use whatever DataFrame-like object OpenBB returns.

The error-classifier fixtures cover the exception surface originally
catalogued by the 2026-04-23 existing-tools-verification pass ("Exception
surface probe"); that spec's report has since been retired, but the
four-value ``ErrorCategory`` contract it established is locked in here.
"""

from __future__ import annotations

import io
import json
import math
import re
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

from _common import (  # type: ignore[import-not-found]
    CREDENTIAL_PREFIX,
    PLAN_PREFIX,
    ErrorCategory,
    classify_exception,
    emit,
    emit_error,
    safe_call,
    sanitize_for_json,
    silence_stdout,
    to_records,
    wrap,
)

pytestmark = pytest.mark.unit


_ISO_8601_WITH_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})$"
)


# ---------------------------------------------------------------------------
# silence_stdout
# ---------------------------------------------------------------------------


def test_silence_stdout_suppresses_writes_and_restores_on_normal_exit() -> None:
    saved = sys.stdout
    try:
        with silence_stdout():
            inner = sys.stdout
            assert inner is not saved
            assert isinstance(inner, io.StringIO)
            inner.write("noise that must not leak")
        assert sys.stdout is saved
        assert inner.getvalue() == "noise that must not leak"
    finally:
        sys.stdout = saved


def test_silence_stdout_restores_sys_stdout_on_inner_exception() -> None:
    saved = sys.stdout
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with silence_stdout():
                assert sys.stdout is not saved
                raise RuntimeError("boom")
        assert sys.stdout is saved
    finally:
        sys.stdout = saved


# ---------------------------------------------------------------------------
# wrap
# ---------------------------------------------------------------------------


def test_wrap_returns_envelope_with_iso_timestamp_source_data_and_meta() -> None:
    payload = [{"ticker": "AAPL", "price": 1.5}]
    result = wrap(payload, run_id="abc123", provider="yfinance")

    assert result["source"] == "marketdesk-for-ai-agents"
    assert result["data"] is payload
    assert result["run_id"] == "abc123"
    assert result["provider"] == "yfinance"
    assert _ISO_8601_WITH_OFFSET.match(result["collected_at"]), (
        f"collected_at not ISO-8601 with offset: {result['collected_at']!r}"
    )


def test_wrap_without_meta_still_has_envelope_keys() -> None:
    result = wrap({"k": "v"})

    assert set(result) == {"collected_at", "source", "data"}
    assert result["data"] == {"k": "v"}


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------


def test_emit_writes_single_json_document_with_one_trailing_newline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    when = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    payload = {"name": "日本語", "when": when}

    emit(payload)

    captured = capsys.readouterr().out
    assert captured.endswith("\n")
    assert not captured.endswith("\n\n"), "exactly one trailing newline expected"

    body = captured[:-1]
    parsed = json.loads(body)
    assert parsed["name"] == "日本語"
    # `emit` passes `default=str`, so datetime is serialized via `str(dt)`
    assert parsed["when"] == str(when)
    # non-ASCII characters preserved un-escaped on the wire
    assert "日本語" in body
    assert "\\u" not in body


def test_emit_replaces_nan_and_infinity_with_null_on_the_wire(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Req 1.1 / 1.2: emit must not leak bare `NaN` / `Infinity` tokens."""

    emit({"a": float("nan"), "b": [float("inf"), 1.0], "c": float("-inf")})

    captured = capsys.readouterr().out
    assert "NaN" not in captured
    assert "Infinity" not in captured

    parsed = json.loads(captured)
    assert parsed == {"a": None, "b": [None, 1.0], "c": None}


def test_emit_raises_value_error_when_sanitizer_bypassed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 1.3: if a residual NaN survives sanitization the `allow_nan=False`
    defense line must raise `ValueError` rather than silently emit `NaN`.
    """

    from _common import emit as emit_fn  # type: ignore[import-not-found]
    import _common  # type: ignore[import-not-found]

    # Bypass the sanitizer to simulate a future regression in which a
    # NaN float slips past into json.dump.
    monkeypatch.setattr(_common, "sanitize_for_json", lambda value: value)

    with pytest.raises(ValueError):
        emit_fn({"broken": float("nan")})


# ---------------------------------------------------------------------------
# emit_error
# ---------------------------------------------------------------------------


def test_emit_error_returns_two_and_writes_error_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = emit_error("boom happened", request_id="req-1", attempt=3)

    assert rc == 2

    captured = capsys.readouterr().out
    assert captured.endswith("\n")
    parsed = json.loads(captured)
    assert parsed["error"] == "boom happened"
    assert parsed["request_id"] == "req-1"
    assert parsed["attempt"] == 3
    assert _ISO_8601_WITH_OFFSET.match(parsed["collected_at"])


# ---------------------------------------------------------------------------
# safe_call (basic success / failure shape)
# ---------------------------------------------------------------------------


class _Boom(RuntimeError):
    pass


def test_safe_call_wraps_exception_into_error_record() -> None:
    def fn(**_: object) -> None:
        raise _Boom("nope")

    result = safe_call(fn, ticker="AAPL")

    assert result == {
        "ok": False,
        "error": "nope",
        "error_type": "_Boom",
        "error_category": "other",
    }


# ---------------------------------------------------------------------------
# to_records
# ---------------------------------------------------------------------------


class _FakeOBB:
    """Inline stub mimicking the OBBject `.to_df()` surface used by `to_records`."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def to_df(self) -> pd.DataFrame:
        return self._df


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ([], []),
        ([{"a": 1}, {"a": 2}], [{"a": 1}, {"a": 2}]),
        ({"a": 1}, [{"a": 1}]),
        (42, [{"value": 42}]),
    ],
    ids=["none", "empty-list", "list-of-dicts", "single-dict", "scalar"],
)
def test_to_records_basic_branches(value: object, expected: list[dict]) -> None:
    assert to_records(value) == expected


def test_to_records_obbject_to_df_branch_returns_list_of_dicts() -> None:
    df = pd.DataFrame([{"ticker": "AAPL", "price": 1.5}, {"ticker": "MSFT", "price": 2.5}])
    fake = _FakeOBB(df)

    records = to_records(fake)

    assert isinstance(records, list)
    assert all(isinstance(r, dict) for r in records)
    # column values survive the round-trip
    tickers = sorted(r["ticker"] for r in records)
    assert tickers == ["AAPL", "MSFT"]


def test_to_records_drops_default_rangeindex_noise_column() -> None:
    """A default RangeIndex must not leak into records as an `index` row-number column.

    OpenBB endpoints that return a plain tabular payload (estimates,
    fundamentals income/balance/cash/metrics, etc.) end up with a
    nameless RangeIndex on `to_df()`. Calling `reset_index()` would
    inject `{"index": 0, ...}` noise that downstream AI agents could
    mistake for a real field.
    """

    df = pd.DataFrame([{"a": 1}, {"a": 2}, {"a": 3}])
    assert type(df.index).__name__ == "RangeIndex"
    fake = _FakeOBB(df)

    records = to_records(fake)

    assert all("index" not in r for r in records), records
    assert [r["a"] for r in records] == [1, 2, 3]


def test_to_records_preserves_named_index_as_column() -> None:
    """A meaningful index (e.g. date) must still surface as a column."""

    df = pd.DataFrame({"close": [10.0, 11.0]}, index=pd.Index(["2026-01-01", "2026-01-02"], name="date"))
    fake = _FakeOBB(df)

    records = to_records(fake)

    assert [r.get("date") for r in records] == ["2026-01-01", "2026-01-02"]
    assert [r.get("close") for r in records] == [10.0, 11.0]


# ---------------------------------------------------------------------------
# sanitize_for_json
#
# The sanitizer sits in front of `emit` and is the single point that
# converts pandas-origin `NaN` / `+inf` / `-inf` float cells to `None`
# so stdout JSON stays RFC 8259 compliant (Req 1.1 - 1.4).
# ---------------------------------------------------------------------------


def test_sanitize_replaces_top_level_nan_with_none() -> None:
    assert sanitize_for_json({"a": float("nan")}) == {"a": None}


def test_sanitize_replaces_positive_and_negative_infinity_with_none() -> None:
    out = sanitize_for_json({"a": float("inf"), "b": float("-inf")})
    assert out == {"a": None, "b": None}


def test_sanitize_recurses_into_nested_lists_dicts_and_tuples() -> None:
    nan = float("nan")
    inf = float("inf")
    payload = {"a": nan, "b": [nan, 1.0], "c": {"d": inf}, "e": (nan, 2.0)}

    out = sanitize_for_json(payload)

    assert out == {
        "a": None,
        "b": [None, 1.0],
        "c": {"d": None},
        "e": [None, 2.0],
    }


def test_sanitize_preserves_finite_floats_ints_bools_strs_and_none() -> None:
    payload = {
        "f": 1.5,
        "i": 42,
        "b": True,
        "s": "hello",
        "n": None,
        "list": [0, -1.0, False, "x", None],
    }

    assert sanitize_for_json(payload) == payload


def test_sanitize_does_not_mutate_dict_keys_even_when_nan_like() -> None:
    """Only values are walked. Keys are always strings under JSON semantics."""

    payload = {"NaN": float("nan"), "Infinity": 1.0}
    out = sanitize_for_json(payload)
    assert out == {"NaN": None, "Infinity": 1.0}
    assert "NaN" in out
    assert "Infinity" in out


def test_sanitize_returns_new_objects_and_does_not_mutate_input() -> None:
    original = {"xs": [float("nan"), 1.0]}
    snapshot_xs = original["xs"]

    out = sanitize_for_json(original)

    assert out == {"xs": [None, 1.0]}
    # original untouched
    assert original["xs"] is snapshot_xs
    assert math.isnan(original["xs"][0])
    assert original["xs"][1] == 1.0


def test_sanitize_passes_through_non_json_scalars_unchanged() -> None:
    """Exotic objects get stringified downstream by `default=str`. The
    sanitizer only touches NaN/Inf floats; everything else is passed through.
    """

    sentinel = object()
    out = sanitize_for_json({"a": sentinel, "b": 1})
    assert out["a"] is sentinel
    assert out["b"] == 1


def test_sanitize_leaves_error_string_fields_alone() -> None:
    """Req 1.3: the `error` / `error_type` / `error_category` channel is
    string-only; the sanitizer must not touch it even when the payload
    already carries `ok=False` rows.
    """

    row = {
        "ok": False,
        "error": "HTTP 404 Not Found",
        "error_type": "HTTPError",
        "error_category": "other",
        "value": float("nan"),
    }

    out = sanitize_for_json(row)

    assert out == {
        "ok": False,
        "error": "HTTP 404 Not Found",
        "error_type": "HTTPError",
        "error_category": "other",
        "value": None,
    }


def test_sanitize_handles_deeply_nested_structures() -> None:
    depth = 25
    payload: object = float("nan")
    for _ in range(depth):
        payload = [payload]

    out = sanitize_for_json(payload)

    # Walk back down and confirm the single leaf became None.
    cursor = out
    for _ in range(depth):
        assert isinstance(cursor, list) and len(cursor) == 1
        cursor = cursor[0]
    assert cursor is None


# ---------------------------------------------------------------------------
# classify_exception + safe_call credential/plan prefixing
#
# Fixture classes match MRO-name signals used by the classifier. Built
# locally so the tests do not depend on OpenBB being importable.
# ---------------------------------------------------------------------------


class UnauthorizedError(Exception):
    """Stub matching OpenBB's `UnauthorizedError` by class name."""


class OpenBBError(Exception):
    """Stub matching OpenBB's base `OpenBBError` by class name."""


class EmptyDataError(OpenBBError):
    """Stub matching OpenBB's `EmptyDataError` by class name."""


class ValidationError(Exception):
    """Stub matching pydantic's `ValidationError` by class name."""


class ReadTimeout(Exception):
    """Stub matching requests/httpx `ReadTimeout` by class name."""


# --- classify_exception — one fixture per category signal ---


def test_unauthorized_error_without_message_falls_back_to_credential() -> None:
    # Empty-message `UnauthorizedError` has no plan / credential regex
    # signal — exception-class fallback still routes it to CREDENTIAL so
    # the agent never silently consumes an empty-payload 401/402.
    assert classify_exception(UnauthorizedError()) is ErrorCategory.CREDENTIAL


def test_unauthorized_error_with_402_restricted_message_is_plan_insufficient() -> None:
    # FMP raises `UnauthorizedError` for both 401 (missing key) and 402
    # (plan insufficient); only the message distinguishes the two. The
    # classifier must evaluate the plan regex BEFORE the credential
    # exception-class fallback so a 402 does not get absorbed into
    # CREDENTIAL via the `UnauthorizedError` name.
    exc = UnauthorizedError(
        "Unauthorized FMP request -> 402 -> Restricted Endpoint: "
        "This endpoint is not available under your current subscription"
    )
    assert classify_exception(exc) is ErrorCategory.PLAN_INSUFFICIENT


def test_unauthorized_error_with_missing_credential_message_is_credential() -> None:
    # Same exception class as the plan-insufficient case — only the
    # message differs. Confirms message-first dispatch keeps the two
    # branches separable without a provider-specific exception type.
    exc = UnauthorizedError(
        "[Error] -> Missing credential 'fmp_api_key'. "
        "Check https://financialmodelingprep.com to get it."
    )
    assert classify_exception(exc) is ErrorCategory.CREDENTIAL


def test_http_402_in_message_on_generic_exception_is_plan_insufficient() -> None:
    # A generic exception whose only signal is the 402 token routes to
    # PLAN_INSUFFICIENT, not CREDENTIAL: "plan not sufficient" is the
    # recovery path for 402, distinct from "key missing/invalid" for
    # 401/403.
    exc = RuntimeError("upstream returned 402 Restricted Endpoint")
    assert classify_exception(exc) is ErrorCategory.PLAN_INSUFFICIENT


def test_restricted_endpoint_signature_on_non_listed_exception_is_plan_insufficient() -> None:
    # "Restricted Endpoint" / "upgrade your plan" / "subscription
    # required" are plan-tier tokens independent of any HTTP code.
    exc = OpenBBError("Restricted Endpoint: upgrade your plan")
    assert classify_exception(exc) is ErrorCategory.PLAN_INSUFFICIENT


def test_subscription_required_message_is_plan_insufficient() -> None:
    exc = RuntimeError("subscription required to access this endpoint")
    assert classify_exception(exc) is ErrorCategory.PLAN_INSUFFICIENT


def test_http_401_in_message_is_credential() -> None:
    # 401 stays in the credential bucket — the key itself is rejected,
    # distinct from the 402 "plan" recovery path.
    exc = RuntimeError("upstream returned 401 Unauthorized")
    assert classify_exception(exc) is ErrorCategory.CREDENTIAL


def test_http_403_in_message_is_credential() -> None:
    # 403 stays in the credential bucket for the same reason as 401.
    exc = RuntimeError("upstream returned 403 Forbidden")
    assert classify_exception(exc) is ErrorCategory.CREDENTIAL


def test_openbb_missing_credential_message_is_credential() -> None:
    # Spec 03 Task 2.0 probe observation #1–#3: the no-key path raises
    # the base `OpenBBError` with a "Missing credential" message and NO
    # HTTP status code. Without the "Missing credential" token in the
    # regex this fixture would misclassify as OTHER.
    exc = OpenBBError(
        "[Error] -> Missing credential 'fmp_api_key'. Check "
        "https://financialmodelingprep.com to get it."
    )
    assert classify_exception(exc) is ErrorCategory.CREDENTIAL


def test_empty_data_error_without_credential_signature_is_other() -> None:
    # Guard against silent upgrade: `EmptyDataError` with a benign
    # "no data" message has no credential token and must fall through
    # to OTHER. Flipping this to CREDENTIAL would let a legitimately
    # empty response exit non-zero (false positive breaking exit 0
    # contract of Req 1.5).
    exc = EmptyDataError("no data returned for the given period")
    assert classify_exception(exc) is ErrorCategory.OTHER


def test_timeout_error_is_transient() -> None:
    # Builtin TimeoutError is in `_TRANSIENT_EXC_NAMES` directly.
    assert classify_exception(TimeoutError("read timed out")) is ErrorCategory.TRANSIENT


def test_read_timeout_by_class_name_is_transient() -> None:
    # httpx/requests ReadTimeout is matched via MRO class name, not isinstance.
    assert classify_exception(ReadTimeout()) is ErrorCategory.TRANSIENT


def test_http_5xx_in_message_is_transient() -> None:
    # A generic exception with a 5xx in the message — e.g., upstream 502
    # Bad Gateway from a provider CDN — is transient, not credential.
    exc = RuntimeError("upstream returned 502 Bad Gateway")
    assert classify_exception(exc) is ErrorCategory.TRANSIENT


def test_validation_error_is_validation() -> None:
    assert classify_exception(ValidationError("bad payload")) is ErrorCategory.VALIDATION


def test_value_error_is_validation() -> None:
    # Builtin `ValueError` is listed in `_VALIDATION_EXC_NAMES`.
    assert classify_exception(ValueError("bad ticker")) is ErrorCategory.VALIDATION


def test_unknown_runtime_error_is_other() -> None:
    # No class-name match, no message-regex match → must fall through to
    # OTHER. This is the "never silently upgrade" guardrail.
    assert classify_exception(RuntimeError("something weird happened")) is ErrorCategory.OTHER


# --- safe_call — idempotency of the CredentialError: prefix + ok rows intact ---


def test_safe_call_prefixes_credential_errors_once() -> None:
    def fn(**_: object) -> None:
        # 401 is a credential signal (invalid / missing key) — distinct
        # from 402 which is plan-insufficient.
        raise UnauthorizedError("401 Unauthorized request")

    result = safe_call(fn)

    assert result["ok"] is False
    assert result["error_category"] == ErrorCategory.CREDENTIAL.value
    assert result["error"].startswith(CREDENTIAL_PREFIX)
    # Single prefix only — not "CredentialError: CredentialError: ..."
    assert result["error"].count(CREDENTIAL_PREFIX) == 1


def test_safe_call_does_not_double_prefix_when_message_already_prefixed() -> None:
    # If an upstream layer (e.g., a wrapped provider) already emitted a
    # `CredentialError:`-prefixed message, safe_call must be idempotent.
    def fn(**_: object) -> None:
        raise UnauthorizedError(f"{CREDENTIAL_PREFIX} already prefixed message")

    result = safe_call(fn)

    assert result["error_category"] == ErrorCategory.CREDENTIAL.value
    assert result["error"].count(CREDENTIAL_PREFIX) == 1


def test_safe_call_prefixes_plan_insufficient_errors_once() -> None:
    def fn(**_: object) -> None:
        raise UnauthorizedError(
            "Unauthorized FMP request -> 402 -> Restricted Endpoint: "
            "This endpoint is not available under your current subscription"
        )

    result = safe_call(fn)

    assert result["ok"] is False
    assert result["error_category"] == ErrorCategory.PLAN_INSUFFICIENT.value
    assert result["error"].startswith(PLAN_PREFIX)
    # `PlanError:` is a parallel token to `CredentialError:` — the two
    # never both appear on the same record.
    assert not result["error"].startswith(CREDENTIAL_PREFIX)
    assert result["error"].count(PLAN_PREFIX) == 1


def test_safe_call_does_not_double_prefix_plan_message_already_prefixed() -> None:
    def fn(**_: object) -> None:
        raise UnauthorizedError(
            f"{PLAN_PREFIX} 402 Restricted Endpoint: upgrade your plan"
        )

    result = safe_call(fn)

    assert result["error_category"] == ErrorCategory.PLAN_INSUFFICIENT.value
    assert result["error"].count(PLAN_PREFIX) == 1


def test_safe_call_does_not_prefix_non_credential_errors() -> None:
    def fn(**_: object) -> None:
        raise TimeoutError("read timed out")

    result = safe_call(fn)

    assert result["error_category"] == ErrorCategory.TRANSIENT.value
    assert not result["error"].startswith(CREDENTIAL_PREFIX)
    assert result["error"] == "read timed out"


def test_safe_call_leaves_ok_true_rows_untouched() -> None:
    # ok: True rows never carry error_category, so agents that key off
    # the field's absence can still do so.
    def fn(**_: object) -> list[dict[str, object]]:
        return [{"symbol": "AAPL", "price": 1.5}]

    result = safe_call(fn)

    assert result == {
        "ok": True,
        "records": [{"symbol": "AAPL", "price": 1.5}],
    }
    assert "error_category" not in result
    assert "error" not in result
