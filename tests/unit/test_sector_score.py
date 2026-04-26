"""Unit tests for `scripts/sector_score.py` pure aggregation helpers.

The pre-collection guard in `tests/unit/conftest.py` strips every
`_CREDENTIAL_MAP` env var and installs a fake `openbb` module into
`sys.modules` before this module is imported, so the top-level
`apply_to_openbb()` call inside `sector_score.py` becomes a no-op
and the module is safe to import offline.
"""

from __future__ import annotations

import math

import pytest

from sector_score import (  # type: ignore[import-not-found]
    _normalize_finviz_perf_row,
    _to_float,
    build_scores,
    rank_desc,
    zscore,
)

pytestmark = pytest.mark.unit


_EXPECTED_SIGNAL_KEYS = {
    "return_3m",
    "return_6m",
    "return_12m",
    "clenow_90",
    "clenow_180",
    "risk_adj_1m",
}


# ---------------------------------------------------------------------------
# 5.1: _to_float
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.5", 1.5),
        (2.5, 2.5),
        (3, 3.0),
        (None, None),
        ("NaN", None),
        (float("nan"), None),
        ("abc", None),
    ],
    ids=[
        "numeric-string",
        "float",
        "int",
        "none",
        "nan-string",
        "nan-float",
        "non-numeric-string",
    ],
)
def test_to_float_covers_seven_input_shapes(
    value: object, expected: float | None
) -> None:
    result = _to_float(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# 5.1: zscore
# ---------------------------------------------------------------------------


def test_zscore_preserves_none_and_z_scores_remaining_positions() -> None:
    values = [1.0, None, 2.0, 3.0, None]

    result = zscore(values)

    assert len(result) == len(values)
    assert result[1] is None
    assert result[4] is None

    # Mean/stdev of [1.0, 2.0, 3.0] → mean=2, stdev=1
    assert result[0] == pytest.approx(-1.0, rel=1e-6)
    assert result[2] == pytest.approx(0.0, rel=1e-6, abs=1e-9)
    assert result[3] == pytest.approx(1.0, rel=1e-6)


def test_zscore_returns_zero_for_all_equal_degenerate_case() -> None:
    values = [5.0, 5.0, 5.0, None]

    result = zscore(values)

    assert result[0] == pytest.approx(0.0, abs=1e-9)
    assert result[1] == pytest.approx(0.0, abs=1e-9)
    assert result[2] == pytest.approx(0.0, abs=1e-9)
    assert result[3] is None


def test_zscore_returns_all_none_when_fewer_than_two_valid_entries() -> None:
    single_value = zscore([None, 1.0, None])
    assert single_value == [None, None, None]

    all_none = zscore([None, None, None])
    assert all_none == [None, None, None]

    empty: list[float | None] = []
    assert zscore(empty) == []


# ---------------------------------------------------------------------------
# 5.1: rank_desc
# ---------------------------------------------------------------------------


def test_rank_desc_assigns_descending_ranks_and_preserves_none() -> None:
    values: list[float | None] = [1.0, None, 3.0, 2.0]

    result = rank_desc(values)

    assert len(result) == len(values)
    assert result[2] == 1  # largest (3.0) → rank 1
    assert result[3] == 2  # second (2.0) → rank 2
    assert result[0] == 3  # smallest non-None (1.0) → rank 3
    assert result[1] is None


def test_rank_desc_on_empty_list_returns_empty_list() -> None:
    assert rank_desc([]) == []


# ---------------------------------------------------------------------------
# 5.2: build_scores
# ---------------------------------------------------------------------------


def _full_weights() -> dict[str, float]:
    return {
        "clenow_90": 0.25,
        "clenow_180": 0.25,
        "return_6m": 0.20,
        "return_3m": 0.15,
        "return_12m": 0.10,
        "risk_adj": 0.05,
    }


def _perf_row(
    three_m: float,
    six_m: float,
    one_y: float,
    one_m: float,
    vol_m: float,
) -> dict[str, object]:
    return {
        "three_month": three_m,
        "six_month": six_m,
        "one_year": one_y,
        "one_month": one_m,
        "volatility_month": vol_m,
    }


def test_build_scores_sorts_by_rank_bounds_and_contains_expected_keys() -> None:
    tickers = ["AAA", "BBB", "CCC"]
    perf: dict[str, dict[str, object]] = {
        "AAA": _perf_row(0.10, 0.20, 0.30, 0.02, 0.04),
        "BBB": _perf_row(0.05, 0.10, 0.15, 0.01, 0.04),
        # CCC intentionally absent → must yield a None-composite record.
    }
    clenow_90 = {"AAA": {"factor": 1.0}, "BBB": {"factor": 0.5}}
    clenow_180 = {"AAA": {"factor": 2.0}, "BBB": {"factor": 1.0}}

    records = build_scores(tickers, perf, clenow_90, clenow_180, _full_weights())

    assert [r["ticker"] for r in records[:2]] == ["AAA", "BBB"]
    assert records[-1]["ticker"] == "CCC"

    ranks = [r["rank"] for r in records if r["rank"] is not None]
    assert ranks == sorted(ranks), "records must be sorted ascending by rank"

    for r in records:
        score = r["composite_score_0_100"]
        if score is not None:
            assert 0.0 <= score <= 100.0, f"score out of bounds for {r['ticker']}: {score}"

    ccc = next(r for r in records if r["ticker"] == "CCC")
    assert ccc["composite_score_0_100"] is None
    assert ccc["composite_z"] is None
    assert ccc["rank"] is None

    for r in records:
        assert set(r["signals"]) == _EXPECTED_SIGNAL_KEYS, (
            f"signals keys drifted from source of truth for {r['ticker']}"
        )
        assert set(r["z_scores"]) == _EXPECTED_SIGNAL_KEYS, (
            f"z_scores keys drifted from source of truth for {r['ticker']}"
        )


def test_build_scores_with_zero_sum_weights_returns_none_composite_without_zerodivisionerror() -> None:
    tickers = ["AAA", "BBB"]
    perf: dict[str, dict[str, object]] = {
        "AAA": _perf_row(0.10, 0.20, 0.30, 0.02, 0.04),
        "BBB": _perf_row(0.05, 0.10, 0.15, 0.01, 0.04),
    }
    clenow_90 = {"AAA": {"factor": 1.0}, "BBB": {"factor": 0.5}}
    clenow_180 = {"AAA": {"factor": 2.0}, "BBB": {"factor": 1.0}}
    zero_weights = {
        "clenow_90": 0.0,
        "clenow_180": 0.0,
        "return_6m": 0.0,
        "return_3m": 0.0,
        "return_12m": 0.0,
        "risk_adj": 0.0,
    }

    records = build_scores(tickers, perf, clenow_90, clenow_180, zero_weights)

    for r in records:
        assert r["composite_z"] is None, (
            f"zero-sum weights must yield None composite_z for {r['ticker']}"
        )
        assert r["composite_score_0_100"] is None, (
            f"zero-sum weights must yield None composite_score_0_100 for {r['ticker']}"
        )
        for score in (r["composite_z"], r["composite_score_0_100"]):
            if score is not None:
                assert not math.isnan(score)


# ---------------------------------------------------------------------------
# Task 5.1: _normalize_finviz_perf_row
# ---------------------------------------------------------------------------


def test_normalize_finviz_perf_row_renames_multi_year_percent_strings_to_decimal() -> None:
    row = {
        "symbol": "SPY",
        "one_day": -0.0049,
        "Perf 3Y": "71.87%",
        "Perf 5Y": "70.00%",
        "Perf 10Y": "239.02%",
    }

    result = _normalize_finviz_perf_row(row)

    assert "Perf 3Y" not in result
    assert "Perf 5Y" not in result
    assert "Perf 10Y" not in result
    assert result["perf_3y"] == pytest.approx(0.7187, rel=1e-6)
    assert result["perf_5y"] == pytest.approx(0.70, rel=1e-6)
    assert result["perf_10y"] == pytest.approx(2.3902, rel=1e-6)
    # Untouched keys pass through unchanged.
    assert result["symbol"] == "SPY"
    assert result["one_day"] == pytest.approx(-0.0049)


def test_normalize_finviz_perf_row_coerces_unparseable_values_to_none() -> None:
    row = {"Perf 3Y": None, "Perf 5Y": "N/A", "Perf 10Y": "--"}

    result = _normalize_finviz_perf_row(row)

    assert result["perf_3y"] is None
    assert result["perf_5y"] is None
    assert result["perf_10y"] is None


def test_normalize_finviz_perf_row_accepts_numeric_passthrough_and_missing_keys() -> None:
    row = {
        "symbol": "AAPL",
        "one_year": 0.3,
        "Perf 3Y": 0.5,  # already numeric (unlikely but should pass)
    }

    result = _normalize_finviz_perf_row(row)

    # A bare numeric input is preserved (interpreted as already-decimal).
    assert result["perf_3y"] == pytest.approx(0.5)
    # Keys that aren't present stay absent; no fabrication.
    assert "perf_5y" not in result
    assert "perf_10y" not in result
    # Non-rename keys are untouched.
    assert result["symbol"] == "AAPL"
    assert result["one_year"] == pytest.approx(0.3)


def test_normalize_finviz_perf_row_preserves_keys_outside_rename_map() -> None:
    row = {
        "symbol": "XLK",
        "one_day": -0.0049,
        "one_week": -0.013,
        "one_month": -0.0053,
        "three_month": 0.0437,
        "six_month": 0.0419,
        "one_year": 0.3232,
        "volatility_week": 0.0092,
        "volatility_month": 0.0113,
        "price": 708.45,
    }

    result = _normalize_finviz_perf_row(row)

    assert result == row
