"""Integration coverage for `scripts/etf.py` across retained sub-modes.

The equity_exposure sub-mode was removed (no free provider and no agent
need). Both ``holdings`` and ``sectors`` are gated on FMP_API_KEY — live
verification showed the free-tier SEC route (``obb.etf.nport_disclosure``)
crashes on upstream data-model validation for XLK/SPY/VTI, and tmx returns
empty for non-Canadian tickers, so no reliable free alternative exists for
US holdings or sectors today.
"""

from __future__ import annotations

import math
import os
from typing import Any

import pytest

from tests.integration._sanity import (
    assert_finite_in_range,
    assert_symbols_present,
)
from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_SINGLE_ETF = "SPY"
_HOLDINGS_ETF = "XLK"
_MULTI_ETFS = ("SPY", "QQQ")


SUB_MODES: list[tuple[str, str]] = [
    ("info", "yfinance"),
    ("holdings", "fmp"),
    ("sectors", "fmp"),
]


_FMP_GATED = {"holdings", "sectors"}
_SEC_USER_AGENT_GATED: frozenset[str] = frozenset()


def _assert_envelope(payload: Any, *, sub_type: str, provider: str) -> list[dict[str, Any]]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "etf", payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    assert data.get("type") == sub_type, payload
    assert data.get("provider") == provider, payload
    results = data.get("results")
    assert isinstance(results, list) and results, f"results must be non-empty; got {results!r}"
    return results


