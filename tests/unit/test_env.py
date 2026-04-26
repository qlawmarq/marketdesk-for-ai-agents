"""Unit tests for `scripts/_env.py` credential wiring.

`apply_to_openbb` does `from openbb import obb` then assigns onto
`obb.user.credentials`, so each test injects a fake `openbb` module
into `sys.modules` whose `.obb` attribute carries the fixture's
`user.credentials` namespace. The pre-collection guard in
`tests/unit/conftest.py` already strips `_CREDENTIAL_MAP` env vars,
so a freshly imported `_env` does not see any developer credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _env import _CREDENTIAL_MAP, apply_to_openbb  # type: ignore[import-not-found]

pytestmark = pytest.mark.unit


def _install_fake_openbb(
    monkeypatch: pytest.MonkeyPatch,
    fake_credentials_holder: SimpleNamespace,
) -> None:
    """Mount `fake_credentials_holder` as `openbb.obb` for the duration of one test."""

    fake_module = SimpleNamespace(obb=fake_credentials_holder)
    monkeypatch.setitem(sys.modules, "openbb", fake_module)


# ---------------------------------------------------------------------------
# 4.1: apply_to_openbb set / leave / swallow branches
# ---------------------------------------------------------------------------


def test_apply_to_openbb_sets_credentials_attribute_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
    fake_openbb_module: SimpleNamespace,
) -> None:
    monkeypatch.setenv("FMP_API_KEY", "fmp-secret")
    monkeypatch.setenv("FRED_API_KEY", "fred-secret")
    _install_fake_openbb(monkeypatch, fake_openbb_module)

    apply_to_openbb()

    assert fake_openbb_module.user.credentials.fmp_api_key == "fmp-secret"
    assert fake_openbb_module.user.credentials.fred_api_key == "fred-secret"


def test_apply_to_openbb_leaves_attribute_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
    fake_openbb_module: SimpleNamespace,
) -> None:
    # autouse `_strip_credentials` already cleared every key; assert it stays unset.
    _install_fake_openbb(monkeypatch, fake_openbb_module)

    apply_to_openbb()

    for cred_attr in _CREDENTIAL_MAP.values():
        assert not hasattr(fake_openbb_module.user.credentials, cred_attr), (
            f"unset env var must not produce attribute {cred_attr!r}"
        )


def test_apply_to_openbb_leaves_attribute_when_env_var_empty(
    monkeypatch: pytest.MonkeyPatch,
    fake_openbb_module: SimpleNamespace,
) -> None:
    monkeypatch.setenv("FMP_API_KEY", "")
    _install_fake_openbb(monkeypatch, fake_openbb_module)

    apply_to_openbb()

    assert not hasattr(fake_openbb_module.user.credentials, "fmp_api_key"), (
        "empty env value must be treated as unset (no spurious assignment)"
    )


class _RaisingCredentials:
    """Credentials stand-in whose `__setattr__` raises for one designated attribute."""

    def __init__(self, *, raise_on: str) -> None:
        object.__setattr__(self, "_raise_on", raise_on)
        object.__setattr__(self, "_assignments", {})

    def __setattr__(self, name: str, value: object) -> None:
        if name == self._raise_on:
            raise RuntimeError(f"provider not installed: cannot set {name!r}")
        object.__setattr__(self, name, value)
        self._assignments[name] = value


def test_apply_to_openbb_swallows_setattr_exception_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sanity: the test only makes sense when the map has at least two keys, so a
    # later key can demonstrate that iteration continued past the raising one.
    assert len(_CREDENTIAL_MAP) >= 2

    map_items = list(_CREDENTIAL_MAP.items())
    raising_env_key, raising_cred_attr = map_items[0]
    surviving_env_key, surviving_cred_attr = map_items[-1]

    for env_key in _CREDENTIAL_MAP:
        monkeypatch.setenv(env_key, f"value-for-{env_key}")

    raising_credentials = _RaisingCredentials(raise_on=raising_cred_attr)
    fake_holder = SimpleNamespace(user=SimpleNamespace(credentials=raising_credentials))
    _install_fake_openbb(monkeypatch, fake_holder)

    apply_to_openbb()

    assert raising_cred_attr not in raising_credentials._assignments, (
        f"setattr exception for {raising_cred_attr!r} should have been swallowed, not assigned"
    )
    assert raising_credentials._assignments.get(surviving_cred_attr) == (
        f"value-for-{surviving_env_key}"
    ), "iteration must continue after the swallowed exception"


# ---------------------------------------------------------------------------
# 4.2: _CREDENTIAL_MAP invariants vs `.env.example`
# ---------------------------------------------------------------------------


def test_sec_user_agent_is_not_a_credential_map_key() -> None:
    assert "SEC_USER_AGENT" not in _CREDENTIAL_MAP, (
        "SEC_USER_AGENT is read directly by OpenBB as an env var; "
        "do not map it onto a credentials attribute"
    )


def _resolve_env_example_path() -> Path:
    from _env import __file__ as env_file  # type: ignore[import-not-found]

    return Path(env_file).resolve().parent.parent / ".env.example"


def test_every_credential_map_key_appears_in_env_example() -> None:
    env_example = _resolve_env_example_path()
    assert env_example.exists(), (
        f".env.example expected at {env_example} but was not found"
    )

    body = env_example.read_text(encoding="utf-8")
    declared_keys = {
        line.split("=", 1)[0].strip()
        for line in body.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }

    missing = [
        env_key
        for env_key in _CREDENTIAL_MAP
        if env_key != "SEC_USER_AGENT" and env_key not in declared_keys
    ]
    assert not missing, (
        f"_CREDENTIAL_MAP keys missing from {env_example}: {missing}; "
        "add a `KEY=` line for each"
    )
