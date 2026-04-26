"""Completion-gate tests for README ↔ test-suite ↔ scripts/ consistency.

Two assertions enforce README integrity mechanically so the README
cannot silently drift away from the suite it documents:

  1. ``test_readme_verified_markers_resolve`` — every
     ``tests/integration/...::test_*`` reference in ``README.md``
     points at a file that exists and a function defined in it.
  2. ``test_readme_rows_and_scripts_are_in_bijection`` — the set of
     scripts named in README §1-1 equals the set of ``scripts/*.py``
     files on disk (excluding ``_*`` helpers).

Pure repo-state introspection: no network, no mocking, no writes. The
offline self-test under ``tests/unit/test_verification_gate_self.py``
exercises each failure mode against a temp-dir fake repo so the real
repo never has to be intentionally broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._verification_gate import (
    check_readme_rows_and_scripts_bijection,
    check_readme_verified_markers_resolve,
)

pytestmark = pytest.mark.integration


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_readme_verified_markers_resolve() -> None:
    readme_path = _REPO_ROOT / "README.md"
    errors = check_readme_verified_markers_resolve(readme_path, _REPO_ROOT)
    assert not errors, "README verified-marker resolution failed:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def test_readme_rows_and_scripts_are_in_bijection() -> None:
    readme_path = _REPO_ROOT / "README.md"
    errors = check_readme_rows_and_scripts_bijection(readme_path, _REPO_ROOT)
    assert not errors, "README↔scripts bijection failed:\n" + "\n".join(
        f"  - {e}" for e in errors
    )
