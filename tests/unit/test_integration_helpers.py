"""Unit tests for the integration-tier helpers.

Protects the Req 5.6 contract: the transient-signature classifier
allowlist must catch genuine network/5xx failures while leaving
contract regressions (tracebacks containing line numbers, byte-count
strings, etc.) to hard-fail. `_has_error_key` is a pure recursive
predicate used by the invalid-ticker case.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import _classify_transient, _has_error_key

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _has_error_key: purity across nested and flat payloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "boom"},
        {"error": ""},
        {"a": 1, "error": "x"},
    ],
    ids=["flat-error-only", "flat-error-empty", "flat-mixed"],
)
def test_has_error_key_true_for_flat_dict_with_error(payload: object) -> None:
    assert _has_error_key(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"nested": {"error": "deep"}}},
        {"records": [{"ok": True}, {"error": "in-list"}]},
        [{"ok": True}, {"meta": {"error": "buried"}}],
        {"a": [[[{"error": "very-deep"}]]]},
    ],
    ids=["nested-dict", "list-in-dict", "list-of-dicts", "deeply-nested"],
)
def test_has_error_key_true_when_error_buried_in_nesting(payload: object) -> None:
    assert _has_error_key(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"data": [], "meta": {"source": "x"}},
        [{"ok": True}, {"value": 42}],
        "error",  # bare string named "error" must not count
        42,
        None,
        {"errors": "plural-key-does-not-count"},
    ],
    ids=[
        "empty-dict",
        "empty-list",
        "nested-no-error",
        "list-of-dicts-no-error",
        "bare-string",
        "bare-int",
        "none",
        "different-key",
    ],
)
def test_has_error_key_false_when_no_error_key_present(payload: object) -> None:
    assert _has_error_key(payload) is False


def test_has_error_key_does_not_mutate_input() -> None:
    payload = {"a": {"b": [{"error": "x"}, {"ok": True}]}}
    snapshot = repr(payload)

    _has_error_key(payload)

    assert repr(payload) == snapshot, "_has_error_key must be a pure predicate"


# ---------------------------------------------------------------------------
# _classify_transient: genuine transient signatures map to xfail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "urllib3.exceptions.ReadTimeoutError: HTTPSConnectionPool(host='query1.finance.yahoo.com', port=443): Read timed out.",
        "ConnectTimeoutError(<urllib3.connection.HTTPSConnection object>, 'Connection to ... timed out.')",
        "HTTPSConnectionPool(host='query1.finance.yahoo.com', port=443): Max retries exceeded",
        "socket.gaierror: [Errno -2] Name or service not known",
        "Temporary failure in name resolution",
        "http.client.RemoteDisconnected: Remote end closed connection without response",
        "requests.exceptions.HTTPError: 503 Server Error: Service Unavailable for url: ...",
        "HTTPSError: 500 something",
        "yfinance error: status code: 502",
        "received status=503 from upstream",
        "got status code = 504 from yahoo",
        "yahoo returned 503 Service Unavailable",
        "upstream replied 502 Bad Gateway",
        "response: 500 Internal Server Error",
        "504 Gateway Timeout while fetching quote",
    ],
    ids=[
        "read-timeout",
        "connect-timeout",
        "https-connection-pool",
        "dns-name-or-service",
        "dns-temp-failure",
        "remote-end-closed",
        "http-error-503",
        "https-error-500",
        "status-code-colon-502",
        "status-equals-503",
        "status-code-equals-504",
        "503-service-unavailable",
        "502-bad-gateway",
        "500-internal-server-error",
        "504-gateway-timeout",
    ],
)
def test_classify_transient_returns_excerpt_for_genuine_transient_signatures(
    stderr: str,
) -> None:
    excerpt = _classify_transient(stderr)
    assert excerpt is not None and excerpt != "", (
        f"expected a transient classification for stderr: {stderr!r}"
    )


# ---------------------------------------------------------------------------
# _classify_transient: contract regressions must NOT classify as xfail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        # Tracebacks whose line numbers happen to be in the 5xx range.
        'Traceback (most recent call last):\n  File "scripts/quote.py", line 523, in <module>\n    main()\nKeyError: \'symbol\'',
        '  File "/usr/lib/python3.12/json/decoder.py", line 512, in raw_decode',
        # Byte-count messages that mention 5xx-shaped numbers.
        "DEBUG: received 500 bytes from upstream",
        "downloaded 502 bytes total",
        "buffer size: 504 bytes consumed",
        # Logic / contract errors with no transient signature.
        "AttributeError: 'NoneType' object has no attribute 'symbol'",
        "KeyError: 'close'",
        "ValueError: invalid literal for int() with base 10",
        # 4xx is not transient (e.g., bad request, auth failure).
        "requests.exceptions.HTTPError: 404 Not Found",
        "got status code: 401 Unauthorized",
        # Empty stderr is not transient.
        "",
        # 3-digit numbers in non-status contexts.
        "processed 500 records in 503 ms",
    ],
    ids=[
        "traceback-line-523",
        "traceback-line-512",
        "received-500-bytes",
        "downloaded-502-bytes",
        "buffer-504-bytes",
        "attribute-error",
        "key-error",
        "value-error",
        "http-404",
        "status-401",
        "empty-stderr",
        "processed-500-records",
    ],
)
def test_classify_transient_returns_none_for_contract_regressions(
    stderr: str,
) -> None:
    assert _classify_transient(stderr) is None, (
        f"contract regression must hard-fail, not xfail; stderr: {stderr!r}"
    )
