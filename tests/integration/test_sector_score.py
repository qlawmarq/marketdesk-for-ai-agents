"""Integration coverage for `scripts/sector_score.py`.

Parametrizes across sector-spdr, theme-ark, global-factor, jp-sector, and a
custom ticker list. Asserts the shared envelope, happy-path success, and
composite integrity: the 0-100 composite is finite and within bounds, each
declared component signal is finite, and the composite is derived only from
the declared components (no fabricated score when inputs are absent).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from tests.integration._sanity import assert_finite_in_range
from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_COMPONENT_KEYS = (
    "return_3m",
    "return_6m",
    "return_12m",
    "clenow_90",
    "clenow_180",
    "risk_adj_1m",
)


# (id, argv extension, expected universe name)
UNIVERSES: list[tuple[str, list[str], str]] = [
    ("sector-spdr", ["--universe", "sector-spdr"], "sector-spdr"),
    ("theme-ark", ["--universe", "theme-ark"], "theme-ark"),
    ("global-factor", ["--universe", "global-factor"], "global-factor"),
    ("jp-sector", ["--universe", "jp-sector"], "jp-sector"),
    ("custom", ["--tickers", "AAPL,MSFT,NVDA"], "custom"),
]


def _assert_envelope(payload: Any, *, universe: str) -> dict[str, Any]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "sector_score", payload
    assert "ranked" not in payload, (
        f"envelope must not carry a top-level `ranked` key; use data.results; payload={payload!r}"
    )
    assert "missing_tickers" not in payload, (
        f"envelope must not carry a top-level `missing_tickers`; "
        f"missing_tickers belongs under data. payload={payload!r}"
    )
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    assert data.get("universe") == universe, data
    assert "ranked" not in data, (
        f"data must not carry a legacy `ranked` key; use data.results; data={data!r}"
    )
    assert "missing_tickers" in data, (
        f"data must carry `missing_tickers` under data; data keys={list(data)}"
    )
    results = data.get("results")
    assert isinstance(results, list) and results, f"results must be non-empty; got {results!r}"
    return data


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return False
    return isinstance(value, (int, float))


_ALLOWED_ERROR_CATEGORIES = {
    "credential",
    "plan_insufficient",
    "transient",
    "validation",
    "other",
}
_ALLOWED_PROVIDER_STAGES = {
    "etf_price_performance_finviz",
    "equity_price_performance_finviz",
    "yfinance_history",
    "clenow_90",
    "clenow_180",
}


def _assert_row_warning_entries_well_formed(warnings: Any) -> None:
    """Enforce the sector_score envelope.warnings schema (per-row only).

    Post-Req 3 split: `warnings[]` carries only per-row failures produced
    by `_decide_exit_and_warnings`, shaped `{symbol, error, error_type,
    error_category}`. Provider-stage entries belong under
    `data.provider_diagnostics` and are validated separately.
    """

    if warnings is None:
        return
    assert isinstance(warnings, list), (
        f"warnings must be a list when present; got {type(warnings).__name__}"
    )
    assert warnings, "warnings list must be non-empty when present"
    for index, entry in enumerate(warnings):
        assert isinstance(entry, dict), (
            f"warnings[{index}] must be a dict; got {type(entry).__name__}"
        )
        assert "error" in entry, f"warnings[{index}] missing `error`: {entry!r}"
        assert "error_category" in entry, (
            f"warnings[{index}] missing `error_category`: {entry!r}"
        )
        assert "provider" not in entry and "stage" not in entry, (
            f"warnings[{index}] must not carry provider/stage keys — "
            f"those belong under data.provider_diagnostics; got {entry!r}"
        )
        category = entry.get("error_category")
        assert category is None or category in _ALLOWED_ERROR_CATEGORIES, (
            f"warnings[{index}].error_category must be one of "
            f"{sorted(_ALLOWED_ERROR_CATEGORIES)!r} or None; got {category!r}"
        )


def _assert_provider_diagnostics_well_formed(diagnostics: Any) -> None:
    """Enforce the sector_score data.provider_diagnostics schema.

    Provider-stage failures are shaped `{provider, stage, error,
    error_category[, symbol]}`. The `symbol` key is optional (set for
    per-ticker stages like `yfinance_history` / `clenow_{90,180}`, absent
    for universe-wide Finviz calls).
    """

    if diagnostics is None:
        return
    assert isinstance(diagnostics, list), (
        f"provider_diagnostics must be a list when present; got "
        f"{type(diagnostics).__name__}"
    )
    assert diagnostics, "provider_diagnostics list must be non-empty when present"
    for index, entry in enumerate(diagnostics):
        assert isinstance(entry, dict), (
            f"provider_diagnostics[{index}] must be a dict; got "
            f"{type(entry).__name__}"
        )
        for key in ("provider", "stage", "error", "error_category"):
            assert key in entry, (
                f"provider_diagnostics[{index}] missing `{key}`: {entry!r}"
            )
        stage = entry["stage"]
        assert stage in _ALLOWED_PROVIDER_STAGES, (
            f"provider_diagnostics[{index}].stage must be one of "
            f"{sorted(_ALLOWED_PROVIDER_STAGES)!r}; got {stage!r}"
        )
        category = entry.get("error_category")
        assert category is None or category in _ALLOWED_ERROR_CATEGORIES, (
            f"provider_diagnostics[{index}].error_category must be one of "
            f"{sorted(_ALLOWED_ERROR_CATEGORIES)!r} or None; got {category!r}"
        )


@pytest.mark.parametrize(
    ("case_id", "argv_ext", "universe"),
    UNIVERSES,
    ids=[u[0] for u in UNIVERSES],
)
def test_sector_score_universe_composite_integrity(
    case_id: str, argv_ext: list[str], universe: str
) -> None:
    completed = run_wrapper_or_xfail(
        ["scripts/sector_score.py", *argv_ext],
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"sector_score.py {case_id} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope(payload, universe=universe)

    ranked = data["results"]
    populated_rows = [row for row in ranked if row.get("composite_score_0_100") is not None]
    assert populated_rows, (
        f"expected at least one scored row for {universe}; ranked={ranked!r}"
    )

    _assert_row_warning_entries_well_formed(payload.get("warnings"))
    _assert_provider_diagnostics_well_formed(data.get("provider_diagnostics"))

    # Req 5.4: numeric performance fields inside `benchmark.performance`
    # must be JSON number / null — no Finviz percent-strings slip through.
    # String identifiers (`symbol`, `source`) are legitimate passthroughs.
    benchmark = data.get("benchmark") or {}
    benchmark_perf = benchmark.get("performance") or {}
    assert isinstance(benchmark_perf, dict), (
        f"benchmark.performance must be a dict; got {type(benchmark_perf).__name__}"
    )
    _IDENTIFIER_KEYS = {"symbol", "source"}
    for key, value in benchmark_perf.items():
        if key in _IDENTIFIER_KEYS:
            assert value is None or isinstance(value, str), (
                f"benchmark.performance[{key!r}] identifier must be str/None; "
                f"got {type(value).__name__}={value!r}"
            )
            continue
        assert isinstance(value, (int, float, type(None))) and not isinstance(
            value, bool
        ), (
            f"benchmark.performance[{key!r}] must be int/float/None; "
            f"got {type(value).__name__}={value!r}"
        )
    # Req 5.3: no "Perf 3Y"-style string keys leaked into the envelope.
    for key in benchmark_perf:
        assert " " not in key and "%" not in key, (
            f"benchmark.performance key {key!r} looks like an un-normalized "
            f"Finviz column; expected snake_case numeric fields only"
        )

    for row in populated_rows:
        composite = row.get("composite_score_0_100")
        assert_finite_in_range(
            composite,
            low=0.0,
            high=100.0,
            name=f"{universe}.{row.get('ticker')}.composite_score_0_100",
        )

        signals = row.get("signals")
        assert isinstance(signals, dict), (
            f"row {row.get('ticker')} missing signals dict: {row!r}"
        )
        # Req 5.3: no string percent values inside signals (type safety).
        for key, value in signals.items():
            assert not isinstance(value, str), (
                f"row {row.get('ticker')}.signals[{key!r}] must not be a "
                f"string; got {value!r}"
            )

        # The composite is derived from the declared components. If the
        # composite is populated, at least one component signal must also
        # be populated — the wrapper must never fabricate a score from thin
        # air. This is the Req 4.3 integration-tier complement to the
        # offline `tests/unit/test_sector_score.py` coverage for the
        # "emit null when inputs are missing" clause.
        populated_components = [
            key for key in _COMPONENT_KEYS if _finite(signals.get(key))
        ]
        assert populated_components, (
            f"row {row.get('ticker')} has a composite but no populated "
            f"declared components; signals={signals!r}"
        )

    # Composite-null integrity: any row with a null composite must either
    # lack populated components or be explicitly flagged missing — we assert
    # the former, since the wrapper emits None when signals are empty.
    for row in ranked:
        if row.get("composite_score_0_100") is not None:
            continue
        signals = row.get("signals") or {}
        populated_components = [
            key for key in _COMPONENT_KEYS if _finite(signals.get(key))
        ]
        assert not populated_components, (
            f"row {row.get('ticker')} has null composite despite populated "
            f"components {populated_components!r}; signals={signals!r}"
        )


def test_sector_score_invalid_ticker_emits_provider_warning() -> None:
    """Req 3.3: envelope.warnings lists per-row failures only (one entry per
    failing ticker), while provider-stage detail lives under
    `data.provider_diagnostics`. The bogus ticker must surface in either
    the diagnostics list or `missing_tickers` — never silently absent.
    """

    completed = run_wrapper_or_xfail(
        ["scripts/sector_score.py", "--tickers", "AAPL,ZZZZZZINVALID"],
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"sector_score.py exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope(payload, universe="custom")

    warnings = payload.get("warnings") or []
    _assert_row_warning_entries_well_formed(warnings or None)

    # envelope.warnings carries per-row entries only; no provider/stage
    # keys should appear here (those live in data.provider_diagnostics).
    for w in warnings:
        assert "provider" not in w and "stage" not in w, (
            f"envelope.warnings must carry per-row entries only; "
            f"unexpected provider/stage in {w!r}"
        )

    # `len(warnings)` must equal the count of unique failing tickers so
    # downstream consumers can count problems with a single len() call
    # (Req 3.1 / 3.2).
    failing_symbols = {w.get("symbol") for w in warnings if w.get("symbol")}
    assert len(warnings) == len(failing_symbols), (
        f"envelope.warnings should contain one entry per unique failing "
        f"ticker; got {len(warnings)} entries for symbols "
        f"{sorted(failing_symbols)!r}"
    )

    diagnostics = data.get("provider_diagnostics")
    _assert_provider_diagnostics_well_formed(diagnostics)

    missing = data.get("missing_tickers") or []
    diagnostic_entries = diagnostics or []
    bogus_surfaced = (
        "ZZZZZZINVALID" in missing
        or any(
            entry.get("symbol") == "ZZZZZZINVALID"
            for entry in diagnostic_entries
        )
    )
    assert bogus_surfaced, (
        f"expected ZZZZZZINVALID to surface in missing_tickers or "
        f"provider_diagnostics; missing={missing!r}, "
        f"diagnostics={diagnostic_entries!r}"
    )


def test_sector_score_partial_data_ticker_not_in_missing_tickers() -> None:
    """A ticker with valid raw signals but a null composite (because the
    surviving peer count is too small for a z-score) must NOT appear in
    `missing_tickers`. Only tickers with `ok: False` — i.e. every provider
    path failed — belong there.

    Regression guard: before the fix, `missing_tickers` was derived from
    `composite_score_0_100 is None`, which conflated "no data" with "peer
    count insufficient for z-score". With a 2-ticker custom universe where
    one ticker is bogus, the healthy ticker was wrongly flagged missing.
    """

    completed = run_wrapper_or_xfail(
        ["scripts/sector_score.py", "--tickers", "AAPL,ZZZZZZINVALID"],
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"sector_score.py exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope(payload, universe="custom")

    rows_by_ticker = {row.get("ticker"): row for row in data["results"]}
    aapl_row = rows_by_ticker.get("AAPL")
    assert aapl_row is not None, f"expected AAPL row; rows={list(rows_by_ticker)!r}"
    assert aapl_row.get("ok") is True, (
        f"AAPL must carry ok=True when at least one provider path succeeded; "
        f"row={aapl_row!r}"
    )
    signals = aapl_row.get("signals") or {}
    populated_components = [
        key for key in _COMPONENT_KEYS if _finite(signals.get(key))
    ]
    assert populated_components, (
        f"AAPL must carry at least one finite signal under this invocation; "
        f"signals={signals!r}"
    )

    missing = data.get("missing_tickers") or []
    assert "AAPL" not in missing, (
        f"AAPL has partial data (ok=True, finite signals {populated_components!r}) "
        f"and must not be listed in missing_tickers; missing={missing!r}"
    )

    # The bogus ticker side: every provider path fails, so the row is
    # ok=False and its ticker must surface in missing_tickers.
    bogus_row = rows_by_ticker.get("ZZZZZZINVALID")
    assert bogus_row is not None, (
        f"expected ZZZZZZINVALID row; rows={list(rows_by_ticker)!r}"
    )
    assert bogus_row.get("ok") is False, (
        f"ZZZZZZINVALID must carry ok=False when every provider path failed; "
        f"row={bogus_row!r}"
    )
    assert "ZZZZZZINVALID" in missing, (
        f"ok=False ticker must appear in missing_tickers; missing={missing!r}"
    )
