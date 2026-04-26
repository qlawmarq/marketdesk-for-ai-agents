"""Integration test: every non-underscore wrapper under `scripts/`
responds to `--help` with exit code 0 and non-empty stdout.

New wrappers are picked up automatically via the `WRAPPERS` glob in
`tests/integration/conftest.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import WRAPPERS, run_wrapper

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("script", WRAPPERS, ids=lambda p: p.stem)
def test_wrapper_help_exits_zero_with_nonempty_stdout(script: Path) -> None:
    completed = run_wrapper([str(script), "--help"])

    assert completed.returncode == 0, (
        f"{script.name} --help exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    assert completed.stdout.strip() != "", (
        f"{script.name} --help produced empty stdout"
    )
