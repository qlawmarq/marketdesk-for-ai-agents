"""Unit tests for `scripts/entry_timing_scorer.py` cross-sectional scoring layer.

Covers Task 5 from `docs/tasks/todo/entry-timing-scorer/tasks.md`:

- Task 5.1: ``compute_cross_sectional`` — per-signal cross-sectional
  z-scores with ``min_basket=3``, sum-of-available-weights composition
  for ``trend_z`` / ``mean_reversion_z``, ``clip(50 + z*25, 0, 100)``
  transform, ``basket_size`` / ``basket_size_sufficient`` accounting,
  per-signal small-basket flags via ``_SIGNAL_FLAG_NAME``, and the
  whole-basket short-circuit that sets every score to ``None`` and
  queues the top-level validation warning.
- Task 5.2: blend-profile handling — ``balanced`` blends via
  ``0.5*trend_z + 0.5*mean_reversion_z`` then the 0-100 transform;
  ``trend`` / ``mean_reversion`` mirror the corresponding sub-score
  verbatim; ``none`` emits ``blended_score_0_100 = None`` so the row
  builder can omit the field entirely; the ``z_scores`` block emits
  the five transformed signal keys plus ``trend_z`` /
  ``mean_reversion_z``.
"""

from __future__ import annotations

from statistics import mean, stdev

import pytest

from entry_timing_scorer import (  # type: ignore[import-not-found]
    SCORER_SIGNAL_KEYS,
    CrossSectionalResult,
    MeanReversionWeights,
    ScoredRow,
    SignalBundle,
    TrendWeights,
    compute_cross_sectional,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _default_trend_weights() -> TrendWeights:
    return TrendWeights(clenow=0.50, macd=0.25, volume=0.25)


def _default_mr_weights() -> MeanReversionWeights:
    return MeanReversionWeights(range=0.60, rsi=0.40)


def _bundle(
    symbol: str,
    *,
    ok: bool = True,
    clenow_126: float | None = None,
    macd_histogram: float | None = None,
    volume_z_20d: float | None = None,
    inv_range_pct_52w: float | None = None,
    oversold_rsi_14: float | None = None,
) -> SignalBundle:
    return SignalBundle(
        symbol=symbol,
        ok=ok,
        clenow_126=clenow_126,
        macd_histogram=macd_histogram,
        volume_z_20d=volume_z_20d,
        inv_range_pct_52w=inv_range_pct_52w,
        oversold_rsi_14=oversold_rsi_14,
    )


def _healthy_basket(n: int = 5) -> list[SignalBundle]:
    """Return a 5-ticker basket with dispersion across every signal."""

    rows = []
    for i in range(n):
        rows.append(
            _bundle(
                f"T{i}",
                clenow_126=0.10 + i * 0.05,
                macd_histogram=0.20 + i * 0.10,
                volume_z_20d=0.30 + i * 0.15,
                inv_range_pct_52w=0.40 + i * 0.05,
                oversold_rsi_14=10.0 + i * 2.5,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Healthy basket — trend / mean-reversion composition (Req 6.1-6.4, 6.10, 6.11)
# ---------------------------------------------------------------------------


def test_compute_cross_sectional_returns_one_row_per_bundle() -> None:
    bundles = _healthy_basket()

    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )

    assert isinstance(result, CrossSectionalResult)
    assert len(result.rows) == len(bundles)
    assert [r.symbol for r in result.rows] == [b.symbol for b in bundles]


def test_compute_cross_sectional_emits_typed_rows() -> None:
    bundles = _healthy_basket()
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    assert all(isinstance(r, ScoredRow) for r in result.rows)


def test_compute_cross_sectional_z_scores_block_contains_five_signals_plus_subscores() -> None:
    """Req 6.10: the per-ticker ``z_scores`` block carries the five
    transformed signal keys plus ``trend_z`` / ``mean_reversion_z``."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )

    expected_keys = set(SCORER_SIGNAL_KEYS) | {"trend_z", "mean_reversion_z"}
    for row in result.rows:
        assert set(row.z_scores.keys()) == expected_keys


def test_compute_cross_sectional_z_scores_block_never_carries_earnings_fields() -> None:
    """Req 7.1: earnings fields are structurally excluded from composites."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="balanced",
    )
    for row in result.rows:
        assert "next_earnings_date" not in row.z_scores
        assert "days_to_next_earnings" not in row.z_scores
        assert "earnings_proximity_warning" not in row.z_scores


def test_compute_cross_sectional_per_signal_z_matches_manual_standardization() -> None:
    """Req 6.1: per-signal cross-sectional z is ``(v - mean) / stdev``."""

    bundles = _healthy_basket()
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )

    clenow_values = [b.clenow_126 for b in bundles]
    m = mean(clenow_values)
    sd = stdev(clenow_values)
    expected = [(v - m) / sd for v in clenow_values]

    for row, exp in zip(result.rows, expected):
        assert row.z_scores["clenow_126"] == pytest.approx(exp)


