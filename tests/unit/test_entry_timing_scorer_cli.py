"""Unit tests for `scripts/entry_timing_scorer.py` CLI + input-resolution layer.

Covers tasks 1.1-1.4 from `docs/tasks/todo/entry-timing-scorer/tasks.md`:
provider routing, CSV parsing, portfolio-YAML parsing, argparse wiring, and
the second-stage bounds validator. The pre-collection guard in
`tests/unit/conftest.py` strips `_CREDENTIAL_MAP` env vars and installs a
fake `openbb` module before this module imports, so the top-level
`apply_to_openbb()` call inside the wrapper is a no-op and the module is
safe to import offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entry_timing_scorer import (  # type: ignore[import-not-found]
    DEFAULT_EARNINGS_PROXIMITY_DAYS,
    DEFAULT_EARNINGS_WINDOW_DAYS,
    MeanReversionWeights,
    ScorerConfig,
    TrendWeights,
    _ConfigError,
    _parse_ticker_csv,
    _validate_bounds,
    build_config,
    load_portfolio_file,
    resolve_providers,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# resolve_providers (Req 2.1, 2.2, 2.3)
# ---------------------------------------------------------------------------


def test_resolve_providers_yfinance_maps_to_nasdaq_calendar() -> None:
    assert resolve_providers("yfinance") == ("yfinance", "nasdaq")


def test_resolve_providers_fmp_maps_to_fmp_calendar() -> None:
    assert resolve_providers("fmp") == ("fmp", "fmp")


def test_resolve_providers_rejects_unknown_provider_at_resolution_time() -> None:
    with pytest.raises(ValueError, match="unknown --provider"):
        resolve_providers("polygon")


# ---------------------------------------------------------------------------
# _parse_ticker_csv (Req 1.1, 1.5)
# ---------------------------------------------------------------------------


def test_parse_ticker_csv_preserves_input_order_and_dedupes_first_seen() -> None:
    result = _parse_ticker_csv("FLXS,ASC,CMCL,ASC,SM")
    assert result == ["FLXS", "ASC", "CMCL", "SM"]


def test_parse_ticker_csv_strips_whitespace_and_drops_empty_tokens() -> None:
    result = _parse_ticker_csv(" TLT , , LQD,  ,ASC")
    assert result == ["TLT", "LQD", "ASC"]


def test_parse_ticker_csv_forwards_dot_t_suffix_unchanged_for_jp_equities() -> None:
    result = _parse_ticker_csv("1615.T,1617.T,AAPL")
    assert result == ["1615.T", "1617.T", "AAPL"]


def test_parse_ticker_csv_empty_string_returns_empty_list() -> None:
    assert _parse_ticker_csv("") == []
    assert _parse_ticker_csv(",, ,") == []


# ---------------------------------------------------------------------------
# load_portfolio_file (Req 1.2, 1.7, 1.9, 10.1, 10.2)
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "portfolio.yaml"
    path.write_text(text)
    return path


def test_load_portfolio_file_tags_positions_as_holding_and_watchlist_as_watchlist(
    tmp_path: Path,
) -> None:
    path = _write_yaml(
        tmp_path,
        """
positions:
  - ticker: TLT
  - ticker: LQD
watchlist:
  - ticker: ASC
  - ticker: CMCL
""",
    )

    resolution = load_portfolio_file(path)

    assert resolution.tickers == ["TLT", "LQD", "ASC", "CMCL"]
    assert resolution.contexts == {
        "TLT": "holding",
        "LQD": "holding",
        "ASC": "watchlist",
        "CMCL": "watchlist",
    }
    assert resolution.duplicate_flags == {}


def test_load_portfolio_file_duplicate_resolves_to_holding_with_flag(
    tmp_path: Path,
) -> None:
    path = _write_yaml(
        tmp_path,
        """
positions:
  - ticker: TLT
watchlist:
  - ticker: TLT
  - ticker: ASC
""",
    )

    resolution = load_portfolio_file(path)

    assert resolution.tickers == ["TLT", "ASC"]
    assert resolution.contexts["TLT"] == "holding"
    assert resolution.duplicate_flags["TLT"] == [
        "context_duplicate_positions_and_watchlist"
    ]


def test_load_portfolio_file_silently_ignores_unrelated_keys(tmp_path: Path) -> None:
    """Req 10.1 / 10.2: fields other than positions[].ticker and watchlist[].ticker
    (e.g. exit_rules, triggers, targets) are silently ignored."""

    path = _write_yaml(
        tmp_path,
        """
