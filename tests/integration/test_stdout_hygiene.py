"""Integration test: wrappers emit a single JSON document on stdout.

Protects the pure-stdout-JSON regression contract (Req 6.1–6.3):
- Entire stdout (after trimming one trailing newline) must parse via a
  single `json.loads`.
- A non-JSON line on stdout fails the test with the offending line
  surfaced by `assert_stdout_is_single_json`.
- Nothing is asserted about `result.stderr`.

The canonical invocation set is deliberately small; breadth across
every wrapper is provided by `test_cli_help.py`.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


# Canonical invocations chosen to cover both the provider-backed
# JSON-emitting path (`quote.py`, `historical.py`). `--help` is
# intentionally excluded here: argparse prints plaintext usage to stdout,
# which cannot parse as JSON — the `--help` contract is covered by
# `test_cli_help.py`.
_CANONICAL_INVOCATIONS: list[list[str]] = [
    ["scripts/quote.py", "AAPL", "--provider", "yfinance"],
    [
        "scripts/historical.py",
        "AAPL",
        "--start",
        "2025-01-01",
        "--provider",
        "yfinance",
    ],
]


@pytest.mark.parametrize(
    "argv",
    _CANONICAL_INVOCATIONS,
    ids=lambda argv: " ".join(argv),
)
def test_wrapper_stdout_is_a_single_json_document(argv: list[str]) -> None:
    completed = run_wrapper_or_xfail(argv)

    assert_stdout_is_single_json(completed)
