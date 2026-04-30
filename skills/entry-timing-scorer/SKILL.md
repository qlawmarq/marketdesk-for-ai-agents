---
name: entry-timing-scorer
description: >-
  Score a short basket (5-10 tickers, typically holdings + watchlist) on
  entry-timing analytics split across a trend axis and a mean-reversion
  axis. Use when an agent needs per-ticker momentum / range / RSI / MACD /
  volume-z-score readings for the daily holdings-monitoring loop, with an
  earnings-proximity flag kept outside the composite.
covers_scripts:
  - scripts/entry_timing_scorer.py
---

## When to Use

- Daily holdings + watchlist monitoring: rank a short basket on trend and mean-reversion axes in one invocation.
- Detecting "earnings close + momentum high" cases where a blended score would mute the signal.
- Locking reviewer R1's 20-day-vs-3-month volume-label ambiguity: every row carries `volume_avg_window: "20d_real"`.

Not for: single-indicator queries (use `../momentum/SKILL.md`), sector / factor ETF rotation (use `../sector_score/SKILL.md`), portfolio-trigger matching or macro-quadrant blending (stay in the analyst layer).

## Inputs

| Argument | Default | Notes |
|---|---|---|
| `--tickers <CSV>` | — | Mutually exclusive with `--portfolio-file`. Input order preserved, deduplicated. |
| `--portfolio-file <path>` | — | YAML with `positions[].ticker` → `context: holding`, `watchlist[].ticker` → `context: watchlist`. No other fields read. |
| `--context {watchlist,holding,unknown}` | `unknown` | Applies uniformly under `--tickers`. Rejected under `--portfolio-file`. |
| `--provider {yfinance,fmp}` | `yfinance` | `yfinance` → nasdaq for earnings; `fmp` → fmp for both equity and earnings. |
| `--earnings-window-days <int>` | `45` | Calendar-day look-ahead for `obb.equity.calendar.earnings`. Bounded `[1, 90]`. Full 90d is reliably reachable only under `--provider fmp`. |
| `--earnings-proximity-days <int>` | `5` | Calendar-day threshold for `earnings_proximity_warning`. `>= 0`. |
| `--volume-z-estimator {robust,classical}` | `robust` | `robust` = log-MAD; `classical` = mean / stdev. Latest session excluded from the 20-session reference window. |
| `--blend-profile {trend,mean_reversion,balanced,none}` | `none` | `none` omits `blended_score_0_100` entirely. `balanced` = 0.5/0.5 of the two sub-score z's. |
| `--weight-trend-{clenow,macd,volume}` | `0.50 / 0.25 / 0.25` | Sum-of-available-weights normalization; a missing signal degrades gracefully. |
| `--weight-meanrev-{range,rsi}` | `0.60 / 0.40` | Same normalization. |

Provider mix: yfinance (equity) + nasdaq (earnings) is keyless. `--provider fmp` needs `FMP_API_KEY`. See `../_providers/SKILL.md`.

## Command

```bash
uv run scripts/entry_timing_scorer.py --tickers ASC,CMCL,FLXS,SM,TLT,LQD
uv run scripts/entry_timing_scorer.py --portfolio-file portfolio.yaml --blend-profile balanced
uv run scripts/entry_timing_scorer.py --tickers 7203.T,9432.T --context watchlist
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "entry_timing_scorer"`. `data.results[]` holds one row per input ticker, sorted by the score matching the active `--blend-profile` (`trend_score_0_100` under `none` / `trend`, `mean_reversion_score_0_100` under `mean_reversion`, `blended_score_0_100` under `balanced`); `null` scores sink. `rank` is a 1-based dense rank by the same key; `ok: false` rows and null-score rows get `rank: null`.

`data` namespace: `provider`, `tickers`, `weights`, `days_to_next_earnings_unit: "calendar_days"`, `earnings_window_days`, `earnings_proximity_days_threshold`, `missing_tickers`, `analytical_caveats` (three-string tuple — see Interpretation), optional `provider_diagnostics[]` when a provider stage fails.

Per-ticker `ok: true` row fields (abbreviated): `{symbol, provider, ok, context, rank, trend_score_0_100, mean_reversion_score_0_100, signals{clenow_126, range_pct_52w, rsi_14, macd_histogram, volume_z_20d, ma200_distance, last_price, year_high, year_low, ma_200d, ma_50d, latest_volume}, z_scores{<five signal z's + trend_z + mean_reversion_z>}, basket_size, basket_size_sufficient, next_earnings_date, days_to_next_earnings, earnings_proximity_warning, volume_avg_window, volume_z_estimator, volume_reference{volume_average{window,value}, volume_average_10d{window,value}}, data_quality_flags[], interpretation{...}}`. `blended_score_0_100` + `blend_profile` appear only when `--blend-profile` is not `none`. `ok: false` rows omit the score + z-score blocks and carry `{symbol, provider, context, error, error_type, error_category}`.