def test_compute_cross_sectional_subscore_uses_weighted_average_of_z() -> None:
    """Req 6.2: ``trend_z`` is the weight-normalized sum of available
    signal z-scores (clenow / macd / volume)."""

    bundles = _healthy_basket()
    weights = TrendWeights(clenow=0.50, macd=0.25, volume=0.25)
    result = compute_cross_sectional(
        bundles,
        trend_weights=weights,
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )

    row_0 = result.rows[0]
    z_c = row_0.z_scores["clenow_126"]
    z_m = row_0.z_scores["macd_histogram"]
    z_v = row_0.z_scores["volume_z_20d"]
    total_w = weights.clenow + weights.macd + weights.volume
    expected = (z_c * weights.clenow + z_m * weights.macd + z_v * weights.volume) / total_w
    assert row_0.trend_z == pytest.approx(expected)


def test_compute_cross_sectional_subscore_degrades_when_one_signal_missing() -> None:
    """Req 6.2: a missing signal drops out of the weight denominator
    rather than collapsing the whole sub-score."""

    # Strip the macd signal from every bundle in the basket — the
    # per-signal z-score collapses to None everywhere, but trend_z
    # should still fall through to the clenow + volume composition.
    bundles = [
        SignalBundle(
            symbol=b.symbol,
            ok=b.ok,
            clenow_126=b.clenow_126,
            macd_histogram=None,
            volume_z_20d=b.volume_z_20d,
            inv_range_pct_52w=b.inv_range_pct_52w,
            oversold_rsi_14=b.oversold_rsi_14,
        )
        for b in _healthy_basket()
    ]

    weights = TrendWeights(clenow=0.50, macd=0.25, volume=0.25)
    result = compute_cross_sectional(
        bundles,
        trend_weights=weights,
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )

    row_0 = result.rows[0]
    assert row_0.z_scores["macd_histogram"] is None
    z_c = row_0.z_scores["clenow_126"]
    z_v = row_0.z_scores["volume_z_20d"]
    expected = (z_c * weights.clenow + z_v * weights.volume) / (
        weights.clenow + weights.volume
    )
    assert row_0.trend_z == pytest.approx(expected)


