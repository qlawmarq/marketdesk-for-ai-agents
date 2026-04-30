"""Unit tests for `scripts/entry_timing_scorer.py` envelope-assembly layer.

Covers Task 7 from `docs/tasks/todo/entry-timing-scorer/tasks.md`:

- ``build_signals_block`` — twelve-field per-ticker signals dict
  (Req 9.3).
- ``build_ok_row`` — per-ticker ``ok: true`` row carrying the minimum
  schema from Req 9.1 plus ``blended_score_0_100`` /
  ``blend_profile`` only when the active profile is not ``none``
  (Req 9.1, 9.2, 6.6).
- ``build_failure_row`` — ``ok: false`` row omits score /
  z_score blocks, keeping only the envelope + failure fields
  (Req 9.8, 8.3).
- ``primary_score_of_row`` — picks the sort key dictated by the
  active blend profile (Req 6.9).
- ``sort_and_rank_rows`` — stable descending sort by primary score
  with ``null`` mapped to ``-inf`` (nulls sink), alphabetical-symbol
  tie-break, then 1-based dense rank; null-scored / ``ok: false``
  rows receive ``rank: None`` (Req 6.9, 9.1 rank field).
- ``build_data_namespace`` — assembles the ``data`` siblings
  (``provider``, ``tickers``, ``weights``, ``days_to_next_earnings_unit``,
  ``earnings_window_days``, ``earnings_proximity_days_threshold``,
  ``missing_tickers``, ``analytical_caveats``, optional
  ``provider_diagnostics``). ``provider_diagnostics`` is only emitted
  when at least one stage failed (Req 3.5, 3.6, 8.2, 9.9).
"""

from __future__ import annotations

import pytest