Live sample (truncated from `uv run scripts/entry_timing_scorer.py --tickers ASC,CMCL,FLXS,SM,TLT,LQD`):

```json
{
  "tool": "entry_timing_scorer",
  "data": {
    "provider": "yfinance",
    "earnings_window_days": 45,
    "earnings_proximity_days_threshold": 5,
    "days_to_next_earnings_unit": "calendar_days",
    "analytical_caveats": [
      "scores_are_basket_internal_ranks_not_absolute_strength",
      "trend_and_mean_reversion_are_separate_axes",
      "earnings_proximity_is_flag_not_score_component"
    ],
    "results": [
      {"symbol": "SM", "ok": true, "context": "unknown", "rank": 1,
       "trend_score_0_100": 67.36, "mean_reversion_score_0_100": 34.41,
       "signals": {"clenow_126": 1.518, "range_pct_52w": 0.872, "rsi_14": 62.25,
                   "macd_histogram": 0.087, "volume_z_20d": 0.296, "last_price": 31.23},
       "next_earnings_date": "2026-05-06", "days_to_next_earnings": 6,
       "earnings_proximity_warning": false,
       "volume_avg_window": "20d_real", "volume_z_estimator": "robust",
       "data_quality_flags": []}
    ]
  }
}
```

## Interpretation

`data.analytical_caveats` travels with every response; consumers must read it before acting on scores. Verbatim strings:

- `scores_are_basket_internal_ranks_not_absolute_strength` — with n=5–10 the cross-sectional z collapses to a monotone transform of within-basket rank. "70 points" is rank-relative, not absolute strength.
- `trend_and_mean_reversion_are_separate_axes` — a name can rank high on both. Averaging into one number hides the split; use the two sub-scores directly.
- `earnings_proximity_is_flag_not_score_component` — `earnings_proximity_warning` is a standalone gate, never a component of `trend_score_0_100` / `mean_reversion_score_0_100` / `blended_score_0_100`.

### Reading by context

`interpretation.reading_for_context` on every row tells the agent how to read the two sub-scores for that ticker. Verbatim strings:

- `entry_candidate_if_high_scores` — context `watchlist`. High trend ⇒ momentum entry; high mean-reversion ⇒ oversold entry.
- `hold_or_add_if_high_trend,reconsider_if_high_mean_reversion` — context `holding`. High trend supports holding / adding; high mean-reversion suggests reconsidering (not an automatic exit — the analyst layer owns that call).
- `ambiguous_without_context` — context `unknown`. No context-tailored reading available; interpret on score magnitude alone.

### Estimator choice

`--volume-z-estimator robust` (default) uses log-MAD, which dampens single-outlier-driven spikes that the classical mean/stdev formulation inflates. Expect up to a ~0.9-z divergence on illiquid names; both estimators are echoed per row as `volume_z_estimator`.

## Scope boundaries

- No portfolio-trigger matching. `--portfolio-file` reads only `positions[].ticker` / `watchlist[].ticker`; `exit_rules`, `triggers`, `targets`, and every other field are ignored.
- No macro-quadrant blending. The tool is ticker-level; macro integration stays in the analyst layer.
- No notifications, no cache files, no state between invocations. Stdout JSON + stderr only.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Earnings-calendar call fails → `data.provider_diagnostics[{provider, stage: "earnings_calendar", error, error_category}]`; every per-ticker row still emits with `next_earnings_date: null`. No in-process retry (nasdaq's 403 poisons the process).
- Whole basket < 3 tickers resolved → top-level warning `{symbol: null, error: "insufficient basket size for cross-sectional z-score", error_category: "validation"}`; raw per-ticker signals still emit, all scores `null`.
- Per-signal basket < 3 → that signal's z collapses to `null`; affected rows carry `"basket_too_small_for_z(<signal>)"` in `data_quality_flags[]` using the original signal name (e.g. `range_pct_52w`, `rsi_14`).
- `--provider fmp` + no `FMP_API_KEY` → every row fails `error_category: credential`; exit 2 via `aggregate_emit`.

## References

- `scripts/entry_timing_scorer.py`
- `README.md` § 1-1 (entry-timing scorer feature row).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
- `../momentum/SKILL.md`, `../sector_score/SKILL.md`, `../calendars/SKILL.md` (underlying primitives).
