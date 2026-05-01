"""Unit tests for `scripts/sector_stock_screener.py`.

Covers Task 1–2 from `docs/tasks/todo/sector-stock-screener/tasks.md`:

- Task 1.1 — module-scope constants exist with the expected shape and
  structurally exclude analyst-revision-momentum keys (Req 11.5).
- Task 1.2 — ``append_quality_flag`` validates every appended string
  against the closed ``DATA_QUALITY_FLAGS`` catalog (Req 10.7) and
  forbids ``non_us_tickers_filtered_from_pool`` (which lives on
  ``data.analytical_caveats`` only).
- Task 2.1 — ``build_config`` + argparse wiring + second-stage bounds
  validator (Req 1.1–1.6, 2.1, 3.2, 3.5, 4.2, 7.6).
- Task 2.2 — fail-fast credential gate on missing ``FMP_API_KEY``
  (Req 2.1, 2.2).
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

import sector_stock_screener  # type: ignore[import-not-found]
from sector_stock_screener import (  # type: ignore[import-not-found]
    ANALYTICAL_CAVEATS_BASE,
    DATA_QUALITY_FLAGS,
    DEFAULT_PRICE_TARGET_LIMIT,
    DEFAULT_TOP_SECTORS,
    DEFAULT_TOP_STOCKS_PER_SECTOR,
    SUBSCORE_SIGNAL_KEYS,
    ScreenerConfig,
    SectorRankRow,
    SectorRankWeights,
    TopLevelWeights,
    _ANALYST_COUNT_WINDOW_DAYS,
    _ANALYST_COVERAGE_THRESHOLD,
    _ConfigError,
    _FMP,
    _FMP_METRIC_ALIASES,
    _MIN_BASKET_SIZE,
    _NON_US_SUFFIX_RE,
    _NORMALIZATION_SCOPE,
    _SPDR_GICS_SECTOR,
    _SUBSCORE_INTERNAL_WEIGHTS,
    _compute_perf_record_from_rows,
    _parse_ticker_csv,
    _split_history_by_symbol,
    append_quality_flag,
    build_config,
    build_pool,
    check_fmp_credential,
    fetch_etf_holdings,
    main,
    sector_ranks_envelope_rows,
    select_top_sectors,
)
from sector_stock_screener import HoldingsRow, PoolBuildOutcome, StockPoolEntry  # type: ignore[import-not-found]
from sector_stock_screener import (  # type: ignore[import-not-found]
    LastPriceResolution,
    _extract_consensus_fields,
    _extract_metrics_fields,
    _extract_quote_fields,
    _index_by_symbol,
    derive_number_of_analysts,
    fetch_consensus_batched,
    fetch_metrics_batched,
    fetch_price_target_batched,
    fetch_quotes_batched,
    fetch_stock_clenow_fmp,
    resolve_last_price,
)
from sector_stock_screener import (  # type: ignore[import-not-found]
    DerivedIndicators,
    ScoredRow,
    _classify_stock_failure,
    _to_100,
    _weighted_compose,
    _zscore_min_basket,
    _zscore_min_basket_sector_neutral,
    compute_cross_sectional,
    compute_derived_indicators,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Task 1.1 — module-scope constants (Req 2.1, 5.2, 5.7, 5.4, 6.4, 7.1,
# 7.2, 7.4, 10.6, 10.7, 11.5)
# ---------------------------------------------------------------------------


def test_fmp_provider_literal_is_pinned() -> None:
    assert _FMP == "fmp"


def test_fmp_metric_alias_map_carries_three_required_entries() -> None:
    assert _FMP_METRIC_ALIASES == {
        "ev_to_ebitda": "enterprise_to_ebitda",
        "return_on_equity": "roe",
        "free_cash_flow_yield": "fcf_yield",
    }


def test_spdr_gics_sector_map_is_eleven_entries() -> None:
    assert len(_SPDR_GICS_SECTOR) == 11
    assert _SPDR_GICS_SECTOR["XLK"] == "Information Technology"
    assert _SPDR_GICS_SECTOR["XLC"] == "Communication Services"


def test_non_us_suffix_regex_matches_hk_and_passes_us_listings() -> None:
    assert isinstance(_NON_US_SUFFIX_RE, re.Pattern)
    assert _NON_US_SUFFIX_RE.search("0700.HK") is not None
    assert _NON_US_SUFFIX_RE.search("9988.HK") is not None
    assert _NON_US_SUFFIX_RE.search("AAPL") is None
    assert _NON_US_SUFFIX_RE.search("MSFT") is None


def test_non_us_suffix_regex_does_not_drop_us_class_shares() -> None:
    """Req 4.8 (Task 10.2 fix): the non-US filter must NOT match
    US-listed class shares whose tickers carry a single-letter suffix
    (``BRK.A``, ``BRK.B``, ``BF.B``, ``GEF.B``, ``LEN.B``). These are
    real constituents of the SPDR sector ETFs (BRK.B sits in XLF, BF.B
    in XLP) and dropping them silently corrupts the sector-neutral
    z-scores the wrapper is responsible for producing."""

    class_shares = ["BRK.A", "BRK.B", "BF.B", "GEF.B", "LEN.B", "RDS.A"]
    for ticker in class_shares:
        assert _NON_US_SUFFIX_RE.search(ticker) is None, (
            f"{ticker!r} is a US-listed class share; the non-US filter "
            f"must not match it"
        )


def test_non_us_suffix_regex_covers_known_non_us_exchanges() -> None:
    """Spot-check the non-US allowlist against the exchanges FMP
    Starter+ rejects with HTTP 402 in live runs."""

    non_us = [
        "0700.HK",     # Hong Kong
        "7203.T",      # Tokyo
        "005930.KS",   # Korea KOSPI
        "2330.TW",     # Taiwan
        "RELIANCE.NS", # NSE India
        "BHP.AX",      # ASX
        "VOD.L",       # LSE
        "MC.PA",       # Euronext Paris
        "SAP.DE",      # Deutsche Börse
        "NESN.SW",     # SIX Swiss
        "ERIC-B.ST",   # Stockholm
        "SHOP.TO",     # Toronto
        "VALE3.SA",    # B3 São Paulo
        "WALMEX.MX",   # Bolsa Mexicana
        "TEVA.TA",     # Tel Aviv
    ]
    for ticker in non_us:
        assert _NON_US_SUFFIX_RE.search(ticker) is not None, (
            f"{ticker!r} carries a non-US exchange suffix; the filter "
            f"must drop it"
        )


def test_analyst_coverage_window_and_threshold() -> None:
    assert _ANALYST_COUNT_WINDOW_DAYS == 90
    assert _ANALYST_COVERAGE_THRESHOLD == 5


def test_min_basket_size_is_three() -> None:
    assert _MIN_BASKET_SIZE == 3


def test_default_top_sectors_and_top_stocks_and_price_target_limit() -> None:
    assert DEFAULT_TOP_SECTORS == 3
    assert DEFAULT_TOP_STOCKS_PER_SECTOR == 20
    assert DEFAULT_PRICE_TARGET_LIMIT == 200


def test_subscore_signal_keys_has_exactly_four_entries_and_no_revision_momentum() -> None:
    """Req 11.5 structural guard: no analyst-revision-momentum key is
    structurally reachable from the composite."""

    assert set(SUBSCORE_SIGNAL_KEYS.keys()) == {
        "momentum_z",
        "value_z",
        "quality_z",
        "forward_z",
    }
    flattened = {sig for keys in SUBSCORE_SIGNAL_KEYS.values() for sig in keys}
    assert not any("revision" in k for k in flattened)
    assert not any("recommendation" in k for k in flattened)


def test_normalization_scope_covers_six_factors() -> None:
    assert _NORMALIZATION_SCOPE == {
        "ev_ebitda_yield": "sector_neutral",
        "roe": "sector_neutral",
        "clenow_90": "basket",
        "inv_range_pct_52w": "basket",
        "ma200_distance": "basket",
        "target_upside": "basket",
    }


def test_subscore_internal_weights_fixed_shape() -> None:
    assert _SUBSCORE_INTERNAL_WEIGHTS == {
        "momentum_z": {"clenow_90": 0.5, "ma200_distance": 0.5},
        "value_z": {"ev_ebitda_yield": 0.5, "inv_range_pct_52w": 0.5},
        "quality_z": {"roe": 1.0},
        "forward_z": {"target_upside": 1.0},
    }


def test_analytical_caveats_base_has_six_entries() -> None:
    assert len(ANALYTICAL_CAVEATS_BASE) == 6
    # Req 10.6 base strings
    assert "scores_are_basket_internal_ranks_not_absolute_strength" in ANALYTICAL_CAVEATS_BASE
    assert "value_and_quality_are_sector_neutral_z_scores" in ANALYTICAL_CAVEATS_BASE
    assert "momentum_and_forward_are_basket_wide_z_scores" in ANALYTICAL_CAVEATS_BASE
    assert "etf_holdings_may_lag_spot_by_up_to_one_week" in ANALYTICAL_CAVEATS_BASE
    assert "forward_score_requires_number_of_analysts_ge_5" in ANALYTICAL_CAVEATS_BASE
    assert (
        "number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions"
        in ANALYTICAL_CAVEATS_BASE
    )
    # non_us filter caveat is conditional, so it must NOT be baked in
    assert "non_us_tickers_filtered_from_pool" not in ANALYTICAL_CAVEATS_BASE


def test_data_quality_flags_is_frozenset_of_eleven_members() -> None:
    assert isinstance(DATA_QUALITY_FLAGS, frozenset)
    assert len(DATA_QUALITY_FLAGS) == 11
    expected = {
        "last_price_from_prev_close",
        "last_price_unavailable",
        "ev_ebitda_non_positive",
        "analyst_coverage_too_thin",
        "sector_group_too_small_for_neutral_z(ev_ebitda_yield)",
        "sector_group_too_small_for_neutral_z(roe)",
        "basket_too_small_for_z(clenow_90)",
        "basket_too_small_for_z(range_pct_52w)",
        "basket_too_small_for_z(ma200_distance)",
        "basket_too_small_for_z(target_upside)",
        "stock_appears_in_multiple_top_sectors",
    }
    assert DATA_QUALITY_FLAGS == expected


def test_non_us_tickers_filtered_from_pool_absent_from_quality_flags() -> None:
    """Req 10.7 last sentence: the non-US-filter string is a caveat on
    ``data.analytical_caveats`` only, not a per-row quality flag."""

    assert "non_us_tickers_filtered_from_pool" not in DATA_QUALITY_FLAGS


# ---------------------------------------------------------------------------
# Task 1.2 — append_quality_flag validation (Req 10.7)
# ---------------------------------------------------------------------------


def test_append_quality_flag_appends_a_catalog_member() -> None:
    flags: list[str] = []
    append_quality_flag(flags, "last_price_from_prev_close")
    assert flags == ["last_price_from_prev_close"]


def test_append_quality_flag_preserves_prior_entries() -> None:
    flags = ["ev_ebitda_non_positive"]
    append_quality_flag(flags, "analyst_coverage_too_thin")
    assert flags == ["ev_ebitda_non_positive", "analyst_coverage_too_thin"]


def test_append_quality_flag_rejects_unknown_string() -> None:
    flags: list[str] = []
    with pytest.raises(ValueError):
        append_quality_flag(flags, "not_a_real_flag")
    assert flags == []


def test_append_quality_flag_forbids_non_us_tickers_filtered_from_pool() -> None:
    """Task 1.2 guard: that string is a data-namespace caveat, not a
    per-row quality flag — appending it must raise."""

    flags: list[str] = []
    with pytest.raises(ValueError):
        append_quality_flag(flags, "non_us_tickers_filtered_from_pool")


def test_module_exposes_apply_to_openbb_call_at_import_time() -> None:
    """The wrapper boilerplate runs ``apply_to_openbb()`` once at
    module import (see ``docs/steering/structure.md`` entry-point
    boilerplate). This just confirms the module loaded cleanly."""

    assert hasattr(sector_stock_screener, "_FMP")


# ---------------------------------------------------------------------------
# Task 2.1 — _parse_ticker_csv (Req 1.2)
# ---------------------------------------------------------------------------


def test_parse_ticker_csv_preserves_input_order_and_dedupes_first_seen() -> None:
    assert _parse_ticker_csv("AAPL,MSFT,NVDA,AAPL,TSLA") == [
        "AAPL",
        "MSFT",
        "NVDA",
        "TSLA",
    ]


def test_parse_ticker_csv_strips_whitespace_and_upcases() -> None:
    assert _parse_ticker_csv("  aapl , msft ,  nvda  ") == ["AAPL", "MSFT", "NVDA"]


def test_parse_ticker_csv_drops_empty_tokens() -> None:
    assert _parse_ticker_csv("AAPL,,MSFT, ,NVDA") == ["AAPL", "MSFT", "NVDA"]


def test_parse_ticker_csv_empty_input_returns_empty_list() -> None:
    assert _parse_ticker_csv("") == []
    assert _parse_ticker_csv("   ,  ,") == []


# ---------------------------------------------------------------------------
# Task 2.1 — build_config happy path (Req 1.1, 1.2, 3.2, 3.5, 4.2, 7.6)
# ---------------------------------------------------------------------------


def test_build_config_universe_spdr_resolves_to_eleven_etfs() -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    assert isinstance(cfg, ScreenerConfig)
    assert cfg.universe_key == "sector-spdr"
    assert len(cfg.etfs) == 11
    assert "XLK" in cfg.etfs
    assert cfg.top_sectors == DEFAULT_TOP_SECTORS
    assert cfg.top_stocks_per_sector == DEFAULT_TOP_STOCKS_PER_SECTOR


def test_build_config_custom_tickers_preserves_order_and_dedupes() -> None:
    cfg = build_config(["--tickers", "XLK,XLF,XLK,XLE"])
    assert cfg.universe_key == "custom"
    assert cfg.etfs == ["XLK", "XLF", "XLE"]


def test_build_config_defaults_top_sectors_and_sub_weights() -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    # Top-level sub-score weights default to 0.25 each (Req 7.6)
    assert cfg.subscore_weights == TopLevelWeights(
        momentum=0.25, value=0.25, quality=0.25, forward=0.25
    )
    # Sector weights default match sector_score.py's default weights (Req 3.5)
    assert cfg.sector_weights == SectorRankWeights(
        clenow_90=0.25,
        clenow_180=0.25,
        return_6m=0.20,
        return_3m=0.15,
        return_12m=0.10,
        risk_adj=0.05,
    )


def test_build_config_overrides_top_sectors_and_top_stocks_per_sector() -> None:
    cfg = build_config(
        [
            "--universe",
            "sector-spdr",
            "--top-sectors",
            "5",
            "--top-stocks-per-sector",
            "10",
        ]
    )
    assert cfg.top_sectors == 5
    assert cfg.top_stocks_per_sector == 10


def test_build_config_overrides_sector_weights() -> None:
    cfg = build_config(
        [
            "--universe",
            "sector-spdr",
            "--weight-clenow-90",
            "0.30",
            "--weight-clenow-180",
            "0.20",
            "--weight-return-6m",
            "0.15",
            "--weight-return-3m",
            "0.15",
            "--weight-return-12m",
            "0.15",
            "--weight-risk-adj",
            "0.05",
        ]
    )
    assert cfg.sector_weights.clenow_90 == pytest.approx(0.30)
    assert cfg.sector_weights.clenow_180 == pytest.approx(0.20)


def test_build_config_overrides_sub_score_weights() -> None:
    cfg = build_config(
        [
            "--universe",
            "sector-spdr",
            "--weight-sub-momentum",
            "0.40",
            "--weight-sub-value",
            "0.30",
            "--weight-sub-quality",
            "0.20",
            "--weight-sub-forward",
            "0.10",
        ]
    )
    assert cfg.subscore_weights == TopLevelWeights(
        momentum=0.40, value=0.30, quality=0.20, forward=0.10
    )


# ---------------------------------------------------------------------------
# Task 2.1 — build_config validation (Req 1.3, 1.4, 1.5, 1.6)
# ---------------------------------------------------------------------------


def test_build_config_rejects_jp_sector_universe_as_validation_error() -> None:
    """Req 1.5: `jp-sector` is rejected before any OpenBB call."""

    with pytest.raises(_ConfigError):
        build_config(["--universe", "jp-sector"])


def test_build_config_rejects_missing_source_at_argparse_time() -> None:
    """Req 1.4: neither --universe nor --tickers → argparse exits 2."""

    with pytest.raises(SystemExit):
        build_config([])


def test_build_config_rejects_mutex_violation_at_argparse_time() -> None:
    """Req 1.3: --universe and --tickers are mutually exclusive."""

    with pytest.raises(SystemExit):
        build_config(["--universe", "sector-spdr", "--tickers", "XLK"])


def test_build_config_rejects_empty_tickers_after_dedup() -> None:
    """Req 1.6: empty deduped tickers → validation error."""

    with pytest.raises(_ConfigError):
        build_config(["--tickers", " , , "])


def test_build_config_rejects_top_sectors_below_one() -> None:
    with pytest.raises(_ConfigError):
        build_config(["--universe", "sector-spdr", "--top-sectors", "0"])


def test_build_config_rejects_top_sectors_above_eleven() -> None:
    with pytest.raises(_ConfigError):
        build_config(["--universe", "sector-spdr", "--top-sectors", "12"])


def test_build_config_accepts_top_sectors_bounds_edges() -> None:
    cfg_low = build_config(["--universe", "sector-spdr", "--top-sectors", "1"])
    cfg_high = build_config(["--universe", "sector-spdr", "--top-sectors", "11"])
    assert cfg_low.top_sectors == 1
    assert cfg_high.top_sectors == 11


def test_build_config_rejects_top_stocks_per_sector_below_one() -> None:
    with pytest.raises(_ConfigError):
        build_config(
            ["--universe", "sector-spdr", "--top-stocks-per-sector", "0"]
        )


def test_build_config_rejects_top_stocks_per_sector_above_hundred() -> None:
    with pytest.raises(_ConfigError):
        build_config(
            ["--universe", "sector-spdr", "--top-stocks-per-sector", "101"]
        )


def test_build_config_accepts_top_stocks_per_sector_bounds_edges() -> None:
    cfg_low = build_config(
        ["--universe", "sector-spdr", "--top-stocks-per-sector", "1"]
    )
    cfg_high = build_config(
        ["--universe", "sector-spdr", "--top-stocks-per-sector", "100"]
    )
    assert cfg_low.top_stocks_per_sector == 1
    assert cfg_high.top_stocks_per_sector == 100


# ---------------------------------------------------------------------------
# Task 2.1 — no --provider flag (Req 2.1)
# ---------------------------------------------------------------------------


def test_build_config_does_not_expose_provider_flag() -> None:
    """Req 2.1: the wrapper is single-provider; no --provider flag."""

    with pytest.raises(SystemExit):
        build_config(
            ["--universe", "sector-spdr", "--provider", "yfinance"]
        )


# ---------------------------------------------------------------------------
# Task 2.2 — credential gate (Req 2.1, 2.2)
# ---------------------------------------------------------------------------


def test_check_fmp_credential_missing_returns_nonzero_and_emits_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    rc = check_fmp_credential()
    assert rc == 2
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["error_category"] == "credential"
    assert "FMP_API_KEY" in payload["error"]
    assert "Starter" in payload["error"]


def test_check_fmp_credential_empty_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FMP_API_KEY", "   ")
    rc = check_fmp_credential()
    assert rc == 2
    capsys.readouterr()  # drain


def test_check_fmp_credential_present_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FMP_API_KEY", "fmp-secret-key")
    assert check_fmp_credential() == 0


def test_main_missing_credential_returns_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Req 2.2: credential gate runs after config build, before any
    OpenBB call, and exits 2 when the key is absent."""

    monkeypatch.delenv("FMP_API_KEY", raising=False)
    rc = main(["--universe", "sector-spdr"])
    assert rc == 2
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["error_category"] == "credential"


