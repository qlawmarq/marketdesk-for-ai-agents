"""Unit tests for `scripts/entry_timing_scorer.py` per-ticker fetch layer.

Covers Task 3 from `docs/tasks/todo/entry-timing-scorer/tasks.md`:

- Task 3.1: ``fetch_ticker_bundle`` — five safe_call sites, shared
  ``history.results`` across the three technicals, empty-history
  short-circuit, fatal-category short-circuit.
- Task 3.2: ``resolve_quote_fields`` — provider-aware quote-field
  resolver with closed ``{yfinance, fmp}`` map.
- Task 3.3: ``resolve_last_price`` fallback chain, Clenow ``factor``
  coercion, RSI suffix extraction, MACD histogram suffix extraction.

The pre-collection guard in ``tests/unit/conftest.py`` makes the
top-level ``apply_to_openbb()`` call inside the wrapper a no-op so
the module is safe to import offline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import entry_timing_scorer  # type: ignore[import-not-found]
from entry_timing_scorer import (  # type: ignore[import-not-found]
    HISTORICAL_LOOKBACK_DAYS,
    LastPriceResolution,
    QuoteFields,
    TickerBundle,
    extract_clenow_factor,
    extract_macd_histogram,
    extract_rsi_14,
    fetch_ticker_bundle,
    resolve_last_price,
    resolve_quote_fields,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# resolve_quote_fields (Task 3.2 — Req 4.1, 5.7)
# ---------------------------------------------------------------------------


_YFINANCE_QUOTE = {
    "last_price": 100.0,
    "prev_close": 99.0,
    "year_high": 120.0,
    "year_low": 80.0,
    "ma_200d": 95.0,
    "ma_50d": 98.0,
    "volume_average": 1_500_000,
    "volume_average_10d": 2_000_000,
    "volume": 3_000_000,
}


_FMP_QUOTE = {
    "last_price": 100.0,
    "prev_close": 99.0,
    "year_high": 120.0,
    "year_low": 80.0,
    "ma200": 95.0,
    "ma50": 98.0,
    # Note: FMP's quote response does not populate volume_average*
    "volume_average": None,
    "volume_average_10d": None,
    "volume": 3_000_000,
}


def test_resolve_quote_fields_yfinance_reads_verbatim_keys() -> None:
    result = resolve_quote_fields(_YFINANCE_QUOTE, "yfinance")

    assert isinstance(result, QuoteFields)
    assert result.last_price == 100.0
    assert result.prev_close == 99.0
    assert result.year_high == 120.0
    assert result.year_low == 80.0
    assert result.ma_200d == 95.0
    assert result.ma_50d == 98.0
    assert result.volume_average == 1_500_000
    assert result.volume_average_10d == 2_000_000


def test_resolve_quote_fields_fmp_remaps_ma_keys_and_nulls_volume_averages() -> None:
    result = resolve_quote_fields(_FMP_QUOTE, "fmp")

    assert result.last_price == 100.0
    assert result.ma_200d == 95.0  # sourced from `ma200`
    assert result.ma_50d == 98.0  # sourced from `ma50`
    assert result.volume_average is None
    assert result.volume_average_10d is None


def test_resolve_quote_fields_raises_for_unknown_provider() -> None:
    with pytest.raises(KeyError):
        resolve_quote_fields(_YFINANCE_QUOTE, "polygon")


def test_resolve_quote_fields_handles_missing_quote_keys_as_none() -> None:
    """Rather than raising on a partial quote dict, absent logical fields
    resolve to ``None`` so upstream bond-ETF / halted-session edge cases
    survive without special-casing each provider."""

    sparse = {"last_price": 42.0}
    result = resolve_quote_fields(sparse, "yfinance")
    assert result.last_price == 42.0
    assert result.year_high is None
    assert result.ma_200d is None


# ---------------------------------------------------------------------------
# resolve_last_price (Task 3.3 — Req 4.7, 9.10)
# ---------------------------------------------------------------------------


def _history(*closes: float) -> list[dict[str, Any]]:
    return [{"close": c} for c in closes]


def test_resolve_last_price_rung1_takes_quote_last_price_with_no_flag() -> None:
    quote = QuoteFields(
        last_price=100.0,
        prev_close=99.0,
        year_high=120.0,
        year_low=80.0,
        ma_200d=None,
        ma_50d=None,
        volume_average=None,
        volume_average_10d=None,
    )

    resolution = resolve_last_price(quote, _history(88.0, 90.0, 97.5))

    assert isinstance(resolution, LastPriceResolution)
    assert resolution.value == 100.0
    assert resolution.flag is None


def test_resolve_last_price_rung2_falls_through_to_prev_close_with_flag() -> None:
    quote = QuoteFields(
        last_price=None,
        prev_close=99.0,
        year_high=120.0,
        year_low=80.0,
        ma_200d=None,
        ma_50d=None,
        volume_average=None,
        volume_average_10d=None,
    )

    resolution = resolve_last_price(quote, _history(88.0, 90.0, 97.5))

    assert resolution.value == 99.0
    assert resolution.flag == "last_price_from_prev_close"


def test_resolve_last_price_rung3_falls_through_to_historical_close_with_flag() -> None:
    quote = QuoteFields(
        last_price=None,
        prev_close=None,
        year_high=120.0,
        year_low=80.0,
        ma_200d=None,
        ma_50d=None,
        volume_average=None,
        volume_average_10d=None,
    )

    resolution = resolve_last_price(quote, _history(88.0, 90.0, 97.5))

    assert resolution.value == 97.5
    assert resolution.flag == "last_price_from_historical_close"


def test_resolve_last_price_all_null_flags_unavailable() -> None:
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

    resolution = resolve_last_price(quote, [])

    assert resolution.value is None
    assert resolution.flag == "last_price_unavailable"


def test_resolve_last_price_skips_historical_rows_with_null_close() -> None:
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

    history = [{"close": 88.0}, {"close": None}]
    resolution = resolve_last_price(quote, history)

    # The `[-1].close` rung sees ``None`` → falls through to unavailable.
    assert resolution.value is None
    assert resolution.flag == "last_price_unavailable"


# ---------------------------------------------------------------------------
# extract_clenow_factor / extract_rsi_14 / extract_macd_histogram (Task 3.3)
# ---------------------------------------------------------------------------


def test_extract_clenow_factor_coerces_stringified_float() -> None:
    """OpenBB emits Clenow's factor as a stringified float (live-verified)."""

    row = {"factor": "0.63826", "r^2": "0.91"}
    assert extract_clenow_factor(row) == pytest.approx(0.63826)


