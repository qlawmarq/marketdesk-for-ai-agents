"""Integration coverage for ``scripts/options.py --type iv``.

Single happy-path subprocess run against the key-free ``yfinance``
provider, asserting the derived per-expiration row shape
(`{expiration, atm_iv, put_call_oi_ratio}`) plus the ``missing_fields``
echo under ``data``.

The parametrized contract suite already covers ``--type chain`` for
this wrapper; this file adds the dedicated assertion that the IV
derivation surfaces in the envelope, which the parametrized table
cannot express without leaking derivation-aware logic into the generic
suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_DERIVED_ROW_KEYS = {"expiration", "atm_iv", "put_call_oi_ratio"}


def test_options_iv_yfinance_derives_per_expiration_rows() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/options.py", "AAPL", "--type", "iv", "--provider", "yfinance"],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"options.py --type iv exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "options", payload

    data = payload.get("data")
    assert isinstance(data, dict), payload
    assert data.get("type") == "iv", payload
    assert data.get("provider") == "yfinance", payload
    assert data.get("symbol") == "AAPL", payload

    missing_fields = data.get("missing_fields")
    assert isinstance(missing_fields, list), payload

    results: list[dict[str, Any]] = data.get("results") or []
    assert isinstance(results, list), payload
    assert results, (
        f"expected at least one derived per-expiration row for AAPL; "
        f"missing_fields={missing_fields!r}"
    )

    for index, row in enumerate(results):
        assert isinstance(row, dict), (
            f"row [{index}] must be a dict; got {type(row).__name__}"
        )
        extras = set(row.keys()) - _DERIVED_ROW_KEYS
        assert not extras, (
            f"row [{index}] must only carry {_DERIVED_ROW_KEYS!r}; "
            f"unexpected keys: {sorted(extras)!r}"
        )
        assert isinstance(row["expiration"], str) and row["expiration"], (
            f"row [{index}] expiration must be an ISO date string; row={row!r}"
        )
        atm_iv = row["atm_iv"]
        assert atm_iv is None or isinstance(atm_iv, (int, float)), (
            f"row [{index}] atm_iv must be numeric or None; row={row!r}"
        )
        ratio = row["put_call_oi_ratio"]
        assert ratio is None or isinstance(ratio, (int, float)), (
            f"row [{index}] put_call_oi_ratio must be numeric or None; row={row!r}"
        )
