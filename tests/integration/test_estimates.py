"""Integration coverage for `scripts/estimates.py` across retained sub-modes.

The forward_eps and forward_ebitda sub-modes were removed because no free-tier
provider covers them. This file exercises the two retained sub-modes:
consensus (yfinance) and price_target (finviz).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from tests.integration._sanity import assert_symbols_present
from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_SINGLE_SYMBOL = "AAPL"
_MULTI_SYMBOLS = ("AAPL", "MSFT")


SUB_MODES: list[tuple[str, str]] = [
    ("consensus", "yfinance"),
    ("price_target", "finviz"),
]


def _assert_envelope(payload: Any, *, sub_type: str, provider: str) -> list[dict[str, Any]]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "estimates", payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    assert data.get("type") == sub_type, payload
    assert data.get("provider") == provider, payload
    results = data.get("results")
    assert isinstance(results, list) and results, f"results must be non-empty; got {results!r}"
    return results


def _finite_numbers(record: dict[str, Any]):
    for key, value in record.items():
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        if isinstance(value, (int, float)):
            yield key, value


@pytest.mark.parametrize(("sub_type", "provider"), SUB_MODES, ids=[m[0] for m in SUB_MODES])
def test_estimates_single_symbol_happy_path(sub_type: str, provider: str) -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/estimates.py",
            _SINGLE_SYMBOL,
            "--type",
            sub_type,
            "--provider",
            provider,
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"estimates.py --type {sub_type} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type=sub_type, provider=provider)

    assert len(results) == 1
    row = results[0]
    assert row.get("symbol") == _SINGLE_SYMBOL
    assert row.get("ok") is True, f"sub-mode {sub_type} failed: {row!r}"
    records = row.get("records")
    assert isinstance(records, list) and records, f"expected records for {sub_type}; got {records!r}"

    populated = 0
    for record in records:
        for key, value in _finite_numbers(record):
            # Consensus: EPS / revenue estimates may be negative for losses.
            # Price targets must be positive.
            if sub_type == "price_target" and "price" in key.lower():
                assert value > 0, f"price_target {key}={value!r} is not positive"
            populated += 1
    assert populated > 0, f"expected at least one finite numeric field for {sub_type}; records={records!r}"


def test_estimates_consensus_multi_symbol_integrity() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/estimates.py",
            *_MULTI_SYMBOLS,
            "--type",
            "consensus",
            "--provider",
            "yfinance",
        ],
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"estimates.py multi-symbol exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, sub_type="consensus", provider="yfinance")

    assert_symbols_present(results, expected=_MULTI_SYMBOLS, symbol_key="symbol")
    for row in results:
        assert row.get("ok") is True, f"row failed: {row!r}"
