"""Unit-tier pytest configuration.

Pre-collection guard: strips every `_CREDENTIAL_MAP` key from the
process environment and installs a `SimpleNamespace`-based fake
`openbb` module into `sys.modules` **at conftest import time**, i.e.
before any sibling `test_*.py` module body executes.

The load-order rationale is load-bearing: pytest imports `conftest.py`
before it collects sibling test modules, so this guard runs in time
to make `sector_score.py`'s top-level `apply_to_openbb()` a no-op.
Autouse fixtures alone would be too late — they fire per-test, after
module-import-time side effects have already executed.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from _env import _CREDENTIAL_MAP  # type: ignore[import-not-found]


for _key in _CREDENTIAL_MAP:
    os.environ.pop(_key, None)


def _build_fake_openbb() -> SimpleNamespace:
    ns = SimpleNamespace(user=SimpleNamespace(credentials=SimpleNamespace()))
    # `from openbb import obb` works against the installed module when `.obb`
    # resolves to the same namespace carrying `.user.credentials`.
    ns.obb = ns
    return ns


sys.modules.setdefault("openbb", _build_fake_openbb())


@pytest.fixture(autouse=True)
def _strip_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-strip every `_CREDENTIAL_MAP` key before each unit test."""

    for key in _CREDENTIAL_MAP:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_openbb_module() -> SimpleNamespace:
    """Fresh fake `openbb` namespace exposing `.user.credentials` for per-test mutation."""

    return _build_fake_openbb()