def test_extract_clenow_factor_returns_none_for_missing_or_non_numeric() -> None:
    assert extract_clenow_factor(None) is None
    assert extract_clenow_factor({}) is None
    assert extract_clenow_factor({"factor": None}) is None
    assert extract_clenow_factor({"factor": "not-a-number"}) is None


def test_extract_rsi_14_reads_close_rsi_14_column_case_insensitively() -> None:
    rows = [
        {"date": "2026-04-29", "close": 100.0, "close_RSI_14": 55.3},
        {"date": "2026-04-30", "close": 101.0, "close_RSI_14": 58.1},
    ]
    assert extract_rsi_14(rows) == pytest.approx(58.1)


def test_extract_rsi_14_handles_lowercase_rsi_column_drift() -> None:
    rows = [{"close": 101.0, "close_rsi_14": 42.0}]
    assert extract_rsi_14(rows) == pytest.approx(42.0)


def test_extract_rsi_14_returns_none_when_no_rsi_column_is_present() -> None:
    rows = [{"date": "2026-04-30", "close": 101.0}]
    assert extract_rsi_14(rows) is None


def test_extract_rsi_14_returns_none_on_empty_records() -> None:
    assert extract_rsi_14([]) is None


def test_extract_macd_histogram_reads_macdh_avoiding_macd_and_macds() -> None:
    """Case-sensitive suffix match on "MACDh" to avoid the MACD/MACDs/MACDh
    collision (Req 4.5)."""

    rows = [
        {
            "date": "2026-04-30",
            "close": 101.0,
            "close_MACD_12_26_9": 1.5,
            "close_MACDs_12_26_9": 1.2,
            "close_MACDh_12_26_9": 0.3,
        }
    ]
    assert extract_macd_histogram(rows) == pytest.approx(0.3)