def test_main_validation_failure_returns_exit_two_with_no_data_block(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FMP_API_KEY", "fmp-secret")
    rc = main(["--universe", "jp-sector"])
    assert rc == 2
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["error_category"] == "validation"
    assert "data" not in payload


# ---------------------------------------------------------------------------
# Task 3.1 — split-history-by-symbol + perf-record computation (Req 3.1)
# ---------------------------------------------------------------------------


def test_split_history_by_symbol_groups_rows_by_symbol_field() -> None:
    rows = [
        {"symbol": "XLK", "date": "2025-01-01", "close": 100.0},
        {"symbol": "XLF", "date": "2025-01-01", "close": 40.0},
        {"symbol": "XLK", "date": "2025-01-02", "close": 101.0},
    ]
    split = _split_history_by_symbol(rows)
    assert set(split.keys()) == {"XLK", "XLF"}
    assert len(split["XLK"]) == 2
    assert len(split["XLF"]) == 1


def test_split_history_by_symbol_drops_rows_missing_symbol_defensively() -> None:
    rows = [
        {"symbol": "XLK", "date": "2025-01-01", "close": 100.0},
        {"date": "2025-01-02", "close": 101.0},
        {"symbol": "", "date": "2025-01-02", "close": 102.0},
    ]
    split = _split_history_by_symbol(rows)
    assert list(split.keys()) == ["XLK"]
    assert len(split["XLK"]) == 1


def _synthetic_history(symbol: str, count: int, start: float = 100.0) -> list[dict[str, object]]:
    """Build ``count`` rising closes so multi-period returns are well-defined."""

    return [
        {"symbol": symbol, "date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "close": start + i * 1.0}
        for i in range(count)
    ]


def test_compute_perf_record_from_rows_emits_sector_score_shape() -> None:
    rows = _synthetic_history("XLK", 260)
    record = _compute_perf_record_from_rows("XLK", rows)
    assert record is not None
    assert record["symbol"] == "XLK"
    # Monotone rising closes — every window return must be positive.
    for key in ("one_month", "three_month", "six_month", "one_year"):
        value = record[key]
        assert value is not None and value > 0
    assert record["volatility_month"] is not None
    assert record["source"] == "fmp-computed"


def test_compute_perf_record_from_rows_empty_returns_none() -> None:
    assert _compute_perf_record_from_rows("XLK", []) is None


def test_compute_perf_record_short_history_leaves_long_windows_null() -> None:
    rows = _synthetic_history("XLF", 30)  # enough for one_month, not for one_year
    record = _compute_perf_record_from_rows("XLF", rows)
    assert record is not None
    assert record["one_month"] is not None
    assert record["six_month"] is None
    assert record["one_year"] is None


# ---------------------------------------------------------------------------
# Task 3.3 — top-N selector + envelope shape (Req 3.3, 3.4)
# ---------------------------------------------------------------------------


def _ranked_row(
    ticker: str,
    rank: int | None,
    score: float | None = 50.0,
    ok: bool = True,
) -> SectorRankRow:
    return SectorRankRow(
        ticker=ticker,
        rank=rank,
        composite_score_0_100=score,
        composite_z=(score - 50.0) / 25.0 if score is not None else None,
        ok=ok,
    )


def test_select_top_sectors_picks_first_n_in_rank_order() -> None:
    ranked = [
        _ranked_row("XLK", rank=2),
        _ranked_row("XLF", rank=1),
        _ranked_row("XLE", rank=3),
    ]
    outcome = select_top_sectors(ranked, top_n=2)
    assert [r.ticker for r in outcome.selected] == ["XLF", "XLK"]
    assert outcome.shortfall_note is None


def test_select_top_sectors_shortfall_note_when_fewer_sectors_succeed() -> None:
    ranked = [
        _ranked_row("XLK", rank=1),
        _ranked_row("XLF", rank=None, ok=False),
        _ranked_row("XLE", rank=None, ok=False),
    ]
    outcome = select_top_sectors(ranked, top_n=3)
    assert [r.ticker for r in outcome.selected] == ["XLK"]
    assert outcome.shortfall_note == "top_sectors_shortfall: requested=3, resolved=1"


def test_select_top_sectors_filters_failed_rows() -> None:
    ranked = [
        _ranked_row("XLK", rank=1, ok=True),
        _ranked_row("XLF", rank=2, ok=False),  # classified as failed — must be excluded
    ]
    outcome = select_top_sectors(ranked, top_n=2)
    assert [r.ticker for r in outcome.selected] == ["XLK"]
    assert outcome.shortfall_note == "top_sectors_shortfall: requested=2, resolved=1"


def test_sector_ranks_envelope_rows_shape() -> None:
    ranked = [
        _ranked_row("XLK", rank=1, score=80.0),
        _ranked_row("XLF", rank=None, ok=False),
    ]
    rows = sector_ranks_envelope_rows(ranked)
    assert rows == [
        {
            "ticker": "XLK",
            "rank": 1,
            "composite_score_0_100": 80.0,
            "composite_z": pytest.approx((80.0 - 50.0) / 25.0),
        },
        {
            "ticker": "XLF",
            "rank": None,
            "composite_score_0_100": 50.0,
            "composite_z": 0.0,
        },
    ]


# ---------------------------------------------------------------------------
# Task 3.1/3.2 — FMP-native fetchers with stubbed safe_call (Req 3.1)
# ---------------------------------------------------------------------------


class _HistoricalOBBject:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.results = rows
        self._rows = rows

    def to_df(self) -> object:  # pragma: no cover - safe_call uses records path
        import pandas as pd

        return pd.DataFrame(self._rows)


def test_fetch_sector_performance_fmp_batched_call_populates_perf_and_emits_no_diag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _synthetic_history("XLK", 260) + _synthetic_history("XLF", 260, start=50.0)

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {"ok": True, "records": rows}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    perf, diagnostics = sector_stock_screener.fetch_sector_performance_fmp(
        ["XLK", "XLF"]
    )
    assert set(perf.keys()) == {"XLK", "XLF"}
    assert diagnostics == []


def test_fetch_sector_performance_fmp_records_diagnostic_on_batched_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "ok": False,
            "error": "CredentialError: unauthorized",
            "error_type": "UnauthorizedError",
            "error_category": "credential",
        }

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    perf, diagnostics = sector_stock_screener.fetch_sector_performance_fmp(
        ["XLK", "XLF"]
    )
    assert perf == {}
    assert len(diagnostics) == 1
    entry = diagnostics[0]
    assert entry["provider"] == "fmp"
    assert entry["stage"] == "sector_historical_fmp"
    assert entry["error_category"] == "credential"


def _install_fake_obb_equity_price_historical(
    monkeypatch: pytest.MonkeyPatch, obj: _HistoricalOBBject
) -> None:
    """Graft ``obb.equity.price.historical`` onto the fake openbb namespace."""

    obb = sector_stock_screener.obb
    equity = SimpleNamespace(
        price=SimpleNamespace(historical=lambda **kw: obj),
        fundamental=SimpleNamespace(),
        estimates=SimpleNamespace(),
    )
    monkeypatch.setattr(obb, "equity", equity, raising=False)
    monkeypatch.setattr(
        obb,
        "technical",
        SimpleNamespace(clenow=lambda **kw: None),
        raising=False,
    )


def test_fetch_sector_clenow_fmp_runs_two_local_reductions_from_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _synthetic_history("XLK", 260)
    obj = _HistoricalOBBject(rows)
    _install_fake_obb_equity_price_historical(monkeypatch, obj)

    call_log: list[str] = []

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        # First call is the historical fetch (no ``period`` kwarg because the
        # closure captures etf/start internally); subsequent calls carry
        # ``period`` so the technical.clenow reductions are observable.
        if "period" not in kwargs:
            fn()  # populate hist_capture["obj"]
            call_log.append("historical")
            return {"ok": True, "records": rows}
        call_log.append(f"clenow_{kwargs['period']}")
        return {"ok": True, "records": [{"factor": float(kwargs["period"])}]}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)

    result = sector_stock_screener.fetch_sector_clenow_fmp("XLK")
    assert result == {"ok": True, "clenow_90": 90.0, "clenow_180": 180.0}
    assert call_log == ["historical", "clenow_90", "clenow_180"]


