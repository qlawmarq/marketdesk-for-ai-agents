---
name: sector-stock-screener
description: >-
  Expand a sector / theme / factor ETF universe into a ranked individual-stock
  candidate basket scored on momentum x value x quality x forward-consensus.
  Value and quality z-scores are computed within GICS sector; momentum, range
  position, trend, and analyst upside stay cross-sectional. Use when the analyst
  needs mid-term (6-month to 2-year) watchlist candidates distilled from sector
  ETF constituents in one invocation.
covers_scripts:
  - scripts/sector_stock_screener.py
---

## When to Use

- Quarterly mid-term watchlist drafting: rank deduplicated constituents of the top-N sector ETFs on a four-axis composite.
- Follow-on to `../sector_score/SKILL.md` when the ETF-level ranks already point at the right sectors and the next question is "which names inside."

Not for: single-name technicals (use `../momentum/SKILL.md`), daily entry-timing on a fixed basket (use `../entry-timing-scorer/SKILL.md`), JP-listed ETF universes (out of MVP scope; FMP Starter+ does not cover TSE ETFs cleanly).

## Inputs

| Argument | Default | Notes |
|---|---|---|
| `--universe {sector-spdr,theme-ark,global-factor}` | — | Mutually exclusive with `--tickers`. `jp-sector` is rejected with `error_category: validation` in MVP. |
| `--tickers <CSV>` | — | Comma-separated ETF tickers. Input order preserved, deduplicated first-seen. |
| `--top-sectors <int>` | `3` | Bounded `[1, 11]`. Echoed as `data.top_sectors_requested`. |
| `--top-stocks-per-sector <int>` | `20` | Bounded `[1, 100]`. Echoed as `data.top_stocks_per_sector_requested`. |
| `--weight-clenow-{90,180}` / `--weight-return-{6m,3m,12m}` / `--weight-risk-adj` | `0.25 / 0.25 / 0.20 / 0.15 / 0.10 / 0.05` | Sector-rank weights; defaults match `../sector_score/SKILL.md`. |
| `--weight-sub-{momentum,value,quality,forward}` | `0.25` each | Top-level sub-score composition weights; sum-of-available-weights normalization. |

Sub-score internal weights (inside `momentum_z`, `value_z`, `quality_z`, `forward_z`) are fixed in MVP and echoed under `data.weights.sub_scores_internal`; they are not CLI-tunable.

## Provider

FMP Starter+ pinned at every OpenBB call. `FMP_API_KEY` is required — a missing / empty key exits 2 with `error_category: "credential"` before any OpenBB call is issued (Req 2.2). `etf.holdings` requires Starter+; free-tier keys surface as `error_category: "plan_insufficient"` under `data.provider_diagnostics`. No `--provider` flag. See `../_providers/SKILL.md`.

## Command

```bash
uv run scripts/sector_stock_screener.py --universe sector-spdr
uv run scripts/sector_stock_screener.py --universe sector-spdr --top-sectors 2 --top-stocks-per-sector 10
uv run scripts/sector_stock_screener.py --tickers XLK,XLF,XLE --top-sectors 2
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "sector_stock_screener"`. `data.results[]` holds one row per resolved stock, sorted by `composite_score_0_100` descending with `null` scores sinking; `rank` is 1-indexed from the sort position. No truncation — callers apply `jq '.data.results[:N]'` themselves.

`data` namespace siblings: `universe`, `tickers` (the ETF universe echoed back), `weights` (sector + sub_scores + sub_scores_internal), `sector_ranks[]`, `top_sectors_requested`, `top_stocks_per_sector_requested`, `etf_holdings_updated_max_age_days`, `missing_tickers`, `analytical_caveats`, `notes`, optional `non_us_tickers_filtered` (only when non-empty), optional `provider_diagnostics` (only when at least one stage failed). No `provider` field under `data` — single-provider wrapper.

Per-row `ok: true` minimum shape: `{symbol, ok, rank, gics_sector, sector_origins[], composite_score_0_100, momentum_score_0_100, value_score_0_100, quality_score_0_100, forward_score_0_100, signals{17 fields}, z_scores{fixed key set with _sector_neutral / _basket suffixes}, basket_size, sector_group_size, basket_size_sufficient, data_quality_flags[], interpretation{5 keys}}`. `ok: false` rows omit `*_score_0_100` and `z_scores` and carry `{symbol, ok, rank, gics_sector, sector_origins, error, error_type, error_category}`.