def test_extract_macd_histogram_is_case_sensitive_and_ignores_lower_macdh() -> None:
    rows = [{"close": 101.0, "close_macdh_12_26_9": 0.3}]
    # ``close_macdh_12_26_9`` does NOT contain the case-sensitive ``MACDh``.
    assert extract_macd_histogram(rows) is None


def test_extract_macd_histogram_returns_none_when_only_macd_or_macds_present() -> None:
    rows = [
        {
            "close": 101.0,
            "close_MACD_12_26_9": 1.5,
            "close_MACDs_12_26_9": 1.2,
        }
    ]
    assert extract_macd_histogram(rows) is None


def test_extract_macd_histogram_returns_none_on_empty_records() -> None:
    assert extract_macd_histogram([]) is None


# ---------------------------------------------------------------------------
# fetch_ticker_bundle (Task 3.1 — Req 4.2, 4.3, 4.4, 4.5, 4.6, 4.8, 13.3, 13.4)
# ---------------------------------------------------------------------------


class _HistObj:
    """Minimal OBBject stand-in: carries ``.results`` for technicals to consume
    and ``.to_df()`` for ``_common.to_records`` to convert into list-of-dicts."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.results = rows
        self._rows = rows

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows)


class _DFObj:
    """OBBject stand-in for quote / technical outputs: only needs ``to_df()``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows)