def test_fetch_sector_clenow_fmp_returns_failure_shape_when_historical_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        if "period" not in kwargs:
            return {
                "ok": False,
                "error": "boom",
                "error_type": "TimeoutError",
                "error_category": "transient",
            }
        raise AssertionError("technical.clenow must not be called on historical failure")

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    result = sector_stock_screener.fetch_sector_clenow_fmp("XLK")
    assert result["ok"] is False
    assert result["stage"] == "sector_clenow_historical"
    assert result["error_category"] == "transient"


# ---------------------------------------------------------------------------
# Task 4.1 — fetch_etf_holdings (Req 4.1, 4.5)
# ---------------------------------------------------------------------------


from datetime import date, datetime, timedelta


def _cfg_with_top_m(top_m: int = 20) -> ScreenerConfig:
    return ScreenerConfig(
        universe_key="sector-spdr",
        etfs=["XLK", "XLF"],
        top_sectors=3,
        top_stocks_per_sector=top_m,
        sector_weights=SectorRankWeights(
            clenow_90=0.25,
            clenow_180=0.25,
            return_6m=0.20,
            return_3m=0.15,
            return_12m=0.10,
            risk_adj=0.05,
        ),
        subscore_weights=TopLevelWeights(
            momentum=0.25, value=0.25, quality=0.25, forward=0.25
        ),
    )


def test_fetch_etf_holdings_issues_one_safe_call_per_etf_and_shapes_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        # Capture the etf symbol from the underlying closure.
        result = {
            "XLK": [
                {
                    "symbol": "AAPL",
                    "name": "Apple",
                    "weight": 0.12,
                    "shares": 100.0,
                    "value": 1000.0,
                    "updated": "2026-04-20",
                }
            ],
            "XLF": [
                {
                    "symbol": "JPM",
                    "name": "JPMorgan",
                    "weight": 0.09,
                    "shares": 50.0,
                    "value": 500.0,
                    "updated": datetime(2026, 4, 15),
                }
            ],
        }
        # Execute the closure to observe which etf it was bound to.
        # The wrapper's closure calls ``obb.etf.holdings(symbol=etf, ...)``.
        class _Obb:
            class _Etf:
                @staticmethod
                def holdings(symbol, provider):  # type: ignore[no-untyped-def]
                    calls.append(symbol)
                    return result.get(symbol, [])
            etf = _Etf()
        monkeypatch.setattr(sector_stock_screener, "obb", _Obb(), raising=False)
        rows = fn()
        return {"ok": True, "records": rows}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    rows, diagnostics = fetch_etf_holdings(["XLK", "XLF"])
    assert calls == ["XLK", "XLF"]
    assert diagnostics == []
    assert [r.symbol for r in rows] == ["AAPL", "JPM"]
    assert rows[0].etf_ticker == "XLK"
    assert rows[0].updated == date(2026, 4, 20)
    assert rows[1].updated == date(2026, 4, 15)


def test_fetch_etf_holdings_records_per_sector_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        # Probe the closure binding via a sentinel: the first call fails,
        # subsequent calls succeed — but to determine which ETF the
        # closure targets we need to execute it; substitute an obb stub.
        class _Obb:
            class _Etf:
                calls = 0

                @classmethod
                def holdings(cls, symbol, provider):  # type: ignore[no-untyped-def]
                    cls.calls += 1
                    if symbol == "XLK":
                        raise RuntimeError("Unauthorized FMP request")
                    return [
                        {
                            "symbol": "JPM",
                            "name": "JPMorgan",
                            "weight": 0.09,
                            "updated": "2026-04-10",
                        }
                    ]
            etf = _Etf()

        monkeypatch.setattr(sector_stock_screener, "obb", _Obb(), raising=False)
        try:
            rows = fn()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"CredentialError: {exc}",
                "error_type": type(exc).__name__,
                "error_category": "credential",
            }
        return {"ok": True, "records": rows}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    rows, diagnostics = fetch_etf_holdings(["XLK", "XLF"])
    assert len(rows) == 1
    assert rows[0].etf_ticker == "XLF"
    assert len(diagnostics) == 1
    assert diagnostics[0] == {
        "provider": "fmp",
        "stage": "etf_holdings",
        "symbol": "XLK",
        "error": diagnostics[0]["error"],
        "error_category": "credential",
    }


def test_fetch_etf_holdings_drops_rows_missing_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "ok": True,
            "records": [
                {"symbol": "AAPL", "weight": 0.12, "updated": "2026-04-20"},
                {"name": "No symbol", "weight": 0.01},
                {"symbol": "", "weight": 0.02},
            ],
        }

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    rows, _ = fetch_etf_holdings(["XLK"])
    assert [r.symbol for r in rows] == ["AAPL"]


# ---------------------------------------------------------------------------
# Task 4.2 — build_pool (Req 4.3, 4.4, 4.8, 10.6, 10.7)
# ---------------------------------------------------------------------------


def _h(
    etf: str,
    symbol: str,
    weight: float | None = None,
    updated: date | None = None,
) -> HoldingsRow:
    return HoldingsRow(
        etf_ticker=etf,
        symbol=symbol,
        name=None,
        weight=weight,
        shares=None,
        value=None,
        updated=updated,
    )


def test_build_pool_drops_non_us_suffix_and_records_filtered_list() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    holdings = [
        _h("XLK", "AAPL", weight=0.1),
        _h("XLK", "0700.HK", weight=0.05),
        _h("XLK", "9988.HK", weight=0.04),
    ]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert [e.symbol for e in outcome.pool] == ["AAPL"]
    filtered_syms = {x["symbol"] for x in outcome.non_us_tickers_filtered}
    assert filtered_syms == {"0700.HK", "9988.HK"}
    assert all(
        x["etf_ticker"] == "XLK" for x in outcome.non_us_tickers_filtered
    )


def test_build_pool_top_m_slice_by_weight_descending_per_etf() -> None:
    cfg = _cfg_with_top_m(top_m=2)
    holdings = [
        _h("XLK", "LOW", weight=0.01),
        _h("XLK", "MID", weight=0.05),
        _h("XLK", "HIGH", weight=0.15),
        _h("XLK", "HIGHER", weight=0.20),
    ]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert [e.symbol for e in outcome.pool] == ["HIGHER", "HIGH"]


def test_build_pool_dedups_across_etfs_and_appends_multi_sector_flag() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    holdings = [
        _h("XLK", "AAPL", weight=0.12, updated=date(2026, 4, 20)),
        _h("XLF", "AAPL", weight=0.02, updated=date(2026, 4, 15)),
    ]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert len(outcome.pool) == 1
    entry = outcome.pool[0]
    assert entry.symbol == "AAPL"
    assert len(entry.sector_origins) == 2
    assert entry.sector_origins[0]["etf_ticker"] == "XLK"
    assert entry.sector_origins[1]["etf_ticker"] == "XLF"
    assert "stock_appears_in_multiple_top_sectors" in entry.quality_flags


def test_build_pool_single_origin_no_multi_sector_flag() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    holdings = [_h("XLK", "AAPL", weight=0.12)]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert outcome.pool[0].quality_flags == []


# ---------------------------------------------------------------------------
# Task 4.3 — etf_holdings_updated_max_age_days (Req 4.6)
# ---------------------------------------------------------------------------


def test_build_pool_max_age_from_oldest_non_null_updated() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    holdings = [
        _h("XLK", "AAPL", weight=0.12, updated=date(2026, 4, 24)),
        _h("XLK", "MSFT", weight=0.10, updated=date(2026, 4, 10)),
        _h("XLF", "JPM", weight=0.05, updated=None),
    ]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert outcome.etf_holdings_updated_max_age_days == (date(2026, 5, 1) - date(2026, 4, 10)).days


def test_build_pool_max_age_null_when_every_updated_is_null() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    holdings = [
        _h("XLK", "AAPL", weight=0.12, updated=None),
        _h("XLK", "MSFT", weight=0.10, updated=None),
    ]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert outcome.etf_holdings_updated_max_age_days is None


# ---------------------------------------------------------------------------
# Task 4.4 — GICS tagger via sector_origins[0] (Req 5.7)
# ---------------------------------------------------------------------------


def test_build_pool_tags_gics_sector_via_first_seen_etf_origin() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    holdings = [_h("XLK", "AAPL", weight=0.12)]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert outcome.pool[0].gics_sector == "Information Technology"


def test_build_pool_gics_sector_null_for_unmapped_etf() -> None:
    cfg = _cfg_with_top_m(top_m=10)
    cfg = ScreenerConfig(
        universe_key="theme-ark",
        etfs=["ARKK"],
        top_sectors=1,
        top_stocks_per_sector=10,
        sector_weights=cfg.sector_weights,
        subscore_weights=cfg.subscore_weights,
    )
    holdings = [_h("ARKK", "TSLA", weight=0.10)]
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    assert outcome.pool[0].gics_sector is None


def test_build_pool_gics_sector_retains_first_seen_even_when_second_origin_maps() -> None:
    """First-seen ETF wins (Task 4.4: tag via ``sector_origins[0]``)."""

    cfg = _cfg_with_top_m(top_m=10)
    holdings = [
        _h("ARKK", "TSLA", weight=0.10),   # unmapped — first seen
        _h("XLY", "TSLA", weight=0.05),    # mapped
    ]
    cfg = ScreenerConfig(
        universe_key="custom",
        etfs=["ARKK", "XLY"],
        top_sectors=2,
        top_stocks_per_sector=10,
        sector_weights=cfg.sector_weights,
        subscore_weights=cfg.subscore_weights,
    )
    outcome = build_pool(holdings, cfg, today=date(2026, 5, 1))
    # First origin is unmapped ARKK → gics_sector stays None.
    assert outcome.pool[0].gics_sector is None


# ---------------------------------------------------------------------------
# Task 5.1 — _index_by_symbol helper (Req 5.5)
# ---------------------------------------------------------------------------


def test_index_by_symbol_single_indexes_first_seen_row_per_symbol() -> None:
    rows = [
        {"symbol": "AAPL", "last_price": 180.0},
        {"symbol": "MSFT", "last_price": 400.0},
        {"symbol": "AAPL", "last_price": 181.0},  # second sighting ignored
    ]
    out = _index_by_symbol(rows)
    assert set(out.keys()) == {"AAPL", "MSFT"}
    assert out["AAPL"]["last_price"] == 180.0


