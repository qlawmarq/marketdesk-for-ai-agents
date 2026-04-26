"""Integration-tier pytest configuration.

Centralizes subprocess invocation, timeout, environment, wrapper-list
discovery, and transient-flake triage for the integration tier.

Integration tests are subprocess-only: they must not import `openbb`
or any wrapper module directly.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


WRAPPERS: list[Path] = sorted(
    p for p in _SCRIPTS_DIR.glob("*.py") if not p.stem.startswith("_")
)


# Transient-signature allowlist — deliberately narrow so real contract
# regressions (non-5xx stderr, logic errors) still hard-fail.
# Never use a bare `\b5\d\d\b`: tracebacks contain `line 512`,
# byte-count messages contain `500 bytes`, etc.
_TRANSIENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ReadTimeoutError"),
    re.compile(r"ConnectTimeoutError"),
    re.compile(r"HTTPSConnectionPool"),
    re.compile(r"Name or service not known"),
    re.compile(r"Temporary failure in name resolution"),
    re.compile(r"Remote end closed connection"),
    re.compile(r"\bHTTP(?:S)?Error:?\s*5\d\d\b"),
    re.compile(r"\bstatus(?:\s*code)?\s*[:=]?\s*5\d\d\b", re.IGNORECASE),
    re.compile(
        r"\b5\d\d\s+(?:Internal Server Error|Bad Gateway|"
        r"Service Unavailable|Gateway Timeout)\b"
    ),
)


def _classify_transient(stderr: str) -> str | None:
    """Return the matching transient signature excerpt, or None if not transient."""

    for pattern in _TRANSIENT_PATTERNS:
        match = pattern.search(stderr)
        if match:
            return match.group(0)
    return None


def run_wrapper(
    argv: list[str],
    timeout: int = 60,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `uv run <argv...>` from the OpenBB repo root with captured output.

    `env_overrides` maps variable name → value and is applied on top of
    the inherited environment. Pass an empty string to simulate an unset
    credential without letting ``scripts/_env.py``'s `load_dotenv` repopulate
    it (dotenv loads with `override=False`, so an empty string in the
    caller-provided env takes precedence over the repo `.env` entry).
    """

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["uv", "run", *argv],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_wrapper_or_xfail(
    argv: list[str],
    timeout: int = 60,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a wrapper; xfail on recognized transient network signatures."""

    try:
        completed = run_wrapper(argv, timeout=timeout, env_overrides=env_overrides)
    except subprocess.TimeoutExpired as exc:
        pytest.xfail(f"transient provider failure: timeout after {exc.timeout}s")

    if completed.returncode != 0:
        excerpt = _classify_transient(completed.stderr)
        if excerpt is not None:
            pytest.xfail(f"transient provider failure: {excerpt}")
    return completed


def assert_stdout_is_single_json(
    completed: subprocess.CompletedProcess[str],
) -> Any:
    """Parse stdout as a single JSON document, or raise an AssertionError
    quoting the first offending non-JSON line."""

    stdout = completed.stdout
    if stdout.endswith("\n"):
        stdout = stdout[:-1]
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        offending = _first_non_json_line(completed.stdout)
        raise AssertionError(
            f"stdout is not a single JSON document ({exc.msg}); "
            f"first offending line: {offending!r}"
        ) from exc


def _first_non_json_line(stdout: str) -> str:
    """Identify the first line that does not begin with a JSON container/literal."""

    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        first = stripped[0]
        if first in "{[":
            continue
        return line
    return stdout.splitlines()[0] if stdout.splitlines() else ""


def _has_error_key(obj: Any) -> bool:
    """True if `obj` (or any nested dict/list) carries an `error` key."""

    if isinstance(obj, dict):
        if "error" in obj:
            return True
        return any(_has_error_key(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_error_key(v) for v in obj)
    return False


@pytest.fixture(scope="session", autouse=True)
def _load_integration_dotenv() -> None:
    """Populate `os.environ` from the repo-level `.env` before any
    integration test runs.

    Must be a session-autouse fixture (rather than a module-level
    import-time call) because `tests/unit/conftest.py` strips every
    credential env var at import time for the unit tier, and that
    sibling conftest imports after this one when pytest collects from
    `testpaths=["tests"]`. A module-level `load_dotenv` here would be
    undone. Fixtures run after all conftest imports complete, so this
    restoration is durable for the integration tier.
    Uses `override=True` to defeat the unit-tier strip; unit tests
    have their own per-test `_strip_credentials` autouse fixture, so
    restoring the env at session scope does not compromise unit
    isolation.
    """

    load_dotenv(_REPO_ROOT / ".env", override=True)


@pytest.fixture(scope="session", autouse=True)
def _warm_up_yfinance(_load_integration_dotenv: None) -> None:
    """Run one warm-up `quote.py AAPL --provider yfinance` before the first
    network test.

    On transient provider signatures skip the entire integration tier with
    a structured reason; on any other non-zero exit re-raise so contract
    regressions surface early.
    """

    try:
        completed = run_wrapper(
            ["scripts/quote.py", "AAPL", "--provider", "yfinance"],
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.skip(
            f"integration tier skipped: yfinance warm-up timed out "
            f"after {exc.timeout}s",
            allow_module_level=True,
        )

    if completed.returncode == 0:
        return

    excerpt = _classify_transient(completed.stderr)
    if excerpt is not None:
        pytest.skip(
            f"integration tier skipped: transient provider failure: {excerpt}",
            allow_module_level=True,
        )

    raise RuntimeError(
        "yfinance warm-up failed with non-transient stderr; "
        f"returncode={completed.returncode}; stderr tail:\n"
        f"{completed.stderr[-2000:]}"
    )
