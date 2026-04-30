"""Unit tests for `scripts/entry_timing_scorer.py` flags / interpretation layer.

Covers Task 6 from `docs/tasks/todo/entry-timing-scorer/tasks.md`:

- Task 6.1: ``compute_proximity_flag`` — returns
  ``EarningsFlagFields(next_earnings_date, days_to_next_earnings,
  earnings_proximity_warning)``; the warning is a boolean on every
  path while the underlying date / day-count may be null; earnings
  fields stay outside the score composites.
- Task 6.2: ``build_interpretation`` — exactly five keys with literal
  string values; context → reading_for_context mapping; negative
  invariant: no ``interpretation_hint`` key is ever constructed.
- Task 6.3: ``DATA_QUALITY_FLAGS`` closed-enumeration frozenset
  (15 members), ``append_quality_flag`` validation, and
  ``collect_data_quality_flags`` row-level aggregation including
  ``rsi_oversold_lt_20`` and ``basket_too_small_for_z``.
"""

from __future__ import annotations

from datetime import date

import pytest

import entry_timing_scorer  # type: ignore[import-not-found]
from entry_timing_scorer import (  # type: ignore[import-not-found]
    DATA_QUALITY_FLAGS,
    EarningsFlagFields,
    EarningsIndex,
    append_quality_flag,
    build_interpretation,
    collect_data_quality_flags,
    compute_proximity_flag,
)

pytestmark = pytest.mark.unit


TODAY = date(2026, 4, 30)


def _index(by_symbol: dict[str, date]) -> EarningsIndex:
    return EarningsIndex(by_symbol=by_symbol, diagnostic=None)


# ---------------------------------------------------------------------------
# Task 6.1 — compute_proximity_flag (Req 7.1, 7.3, 7.4, 7.5)
# ---------------------------------------------------------------------------


def test_compute_proximity_flag_returns_earnings_flag_fields_instance() -> None:
    result = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 6)}),
        today=TODAY,
        threshold_days=5,
    )
    assert isinstance(result, EarningsFlagFields)


def test_compute_proximity_flag_emits_iso_date_string_and_integer_days() -> None:
    """Req 7.5: raw next_earnings_date is ISO string, days_to_next_earnings
    is an integer; both emit on every row carrying an earnings date."""

    result = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 6)}),
        today=TODAY,
        threshold_days=5,
    )
    assert result.next_earnings_date == "2026-05-06"
    assert isinstance(result.next_earnings_date, str)
    assert result.days_to_next_earnings == 6
    assert isinstance(result.days_to_next_earnings, int)


def test_compute_proximity_flag_warning_true_when_within_threshold() -> None:
    """Req 7.3: ``days <= threshold`` → warning=true."""

    result = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 3)}),
        today=TODAY,
        threshold_days=5,
    )
    assert result.days_to_next_earnings == 3
    assert result.earnings_proximity_warning is True


def test_compute_proximity_flag_warning_true_at_exact_threshold_boundary() -> None:
    """Equality counts as "within threshold" per Req 7.3 (``<=``)."""

    result = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 5)}),
        today=TODAY,
        threshold_days=5,
    )
    assert result.days_to_next_earnings == 5
    assert result.earnings_proximity_warning is True


def test_compute_proximity_flag_warning_false_when_beyond_threshold() -> None:
    """Req 7.4: days > threshold → warning=false."""

    result = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 10)}),
        today=TODAY,
        threshold_days=5,
    )
    assert result.days_to_next_earnings == 10
    assert result.earnings_proximity_warning is False


def test_compute_proximity_flag_absent_ticker_returns_nulls_and_warning_false() -> None:
    """Req 7.4 + 3.4: when no earnings row exists for the ticker, every
    earnings field is null and the warning is false (never null)."""

    result = compute_proximity_flag(
        "FLXS",
        _index({"ASC": date(2026, 5, 6)}),
        today=TODAY,
        threshold_days=5,
    )
    assert result.next_earnings_date is None
    assert result.days_to_next_earnings is None
    assert result.earnings_proximity_warning is False


def test_compute_proximity_flag_warning_is_always_a_boolean_type() -> None:
    """The flag is a boolean on every path including the null path."""

    present = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 6)}),
        today=TODAY,
        threshold_days=5,
    )
    absent = compute_proximity_flag(
        "FLXS",
        _index({"ASC": date(2026, 5, 6)}),
        today=TODAY,
        threshold_days=5,
    )
    assert isinstance(present.earnings_proximity_warning, bool)
    assert isinstance(absent.earnings_proximity_warning, bool)