def test_index_by_symbol_multi_groups_rows_preserving_order() -> None:
    rows = [
        {"symbol": "AAPL", "target": 200},
        {"symbol": "MSFT", "target": 500},
        {"symbol": "AAPL", "target": 205},
    ]
    out = _index_by_symbol(rows, multi=True)
    assert list(out["AAPL"]) == rows[::2] + [] if False else [rows[0], rows[2]]
    assert out["MSFT"] == [rows[1]]


def test_index_by_symbol_drops_rows_with_missing_or_blank_symbol() -> None:
    rows = [
        {"symbol": "AAPL", "x": 1},
        {"symbol": "", "x": 2},
        {"x": 3},
        {"symbol": "   ", "x": 4},
    ]
    assert _index_by_symbol(rows) == {"AAPL": rows[0]}


def test_index_by_symbol_normalizes_symbol_casing() -> None:
    rows = [{"symbol": "aapl", "x": 1}]
    assert _index_by_symbol(rows) == {"AAPL": rows[0]}


def test_index_by_symbol_empty_and_none_inputs_return_empty_dict() -> None:
    assert _index_by_symbol([]) == {}
    assert _index_by_symbol(None) == {}


# ---------------------------------------------------------------------------
# Task 5.2 — logical-field extractors + batched fetchers
# (Req 5.1, 5.2, 5.4, 14.1, 14.2, 14.3)
# ---------------------------------------------------------------------------


def test_extract_quote_fields_maps_ma200_ma50_onto_logical_names() -> None:
    row = {
        "symbol": "AAPL",
        "last_price": 180.0,
        "prev_close": 179.0,
        "year_high": 200.0,
        "year_low": 150.0,
        "ma200": 170.0,
        "ma50": 175.0,
    }
    fields = _extract_quote_fields(row)
    assert fields == {
        "last_price": 180.0,
        "year_high": 200.0,
        "year_low": 150.0,
        "prev_close": 179.0,
        "ma_200d": 170.0,
        "ma_50d": 175.0,
    }


def test_extract_quote_fields_none_row_emits_all_nulls() -> None:
    fields = _extract_quote_fields(None)
    assert all(v is None for v in fields.values())
    assert set(fields.keys()) == {
        "last_price",
        "year_high",
        "year_low",
        "prev_close",
        "ma_200d",
        "ma_50d",
    }


def test_extract_metrics_fields_aliases_three_fmp_native_names() -> None:
    row = {
        "symbol": "AAPL",
        "market_cap": 3e12,
        "ev_to_ebitda": 22.0,
        "return_on_equity": 0.40,
        "free_cash_flow_yield": 0.03,
        "pe_ratio": 28.0,  # must be ignored
    }
    fields = _extract_metrics_fields(row)
    assert fields == {
        "market_cap": 3e12,
        "enterprise_to_ebitda": 22.0,
        "roe": 0.40,
        "fcf_yield": 0.03,
    }
    assert "pe_ratio" not in fields
    assert "gross_margin" not in fields


def test_extract_consensus_fields_picks_target_levels_only() -> None:
    row = {
        "symbol": "AAPL",
        "target_consensus": 210.0,
        "target_median": 215.0,
        "recommendation_mean": 2.1,  # ignored
    }
    fields = _extract_consensus_fields(row)
    assert fields == {"target_consensus": 210.0, "target_median": 215.0}


def test_fetch_quotes_batched_empty_pool_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("safe_call must not be invoked on empty pool")

    monkeypatch.setattr(sector_stock_screener, "safe_call", boom)
    assert fetch_quotes_batched([]) == {"ok": True, "by_symbol": {}}


def test_fetch_quotes_batched_indexes_response_by_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        {"symbol": "AAPL", "last_price": 180.0},
        {"symbol": "MSFT", "last_price": 400.0},
    ]

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {"ok": True, "records": records}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_quotes_batched(["AAPL", "MSFT"])
    assert out["ok"] is True
    assert set(out["by_symbol"].keys()) == {"AAPL", "MSFT"}


def test_fetch_quotes_batched_surfaces_failure_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "ok": False,
            "error": "CredentialError: bad",
            "error_type": "UnauthorizedError",
            "error_category": "credential",
        }

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_quotes_batched(["AAPL"])
    assert out["ok"] is False
    assert out["error_category"] == "credential"


def test_fetch_metrics_batched_returns_symbol_indexed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "ok": True,
            "records": [{"symbol": "AAPL", "ev_to_ebitda": 22.0}],
        }

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_metrics_batched(["AAPL"])
    assert out["by_symbol"]["AAPL"]["ev_to_ebitda"] == 22.0


def test_fetch_consensus_batched_returns_symbol_indexed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "ok": True,
            "records": [
                {"symbol": "AAPL", "target_consensus": 210.0, "target_median": 215.0}
            ],
        }

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_consensus_batched(["AAPL"])
    assert out["by_symbol"]["AAPL"]["target_consensus"] == 210.0


def test_fetch_price_target_batched_uses_multi_mode_and_default_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        # Execute the factory against a stubbed obb to capture kwargs.
        class _Est:
            @staticmethod
            def price_target(**kw):  # type: ignore[no-untyped-def]
                captured.update(kw)
                return [
                    {"symbol": "AAPL", "analyst_firm": "GS", "published_date": "2026-04-20"},
                    {"symbol": "AAPL", "analyst_firm": "MS", "published_date": "2026-04-22"},
                ]

        class _Equity:
            estimates = _Est()

        class _Obb:
            equity = _Equity()

        monkeypatch.setattr(sector_stock_screener, "obb", _Obb(), raising=False)
        rows = fn()
        return {"ok": True, "records": rows}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_price_target_batched(["AAPL"])
    assert out["ok"] is True
    assert captured["limit"] == 200
    assert captured["provider"] == "fmp"
    assert isinstance(out["by_symbol"]["AAPL"], list)
    assert len(out["by_symbol"]["AAPL"]) == 2


# ---------------------------------------------------------------------------
# Task 5.3 — per-symbol Clenow (Req 5.3, 14.4)
# ---------------------------------------------------------------------------


def test_fetch_stock_clenow_fmp_one_historical_plus_one_reduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"symbol": "AAPL", "date": "2025-12-01", "close": 180.0}]
    obj = _HistoricalOBBject(rows)
    obb = sector_stock_screener.obb
    equity = SimpleNamespace(
        price=SimpleNamespace(historical=lambda **kw: obj),
        fundamental=SimpleNamespace(),
        estimates=SimpleNamespace(),
    )
    monkeypatch.setattr(obb, "equity", equity, raising=False)
    monkeypatch.setattr(
        obb,
        "technical",
        SimpleNamespace(clenow=lambda **kw: None),
        raising=False,
    )

    call_log: list[str] = []

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        # The historical fetch captures obj; the clenow call has no kwargs
        # but different content. We run the closure and tag which leg it was
        # by inspecting whether the captured obj was populated afterwards.
        prior = getattr(fn, "__name__", "")
        result = fn()
        if result is obj:
            call_log.append("historical")
            return {"ok": True, "records": rows}
        # technical.clenow branch
        call_log.append("clenow_90")
        return {"ok": True, "records": [{"factor": 0.42}]}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_stock_clenow_fmp("AAPL")
    assert out == {"ok": True, "clenow_90": 0.42}
    assert call_log == ["historical", "clenow_90"]


def test_fetch_stock_clenow_fmp_failure_on_historical_has_stage_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "ok": False,
            "error": "boom",
            "error_type": "TimeoutError",
            "error_category": "transient",
        }

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_stock_clenow_fmp("AAPL")
    assert out["ok"] is False
    assert out["stage"] == "stock_clenow_historical"
    assert out["error_category"] == "transient"


def test_fetch_stock_clenow_fmp_null_when_clenow_reduction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"symbol": "AAPL", "date": "2025-12-01", "close": 180.0}]
    obj = _HistoricalOBBject(rows)
    obb = sector_stock_screener.obb
    equity = SimpleNamespace(
        price=SimpleNamespace(historical=lambda **kw: obj),
        fundamental=SimpleNamespace(),
        estimates=SimpleNamespace(),
    )
    monkeypatch.setattr(obb, "equity", equity, raising=False)
    monkeypatch.setattr(
        obb,
        "technical",
        SimpleNamespace(clenow=lambda **kw: (_ for _ in ()).throw(RuntimeError("nope"))),
        raising=False,
    )

    def fake_safe_call(fn, **kwargs):  # type: ignore[no-untyped-def]
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "error_category": "other",
            }
        if result is obj:
            return {"ok": True, "records": rows}
        return {"ok": True, "records": []}

    monkeypatch.setattr(sector_stock_screener, "safe_call", fake_safe_call)
    out = fetch_stock_clenow_fmp("AAPL")
    assert out == {"ok": True, "clenow_90": None}


# ---------------------------------------------------------------------------
# Task 5.4 — derive_number_of_analysts (Req 5.4, 6.4, 6.5)
# ---------------------------------------------------------------------------


def test_derive_number_of_analysts_counts_distinct_firms_in_window() -> None:
    today = date(2026, 5, 1)
    rows = [
        {"analyst_firm": "GS", "published_date": "2026-04-20"},
        {"analyst_firm": "MS", "published_date": "2026-04-25"},
        {"analyst_firm": "GS", "published_date": "2026-04-28"},  # duplicate
    ]
    assert derive_number_of_analysts(rows, today) == 2


def test_derive_number_of_analysts_excludes_empty_and_whitespace_firms() -> None:
    today = date(2026, 5, 1)
    rows = [
        {"analyst_firm": "GS", "published_date": "2026-04-20"},
        {"analyst_firm": "", "published_date": "2026-04-22"},
        {"analyst_firm": "   ", "published_date": "2026-04-24"},
        {"analyst_firm": None, "published_date": "2026-04-26"},
    ]
    assert derive_number_of_analysts(rows, today) == 1


def test_derive_number_of_analysts_filters_rows_outside_90d_window() -> None:
    today = date(2026, 5, 1)
    rows = [
        {"analyst_firm": "GS", "published_date": "2026-04-20"},   # in window
        {"analyst_firm": "MS", "published_date": "2025-01-01"},   # too old
    ]
    assert derive_number_of_analysts(rows, today) == 1


def test_derive_number_of_analysts_null_input_returns_null() -> None:
    assert derive_number_of_analysts(None, date(2026, 5, 1)) is None


def test_derive_number_of_analysts_happy_path_returns_five_for_threshold() -> None:
    today = date(2026, 5, 1)
    rows = [
        {"analyst_firm": f"Firm{i}", "published_date": "2026-04-15"} for i in range(5)
    ]
    assert derive_number_of_analysts(rows, today) == 5


# ---------------------------------------------------------------------------
# Task 5.5 — resolve_last_price (Req 5.6)
# ---------------------------------------------------------------------------


def test_resolve_last_price_rung_one_uses_quote_last_price() -> None:
    resolution = resolve_last_price({"last_price": 180.0, "prev_close": 179.0})
    assert resolution == LastPriceResolution(value=180.0, flag=None)


def test_resolve_last_price_rung_two_falls_back_to_prev_close_with_flag() -> None:
    resolution = resolve_last_price({"last_price": None, "prev_close": 179.0})
    assert resolution.value == 179.0
    assert resolution.flag == "last_price_from_prev_close"


def test_resolve_last_price_both_null_emits_unavailable_flag() -> None:
    resolution = resolve_last_price({"last_price": None, "prev_close": None})
    assert resolution.value is None
    assert resolution.flag == "last_price_unavailable"


def test_resolve_last_price_none_row_emits_unavailable_flag() -> None:
    resolution = resolve_last_price(None)
    assert resolution.value is None
    assert resolution.flag == "last_price_unavailable"


def test_resolve_last_price_flag_is_a_valid_data_quality_flag() -> None:
    """The two fallback-flag strings must be catalog members so the
    downstream ``append_quality_flag`` accepts them without raising."""

    assert "last_price_from_prev_close" in DATA_QUALITY_FLAGS
    assert "last_price_unavailable" in DATA_QUALITY_FLAGS


# ---------------------------------------------------------------------------
# Task 6.1 — compute_derived_indicators (Req 6.1, 6.2, 6.3, 6.4, 6.5)
# ---------------------------------------------------------------------------


def _di(**overrides: object) -> DerivedIndicators:
    kwargs = {
        "last_price": 180.0,
        "year_high": 200.0,
        "year_low": 150.0,
        "ma_200d": 170.0,
        "enterprise_to_ebitda": 22.0,
        "target_consensus": 210.0,
        "number_of_analysts": 10,
    }
    kwargs.update(overrides)
    return compute_derived_indicators(**kwargs)  # type: ignore[arg-type]


def test_compute_derived_indicators_happy_path_populates_all_four() -> None:
    out = _di()
    assert out.range_pct_52w == pytest.approx((180.0 - 150.0) / 50.0)
    assert out.ma200_distance == pytest.approx((180.0 - 170.0) / 170.0)
    assert out.ev_ebitda_yield == pytest.approx(1.0 / 22.0)
    assert out.target_upside == pytest.approx((210.0 - 180.0) / 180.0)
    assert out.extra_flags == []