class _FakeCall:
    """Records every invocation's kwargs and returns a canned payload or raises."""

    def __init__(
        self,
        payload: Any = None,
        raise_with: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.raise_with = raise_with
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raise_with is not None:
            raise self.raise_with
        return self.payload


def _install_fake_obb(
    monkeypatch: pytest.MonkeyPatch,
    *,
    quote: _FakeCall,
    historical: _FakeCall,
    clenow: _FakeCall,
    rsi: _FakeCall,
    macd: _FakeCall,
) -> None:
    ns = SimpleNamespace(
        equity=SimpleNamespace(
            price=SimpleNamespace(quote=quote, historical=historical),
            calendar=SimpleNamespace(earnings=_FakeCall(payload=[])),
        ),
        technical=SimpleNamespace(clenow=clenow, rsi=rsi, macd=macd),
    )
    monkeypatch.setattr(entry_timing_scorer, "obb", ns)


def _happy_history_rows(n: int = 30) -> list[dict[str, Any]]:
    return [
        {"date": f"2026-03-{(i % 28) + 1:02d}", "close": 100.0 + i, "volume": 1_000_000 + i * 1000}
        for i in range(n)
    ]


def test_fetch_ticker_bundle_happy_path_issues_exactly_five_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 4.8 / Req 13.3: exactly one quote + one historical + three
    technicals per ticker. The three technicals consume the SAME
    ``history.results`` list — no re-fetch of ``historical``."""

    history_rows = _happy_history_rows()
    hist_obj = _HistObj(history_rows)

    quote = _FakeCall(payload=_DFObj([_YFINANCE_QUOTE]))
    historical = _FakeCall(payload=hist_obj)
    clenow = _FakeCall(
        payload=_DFObj([{"date": "2026-04-30", "factor": "0.42", "r^2": "0.9"}])
    )
    rsi = _FakeCall(
        payload=_DFObj(
            [
                {"date": "2026-04-29", "close": 100.0, "close_RSI_14": 55.0},
                {"date": "2026-04-30", "close": 101.0, "close_RSI_14": 58.0},
            ]
        )
    )
    macd = _FakeCall(
        payload=_DFObj(
            [
                {
                    "date": "2026-04-30",
                    "close": 101.0,
                    "close_MACD_12_26_9": 1.5,
                    "close_MACDs_12_26_9": 1.2,
                    "close_MACDh_12_26_9": 0.3,
                }
            ]
        )
    )
    _install_fake_obb(
        monkeypatch,
        quote=quote,
        historical=historical,
        clenow=clenow,
        rsi=rsi,
        macd=macd,
    )

    bundle = fetch_ticker_bundle("ASC", provider="yfinance")

    assert isinstance(bundle, TickerBundle)
    assert bundle.symbol == "ASC"
    assert bundle.provider == "yfinance"
    assert bundle.ok is True
    assert bundle.fatal_category is None
    assert bundle.failures == []

    assert len(quote.calls) == 1
    assert len(historical.calls) == 1
    assert len(clenow.calls) == 1
    assert len(rsi.calls) == 1
    assert len(macd.calls) == 1

    # Technicals share `history.results` — same list identity.
    assert clenow.calls[0]["data"] is hist_obj.results
    assert rsi.calls[0]["data"] is hist_obj.results
    assert macd.calls[0]["data"] is hist_obj.results

    assert bundle.quote_row is not None
    assert bundle.quote_row.get("last_price") == _YFINANCE_QUOTE["last_price"]
    assert len(bundle.history_rows) == len(history_rows)
    assert bundle.clenow_row is not None
    assert bundle.clenow_row.get("factor") == "0.42"
    assert bundle.rsi_rows and bundle.rsi_rows[-1].get("close_RSI_14") == 58.0
    assert bundle.macd_rows and bundle.macd_rows[-1].get("close_MACDh_12_26_9") == 0.3


def test_fetch_ticker_bundle_uses_configured_historical_lookback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-ticker historical call must use ``start_date = today - 210 days``
    by default so Clenow(period=126) has enough trading days plus RSI warm-up
    (Req 4.2). We only assert the call was issued with a ``start_date`` string —
    the concrete date depends on the run-time clock."""

    hist_obj = _HistObj(_happy_history_rows())
    quote = _FakeCall(payload=_DFObj([_YFINANCE_QUOTE]))
    historical = _FakeCall(payload=hist_obj)
    clenow = _FakeCall(payload=_DFObj([{"factor": "0.1"}]))
    rsi = _FakeCall(payload=_DFObj([{"close_RSI_14": 50.0}]))
    macd = _FakeCall(payload=_DFObj([{"close_MACDh_12_26_9": 0.1}]))
    _install_fake_obb(
        monkeypatch,
        quote=quote,
        historical=historical,
        clenow=clenow,
        rsi=rsi,
        macd=macd,
    )

    fetch_ticker_bundle("ASC", provider="yfinance")

    assert "start_date" in historical.calls[0]
    assert historical.calls[0]["symbol"] == "ASC"
    assert historical.calls[0]["provider"] == "yfinance"
    assert HISTORICAL_LOOKBACK_DAYS == 210


def test_fetch_ticker_bundle_short_circuits_technicals_on_empty_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec: ``Short-circuit the three technicals to a documented
    empty-history failure when ``history.results`` is empty so the per-ticker
    call count stays at most five on the known-null path``."""

    quote = _FakeCall(payload=_DFObj([_YFINANCE_QUOTE]))
    historical = _FakeCall(payload=_HistObj([]))
    clenow = _FakeCall(payload=_DFObj([{"factor": "0.1"}]))
    rsi = _FakeCall(payload=_DFObj([{"close_RSI_14": 50.0}]))
    macd = _FakeCall(payload=_DFObj([{"close_MACDh_12_26_9": 0.1}]))
    _install_fake_obb(
        monkeypatch,
        quote=quote,
        historical=historical,
        clenow=clenow,
        rsi=rsi,
        macd=macd,
    )

    bundle = fetch_ticker_bundle("ASC", provider="yfinance")

    # The three technical stubs were never invoked.
    assert clenow.calls == []
    assert rsi.calls == []
    assert macd.calls == []

    # Per-stage empty-history failures are recorded for the three skipped stages.
    stages = {f["stage"] for f in bundle.failures}
    assert {"clenow", "rsi", "macd"}.issubset(stages)
    for failure in bundle.failures:
        assert failure["provider"] == "yfinance"

    # Quote succeeded, so `ok` stays True (partial-success handling).
    assert bundle.ok is True


def test_fetch_ticker_bundle_short_circuits_after_credential_on_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec: ``Short-circuit subsequent equity calls after a fatal
    credential / plan_insufficient category to avoid burning budget
    needlessly``."""

    quote = _FakeCall(raise_with=RuntimeError("HTTP 401 Unauthorized"))
    historical = _FakeCall(payload=_HistObj(_happy_history_rows()))
    clenow = _FakeCall(payload=_DFObj([{"factor": "0.1"}]))
    rsi = _FakeCall(payload=_DFObj([{"close_RSI_14": 50.0}]))
    macd = _FakeCall(payload=_DFObj([{"close_MACDh_12_26_9": 0.1}]))
    _install_fake_obb(
        monkeypatch,
        quote=quote,
        historical=historical,
        clenow=clenow,
        rsi=rsi,
        macd=macd,
    )

    bundle = fetch_ticker_bundle("ASC", provider="yfinance")

    # Only the quote call fired; the other four are skipped.
    assert len(quote.calls) == 1
    assert historical.calls == []
    assert clenow.calls == []
    assert rsi.calls == []
    assert macd.calls == []

    assert bundle.ok is False
    assert bundle.fatal_category == "credential"
    assert any(f["stage"] == "quote" for f in bundle.failures)


def test_fetch_ticker_bundle_non_fatal_transient_on_historical_skips_technicals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``historical`` fails non-fatally, technicals can't share
    ``history.results``, so they short-circuit. Quote still succeeded
    ⇒ ``ok`` stays True so the row is preserved for cross-sectional z."""

    quote = _FakeCall(payload=_DFObj([_YFINANCE_QUOTE]))
    historical = _FakeCall(raise_with=ConnectionError("connection reset"))
    clenow = _FakeCall(payload=_DFObj([{"factor": "0.1"}]))
    rsi = _FakeCall(payload=_DFObj([{"close_RSI_14": 50.0}]))
    macd = _FakeCall(payload=_DFObj([{"close_MACDh_12_26_9": 0.1}]))
    _install_fake_obb(
        monkeypatch,
        quote=quote,
        historical=historical,
        clenow=clenow,
        rsi=rsi,
        macd=macd,
    )

    bundle = fetch_ticker_bundle("ASC", provider="yfinance")

    assert len(historical.calls) == 1
    assert clenow.calls == []
    assert rsi.calls == []
    assert macd.calls == []

    assert bundle.ok is True  # quote carried usable data
    assert bundle.fatal_category is None
    stages = {f["stage"] for f in bundle.failures}
    assert "historical" in stages


def test_fetch_ticker_bundle_all_stages_fail_non_fatally_emits_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote = _FakeCall(raise_with=ConnectionError("connection reset"))
    historical = _FakeCall(raise_with=ConnectionError("connection reset"))
    clenow = _FakeCall(payload=_DFObj([]))
    rsi = _FakeCall(payload=_DFObj([]))
    macd = _FakeCall(payload=_DFObj([]))
    _install_fake_obb(
        monkeypatch,
        quote=quote,
        historical=historical,
        clenow=clenow,
        rsi=rsi,
        macd=macd,
    )

    bundle = fetch_ticker_bundle("ASC", provider="yfinance")

    assert bundle.ok is False
    assert bundle.fatal_category is None
    # Every stage recorded a failure (historical path empty ⇒ 3 tech stubs empty).
    stages = {f["stage"] for f in bundle.failures}
    assert {"quote", "historical", "clenow", "rsi", "macd"} <= stages