def test_compute_proximity_flag_threshold_zero_fires_only_on_same_day() -> None:
    """Threshold of 0 means "only fire on the day of earnings"."""

    same_day = compute_proximity_flag(
        "ASC",
        _index({"ASC": TODAY}),
        today=TODAY,
        threshold_days=0,
    )
    next_day = compute_proximity_flag(
        "ASC",
        _index({"ASC": date(2026, 5, 1)}),
        today=TODAY,
        threshold_days=0,
    )
    assert same_day.days_to_next_earnings == 0
    assert same_day.earnings_proximity_warning is True
    assert next_day.days_to_next_earnings == 1
    assert next_day.earnings_proximity_warning is False


# ---------------------------------------------------------------------------
# Task 6.2 — build_interpretation (Req 9.4, 9.5, 11.7)
# ---------------------------------------------------------------------------


def test_build_interpretation_returns_dict_with_exactly_five_keys() -> None:
    """Req 9.4: the interpretation object carries exactly five keys."""

    result = build_interpretation("watchlist")
    assert set(result.keys()) == {
        "score_meaning",
        "trend_polarity",
        "mean_reversion_polarity",
        "context",
        "reading_for_context",
    }


def test_build_interpretation_uses_literal_string_values() -> None:
    """Req 9.4: fixed literal strings for score_meaning / polarity fields."""

    result = build_interpretation("watchlist")
    assert result["score_meaning"] == "basket_internal_rank"
    assert result["trend_polarity"] == "high=stronger_trend"
    assert result["mean_reversion_polarity"] == "high=more_oversold"


def test_build_interpretation_watchlist_context_reading() -> None:
    result = build_interpretation("watchlist")
    assert result["context"] == "watchlist"
    assert result["reading_for_context"] == "entry_candidate_if_high_scores"


def test_build_interpretation_holding_context_reading() -> None:
    result = build_interpretation("holding")
    assert result["context"] == "holding"
    assert result["reading_for_context"] == (
        "hold_or_add_if_high_trend,reconsider_if_high_mean_reversion"
    )


def test_build_interpretation_unknown_context_reading() -> None:
    result = build_interpretation("unknown")
    assert result["context"] == "unknown"
    assert result["reading_for_context"] == "ambiguous_without_context"


def test_build_interpretation_never_emits_interpretation_hint_key() -> None:
    """Req 9.5 negative invariant: the ``interpretation_hint`` scalar
    must never appear in the returned dict — enforced by absence."""

    for context in ("watchlist", "holding", "unknown"):
        result = build_interpretation(context)
        assert "interpretation_hint" not in result


def test_build_interpretation_module_never_declares_interpretation_hint() -> None:
    """The module literally does not construct a scalar named
    ``interpretation_hint`` anywhere — verified via module-level
    symbol + source-text absence so a future contributor cannot
    silently re-introduce it under a different helper."""

    # Module namespace check.
    assert not hasattr(entry_timing_scorer, "interpretation_hint")
    assert not hasattr(entry_timing_scorer, "INTERPRETATION_HINT")

    # Source text check — the token must not appear at all so the
    # negative invariant cannot regress through a new helper.
    from pathlib import Path

    module_file = Path(entry_timing_scorer.__file__)
    source = module_file.read_text()
    assert "interpretation_hint" not in source


# ---------------------------------------------------------------------------
# Task 6.3 — DATA_QUALITY_FLAGS catalog + append validator (Req 9.6, 9.7, 9.10)
# ---------------------------------------------------------------------------


def test_data_quality_flags_is_a_frozenset_of_strings() -> None:
    assert isinstance(DATA_QUALITY_FLAGS, frozenset)
    assert all(isinstance(f, str) for f in DATA_QUALITY_FLAGS)


def test_data_quality_flags_carries_exactly_fifteen_members() -> None:
    """Req 9.10: closed enumeration with fifteen entries."""

    assert len(DATA_QUALITY_FLAGS) == 15


def test_data_quality_flags_catalog_matches_req_910_verbatim() -> None:
    """Req 9.10: the closed enumeration must match the requirements
    document verbatim — drift is an explicit contract change."""

    expected = {
        "rsi_oversold_lt_20",
        "basket_too_small_for_z",
        "basket_too_small_for_z(clenow_126)",
        "basket_too_small_for_z(macd_histogram)",
        "basket_too_small_for_z(volume_z_20d)",
        "basket_too_small_for_z(range_pct_52w)",
        "basket_too_small_for_z(rsi_14)",
        "volume_window_too_short",
        "volume_zero_dispersion",
        "volume_non_positive",
        "volume_reference_unavailable_on_provider",
        "last_price_from_prev_close",
        "last_price_from_historical_close",
        "last_price_unavailable",
        "context_duplicate_positions_and_watchlist",
    }
    assert DATA_QUALITY_FLAGS == expected


