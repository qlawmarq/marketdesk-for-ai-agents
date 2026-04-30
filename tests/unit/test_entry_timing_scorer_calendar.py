"""Unit tests for `scripts/entry_timing_scorer.py` earnings-calendar layer.

Covers Task 2 from `docs/tasks/todo/entry-timing-scorer/tasks.md`: the
single-shot ``fetch_earnings_window`` + its pure indexing helper
``_index_earnings_rows``. The pre-collection guard in
``tests/unit/conftest.py`` makes the top-level ``apply_to_openbb()``
call a no-op and the module safe to import offline.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import entry_timing_scorer  # type: ignore[import-not-found]
from entry_timing_scorer import (  # type: ignore[import-not-found]
    EarningsIndex,
    _coerce_report_date,
    _index_earnings_rows,
    fetch_earnings_window,
)

pytestmark = pytest.mark.unit


TODAY = date(2026, 4, 30)


# ---------------------------------------------------------------------------
# _coerce_report_date (Req 3.3, research Decision 10)
# ---------------------------------------------------------------------------


def test_coerce_report_date_parses_iso_string() -> None:
    assert _coerce_report_date("2026-05-06") == date(2026, 5, 6)


def test_coerce_report_date_parses_iso_string_with_time_suffix() -> None:
    assert _coerce_report_date("2026-05-06T09:00:00") == date(2026, 5, 6)


def test_coerce_report_date_returns_date_instance_unchanged() -> None:
    d = date(2026, 5, 6)
    assert _coerce_report_date(d) == d


def test_coerce_report_date_strips_datetime_to_date() -> None:
    dt = datetime(2026, 5, 6, 14, 30)
    assert _coerce_report_date(dt) == date(2026, 5, 6)


@pytest.mark.parametrize(
    "bad",
    ["", "not-a-date", "2026/05/06", "2026-13-01"],
)
def test_coerce_report_date_returns_none_for_malformed_strings(bad: str) -> None:
    assert _coerce_report_date(bad) is None


@pytest.mark.parametrize("bad", [None, 1717977600, 1.5, object()])
def test_coerce_report_date_returns_none_for_non_date_non_string_types(bad: Any) -> None:
    assert _coerce_report_date(bad) is None


# ---------------------------------------------------------------------------
# _index_earnings_rows (Req 3.2, 3.3, research Decision 10)
# ---------------------------------------------------------------------------


def test_index_picks_earliest_report_date_on_or_after_today_per_ticker() -> None:
    rows = [
        {"symbol": "ASC", "report_date": "2026-07-15"},
        {"symbol": "ASC", "report_date": "2026-05-06"},  # earliest >= today
        {"symbol": "ASC", "report_date": "2026-06-01"},
    ]

    index = _index_earnings_rows(rows, ["ASC"], TODAY)

    assert index == {"ASC": date(2026, 5, 6)}


def test_index_filters_to_input_ticker_set_before_indexing() -> None:
    rows = [
        {"symbol": "ASC", "report_date": "2026-05-06"},
        {"symbol": "AAPL", "report_date": "2026-05-01"},  # not in input
        {"symbol": "SM", "report_date": "2026-05-06"},
    ]

    index = _index_earnings_rows(rows, ["ASC", "SM"], TODAY)

    assert index == {"ASC": date(2026, 5, 6), "SM": date(2026, 5, 6)}


def test_index_drops_report_dates_strictly_before_today() -> None:
    """Req 3.3: earliest `report_date >= TODAY` per ticker.

    A stale row from an earlier announcement must never win over a
    later future row, even if it is the only row returned.
    """

    rows = [
        {"symbol": "ASC", "report_date": "2026-04-01"},  # before today
        {"symbol": "ASC", "report_date": "2026-05-06"},
    ]

    index = _index_earnings_rows(rows, ["ASC"], TODAY)

    assert index == {"ASC": date(2026, 5, 6)}


def test_index_tickers_with_no_surviving_row_are_absent_from_index() -> None:
    """Req 3.4: tickers with no surviving row map to None at lookup time.

    ``_index_earnings_rows`` represents that as absence from the dict —
    the caller (``compute_proximity_flag`` in task 6) converts the
    absence into ``next_earnings_date: null`` at row-build time.
    """

    rows = [{"symbol": "ASC", "report_date": "2026-05-06"}]

    index = _index_earnings_rows(rows, ["ASC", "FLXS", "TLT"], TODAY)

    assert index == {"ASC": date(2026, 5, 6)}
    assert "FLXS" not in index
    assert "TLT" not in index


def test_index_skips_non_dict_rows() -> None:
    rows = [
        None,
        "not-a-dict",
        ["also", "not", "a", "dict"],
        {"symbol": "ASC", "report_date": "2026-05-06"},
    ]

    index = _index_earnings_rows(rows, ["ASC"], TODAY)

    assert index == {"ASC": date(2026, 5, 6)}


def test_index_skips_rows_missing_symbol_or_report_date() -> None:
    rows = [
        {"report_date": "2026-05-06"},  # no symbol
        {"symbol": "ASC"},  # no report_date
        {"symbol": "", "report_date": "2026-05-06"},  # empty symbol
        {"symbol": "ASC", "report_date": None},  # null report_date
        {"symbol": "ASC", "report_date": "2026-05-06"},  # happy
    ]

    index = _index_earnings_rows(rows, ["ASC"], TODAY)

    assert index == {"ASC": date(2026, 5, 6)}


def test_index_accepts_datetime_in_report_date() -> None:
    """FMP and some nasdaq rows emit datetimes; they must strip to dates."""

    rows = [
        {"symbol": "ASC", "report_date": datetime(2026, 5, 6, 9, 30)},
        {"symbol": "CMCL", "report_date": date(2026, 5, 11)},
    ]

    index = _index_earnings_rows(rows, ["ASC", "CMCL"], TODAY)

    assert index == {"ASC": date(2026, 5, 6), "CMCL": date(2026, 5, 11)}


def test_index_skips_rows_with_unparseable_report_date_string() -> None:
    rows = [
        {"symbol": "ASC", "report_date": "not-a-date"},
        {"symbol": "ASC", "report_date": "2026-05-06"},
    ]

    index = _index_earnings_rows(rows, ["ASC"], TODAY)

    assert index == {"ASC": date(2026, 5, 6)}


def test_index_returns_empty_dict_for_empty_input_rows() -> None:
    assert _index_earnings_rows([], ["ASC"], TODAY) == {}


# ---------------------------------------------------------------------------
# fetch_earnings_window (Req 3.1, 3.2, 3.5, 13.3, 13.4)
# ---------------------------------------------------------------------------


class _FakeEarnings:
    """Call-count-tracking fake for ``obb.equity.calendar.earnings``.

    Records every invocation's kwargs so tests can assert the Req 13.3
    "exactly one call" and Req 13.4 "no retry" invariants.
    """

    def __init__(self, payload: Any = None, raise_with: Exception | None = None) -> None:
        self.payload = payload if payload is not None else []
        self.raise_with = raise_with
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raise_with is not None:
            raise self.raise_with
        return self.payload


def _install_fake_obb(monkeypatch: pytest.MonkeyPatch, fake: _FakeEarnings) -> None:
    ns = SimpleNamespace(
        equity=SimpleNamespace(
            calendar=SimpleNamespace(earnings=fake),
        )
    )
    monkeypatch.setattr(entry_timing_scorer, "obb", ns)


def test_fetch_earnings_window_success_returns_index_and_no_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEarnings(
        payload=[
            {"symbol": "ASC", "report_date": "2026-05-06"},
            {"symbol": "CMCL", "report_date": "2026-05-11"},
            {"symbol": "AAPL", "report_date": "2026-05-01"},  # filtered out
        ]
    )
    _install_fake_obb(monkeypatch, fake)

    result = fetch_earnings_window(
        ["ASC", "CMCL", "FLXS"],
        calendar_provider="nasdaq",
        window_days=45,
        today=TODAY,
    )

    assert isinstance(result, EarningsIndex)
    assert result.by_symbol == {
        "ASC": date(2026, 5, 6),
        "CMCL": date(2026, 5, 11),
    }
    assert result.diagnostic is None


def test_fetch_earnings_window_issues_exactly_one_call_with_expected_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 13.3: exactly one earnings-calendar call per invocation.

    Verifies both the call-count invariant and the kwargs shape
    (``start_date`` / ``end_date`` ISO strings, ``provider`` routed from
    the calendar-provider arg).
    """

    fake = _FakeEarnings(payload=[])
    _install_fake_obb(monkeypatch, fake)

    fetch_earnings_window(
        ["ASC"],
        calendar_provider="nasdaq",
        window_days=45,
        today=TODAY,
    )

    assert len(fake.calls) == 1
    kwargs = fake.calls[0]
    assert kwargs["start_date"] == "2026-04-30"
    assert kwargs["end_date"] == "2026-06-14"  # 45 calendar days forward
    assert kwargs["provider"] == "nasdaq"


