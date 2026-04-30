"""Integration coverage for `scripts/entry_timing_scorer.py`.

Pins the JSON contract, envelope invariants, sort-order parity across
`--blend-profile`, the `volume_avg_window == "20d_real"` reviewer-R1
fix, the `analytical_caveats` passthrough, the `interpretation_hint`
negative invariant (Req 9.5), the estimator-toggle echo + behavior
change, and the provider-parametrized parity for the provider-aware
quote field resolver.

FMP-dependent slices are auto-skipped when `FMP_API_KEY` is absent so
the free-tier `-m integration` run stays green.
"""

from __future__ import annotations

import math
import os
from typing import Any

import pytest

from tests.integration._sanity import assert_finite_in_range
from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_BASKET = ("AAPL", "MSFT", "NVDA", "SPY", "QQQ")


_REQUIRED_CAVEATS = {
    "scores_are_basket_internal_ranks_not_absolute_strength",
    "trend_and_mean_reversion_are_separate_axes",
    "earnings_proximity_is_flag_not_score_component",
}


def _run(argv_extra: list[str], *, timeout: int = 240) -> Any:
    """Invoke the wrapper and parse stdout into a payload."""

    argv = [
        "scripts/entry_timing_scorer.py",
        "--tickers",
        ",".join(_BASKET),
        *argv_extra,
    ]
    completed = run_wrapper_or_xfail(argv, timeout=timeout)
    assert completed.returncode == 0, (
        f"entry_timing_scorer.py exited {completed.returncode}; "
        f"argv={argv!r}; stderr tail:\n{completed.stderr[-2000:]}"
    )
    return assert_stdout_is_single_json(completed)


def _assert_envelope_base(payload: Any) -> dict[str, Any]:
    """Check the envelope root and return the `data` namespace."""

    assert isinstance(payload, dict), (
        f"expected dict envelope; got {type(payload).__name__}"
    )
    assert "error" not in payload, (
        f"unexpected top-level error: {payload.get('error')!r}"
    )
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "entry_timing_scorer", payload
    data = payload.get("data")
    assert isinstance(data, dict), (
        f"data must be dict; got {type(data).__name__}"
    )
    results = data.get("results")
    assert isinstance(results, list) and results, (
        f"data.results must be a non-empty list; got {results!r}"
    )
    for idx, row in enumerate(results):
        assert isinstance(row, dict), (
            f"data.results[{idx}] must be dict; got {type(row).__name__}"
        )
    return data


def _primary_score_key(blend_profile: str) -> str:
    if blend_profile in ("none", "trend"):
        return "trend_score_0_100"
    if blend_profile == "mean_reversion":
        return "mean_reversion_score_0_100"
    if blend_profile == "balanced":
        return "blended_score_0_100"
    raise AssertionError(f"unknown blend_profile: {blend_profile!r}")


def _ok_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if r.get("ok") is True]


# ---------------------------------------------------------------------------
# Req 12.1 / 12.4 / 12.5 / 12.6 / 12.7 — envelope + baseline contract
# ---------------------------------------------------------------------------


def test_envelope_shape_and_baseline_fields() -> None:
    """Req 12.1 / 12.4 / 12.5 / 12.6 / 12.7.

    Exercises the keyless default provider and asserts the envelope
    shape, `volume_avg_window` fix, earnings-unit/threshold echo,
    analytical caveats, `interpretation_hint` absence, and both sub-
    scores on every `ok:true` row.
    """

    payload = _run(["--provider", "yfinance"])
    data = _assert_envelope_base(payload)

    # Req 12.5: earnings unit + threshold echo
    assert data.get("days_to_next_earnings_unit") == "calendar_days", data
    assert data.get("earnings_proximity_days_threshold") == 5, data
    assert data.get("earnings_window_days") == 45, data

    # Req 12.6: analytical caveats
    caveats = data.get("analytical_caveats")
    assert isinstance(caveats, list), f"analytical_caveats must be list; got {caveats!r}"
    missing = _REQUIRED_CAVEATS - set(caveats)
    assert not missing, (
        f"analytical_caveats missing required strings {sorted(missing)!r}; "
        f"got {caveats!r}"
    )

    results = data["results"]
    ok_rows = _ok_rows(results)
    assert ok_rows, f"expected at least one ok:true row; results={results!r}"

    # Req 12.6: no row carries `interpretation_hint`
    for row in results:
        assert "interpretation_hint" not in row, (
            f"Req 9.5 negative invariant violated: row carries "
            f"`interpretation_hint`: {row!r}"
        )
        interpretation = row.get("interpretation")
        if interpretation is not None:
            assert "interpretation_hint" not in interpretation, (
                f"Req 9.5 negative invariant violated inside interpretation "
                f"block: {row!r}"
            )

    # Req 12.4: at least one ok:true row carries volume_avg_window == "20d_real"
    saw_20d_real = any(
        row.get("volume_avg_window") == "20d_real" for row in ok_rows
    )
    assert saw_20d_real, (
        f"Req 5.6 / 12.4: expected at least one ok:true row with "
        f"volume_avg_window == '20d_real'; rows={ok_rows!r}"
    )

    # Req 12.7: both sub-scores present on every ok:true row; bounded in
    # [0, 100] where not null.
    for row in ok_rows:
        for key in ("trend_score_0_100", "mean_reversion_score_0_100"):
            assert key in row, f"ok:true row missing `{key}`: {row!r}"
        t = row.get("trend_score_0_100")
        m = row.get("mean_reversion_score_0_100")
        if t is not None:
            assert_finite_in_range(
                t, low=0.0, high=100.0, name=f"{row.get('symbol')}.trend_score_0_100"
            )
        if m is not None:
            assert_finite_in_range(
                m,
                low=0.0,
                high=100.0,
                name=f"{row.get('symbol')}.mean_reversion_score_0_100",
            )