def test_compute_derived_indicators_range_denominator_zero_emits_null() -> None:
    out = _di(year_high=150.0, year_low=150.0)
    assert out.range_pct_52w is None


def test_compute_derived_indicators_ma200_distance_null_when_ma_is_null() -> None:
    out = _di(ma_200d=None)
    assert out.ma200_distance is None


def test_compute_derived_indicators_negative_ev_ebitda_emits_flag_and_null() -> None:
    out = _di(enterprise_to_ebitda=-5.0)
    assert out.ev_ebitda_yield is None
    assert "ev_ebitda_non_positive" in out.extra_flags


def test_compute_derived_indicators_zero_ev_ebitda_emits_flag_and_null() -> None:
    out = _di(enterprise_to_ebitda=0.0)
    assert out.ev_ebitda_yield is None
    assert "ev_ebitda_non_positive" in out.extra_flags


def test_compute_derived_indicators_missing_ev_ebitda_emits_flag_and_null() -> None:
    out = _di(enterprise_to_ebitda=None)
    assert out.ev_ebitda_yield is None
    assert "ev_ebitda_non_positive" in out.extra_flags


def test_compute_derived_indicators_thin_coverage_emits_flag_and_null_upside() -> None:
    out = _di(number_of_analysts=3)
    assert out.target_upside is None
    assert "analyst_coverage_too_thin" in out.extra_flags


def test_compute_derived_indicators_threshold_edge_five_admits_upside() -> None:
    out = _di(number_of_analysts=5)
    assert out.target_upside is not None
    assert "analyst_coverage_too_thin" not in out.extra_flags


def test_compute_derived_indicators_null_coverage_emits_null_upside_no_flag() -> None:
    """No flag when ``number_of_analysts`` is None — the upstream
    ``price_target`` fetch failed and that already carries its own
    failure record."""

    out = _di(number_of_analysts=None)
    assert out.target_upside is None
    assert "analyst_coverage_too_thin" not in out.extra_flags


def test_compute_derived_indicators_missing_last_price_blocks_every_derivation() -> None:
    out = _di(last_price=None)
    assert out.range_pct_52w is None
    assert out.ma200_distance is None
    assert out.target_upside is None


# ---------------------------------------------------------------------------
# Task 6.2 — _zscore_min_basket (Req 7.8, 4.7)
# ---------------------------------------------------------------------------


def test_zscore_min_basket_three_non_null_values_produces_zeros_sum() -> None:
    z = _zscore_min_basket([1.0, 2.0, 3.0])
    assert all(v is not None for v in z)
    assert sum(z) == pytest.approx(0.0)  # type: ignore[arg-type]


def test_zscore_min_basket_two_values_collapses_to_all_null() -> None:
    assert _zscore_min_basket([1.0, 2.0]) == [None, None]


