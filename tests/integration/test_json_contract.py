"""Integration test: per-wrapper JSON contract against the key-free
`yfinance` provider.

Covers:
- `quote.py AAPL --provider yfinance` (Req 5.1)
- `historical.py AAPL --start 2025-01-01 --provider yfinance` (Req 5.2)
- Invalid-ticker fallback (`ZZZZZZINVALID`) (Req 5.3)

All invocations route through `run_wrapper_or_xfail` so that transient
yfinance failures are surfaced as `xfail` (with a structured reason),
not as hard regressions.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.integration.conftest import (
    WRAPPERS,
    _has_error_key,
    assert_stdout_is_single_json,
    run_wrapper,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


def _iter_dicts(payload: Any):
    """Yield every dict in `payload` (including nested) in depth-first order."""

    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _iter_dicts(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_dicts(item)


def _envelope_candidates(payload: Any) -> list[dict[str, Any]]:
    """Return dicts that may carry the top-level envelope.

    The envelope can live at the payload root (dict case) or at the first
    level of a list of per-symbol results; we check both.
    """

    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def test_quote_yfinance_aapl_records() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/quote.py", "AAPL", "--provider", "yfinance"]
    )
    assert completed.returncode == 0, (
        f"quote.py AAPL exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)

    assert isinstance(payload, dict) and isinstance(
        payload.get("data"), dict
    ), f"expected wrap() envelope with a dict `data` field; payload={payload!r}"
    results = payload["data"].get("results")
    assert (
        isinstance(results, list) and results
    ), f"expected `data.results` to be a non-empty list; payload={payload!r}"
    aapl_entries = [entry for entry in results if entry.get("symbol") == "AAPL"]
    assert aapl_entries, (
        f"expected at least one `data.results[]` entry with symbol=AAPL; "
        f"payload={payload!r}"
    )


def test_quote_yfinance_aapl_envelope() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/quote.py", "AAPL", "--provider", "yfinance"]
    )
    assert completed.returncode == 0, (
        f"quote.py AAPL exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)

    assert isinstance(
        payload, dict
    ), f"expected quote envelope to be a JSON object; payload={payload!r}"
    assert (
        "collected_at" in payload
    ), f"expected `collected_at` on the envelope; payload={payload!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", (
        f'expected `source == "marketdesk-for-ai-agents"` on the envelope; '
        f"payload={payload!r}"
    )
    assert (
        payload.get("tool") == "quote"
    ), f'expected `tool == "quote"` on the envelope; payload={payload!r}'


def test_historical_yfinance_aapl_contract() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/historical.py",
            "AAPL",
            "--start",
            "2025-01-01",
            "--provider",
            "yfinance",
        ]
    )
    assert completed.returncode == 0, (
        f"historical.py AAPL exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)

    series = _extract_time_series(payload)
    assert series, (
        f"expected a non-empty time-series for AAPL; payload keys only: "
        f"{_shape_summary(payload)!r}"
    )
    for record in series:
        assert isinstance(
            record, dict
        ), f"expected time-series records to be dicts; got {type(record).__name__}"
        assert _record_has_date(
            record
        ), f"expected each record to carry a date field; record={record!r}"
        assert (
            "close" in record
        ), f"expected each record to carry a `close` value; record={record!r}"


def test_historical_yfinance_aapl_envelope() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/historical.py",
            "AAPL",
            "--start",
            "2025-01-01",
            "--provider",
            "yfinance",
        ]
    )
    assert completed.returncode == 0, (
        f"historical.py AAPL exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)

    assert isinstance(
        payload, dict
    ), f"expected historical envelope to be a JSON object; payload={payload!r}"
    assert (
        "collected_at" in payload
    ), f"expected `collected_at` on the envelope; payload={payload!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", (
        f'expected `source == "marketdesk-for-ai-agents"` on the envelope; '
        f"payload={payload!r}"
    )
    assert (
        payload.get("tool") == "historical"
    ), f'expected `tool == "historical"` on the envelope; payload={payload!r}'
    results = payload.get("data", {}).get("results")
    assert (
        isinstance(results, list) and results
    ), f"expected `data.results` to be a non-empty list; payload={payload!r}"


def test_historical_yfinance_aapl_rows_are_ascending_by_date() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/historical.py",
            "AAPL",
            "--start",
            "2025-01-01",
            "--provider",
            "yfinance",
        ]
    )
    assert completed.returncode == 0, (
        f"historical.py AAPL exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)

    series = _extract_time_series(payload)
    assert series, (
        f"expected a non-empty time-series for AAPL; payload shape: "
        f"{_shape_summary(payload)!r}"
    )

    date_key = next(
        (k for k in _DATE_KEYS if k in series[0]),
        None,
    )
    assert date_key is not None, (
        f"expected time-series records to carry a date field; "
        f"first record={series[0]!r}"
    )

    dates = [str(record[date_key]) for record in series]
    assert dates == sorted(dates), (
        "expected rows to be sorted ascending by date; "
        f"first-out-of-order pairs: "
        f"{[(a, b) for a, b in zip(dates, dates[1:]) if a > b][:3]!r}"
    )


def test_invalid_ticker_yields_json_error() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/quote.py", "ZZZZZZINVALID", "--provider", "yfinance"]
    )
    assert completed.returncode in (0, 2), (
        f"expected returncode in (0, 2) for invalid ticker; "
        f"got {completed.returncode}; stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)

    if completed.returncode != 0:
        assert _has_error_key(payload), (
            f"non-zero exit must emit a payload with an `error` key; "
            f"payload={payload!r}"
        )
    else:
        assert _is_empty(payload) or _has_error_key(payload), (
            f"zero-exit invalid-ticker payload must be empty or carry an "
            f"`error` key somewhere; payload={payload!r}"
        )


# ---------------------------------------------------------------------------
# Small payload-shape helpers (test-local, kept narrow on purpose)
# ---------------------------------------------------------------------------


_DATE_KEYS = ("date", "Date", "timestamp", "datetime", "index")


def _record_has_date(record: dict[str, Any]) -> bool:
    return any(k in record for k in _DATE_KEYS)


def _extract_time_series(payload: Any) -> list[dict[str, Any]]:
    """Best-effort extraction of the time-series list from a historical-style
    payload.

    Supports both `{"data": [...]}` / `{"rows": [...]}` dict shapes and a
    bare list-of-dicts shape (the record detection heuristic picks the
    longest candidate list whose first element carries a date field).
    """

    if isinstance(payload, dict):
        for key in ("data", "rows", "records", "results"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if _record_has_date(value[0]):
                    return value
        for value in payload.values():
            nested = _extract_time_series(value)
            if nested:
                return nested
        return []

    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and _record_has_date(payload[0]):
            return payload
        for item in payload:
            nested = _extract_time_series(item)
            if nested:
                return nested
        return []

    return []


def _shape_summary(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: type(v).__name__ for k, v in payload.items()}
    if isinstance(payload, list):
        return [type(item).__name__ for item in payload[:3]]
    return type(payload).__name__


def _is_empty(payload: Any) -> bool:
    if payload is None:
        return True
    if isinstance(payload, (list, dict, str)):
        return len(payload) == 0
    return False


# ---------------------------------------------------------------------------
# Per-wrapper envelope invariants (Req 3.4, 5.2, 6.5, 7.1, 7.3)
#
# WRAPPER_HAPPY_ARGV declares one working argv per wrapper using only
# free-tier providers (yfinance / nasdaq / federal_reserve). FMP-gated
# sub-modes are intentionally absent from this table and exercised by
# dedicated credential-gate tests below (with `env_overrides` to unset
# FMP_API_KEY).
# ---------------------------------------------------------------------------


_ALLOWED_ERROR_CATEGORIES = {"credential", "transient", "validation", "other"}

# Req 2.4: envelope root key allowlist. `source` / `collected_at` / `tool` /
# `data` are mandatory (with `error` replacing `data` on fatal exits); the
# optional slots are `warnings` (row-level or extras) and `error` (fatal).
_ENVELOPE_ROOT_REQUIRED_KEYS = {"source", "collected_at", "tool"}
_ENVELOPE_ROOT_ALLOWED_KEYS = {
    "source",
    "collected_at",
    "tool",
    "data",
    "warnings",
    "error",
}


# stem → argv tail that produces a successful envelope without API keys.
WRAPPER_HAPPY_ARGV: dict[str, list[str]] = {
    "quote": ["AAPL", "--provider", "yfinance"],
    "historical": ["AAPL", "--start", "2025-01-01", "--provider", "yfinance"],
    "fundamentals": ["AAPL", "--type", "overview"],
    "estimates": ["AAPL", "--type", "consensus"],
    "etf": ["SPY", "--type", "info"],
    "calendars": [
        "--type",
        "earnings",
        "--start",
        "2026-04-17",
        "--provider",
        "nasdaq",
    ],
    "macro_survey": ["--series", "fomc_documents"],
    "sector_score": ["--universe", "sector-spdr"],
    "momentum": ["AAPL", "--indicator", "clenow", "--provider", "yfinance"],
    "insider": ["AAPL", "--provider", "sec"],
    "institutional": ["AAPL", "--provider", "fmp"],
    "filings": ["AAPL", "--provider", "sec"],
    "news": [
        "AAPL",
        "--scope",
        "company",
        "--provider",
        "yfinance",
        "--days",
        "7",
        "--limit",
        "5",
    ],
    "options": ["AAPL", "--type", "chain", "--provider", "yfinance"],
    "factors": ["--region", "america", "--frequency", "monthly"],
    "commodity": [
        "--symbol",
        "wti",
        "--type",
        "price",
        "--provider",
        "fred",
        "--start",
        "2025-01-01",
    ],
    "shorts": ["AAPL", "--type", "short_interest", "--provider", "finra"],
}


# stem → argv that must be rejected by argparse (choices violation).
WRAPPER_INVALID_ARGV: dict[str, list[str]] = {
    "quote": ["AAPL", "--provider", "__invalid_provider__"],
    "historical": [
        "AAPL",
        "--start",
        "2025-01-01",
        "--provider",
        "__invalid_provider__",
    ],
    "fundamentals": ["AAPL", "--type", "__invalid_type__"],
    "estimates": ["AAPL", "--type", "__invalid_type__"],
    "etf": ["SPY", "--type", "__invalid_type__"],
    "calendars": ["--type", "__invalid_type__", "--start", "2026-04-17"],
    "macro_survey": ["--series", "__invalid_series__"],
    "sector_score": ["--universe", "__invalid_universe__"],
    "momentum": ["AAPL", "--indicator", "__invalid_indicator__"],
    "insider": ["AAPL", "--provider", "__invalid_provider__"],
    "institutional": ["AAPL", "--provider", "__invalid_provider__"],
    "filings": ["AAPL", "--provider", "__invalid_provider__"],
    "news": ["AAPL", "--scope", "__invalid_scope__"],
    "options": ["AAPL", "--type", "__invalid_type__"],
    "factors": ["--region", "__invalid_region__"],
    "commodity": ["--type", "__invalid_type__"],
    "shorts": ["AAPL", "--type", "__invalid_type__"],
}


# stem → (env var, skip reason) for happy-path runs that depend on a
# free-tier credential. A clean key-free checkout will skip these with
# an explicit reason rather than fail the parametrized invariant tests.
_HAPPY_ARGV_REQUIRED_KEYS: dict[str, tuple[str, str]] = {
    "institutional": (
        "FMP_API_KEY",
        "FMP_API_KEY unset; institutional happy-path requires FMP free tier",
    ),
}


def _maybe_skip_for_required_key(stem: str) -> None:
    """Skip the current happy-path test when the wrapper requires a
    credential and the host environment lacks it."""

    requirement = _HAPPY_ARGV_REQUIRED_KEYS.get(stem)
    if requirement is None:
        return
    env_var, reason = requirement
    if not os.getenv(env_var):
        pytest.skip(reason)


def _wrapper_ids() -> list[str]:
    return [p.stem for p in WRAPPERS]


def _argv_for(stem: str, table: dict[str, list[str]]) -> list[str]:
    assert stem in table, (
        f"{stem}: missing from argv table — add an entry so the contract "
        f"invariants parametrize over every wrapper"
    )
    return [f"scripts/{stem}.py", *table[stem]]


def _collect_failure_rows(results: list[Any]) -> list[dict[str, Any]]:
    return [row for row in results if isinstance(row, dict) and row.get("ok") is False]


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_declares_happy_argv(wrapper_path: Any) -> None:
    """Guard against a new wrapper landing without a happy-path entry here.

    If this fails, add `wrapper_path.stem` to both `WRAPPER_HAPPY_ARGV` and
    `WRAPPER_INVALID_ARGV` so the rest of the parametrized suite keeps
    covering it.
    """

    assert wrapper_path.stem in WRAPPER_HAPPY_ARGV, (
        f"{wrapper_path.stem}: missing from WRAPPER_HAPPY_ARGV — "
        f"register a free-tier argv so envelope invariants stay enforced"
    )
    assert wrapper_path.stem in WRAPPER_INVALID_ARGV, (
        f"{wrapper_path.stem}: missing from WRAPPER_INVALID_ARGV — "
        f"register an argparse-choices violation so exit-code 2 stays enforced"
    )


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_envelope_has_data_results_list(wrapper_path: Any) -> None:
    """Req 3.1: every wrapper exposes `data.results: list[dict]`."""

    stem = wrapper_path.stem
    _maybe_skip_for_required_key(stem)
    argv = _argv_for(stem, WRAPPER_HAPPY_ARGV)
    # sector_score fans out to 11 tickers × multiple providers, so bump
    # the per-wrapper timeout for that stem specifically.
    timeout = 600 if stem == "sector_score" else 180
    completed = run_wrapper_or_xfail(argv, timeout=timeout)

    assert completed.returncode == 0, (
        f"{stem} exited {completed.returncode} on happy argv {argv!r}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert isinstance(
        payload, dict
    ), f"{stem}: envelope must be a JSON object; got {type(payload).__name__}"
    data = payload.get("data")
    assert isinstance(
        data, dict
    ), f"{stem}: `data` must be a dict; got {type(data).__name__}"
    results = data.get("results")
    assert isinstance(results, list), (
        f"{stem}: `data.results` must be a list; got "
        f"{type(results).__name__}={results!r}"
    )
    for index, row in enumerate(results):
        assert isinstance(row, dict), (
            f"{stem}: `data.results[{index}]` must be a dict; "
            f"got {type(row).__name__}={row!r}"
        )


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_tool_field_matches_stem(wrapper_path: Any) -> None:
    """Req 5.2: `payload["tool"]` equals `scripts/<stem>.py`'s stem."""

    stem = wrapper_path.stem
    _maybe_skip_for_required_key(stem)
    argv = _argv_for(stem, WRAPPER_HAPPY_ARGV)
    timeout = 600 if stem == "sector_score" else 180
    completed = run_wrapper_or_xfail(argv, timeout=timeout)
    assert completed.returncode == 0, (
        f"{stem} exited {completed.returncode}; stderr tail:\n"
        f"{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == stem, (
        f"{stem}: `tool` must equal the file stem; " f"got tool={payload.get('tool')!r}"
    )


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_envelope_root_is_restricted_to_allowlist(
    wrapper_path: Any,
) -> None:
    """Req 2.4: the envelope root carries only `{source, collected_at, tool,
    data}` plus the optional `{warnings, error}` slots.

    Per-query meta (`provider` / `type` / `indicator` / `start` / `series`
    etc.) must live under `data/` as a sibling of `results`. Regression
    guard against `_common.wrap(data, **meta)` or a new helper silently
    leaking meta back to the envelope root.
    """

    stem = wrapper_path.stem
    _maybe_skip_for_required_key(stem)
    argv = _argv_for(stem, WRAPPER_HAPPY_ARGV)
    timeout = 600 if stem == "sector_score" else 180
    completed = run_wrapper_or_xfail(argv, timeout=timeout)
    assert completed.returncode == 0, (
        f"{stem} exited {completed.returncode} on happy argv {argv!r}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert isinstance(
        payload, dict
    ), f"{stem}: envelope must be a JSON object; got {type(payload).__name__}"

    root_keys = set(payload.keys())
    extras = root_keys - _ENVELOPE_ROOT_ALLOWED_KEYS
    assert not extras, (
        f"{stem}: envelope root must be a subset of "
        f"{sorted(_ENVELOPE_ROOT_ALLOWED_KEYS)!r}; unexpected keys: "
        f"{sorted(extras)!r}"
    )
    missing_required = _ENVELOPE_ROOT_REQUIRED_KEYS - root_keys
    assert not missing_required, (
        f"{stem}: envelope root is missing required keys "
        f"{sorted(missing_required)!r}; payload keys: {sorted(root_keys)!r}"
    )
    # `data` and `error` are mutually-exclusive: a happy-path run carries
    # `data`, a fatal run carries `error`. Require one of them so the
    # allowlist assert above does not pass on a zero-key payload.
    assert ("data" in root_keys) ^ ("error" in root_keys), (
        f"{stem}: envelope root must carry exactly one of `data` / `error`; "
        f"got {sorted(root_keys)!r}"
    )


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_failure_rows_carry_three_error_fields(
    wrapper_path: Any,
) -> None:
    """Req 6.5 / 6.1: every `ok=False` row declares `error`, `error_type`,
    `error_category` — and the category is one of the four canonical values.

    The happy argv usually returns no failure rows; when it does (e.g.
    sector_score partials), the invariant still holds. We therefore only
    assert the schema conditionally on failures being present.
    """

    stem = wrapper_path.stem
    _maybe_skip_for_required_key(stem)
    argv = _argv_for(stem, WRAPPER_HAPPY_ARGV)
    timeout = 600 if stem == "sector_score" else 180
    completed = run_wrapper_or_xfail(argv, timeout=timeout)
    if completed.returncode != 0:
        pytest.skip(
            f"{stem}: happy argv exited non-zero "
            f"({completed.returncode}); failure-row invariant is covered "
            f"by the credential-gate tests"
        )
    payload = assert_stdout_is_single_json(completed)
    results = payload.get("data", {}).get("results") or []
    failure_rows = _collect_failure_rows(results)
    if not failure_rows:
        pytest.skip(f"{stem}: no failure rows in happy argv; invariant vacuous")
    for index, row in enumerate(failure_rows):
        for field in ("error", "error_type", "error_category"):
            assert (
                field in row
            ), f"{stem}: failure row [{index}] missing `{field}`: {row!r}"
        category = row["error_category"]
        assert category in _ALLOWED_ERROR_CATEGORIES, (
            f"{stem}: failure row [{index}].error_category must be one of "
            f"{sorted(_ALLOWED_ERROR_CATEGORIES)!r}; got {category!r}"
        )


