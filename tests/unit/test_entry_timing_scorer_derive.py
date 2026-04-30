"""Unit tests for `scripts/entry_timing_scorer.py` derived-indicators layer.

Covers Task 4 from `docs/tasks/todo/entry-timing-scorer/tasks.md`:

- ``compute_range_pct_52w`` — null when any input is null or denominator is zero.
- ``compute_ma200_distance`` — null when any input is null or ``ma_200d == 0``.
- ``compute_volume_z_20d`` — robust (log-median-MAD) and classical
  (mean / stdev) estimators, 21-row minimum, narrowest-gate ordering for
  degenerate input (window_too_short → non_positive (robust) → zero_dispersion),
  shared latest-session sourcing (``history_rows[-1].volume``).
- ``build_volume_reference`` — sibling block with fixed window labels
  and single ``volume_reference_unavailable_on_provider`` flag when any
  value is null.
- ``compute_derived_indicators`` — integration of the above plus
  ``volume_avg_window`` tag (``"20d_real"`` iff ``volume_z_20d`` is
  non-null, else ``None``) and ``volume_z_estimator`` echo.
"""

from __future__ import annotations

import math
from statistics import mean, median, stdev
from typing import Any

import pytest

from entry_timing_scorer import (  # type: ignore[import-not-found]
    DerivedIndicators,
    QuoteFields,
    build_volume_reference,
    compute_derived_indicators,
    compute_ma200_distance,
    compute_range_pct_52w,
    compute_volume_z_20d,
    resolve_last_price,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# compute_range_pct_52w (Req 5.1)
# ---------------------------------------------------------------------------


def test_compute_range_pct_52w_happy_path_at_mid() -> None:
    # last=100, low=80, high=120 → (100-80)/(120-80) = 0.5
    assert compute_range_pct_52w(100.0, 120.0, 80.0) == pytest.approx(0.5)


def test_compute_range_pct_52w_at_year_low_is_zero() -> None:
    assert compute_range_pct_52w(80.0, 120.0, 80.0) == pytest.approx(0.0)


def test_compute_range_pct_52w_at_year_high_is_one() -> None:
    assert compute_range_pct_52w(120.0, 120.0, 80.0) == pytest.approx(1.0)


def test_compute_range_pct_52w_zero_denominator_returns_none() -> None:
    """When year_high == year_low (e.g. halted / illiquid), denominator is
    zero — Req 5.1 says emit null rather than ``ZeroDivisionError``."""

    assert compute_range_pct_52w(100.0, 100.0, 100.0) is None


@pytest.mark.parametrize(
    "last,high,low",
    [
        (None, 120.0, 80.0),
        (100.0, None, 80.0),
        (100.0, 120.0, None),
        (None, None, None),
    ],
)
def test_compute_range_pct_52w_returns_none_for_any_null_input(
    last: float | None, high: float | None, low: float | None
) -> None:
    assert compute_range_pct_52w(last, high, low) is None


# ---------------------------------------------------------------------------
# compute_ma200_distance (Req 5.2)
# ---------------------------------------------------------------------------


def test_compute_ma200_distance_happy_path_positive_above_ma() -> None:
    # (105 - 100) / 100 = 0.05
    assert compute_ma200_distance(105.0, 100.0) == pytest.approx(0.05)


def test_compute_ma200_distance_happy_path_negative_below_ma() -> None:
    assert compute_ma200_distance(95.0, 100.0) == pytest.approx(-0.05)


def test_compute_ma200_distance_zero_ma_returns_none() -> None:
    """Req 5.2: ``ma_200d != 0`` is a precondition; zero denominator
    must not raise ``ZeroDivisionError``."""

    assert compute_ma200_distance(105.0, 0.0) is None


@pytest.mark.parametrize(
    "last,ma",
    [(None, 100.0), (100.0, None), (None, None)],
)
def test_compute_ma200_distance_returns_none_for_any_null_input(
    last: float | None, ma: float | None
) -> None:
    assert compute_ma200_distance(last, ma) is None


# ---------------------------------------------------------------------------
# compute_volume_z_20d (Req 5.3, 5.4, 5.5)
# ---------------------------------------------------------------------------


def _rows_with_volumes(volumes: list[float | None]) -> list[dict[str, Any]]:
    return [{"date": f"2026-03-{i+1:02d}", "close": 100.0, "volume": v} for i, v in enumerate(volumes)]


def test_compute_volume_z_20d_window_too_short_for_twenty_rows() -> None:
    """Req 5.3: ``volume_z_20d`` needs ≥21 rows (20-session reference
    window EXCLUDING the latest session)."""

    rows = _rows_with_volumes([1_000_000.0] * 20)
    z, flag, latest = compute_volume_z_20d(rows, "robust")

    assert z is None
    assert flag == "volume_window_too_short"


def test_compute_volume_z_20d_window_too_short_on_empty_history() -> None:
    z, flag, _ = compute_volume_z_20d([], "robust")
    assert z is None
    assert flag == "volume_window_too_short"


def test_compute_volume_z_20d_robust_matches_manual_log_median_mad() -> None:
    """Robust formula: (log(latest) - median(log(ref))) / (1.4826 * MAD).

    Uses a monotonically increasing reference so the median / MAD are
    deterministic and can be matched by hand.
    """

    ref = [float(1_000_000 + i * 10_000) for i in range(20)]  # 20 non-equal values
    latest = 1_500_000.0  # an outlier to get a non-trivial z
    rows = _rows_with_volumes(ref + [latest])

    z, flag, returned_latest = compute_volume_z_20d(rows, "robust")

    logs = [math.log(v) for v in ref]
    med = median(logs)
    mad = median(abs(x - med) for x in logs)
    expected = (math.log(latest) - med) / (1.4826 * mad)

    assert flag is None
    assert z == pytest.approx(expected)
    assert returned_latest == pytest.approx(latest)


def test_compute_volume_z_20d_classical_matches_manual_mean_stdev() -> None:
    ref = [float(1_000_000 + i * 10_000) for i in range(20)]
    latest = 1_500_000.0
    rows = _rows_with_volumes(ref + [latest])

    z, flag, returned_latest = compute_volume_z_20d(rows, "classical")

    expected = (latest - mean(ref)) / stdev(ref)

    assert flag is None
    assert z == pytest.approx(expected)
    assert returned_latest == pytest.approx(latest)


def test_compute_volume_z_20d_zero_dispersion_robust_flat_reference() -> None:
    """All 20 ref volumes identical ⇒ MAD == 0 ⇒ denominator zero ⇒
    ``volume_zero_dispersion`` flag (narrowest non-window gate for robust)."""

    rows = _rows_with_volumes([1_000_000.0] * 20 + [1_500_000.0])
    z, flag, _ = compute_volume_z_20d(rows, "robust")

    assert z is None
    assert flag == "volume_zero_dispersion"


def test_compute_volume_z_20d_zero_dispersion_classical_flat_reference() -> None:
    rows = _rows_with_volumes([1_000_000.0] * 20 + [1_500_000.0])
    z, flag, _ = compute_volume_z_20d(rows, "classical")

    assert z is None
    assert flag == "volume_zero_dispersion"


def test_compute_volume_z_20d_non_positive_flag_fires_only_under_robust() -> None:
    """Robust requires log(v) — a zero / negative entry blocks the transform
    and emits ``volume_non_positive``. Classical is not gated by this
    flag because it tolerates zero volumes arithmetically."""

    ref = [float(1_000_000 + i * 10_000) for i in range(19)] + [0.0]  # one zero
    rows = _rows_with_volumes(ref + [1_500_000.0])

    z_robust, flag_robust, _ = compute_volume_z_20d(rows, "robust")
    assert z_robust is None
    assert flag_robust == "volume_non_positive"

    z_classical, flag_classical, _ = compute_volume_z_20d(rows, "classical")
    # Classical should happily compute — zero is a legitimate value for
    # mean/stdev even if rare in practice.
    assert flag_classical is None
    assert z_classical is not None


def test_compute_volume_z_20d_non_positive_latest_triggers_flag_under_robust() -> None:
    ref = [float(1_000_000 + i * 10_000) for i in range(20)]
    rows = _rows_with_volumes(ref + [0.0])

    z, flag, _ = compute_volume_z_20d(rows, "robust")
    assert z is None
    assert flag == "volume_non_positive"


def test_compute_volume_z_20d_narrowest_gate_wins_window_before_non_positive() -> None:
    """Spec: ordering is ``volume_window_too_short → volume_non_positive
    (robust only) → volume_zero_dispersion`` — narrowest gate fires first.
    A 15-row history with zeros should flag window_too_short, not
    non_positive."""

    rows = _rows_with_volumes([0.0] * 15)
    z, flag, _ = compute_volume_z_20d(rows, "robust")

    assert z is None
    assert flag == "volume_window_too_short"


def test_compute_volume_z_20d_uses_last_row_excluding_latest_as_reference() -> None:
    """Req 5.3: the reference window is the 20 sessions EXCLUDING the
    latest session. If we feed 21 rows, the z is computed against the
    first 20 and the latest is row[-1]."""

    # 20 constant ref values + a wildly different latest session → z != 0
    ref = [1_000_000.0 + i * 10_000 for i in range(20)]
    latest_high = 10_000_000.0
    rows = _rows_with_volumes(ref + [latest_high])

    z_high, _, latest_returned = compute_volume_z_20d(rows, "classical")
    assert latest_returned == pytest.approx(latest_high)
    assert z_high is not None and z_high > 0

    # If we swap in a tiny latest, z must flip sign — confirming the
    # latest session truly comes from row[-1], not from the middle of
    # the ref window.
    latest_low = 500_000.0
    rows_low = _rows_with_volumes(ref + [latest_low])
    z_low, _, _ = compute_volume_z_20d(rows_low, "classical")
    assert z_low is not None and z_low < 0


def test_compute_volume_z_20d_null_latest_collapses_to_window_too_short() -> None:
    """A null latest volume breaks the 21-row-well-formed-window
    precondition, so the narrowest gate (``volume_window_too_short``)
    fires — consistent with "a row with a null volume cell is as good
    as missing" under the narrowest-gate ordering."""

    ref = [float(1_000_000 + i * 10_000) for i in range(20)]
    rows = _rows_with_volumes(ref + [None])

    z, flag, _ = compute_volume_z_20d(rows, "classical")
    assert z is None
    assert flag == "volume_window_too_short"


# ---------------------------------------------------------------------------
# build_volume_reference (Req 5.7)
# ---------------------------------------------------------------------------


def _quote(
    *,
    volume_average: float | None,
    volume_average_10d: float | None,
) -> QuoteFields:
    return QuoteFields(
        last_price=100.0,
        prev_close=99.0,
        year_high=120.0,
        year_low=80.0,
        ma_200d=95.0,
        ma_50d=98.0,
        volume_average=volume_average,
        volume_average_10d=volume_average_10d,
    )


def test_build_volume_reference_yfinance_preserves_values_and_no_flag() -> None:
    quote = _quote(volume_average=1_500_000.0, volume_average_10d=2_000_000.0)
    reference, flags = build_volume_reference(quote)

    assert reference == {
        "volume_average": {"window": "3m_rolling", "value": 1_500_000.0},
        "volume_average_10d": {"window": "10d", "value": 2_000_000.0},
    }
    assert flags == []


def test_build_volume_reference_fmp_nulls_both_emits_flag_exactly_once() -> None:
    """Req 5.7: under FMP both values typically resolve to ``None``.
    The flag must fire exactly once per row even if both values are
    null."""

    quote = _quote(volume_average=None, volume_average_10d=None)
    reference, flags = build_volume_reference(quote)

    assert reference == {
        "volume_average": {"window": "3m_rolling", "value": None},
        "volume_average_10d": {"window": "10d", "value": None},
    }
    assert flags == ["volume_reference_unavailable_on_provider"]


def test_build_volume_reference_one_null_still_emits_flag() -> None:
    quote = _quote(volume_average=1_500_000.0, volume_average_10d=None)
    reference, flags = build_volume_reference(quote)

    assert reference["volume_average"]["value"] == 1_500_000.0
    assert reference["volume_average_10d"]["value"] is None
    assert flags == ["volume_reference_unavailable_on_provider"]


# ---------------------------------------------------------------------------
# compute_derived_indicators (integration — Req 5.1-5.7)
# ---------------------------------------------------------------------------


def _happy_history(n: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "date": f"2026-03-{(i % 28) + 1:02d}",
            "close": 100.0 + i * 0.1,
            "volume": 1_000_000.0 + i * 10_000,
        }
        for i in range(n)
    ]