Truncated real-run sample (`--universe sector-spdr --top-sectors 2 --top-stocks-per-sector 5`):

```json
{
  "tool": "sector_stock_screener",
  "data": {
    "universe": "sector-spdr",
    "top_sectors_requested": 2,
    "top_stocks_per_sector_requested": 5,
    "etf_holdings_updated_max_age_days": 4,
    "analytical_caveats": [
      "scores_are_basket_internal_ranks_not_absolute_strength",
      "value_and_quality_are_sector_neutral_z_scores",
      "momentum_and_forward_are_basket_wide_z_scores",
      "etf_holdings_may_lag_spot_by_up_to_one_week",
      "forward_score_requires_number_of_analysts_ge_5",
      "number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions"
    ],
    "sector_ranks": [{"ticker":"XLK","rank":1,"composite_score_0_100":82.3,"composite_z":1.29}],
    "results": [
      {"symbol":"NVDA","ok":true,"rank":1,"gics_sector":"Information Technology",
       "composite_score_0_100":78.4,"momentum_score_0_100":86.1,"value_score_0_100":41.2,
       "quality_score_0_100":72.0,"forward_score_0_100":62.3,
       "signals":{"clenow_90":0.42,"ev_ebitda_yield":0.038,"roe":0.51,"target_upside":0.14,
                  "number_of_analysts":38,"last_price":420.0},
       "data_quality_flags":[]}
    ]
  }
}
```

## Interpretation

`data.analytical_caveats` travels with every response — read it before acting on scores:

- `scores_are_basket_internal_ranks_not_absolute_strength` — a "70" means "top third of this resolved pool," not absolute quality. Re-rank across different `--universe` values and the number moves.
- `value_and_quality_are_sector_neutral_z_scores` / `momentum_and_forward_are_basket_wide_z_scores` — EV/EBITDA and ROE are z-scored within each GICS sector to neutralise ~10x cross-sector dispersion (Damodaran 2026-01); momentum, 52w range position, MA-200 trend, and analyst upside stay cross-sectional. Check `z_scores.z_<factor>_sector_neutral` vs `z_<factor>_basket` per row to see which branch populated the score.
- `forward_score_requires_number_of_analysts_ge_5` — `target_upside` and `forward_score_0_100` are `null` on names with < 5 distinct analyst firms in the last 90 days (`analyst_coverage_too_thin` in `data_quality_flags`). `number_of_analysts` itself is derived from `estimates.price_target` 90-day distinct-firm counts, not the consensus endpoint.

## Scope boundaries

- No buy / sell / hold output — the contract ends at ranked candidates. Integration test enforces absence of `buy_signal` / `recommendation` fields.
- No backtesting, no portfolio optimization, no macro-quadrant blending.
- No analyst-revision-momentum sub-score in MVP; only the `target_upside` level feeds the `forward_z` axis.
- No JP sector coverage in MVP (`--universe jp-sector` is rejected).
- Stdout JSON only — no cache files, no state between invocations.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Missing / empty `FMP_API_KEY` → exit 2 with top-level `error_category: "credential"` before any OpenBB call.
- Per-sector `etf.holdings` failure → `data.provider_diagnostics[{provider, stage:"etf_holdings", symbol, error, error_category}]`; remaining sectors continue. Every sector failing with the same fatal category → exit 2 via `aggregate_emit`.
- Pool size after dedup < 3 → top-level `warnings[{symbol:null, error:"insufficient stock pool size for cross-sectional z-score", error_category:"validation"}]`; per-row rows still emit with `null` z-scores.
- Per-stock basket < 3 on a factor → `z_<factor>_basket` becomes `null` on every row and `basket_too_small_for_z(<factor>)` is appended to `data_quality_flags[]`.

## References

- `scripts/sector_stock_screener.py`
- `scripts/sector_score.py` (`UNIVERSES`, `build_scores`, `_classify_ticker_failure` imported verbatim)
- `README.md` § 1-1 feature row (sector-stock screener).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
- `../sector_score/SKILL.md`, `../etf/SKILL.md`, `../estimates/SKILL.md`.
