"""Integration coverage for ``scripts/sector_stock_screener.py``.

Locks in the Req 13.x envelope contract via a subprocess run against
live FMP. Auto-skips when ``FMP_API_KEY`` is absent so the free-tier
``-m integration`` lane stays green.

Asserts (one-to-one with Req 13.1–13.8):

- 13.1 subprocess invocation through ``run_wrapper_or_xfail`` with
  ``FMP_API_KEY`` skip-guard;
- 13.2 ``data.results[]`` sorted descending by ``composite_score_0_100``
  with null scores sinking;
- 13.3 ``data.analytical_caveats`` carries the six required base
  strings, and no per-ticker row emits ``buy_signal`` / ``recommendation``;
- 13.4 every ``ok: true`` row carries ``gics_sector`` + ≥1 sector
  origin and all four ``*_score_0_100`` fields (``forward_score_0_100``
  may be null on thin-coverage rows);
- 13.5 ``data.top_sectors_requested`` / ``top_stocks_per_sector_requested``
  match the CLI inputs and ``etf_holdings_updated_max_age_days`` is a
  non-negative integer;
- 13.6 ≥1 ``z_scores`` key ends in ``_sector_neutral`` and ≥1 ends in
  ``_basket``;
- 13.7 the 90-day distinct-firm disclosure caveat is present on every
  successful run (overlaps with 13.3 but pinned separately per the
  requirements text);
- 13.8 the wrapper source contains no positional batched lookup of the
  form ``results[<integer>]``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import (
    assert_stdout_is_single_json,
    run_wrapper_or_xfail,
)

pytestmark = pytest.mark.integration


_REQUIRED_BASE_CAVEATS = {
    "scores_are_basket_internal_ranks_not_absolute_strength",
    "value_and_quality_are_sector_neutral_z_scores",
    "momentum_and_forward_are_basket_wide_z_scores",
    "etf_holdings_may_lag_spot_by_up_to_one_week",
    "forward_score_requires_number_of_analysts_ge_5",
    "number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions",
}

_TOP_SECTORS = 2
_TOP_STOCKS = 5


def _require_fmp_or_skip() -> None:
    if not os.getenv("FMP_API_KEY"):
        pytest.skip(
            "FMP_API_KEY unset; sector_stock_screener integration slice requires "
            "FMP Starter+"
        )


def _run() -> dict[str, Any]:
    _require_fmp_or_skip()
    argv = [
        "scripts/sector_stock_screener.py",
        "--universe",
        "sector-spdr",
        "--top-sectors",
        str(_TOP_SECTORS),
        "--top-stocks-per-sector",
        str(_TOP_STOCKS),
    ]
    completed = run_wrapper_or_xfail(argv, timeout=600)
    assert completed.returncode == 0, (
        f"sector_stock_screener.py exited {completed.returncode}; "
        f"stderr tail:\n{completed.stderr[-2000:]}"
    )
    payload = assert_stdout_is_single_json(completed)
    assert isinstance(payload, dict), payload
    assert payload.get("tool") == "sector_stock_screener", payload
    data = payload.get("data")
    assert isinstance(data, dict), payload
    return payload


def test_envelope_shape_and_data_namespace_echoes() -> None:
    """Req 13.5 + baseline envelope coverage."""

    payload = _run()
    data = payload["data"]
    assert data["top_sectors_requested"] == _TOP_SECTORS
    assert data["top_stocks_per_sector_requested"] == _TOP_STOCKS
    assert isinstance(data.get("universe"), str)
    assert isinstance(data.get("tickers"), list) and data["tickers"]
    max_age = data.get("etf_holdings_updated_max_age_days")
    assert max_age is None or (isinstance(max_age, int) and max_age >= 0), (
        f"etf_holdings_updated_max_age_days must be None or a non-negative "
        f"integer; got {max_age!r}"
    )
    weights = data.get("weights")
    assert isinstance(weights, dict)
    assert {"sector", "sub_scores", "sub_scores_internal"} <= set(weights)


def test_results_sorted_by_composite_desc_nulls_sink() -> None:
    """Req 13.2."""

    payload = _run()
    results = payload["data"]["results"]
    assert isinstance(results, list) and results, results

    seen_null_or_failure = False
    previous: float | None = None
    for idx, row in enumerate(results):
        if row.get("ok") is not True:
            seen_null_or_failure = True
            continue
        score = row.get("composite_score_0_100")
        if score is None:
            seen_null_or_failure = True
            continue
        assert not seen_null_or_failure, (
            f"Req 13.2: row[{idx}] with composite={score!r} appears after a "
            f"null-score or ok:false row; null scores must sink"
        )
        if previous is not None:
            assert score <= previous + 1e-9, (
                f"Req 13.2: row[{idx}] composite={score!r} is not <= previous "
                f"{previous!r}"
            )
        previous = score

    # Ranks are 1-indexed and dense over the full emitted list.
    assert [r.get("rank") for r in results] == list(range(1, len(results) + 1))


def test_analytical_caveats_and_negative_invariants() -> None:
    """Req 13.3 + 13.7 + Req 10.4 negative invariant."""

    payload = _run()
    data = payload["data"]
    caveats = data.get("analytical_caveats")
    assert isinstance(caveats, list) and caveats, caveats
    missing = _REQUIRED_BASE_CAVEATS - set(caveats)
    assert not missing, f"analytical_caveats missing {sorted(missing)!r}"

    body = str(payload)
    assert "buy_signal" not in body, "Req 10.4: no buy_signal anywhere"
    assert "recommendation" not in body, "Req 10.4: no recommendation anywhere"

    for row in data["results"]:
        assert "buy_signal" not in row, row
        assert "recommendation" not in row, row


def test_ok_row_shape_and_score_fields() -> None:
    """Req 13.4."""

    payload = _run()
    results = payload["data"]["results"]
    ok_rows = [r for r in results if r.get("ok") is True]
    assert ok_rows, "expected at least one ok:true row"
    for row in ok_rows:
        assert isinstance(row.get("gics_sector"), (str, type(None)))
        origins = row.get("sector_origins")
        assert isinstance(origins, list) and len(origins) >= 1, row
        # The four sub-score fields are always present; `forward` may be
        # null on thin-coverage rows (Req 6.4 / 6.5).
        for key in (
            "composite_score_0_100",
            "momentum_score_0_100",
            "value_score_0_100",
            "quality_score_0_100",
        ):
            assert key in row, row
        assert "forward_score_0_100" in row, row


def test_z_scores_block_tags_both_sector_neutral_and_basket() -> None:
    """Req 13.6."""

    payload = _run()
    ok_rows = [r for r in payload["data"]["results"] if r.get("ok") is True]
    assert ok_rows
    sample_keys = set()
    for row in ok_rows:
        z = row.get("z_scores") or {}
        sample_keys.update(z.keys())
    assert any(k.endswith("_sector_neutral") for k in sample_keys), sample_keys
    assert any(k.endswith("_basket") for k in sample_keys), sample_keys


def test_no_positional_batched_lookup_in_wrapper_source() -> None:
    """Req 13.8: structural guarantee enforced by source-text grep.

    Runs offline — does NOT require FMP_API_KEY — so the grep regression
    gate fires even in free-tier CI.
    """

    repo_root = Path(__file__).resolve().parent.parent.parent
    source = (repo_root / "scripts" / "sector_stock_screener.py").read_text(
        encoding="utf-8"
    )
    # Strip docstrings and comments before the grep so Req 10.7 / Req
    # 13.8 documentation that legitimately mentions "results[]" in prose
    # does not false-positive.
    code_only_lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        code_only_lines.append(line)
    code_only = "\n".join(code_only_lines)
    # Strip triple-quoted docstrings (both """ and ''').
    code_only = re.sub(r'"""[\s\S]*?"""', "", code_only)
    code_only = re.sub(r"'''[\s\S]*?'''", "", code_only)

    match = re.search(r"\bresults\[\s*\d+\s*\]", code_only)
    assert match is None, (
        f"Req 13.8 / Req 5.5: positional batched lookup detected in "
        f"sector_stock_screener.py source: {match.group(0)!r}. Route through "
        f"`_index_by_symbol` instead."
    )


def test_sector_ranks_echoed_under_data_namespace() -> None:
    """Req 3.4: every resolved sector ETF is echoed with its rank."""

    payload = _run()
    ranks = payload["data"].get("sector_ranks")
    assert isinstance(ranks, list) and ranks, ranks
    for entry in ranks:
        assert set(entry.keys()) >= {
            "ticker",
            "rank",
            "composite_score_0_100",
            "composite_z",
        }, entry