def test_compute_derived_indicators_happy_path_emits_volume_avg_window() -> None:
    quote = _quote(volume_average=1_500_000.0, volume_average_10d=2_000_000.0)
    # Use the actual fallback chain result so the types match real usage.
    last = resolve_last_price(quote, _happy_history())
    history = _happy_history(30)

    derived = compute_derived_indicators(quote, last, history, "robust")

    assert isinstance(derived, DerivedIndicators)
    assert derived.range_pct_52w == pytest.approx((100.0 - 80.0) / (120.0 - 80.0))
    assert derived.ma200_distance == pytest.approx((100.0 - 95.0) / 95.0)
    assert derived.volume_z_20d is not None
    assert derived.volume_avg_window == "20d_real"
    assert derived.volume_z_estimator == "robust"
    assert derived.latest_volume == pytest.approx(history[-1]["volume"])


def test_compute_derived_indicators_volume_avg_window_null_when_z_null() -> None:
    """Data-model contract: ``volume_avg_window`` is ``"20d_real"`` only
    when ``volume_z_20d`` is non-null; otherwise it is ``None`` so
    consumers never see the tag without the matching scalar."""

    quote = _quote(volume_average=1_500_000.0, volume_average_10d=2_000_000.0)
    last = resolve_last_price(quote, _happy_history(5))
    # Only 5 rows → window_too_short
    derived = compute_derived_indicators(quote, last, _happy_history(5), "robust")

    assert derived.volume_z_20d is None
    assert derived.volume_avg_window is None
    assert "volume_window_too_short" in derived.extra_flags