def test_zscore_min_basket_zero_dispersion_emits_zeros_for_non_null_rows() -> None:
    assert _zscore_min_basket([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_zscore_min_basket_null_passthrough_mixed_basket() -> None:
    z = _zscore_min_basket([1.0, None, 2.0, 3.0])
    assert z[1] is None
    assert z[0] is not None and z[2] is not None and z[3] is not None


# ---------------------------------------------------------------------------
# Task 6.2 — _zscore_min_basket_sector_neutral (Req 7.1, 7.7)
# ---------------------------------------------------------------------------


def test_zscore_min_basket_sector_neutral_groups_with_three_each_no_fallback() -> None:
    values = [1.0, 2.0, 3.0, 10.0, 20.0, 30.0]
    tags = ["A", "A", "A", "B", "B", "B"]
    z_sn, z_bk, fell_back = _zscore_min_basket_sector_neutral(
        values, tags, "roe"
    )
    assert all(v is not None for v in z_sn)
    assert all(v is None for v in z_bk)
    assert fell_back == [False] * 6


def test_zscore_min_basket_sector_neutral_small_group_falls_back_to_basket() -> None:
    # Group A has size 2 (below MIN=3) → fallback; group B has size 3 → sector-neutral.
    values = [1.0, 2.0, 10.0, 20.0, 30.0]
    tags = ["A", "A", "B", "B", "B"]
    z_sn, z_bk, fell_back = _zscore_min_basket_sector_neutral(
        values, tags, "roe"
    )
    assert z_sn[0] is None and z_sn[1] is None
    assert z_bk[0] is not None and z_bk[1] is not None
    assert fell_back[0] is True and fell_back[1] is True
    # Group B rows keep sector-neutral.
    assert z_sn[2] is not None and z_sn[3] is not None and z_sn[4] is not None
    assert z_bk[2] is None and z_bk[3] is None and z_bk[4] is None
    assert fell_back[2] is False


def test_zscore_min_basket_sector_neutral_null_tag_falls_back() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    tags = ["A", "A", "A", None]
    z_sn, z_bk, fell_back = _zscore_min_basket_sector_neutral(
        values, tags, "roe"
    )
    assert z_sn[3] is None
    assert z_bk[3] is not None
    assert fell_back[3] is True
    assert fell_back[:3] == [False, False, False]


def test_zscore_min_basket_sector_neutral_every_group_small_whole_basket_fallback() -> None:
    values = [1.0, 2.0, 10.0, 20.0]
    tags = ["A", "A", "B", "B"]
    z_sn, z_bk, fell_back = _zscore_min_basket_sector_neutral(
        values, tags, "roe"
    )
    assert all(v is None for v in z_sn)
    assert all(v is not None for v in z_bk)
    assert fell_back == [True, True, True, True]


def test_zscore_min_basket_sector_neutral_empty_input() -> None:
    z_sn, z_bk, fell_back = _zscore_min_basket_sector_neutral([], [], "roe")
    assert z_sn == []
    assert z_bk == []
    assert fell_back == []


# ---------------------------------------------------------------------------
# Task 6.4 — _weighted_compose + _to_100 (Req 7.3, 7.5)
# ---------------------------------------------------------------------------


def test_weighted_compose_normalizes_available_weights() -> None:
    # Only half the weight is populated — the composition divides by the
    # available weight only.
    out = _weighted_compose(
        {"a": 1.0, "b": None}, {"a": 0.5, "b": 0.5}
    )
    assert out == pytest.approx(1.0)


def test_weighted_compose_returns_null_when_every_weight_drops_out() -> None:
    assert _weighted_compose({"a": None, "b": None}, {"a": 0.5, "b": 0.5}) is None


def test_to_100_clip_and_midpoint() -> None:
    assert _to_100(0.0) == 50.0
    assert _to_100(2.0) == pytest.approx(100.0)
    assert _to_100(-2.0) == pytest.approx(0.0)
    assert _to_100(10.0) == 100.0  # clipped
    assert _to_100(-10.0) == 0.0  # clipped
    assert _to_100(None) is None


# ---------------------------------------------------------------------------
# Task 6 orchestrator — compute_cross_sectional (Req 6.x, 7.x, 10.7)
# ---------------------------------------------------------------------------


def _default_sub_weights() -> TopLevelWeights:
    return TopLevelWeights(
        momentum=0.25, value=0.25, quality=0.25, forward=0.25
    )


def _signals_row(
    **overrides: object,
) -> dict[str, float | None | list[str]]:
    row: dict[str, float | None | list[str]] = {
        "clenow_90": 0.5,
        "ma200_distance": 0.05,
        "ev_ebitda_yield": 0.06,
        "inv_range_pct_52w": 0.3,
        "roe": 0.25,
        "target_upside": 0.1,
        "_flags": [],
    }
    row.update(overrides)  # type: ignore[arg-type]
    return row


def test_compute_cross_sectional_produces_fixed_shape_z_scores_block() -> None:
    signals = {
        sym: _signals_row(clenow_90=v, ma200_distance=v * 0.1, roe=v * 0.05)
        for sym, v in zip(
            ["AAPL", "MSFT", "NVDA"], [0.10, 0.20, 0.30]
        )
    }
    gics = {"AAPL": "Information Technology", "MSFT": "Information Technology", "NVDA": "Information Technology"}
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    assert len(rows) == 3
    # Every row must carry the full fixed-shape z_scores key set.
    expected_keys = {
        "z_ev_ebitda_yield_sector_neutral",
        "z_ev_ebitda_yield_basket",
        "z_roe_sector_neutral",
        "z_roe_basket",
        "z_clenow_90_basket",
        "z_inv_range_pct_52w_basket",
        "z_ma200_distance_basket",
        "z_target_upside_basket",
        "z_momentum_z_basket",
        "z_value_z_basket",
        "z_quality_z_basket",
        "z_forward_z_basket",
        "z_composite_z_basket",
    }
    for row in rows:
        assert set(row.z_scores.keys()) == expected_keys


def test_compute_cross_sectional_sector_neutral_vs_basket_mutually_exclusive() -> None:
    # Three IT rows, one Energy (alone → group size 1 → fallback to basket).
    signals = {
        sym: _signals_row(roe=v)
        for sym, v in zip(["A", "B", "C", "D"], [0.1, 0.2, 0.3, 0.4])
    }
    gics = {
        "A": "Information Technology",
        "B": "Information Technology",
        "C": "Information Technology",
        "D": "Energy",
    }
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    by_sym = {r.symbol: r for r in rows}
    for sym in ("A", "B", "C"):
        r = by_sym[sym]
        # Sector-neutral populated, basket null for roe.
        assert r.z_scores["z_roe_sector_neutral"] is not None
        assert r.z_scores["z_roe_basket"] is None
        assert "sector_group_too_small_for_neutral_z(roe)" not in r.per_row_quality_flags
    d = by_sym["D"]
    assert d.z_scores["z_roe_sector_neutral"] is None
    assert d.z_scores["z_roe_basket"] is not None
    assert "sector_group_too_small_for_neutral_z(roe)" in d.per_row_quality_flags


def test_compute_cross_sectional_basket_collapse_flags_every_row() -> None:
    # Only one row has clenow_90 populated → full-basket collapse for clenow_90.
    signals = {
        "A": _signals_row(clenow_90=0.5),
        "B": _signals_row(clenow_90=None),
        "C": _signals_row(clenow_90=None),
    }
    gics = {s: "Information Technology" for s in signals}
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    for r in rows:
        assert "basket_too_small_for_z(clenow_90)" in r.per_row_quality_flags
        assert r.z_scores["z_clenow_90_basket"] is None


def test_compute_cross_sectional_basket_size_counts_rows_with_any_signal() -> None:
    # Two rows with signals, one row without any populated sub-score signal.
    signals = {
        "A": _signals_row(),
        "B": _signals_row(),
        "C": {
            "clenow_90": None,
            "ma200_distance": None,
            "ev_ebitda_yield": None,
            "inv_range_pct_52w": None,
            "roe": None,
            "target_upside": None,
            "_flags": [],
        },
    }
    gics = {s: "Information Technology" for s in signals}
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    assert rows[0].basket_size == 2
    assert rows[0].basket_size_sufficient is False  # 2 < MIN=3


def test_compute_cross_sectional_sector_group_size_counted_per_row() -> None:
    signals = {
        "A": _signals_row(),
        "B": _signals_row(),
        "C": _signals_row(),
        "D": _signals_row(),
    }
    gics = {
        "A": "Information Technology",
        "B": "Information Technology",
        "C": "Information Technology",
        "D": "Energy",
    }
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["A"].sector_group_size == 3
    assert by_sym["D"].sector_group_size == 1


def test_compute_cross_sectional_composite_score_and_sub_scores_in_0_100() -> None:
    signals = {
        sym: _signals_row(clenow_90=v, ma200_distance=v * 0.2)
        for sym, v in zip(["A", "B", "C"], [0.1, 0.3, 0.5])
    }
    gics = {s: "Information Technology" for s in signals}
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    for r in rows:
        for field in (
            "momentum_score_0_100",
            "value_score_0_100",
            "quality_score_0_100",
            "forward_score_0_100",
            "composite_score_0_100",
        ):
            value = getattr(r, field)
            if value is not None:
                assert 0.0 <= value <= 100.0


def test_compute_cross_sectional_preserves_seeded_flags() -> None:
    signals = {
        "A": _signals_row(_flags=["last_price_from_prev_close"]),
        "B": _signals_row(),
        "C": _signals_row(),
    }
    gics = {s: "Information Technology" for s in signals}
    rows = compute_cross_sectional(signals, gics, subscore_weights=_default_sub_weights())
    by_sym = {r.symbol: r for r in rows}
    assert "last_price_from_prev_close" in by_sym["A"].per_row_quality_flags


# ---------------------------------------------------------------------------
# Task 7 — _classify_stock_failure (Req 15.1, 15.2, 15.3)
# ---------------------------------------------------------------------------


def _axis_ok(**extra: object) -> dict[str, object]:
    return {"ok": True, **extra}


def _axis_fail(category: str, **extra: object) -> dict[str, object]:
    return {
        "ok": False,
        "error": extra.get("error", f"{category} error"),
        "error_type": extra.get("error_type", "SomeError"),
        "error_category": category,
    }


def test_classify_stock_failure_any_axis_usable_returns_none() -> None:
    fetches = {
        "quote": _axis_ok(),
        "metrics": _axis_fail("credential"),
        "historical": _axis_fail("credential"),
        "consensus": _axis_fail("credential"),
        "price_target": _axis_fail("credential"),
    }
    assert _classify_stock_failure("AAPL", fetches) is None


def test_classify_stock_failure_all_axes_failed_returns_record() -> None:
    fetches = {
        "quote": _axis_fail("other"),
        "metrics": _axis_fail("other"),
        "historical": _axis_fail("other"),
        "consensus": _axis_fail("other"),
        "price_target": _axis_fail("other"),
    }
    result = _classify_stock_failure("AAPL", fetches)
    assert result is not None
    assert set(result.keys()) == {"error", "error_type", "error_category"}
    assert result["error_category"] == "other"


def test_classify_stock_failure_all_credential_promotes_category() -> None:
    fetches = {
        "quote": _axis_fail("credential"),
        "metrics": _axis_fail("credential"),
        "historical": _axis_fail("credential"),
        "consensus": _axis_fail("credential"),
        "price_target": _axis_fail("credential"),
    }
    result = _classify_stock_failure("AAPL", fetches)
    assert result is not None
    assert result["error_category"] == "credential"


def test_classify_stock_failure_all_plan_insufficient_promotes_category() -> None:
    fetches = {
        "quote": _axis_fail("plan_insufficient"),
        "metrics": _axis_fail("plan_insufficient"),
        "historical": _axis_fail("plan_insufficient"),
        "consensus": _axis_fail("plan_insufficient"),
        "price_target": _axis_fail("plan_insufficient"),
    }
    result = _classify_stock_failure("AAPL", fetches)
    assert result is not None
    assert result["error_category"] == "plan_insufficient"


def test_classify_stock_failure_mixed_categories_carries_first_seen() -> None:
    fetches = {
        "quote": _axis_fail("transient"),
        "metrics": _axis_fail("credential"),
        "historical": _axis_fail("credential"),
        "consensus": _axis_fail("other"),
        "price_target": _axis_fail("credential"),
    }
    result = _classify_stock_failure("AAPL", fetches)
    assert result is not None
    # Mixed → NOT promoted to fatal; carries the first-seen category.
    assert result["error_category"] == "transient"


def test_classify_stock_failure_empty_fetches_returns_none() -> None:
    assert _classify_stock_failure("AAPL", {}) is None


# ---------------------------------------------------------------------------
# Task 8.1 — build_signals_block, build_interpretation, build_ok_row,
# build_failure_row, build_data_namespace (Req 10.1, 10.2, 10.3, 10.5, 9.2)
# ---------------------------------------------------------------------------


from sector_stock_screener import (  # type: ignore[import-not-found]
    build_data_namespace,
    build_failure_row,
    build_interpretation,
    build_ok_row,
    build_signals_block,
    compose_analytical_caveats,
    sort_and_rank_rows,
)


def test_build_signals_block_has_exactly_seventeen_logical_fields() -> None:
    block = build_signals_block(
        last_price=180.0,
        year_high=200.0,
        year_low=150.0,
        ma_200d=170.0,
        ma_50d=175.0,
        range_pct_52w=0.6,
        ma200_distance=0.058,
        market_cap=3e12,
        enterprise_to_ebitda=22.0,
        ev_ebitda_yield=0.045,
        roe=0.4,
        fcf_yield=0.03,
        clenow_90=0.5,
        target_consensus=210.0,
        target_median=215.0,
        target_upside=0.17,
        number_of_analysts=12,
    )
    expected_keys = {
        "last_price",
        "year_high",
        "year_low",
        "ma_200d",
        "ma_50d",
        "range_pct_52w",
        "ma200_distance",
        "market_cap",
        "enterprise_to_ebitda",
        "ev_ebitda_yield",
        "roe",
        "fcf_yield",
        "clenow_90",
        "target_consensus",
        "target_median",
        "target_upside",
        "number_of_analysts",
    }
    assert set(block.keys()) == expected_keys
    assert "pe_ratio" not in block
    assert "gross_margin" not in block
    assert "recommendation_mean" not in block


def test_build_interpretation_block_has_five_fixed_keys() -> None:
    interp = build_interpretation()
    assert interp == {
        "score_meaning": "basket_internal_rank",
        "composite_polarity": "high=better_candidate",
        "forward_looking_component_gated_on": "number_of_analysts>=5",
        "sector_neutral_factors": ["ev_ebitda_yield", "roe"],
        "basket_wide_factors": [
            "clenow_90",
            "range_pct_52w",
            "ma200_distance",
            "target_upside",
        ],
    }


def test_build_ok_row_carries_minimum_field_set_and_omits_per_row_provider() -> None:
    row = build_ok_row(
        symbol="AAPL",
        gics_sector="Information Technology",
        sector_origins=[{"etf_ticker": "XLK", "weight_in_etf": 0.12, "updated": None}],
        signals={"last_price": 180.0},
        z_scores={"z_roe_sector_neutral": 0.5, "z_roe_basket": None},
        momentum_score_0_100=70.0,
        value_score_0_100=55.0,
        quality_score_0_100=60.0,
        forward_score_0_100=None,
        composite_score_0_100=65.0,
        basket_size=10,
        sector_group_size=5,
        basket_size_sufficient=True,
        data_quality_flags=[],
    )
    required = {
        "symbol",
        "ok",
        "rank",
        "gics_sector",
        "sector_origins",
        "composite_score_0_100",
        "momentum_score_0_100",
        "value_score_0_100",
        "quality_score_0_100",
        "forward_score_0_100",
        "signals",
        "z_scores",
        "basket_size",
        "sector_group_size",
        "basket_size_sufficient",
        "data_quality_flags",
        "interpretation",
    }
    assert required <= set(row.keys())
    assert row["ok"] is True
    assert row["rank"] is None  # sort_and_rank_rows assigns later
    assert "provider" not in row
    assert "buy_signal" not in row
    assert "recommendation" not in row


def test_build_failure_row_omits_scores_and_z_scores() -> None:
    row = build_failure_row(
        symbol="AAPL",
        gics_sector="Information Technology",
        sector_origins=[{"etf_ticker": "XLK"}],
        error="boom",
        error_type="RuntimeError",
        error_category="other",
    )
    assert row["ok"] is False
    assert row["symbol"] == "AAPL"
    assert row["error"] == "boom"
    assert row["error_category"] == "other"
    assert "composite_score_0_100" not in row
    assert "z_scores" not in row
    assert "momentum_score_0_100" not in row
    assert "signals" not in row
    assert "provider" not in row


def test_compose_analytical_caveats_base_only_when_no_non_us_filter() -> None:
    caveats = compose_analytical_caveats(non_us_filter_applied=False)
    assert "scores_are_basket_internal_ranks_not_absolute_strength" in caveats
    assert (
        "number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions"
        in caveats
    )
    assert "non_us_tickers_filtered_from_pool" not in caveats
    assert len(caveats) == 6


def test_compose_analytical_caveats_adds_non_us_entry_when_filter_fired() -> None:
    caveats = compose_analytical_caveats(non_us_filter_applied=True)
    assert "non_us_tickers_filtered_from_pool" in caveats
    assert len(caveats) == 7


def _ok_row_with_score(symbol: str, score: float | None) -> dict[str, object]:
    return {
        "symbol": symbol,
        "ok": True,
        "rank": None,
        "composite_score_0_100": score,
    }


def test_sort_and_rank_rows_sorts_by_composite_desc_nulls_sink() -> None:
    rows = [
        _ok_row_with_score("A", 50.0),
        _ok_row_with_score("B", 80.0),
        _ok_row_with_score("C", None),
        _ok_row_with_score("D", 65.0),
    ]
    sorted_rows = sort_and_rank_rows(rows)
    assert [r["symbol"] for r in sorted_rows] == ["B", "D", "A", "C"]
    assert [r["rank"] for r in sorted_rows] == [1, 2, 3, 4]


def test_sort_and_rank_rows_stable_on_equal_scores() -> None:
    rows = [
        _ok_row_with_score("A", 50.0),
        _ok_row_with_score("B", 50.0),
    ]
    sorted_rows = sort_and_rank_rows(rows)
    # Stable sort: A comes before B because it appeared first.
    assert [r["symbol"] for r in sorted_rows] == ["A", "B"]
    assert [r["rank"] for r in sorted_rows] == [1, 2]


def test_build_data_namespace_minimum_shape_for_default_run() -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    data = build_data_namespace(
        config=cfg,
        sector_ranks=[],
        missing_tickers=[],
        non_us_tickers_filtered=[],
        provider_diagnostics=[],
        analytical_caveats=["x"],
        notes=[],
        etf_holdings_updated_max_age_days=3,
    )
    # Required siblings from design.md §Data Models
    for key in (
        "universe",
        "tickers",
        "weights",
        "sector_ranks",
        "top_sectors_requested",
        "top_stocks_per_sector_requested",
        "etf_holdings_updated_max_age_days",
        "missing_tickers",
        "analytical_caveats",
        "notes",
    ):
        assert key in data, f"expected {key} in data namespace"
    # Single-provider wrapper — no `provider` field under `data`.
    assert "provider" not in data
    # Optional blocks omitted when empty.
    assert "non_us_tickers_filtered" not in data
    assert "provider_diagnostics" not in data
    # Weights echoed with the three sub-groups.
    assert set(data["weights"].keys()) == {"sector", "sub_scores", "sub_scores_internal"}
    assert data["top_sectors_requested"] == cfg.top_sectors
    assert data["top_stocks_per_sector_requested"] == cfg.top_stocks_per_sector
    assert data["etf_holdings_updated_max_age_days"] == 3


def test_build_data_namespace_emits_optional_blocks_when_non_empty() -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    data = build_data_namespace(
        config=cfg,
        sector_ranks=[],
        missing_tickers=[],
        non_us_tickers_filtered=[{"symbol": "0700.HK", "etf_ticker": "XLK"}],
        provider_diagnostics=[
            {"provider": "fmp", "stage": "etf_holdings", "symbol": "XLK", "error": "boom", "error_category": "other"}
        ],
        analytical_caveats=["x"],
        notes=["top_sectors_shortfall: requested=3, resolved=1"],
        etf_holdings_updated_max_age_days=None,
    )
    assert data["non_us_tickers_filtered"] == [
        {"symbol": "0700.HK", "etf_ticker": "XLK"}
    ]
    assert len(data["provider_diagnostics"]) == 1
    assert data["etf_holdings_updated_max_age_days"] is None
    assert data["notes"] == ["top_sectors_shortfall: requested=3, resolved=1"]


# ---------------------------------------------------------------------------
# Task 8.3 — assemble_and_emit (Req 9.1, 9.2, 9.3, 4.7)
# ---------------------------------------------------------------------------


from sector_stock_screener import assemble_and_emit  # type: ignore[import-not-found]


def _mk_ok_row(symbol: str, score: float | None) -> dict[str, object]:
    return build_ok_row(
        symbol=symbol,
        gics_sector="Information Technology",
        sector_origins=[{"etf_ticker": "XLK", "weight_in_etf": 0.1, "updated": None}],
        signals=build_signals_block(
            last_price=180.0,
            year_high=200.0,
            year_low=150.0,
            ma_200d=170.0,
            ma_50d=175.0,
            range_pct_52w=0.6,
            ma200_distance=0.058,
            market_cap=3e12,
            enterprise_to_ebitda=22.0,
            ev_ebitda_yield=0.045,
            roe=0.4,
            fcf_yield=0.03,
            clenow_90=0.5,
            target_consensus=210.0,
            target_median=215.0,
            target_upside=0.17,
            number_of_analysts=12,
        ),
        z_scores={"z_roe_sector_neutral": 0.5},
        momentum_score_0_100=70.0,
        value_score_0_100=55.0,
        quality_score_0_100=60.0,
        forward_score_0_100=50.0,
        composite_score_0_100=score,
        basket_size=5,
        sector_group_size=3,
        basket_size_sufficient=True,
        data_quality_flags=[],
    )


def test_assemble_and_emit_emits_sorted_envelope_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = build_config(["--universe", "sector-spdr", "--top-sectors", "2"])
    rows = [_mk_ok_row("A", 60.0), _mk_ok_row("B", 80.0)]
    rc = assemble_and_emit(
        config=cfg,
        rows=rows,
        sector_ranks=[{"ticker": "XLK", "rank": 1, "composite_score_0_100": 80.0, "composite_z": 1.0}],
        missing_tickers=[],
        non_us_tickers_filtered=[],
        provider_diagnostics=[],
        notes=[],
        etf_holdings_updated_max_age_days=3,
        pool_size=5,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "sector_stock_screener"
    assert payload["data"]["top_sectors_requested"] == 2
    assert [r["symbol"] for r in payload["data"]["results"]] == ["B", "A"]
    assert payload["data"]["results"][0]["rank"] == 1
    # No per-row provider field; no buy_signal / recommendation anywhere.
    serialised = json.dumps(payload)
    assert "buy_signal" not in serialised
    assert "recommendation" not in serialised


def test_assemble_and_emit_adds_sparse_pool_warning_when_pool_lt_three(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    rows = [_mk_ok_row("A", 60.0), _mk_ok_row("B", 80.0)]
    assemble_and_emit(
        config=cfg,
        rows=rows,
        sector_ranks=[],
        missing_tickers=[],
        non_us_tickers_filtered=[],
        provider_diagnostics=[],
        notes=[],
        etf_holdings_updated_max_age_days=None,
        pool_size=2,  # below MIN=3
    )
    payload = json.loads(capsys.readouterr().out)
    warnings = payload.get("warnings", [])
    assert any(
        w.get("symbol") is None
        and w.get("error_category") == "validation"
        and "insufficient stock pool size" in (w.get("error") or "")
        for w in warnings
    )


def test_assemble_and_emit_appends_non_us_caveat_when_filter_fired(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    rows = [_mk_ok_row("A", 60.0)]
    assemble_and_emit(
        config=cfg,
        rows=rows,
        sector_ranks=[],
        missing_tickers=[],
        non_us_tickers_filtered=[{"symbol": "0700.HK", "etf_ticker": "XLK"}],
        provider_diagnostics=[],
        notes=[],
        etf_holdings_updated_max_age_days=None,
        pool_size=5,
    )
    payload = json.loads(capsys.readouterr().out)
    caveats = payload["data"]["analytical_caveats"]
    assert "non_us_tickers_filtered_from_pool" in caveats
    assert payload["data"]["non_us_tickers_filtered"] == [
        {"symbol": "0700.HK", "etf_ticker": "XLK"}
    ]


def test_assemble_and_emit_promotes_fatal_credential_to_exit_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = build_config(["--universe", "sector-spdr"])
    fail_row = build_failure_row(
        symbol="AAPL",
        gics_sector="Information Technology",
        sector_origins=[{"etf_ticker": "XLK"}],
        error="CredentialError: boom",
        error_type="UnauthorizedError",
        error_category="credential",
    )
    rc = assemble_and_emit(
        config=cfg,
        rows=[fail_row],
        sector_ranks=[],
        missing_tickers=["AAPL"],
        non_us_tickers_filtered=[],
        provider_diagnostics=[],
        notes=[],
        etf_holdings_updated_max_age_days=None,
        pool_size=1,
    )
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_category"] == "credential"


# ---------------------------------------------------------------------------
# Task 10.1 — run_pipeline end-to-end (Req 2.1, 3.x, 4.x, 5.x, 6.x, 7.x, 9.x)
#
# These tests stub the wrapper's own fetch layer so the scoring / ranking
# logic runs over deterministic inputs. They verify that the pipeline (a)
# actually chains every stage, (b) emits a ranked envelope, (c) applies
# the sector-neutral z-score scope to EV/EBITDA and ROE, and (d) keeps
# the basket-wide z-score scope on momentum / trend / forward factors —
# i.e., the accurate sector-analysis contract the feature exists to
# deliver.
# ---------------------------------------------------------------------------


from sector_stock_screener import run_pipeline  # type: ignore[import-not-found]


def _make_pipeline_config() -> ScreenerConfig:
    return ScreenerConfig(
        universe_key="sector-spdr",
        etfs=["XLK", "XLF", "XLE"],
        top_sectors=2,
        top_stocks_per_sector=3,
        sector_weights=SectorRankWeights(
            clenow_90=0.25,
            clenow_180=0.25,
            return_6m=0.20,
            return_3m=0.15,
            return_12m=0.10,
            risk_adj=0.05,
        ),
        subscore_weights=TopLevelWeights(
            momentum=0.25, value=0.25, quality=0.25, forward=0.25
        ),
    )


def _install_pipeline_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ranked: list[SectorRankRow],
    holdings: list[HoldingsRow],
    quote_rows: dict[str, dict[str, object]],
    metrics_rows: dict[str, dict[str, object]],
    consensus_rows: dict[str, dict[str, object]],
    price_target_rows: dict[str, list[dict[str, object]]],
    clenow_by_symbol: dict[str, float | None],
) -> None:
    monkeypatch.setattr(
        sector_stock_screener,
        "run_sector_rank",
        lambda cfg: (ranked, []),
    )
    monkeypatch.setattr(
        sector_stock_screener,
        "fetch_etf_holdings",
        lambda etfs: (
            [h for h in holdings if h.etf_ticker in etfs],
            [],
        ),
    )

    def _fake_quotes(symbols: list[str]) -> dict[str, object]:
        return {
            "ok": True,
            "by_symbol": {s: quote_rows[s] for s in symbols if s in quote_rows},
        }

    def _fake_metrics(symbols: list[str]) -> dict[str, object]:
        return {
            "ok": True,
            "by_symbol": {
                s: metrics_rows[s] for s in symbols if s in metrics_rows
            },
        }

    def _fake_consensus(symbols: list[str]) -> dict[str, object]:
        return {
            "ok": True,
            "by_symbol": {
                s: consensus_rows[s] for s in symbols if s in consensus_rows
            },
        }

    def _fake_price_target(symbols: list[str]) -> dict[str, object]:
        return {
            "ok": True,
            "by_symbol": {
                s: price_target_rows[s]
                for s in symbols
                if s in price_target_rows
            },
        }

    def _fake_stock_clenow(symbol: str) -> dict[str, object]:
        factor = clenow_by_symbol.get(symbol)
        return {"ok": True, "clenow_90": factor}

    monkeypatch.setattr(
        sector_stock_screener, "fetch_quotes_batched", _fake_quotes
    )
    monkeypatch.setattr(
        sector_stock_screener, "fetch_metrics_batched", _fake_metrics
    )
    monkeypatch.setattr(
        sector_stock_screener, "fetch_consensus_batched", _fake_consensus
    )
    monkeypatch.setattr(
        sector_stock_screener, "fetch_price_target_batched", _fake_price_target
    )
    monkeypatch.setattr(
        sector_stock_screener, "fetch_stock_clenow_fmp", _fake_stock_clenow
    )


def _pt_rows(count: int, published: str = "2026-04-20") -> list[dict[str, object]]:
    """Build ``count`` distinct-firm price-target rows inside the 90-day window."""

    return [
        {"analyst_firm": f"Firm{i}", "published_date": published}
        for i in range(count)
    ]


def test_run_pipeline_end_to_end_emits_ranked_envelope_with_correct_scopes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Drive the full pipeline with stubbed fetchers and verify the
    envelope (a) ranks pool rows by composite desc, (b) routes EV/EBITDA
    + ROE through the sector-neutral scope when the sector group has
    ≥3 members, and (c) keeps Clenow / range / MA200 / upside basket-
    wide."""

    cfg = _make_pipeline_config()

    # Two winning sectors (XLK, XLF); XLE stays ranked but below top-N.
    ranked = [
        SectorRankRow(
            ticker="XLK",
            rank=1,
            composite_score_0_100=85.0,
            composite_z=1.4,
            ok=True,
        ),
        SectorRankRow(
            ticker="XLF",
            rank=2,
            composite_score_0_100=70.0,
            composite_z=0.8,
            ok=True,
        ),
        SectorRankRow(
            ticker="XLE",
            rank=3,
            composite_score_0_100=30.0,
            composite_z=-0.8,
            ok=True,
        ),
    ]

    # Six US-listed constituents split evenly across XLK and XLF so each
    # sector's group size hits the _MIN_BASKET_SIZE=3 sector-neutral floor.
    holdings = [
        HoldingsRow(
            etf_ticker="XLK",
            symbol="AAPL",
            name="Apple",
            weight=0.12,
            shares=None,
            value=None,
            updated=date(2026, 4, 24),
        ),
        HoldingsRow(
            etf_ticker="XLK",
            symbol="MSFT",
            name="Microsoft",
            weight=0.11,
            shares=None,
            value=None,
            updated=date(2026, 4, 20),
        ),
        HoldingsRow(
            etf_ticker="XLK",
            symbol="NVDA",
            name="Nvidia",
            weight=0.10,
            shares=None,
            value=None,
            updated=date(2026, 4, 22),
        ),
        HoldingsRow(
            etf_ticker="XLF",
            symbol="JPM",
            name="JPMorgan",
            weight=0.08,
            shares=None,
            value=None,
            updated=date(2026, 4, 18),
        ),
        HoldingsRow(
            etf_ticker="XLF",
            symbol="BAC",
            name="Bank of America",
            weight=0.07,
            shares=None,
            value=None,
            updated=date(2026, 4, 15),
        ),
        HoldingsRow(
            etf_ticker="XLF",
            symbol="WFC",
            name="Wells Fargo",
            weight=0.06,
            shares=None,
            value=None,
            updated=date(2026, 4, 10),
        ),
    ]

    quote_rows: dict[str, dict[str, object]] = {
        "AAPL": {"symbol": "AAPL", "last_price": 180.0, "prev_close": 179.0, "year_high": 200.0, "year_low": 150.0, "ma200": 170.0, "ma50": 175.0},
        "MSFT": {"symbol": "MSFT", "last_price": 400.0, "prev_close": 398.0, "year_high": 440.0, "year_low": 300.0, "ma200": 380.0, "ma50": 395.0},
        "NVDA": {"symbol": "NVDA", "last_price": 900.0, "prev_close": 895.0, "year_high": 1000.0, "year_low": 400.0, "ma200": 700.0, "ma50": 850.0},
        "JPM": {"symbol": "JPM", "last_price": 180.0, "prev_close": 179.0, "year_high": 200.0, "year_low": 140.0, "ma200": 165.0, "ma50": 175.0},
        "BAC": {"symbol": "BAC", "last_price": 40.0, "prev_close": 39.5, "year_high": 48.0, "year_low": 28.0, "ma200": 36.0, "ma50": 38.0},
        "WFC": {"symbol": "WFC", "last_price": 60.0, "prev_close": 59.5, "year_high": 65.0, "year_low": 40.0, "ma200": 55.0, "ma50": 58.0},
    }

    # Value / quality factors are sector-dependent — Tech shows higher
    # EV/EBITDA (lower yield) but higher ROE than Financials, mirroring
    # real cross-sector dispersion.
    metrics_rows: dict[str, dict[str, object]] = {
        "AAPL": {"symbol": "AAPL", "market_cap": 3e12, "ev_to_ebitda": 25.0, "return_on_equity": 0.45, "free_cash_flow_yield": 0.03},
        "MSFT": {"symbol": "MSFT", "market_cap": 3e12, "ev_to_ebitda": 28.0, "return_on_equity": 0.38, "free_cash_flow_yield": 0.03},
        "NVDA": {"symbol": "NVDA", "market_cap": 2e12, "ev_to_ebitda": 35.0, "return_on_equity": 0.60, "free_cash_flow_yield": 0.02},
        "JPM": {"symbol": "JPM", "market_cap": 6e11, "ev_to_ebitda": 8.0, "return_on_equity": 0.17, "free_cash_flow_yield": 0.06},
        "BAC": {"symbol": "BAC", "market_cap": 3e11, "ev_to_ebitda": 9.0, "return_on_equity": 0.12, "free_cash_flow_yield": 0.05},
        "WFC": {"symbol": "WFC", "market_cap": 2.2e11, "ev_to_ebitda": 10.0, "return_on_equity": 0.10, "free_cash_flow_yield": 0.04},
    }

    consensus_rows: dict[str, dict[str, object]] = {
        "AAPL": {"symbol": "AAPL", "target_consensus": 210.0, "target_median": 208.0},
        "MSFT": {"symbol": "MSFT", "target_consensus": 450.0, "target_median": 445.0},
        "NVDA": {"symbol": "NVDA", "target_consensus": 1100.0, "target_median": 1050.0},
        "JPM": {"symbol": "JPM", "target_consensus": 200.0, "target_median": 198.0},
        "BAC": {"symbol": "BAC", "target_consensus": 45.0, "target_median": 44.0},
        "WFC": {"symbol": "WFC", "target_consensus": 65.0, "target_median": 63.0},
    }

    price_target_rows: dict[str, list[dict[str, object]]] = {
        sym: _pt_rows(8) for sym in quote_rows  # 8 distinct firms per stock, well over the 5-firm gate
    }

    clenow_by_symbol: dict[str, float | None] = {
        "AAPL": 0.40,
        "MSFT": 0.55,
        "NVDA": 0.70,
        "JPM": 0.20,
        "BAC": 0.10,
        "WFC": 0.05,
    }

    _install_pipeline_stubs(
        monkeypatch,
        ranked=ranked,
        holdings=holdings,
        quote_rows=quote_rows,
        metrics_rows=metrics_rows,
        consensus_rows=consensus_rows,
        price_target_rows=price_target_rows,
        clenow_by_symbol=clenow_by_symbol,
    )

    rc = run_pipeline(cfg)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "sector_stock_screener"
    data = payload["data"]

    # --- Envelope siblings echo + sector ranks survived ----------------
    assert data["top_sectors_requested"] == 2
    assert data["top_stocks_per_sector_requested"] == 3
    assert data["universe"] == "sector-spdr"
    assert data["tickers"] == ["XLK", "XLF", "XLE"]
    ranked_block = data["sector_ranks"]
    assert [r["ticker"] for r in ranked_block] == ["XLK", "XLF", "XLE"]
    assert ranked_block[0]["rank"] == 1

    # --- Pool covers exactly the selected top-2 sectors, 3 each -------
    results = data["results"]
    assert len(results) == 6
    gics_by_result = {r["symbol"]: r["gics_sector"] for r in results}
    assert gics_by_result["AAPL"] == "Information Technology"
    assert gics_by_result["JPM"] == "Financials"

    # --- Sort order: composite desc, nulls sink, 1-indexed rank -------
    scores = [r["composite_score_0_100"] for r in results]
    finite = [s for s in scores if s is not None]
    assert finite == sorted(finite, reverse=True)
    assert [r["rank"] for r in results] == [1, 2, 3, 4, 5, 6]

    # --- Req 7.1: EV/EBITDA and ROE take the sector-neutral slot -----
    # Every IT row has three peers in its GICS group; every Financials
    # row has three peers in its GICS group. Both groups satisfy
    # _MIN_BASKET_SIZE=3, so the sector-neutral slot must be populated
    # and the _basket fallback slot must be null.
    for row in results:
        z = row["z_scores"]
        assert z["z_ev_ebitda_yield_sector_neutral"] is not None, row
        assert z["z_ev_ebitda_yield_basket"] is None, row
        assert z["z_roe_sector_neutral"] is not None, row
        assert z["z_roe_basket"] is None, row
        # The scope-fallback flag must NOT be present when sector-neutral
        # path succeeded.
        flags = row["data_quality_flags"]
        assert "sector_group_too_small_for_neutral_z(ev_ebitda_yield)" not in flags
        assert "sector_group_too_small_for_neutral_z(roe)" not in flags

    # --- Req 7.2: Momentum / trend / range / forward stay basket-wide --
    for row in results:
        z = row["z_scores"]
        for key in (
            "z_clenow_90_basket",
            "z_inv_range_pct_52w_basket",
            "z_ma200_distance_basket",
            "z_target_upside_basket",
        ):
            assert z[key] is not None, f"{key} missing on {row['symbol']}"

    # --- Req 6.4: analyst-gated target_upside is populated because
    # number_of_analysts=8 ≥ 5 on every stock.
    for row in results:
        signals = row["signals"]
        assert signals["number_of_analysts"] == 8
        assert signals["target_upside"] is not None

    # --- Sector-neutral ranks reflect within-group ordering ----------
    # Inside Tech, NVDA has the highest momentum + ROE, while AAPL has
    # the cheapest EV/EBITDA (lowest EV/EBITDA ratio → highest yield).
    # The composite score must rank at least one of those ahead of the
    # Financials group, confirming the composite combines both scopes
    # rather than collapsing to a single axis.
    top_row = results[0]
    assert top_row["symbol"] in {"NVDA", "AAPL", "MSFT"}

    # --- Analytical caveats carry every base entry -------------------
    caveats = data["analytical_caveats"]
    for required in ANALYTICAL_CAVEATS_BASE:
        assert required in caveats
    # All holdings are US-listed, so the conditional caveat is absent.
    assert "non_us_tickers_filtered_from_pool" not in caveats

    # --- Req 10.4: no buy_signal / recommendation anywhere -----------
    body = json.dumps(payload)
    assert "buy_signal" not in body
    assert "recommendation" not in body

    # --- Req 4.6: holdings max-age is a non-negative integer --------
    assert isinstance(data["etf_holdings_updated_max_age_days"], int)
    assert data["etf_holdings_updated_max_age_days"] >= 0


def test_run_pipeline_small_sector_group_falls_back_to_basket_with_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When a sector group has fewer than three members, the sector-
    neutral scope must fall back to basket-wide z with an auditable
    ``sector_group_too_small_for_neutral_z(<factor>)`` flag (Req 7.7)."""

    cfg = _make_pipeline_config()
    cfg = ScreenerConfig(
        universe_key="sector-spdr",
        etfs=cfg.etfs,
        top_sectors=2,
        top_stocks_per_sector=5,
        sector_weights=cfg.sector_weights,
        subscore_weights=cfg.subscore_weights,
    )

    # XLK contributes 3 names; XLF contributes only 1 → Financials group
    # has size 1, below _MIN_BASKET_SIZE=3 → fallback + flag.
    ranked = [
        SectorRankRow(
            ticker="XLK", rank=1, composite_score_0_100=85.0, composite_z=1.4, ok=True
        ),
        SectorRankRow(
            ticker="XLF", rank=2, composite_score_0_100=70.0, composite_z=0.8, ok=True
        ),
        SectorRankRow(
            ticker="XLE", rank=3, composite_score_0_100=30.0, composite_z=-0.8, ok=True
        ),
    ]
    holdings = [
        HoldingsRow(
            etf_ticker="XLK",
            symbol=sym,
            name=None,
            weight=w,
            shares=None,
            value=None,
            updated=date(2026, 4, 20),
        )
        for sym, w in (("AAPL", 0.12), ("MSFT", 0.11), ("NVDA", 0.10))
    ] + [
        HoldingsRow(
            etf_ticker="XLF",
            symbol="JPM",
            name=None,
            weight=0.08,
            shares=None,
            value=None,
            updated=date(2026, 4, 18),
        )
    ]

    quote_rows = {
        "AAPL": {"symbol": "AAPL", "last_price": 180.0, "prev_close": 179.0, "year_high": 200.0, "year_low": 150.0, "ma200": 170.0, "ma50": 175.0},
        "MSFT": {"symbol": "MSFT", "last_price": 400.0, "prev_close": 398.0, "year_high": 440.0, "year_low": 300.0, "ma200": 380.0, "ma50": 395.0},
        "NVDA": {"symbol": "NVDA", "last_price": 900.0, "prev_close": 895.0, "year_high": 1000.0, "year_low": 400.0, "ma200": 700.0, "ma50": 850.0},
        "JPM": {"symbol": "JPM", "last_price": 180.0, "prev_close": 179.0, "year_high": 200.0, "year_low": 140.0, "ma200": 165.0, "ma50": 175.0},
    }
    metrics_rows = {
        "AAPL": {"symbol": "AAPL", "market_cap": 3e12, "ev_to_ebitda": 25.0, "return_on_equity": 0.45, "free_cash_flow_yield": 0.03},
        "MSFT": {"symbol": "MSFT", "market_cap": 3e12, "ev_to_ebitda": 28.0, "return_on_equity": 0.38, "free_cash_flow_yield": 0.03},
        "NVDA": {"symbol": "NVDA", "market_cap": 2e12, "ev_to_ebitda": 35.0, "return_on_equity": 0.60, "free_cash_flow_yield": 0.02},
        "JPM": {"symbol": "JPM", "market_cap": 6e11, "ev_to_ebitda": 8.0, "return_on_equity": 0.17, "free_cash_flow_yield": 0.06},
    }
    consensus_rows = {
        "AAPL": {"symbol": "AAPL", "target_consensus": 210.0, "target_median": 208.0},
        "MSFT": {"symbol": "MSFT", "target_consensus": 450.0, "target_median": 445.0},
        "NVDA": {"symbol": "NVDA", "target_consensus": 1100.0, "target_median": 1050.0},
        "JPM": {"symbol": "JPM", "target_consensus": 200.0, "target_median": 198.0},
    }
    price_target_rows = {sym: _pt_rows(6) for sym in quote_rows}
    clenow = {"AAPL": 0.40, "MSFT": 0.55, "NVDA": 0.70, "JPM": 0.20}

    _install_pipeline_stubs(
        monkeypatch,
        ranked=ranked,
        holdings=holdings,
        quote_rows=quote_rows,
        metrics_rows=metrics_rows,
        consensus_rows=consensus_rows,
        price_target_rows=price_target_rows,
        clenow_by_symbol=clenow,
    )

    rc = run_pipeline(cfg)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    results = payload["data"]["results"]
    jpm = next(r for r in results if r["symbol"] == "JPM")
    aapl = next(r for r in results if r["symbol"] == "AAPL")

    # JPM's Financials group has size 1 → fallback.
    assert jpm["z_scores"]["z_roe_sector_neutral"] is None
    assert jpm["z_scores"]["z_roe_basket"] is not None
    assert "sector_group_too_small_for_neutral_z(roe)" in jpm["data_quality_flags"]
    assert (
        "sector_group_too_small_for_neutral_z(ev_ebitda_yield)"
        in jpm["data_quality_flags"]
    )

    # AAPL's IT group has size 3 → sector-neutral path.
    assert aapl["z_scores"]["z_roe_sector_neutral"] is not None
    assert aapl["z_scores"]["z_roe_basket"] is None
    assert (
        "sector_group_too_small_for_neutral_z(roe)" not in aapl["data_quality_flags"]
    )


def test_run_pipeline_sparse_pool_emits_validation_warning_but_still_ranks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the deduplicated pool is smaller than the min basket size
    (3), the wrapper still emits rows but surfaces a sparse-pool
    warning (Req 4.7) and every basket-wide z is null with the
    ``basket_too_small_for_z(<factor>)`` flag populated."""

    cfg = _make_pipeline_config()
    ranked = [
        SectorRankRow(
            ticker="XLK", rank=1, composite_score_0_100=85.0, composite_z=1.4, ok=True
        ),
    ]
    holdings = [
        HoldingsRow(
            etf_ticker="XLK",
            symbol="AAPL",
            name=None,
            weight=0.12,
            shares=None,
            value=None,
            updated=date(2026, 4, 20),
        ),
        HoldingsRow(
            etf_ticker="XLK",
            symbol="MSFT",
            name=None,
            weight=0.11,
            shares=None,
            value=None,
            updated=date(2026, 4, 20),
        ),
    ]
    quote_rows = {
        "AAPL": {"symbol": "AAPL", "last_price": 180.0, "prev_close": 179.0, "year_high": 200.0, "year_low": 150.0, "ma200": 170.0, "ma50": 175.0},
        "MSFT": {"symbol": "MSFT", "last_price": 400.0, "prev_close": 398.0, "year_high": 440.0, "year_low": 300.0, "ma200": 380.0, "ma50": 395.0},
    }
    metrics_rows = {
        "AAPL": {"symbol": "AAPL", "market_cap": 3e12, "ev_to_ebitda": 25.0, "return_on_equity": 0.45, "free_cash_flow_yield": 0.03},
        "MSFT": {"symbol": "MSFT", "market_cap": 3e12, "ev_to_ebitda": 28.0, "return_on_equity": 0.38, "free_cash_flow_yield": 0.03},
    }
    consensus_rows = {
        "AAPL": {"symbol": "AAPL", "target_consensus": 210.0, "target_median": 208.0},
        "MSFT": {"symbol": "MSFT", "target_consensus": 450.0, "target_median": 445.0},
    }
    price_target_rows = {sym: _pt_rows(6) for sym in quote_rows}
    clenow = {"AAPL": 0.40, "MSFT": 0.55}

    _install_pipeline_stubs(
        monkeypatch,
        ranked=ranked,
        holdings=holdings,
        quote_rows=quote_rows,
        metrics_rows=metrics_rows,
        consensus_rows=consensus_rows,
        price_target_rows=price_target_rows,
        clenow_by_symbol=clenow,
    )

    rc = run_pipeline(cfg)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    results = payload["data"]["results"]
    assert len(results) == 2
    warnings = payload.get("warnings", [])
    assert any(
        w.get("symbol") is None
        and w.get("error_category") == "validation"
        and "insufficient stock pool size" in (w.get("error") or "")
        for w in warnings
    )
    # With only 2 rows, every basket-wide z must be null + flag set.
    for row in results:
        z = row["z_scores"]
        for basket_key in (
            "z_clenow_90_basket",
            "z_inv_range_pct_52w_basket",
            "z_ma200_distance_basket",
            "z_target_upside_basket",
        ):
            assert z[basket_key] is None, f"{basket_key} must be null on sparse basket"
        flags = row["data_quality_flags"]
        assert "basket_too_small_for_z(clenow_90)" in flags
        assert "basket_too_small_for_z(range_pct_52w)" in flags
        assert "basket_too_small_for_z(ma200_distance)" in flags
        assert "basket_too_small_for_z(target_upside)" in flags