# ---------------------------------------------------------------------------
# Req 12.3 — sort-order parity with --blend-profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blend_profile", "extra_args"),
    [
        ("none", []),
        ("mean_reversion", ["--blend-profile", "mean_reversion"]),
        ("balanced", ["--blend-profile", "balanced"]),
    ],
    ids=["none", "mean_reversion", "balanced"],
)
def test_sort_order_parity_by_blend_profile(
    blend_profile: str, extra_args: list[str]
) -> None:
    """Req 12.3: `data.results[]` is sorted descending by the active
    primary score (per `--blend-profile`), with null scores sinking."""

    payload = _run(["--provider", "yfinance", *extra_args])
    data = _assert_envelope_base(payload)
    results = data["results"]

    key = _primary_score_key(blend_profile)
    # Null scores must sink to the bottom; among non-null scores the
    # values must descend monotonically.
    seen_null = False
    previous: float | None = None
    for idx, row in enumerate(results):
        score = row.get(key)
        if score is None:
            seen_null = True
            continue
        assert not seen_null, (
            f"Req 12.3: row[{idx}] with {key}={score!r} appears after a "
            f"null-score row; null scores must sink. results={results!r}"
        )
        if previous is not None:
            assert score <= previous + 1e-9, (
                f"Req 12.3: row[{idx}] {key}={score!r} is not <= previous "
                f"{previous!r}; expected descending order. results={results!r}"
            )
        previous = score

    # Under --blend-profile != none, the blend field must exist on each
    # ok:true row; under 'none', it must NOT appear (Req 6.6).
    for row in _ok_rows(results):
        if blend_profile == "none":
            assert "blended_score_0_100" not in row, (
                f"Req 6.6: --blend-profile none must omit "
                f"blended_score_0_100; got {row!r}"
            )
            assert "blend_profile" not in row, (
                f"Req 6.6: --blend-profile none must omit blend_profile; "
                f"got {row!r}"
            )
        else:
            assert "blended_score_0_100" in row, (
                f"Req 9.2: --blend-profile={blend_profile} must include "
                f"blended_score_0_100; got {row!r}"
            )
            assert row.get("blend_profile") == blend_profile, (
                f"Req 9.2: --blend-profile={blend_profile} must echo on "
                f"row; got {row!r}"
            )


# ---------------------------------------------------------------------------
# Req 12.8 — estimator echo + behavior change
# ---------------------------------------------------------------------------


def test_volume_z_estimator_toggle_echo_and_difference() -> None:
    """Req 12.8: two-run estimator comparison must echo the flag per row
    and produce at least one row where the two estimators give
    different `volume_z_20d` values (confirming the flag is wired
    through rather than silently ignored)."""

    robust = _run(["--provider", "yfinance", "--volume-z-estimator", "robust"])
    classical = _run(
        ["--provider", "yfinance", "--volume-z-estimator", "classical"]
    )
    robust_rows = {
        r["symbol"]: r
        for r in robust["data"]["results"]
        if r.get("ok") is True
    }
    classical_rows = {
        r["symbol"]: r
        for r in classical["data"]["results"]
        if r.get("ok") is True
    }

    # Per-row echo of the active estimator choice.
    for row in robust_rows.values():
        assert row.get("volume_z_estimator") == "robust", row
    for row in classical_rows.values():
        assert row.get("volume_z_estimator") == "classical", row

    # At least one common ok:true symbol where the two estimators
    # produce different (and non-null) z values. The robust log-MAD and
    # classical mean-stdev z are not equal except in degenerate cases;
    # a single-basket equality across every ticker would indicate the
    # flag is not wired through.
    common = set(robust_rows) & set(classical_rows)
    assert common, (
        f"expected at least one common ok:true ticker across runs; "
        f"robust={list(robust_rows)}, classical={list(classical_rows)}"
    )
    diffs = []
    for sym in common:
        r_z = (robust_rows[sym].get("signals") or {}).get("volume_z_20d")
        c_z = (classical_rows[sym].get("signals") or {}).get("volume_z_20d")
        if r_z is None or c_z is None:
            continue
        if not math.isclose(r_z, c_z, rel_tol=1e-9, abs_tol=1e-9):
            diffs.append((sym, r_z, c_z))
    assert diffs, (
        f"Req 12.8: expected at least one ok:true row where robust vs "
        f"classical volume_z_20d differ; robust_rows={robust_rows!r}, "
        f"classical_rows={classical_rows!r}"
    )


