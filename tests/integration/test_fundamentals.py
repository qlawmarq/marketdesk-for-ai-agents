"""Integration coverage for `scripts/fundamentals.py` across every sub-mode.

Every case exercises the wrapper as a subprocess against a free-tier provider
(yfinance, now the default for every retained sub-mode) and asserts the
shared envelope, happy-path success, multi-symbol integrity, and numeric
sanity. The `ratios` sub-mode additionally asserts the unit-tagged
`{value, unit}` payload shape and the `|value| <= 5 when unit == "decimal"`
invariant.
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


_SINGLE_SYMBOL = "AAPL"
_MULTI_SYMBOLS = ("AAPL", "MSFT")


SUB_MODES: list[tuple[str, str]] = [
    ("overview", "yfinance"),
    ("income", "yfinance"),
    ("balance", "yfinance"),
    ("cash", "yfinance"),
    # `ratios` stays on fmp: OpenBB does not expose the ratios endpoint via
    # yfinance, so task 4.1 could not migrate this sub-mode. The wrapper's
    # unit-normalization pipeline still runs under fmp.
    ("ratios", "fmp"),
    ("metrics", "yfinance"),
]


# Sub-modes whose default provider was migrated from fmp to yfinance in task 4.1.
# The default-provider regression exercises the wrapper with `--provider`
# omitted to lock in the new free-tier default.
_MIGRATED_DEFAULT_SUB_MODES: tuple[str, ...] = ("income", "balance", "cash")


# Sub-modes that must still be gated on a paid FMP key because no free-tier
# OpenBB provider exposes the endpoint.
_FMP_GATED: frozenset[str] = frozenset({"ratios"})


def _assert_envelope(payload: Any, *, sub_type: str, provider: str) -> list[dict[str, Any]]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "fundamentals", payload
    assert "collected_at" in payload, payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be a dict, got {type(data).__name__}"
    assert data.get("type") == sub_type, payload
    assert data.get("provider") == provider, payload
    results = data.get("results")
    assert isinstance(results, list) and results, f"results must be non-empty list; got {results!r}"
    return results


def _iter_numeric(records: list[dict[str, Any]]):
    """Yield populated numeric (key, value) pairs; NaN/None are skipped.

    Sparse providers (e.g., yfinance) emit NaN for statement lines that do
    not apply; those are absent data, not defects.
    """
    for record in records:
        for key, value in record.items():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            if isinstance(value, (int, float)):
                yield key, value
            elif isinstance(value, dict) and "value" in value:
                inner = value["value"]
                if isinstance(inner, bool) or inner is None:
                    continue
                if isinstance(inner, float) and math.isnan(inner):
                    continue
                if isinstance(inner, (int, float)):
                    yield key, inner


@pytest.mark.parametrize(("sub_type", "provider"), SUB_MODES, ids=[m[0] for m in SUB_MODES])
def test_fundamentals_single_symbol_happy_path(sub_type: str, provider: str) -> None:
    if sub_type in _FMP_GATED and not os.environ.get("FMP_API_KEY"):
        pytest.skip(
            f"{sub_type} sub-mode has no free-tier provider; "
            "skipped without FMP_API_KEY"
        )
    completed = run_wrapper_or_xfail(
        [
            "scripts/fundamentals.py",
            _SINGLE_SYMBOL,
            "--type",
            sub_type,
            "--provider",
            provider,
            "--limit",
            "3",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"fundamentals.py --type {sub_type} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type=sub_type, provider=provider)

    assert len(results) == 1, f"expected one row for single symbol; got {len(results)}"
    row = results[0]
    assert row.get("symbol") == _SINGLE_SYMBOL
    assert row.get("ok") is True, f"sub-mode {sub_type} failed: {row!r}"
    records = row.get("records")
    assert isinstance(records, list) and records, (
        f"expected non-empty records for {sub_type}; got {records!r}"
    )

    numeric_count = 0
    for key, value in _iter_numeric(records):
        assert_finite_in_range(
            value,
            low=-1e15,
            high=1e15,
            name=f"{sub_type}.{key}",
        )
        numeric_count += 1
    assert numeric_count > 0, (
        f"expected at least one populated numeric field for {sub_type}; records={records!r}"
    )


def test_fundamentals_ratios_unit_tag_invariant() -> None:
    """Every ratio field must carry `{value, unit}` and satisfy the
    `|value| <= 5 when unit == "decimal"` invariant.

    OpenBB does not expose the ratios endpoint via yfinance, so this
    case remains FMP_API_KEY-gated; the unit-normalization pipeline
    still runs under fmp.
    """

    if not os.environ.get("FMP_API_KEY"):
        pytest.skip("ratios sub-mode requires FMP_API_KEY (no free-tier OpenBB provider)")
    completed = run_wrapper_or_xfail(
        [
            "scripts/fundamentals.py",
            _SINGLE_SYMBOL,
            "--type",
            "ratios",
            "--provider",
            "fmp",
            "--limit",
            "3",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"fundamentals.py --type ratios exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type="ratios", provider="fmp")
    row = results[0]
    assert row.get("ok") is True, f"ratios sub-mode failed: {row!r}"
    records = row.get("records")
    assert isinstance(records, list) and records, (
        f"expected non-empty records for ratios; got {records!r}"
    )

    tagged_fields = 0
    for index, record in enumerate(records):
        for key, value in record.items():
            if not isinstance(value, dict):
                continue
            assert set(value).issuperset({"value", "unit"}), (
                f"ratios[{index}].{key} missing unit tag: {value!r}"
            )
            unit = value["unit"]
            assert unit in {"decimal", "percent", "ratio"}, (
                f"ratios[{index}].{key} has unexpected unit {unit!r}"
            )
            inner = value["value"]
            if inner is None:
                continue
            if isinstance(inner, float) and math.isnan(inner):
                continue
            assert isinstance(inner, (int, float)) and not isinstance(inner, bool), (
                f"ratios[{index}].{key} value is not numeric: {inner!r}"
            )
            tagged_fields += 1
            if unit == "decimal":
                assert abs(inner) <= 5, (
                    f"ratios[{index}].{key} decimal value {inner!r} exceeds |value| <= 5 "
                    "(suspected percent not normalized to decimal)"
                )
    assert tagged_fields > 0, (
        f"expected at least one unit-tagged numeric ratio field; records={records!r}"
    )


@pytest.mark.parametrize("sub_type", _MIGRATED_DEFAULT_SUB_MODES)
def test_fundamentals_default_provider_is_free_tier(sub_type: str) -> None:
    """Regression: the sub-modes migrated in task 4.1 must run with `--provider`
    omitted (i.e. with no paid key) and report the yfinance default on the envelope.
    """

    completed = run_wrapper_or_xfail(
        [
            "scripts/fundamentals.py",
            _SINGLE_SYMBOL,
            "--type",
            sub_type,
            "--limit",
            "2",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"fundamentals.py --type {sub_type} (default provider) exited "
        f"{completed.returncode}; stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type=sub_type, provider="yfinance")
    assert results[0].get("ok") is True, f"sub-mode {sub_type} failed: {results[0]!r}"


def test_fundamentals_ratios_credential_error_without_fmp_key() -> None:
    """No `FMP_API_KEY` → ``fundamentals.py --type ratios`` exits
    non-zero with a top-level ``CredentialError:`` payload
    (Req 1.4 case (a) for ratios)."""

    completed = run_wrapper_or_xfail(
        [
            "scripts/fundamentals.py",
            _SINGLE_SYMBOL,
            "--type",
            "ratios",
            "--provider",
            "fmp",
            "--limit",
            "3",
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
    assert payload.get("tool") == "fundamentals", payload


def test_fundamentals_metrics_unit_tagging() -> None:
    """Req 2.5 — `fundamentals --type metrics` must tag every classifier-
    known numeric field with `{value, unit}` (`unit ∈ {decimal, percent,
    ratio, currency}`) and pass unknown fields through as bare numbers.

    Pins AAPL yfinance metrics so a silent mis-classification (e.g.
    currency totals tagged as `ratio`, or percent fields returning bare
    numbers) is caught at CI time rather than downstream in agent code.
    """

    completed = run_wrapper_or_xfail(
        [
            "scripts/fundamentals.py",
            _SINGLE_SYMBOL,
            "--type",
            "metrics",
            "--provider",
            "yfinance",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"fundamentals.py --type metrics exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type="metrics", provider="yfinance")
    row = results[0]
    assert row.get("ok") is True, f"metrics sub-mode failed: {row!r}"
    records = row.get("records")
    assert isinstance(records, list) and records, (
        f"expected non-empty records for metrics; got {records!r}"
    )
    record = records[0]

    expected_units = {
        "market_cap": "currency",
        "enterprise_value": "currency",
        "debt_to_equity": "percent",
        "dividend_yield": "percent",
        "gross_margin": "decimal",
        "payout_ratio": "decimal",
        "return_on_equity": "decimal",
        "pe_ratio": "ratio",
        "beta": "ratio",
    }
    for field, expected_unit in expected_units.items():
        cell = record.get(field)
        assert isinstance(cell, dict) and set(cell).issuperset({"value", "unit"}), (
            f"metrics.{field} must be a {{value, unit}} cell; got {cell!r}"
        )
        assert cell["unit"] == expected_unit, (
            f"metrics.{field} expected unit {expected_unit!r}; got {cell['unit']!r}"
        )
        inner = cell["value"]
        if inner is None:
            continue
        if isinstance(inner, float) and math.isnan(inner):
            continue
        assert isinstance(inner, (int, float)) and not isinstance(inner, bool), (
            f"metrics.{field} value is not numeric: {inner!r}"
        )

    # Passthrough regression: `book_value` is a per-share USD amount in
    # yfinance metrics and `overall_risk` is a discrete governance score.
    # Neither belongs in `METRIC_UNIT_MAP`, so the wrapper must emit them
    # as bare numbers (or None) rather than `{value, unit}` cells.
    for passthrough_field in ("book_value", "overall_risk"):
        if passthrough_field in record:
            bare = record[passthrough_field]
            assert not isinstance(bare, dict), (
                f"metrics.{passthrough_field} must passthrough as a bare "
                f"number; got tagged cell {bare!r}"
            )

    # Invariant: currency totals must never be mis-tagged as `ratio`.
    # Walk every tagged cell and assert that the canonical currency fields
    # never appear under `unit == "ratio"`.
    currency_fields = {"market_cap", "enterprise_value"}
    for key, cell in record.items():
        if isinstance(cell, dict) and cell.get("unit") == "ratio":
            assert key not in currency_fields, (
                f"metrics.{key} is a currency total mis-tagged as ratio: {cell!r}"
            )


def test_fundamentals_multi_symbol_integrity() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/fundamentals.py",
            *_MULTI_SYMBOLS,
            "--type",
            "overview",
            "--provider",
            "yfinance",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"fundamentals.py multi-symbol exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type="overview", provider="yfinance")

    assert_symbols_present(results, expected=_MULTI_SYMBOLS, symbol_key="symbol")
    for row in results:
        assert row.get("ok") is True, f"row failed: {row!r}"
