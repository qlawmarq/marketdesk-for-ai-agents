"""Offline unit coverage for ``scripts/factors.py::resolve_defaults``.

Pins the defaults-projection logic that fills the ``data.defaults_applied``
metadata echo: when ``--region`` / ``--frequency`` / ``--factor`` are
omitted, the corresponding entries appear in canonical order; partial
overrides produce the correct subset; full overrides yield an empty
list.

Runs under the ``unit`` marker. The pre-collection guard in
``tests/unit/conftest.py`` installs a fake ``openbb`` module so importing
``factors`` is side-effect free.
"""

from __future__ import annotations

import pytest

from factors import (  # type: ignore[import-not-found]
    DEFAULT_FACTOR,
    DEFAULT_FREQUENCY,
    DEFAULT_REGION,
    resolve_defaults,
)

pytestmark = pytest.mark.unit


def test_all_none_applies_every_default_in_canonical_order() -> None:
    region, frequency, factor, applied = resolve_defaults(None, None, None)

    assert region == DEFAULT_REGION
    assert frequency == DEFAULT_FREQUENCY
    assert factor == DEFAULT_FACTOR
    assert applied == ["region", "frequency", "factor"]


def test_full_overrides_yield_empty_defaults_applied() -> None:
    region, frequency, factor, applied = resolve_defaults(
        "japan", "weekly", "momentum"
    )

    assert region == "japan"
    assert frequency == "weekly"
    assert factor == "momentum"
    assert applied == []


def test_only_region_overridden_records_frequency_and_factor() -> None:
    _, _, _, applied = resolve_defaults("japan", None, None)

    assert applied == ["frequency", "factor"]


def test_only_frequency_overridden_records_region_and_factor() -> None:
    _, _, _, applied = resolve_defaults(None, "weekly", None)

    assert applied == ["region", "factor"]


def test_only_factor_overridden_records_region_and_frequency() -> None:
    _, _, _, applied = resolve_defaults(None, None, "momentum")

    assert applied == ["region", "frequency"]


def test_region_and_frequency_overridden_records_factor_only() -> None:
    _, _, _, applied = resolve_defaults("japan", "weekly", None)

    assert applied == ["factor"]
