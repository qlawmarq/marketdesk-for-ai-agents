"""Integration coverage for `scripts/momentum.py` across every indicator.

Parametrizes across clenow, rsi, macd, cones, and adx using yfinance as the
price source. Asserts the shared envelope, happy-path success, and indicator
semantics: RSI within [0, 100], ADX within [0, 100], MACD components finite,
clenow factor / r^2 finite, and every requested symbol represented.
"""

from __future__ import annotations

import math
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


INDICATORS: list[str] = ["clenow", "rsi", "macd", "cones", "adx"]


_CONES_LABEL_KEYS = {"window", "model", "metric", "label", "category"}


def _looks_like_label(key: str) -> bool:
    """Identify cones columns that are legitimately non-numeric labels."""

    if not isinstance(key, str):
        return False
    return key.lower() in _CONES_LABEL_KEYS


def _assert_envelope(payload: Any, *, indicator: str) -> list[dict[str, Any]]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "momentum", payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    assert data.get("indicator") == indicator, payload
    results = data.get("results")
    assert isinstance(results, list) and results, f"results must be non-empty; got {results!r}"
    return results


def _assert_strict_json_number(value: Any, *, name: str) -> None:
    """Req 1.1 / 8.3: numeric fields must arrive as JSON numbers or null.

    Strings that happen to parse as floats are a regression — the wrapper's
    responsibility is to coerce NumPy/pandas scalars to Python floats before
    emit so agents can compare without re-parsing. A `null` is accepted
    because Req 1.2 maps NaN / None / parse-failure to null (data missing,
    not an error).
    """

    if value is None:
        return
    assert not isinstance(value, bool), (
        f"{name}: expected a JSON number or null, got bool {value!r}"
    )
    assert not isinstance(value, str), (
        f"{name}: expected a JSON number or null, got string {value!r} "
        f"(numpy/pandas scalar was not coerced to float before emit)"
    )
    assert isinstance(value, (int, float)), (
        f"{name}: expected a JSON number or null, got "
        f"{type(value).__name__}={value!r}"
    )


def _assert_finite_number(value: Any, *, name: str, low: float, high: float) -> None:
    _assert_strict_json_number(value, name=name)
    assert value is not None, f"{name}: expected a finite number, got null"
    assert_finite_in_range(value, low=low, high=high, name=name)


@pytest.mark.parametrize("indicator", INDICATORS)
def test_momentum_single_symbol_semantics(indicator: str) -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/momentum.py",
            _SINGLE_SYMBOL,
            "--indicator",
            indicator,
            "--provider",
            "yfinance",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"momentum.py --indicator {indicator} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, indicator=indicator)

    assert len(results) == 1
    row = results[0]
    assert row.get("symbol") == _SINGLE_SYMBOL
    assert row.get("ok") is True, f"indicator {indicator} failed: {row!r}"

    if indicator == "rsi":
        _assert_strict_json_number(row.get("rsi"), name="rsi")
        _assert_finite_number(row.get("rsi"), name="rsi", low=0.0, high=100.0)
    elif indicator == "clenow":
        # Req 1.1: Clenow numeric fields must be JSON numbers, never strings.
        for key in ("momentum_factor", "r_squared", "fit_coef"):
            _assert_strict_json_number(row.get(key), name=f"clenow.{key}")
        _assert_finite_number(
            row.get("momentum_factor"), name="momentum_factor", low=-1e6, high=1e6
        )
        _assert_finite_number(row.get("r_squared"), name="r_squared", low=0.0, high=1.0)
        _assert_strict_json_number(row.get("rank"), name="clenow.rank")
    elif indicator == "adx":
        adx_value = None
        for key, value in row.items():
            if not isinstance(key, str) or not key.upper().startswith("ADX_"):
                continue
            # Req 8.3 regression: ADX_* components must be JSON numbers.
            _assert_strict_json_number(value, name=key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                adx_value = value
                break
        assert adx_value is not None, f"no ADX_* column on adx row: {row!r}"
        _assert_finite_number(adx_value, name="adx", low=0.0, high=100.0)
    elif indicator == "macd":
        populated = 0
        for key, value in row.items():
            if not isinstance(key, str) or "macd" not in key.lower():
                continue
            # Req 8.3 regression: MACD components emitted as JSON numbers.
            _assert_strict_json_number(value, name=key)
            if value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            _assert_finite_number(value, name=key, low=-1e6, high=1e6)
            populated += 1
        assert populated > 0, f"no finite MACD components on row: {row!r}"
    elif indicator == "cones":
        cones_records = row.get("records")
        assert isinstance(cones_records, list) and cones_records, (
            f"cones records must be non-empty; got {cones_records!r}"
        )
        # Req 8.3 regression: every numeric cell in the multi-row cones
        # payload must also be a JSON number (never a string).
        for record_index, cones_row in enumerate(cones_records):
            assert isinstance(cones_row, dict), (
                f"cones record {record_index} must be dict; got {cones_row!r}"
            )
            for key, value in cones_row.items():
                if isinstance(value, str) and not _looks_like_label(key):
                    raise AssertionError(
                        f"cones[{record_index}].{key}: expected a JSON number, "
                        f"got string {value!r}"
                    )


def test_momentum_clenow_multi_symbol_integrity() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/momentum.py",
            *_MULTI_SYMBOLS,
            "--indicator",
            "clenow",
            "--provider",
            "yfinance",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"momentum.py multi-symbol exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    results = _assert_envelope(payload, indicator="clenow")

    assert_symbols_present(results, expected=_MULTI_SYMBOLS, symbol_key="symbol")
    for row in results:
        assert row.get("ok") is True, f"row failed: {row!r}"
        for key in ("momentum_factor", "r_squared", "fit_coef"):
            _assert_strict_json_number(row.get(key), name=f"{row.get('symbol')}.{key}")

    # Req 7.2 / 8.3: partial failures (if any) must surface via top-level
    # `warnings[]` with the shared `{symbol, error, error_category}` schema —
    # never a wrapper-specific shape. No failures on this happy path, but
    # validate schema integrity when the key is present.
    warnings = payload.get("warnings")
    if warnings is not None:
        assert isinstance(warnings, list) and warnings, (
            f"warnings key must be absent or a non-empty list; got {warnings!r}"
        )
        for index, entry in enumerate(warnings):
            assert isinstance(entry, dict), (
                f"warnings[{index}] must be dict; got {entry!r}"
            )
            for required in ("symbol", "error", "error_category"):
                assert required in entry, (
                    f"warnings[{index}] missing `{required}`: {entry!r}"
                )