@pytest.mark.parametrize(("sub_type", "provider"), SUB_MODES, ids=[m[0] for m in SUB_MODES])
def test_etf_single_symbol_happy_path(sub_type: str, provider: str) -> None:
    if sub_type in _FMP_GATED and not os.environ.get("FMP_API_KEY"):
        pytest.skip(
            f"{sub_type} sub-mode has no free-tier provider; "
            "skipped without FMP_API_KEY"
        )
    if sub_type in _SEC_USER_AGENT_GATED and not os.environ.get("SEC_USER_AGENT"):
        pytest.skip(
            f"{sub_type} via sec requires SEC_USER_AGENT to be set"
        )

    symbol = _HOLDINGS_ETF if sub_type == "holdings" else _SINGLE_ETF
    completed = run_wrapper_or_xfail(
        [
            "scripts/etf.py",
            symbol,
            "--type",
            sub_type,
            "--provider",
            provider,
        ],
        timeout=120,
    )
    # FMP's free tier covers neither etf.holdings nor etf.sectors. A free key
    # surfaces as rc=2 + top-level `PlanError:` (error_category=plan_insufficient,
    # spec 2026-04-24-bugfix-2 task 2); a missing key surfaces as rc=2 + top-level
    # `CredentialError:`. Either path means "no paid subscription available" for
    # this happy-path slot, so skip. Dedicated regression coverage lives in
    # `test_etf_holdings_plan_insufficient_on_free_tier` and
    # `test_etf_credential_error_on_free_tier_402`.
    if completed.returncode != 0 and sub_type in _FMP_GATED:
        payload = assert_stdout_is_single_json(completed)
        if isinstance(payload, dict):
            err = str(payload.get("error") or "")
            if err.startswith(("CredentialError:", "PlanError:")):
                pytest.skip(
                    f"{sub_type} via fmp requires a paid FMP subscription tier "
                    f"(free tier returns 402 Restricted Endpoint)"
                )
    assert completed.returncode == 0, (
        f"etf.py --type {sub_type} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type=sub_type, provider=provider)

    assert len(results) == 1
    row = results[0]
    assert row.get("symbol") == symbol
    if not row.get("ok"):
        raise AssertionError(f"sub-mode {sub_type} failed: {row!r}")
    records = row.get("records")
    assert isinstance(records, list) and records, f"expected records for {sub_type}"

    if sub_type == "holdings":
        seen_symbols: set[str] = set()
        for index, record in enumerate(records):
            for weight_key in ("weight", "percentage_of_portfolio", "allocation"):
                value = record.get(weight_key)
                if value is None:
                    continue
                if isinstance(value, float) and math.isnan(value):
                    continue
                if isinstance(value, (int, float)):
                    # SEC / OpenBB may emit weights either as a 0-1 fraction
                    # or as 0-100 percentages depending on provider; accept
                    # either while rejecting NaN/inf and negative / >1.0x values.
                    assert_finite_in_range(
                        value,
                        low=0.0,
                        high=100.0,
                        name=f"holdings[{index}].{weight_key}",
                    )

            holding_symbol = record.get("symbol")
            if isinstance(holding_symbol, str) and holding_symbol.strip():
                assert holding_symbol not in seen_symbols, (
                    f"duplicated holding symbol {holding_symbol!r}"
                )
                seen_symbols.add(holding_symbol)


_CREDENTIAL_GATED_ETF_SUB_MODES: tuple[str, ...] = ("holdings", "sectors")


@pytest.mark.parametrize("sub_type", _CREDENTIAL_GATED_ETF_SUB_MODES)
def test_etf_credential_error_without_fmp_key(sub_type: str) -> None:
    """No `FMP_API_KEY` → wrapper exits non-zero with a top-level
    ``CredentialError:`` payload (Req 1.4 case (a))."""

    symbol = _HOLDINGS_ETF if sub_type == "holdings" else _SINGLE_ETF
    completed = run_wrapper_or_xfail(
        [
            "scripts/etf.py",
            symbol,
            "--type",
            sub_type,
            "--provider",
            "fmp",
        ],
        timeout=120,
        env_overrides={"FMP_API_KEY": ""},
    )
    assert completed.returncode != 0, (
        f"expected non-zero exit for unset FMP_API_KEY; got rc={completed.returncode}; "
        f"stdout head:\n{completed.stdout[:2000]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    error = payload.get("error")
    assert isinstance(error, str) and error.startswith("CredentialError:"), (
        f"expected top-level error starting with 'CredentialError:'; got {error!r}"
    )
    # Companion guardrail for the plan_insufficient split (Req 4.1 / 4.2):
    # the unset-key path must remain `credential`, not drift into
    # `plan_insufficient`.
    assert payload.get("error_category") == "credential", payload
    assert payload.get("tool") == "etf", payload


def test_etf_holdings_plan_insufficient_on_free_tier() -> None:
    """Free-tier FMP key → 402 Restricted → wrapper exits non-zero with
    a top-level ``PlanError:`` payload tagged ``plan_insufficient`` —
    distinct from the ``CredentialError:`` / ``credential`` path that
    fires when `FMP_API_KEY` is unset.

    Reason for the split (Req 4.1 / 4.2): FMP raises the same
    `UnauthorizedError` for 401 (key missing/invalid) and 402 (plan
    insufficient); only the message distinguishes them. Agents recover
    differently — rotate credentials vs. upgrade the subscription —
    so the two categories must not collapse into one.
    """

    if not os.environ.get("FMP_API_KEY"):
        pytest.skip(
            "requires FMP_API_KEY to be set so the provider can return "
            "402 Restricted; use test_etf_credential_error_without_fmp_key "
            "when unset"
        )

    completed = run_wrapper_or_xfail(
        [
            "scripts/etf.py",
            _HOLDINGS_ETF,
            "--type",
            "holdings",
            "--provider",
            "fmp",
        ],
        timeout=120,
    )
    if completed.returncode == 0:
        pytest.skip(
            "holdings succeeded under the current FMP tier — this "
            "regression only fires on the free tier (paid tiers pass). "
            "test_etf_credential_error_without_fmp_key still guards the "
            "unset-key credential branch."
        )
    assert completed.returncode == 2, (
        f"expected exit code 2 for free-tier 402 Restricted; got "
        f"rc={completed.returncode}; stdout head:\n{completed.stdout[:2000]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    error = payload.get("error")
    assert isinstance(error, str) and error.startswith("PlanError:"), (
        f"expected top-level error starting with 'PlanError:'; got {error!r}"
    )
    assert not error.startswith("CredentialError:"), (
        f"PlanError and CredentialError are parallel tokens and must not "
        f"coexist on one envelope; got {error!r}"
    )
    assert payload.get("error_category") == "plan_insufficient", payload
    assert payload.get("tool") == "etf", payload


def test_etf_mixed_success_plus_credential_failure_is_not_achievable() -> None:
    """Req 1.4 case (c) — mixed success + credential failure — is not
    achievable for `etf.py --type holdings/sectors`.

    The gated endpoints share a single process-level `FMP_API_KEY`, so
    every row in a multi-symbol invocation either all-succeeds or
    all-credential-fails. The unit-tier `test_common_aggregate.py`
    covers the mixed-success + credential-failure aggregator branch
    directly; the integration tier has no hook to reproduce a
    partial-success credential state against a real OpenBB endpoint.

    Recorded as N/A in the 02 verification report and guarded by an
    explicit skip so the intent stays visible in the test suite.
    """

    pytest.skip(
        "N/A: etf.py holdings/sectors cannot produce mixed success + "
        "credential failure against a real provider — FMP_API_KEY is "
        "process-scoped. Aggregator branch covered by "
        "tests/unit/test_common_aggregate.py instead."
    )


def test_etf_info_multi_symbol_integrity() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/etf.py",
            *_MULTI_ETFS,
            "--type",
            "info",
            "--provider",
            "yfinance",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"etf.py multi-symbol exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type="info", provider="yfinance")

    assert_symbols_present(results, expected=_MULTI_ETFS, symbol_key="symbol")
    for row in results:
        assert row.get("ok") is True, f"row failed: {row!r}"
