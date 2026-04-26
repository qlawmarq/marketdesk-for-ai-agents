"""Offline unit coverage for ``scripts/options.py::derive_iv_view``.

Pins the per-expiration ATM-IV / put-call-OI derivation against the
documented degradation contract in the design (`options.py` block):
empty in / empty out, missing-fields marker when no contract carries
implied volatility, ATM-IV selection picks the strike nearest the
underlying, and the put/call open-interest ratio handles the all-puts /
all-calls edge cases.

Runs under the ``unit`` marker. The pre-collection guard in
``tests/unit/conftest.py`` installs a fake ``openbb`` module so importing
``options`` is side-effect free.
"""

from __future__ import annotations

from datetime import date

import pytest

from options import derive_iv_view  # type: ignore[import-not-found]

pytestmark = pytest.mark.unit


def _contract(
    *,
    expiration: object,
    option_type: str,
    strike: float,
    underlying_price: float = 100.0,
    implied_volatility: float | None = 0.25,
    open_interest: float | None = 100.0,
) -> dict[str, object]:
    """Build a minimal options-chain row carrying every field the
    derivation reads."""

    return {
        "expiration": expiration,
        "option_type": option_type,
        "strike": strike,
        "underlying_price": underlying_price,
        "implied_volatility": implied_volatility,
        "open_interest": open_interest,
    }


def test_empty_records_yield_empty_payload() -> None:
    out = derive_iv_view([])

    assert out == {"records": [], "missing_fields": []}


def test_single_expiration_yields_one_output_row() -> None:
    exp = date(2026, 6, 19)
    records = [
        _contract(expiration=exp, option_type="call", strike=100.0),
        _contract(expiration=exp, option_type="put", strike=100.0),
    ]

    out = derive_iv_view(records)

    assert len(out["records"]) == 1
    assert out["records"][0]["expiration"] == exp.isoformat()
    assert out["missing_fields"] == []


def test_two_expirations_yield_two_output_rows_in_ascending_order() -> None:
    near = date(2026, 6, 19)
    far = date(2026, 9, 18)
    records = [
        _contract(expiration=far, option_type="call", strike=100.0),
        _contract(expiration=near, option_type="call", strike=100.0),
    ]

    out = derive_iv_view(records)

    assert [r["expiration"] for r in out["records"]] == [
        near.isoformat(),
        far.isoformat(),
    ]


def test_missing_iv_on_every_contract_populates_missing_fields() -> None:
    exp = date(2026, 6, 19)
    records = [
        _contract(
            expiration=exp,
            option_type="call",
            strike=100.0,
            implied_volatility=None,
        ),
        _contract(
            expiration=exp,
            option_type="put",
            strike=100.0,
            implied_volatility=None,
        ),
    ]

    out = derive_iv_view(records)

    assert "implied_volatility" in out["missing_fields"]
    assert out["records"][0]["atm_iv"] is None


def test_atm_iv_selects_strike_nearest_underlying() -> None:
    exp = date(2026, 6, 19)
    underlying = 102.5
    # 100 strike is 2.5 away; 105 strike is 2.5 away too — but 103 is
    # closer (0.5). We expect 103's IV to be picked on each side.
    records = [
        _contract(
            expiration=exp, option_type="call", strike=80.0,
            underlying_price=underlying, implied_volatility=0.40,
        ),
        _contract(
            expiration=exp, option_type="call", strike=103.0,
            underlying_price=underlying, implied_volatility=0.20,
        ),
        _contract(
            expiration=exp, option_type="call", strike=120.0,
            underlying_price=underlying, implied_volatility=0.50,
        ),
        _contract(
            expiration=exp, option_type="put", strike=80.0,
            underlying_price=underlying, implied_volatility=0.45,
        ),
        _contract(
            expiration=exp, option_type="put", strike=103.0,
            underlying_price=underlying, implied_volatility=0.30,
        ),
        _contract(
            expiration=exp, option_type="put", strike=120.0,
            underlying_price=underlying, implied_volatility=0.55,
        ),
    ]

    out = derive_iv_view(records)

    # Average of the 103-strike call (0.20) and 103-strike put (0.30).
    assert out["records"][0]["atm_iv"] == pytest.approx(0.25)


def test_atm_iv_falls_back_to_single_side_when_other_side_missing() -> None:
    exp = date(2026, 6, 19)
    underlying = 100.0
    records = [
        _contract(
            expiration=exp, option_type="call", strike=100.0,
            underlying_price=underlying, implied_volatility=0.30,
        ),
        _contract(
            expiration=exp, option_type="put", strike=100.0,
            underlying_price=underlying, implied_volatility=None,
        ),
    ]

    out = derive_iv_view(records)

    assert out["records"][0]["atm_iv"] == pytest.approx(0.30)


def test_put_call_oi_ratio_arithmetic() -> None:
    exp = date(2026, 6, 19)
    records = [
        _contract(
            expiration=exp, option_type="call", strike=100.0,
            open_interest=200.0,
        ),
        _contract(
            expiration=exp, option_type="call", strike=110.0,
            open_interest=100.0,
        ),
        _contract(
            expiration=exp, option_type="put", strike=90.0,
            open_interest=150.0,
        ),
        _contract(
            expiration=exp, option_type="put", strike=80.0,
            open_interest=300.0,
        ),
    ]

    out = derive_iv_view(records)

    # put OI = 450, call OI = 300, ratio = 1.5
    assert out["records"][0]["put_call_oi_ratio"] == pytest.approx(1.5)


def test_all_calls_yields_zero_put_call_oi_ratio() -> None:
    exp = date(2026, 6, 19)
    records = [
        _contract(
            expiration=exp, option_type="call", strike=100.0,
            open_interest=200.0,
        ),
        _contract(
            expiration=exp, option_type="call", strike=110.0,
            open_interest=300.0,
        ),
    ]

    out = derive_iv_view(records)

    # No puts → numerator is 0; calls present → denominator > 0; ratio = 0.
    assert out["records"][0]["put_call_oi_ratio"] == pytest.approx(0.0)


def test_all_puts_yields_none_put_call_oi_ratio() -> None:
    exp = date(2026, 6, 19)
    records = [
        _contract(
            expiration=exp, option_type="put", strike=100.0,
            open_interest=200.0,
        ),
        _contract(
            expiration=exp, option_type="put", strike=90.0,
            open_interest=150.0,
        ),
    ]

    out = derive_iv_view(records)

    # call OI sum is 0 → ratio is None per the contract.
    assert out["records"][0]["put_call_oi_ratio"] is None