def test_compute_derived_indicators_echoes_estimator_choice() -> None:
    quote = _quote(volume_average=1_500_000.0, volume_average_10d=2_000_000.0)
    history = _happy_history(30)
    last = resolve_last_price(quote, history)

    robust = compute_derived_indicators(quote, last, history, "robust")
    classical = compute_derived_indicators(quote, last, history, "classical")

    assert robust.volume_z_estimator == "robust"
    assert classical.volume_z_estimator == "classical"


def test_compute_derived_indicators_null_price_inputs_collapse_range_and_ma() -> None:
    """When ``last_price`` is unresolvable (bond ETF under yfinance, all
    fallback rungs null), ``range_pct_52w`` and ``ma200_distance`` are
    both null — but the volume path is still exercised because
    ``volume_z_20d`` depends only on ``history_rows``."""

    quote = QuoteFields(
        last_price=None,
        prev_close=None,
        year_high=None,
        year_low=None,
        ma_200d=None,
        ma_50d=None,
        volume_average=None,
        volume_average_10d=None,
    )
    history = _happy_history(30)
    last = resolve_last_price(quote, [])

    derived = compute_derived_indicators(quote, last, history, "robust")

    assert derived.range_pct_52w is None
    assert derived.ma200_distance is None
    assert derived.volume_z_20d is not None  # volume path is independent


def test_compute_derived_indicators_gate_flag_is_single_entry_in_extra_flags() -> None:
    """Narrowest-gate ordering: exactly one of the three volume flags fires
    on any degenerate input — so ``extra_flags`` carries at most one
    volume_* entry from the z-score computation (the unavailable-provider
    flag for the reference block is handled separately at envelope time)."""

    quote = _quote(volume_average=1_500_000.0, volume_average_10d=2_000_000.0)
    # Flat volumes → zero_dispersion; never non_positive because all > 0.
    history = _rows_with_volumes([1_000_000.0] * 21)
    last = resolve_last_price(quote, history)

    derived = compute_derived_indicators(quote, last, history, "robust")

    volume_z_flags = [
        f
        for f in derived.extra_flags
        if f
        in {
            "volume_window_too_short",
            "volume_non_positive",
            "volume_zero_dispersion",
        }
    ]
    assert volume_z_flags == ["volume_zero_dispersion"]