def _fail_on_nan_token(token: str) -> Any:
    """`json.loads` parse_constant callback — fires only for the three
    non-standard tokens `NaN` / `Infinity` / `-Infinity`. Raising here
    turns strict JSON into a hard assertion without needing jq / node
    on the host.
    """

    raise ValueError(f"non-standard JSON token encountered: {token!r}")


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_stdout_is_strict_json(wrapper_path: Any) -> None:
    """Req 1.1 / 1.5: every wrapper's stdout parses under RFC 8259 strict
    mode — no bare `NaN` / `Infinity` / `-Infinity` literals.

    `json.loads(..., parse_constant=_fail)` is called only for those
    three non-standard tokens (Python stdlib `json` contract as of 3.12),
    so a raising `parse_constant` is equivalent to a strict-JSON gate
    without adding jq / node subprocess dependencies.
    """

    stem = wrapper_path.stem
    _maybe_skip_for_required_key(stem)
    argv = _argv_for(stem, WRAPPER_HAPPY_ARGV)
    timeout = 600 if stem == "sector_score" else 180
    completed = run_wrapper_or_xfail(argv, timeout=timeout)

    assert completed.returncode == 0, (
        f"{stem} exited {completed.returncode} on happy argv {argv!r}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    stdout = completed.stdout
    if stdout.endswith("\n"):
        stdout = stdout[:-1]

    try:
        json.loads(stdout, parse_constant=_fail_on_nan_token)
    except ValueError as exc:
        raise AssertionError(
            f"{stem}: stdout contains non-standard JSON token "
            f"({exc}); argv={argv!r}"
        ) from exc


@pytest.mark.parametrize("wrapper_path", WRAPPERS, ids=_wrapper_ids())
def test_wrapper_argparse_invalid_input_exits_2(wrapper_path: Any) -> None:
    """Req 7.1: argparse choices violations (and other argparse misuse)
    must surface as exit code 2, never a JSON envelope with zero exit.
    """

    stem = wrapper_path.stem
    argv = _argv_for(stem, WRAPPER_INVALID_ARGV)
    completed = run_wrapper(argv, timeout=60)
    assert completed.returncode == 2, (
        f"{stem}: argparse misuse must exit 2; got {completed.returncode}. "
        f"stdout tail:\n{completed.stdout[-500:]}\n"
        f"stderr tail:\n{completed.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# Credential-gate regressions (Req 6.3, 7.1)
# ---------------------------------------------------------------------------


def _fmp_credential_env() -> dict[str, str]:
    """Empty-string overrides so dotenv's `override=False` behaviour keeps
    the blank in place (see conftest for rationale)."""

    return {"FMP_API_KEY": ""}


def test_fundamentals_ratios_credential_gate() -> None:
    """Req 6.3 / 7.1: `fundamentals --type ratios` (FMP-only) exits 2 with
    `CredentialError:` when FMP_API_KEY is unset.
    """

    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset in the host env; the credential gate can "
            "still flip via env_overrides but the dotenv-restored key "
            "would otherwise mask it"
        )
    completed = run_wrapper(
        ["scripts/fundamentals.py", "AAPL", "--type", "ratios"],
        timeout=120,
        env_overrides=_fmp_credential_env(),
    )
    assert completed.returncode == 2, (
        f"fundamentals --type ratios must exit 2 on all-credential failure; "
        f"got {completed.returncode}. stdout tail:\n{completed.stdout[-500:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    error_message = payload.get("error") or ""
    assert "CredentialError:" in error_message, (
        f"credential-all-failed envelope must carry the "
        f"`CredentialError:` prefix in `error`; got {error_message!r}"
    )
    assert payload.get("tool") == "fundamentals", payload


def test_etf_holdings_credential_gate() -> None:
    """Req 6.3: `etf --type holdings` defaults to the FMP provider, so the
    credential gate fires the same way as fundamentals.
    """

    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset in the host env; see "
            "test_fundamentals_ratios_credential_gate for the rationale"
        )
    completed = run_wrapper(
        ["scripts/etf.py", "SPY", "--type", "holdings"],
        timeout=120,
        env_overrides=_fmp_credential_env(),
    )
    assert completed.returncode == 2, (
        f"etf --type holdings must exit 2 on all-credential failure; "
        f"got {completed.returncode}. stdout tail:\n{completed.stdout[-500:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    error_message = payload.get("error") or ""
    assert "CredentialError:" in error_message, (
        f"credential-all-failed envelope must carry the "
        f"`CredentialError:` prefix in `error`; got {error_message!r}"
    )
    assert payload.get("tool") == "etf", payload


# ---------------------------------------------------------------------------
# EIA-gated commodity happy paths (Req 9.3)
#
# `commodity.py --type weekly_report` and `--type steo` both require
# `EIA_API_KEY`. The `--type price` happy path uses FRED and is covered
# by the parametrized table above; these two dedicated tests fill the
# remaining commodity sub-modes and skip with an explicit reason on a
# clean key-free checkout.
# ---------------------------------------------------------------------------


def test_commodity_weekly_report_happy_path() -> None:
    """Req 4.2 / 9.2 / 9.3: `commodity --type weekly_report` returns the
    EIA Weekly Petroleum Status Report envelope when EIA_API_KEY is set.
    """

    if not os.getenv("EIA_API_KEY"):
        pytest.skip(
            "EIA_API_KEY unset; commodity --type weekly_report requires "
            "the EIA free-tier key"
        )
    completed = run_wrapper_or_xfail(
        [
            "scripts/commodity.py",
            "--type",
            "weekly_report",
            "--start",
            "2025-01-01",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"commodity --type weekly_report exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "commodity", payload
    data = payload.get("data")
    assert isinstance(data, dict), payload
    results = data.get("results")
    assert isinstance(results, list), payload


def test_commodity_steo_happy_path() -> None:
    """Req 4.2 / 9.2 / 9.3: `commodity --type steo` returns the EIA
    Short-Term Energy Outlook envelope when EIA_API_KEY is set.
    """

    if not os.getenv("EIA_API_KEY"):
        pytest.skip(
            "EIA_API_KEY unset; commodity --type steo requires the EIA " "free-tier key"
        )
    completed = run_wrapper_or_xfail(
        ["scripts/commodity.py", "--type", "steo"],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"commodity --type steo exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "commodity", payload
    data = payload.get("data")
    assert isinstance(data, dict), payload
    results = data.get("results")
    assert isinstance(results, list), payload


# ---------------------------------------------------------------------------
# institutional 13F partial-filing-window contract (Req 1.6 / 1.8)
#
# `scripts/institutional.py` injects `partial_filing_window: bool` into each
# inner record dict (`payload["data"]["results"][i]["records"][j]`) when the
# upstream call succeeds. The two tests below pin the contract:
#
# - A quarter older than 1 year (filing window long since closed) must yield
#   `partial_filing_window is False` on every record.
# - The free-tier happy argv (no `--year` / `--quarter`) must still carry the
#   key on every success record as a `bool` (regression guard for accidental
#   key removal — value may be either True or False depending on calendar).
# ---------------------------------------------------------------------------


def test_institutional_completed_quarter_partial_filing_window_is_false() -> None:
    """Req 1.6 / 1.8: an explicitly-queried completed quarter (filing window
    closed >= 1 year ago) must emit `partial_filing_window: False` on every
    inner record. Failure rows (`ok=False`) carry no `records` key and no
    flag at all.
    """

    _maybe_skip_for_required_key("institutional")
    argv = [
        "scripts/institutional.py",
        "AAPL",
        "--provider",
        "fmp",
        "--year",
        "2025",
        "--quarter",
        "1",
    ]
    completed = run_wrapper_or_xfail(argv, timeout=180)
    assert completed.returncode == 0, (
        f"institutional --year 2025 --quarter 1 exited "
        f"{completed.returncode}; stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)

    outer_results = payload.get("data", {}).get("results")
    assert (
        isinstance(outer_results, list) and outer_results
    ), f"expected `data.results` to be a non-empty list; payload={payload!r}"

    for outer in outer_results:
        if not outer.get("ok"):
            assert (
                "records" not in outer
            ), f"failure row must not carry `records`; got {outer!r}"
            assert "partial_filing_window" not in outer, (
                f"failure row must not carry `partial_filing_window`; " f"got {outer!r}"
            )
            continue
        records = outer.get("records") or []
        assert records, (
            f"completed quarter must yield at least one record; " f"got outer={outer!r}"
        )
        for rec in records:
            assert rec.get("partial_filing_window") is False, (
                f"completed quarter {rec.get('date')!r} must not flag "
                f"partial_filing_window; got rec={rec!r}"
            )


def test_institutional_happy_argv_records_carry_partial_filing_window_key() -> None:
    """Req 1.8 regression guard: the default happy argv (no `--year` /
    `--quarter`) must still emit `partial_filing_window: bool` on every
    success record. Either truth value is acceptable — this only pins the
    key's presence and type so future refactors cannot silently drop it.
    """

    _maybe_skip_for_required_key("institutional")
    argv = _argv_for("institutional", WRAPPER_HAPPY_ARGV)
    completed = run_wrapper_or_xfail(argv, timeout=180)
    assert completed.returncode == 0, (
        f"institutional happy argv exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)

    outer_results = payload.get("data", {}).get("results")
    assert (
        isinstance(outer_results, list) and outer_results
    ), f"expected `data.results` to be a non-empty list; payload={payload!r}"

    saw_success_record = False
    for outer in outer_results:
        if not outer.get("ok"):
            continue
        records = outer.get("records") or []
        for rec in records:
            saw_success_record = True
            assert "partial_filing_window" in rec, (
                f"success record must carry `partial_filing_window` key; "
                f"got rec={rec!r}"
            )
            assert isinstance(rec["partial_filing_window"], bool), (
                f"`partial_filing_window` must be bool; got "
                f"{type(rec['partial_filing_window']).__name__}={rec!r}"
            )
    assert saw_success_record, (
        f"expected at least one success record across "
        f"`data.results[*].records[*]`; payload={payload!r}"
    )