# ---------------------------------------------------------------------------
# Req 12.10 / 12.11 — provider-parametrized parity (yfinance + fmp)
# ---------------------------------------------------------------------------


def _require_fmp_or_skip() -> None:
    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset; --provider fmp integration slice requires the "
            "FMP free tier"
        )


@pytest.mark.parametrize("provider", ["yfinance", "fmp"], ids=["yfinance", "fmp"])
def test_provider_parity_ma_fields_non_null(provider: str) -> None:
    """Req 12.10: under both providers, `signals.ma_200d` and
    `signals.ma_50d` must be non-null on at least one `ok:true` equity
    row (confirming the provider-aware quote field resolver reads the
    correct key per provider)."""

    if provider == "fmp":
        _require_fmp_or_skip()

    payload = _run(["--provider", provider])
    data = _assert_envelope_base(payload)
    ok_rows = _ok_rows(data["results"])
    assert ok_rows, f"{provider}: expected at least one ok:true row"

    rows_with_ma200 = [
        r for r in ok_rows if (r.get("signals") or {}).get("ma_200d") is not None
    ]
    rows_with_ma50 = [
        r for r in ok_rows if (r.get("signals") or {}).get("ma_50d") is not None
    ]
    assert rows_with_ma200, (
        f"Req 12.10 ({provider}): expected at least one ok:true row with "
        f"signals.ma_200d non-null; ok_rows={ok_rows!r}"
    )
    assert rows_with_ma50, (
        f"Req 12.10 ({provider}): expected at least one ok:true row with "
        f"signals.ma_50d non-null; ok_rows={ok_rows!r}"
    )


@pytest.mark.parametrize("provider", ["yfinance", "fmp"], ids=["yfinance", "fmp"])
def test_provider_parity_volume_z_non_null(provider: str) -> None:
    """Req 12.11: under both providers, `volume_z_20d` must be non-null
    on at least one `ok:true` equity row, confirming that the locally
    computed 20-day z is provider-independent and that the FMP-specific
    `volume_reference_unavailable_on_provider` flag does not bleed into
    the scorer signal itself."""

    if provider == "fmp":
        _require_fmp_or_skip()

    payload = _run(["--provider", provider])
    data = _assert_envelope_base(payload)
    ok_rows = _ok_rows(data["results"])
    with_z = [
        r for r in ok_rows if (r.get("signals") or {}).get("volume_z_20d") is not None
    ]
    assert with_z, (
        f"Req 12.11 ({provider}): expected at least one ok:true row with "
        f"signals.volume_z_20d non-null; ok_rows={ok_rows!r}"
    )


def test_fmp_volume_reference_unavailable_flag_on_every_ok_row() -> None:
    """Req 12.10 / 5.7: under `--provider fmp`, every `ok:true` row must
    carry `"volume_reference_unavailable_on_provider"` in
    `data_quality_flags[]`, and `volume_reference.*.value` must be
    `null` while the `window` labels are preserved."""

    _require_fmp_or_skip()

    payload = _run(["--provider", "fmp"])
    data = _assert_envelope_base(payload)
    ok_rows = _ok_rows(data["results"])
    assert ok_rows, "fmp: expected at least one ok:true row"

    for row in ok_rows:
        flags = row.get("data_quality_flags") or []
        assert "volume_reference_unavailable_on_provider" in flags, (
            f"Req 5.7 / 12.10: fmp ok:true row missing "
            f"`volume_reference_unavailable_on_provider` flag; row={row!r}"
        )
        reference = row.get("volume_reference") or {}
        assert set(reference.keys()) >= {"volume_average", "volume_average_10d"}, (
            f"volume_reference missing expected keys; got {reference!r}"
        )
        assert reference["volume_average"].get("window") == "3m_rolling", (
            f"fmp row must preserve volume_average window label; "
            f"got {reference!r}"
        )
        assert reference["volume_average"].get("value") is None, (
            f"fmp row must emit volume_average value=null; got {reference!r}"
        )
        assert reference["volume_average_10d"].get("window") == "10d", (
            f"fmp row must preserve volume_average_10d window label; "
            f"got {reference!r}"
        )
        assert reference["volume_average_10d"].get("value") is None, (
            f"fmp row must emit volume_average_10d value=null; got {reference!r}"
        )
