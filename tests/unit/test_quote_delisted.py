"""Offline unit coverage for ``scripts/quote.py::_flag_missing``.

The quote wrapper stamps ``status="missing"`` on records whose
``last_price`` is any of the sentinel values in ``_MISSING_PRICE_VALUES``
so downstream agents never mistake a delisted tape for a real zero
price. This test pins that branch offline — no network, no subprocess —
against the normalizer imported directly from ``scripts.quote``.

Runs under the ``unit`` marker. The pre-collection guard in
``tests/unit/conftest.py`` installs a fake ``openbb`` module so importing
``quote`` is side-effect free.
"""

from __future__ import annotations

import pytest

from quote import _MISSING_PRICE_VALUES, _flag_missing  # type: ignore[import-not-found]

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("missing_value", list(_MISSING_PRICE_VALUES))
def test_flag_missing_marks_sentinel_last_price_values_as_missing(missing_value: object) -> None:
    records = [{"symbol": "ZZZZ", "last_price": missing_value}]

    flagged = _flag_missing(records)

    assert flagged[0]["status"] == "missing"
    assert flagged[0]["symbol"] == "ZZZZ"
    assert flagged[0]["last_price"] == missing_value


def test_flag_missing_leaves_non_zero_price_unflagged() -> None:
    records = [{"symbol": "AAPL", "last_price": 189.42}]

    flagged = _flag_missing(records)

    assert flagged[0]["last_price"] == 189.42
    assert "status" not in flagged[0]