def test_fetch_earnings_window_routes_fmp_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 2.2: under ``--provider fmp`` the calendar call reaches FMP."""

    fake = _FakeEarnings(payload=[])
    _install_fake_obb(monkeypatch, fake)

    fetch_earnings_window(
        ["ASC"],
        calendar_provider="fmp",
        window_days=90,
        today=TODAY,
    )

    assert fake.calls[0]["provider"] == "fmp"
    assert fake.calls[0]["end_date"] == "2026-07-29"


def test_fetch_earnings_window_failure_returns_empty_index_with_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 3.5: on calendar failure return an empty index plus a single
    ``{provider, stage, error, error_category}`` diagnostic.

    Uses ``ConnectionError`` so ``_common.classify_exception`` maps the
    failure to ``transient`` — the per-category wiring is the point of
    this test, not the exact string content.
    """

    fake = _FakeEarnings(raise_with=ConnectionError("connection reset"))
    _install_fake_obb(monkeypatch, fake)

    result = fetch_earnings_window(
        ["ASC", "CMCL"],
        calendar_provider="nasdaq",
        window_days=45,
        today=TODAY,
    )

    assert result.by_symbol == {}
    assert result.diagnostic is not None
    assert result.diagnostic["provider"] == "nasdaq"
    assert result.diagnostic["stage"] == "earnings_calendar"
    assert result.diagnostic["error_category"] == "transient"
    assert "connection reset" in result.diagnostic["error"]


def test_fetch_earnings_window_does_not_retry_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 13.4: no in-process retry on failure.

    The nasdaq 403 poisons the Python process (see research §L4);
    a naive retry cannot recover. This invariant is universal across
    providers for provider-agnostic behavior.
    """

    fake = _FakeEarnings(raise_with=RuntimeError("HTTP 403"))
    _install_fake_obb(monkeypatch, fake)

    fetch_earnings_window(
        ["ASC"],
        calendar_provider="nasdaq",
        window_days=45,
        today=TODAY,
    )

    assert len(fake.calls) == 1


def test_fetch_earnings_window_empty_response_returns_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 3.4: no row for any ticker ⇒ empty index, no diagnostic.

    Distinguishes "provider returned zero rows" (success with nothing
    to index) from "provider failed" (diagnostic populated).
    """

    fake = _FakeEarnings(payload=[])
    _install_fake_obb(monkeypatch, fake)

    result = fetch_earnings_window(
        ["ASC", "FLXS"],
        calendar_provider="nasdaq",
        window_days=45,
        today=TODAY,
    )

    assert result.by_symbol == {}
    assert result.diagnostic is None