def test_compute_cross_sectional_0_to_100_transform_applies_clip_and_offset() -> None:
    """Req 6.4: ``trend_score_0_100 = clip(50 + z * 25, 0, 100)``."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    for row in result.rows:
        assert row.trend_score_0_100 is not None
        expected = max(0.0, min(100.0, 50.0 + row.trend_z * 25.0))
        assert row.trend_score_0_100 == pytest.approx(expected)
        assert 0.0 <= row.trend_score_0_100 <= 100.0


def test_compute_cross_sectional_extreme_z_clips_at_100_floor_and_ceiling() -> None:
    """A large enough basket where one value dominates should saturate
    the ``clip(50 + z*25, 0, 100)`` transform at the 100 ceiling."""

    # With n=6 and one extreme outlier on clenow, z comfortably exceeds
    # 2.0, driving ``50 + z*25`` above 100 so the clip is exercised.
    bundles = [
        _bundle(
            f"T{i}",
            clenow_126=100.0 if i == 0 else 0.1,
            macd_histogram=0.0,
            volume_z_20d=0.0,
            inv_range_pct_52w=0.5,
            oversold_rsi_14=15.0,
        )
        for i in range(6)
    ]

    result = compute_cross_sectional(
        bundles,
        trend_weights=TrendWeights(clenow=1.0, macd=0.0, volume=0.0),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    assert result.rows[0].trend_score_0_100 == pytest.approx(100.0)
    # Lagging rows sit at or below 50.
    assert result.rows[1].trend_score_0_100 is not None
    assert result.rows[1].trend_score_0_100 <= 50.0


def test_compute_cross_sectional_basket_size_counts_eligible_rows() -> None:
    """Req 6.11: ``basket_size`` is the count of rows with ``ok=True``
    and at least one non-null SCORER_SIGNAL_KEYS value."""

    bundles = _healthy_basket()
    # Mark one row ok=False — it drops out of basket_size.
    bundles[0] = SignalBundle(
        symbol="T0",
        ok=False,
        clenow_126=None,
        macd_histogram=None,
        volume_z_20d=None,
        inv_range_pct_52w=None,
        oversold_rsi_14=None,
    )
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    for row in result.rows:
        assert row.basket_size == 4
        assert row.basket_size_sufficient is True


def test_compute_cross_sectional_basket_size_sufficient_flip_at_three() -> None:
    """``basket_size_sufficient = basket_size >= 3`` per row."""

    bundles = [
        _bundle("A", clenow_126=0.10, macd_histogram=0.20, volume_z_20d=0.30,
                inv_range_pct_52w=0.40, oversold_rsi_14=10.0),
        _bundle("B", clenow_126=0.15, macd_histogram=0.30, volume_z_20d=0.45,
                inv_range_pct_52w=0.45, oversold_rsi_14=12.5),
        _bundle("C", clenow_126=0.20, macd_histogram=0.40, volume_z_20d=0.60,
                inv_range_pct_52w=0.50, oversold_rsi_14=15.0),
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    assert all(r.basket_size == 3 for r in result.rows)
    assert all(r.basket_size_sufficient is True for r in result.rows)


# ---------------------------------------------------------------------------
# Per-signal small-basket collapse (Req 6.7)
# ---------------------------------------------------------------------------


def test_compute_cross_sectional_per_signal_collapses_when_fewer_than_three_non_nulls() -> None:
    """Req 6.7: with only two non-null values for one signal, that
    signal's z collapses to None everywhere and affected rows receive
    ``basket_too_small_for_z(<original_name>)``."""

    # Healthy five-row basket, but volume_z_20d is present on only two rows.
    bundles = [
        _bundle("A", clenow_126=0.10, macd_histogram=0.20, volume_z_20d=0.30,
                inv_range_pct_52w=0.40, oversold_rsi_14=10.0),
        _bundle("B", clenow_126=0.15, macd_histogram=0.30, volume_z_20d=0.45,
                inv_range_pct_52w=0.45, oversold_rsi_14=12.5),
        _bundle("C", clenow_126=0.20, macd_histogram=0.40, volume_z_20d=None,
                inv_range_pct_52w=0.50, oversold_rsi_14=15.0),
        _bundle("D", clenow_126=0.25, macd_histogram=0.50, volume_z_20d=None,
                inv_range_pct_52w=0.55, oversold_rsi_14=17.5),
        _bundle("E", clenow_126=0.30, macd_histogram=0.60, volume_z_20d=None,
                inv_range_pct_52w=0.60, oversold_rsi_14=20.0),
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )

    for row in result.rows:
        assert row.z_scores["volume_z_20d"] is None

    # Only the two rows that had the value get flagged — the rest had
    # null for this signal regardless.
    rows_with_flag = [
        row for row in result.rows
        if "basket_too_small_for_z(volume_z_20d)" in row.per_signal_small_basket_flags
    ]
    assert {r.symbol for r in rows_with_flag} == {"A", "B"}


def test_compute_cross_sectional_per_signal_flag_uses_original_signal_names() -> None:
    """The flag-string translation uses the **original** signal names
    from ``_SIGNAL_FLAG_NAME`` (``range_pct_52w`` / ``rsi_14``), not
    the transformed z_scores keys (``inv_range_pct_52w`` /
    ``oversold_rsi_14``) — agents recognise the original names from
    the ``signals.*`` block."""

    # Only two non-null values for inv_range_pct_52w → collapse the signal.
    bundles = [
        _bundle("A", inv_range_pct_52w=0.20, clenow_126=0.1, macd_histogram=0.1,
                volume_z_20d=0.1, oversold_rsi_14=10.0),
        _bundle("B", inv_range_pct_52w=0.40, clenow_126=0.15, macd_histogram=0.15,
                volume_z_20d=0.15, oversold_rsi_14=11.0),
        _bundle("C", inv_range_pct_52w=None, clenow_126=0.20, macd_histogram=0.20,
                volume_z_20d=0.20, oversold_rsi_14=12.0),
        _bundle("D", inv_range_pct_52w=None, clenow_126=0.25, macd_histogram=0.25,
                volume_z_20d=0.25, oversold_rsi_14=13.0),
        _bundle("E", inv_range_pct_52w=None, clenow_126=0.30, macd_histogram=0.30,
                volume_z_20d=0.30, oversold_rsi_14=14.0),
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    flags = [
        flag
        for row in result.rows
        for flag in row.per_signal_small_basket_flags
        if "basket_too_small_for_z" in flag
    ]
    # The flag uses `range_pct_52w`, not `inv_range_pct_52w`.
    assert "basket_too_small_for_z(range_pct_52w)" in flags
    assert "basket_too_small_for_z(inv_range_pct_52w)" not in flags


def test_compute_cross_sectional_per_signal_rsi_flag_uses_original_rsi_14() -> None:
    """Symmetric check for the ``oversold_rsi_14`` → ``rsi_14`` translation."""

    bundles = [
        _bundle("A", oversold_rsi_14=10.0, clenow_126=0.1, macd_histogram=0.1,
                volume_z_20d=0.1, inv_range_pct_52w=0.20),
        _bundle("B", oversold_rsi_14=12.0, clenow_126=0.15, macd_histogram=0.15,
                volume_z_20d=0.15, inv_range_pct_52w=0.30),
        _bundle("C", oversold_rsi_14=None, clenow_126=0.20, macd_histogram=0.20,
                volume_z_20d=0.20, inv_range_pct_52w=0.40),
        _bundle("D", oversold_rsi_14=None, clenow_126=0.25, macd_histogram=0.25,
                volume_z_20d=0.25, inv_range_pct_52w=0.50),
        _bundle("E", oversold_rsi_14=None, clenow_126=0.30, macd_histogram=0.30,
                volume_z_20d=0.30, inv_range_pct_52w=0.60),
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    flags = [
        flag
        for row in result.rows
        for flag in row.per_signal_small_basket_flags
        if "basket_too_small_for_z" in flag
    ]
    assert "basket_too_small_for_z(rsi_14)" in flags
    assert "basket_too_small_for_z(oversold_rsi_14)" not in flags


# ---------------------------------------------------------------------------
# Whole-basket short-circuit (Req 6.8)
# ---------------------------------------------------------------------------


def test_compute_cross_sectional_whole_basket_too_small_sets_scores_null() -> None:
    """Req 6.8: when fewer than three rows are eligible for scoring,
    every row's score is None and a top-level warning is queued. Raw
    per-ticker signals still emit (signals block is not our
    responsibility, but z_scores carry None as expected)."""

    bundles = [
        _bundle("A", clenow_126=0.1, macd_histogram=0.1, volume_z_20d=0.1,
                inv_range_pct_52w=0.4, oversold_rsi_14=10.0),
        _bundle("B", clenow_126=0.2, macd_histogram=0.2, volume_z_20d=0.2,
                inv_range_pct_52w=0.5, oversold_rsi_14=12.0),
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="balanced",
    )
    for row in result.rows:
        assert row.trend_score_0_100 is None
        assert row.mean_reversion_score_0_100 is None
        assert row.blended_score_0_100 is None
        assert row.basket_size == 2
        assert row.basket_size_sufficient is False

    assert result.basket_warning is not None
    assert result.basket_warning["symbol"] is None
    assert result.basket_warning["error"] == (
        "insufficient basket size for cross-sectional z-score"
    )
    assert result.basket_warning["error_category"] == "validation"


def test_compute_cross_sectional_whole_basket_ok_false_rows_do_not_count() -> None:
    """Rows with ``ok=False`` drop out of the eligible count even if
    they carry signal values — matching ``_classify_ticker_failure``'s
    partial-success handling."""

    bundles = [
        _bundle("A", ok=False, clenow_126=0.1, macd_histogram=0.1,
                volume_z_20d=0.1, inv_range_pct_52w=0.4, oversold_rsi_14=10.0),
        _bundle("B", ok=False, clenow_126=0.2, macd_histogram=0.2,
                volume_z_20d=0.2, inv_range_pct_52w=0.5, oversold_rsi_14=12.0),
        _bundle("C", clenow_126=0.3, macd_histogram=0.3, volume_z_20d=0.3,
                inv_range_pct_52w=0.6, oversold_rsi_14=14.0),
        _bundle("D", clenow_126=0.4, macd_histogram=0.4, volume_z_20d=0.4,
                inv_range_pct_52w=0.7, oversold_rsi_14=16.0),
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    assert all(r.basket_size == 2 for r in result.rows)
    assert all(r.basket_size_sufficient is False for r in result.rows)
    assert result.basket_warning is not None


def test_compute_cross_sectional_whole_basket_no_warning_on_healthy_basket() -> None:
    """``basket_warning`` is None when ≥3 rows are eligible."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    assert result.basket_warning is None


