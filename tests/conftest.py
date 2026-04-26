"""Root pytest configuration.

Enforces the two-tier marker convention (`unit` / `integration`) at
collection time and redundantly ensures `scripts/` is on `sys.path`
for flat imports (the authoritative setting lives in
`[tool.pytest.ini_options].pythonpath` inside `pyproject.toml`).

Credential loading for the integration tier lives in
`tests/integration/conftest.py`, not here, because the sibling
`tests/unit/conftest.py` strips credential env vars at import time as
part of its pre-collection guard — loading `.env` at the root would be
undone by the unit conftest import that runs after this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if _SCRIPTS.exists() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


_ALLOWED_MARKERS = frozenset({"unit", "integration"})


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Reject any collected item that is not marked `unit` or `integration`."""

    offenders = [
        item.nodeid
        for item in items
        if not (_ALLOWED_MARKERS & {m.name for m in item.iter_markers()})
    ]
    if offenders:
        raise pytest.UsageError(
            "every test must be marked `unit` or `integration`; "
            f"unmarked: {offenders}"
        )
