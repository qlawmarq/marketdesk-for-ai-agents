"""Repo-state introspection helpers for the verification-gate test.

Pure Python, stdlib only, no network, no writes, no mocking. Every
helper takes a `repo_root` (or a specific file path) so the offline
self-test under `tests/unit/` can exercise each failure mode against a
temp-dir fake repo without having to break the real repo.

The gate consists of two checks, each exposed as a function returning
a list of error messages (empty on pass):

  * ``check_readme_verified_markers_resolve`` — every
    ``tests/integration/...::test_*`` reference in ``README.md`` points
    to a test file that exists and a function defined in that file.
  * ``check_readme_rows_and_scripts_bijection`` — the set of scripts
    named in the README §1-1 feature matrix equals the set of
    ``scripts/*.py`` files on disk (excluding ``_*``).
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Check 1: README verified markers resolve
# ---------------------------------------------------------------------------


_FULL_REF = re.compile(
    r"(tests/(?:integration|unit)/[\w/]+\.py)::([A-Za-z_][\w]*)"
)
_CONT_REF = re.compile(r"(?<![/\w])::([A-Za-z_][\w]*)")


def extract_test_references(readme_text: str) -> list[tuple[str, str]]:
    """Return (relative_test_file, function_name) pairs referenced in
    ``readme_text``.

    Handles the shorthand where a later ``::test_foo`` on the same line
    inherits the most recent ``tests/.../file.py`` prefix, and expands
    brace shortcuts like ``test_quote_yfinance_aapl_{envelope,records}``
    into one reference per option.
    """

    refs: list[tuple[str, str]] = []
    for line in readme_text.splitlines():
        last_file: str | None = None
        cursor = 0
        while cursor < len(line):
            m_full = _FULL_REF.search(line, cursor)
            m_cont = _CONT_REF.search(line, cursor)
            candidates = [m for m in (m_full, m_cont) if m is not None]
            if not candidates:
                break
            next_match = min(candidates, key=lambda m: m.start())
            if next_match is m_full:
                last_file = next_match.group(1)
                func = next_match.group(2)
                # brace expansion immediately after the function name
                func_expanded = _expand_trailing_brace(line, next_match.end(), func)
                for f in func_expanded:
                    refs.append((last_file, f))
                cursor = next_match.end()
            else:
                func = next_match.group(1)
                if last_file is not None:
                    func_expanded = _expand_trailing_brace(
                        line, next_match.end(), func
                    )
                    for f in func_expanded:
                        refs.append((last_file, f))
                cursor = next_match.end()
    return refs


_BRACE_TAIL = re.compile(r"\{([^}]+)\}")


def _expand_trailing_brace(
    line: str, start: int, func: str
) -> list[str]:
    """If the chars immediately following ``start`` form ``{a,b,c}``,
    return one expansion per option appended to ``func`` verbatim; the
    greedy ``\\w*`` match in the caller already consumed any trailing
    ``_`` that belongs to the stem (e.g. ``test_foo_{a,b}`` → func is
    ``test_foo_`` and options are ``a, b``)."""

    tail = line[start : start + 256]
    match = _BRACE_TAIL.match(tail)
    if not match:
        return [func]
    options = [o.strip() for o in match.group(1).split(",")]
    return [f"{func}{o}" for o in options]


def check_readme_verified_markers_resolve(
    readme_path: Path, repo_root: Path
) -> list[str]:
    """Validate Req 7.3: every ``tests/...::test_*`` marker in the README
    points at a file that exists and a function defined in that file."""

    if not readme_path.is_file():
        return [f"README.md not found at {readme_path}"]

    refs = extract_test_references(readme_path.read_text(encoding="utf-8"))
    if not refs:
        return [
            "no `tests/(integration|unit)/...::test_*` markers found in README"
        ]

    errors: list[str] = []
    file_cache: dict[str, str | None] = {}
    for rel_path, func_name in refs:
        if rel_path not in file_cache:
            candidate = repo_root / rel_path
            file_cache[rel_path] = (
                candidate.read_text(encoding="utf-8")
                if candidate.is_file()
                else None
            )
        content = file_cache[rel_path]
        if content is None:
            errors.append(
                f"{rel_path}: referenced from README but file does not exist"
            )
            continue
        pattern = re.compile(rf"\bdef\s+{re.escape(func_name)}\s*\(")
        if not pattern.search(content):
            errors.append(
                f"{rel_path}::{func_name}: function not defined in file"
            )
    return errors


# ---------------------------------------------------------------------------
# Check 2: README feature matrix ↔ scripts/ bijection
# ---------------------------------------------------------------------------


_FEATURE_LIST_HEADING = "### 1-1. Feature List"
_SCRIPT_REF = re.compile(r"scripts/(\w+)\.py")


def _extract_feature_matrix_section(readme_text: str) -> str:
    """Return the raw markdown spanning §1-1; empty string if absent."""

    lines = readme_text.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == _FEATURE_LIST_HEADING:
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("### ") and stripped != _FEATURE_LIST_HEADING:
            end = j
            break
    return "\n".join(lines[start:end])


def check_readme_rows_and_scripts_bijection(
    readme_path: Path, repo_root: Path
) -> list[str]:
    """Validate Req 7.6: every ``scripts/*.py`` file (excluding ``_*``)
    appears in README §1-1 and every script named in §1-1 exists on disk."""

    errors: list[str] = []
    if not readme_path.is_file():
        return [f"README.md not found at {readme_path}"]

    scripts_dir = repo_root / "scripts"
    if not scripts_dir.is_dir():
        return [f"scripts directory not found at {scripts_dir}"]

    on_disk = {
        p.stem
        for p in scripts_dir.glob("*.py")
        if not p.stem.startswith("_")
    }

    section = _extract_feature_matrix_section(
        readme_path.read_text(encoding="utf-8")
    )
    if not section:
        return [
            f"README.md has no {_FEATURE_LIST_HEADING!r} section to cross-check"
        ]
    in_readme = set(_SCRIPT_REF.findall(section))

    missing_from_readme = on_disk - in_readme
    missing_from_disk = in_readme - on_disk

    for name in sorted(missing_from_readme):
        errors.append(
            f"scripts/{name}.py exists on disk but is not referenced in "
            f"README §1-1 feature matrix"
        )
    for name in sorted(missing_from_disk):
        errors.append(
            f"README §1-1 references scripts/{name}.py but no such file "
            f"exists under scripts/"
        )
    return errors
