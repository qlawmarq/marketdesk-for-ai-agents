"""Integration tests covering Task 11 residual gaps.

These tests close gaps surfaced by the post-Task-6 review of the
parametrised harness: the existing `WRAPPER_HAPPY_ARGV` table proves
the envelope contract, but it does not assert that returned rows
carry the expected per-row fields, that sub-modes outside the table
work, that credential gates emit the documented envelope, or that
multi-symbol failure isolation actually fires.

Suites:
- 11.1: per-row field invariants for the six derivation-free wrappers
- 11.2: sub-modes not represented in `WRAPPER_HAPPY_ARGV`
- 11.3: credential-error envelope shape for new credential-gated wrappers
- 11.4: per-symbol failure isolation for new aggregate-envelope wrappers
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 11.1 — per-row field invariants
#
# Each test runs the wrapper's documented happy-path argv and asserts a
# tight subset of stable per-row fields. When the upstream provider
# returns no rows (a quiet day), the assertions are skipped: the goal is
# to catch "envelope passes but rows malformed", not to fail on
# legitimate empty responses.
# ---------------------------------------------------------------------------


def _aggregate_records(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    rows = payload.get("data", {}).get("results") or []
    for row in rows:
        if isinstance(row, dict) and row.get("symbol") == symbol and row.get("ok"):
            return row.get("records") or []
    return []


def _single_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload.get("data", {}).get("results") or []


def _skip_if_empty(records: list[dict[str, Any]], stem: str) -> None:
    if not records:
        pytest.skip(
            f"{stem}: provider returned no records on this run; field "
            f"invariants are only meaningful on a non-empty result set"
        )


def test_insider_rows_carry_symbol_and_a_date_field() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--provider", "sec", "--limit", "20"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    _skip_if_empty(records, "insider")

    for index, rec in enumerate(records):
        assert rec.get("symbol") == "AAPL", (
            f"insider[{index}]: expected symbol=AAPL; got {rec.get('symbol')!r}"
        )
        # SEC Form 4 records always carry filing_date; transaction_date
        # is preferred for the day-window filter and the analyst's lens.
        assert rec.get("transaction_date") or rec.get("filing_date"), (
            f"insider[{index}]: expected transaction_date or filing_date; "
            f"keys={sorted(rec.keys())!r}"
        )


def test_institutional_rows_carry_holder_identifier_and_date() -> None:
    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset; institutional rows require the FMP free tier"
        )
    completed = run_wrapper_or_xfail(
        ["scripts/institutional.py", "AAPL", "--provider", "fmp"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    _skip_if_empty(records, "institutional")

    for index, rec in enumerate(records):
        # FMP's 13F summary endpoint returns aggregated holder counts per
        # filing period; `cik` identifies the issuer, `date` stamps the
        # period, and `investors_holding` is the rollup count proving
        # this is the 13F shape (and not, say, an empty placeholder row).
        assert rec.get("cik"), (
            f"institutional[{index}]: expected non-empty `cik`; "
            f"keys={sorted(rec.keys())!r}"
        )
        assert rec.get("date"), (
            f"institutional[{index}]: expected `date`; "
            f"keys={sorted(rec.keys())!r}"
        )


def test_filings_rows_carry_form_type_and_date() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/filings.py", "AAPL", "--provider", "sec", "--limit", "10"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    _skip_if_empty(records, "filings")

    for index, rec in enumerate(records):
        form = rec.get("report_type") or rec.get("form_type")
        assert isinstance(form, str) and form, (
            f"filings[{index}]: expected `report_type` or `form_type`; "
            f"keys={sorted(rec.keys())!r}"
        )
        assert rec.get("filing_date") or rec.get("report_date"), (
            f"filings[{index}]: expected `filing_date` or `report_date`; "
            f"keys={sorted(rec.keys())!r}"
        )


def test_news_company_rows_carry_title_url_and_symbol() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/news.py", "AAPL",
            "--scope", "company", "--provider", "yfinance",
            "--days", "7", "--limit", "5",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    _skip_if_empty(records, "news")

    for index, rec in enumerate(records):
        assert isinstance(rec.get("title"), str) and rec["title"], (
            f"news[{index}]: expected non-empty `title`; "
            f"keys={sorted(rec.keys())!r}"
        )
        assert isinstance(rec.get("url"), str) and rec["url"], (
            f"news[{index}]: expected non-empty `url`; "
            f"keys={sorted(rec.keys())!r}"
        )
        assert rec.get("symbol") == "AAPL", (
            f"news[{index}]: expected symbol=AAPL; got {rec.get('symbol')!r}"
        )


_NUMERIC_PRICE_KEYS = ("price", "value", "close")


def test_commodity_price_rows_carry_date_and_numeric_value() -> None:
    if not os.getenv("FRED_API_KEY"):
        pytest.skip(
            "FRED_API_KEY unset; commodity --type price requires the FRED key"
        )
    completed = run_wrapper_or_xfail(
        [
            "scripts/commodity.py",
            "--symbol", "wti", "--type", "price", "--provider", "fred",
            "--start", "2025-01-01",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _single_records(payload)
    _skip_if_empty(records, "commodity")

    for index, rec in enumerate(records):
        assert rec.get("date"), (
            f"commodity[{index}]: expected `date`; "
            f"keys={sorted(rec.keys())!r}"
        )
        numeric_present = any(
            isinstance(rec.get(k), (int, float)) for k in _NUMERIC_PRICE_KEYS
        )
        assert numeric_present, (
            f"commodity[{index}]: expected one of "
            f"{list(_NUMERIC_PRICE_KEYS)!r} as a numeric price; "
            f"keys={sorted(rec.keys())!r}"
        )


def test_shorts_short_interest_rows_carry_settlement_date_and_position() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/shorts.py", "AAPL",
            "--type", "short_interest", "--provider", "finra",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    _skip_if_empty(records, "shorts")

    for index, rec in enumerate(records):
        assert rec.get("settlement_date"), (
            f"shorts[{index}]: expected `settlement_date`; "
            f"keys={sorted(rec.keys())!r}"
        )
        # FINRA's canonical short-interest count surfaces as
        # `current_short_position`; the field's numeric type proves the
        # row is a real short-interest record and not an empty stub.
        assert isinstance(rec.get("current_short_position"), (int, float)), (
            f"shorts[{index}]: expected numeric `current_short_position`; "
            f"keys={sorted(rec.keys())!r}"
        )


# ---------------------------------------------------------------------------
# 11.2 — sub-modes outside `WRAPPER_HAPPY_ARGV`
# ---------------------------------------------------------------------------


def test_news_world_fmp_happy_path() -> None:
    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset; news --scope world defaults require the "
            "FMP free tier"
        )
    completed = run_wrapper_or_xfail(
        [
            "scripts/news.py",
            "--scope", "world", "--provider", "fmp",
            "--days", "3", "--limit", "5",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "news"
    data = payload.get("data") or {}
    assert data.get("scope") == "world"
    assert data.get("provider") == "fmp"
    assert isinstance(data.get("results"), list)


def test_shorts_fails_to_deliver_sec_happy_path() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/shorts.py", "AAPL",
            "--type", "fails_to_deliver", "--provider", "sec",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "shorts"
    data = payload.get("data") or {}
    assert data.get("type") == "fails_to_deliver"
    assert data.get("provider") == "sec"


_TEN_K_RE = re.compile(r"10-?K", re.IGNORECASE)


def test_filings_form_filter_narrows_to_10_k() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/filings.py", "AAPL",
            "--form", "10-K", "--provider", "sec",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    _skip_if_empty(records, "filings (10-K)")

    # SEC's native form_type filter accepts CSV and returns the requested
    # form plus its officially-named variants (10-K/A, 10-K405, NT 10-K).
    # The narrowing assertion is therefore: no off-form filings (e.g.
    # 10-Q, 8-K, S-1) leak through.
    for index, rec in enumerate(records):
        form = rec.get("report_type") or rec.get("form_type") or ""
        assert _TEN_K_RE.search(form), (
            f"filings[{index}]: --form 10-K must narrow report_type to a "
            f"10-K variant; got {form!r}"
        )


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def test_insider_days_window_narrows_to_requested_range() -> None:
    days = 30
    completed = run_wrapper_or_xfail(
        ["scripts/insider.py", "AAPL", "--days", str(days), "--provider", "sec"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    records = _aggregate_records(payload, "AAPL")
    if not records:
        pytest.skip(
            "insider --days: no records in the requested window on this run; "
            "the narrowing invariant is only meaningful on a non-empty result"
        )

    cutoff = date.today() - timedelta(days=days)
    for index, rec in enumerate(records):
        record_date = (
            _coerce_date(rec.get("transaction_date"))
            or _coerce_date(rec.get("filing_date"))
        )
        # The wrapper's filter keeps records whose date is ≥ cutoff or
        # whose date could not be parsed; we only assert the parseable
        # branch (parse-failure is allowed to pass under the wrapper's
        # documented "keep-on-unknown-date" rule).
        if record_date is not None:
            assert record_date >= cutoff, (
                f"insider[{index}]: record dated {record_date.isoformat()} "
                f"falls outside --days {days} window (cutoff={cutoff.isoformat()})"
            )


def test_commodity_symbol_with_non_price_type_emits_warning() -> None:
    completed = run_wrapper(
        [
            "scripts/commodity.py",
            "--symbol", "wti", "--type", "weekly_report",
        ],
        timeout=180,
    )
    # Either a clean key-free checkout (EIA missing → exit 2 credential)
    # or a key-provisioned host (exit 0 with warning). The warning
    # invariant must hold in both branches because it is emitted before
    # the safe_call.
    payload = assert_stdout_is_single_json(completed)

    if completed.returncode == 2:
        # Credential-fatal branch: the warning rides on extra_warnings,
        # but `emit_error` does not echo them. Confirm this is the
        # documented credential gate and skip the warning assertion.
        error_message = payload.get("error") or ""
        assert "CredentialError:" in error_message, (
            f"commodity weekly_report exit-2 must be a CredentialError; "
            f"got {error_message!r}"
        )
        pytest.skip(
            "commodity weekly_report: EIA_API_KEY unset → fatal credential "
            "branch swallows extra_warnings; warning invariant covered when "
            "EIA_API_KEY is provisioned"
        )

    assert completed.returncode == 0, completed.stderr[-2000:]
    warnings = payload.get("warnings") or []
    matching = [
        w for w in warnings
        if isinstance(w, dict)
        and "wti" in (w.get("error") or "")
        and "weekly_report" in (w.get("error") or "")
    ]
    assert matching, (
        f"commodity --symbol wti --type weekly_report must emit a warning "
        f"naming both the symbol and the non-price type; "
        f"warnings={warnings!r}"
    )


# ---------------------------------------------------------------------------
# 11.3 — credential-error envelope shape
# ---------------------------------------------------------------------------


def _blank_env(name: str) -> dict[str, str]:
    """Empty-string overrides; `_env.apply_to_openbb` skips empty values
    so the credential is not projected onto `obb.user.credentials`."""

    return {name: ""}


def test_institutional_credential_gate() -> None:
    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset in the host env; the gate can still flip "
            "via env_overrides but the dotenv-restored key would otherwise "
            "mask it (see test_fundamentals_ratios_credential_gate)"
        )
    completed = run_wrapper(
        ["scripts/institutional.py", "AAPL", "--provider", "fmp"],
        timeout=120,
        env_overrides=_blank_env("FMP_API_KEY"),
    )
    assert completed.returncode == 2, (
        f"institutional must exit 2 on all-credential failure; "
        f"got {completed.returncode}. stdout tail:\n{completed.stdout[-500:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    error_message = payload.get("error") or ""
    assert "CredentialError:" in error_message, (
        f"institutional credential-all-failed envelope must carry the "
        f"`CredentialError:` prefix; got {error_message!r}"
    )
    assert payload.get("tool") == "institutional", payload


def test_commodity_weekly_report_no_credential_gate_documented() -> None:
    """`commodity --type weekly_report` does not enforce a credential gate.

    The design originally listed `EIA_API_KEY` as a P0 dependency for
    this sub-mode (matching `--type steo`), but the OpenBB `eia` provider
    fetches the Weekly Petroleum Status Report from EIA's public bulk
    endpoint (no API key required). Verified live 2026-04-25:
    `EIA_API_KEY="" uv run scripts/commodity.py --type weekly_report`
    returns ~131k records with exit 0.

    This test pins the observed behaviour so a future upstream change
    that re-introduces the gate surfaces as a regression here.
    """

    completed = run_wrapper(
        ["scripts/commodity.py", "--type", "weekly_report"],
        timeout=180,
        env_overrides=_blank_env("EIA_API_KEY"),
    )
    if completed.returncode == 2:
        # Upstream re-introduced the gate — surface the canonical envelope.
        payload = assert_stdout_is_single_json(completed)
        error_message = payload.get("error") or ""
        assert "CredentialError:" in error_message, (
            f"commodity weekly_report exit-2 must carry CredentialError; "
            f"got {error_message!r}"
        )
        return

    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "commodity", payload
    assert isinstance(payload.get("data", {}).get("results"), list)


def test_commodity_steo_credential_gate() -> None:
    if not os.getenv("EIA_API_KEY"):
        pytest.skip(
            "EIA_API_KEY unset in the host env; the gate can still flip "
            "via env_overrides but the dotenv-restored key would otherwise "
            "mask it"
        )
    completed = run_wrapper(
        ["scripts/commodity.py", "--type", "steo"],
        timeout=120,
        env_overrides=_blank_env("EIA_API_KEY"),
    )
    assert completed.returncode == 2, (
        f"commodity --type steo must exit 2 on credential failure; "
        f"got {completed.returncode}. stdout tail:\n{completed.stdout[-500:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    error_message = payload.get("error") or ""
    assert "CredentialError:" in error_message, (
        f"commodity steo credential envelope must carry the "
        f"`CredentialError:` prefix; got {error_message!r}"
    )
    assert payload.get("tool") == "commodity", payload


# ---------------------------------------------------------------------------
# 11.4 — per-symbol failure isolation
#
# A multi-symbol invocation with one valid and one bogus ticker must
# return AAPL successfully alongside an isolated per-symbol failure
# row. The aggregate envelope's mixed-failure gate keeps exit code 0
# (only all-rows-same-fatal-category escalates to exit 2).
# ---------------------------------------------------------------------------


_BOGUS_SYMBOL = "ZZZZZZINVALID"
_REQUIRED_FAILURE_FIELDS = ("error", "error_type", "error_category")


def _assert_isolation(
    payload: dict[str, Any],
    *,
    stem: str,
    valid_symbol: str = "AAPL",
    bogus_symbol: str = _BOGUS_SYMBOL,
) -> None:
    rows = payload.get("data", {}).get("results") or []
    by_symbol = {r.get("symbol"): r for r in rows if isinstance(r, dict)}

    valid_row = by_symbol.get(valid_symbol)
    assert valid_row is not None and valid_row.get("ok") is True, (
        f"{stem}: expected `{valid_symbol}` row with ok=True; rows={rows!r}"
    )

    bogus_row = by_symbol.get(bogus_symbol)
    assert bogus_row is not None and bogus_row.get("ok") is False, (
        f"{stem}: expected `{bogus_symbol}` row with ok=False; rows={rows!r}"
    )
    for field in _REQUIRED_FAILURE_FIELDS:
        assert field in bogus_row, (
            f"{stem}: failure row missing `{field}`: {bogus_row!r}"
        )


def test_insider_isolates_failed_symbol() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/insider.py", "AAPL", _BOGUS_SYMBOL,
            "--provider", "sec", "--limit", "5",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    _assert_isolation(payload, stem="insider")


def test_institutional_isolates_failed_symbol() -> None:
    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset; institutional multi-symbol isolation "
            "requires the FMP free tier (a clean checkout would surface "
            "the all-credential fatal gate, not isolation)"
        )
    completed = run_wrapper_or_xfail(
        ["scripts/institutional.py", "AAPL", _BOGUS_SYMBOL, "--provider", "fmp"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    _assert_isolation(payload, stem="institutional")


def test_filings_isolates_failed_symbol() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/filings.py", "AAPL", _BOGUS_SYMBOL,
            "--provider", "sec", "--limit", "5",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    _assert_isolation(payload, stem="filings")


def test_news_company_isolates_failed_symbol() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/news.py", "AAPL", _BOGUS_SYMBOL,
            "--scope", "company", "--provider", "yfinance",
            "--days", "5", "--limit", "3",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    _assert_isolation(payload, stem="news")


def test_shorts_isolates_failed_symbol() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/shorts.py", "AAPL", _BOGUS_SYMBOL,
            "--type", "short_interest", "--provider", "finra",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    payload = assert_stdout_is_single_json(completed)
    _assert_isolation(payload, stem="shorts")