positions:
  - ticker: TLT
    exit_rules:
      - type: stop_loss
        level: 80.0
    triggers:
      - type: earnings_proximity
    targets: [90.0, 95.0]
watchlist:
  - ticker: ASC
    triggers: [high_of_52w_break]
unrelated_top_level: {foo: bar}
""",
    )

    resolution = load_portfolio_file(path)

    assert resolution.tickers == ["TLT", "ASC"]
    assert resolution.contexts == {"TLT": "holding", "ASC": "watchlist"}


def test_load_portfolio_file_rejects_non_mapping_document(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_portfolio_file(path)


def test_load_portfolio_file_skips_malformed_entries(tmp_path: Path) -> None:
    """Entries missing `ticker` or with non-string `ticker` values are skipped
    defensively rather than raising — the envelope stays contract-compliant
    even when a human edits the YAML."""

    path = _write_yaml(
        tmp_path,
        """
positions:
  - ticker: TLT
  - name: "no ticker field"
  - ticker: 123
  - ticker: ""
watchlist: []
""",
    )

    resolution = load_portfolio_file(path)

    assert resolution.tickers == ["TLT"]


# ---------------------------------------------------------------------------
# _validate_bounds (Req 3.7, 7.6)
# ---------------------------------------------------------------------------


class _NamespaceLike:
    def __init__(self, earnings_window_days: int, earnings_proximity_days: int) -> None:
        self.earnings_window_days = earnings_window_days
        self.earnings_proximity_days = earnings_proximity_days


def test_validate_bounds_accepts_defaults() -> None:
    ns = _NamespaceLike(
        earnings_window_days=DEFAULT_EARNINGS_WINDOW_DAYS,
        earnings_proximity_days=DEFAULT_EARNINGS_PROXIMITY_DAYS,
    )
    _validate_bounds(ns)  # type: ignore[arg-type]  # structural duck-type is enough


@pytest.mark.parametrize("bad_value", [0, -1, 91, 1000])
def test_validate_bounds_rejects_earnings_window_outside_inclusive_range(
    bad_value: int,
) -> None:
    ns = _NamespaceLike(
        earnings_window_days=bad_value,
        earnings_proximity_days=5,
    )
    with pytest.raises(_ConfigError, match="--earnings-window-days"):
        _validate_bounds(ns)  # type: ignore[arg-type]


def test_validate_bounds_rejects_negative_proximity_days() -> None:
    ns = _NamespaceLike(earnings_window_days=45, earnings_proximity_days=-1)
    with pytest.raises(_ConfigError, match="--earnings-proximity-days"):
        _validate_bounds(ns)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_config (Req 1.3, 1.4, 1.6, 1.8, 2.3, 3.7, 5.4, 6.3, 6.5, 7.2, 7.6)
# ---------------------------------------------------------------------------


def test_build_config_tickers_default_context_is_unknown() -> None:
    config = build_config(["--tickers", "ASC,CMCL"])

    assert isinstance(config, ScorerConfig)
    assert config.tickers == ["ASC", "CMCL"]
    assert config.contexts == {"ASC": "unknown", "CMCL": "unknown"}
    assert config.provider == "yfinance"
    assert config.calendar_provider == "nasdaq"
    assert config.earnings_window_days == DEFAULT_EARNINGS_WINDOW_DAYS
    assert config.earnings_proximity_days == DEFAULT_EARNINGS_PROXIMITY_DAYS
    assert config.volume_z_estimator == "robust"
    assert config.blend_profile == "none"
    assert isinstance(config.trend_weights, TrendWeights)
    assert config.trend_weights.clenow == pytest.approx(0.50)
    assert config.trend_weights.macd == pytest.approx(0.25)
    assert config.trend_weights.volume == pytest.approx(0.25)
    assert isinstance(config.mean_reversion_weights, MeanReversionWeights)
    assert config.mean_reversion_weights.range == pytest.approx(0.60)
    assert config.mean_reversion_weights.rsi == pytest.approx(0.40)


def test_build_config_tickers_with_context_applies_uniformly() -> None:
    config = build_config(
        ["--tickers", "ASC,CMCL,SM", "--context", "watchlist"]
    )

    assert config.contexts == {
        "ASC": "watchlist",
        "CMCL": "watchlist",
        "SM": "watchlist",
    }


def test_build_config_fmp_provider_maps_calendar_to_fmp() -> None:
    config = build_config(["--tickers", "AAPL", "--provider", "fmp"])
    assert config.provider == "fmp"
    assert config.calendar_provider == "fmp"


def test_build_config_rejects_mutual_exclusion_of_tickers_and_portfolio_file(
    tmp_path: Path,
) -> None:
    """Req 1.3: argparse's mutually_exclusive_group makes this a SystemExit
    (code 2) — before any OpenBB call is issued.
    """

    path = _write_yaml(
        tmp_path,
        "positions:\n  - ticker: TLT\n",
    )
    with pytest.raises(SystemExit) as excinfo:
        build_config(["--tickers", "AAPL", "--portfolio-file", str(path)])
    assert excinfo.value.code == 2


def test_build_config_rejects_missing_source() -> None:
    """Req 1.4: neither --tickers nor --portfolio-file → argparse-side
    validation exit."""

    with pytest.raises(SystemExit) as excinfo:
        build_config([])
    assert excinfo.value.code == 2


def test_build_config_rejects_context_under_portfolio_file(tmp_path: Path) -> None:
    """Context comes from the YAML structure under --portfolio-file; passing
    --context alongside it is rejected as a validation error."""

    path = _write_yaml(tmp_path, "positions:\n  - ticker: TLT\n")
    with pytest.raises(_ConfigError, match="--context"):
        build_config(
            ["--portfolio-file", str(path), "--context", "holding"]
        )


def test_build_config_rejects_empty_tickers_csv() -> None:
    with pytest.raises(_ConfigError, match="no tickers resolved"):
        build_config(["--tickers", ",, ,"])


def test_build_config_rejects_missing_portfolio_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(_ConfigError, match="portfolio-file path does not exist"):
        build_config(["--portfolio-file", str(missing)])


def test_build_config_rejects_window_days_out_of_range() -> None:
    with pytest.raises(_ConfigError, match="--earnings-window-days"):
        build_config(["--tickers", "AAPL", "--earnings-window-days", "91"])


def test_build_config_rejects_negative_proximity_days() -> None:
    with pytest.raises(_ConfigError, match="--earnings-proximity-days"):
        build_config(["--tickers", "AAPL", "--earnings-proximity-days", "-1"])


def test_build_config_argparse_rejects_unknown_provider() -> None:
    """Req 2.3: closed {yfinance, fmp} choice set; argparse rejects strangers
    with SystemExit(2)."""

    with pytest.raises(SystemExit) as excinfo:
        build_config(["--tickers", "AAPL", "--provider", "polygon"])
    assert excinfo.value.code == 2


def test_build_config_portfolio_file_applies_duplicate_flag(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
positions:
  - ticker: TLT
watchlist:
  - ticker: TLT
  - ticker: ASC
""",
    )

    config = build_config(["--portfolio-file", str(path)])

    assert config.tickers == ["TLT", "ASC"]
    assert config.contexts["TLT"] == "holding"
    assert config.context_duplicate_flags["TLT"] == [
        "context_duplicate_positions_and_watchlist"
    ]


def test_build_config_weight_flags_are_thread_through() -> None:
    config = build_config(
        [
            "--tickers",
            "AAPL",
            "--weight-trend-clenow",
            "0.40",
            "--weight-trend-macd",
            "0.30",
            "--weight-trend-volume",
            "0.30",
            "--weight-meanrev-range",
            "0.55",
            "--weight-meanrev-rsi",
            "0.45",
            "--volume-z-estimator",
            "classical",
            "--blend-profile",
            "balanced",
        ]
    )

    assert config.trend_weights == TrendWeights(
        clenow=0.40, macd=0.30, volume=0.30
    )
    assert config.mean_reversion_weights == MeanReversionWeights(
        range=0.55, rsi=0.45
    )
    assert config.volume_z_estimator == "classical"
    assert config.blend_profile == "balanced"
