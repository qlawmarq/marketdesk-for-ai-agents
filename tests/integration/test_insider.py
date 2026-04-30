"""Integration coverage for `scripts/insider.py` new behavior.

Exercises the insider wrapper on the keyless SEC default and the FMP
opt-in path. Asserts: shared envelope shape, multi-symbol fan-out,
client-side day window, transaction-code filter (echo + dropped count),
argparse-side validation rejections, normalized 19-field record schema
(provider-invariant), markdown emission, and the markdown→JSON
fall-back on the all-rows-fatal credential gate.

Cross-provider schema-consistency and FMP cases are skip-gated on
`FMP_API_KEY` per the per-wrapper convention so the default integration
run stays green when the paid key is absent.
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


_CANONICAL_RECORD_KEYS = frozenset(
    {
        "filing_date",
        "transaction_date",
        "reporter_name",
        "reporter_title",
        "transaction_code",
        "transaction_code_label",
        "transaction_type_raw",
        "acquisition_or_disposition",
        "shares",
        "price",
        "total_value",
        "shares_after",
        "form_type",
        "url",
        "ownership_type",
        "security_type",
        "company_cik",
        "owner_cik",
        "footnote",
    }
)


def _assert_envelope_shape(payload: object) -> dict[str, object]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "insider", payload
    assert payload.get("collected_at"), payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    results = data.get("results")
    assert isinstance(results, list), (
        f"expected `data.results` to be a list; got {type(results).__name__}"
    )
    return data


# ---------------------------------------------------------------------------
# Task 8.1 — SEC happy-paths and validation rejections
# ---------------------------------------------------------------------------


def test_insider_sec_multi_symbol_happy_path() -> None:
    """Multi-symbol SEC fetch returns a populated envelope; echo defaults
    are present; rows that succeed carry `dropped_unparseable_codes: 0`
    when no filter is active.
    """

    completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "MSFT", "--provider", "sec", "--days", "90"],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"insider.py multi-symbol SEC exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope_shape(payload)

    assert data.get("provider") == "sec", data
    assert data.get("days") == 90, data
    assert data.get("transaction_codes") is None, (
        f"transaction_codes echo must be null when filter is omitted; got {data.get('transaction_codes')!r}"
    )

    results = data["results"]
    assert isinstance(results, list) and len(results) == 2, results
    seen = {row["symbol"] for row in results}
    assert seen == {"AAPL", "MSFT"}, seen

    populated = 0
    for row in results:
        assert row.get("provider") == "sec", row
        if row.get("ok"):
            assert "records" in row, row
            assert isinstance(row["records"], list), row
            assert row.get("dropped_unparseable_codes") == 0, (
                f"dropped_unparseable_codes must be 0 when filter is inactive; row={row!r}"
            )
            if row["records"]:
                populated += 1
                first = row["records"][0]
                assert set(first.keys()) == _CANONICAL_RECORD_KEYS, (
                    f"record key set diverges from canonical 19-field surface; "
                    f"got {sorted(first.keys())!r}"
                )
    assert populated >= 1, (
        "expected at least one SEC symbol with insider activity in last 90 days; "
        f"results={results!r}"
    )


def test_insider_sec_transaction_code_filter() -> None:
    """A `--transaction-codes P,S` filter on a ticker known to have insider
    activity returns a non-empty subset whose normalized codes are exactly
    the requested letters; the echo lists the uppercased codes.
    """

    completed = run_wrapper_or_xfail(
        [
            "scripts/insider.py",
            "AAPL",
            "--provider",
            "sec",
            "--days",
            "180",
            "--transaction-codes",
            "p,s",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"insider.py code-filter exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope_shape(payload)

    assert data.get("transaction_codes") == ["P", "S"], (
        f"expected transaction_codes echo to be uppercased ['P','S']; "
        f"got {data.get('transaction_codes')!r}"
    )

    results = data["results"]
    assert len(results) == 1, results
    row = results[0]
    assert row.get("ok") is True, row
    assert "dropped_unparseable_codes" in row, row
    assert row["dropped_unparseable_codes"] >= 0, row
    records = row["records"]
    assert records, (
        "expected at least one P/S insider transaction on AAPL in 180-day window; "
        f"row={row!r}"
    )
    for index, record in enumerate(records):
        code = record.get("transaction_code")
        assert code in {"P", "S"}, (
            f"records[{index}].transaction_code must be one of P/S; got {code!r}"
        )


def test_insider_invalid_days_rejected() -> None:
    """`--days 0` is rejected by the argparse validator before any
    OpenBB call; argparse exits 2 with a usage message on stderr."""

    completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--days", "0"],
        timeout=30,
    )
    assert completed.returncode == 2, (
        f"expected exit 2 for --days 0; got {completed.returncode}; "
        f"stdout:\n{completed.stdout[:500]}\nstderr:\n{completed.stderr[-500:]}"
    )
    assert "positive integer" in completed.stderr, (
        f"expected validator message on stderr; got {completed.stderr!r}"
    )


def test_insider_invalid_transaction_codes_rejected() -> None:
    """`--transaction-codes "PP"` is rejected by the argparse validator
    before any OpenBB call."""

    completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--transaction-codes", "PP"],
        timeout=30,
    )
    assert completed.returncode == 2, (
        f"expected exit 2 for --transaction-codes PP; got {completed.returncode}; "
        f"stdout:\n{completed.stdout[:500]}\nstderr:\n{completed.stderr[-500:]}"
    )
    assert "single ASCII letter" in completed.stderr, (
        f"expected validator message on stderr; got {completed.stderr!r}"
    )


def test_insider_failure_row_omits_dropped_unparseable_codes() -> None:
    """Per-row shape invariant: an `ok: False` row carries no
    `dropped_unparseable_codes` field.

    Mixes a real SEC ticker with a non-existent symbol so the batch is
    not all-fatal; the bogus symbol fails with a non-credential
    `error_category` (typically `other`) and surfaces under
    `data.results[]`. The credential-only fatal path bypasses
    `data.results` via `emit_error`, so the structural invariant is
    exercised here on the visible failure shape.
    """

    completed = run_wrapper_or_xfail(
        [
            "scripts/insider.py",
            "AAPL",
            "ZZZZZZZZ",
            "--provider",
            "sec",
            "--days",
            "30",
            "--transaction-codes",
            "P,S",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"mixed-batch insider exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope_shape(payload)
    results = data["results"]
    failure_rows = [r for r in results if not r.get("ok")]
    assert failure_rows, f"expected at least one failure row; got {results!r}"
    for row in failure_rows:
        assert "dropped_unparseable_codes" not in row, (
            f"failure row must omit dropped_unparseable_codes; got {row!r}"
        )
        assert "records" not in row, (
            f"failure row must omit records; got {row!r}"
        )
        assert isinstance(row.get("error_category"), str), row


# ---------------------------------------------------------------------------
# Task 8.2 — FMP happy-paths under skip-gate
# ---------------------------------------------------------------------------


def test_insider_fmp_single_symbol_happy_path() -> None:
    """FMP single-symbol fetch returns a normalized envelope (skip-gated
    on `FMP_API_KEY`)."""

    if not os.environ.get("FMP_API_KEY"):
        pytest.skip("FMP happy-path requires FMP_API_KEY")
    completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--provider", "fmp", "--days", "90"],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"insider.py FMP single-symbol exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope_shape(payload)
    assert data.get("provider") == "fmp", data

    results = data["results"]
    assert len(results) == 1, results
    row = results[0]
    assert row.get("symbol") == "AAPL", row
    assert row.get("provider") == "fmp", row
    if row.get("ok") and row.get("records"):
        first = row["records"][0]
        assert set(first.keys()) == _CANONICAL_RECORD_KEYS, (
            f"FMP record key set diverges from canonical 19-field surface; "
            f"got {sorted(first.keys())!r}"
        )


def test_insider_fmp_markdown_format() -> None:
    """`--format md` under FMP returns a non-empty markdown document
    that is not valid JSON (skip-gated on `FMP_API_KEY`)."""

    if not os.environ.get("FMP_API_KEY"):
        pytest.skip("FMP markdown happy-path requires FMP_API_KEY")
    completed = run_wrapper_or_xfail(
        [
            "scripts/insider.py",
            "AAPL",
            "--provider",
            "fmp",
            "--days",
            "90",
            "--format",
            "md",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"insider.py FMP --format md exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    stdout = completed.stdout
    assert stdout.strip(), "markdown stdout must be non-empty"
    with pytest.raises(json.JSONDecodeError):
        json.loads(stdout)
    assert "## AAPL" in stdout, (
        f"expected `## AAPL` heading in markdown output; got first 500 chars:\n{stdout[:500]}"
    )


# ---------------------------------------------------------------------------
# Task 8.3 — Cross-provider schema-consistency
# ---------------------------------------------------------------------------


def _record_key_set(payload: dict[str, object]) -> set[str] | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None
    row = results[0]
    if not row.get("ok"):
        return None
    records = row.get("records")
    if not isinstance(records, list) or not records:
        return None
    return set(records[0].keys())


def test_insider_schema_is_provider_invariant() -> None:
    """The same single-symbol query under SEC and FMP produces records
    whose key set is exactly the canonical 19-field surface; non-null
    `transaction_code` is a single uppercase letter on both. The FMP
    half is skip-gated on `FMP_API_KEY`."""

    sec_completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--provider", "sec", "--days", "90"],
        timeout=120,
    )
    assert sec_completed.returncode == 0, sec_completed.stderr[-2000:]
    sec_payload = assert_stdout_is_single_json(sec_completed)
    sec_keys = _record_key_set(sec_payload)
    assert sec_keys == _CANONICAL_RECORD_KEYS, (
        f"SEC record key set diverges; got {sorted(sec_keys) if sec_keys else None!r}"
    )

    sec_records = sec_payload["data"]["results"][0]["records"]
    for index, record in enumerate(sec_records):
        code = record.get("transaction_code")
        if code is not None:
            assert isinstance(code, str) and len(code) == 1 and code.isupper() and code.isalpha(), (
                f"SEC records[{index}].transaction_code must be a single uppercase letter; "
                f"got {code!r}"
            )

    if not os.environ.get("FMP_API_KEY"):
        pytest.skip("FMP half of cross-provider consistency requires FMP_API_KEY")

    fmp_completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--provider", "fmp", "--days", "90"],
        timeout=120,
    )
    assert fmp_completed.returncode == 0, fmp_completed.stderr[-2000:]
    fmp_payload = assert_stdout_is_single_json(fmp_completed)
    fmp_keys = _record_key_set(fmp_payload)
    if fmp_keys is None:
        pytest.skip("FMP half returned no records in window; cannot compare key sets")
    assert fmp_keys == _CANONICAL_RECORD_KEYS, (
        f"FMP record key set diverges; got {sorted(fmp_keys)!r}"
    )

    fmp_records = fmp_payload["data"]["results"][0]["records"]
    for index, record in enumerate(fmp_records):
        code = record.get("transaction_code")
        if code is not None:
            assert isinstance(code, str) and len(code) == 1 and code.isupper() and code.isalpha(), (
                f"FMP records[{index}].transaction_code must be a single uppercase letter; "
                f"got {code!r}"
            )


# ---------------------------------------------------------------------------
# Task 8.4 — Markdown invariants and fatal fall-back
# ---------------------------------------------------------------------------


def test_insider_markdown_invariants_sec() -> None:
    """Under `--format md` over SEC, stdout is not valid JSON and contains
    one `## <SYMBOL>` heading per input ticker; for each ticker it carries
    either a markdown table, the inline `_error_category_:` line, or the
    `_no records in window_` line."""

    completed = run_wrapper_or_xfail(
        [
            "scripts/insider.py",
            "AAPL",
            "MSFT",
            "--provider",
            "sec",
            "--days",
            "90",
            "--format",
            "md",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"insider.py --format md exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    stdout = completed.stdout
    with pytest.raises(json.JSONDecodeError):
        json.loads(stdout)

    assert "## AAPL" in stdout, stdout[:500]
    assert "## MSFT" in stdout, stdout[:500]

    sections = stdout.split("## ")
    body_sections = [s for s in sections if s.strip()]
    assert len(body_sections) >= 2, body_sections
    for section in body_sections:
        has_table_header = "filing_date | transaction_date" in section
        has_error_line = "_error_category_:" in section
        has_empty_line = "_no records in window_" in section
        assert has_table_header or has_error_line or has_empty_line, (
            f"each ticker section must carry one of: table header, error line, "
            f"empty line; got section head:\n{section[:400]}"
        )


def test_insider_markdown_falls_back_to_json_on_fatal() -> None:
    """`--format md --provider fmp` with `FMP_API_KEY` deliberately unset
    on a single-symbol input is an all-rows-fatal credential scenario;
    the markdown path falls back to the JSON envelope and exits 2."""

    completed = run_wrapper_or_xfail(
        [
            "scripts/insider.py",
            "AAPL",
            "--provider",
            "fmp",
            "--format",
            "md",
        ],
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
    assert payload.get("tool") == "insider", payload
