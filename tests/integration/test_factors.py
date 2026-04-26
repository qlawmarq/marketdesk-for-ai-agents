"""Integration coverage for ``scripts/factors.py``.

Two happy-path subprocess runs against the key-free famafrench provider:
one with no defaults overridden (asserts the full
``defaults_applied == ["region", "frequency", "factor"]`` echo) and one
with every default overridden (asserts an empty ``defaults_applied``).

The parametrized contract suite already covers a fully-explicit
``--region america --frequency monthly`` invocation; this file adds the
dedicated assertions on the ``defaults_applied`` metadata echo, which
the generic table cannot express without leaking factors-aware logic
into the shared invariants.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


def test_factors_without_args_records_every_default_applied() -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/factors.py"],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"factors.py exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)
    assert payload.get("tool") == "factors", payload

    data = payload.get("data")
    assert isinstance(data, dict), payload
    assert data.get("region") == "america", payload
    assert data.get("frequency") == "monthly", payload
    assert data.get("factor") == "5_factors", payload
    assert data.get("defaults_applied") == ["region", "frequency", "factor"], payload

    results = data.get("results")
    assert isinstance(results, list) and results, (
        f"expected non-empty famafrench rows; payload={payload!r}"
    )


def test_factors_with_full_overrides_records_no_defaults_applied() -> None:
    completed = run_wrapper_or_xfail(
        [
            "scripts/factors.py",
            "--region", "america",
            "--frequency", "monthly",
            "--factor", "5_factors",
        ],
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"factors.py exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )

    payload = assert_stdout_is_single_json(completed)
    data = payload.get("data")
    assert isinstance(data, dict), payload
    assert data.get("defaults_applied") == [], payload
    assert data.get("region") == "america", payload
    assert data.get("frequency") == "monthly", payload
    assert data.get("factor") == "5_factors", payload