from entry_timing_scorer import (  # type: ignore[import-not-found]
    ANALYTICAL_CAVEATS,
    MeanReversionWeights,
    ScorerConfig,
    TrendWeights,
    build_data_namespace,
    build_failure_row,
    build_ok_row,
    build_signals_block,
    primary_score_of_row,
    sort_and_rank_rows,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# build_signals_block (Req 9.3)
# ---------------------------------------------------------------------------


def test_build_signals_block_contains_twelve_named_fields() -> None:
    """Req 9.3: the ``signals`` block carries exactly these twelve keys."""

    signals = build_signals_block(
        clenow_126=0.1,
        range_pct_52w=0.2,
        rsi_14=55.0,
        macd_histogram=0.3,
        volume_z_20d=1.2,
        ma200_distance=0.05,
        last_price=100.0,
        year_high=120.0,
        year_low=80.0,
        ma_200d=95.0,
        ma_50d=98.0,
        latest_volume=1_000_000.0,
    )

    expected_keys = {
        "clenow_126",
        "range_pct_52w",
        "rsi_14",
        "macd_histogram",
        "volume_z_20d",
        "ma200_distance",
        "last_price",
        "year_high",
        "year_low",
        "ma_200d",
        "ma_50d",
        "latest_volume",
    }
    assert set(signals.keys()) == expected_keys


def test_build_signals_block_passes_values_verbatim() -> None:
    signals = build_signals_block(
        clenow_126=0.42,
        range_pct_52w=None,
        rsi_14=19.0,
        macd_histogram=None,
        volume_z_20d=2.5,
        ma200_distance=0.01,
        last_price=101.0,
        year_high=120.0,
        year_low=80.0,
        ma_200d=None,
        ma_50d=98.0,
        latest_volume=None,
    )

    assert signals["clenow_126"] == pytest.approx(0.42)
    assert signals["range_pct_52w"] is None
    assert signals["rsi_14"] == pytest.approx(19.0)
    assert signals["macd_histogram"] is None
    assert signals["volume_z_20d"] == pytest.approx(2.5)
    assert signals["ma_200d"] is None
    assert signals["latest_volume"] is None


# ---------------------------------------------------------------------------
# build_ok_row (Req 9.1, 9.2, 6.6)
# ---------------------------------------------------------------------------


def _full_signals() -> dict:
    return build_signals_block(
        clenow_126=0.1,
        range_pct_52w=0.2,
        rsi_14=55.0,
        macd_histogram=0.3,
        volume_z_20d=1.2,
        ma200_distance=0.05,
        last_price=100.0,
        year_high=120.0,
        year_low=80.0,
        ma_200d=95.0,
        ma_50d=98.0,
        latest_volume=1_000_000.0,
    )


def _interp(context: str = "watchlist") -> dict:
    return {
        "score_meaning": "basket_internal_rank",
        "trend_polarity": "high=stronger_trend",
        "mean_reversion_polarity": "high=more_oversold",
        "context": context,
        "reading_for_context": "entry_candidate_if_high_scores",
    }


def _volume_reference() -> dict:
    return {
        "volume_average": {"window": "3m_rolling", "value": 1_500_000.0},
        "volume_average_10d": {"window": "10d", "value": 2_000_000.0},
    }


def test_build_ok_row_minimum_fields_present_under_profile_none() -> None:
    """Req 9.1: every ``ok: true`` row carries the minimum field set."""

    row = build_ok_row(
        symbol="ASC",
        provider="yfinance",
        context="watchlist",
        signals=_full_signals(),
        z_scores={"clenow_126": 0.5, "trend_z": 0.7, "mean_reversion_z": -0.2,
                  "macd_histogram": 0.2, "volume_z_20d": 1.0,
                  "inv_range_pct_52w": 0.3, "oversold_rsi_14": -0.5},
        trend_score_0_100=72.5,
        mean_reversion_score_0_100=45.0,
        blended_score_0_100=None,
        blend_profile="none",
        basket_size=5,
        basket_size_sufficient=True,
        next_earnings_date="2026-05-06",
        days_to_next_earnings=6,
        earnings_proximity_warning=False,
        volume_avg_window="20d_real",
        volume_z_estimator="robust",
        volume_reference=_volume_reference(),
        data_quality_flags=[],
        interpretation=_interp(),
    )

    required = {
        "symbol",
        "provider",
        "ok",
        "context",
        "rank",
        "trend_score_0_100",
        "mean_reversion_score_0_100",
        "signals",
        "z_scores",
        "basket_size",
        "basket_size_sufficient",
        "next_earnings_date",
        "days_to_next_earnings",
        "earnings_proximity_warning",
        "volume_avg_window",
        "volume_z_estimator",
        "volume_reference",
        "data_quality_flags",
        "interpretation",
    }
    assert required.issubset(row.keys())
    assert row["ok"] is True
    assert row["rank"] is None  # rank assigned later by sort_and_rank_rows


def test_build_ok_row_profile_none_omits_blended_and_profile_fields() -> None:
    """Req 6.6: under profile ``none`` the blend fields are omitted
    entirely — not emitted as ``null``."""

    row = build_ok_row(
        symbol="ASC",
        provider="yfinance",
        context="watchlist",
        signals=_full_signals(),
        z_scores={},
        trend_score_0_100=72.5,
        mean_reversion_score_0_100=45.0,
        blended_score_0_100=None,
        blend_profile="none",
        basket_size=5,
        basket_size_sufficient=True,
        next_earnings_date=None,
        days_to_next_earnings=None,
        earnings_proximity_warning=False,
        volume_avg_window="20d_real",
        volume_z_estimator="robust",
        volume_reference=_volume_reference(),
        data_quality_flags=[],
        interpretation=_interp(),
    )

    assert "blended_score_0_100" not in row
    assert "blend_profile" not in row


def test_build_ok_row_profile_balanced_includes_blended_and_profile_fields() -> None:
    """Req 9.2: under any non-``none`` profile the row carries
    ``blended_score_0_100`` plus the ``blend_profile`` echo."""

    row = build_ok_row(
        symbol="ASC",
        provider="yfinance",
        context="watchlist",
        signals=_full_signals(),
        z_scores={},
        trend_score_0_100=72.5,
        mean_reversion_score_0_100=45.0,
        blended_score_0_100=58.75,
        blend_profile="balanced",
        basket_size=5,
        basket_size_sufficient=True,
        next_earnings_date=None,
        days_to_next_earnings=None,
        earnings_proximity_warning=False,
        volume_avg_window="20d_real",
        volume_z_estimator="robust",
        volume_reference=_volume_reference(),
        data_quality_flags=[],
        interpretation=_interp(),
    )

    assert row["blended_score_0_100"] == pytest.approx(58.75)
    assert row["blend_profile"] == "balanced"


def test_build_ok_row_trend_profile_echoes_trend_score() -> None:
    row = build_ok_row(
        symbol="ASC",
        provider="yfinance",
        context="watchlist",
        signals=_full_signals(),
        z_scores={},
        trend_score_0_100=72.5,
        mean_reversion_score_0_100=45.0,
        blended_score_0_100=72.5,
        blend_profile="trend",
        basket_size=5,
        basket_size_sufficient=True,
        next_earnings_date=None,
        days_to_next_earnings=None,
        earnings_proximity_warning=False,
        volume_avg_window="20d_real",
        volume_z_estimator="robust",
        volume_reference=_volume_reference(),
        data_quality_flags=[],
        interpretation=_interp(),
    )
    assert row["blend_profile"] == "trend"
    assert row["blended_score_0_100"] == pytest.approx(72.5)


# ---------------------------------------------------------------------------
# build_failure_row (Req 9.8, 8.3)
# ---------------------------------------------------------------------------


def test_build_failure_row_carries_only_envelope_and_failure_fields() -> None:
    """Req 9.8: ``ok: false`` rows omit score / z_score blocks while
    retaining ``{symbol, provider, context, error, error_type,
    error_category}``."""

    row = build_failure_row(
        symbol="ASC",
        provider="yfinance",
        context="watchlist",
        error="HTTP 404",
        error_type="HTTPError",
        error_category="other",
    )
    assert row["ok"] is False
    assert row["symbol"] == "ASC"
    assert row["provider"] == "yfinance"
    assert row["context"] == "watchlist"
    assert row["error"] == "HTTP 404"
    assert row["error_type"] == "HTTPError"
    assert row["error_category"] == "other"
    # Score / z_score blocks are absent.
    assert "trend_score_0_100" not in row
    assert "mean_reversion_score_0_100" not in row
    assert "blended_score_0_100" not in row
    assert "z_scores" not in row


# ---------------------------------------------------------------------------
# primary_score_of_row (Req 6.9)
# ---------------------------------------------------------------------------


def test_primary_score_of_row_none_profile_returns_trend_score() -> None:
    row = {"trend_score_0_100": 72.5, "mean_reversion_score_0_100": 45.0}
    assert primary_score_of_row(row, "none") == pytest.approx(72.5)


def test_primary_score_of_row_trend_profile_returns_trend_score() -> None:
    row = {"trend_score_0_100": 72.5, "mean_reversion_score_0_100": 45.0}
    assert primary_score_of_row(row, "trend") == pytest.approx(72.5)


def test_primary_score_of_row_mean_reversion_profile_returns_mean_reversion() -> None:
    row = {"trend_score_0_100": 72.5, "mean_reversion_score_0_100": 45.0}
    assert primary_score_of_row(row, "mean_reversion") == pytest.approx(45.0)


def test_primary_score_of_row_balanced_profile_returns_blended() -> None:
    row = {
        "trend_score_0_100": 72.5,
        "mean_reversion_score_0_100": 45.0,
        "blended_score_0_100": 58.75,
    }
    assert primary_score_of_row(row, "balanced") == pytest.approx(58.75)


def test_primary_score_of_row_failure_row_returns_none() -> None:
    """``ok: false`` rows have no score fields; the helper returns
    ``None`` so ``sort_and_rank_rows`` can sink them."""

    row = {"ok": False, "symbol": "X"}
    assert primary_score_of_row(row, "none") is None
    assert primary_score_of_row(row, "balanced") is None


# ---------------------------------------------------------------------------
# sort_and_rank_rows (Req 6.9, 9.1 rank field, Design §Envelope Assembler)
# ---------------------------------------------------------------------------


def _ok_row(symbol: str, trend: float | None = None, mr: float | None = None,
            blended: float | None = None) -> dict:
    row = {
        "symbol": symbol,
        "ok": True,
        "context": "watchlist",
        "trend_score_0_100": trend,
        "mean_reversion_score_0_100": mr,
        "rank": None,
    }
    if blended is not None:
        row["blended_score_0_100"] = blended
    return row


def _fail_row(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "ok": False,
        "context": "watchlist",
        "rank": None,
    }


def test_sort_and_rank_rows_descending_by_trend_under_profile_none() -> None:
    rows = [
        _ok_row("B", trend=45.0),
        _ok_row("A", trend=72.5),
        _ok_row("C", trend=60.0),
    ]
    sorted_rows = sort_and_rank_rows(rows, "none")
    assert [r["symbol"] for r in sorted_rows] == ["A", "C", "B"]
    assert [r["rank"] for r in sorted_rows] == [1, 2, 3]


def test_sort_and_rank_rows_null_score_rows_sink_to_bottom_with_null_rank() -> None:
    """Nulls sink to the bottom and receive ``rank: None``."""

    rows = [
        _ok_row("A", trend=None),
        _ok_row("B", trend=45.0),
        _ok_row("C", trend=72.5),
    ]
    sorted_rows = sort_and_rank_rows(rows, "none")
    assert [r["symbol"] for r in sorted_rows] == ["C", "B", "A"]
    assert [r["rank"] for r in sorted_rows] == [1, 2, None]


def test_sort_and_rank_rows_failure_rows_receive_null_rank_and_sink() -> None:
    rows = [
        _fail_row("A"),
        _ok_row("B", trend=45.0),
        _ok_row("C", trend=72.5),
    ]
    sorted_rows = sort_and_rank_rows(rows, "none")
    assert [r["symbol"] for r in sorted_rows] == ["C", "B", "A"]
    assert sorted_rows[0]["rank"] == 1
    assert sorted_rows[1]["rank"] == 2
    assert sorted_rows[2]["rank"] is None


def test_sort_and_rank_rows_dense_rank_ties_share_rank() -> None:
    """Design §Envelope Assembler: dense rank — ties share the same rank
    and the next distinct score increments by 1 (not by the tie count)."""

    rows = [
        _ok_row("A", trend=72.0),
        _ok_row("B", trend=72.0),
        _ok_row("C", trend=45.0),
        _fail_row("D"),
    ]
    sorted_rows = sort_and_rank_rows(rows, "none")
    ranks_by_symbol = {r["symbol"]: r["rank"] for r in sorted_rows}
    assert ranks_by_symbol["A"] == 1
    assert ranks_by_symbol["B"] == 1
    assert ranks_by_symbol["C"] == 2  # dense rank: next distinct score is 2, not 3
    assert ranks_by_symbol["D"] is None


def test_sort_and_rank_rows_tie_break_is_alphabetical_symbol() -> None:
    """Tie rows share a rank but emit in ascending-symbol order inside
    the tie group (Design Decision 4)."""

    rows = [
        _ok_row("B", trend=72.0),
        _ok_row("A", trend=72.0),
        _ok_row("C", trend=45.0),
    ]
    sorted_rows = sort_and_rank_rows(rows, "none")
    assert [r["symbol"] for r in sorted_rows] == ["A", "B", "C"]


def test_sort_and_rank_rows_balanced_profile_sorts_on_blended_score() -> None:
    rows = [
        _ok_row("A", trend=72.5, mr=30.0, blended=51.25),
        _ok_row("B", trend=40.0, mr=80.0, blended=60.0),
        _ok_row("C", trend=60.0, mr=60.0, blended=60.0),
    ]
    sorted_rows = sort_and_rank_rows(rows, "balanced")
    # Tie on B/C blended=60.0 — alphabetical tie-break gives B, C.
    assert [r["symbol"] for r in sorted_rows] == ["B", "C", "A"]
    assert [r["rank"] for r in sorted_rows] == [1, 1, 2]


def test_sort_and_rank_rows_mean_reversion_profile_sorts_on_mr_score() -> None:
    rows = [
        _ok_row("A", trend=72.5, mr=30.0),
        _ok_row("B", trend=40.0, mr=80.0),
        _ok_row("C", trend=60.0, mr=45.0),
    ]
    sorted_rows = sort_and_rank_rows(rows, "mean_reversion")
    assert [r["symbol"] for r in sorted_rows] == ["B", "C", "A"]


def test_sort_and_rank_rows_all_null_scores_leaves_every_rank_null() -> None:
    rows = [
        _ok_row("A", trend=None),
        _fail_row("B"),
        _ok_row("C", trend=None),
    ]
    sorted_rows = sort_and_rank_rows(rows, "none")
    # Alphabetical within the null-bucket.
    assert [r["symbol"] for r in sorted_rows] == ["A", "B", "C"]
    assert all(r["rank"] is None for r in sorted_rows)


# ---------------------------------------------------------------------------
# build_data_namespace (Req 3.5, 3.6, 8.2, 9.9)
# ---------------------------------------------------------------------------


def _basic_config(blend_profile: str = "none") -> ScorerConfig:
    return ScorerConfig(
        tickers=["ASC", "CMCL"],
        contexts={"ASC": "watchlist", "CMCL": "watchlist"},
        provider="yfinance",
        calendar_provider="nasdaq",
        earnings_window_days=45,
        earnings_proximity_days=5,
        volume_z_estimator="robust",
        blend_profile=blend_profile,
        trend_weights=TrendWeights(clenow=0.5, macd=0.25, volume=0.25),
        mean_reversion_weights=MeanReversionWeights(range=0.6, rsi=0.4),
    )


def test_build_data_namespace_carries_required_fields() -> None:
    """Req 8.2 / 3.6 / 9.9: data siblings include provider, tickers,
    weights, days_to_next_earnings_unit, earnings_window_days,
    earnings_proximity_days_threshold, missing_tickers,
    analytical_caveats."""

    data = build_data_namespace(
        config=_basic_config(),
        missing_tickers=[],
        provider_diagnostics=[],
    )
    assert data["provider"] == "yfinance"
    assert data["tickers"] == ["ASC", "CMCL"]
    assert data["weights"] == {
        "trend": {"clenow": 0.5, "macd": 0.25, "volume": 0.25},
        "mean_reversion": {"range": 0.6, "rsi": 0.4},
    }
    assert data["days_to_next_earnings_unit"] == "calendar_days"
    assert data["earnings_window_days"] == 45
    assert data["earnings_proximity_days_threshold"] == 5
    assert data["missing_tickers"] == []
    assert data["analytical_caveats"] == list(ANALYTICAL_CAVEATS)


def test_build_data_namespace_analytical_caveats_carries_three_required_strings() -> None:
    """Req 9.9: the three caveat strings must travel in the envelope."""

    data = build_data_namespace(
        config=_basic_config(),
        missing_tickers=[],
        provider_diagnostics=[],
    )
    required = {
        "scores_are_basket_internal_ranks_not_absolute_strength",
        "trend_and_mean_reversion_are_separate_axes",
        "earnings_proximity_is_flag_not_score_component",
    }
    assert required.issubset(set(data["analytical_caveats"]))


def test_build_data_namespace_omits_provider_diagnostics_when_none() -> None:
    """Design §Envelope Assembler: ``provider_diagnostics`` is only
    present under ``data`` when at least one stage failed."""

    data = build_data_namespace(
        config=_basic_config(),
        missing_tickers=[],
        provider_diagnostics=[],
    )
    assert "provider_diagnostics" not in data


def test_build_data_namespace_includes_provider_diagnostics_when_populated() -> None:
    diagnostics = [
        {
            "provider": "nasdaq",
            "stage": "earnings_calendar",
            "error": "HTTP 403",
            "error_category": "other",
        }
    ]
    data = build_data_namespace(
        config=_basic_config(),
        missing_tickers=["TLT"],
        provider_diagnostics=diagnostics,
    )
    assert data["provider_diagnostics"] == diagnostics
    assert data["missing_tickers"] == ["TLT"]


def test_build_data_namespace_reserved_envelope_keys_are_not_present() -> None:
    """Envelope-root keys (source / collected_at / tool / results) are
    owned by ``wrap()`` / ``aggregate_emit`` and must not leak into the
    data namespace the helper builds."""

    data = build_data_namespace(
        config=_basic_config(),
        missing_tickers=[],
        provider_diagnostics=[],
    )
    for reserved in ("source", "collected_at", "tool", "results"):
        assert reserved not in data
