"""Integration coverage for `scripts/macro_survey.py` across every series.

Parametrizes all 10 series. FRED-backed series skip when FRED_API_KEY is
unset. Several payloads carry multiple facets per observation date
(sloos: multiple loan categories per quarter; ny/tx_manufacturing: multiple
survey topics per month; nonfarm_payrolls: ~145 industry symbols per month;
fomc_documents / dealer_positioning: multiple documents / instruments per
date), so their ordering check is non-decreasing within the window rather
than strictly ascending; pure single-observation series use the strict helper.
"""

from __future__ import annotations

import math
import os
from datetime import date
from typing import Any

import pytest

from tests.integration._sanity import (
    _coerce_date,
    assert_dates_ascending_in_range,
    assert_finite_in_range,
)
from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_FRED_SERIES = {
    "sloos",
    "ny_manufacturing",
    "tx_manufacturing",
    "michigan",
    "chicago_conditions",
    "nonfarm_payrolls",
}
# inflation_exp intentionally omitted: OpenBB's inflation_expectations
# endpoint accepts only the federal_reserve provider (no API key required).


# series → (provider-or-None, takes_date_window, strictly_ascending, bounded_window)
# `strictly_ascending=False` is declared for payloads that legitimately
# return multiple rows per date (multi-facet series), so the helper's
# strict-monotonic rule does not reject correct upstream data.
SERIES_MATRIX: list[tuple[str, str | None, bool, bool, tuple[str, str] | None]] = [
    ("sloos", "fred", True, False, ("2022-01-01", "2024-12-31")),
    ("ny_manufacturing", "fred", True, False, ("2022-01-01", "2024-12-31")),
    ("tx_manufacturing", "fred", True, False, ("2022-01-01", "2024-12-31")),
    ("michigan", "fred", True, True, ("2022-01-01", "2024-12-31")),
    ("inflation_exp", "federal_reserve", False, True, None),
    ("chicago_conditions", "fred", True, True, ("2022-01-01", "2024-12-31")),
    ("nonfarm_payrolls", "fred", False, False, None),
    ("fomc_documents", None, False, False, None),
    ("cli", "oecd", True, True, ("2023-01-01", "2024-12-31")),
    ("dealer_positioning", None, True, False, ("2024-01-01", "2024-12-31")),
]


def _assert_envelope(payload: Any, *, series: str, provider: str | None) -> dict[str, Any]:
    assert isinstance(payload, dict), f"expected dict envelope, got {type(payload).__name__}"
    assert "error" not in payload, f"unexpected top-level error: {payload.get('error')!r}"
    assert payload.get("source") == "marketdesk-for-ai-agents", payload
    assert payload.get("tool") == "macro_survey", payload
    data = payload.get("data")
    assert isinstance(data, dict), f"data must be dict, got {type(data).__name__}"
    assert "ok" not in data, (
        f"data must not carry a flattened `ok` key under the single_emit envelope; data={data!r}"
    )
    assert "records" not in data, (
        f"data must not carry a flattened `records` key; use data.results instead; data={data!r}"
    )
    assert data.get("series") == series, data
    assert data.get("provider") == provider, data
    # Regression guard: every series must emit a truthy `unit_note` so AI
    # agents can disambiguate scale (decimal / percent / index / absolute /
    # millions of USD). Content is not validated here — wording is kept in
    # review, this assertion only detects omissions.
    assert data.get("unit_note"), (
        f"series {series} missing data.unit_note; got {data.get('unit_note')!r}"
    )
    warnings = payload.get("warnings") or []
    if warnings:
        err = " ".join(str(w.get("error") or "") for w in warnings)
        # Known upstream flake: FRED occasionally returns a SLOOS row with
        # a NaN `title`, which OpenBB's Pydantic `FredSeniorLoanOfficerSurveyData`
        # model rejects. The wrapper is propagating the upstream error
        # correctly (exit 0, empty data.results, populated top-level
        # warnings — the AI-safe path); xfail so verdicts aren't
        # downgraded by an upstream data-quality hiccup outside our control.
        if "nan" in err.lower() and "title" in err.lower():
            pytest.xfail(
                f"upstream OpenBB Pydantic validation rejected NaN title in "
                f"{series} payload (FRED upstream data-quality flake)"
            )
        raise AssertionError(f"series {series} failed: warnings={warnings!r}")
    results = data.get("results")
    assert isinstance(results, list) and results, f"results must be non-empty; got {results!r}"
    return data


@pytest.mark.parametrize(
    ("series", "provider", "takes_dates", "strictly_ascending", "window"),
    SERIES_MATRIX,
    ids=[m[0] for m in SERIES_MATRIX],
)
def test_macro_survey_series_happy_path(
    series: str,
    provider: str | None,
    takes_dates: bool,
    strictly_ascending: bool,
    window: tuple[str, str] | None,
) -> None:
    if series in _FRED_SERIES and not os.environ.get("FRED_API_KEY"):
        pytest.skip(
            f"{series} requires FRED_API_KEY; skipped without credential"
        )

    argv: list[str] = ["scripts/macro_survey.py", "--series", series]
    if provider is not None:
        argv += ["--provider", provider]
    if takes_dates and window is not None:
        argv += ["--start", window[0], "--end", window[1]]

    completed = run_wrapper_or_xfail(argv, timeout=120)
    assert completed.returncode == 0, (
        f"macro_survey.py --series {series} exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    data = _assert_envelope(payload, series=series, provider=provider)

    records = data["results"]
    start_bound = date.fromisoformat(window[0]) if window else None
    end_bound = date.fromisoformat(window[1]) if window else None

    date_key: str | None = None
    for candidate in ("date", "timestamp", "period", "release_date"):
        if candidate in records[0]:
            date_key = candidate
            break

    if date_key is not None:
        if strictly_ascending:
            assert_dates_ascending_in_range(
                records,
                date_key=date_key,
                start=start_bound,
                end=end_bound,
            )
        else:
            previous: date | None = None
            for index, record in enumerate(records):
                parsed = _coerce_date(
                    record[date_key], name=f"record[{index}].{date_key}"
                )
                if start_bound is not None:
                    assert parsed >= start_bound, (
                        f"record[{index}].{date_key} {parsed} precedes {start_bound}"
                    )
                if end_bound is not None:
                    assert parsed <= end_bound, (
                        f"record[{index}].{date_key} {parsed} exceeds {end_bound}"
                    )
                if previous is not None:
                    assert parsed >= previous, (
                        f"record[{index}].{date_key} {parsed} precedes previous {previous}"
                    )
                previous = parsed

    for index, record in enumerate(records):
        for key, value in record.items():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            if isinstance(value, (int, float)):
                assert_finite_in_range(
                    value,
                    low=-1e15,
                    high=1e15,
                    name=f"{series}[{index}].{key}",
                )
