"""Offline self-test for the verification gate.

Each of the two gate checks is exercised against a temp-dir fake
repo so the real repo never needs to be intentionally broken. Every
case covers both the passing and the failing branch — a gate that is
always green would provide no safety.

Runs under the ``unit`` marker: no network, no mocking, deterministic.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.integration._verification_gate import (
    check_readme_rows_and_scripts_bijection,
    check_readme_verified_markers_resolve,
    extract_test_references,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake repo builders
# ---------------------------------------------------------------------------


def _write_readme(repo_root: Path, section_body: str) -> Path:
    readme = repo_root / "README.md"
    readme.write_text(
        "# Fake README\n\n"
        "### 1-1. Feature List\n\n"
        f"{section_body}\n\n"
        "### 1-2. TODO\n\n"
        "(none)\n",
        encoding="utf-8",
    )
    return readme


def _write_script(repo_root: Path, name: str) -> None:
    (repo_root / "scripts").mkdir(parents=True, exist_ok=True)
    (repo_root / "scripts" / f"{name}.py").write_text(
        '"""stub wrapper."""\n', encoding="utf-8"
    )


def _write_test_file(
    repo_root: Path, rel_path: str, function_names: list[str]
) -> Path:
    path = repo_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(
        f"def {name}() -> None:\n    pass" for name in function_names
    )
    path.write_text(body + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Check 1: README verified markers resolve
# ---------------------------------------------------------------------------


def test_marker_gate_passes_when_every_marker_resolves(tmp_path: Path) -> None:
    _write_test_file(
        tmp_path,
        "tests/integration/test_fake.py",
        ["test_alpha", "test_beta"],
    )
    readme_body = (
        "| Verified | `tests/integration/test_fake.py::test_alpha` |\n"
        "| Verified | `::test_beta` |\n"
    )
    readme = _write_readme(tmp_path, readme_body)
    assert check_readme_verified_markers_resolve(readme, tmp_path) == []


def test_marker_gate_expands_brace_shortcut(tmp_path: Path) -> None:
    _write_test_file(
        tmp_path,
        "tests/integration/test_fake.py",
        ["test_quote_aapl_envelope", "test_quote_aapl_records"],
    )
    readme_body = (
        "| Verified | "
        "`tests/integration/test_fake.py::test_quote_aapl_{envelope,records}` |\n"
    )
    readme = _write_readme(tmp_path, readme_body)
    assert check_readme_verified_markers_resolve(readme, tmp_path) == []


def test_marker_gate_rejects_reference_to_nonexistent_file(
    tmp_path: Path,
) -> None:
    readme_body = (
        "| Verified | `tests/integration/test_missing.py::test_alpha` |\n"
    )
    readme = _write_readme(tmp_path, readme_body)
    errors = check_readme_verified_markers_resolve(readme, tmp_path)
    assert any("test_missing.py" in e and "does not exist" in e for e in errors)


def test_marker_gate_rejects_reference_to_undefined_function(
    tmp_path: Path,
) -> None:
    _write_test_file(
        tmp_path, "tests/integration/test_fake.py", ["test_alpha"]
    )
    readme_body = (
        "| Verified | `tests/integration/test_fake.py::test_beta` |\n"
    )
    readme = _write_readme(tmp_path, readme_body)
    errors = check_readme_verified_markers_resolve(readme, tmp_path)
    assert any("test_beta" in e and "not defined" in e for e in errors)


def test_marker_gate_flags_readme_with_no_markers(tmp_path: Path) -> None:
    readme = _write_readme(tmp_path, "| Feature | Script |\n| foo | foo.py |\n")
    errors = check_readme_verified_markers_resolve(readme, tmp_path)
    assert errors and "no `tests/" in errors[0]


def test_extract_test_references_handles_continuation_and_braces() -> None:
    text = (
        "row 1: `tests/integration/test_fake.py::test_alpha_{a,b}`, "
        "`::test_gamma`.\n"
        "row 2 (unrelated line, no prior file): `::test_orphan` dropped.\n"
        "row 3: `tests/unit/test_other.py::test_delta`\n"
    )
    refs = extract_test_references(text)
    assert ("tests/integration/test_fake.py", "test_alpha_a") in refs
    assert ("tests/integration/test_fake.py", "test_alpha_b") in refs
    assert ("tests/integration/test_fake.py", "test_gamma") in refs
    assert ("tests/unit/test_other.py", "test_delta") in refs
    # `::test_orphan` has no file context on its line and is dropped.
    assert all(func != "test_orphan" for _, func in refs)


# ---------------------------------------------------------------------------
# Check 2: README feature-matrix ↔ scripts/ bijection
# ---------------------------------------------------------------------------


def test_bijection_gate_passes_when_readme_and_scripts_agree(
    tmp_path: Path,
) -> None:
    _write_script(tmp_path, "alpha")
    _write_script(tmp_path, "beta")
    # underscore-prefixed helpers are excluded from the bijection
    _write_script(tmp_path, "_helper")

    section = textwrap.dedent(
        """\
        | # | Feature | Script |
        | --- | --- | --- |
        | 1 | Alpha | `scripts/alpha.py` |
        | 2 | Beta  | `scripts/beta.py` |
        """
    )
    readme = _write_readme(tmp_path, section)
    assert check_readme_rows_and_scripts_bijection(readme, tmp_path) == []


def test_bijection_gate_detects_script_missing_from_readme(
    tmp_path: Path,
) -> None:
    _write_script(tmp_path, "alpha")
    _write_script(tmp_path, "orphaned")
    section = (
        "| # | Script |\n| --- | --- |\n| 1 | `scripts/alpha.py` |\n"
    )
    readme = _write_readme(tmp_path, section)
    errors = check_readme_rows_and_scripts_bijection(readme, tmp_path)
    assert any(
        "scripts/orphaned.py" in e and "not referenced" in e for e in errors
    ), errors


def test_bijection_gate_detects_readme_row_without_backing_script(
    tmp_path: Path,
) -> None:
    _write_script(tmp_path, "alpha")
    section = (
        "| # | Script |\n| --- | --- |\n"
        "| 1 | `scripts/alpha.py` |\n"
        "| 2 | `scripts/phantom.py` |\n"
    )
    readme = _write_readme(tmp_path, section)
    errors = check_readme_rows_and_scripts_bijection(readme, tmp_path)
    assert any(
        "scripts/phantom.py" in e and "no such file" in e for e in errors
    ), errors


def test_bijection_gate_reports_missing_feature_list_section(
    tmp_path: Path,
) -> None:
    _write_script(tmp_path, "alpha")
    (tmp_path / "README.md").write_text("# README without section\n", encoding="utf-8")
    errors = check_readme_rows_and_scripts_bijection(
        tmp_path / "README.md", tmp_path
    )
    assert errors and "1-1. Feature List" in errors[0]