# ---------------------------------------------------------------------------
# Blend profile (Task 5.2 — Req 6.5, 6.6)
# ---------------------------------------------------------------------------


def test_compute_cross_sectional_blend_none_leaves_blended_null() -> None:
    """Req 6.6: under ``--blend-profile none`` the row carries
    ``blended_score_0_100 = None`` so the row builder can omit the
    field entirely."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="none",
    )
    for row in result.rows:
        assert row.blended_score_0_100 is None


def test_compute_cross_sectional_blend_trend_mirrors_trend_score_0_100() -> None:
    """Req 6.5: under ``--blend-profile trend`` the blend emits
    ``trend_score_0_100`` verbatim so the caller has one consistent
    field regardless of stance."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="trend",
    )
    for row in result.rows:
        assert row.blended_score_0_100 == row.trend_score_0_100


def test_compute_cross_sectional_blend_mean_reversion_mirrors_mean_reversion_score() -> None:
    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="mean_reversion",
    )
    for row in result.rows:
        assert row.blended_score_0_100 == row.mean_reversion_score_0_100


def test_compute_cross_sectional_blend_balanced_averages_trend_and_mean_reversion_z() -> None:
    """Req 6.5: ``balanced`` blends ``0.5 * trend_z + 0.5 *
    mean_reversion_z`` then applies the 0-100 transform so the blend
    sits on the same scale as the sub-scores."""

    result = compute_cross_sectional(
        _healthy_basket(),
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="balanced",
    )
    for row in result.rows:
        blended_z = 0.5 * row.trend_z + 0.5 * row.mean_reversion_z
        expected = max(0.0, min(100.0, 50.0 + blended_z * 25.0))
        assert row.blended_score_0_100 == pytest.approx(expected)


def test_compute_cross_sectional_blend_balanced_falls_through_when_one_subscore_is_null() -> None:
    """When one sub-score is None the balanced blend gracefully
    degrades to the other using the sum-of-available-weights pattern."""

    # Drop every mean-reversion input from the basket so mr_z is None
    # on every row, then verify that balanced falls back to trend.
    bundles = [
        SignalBundle(
            symbol=f"T{i}",
            ok=True,
            clenow_126=0.1 + i * 0.05,
            macd_histogram=0.2 + i * 0.10,
            volume_z_20d=0.3 + i * 0.15,
            inv_range_pct_52w=None,
            oversold_rsi_14=None,
        )
        for i in range(3)
    ]
    result = compute_cross_sectional(
        bundles,
        trend_weights=_default_trend_weights(),
        mean_reversion_weights=_default_mr_weights(),
        blend_profile="balanced",
    )
    for row in result.rows:
        assert row.mean_reversion_z is None
        # Balanced blend collapses to the trend sub-score.
        assert row.blended_score_0_100 == pytest.approx(row.trend_score_0_100)