def test_append_quality_flag_appends_known_flag_to_list() -> None:
    flags: list[str] = []
    append_quality_flag(flags, "rsi_oversold_lt_20")
    assert flags == ["rsi_oversold_lt_20"]


def test_append_quality_flag_rejects_unknown_flag_at_append_time() -> None:
    """Req 9.10 structural guard: an unknown flag must raise so the
    closed-enumeration contract holds at development time."""

    flags: list[str] = []
    with pytest.raises(ValueError, match="not a member"):
        append_quality_flag(flags, "totally_unknown_flag_name")
    assert flags == []


def test_append_quality_flag_accepts_every_member_of_the_catalog() -> None:
    """Smoke test: every catalog entry passes validation."""

    for flag in DATA_QUALITY_FLAGS:
        sink: list[str] = []
        append_quality_flag(sink, flag)
        assert sink == [flag]


def test_collect_data_quality_flags_appends_rsi_oversold_lt_20_when_rsi_below_20() -> None:
    """Req 9.6: rsi_14 < 20 → append rsi_oversold_lt_20."""

    flags = collect_data_quality_flags(
        rsi_14=17.5,
        basket_size_sufficient=True,
        upstream_flags=[],
    )
    assert "rsi_oversold_lt_20" in flags


def test_collect_data_quality_flags_does_not_append_rsi_flag_at_boundary() -> None:
    """Req 9.6 uses strict ``<`` so rsi_14 == 20 must not fire."""

    flags = collect_data_quality_flags(
        rsi_14=20.0,
        basket_size_sufficient=True,
        upstream_flags=[],
    )
    assert "rsi_oversold_lt_20" not in flags


def test_collect_data_quality_flags_does_not_append_rsi_flag_when_null() -> None:
    """A null rsi never fires the oversold flag."""

    flags = collect_data_quality_flags(
        rsi_14=None,
        basket_size_sufficient=True,
        upstream_flags=[],
    )
    assert "rsi_oversold_lt_20" not in flags


def test_collect_data_quality_flags_appends_basket_too_small_when_insufficient() -> None:
    """Req 9.7: basket_size_sufficient=False → append
    ``basket_too_small_for_z`` at the row level."""

    flags = collect_data_quality_flags(
        rsi_14=50.0,
        basket_size_sufficient=False,
        upstream_flags=[],
    )
    assert "basket_too_small_for_z" in flags


def test_collect_data_quality_flags_omits_basket_flag_when_sufficient() -> None:
    flags = collect_data_quality_flags(
        rsi_14=50.0,
        basket_size_sufficient=True,
        upstream_flags=[],
    )
    assert "basket_too_small_for_z" not in flags


def test_collect_data_quality_flags_passes_upstream_flags_through() -> None:
    """Upstream-produced flags (last-price fallback, volume gates,
    context duplicates, per-signal small-basket) are preserved in the
    emitted list, letting each producer own its own flag type."""

    upstream = [
        "last_price_from_prev_close",
        "volume_window_too_short",
        "context_duplicate_positions_and_watchlist",
        "basket_too_small_for_z(clenow_126)",
    ]
    flags = collect_data_quality_flags(
        rsi_14=18.0,
        basket_size_sufficient=False,
        upstream_flags=upstream,
    )
    for entry in upstream:
        assert entry in flags
    assert "rsi_oversold_lt_20" in flags
    assert "basket_too_small_for_z" in flags


def test_collect_data_quality_flags_rejects_unknown_upstream_flag() -> None:
    """Req 9.10 structural guard: a bogus upstream flag must fail the
    closed-enumeration check rather than silently leak into the row."""

    with pytest.raises(ValueError, match="not a member"):
        collect_data_quality_flags(
            rsi_14=None,
            basket_size_sufficient=True,
            upstream_flags=["made_up_flag"],
        )


def test_collect_data_quality_flags_every_output_flag_is_in_the_catalog() -> None:
    """Negative invariant: every emitted flag is a member of the catalog."""

    flags = collect_data_quality_flags(
        rsi_14=17.0,
        basket_size_sufficient=False,
        upstream_flags=[
            "last_price_from_historical_close",
            "volume_reference_unavailable_on_provider",
            "basket_too_small_for_z(volume_z_20d)",
        ],
    )
    for f in flags:
        assert f in DATA_QUALITY_FLAGS
