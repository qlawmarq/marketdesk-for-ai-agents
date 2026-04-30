# Implementation Plan

## Tasks

- [x] 1. CLI scaffolding, input resolution, and provider routing
- [x] 1.1 Bootstrap the wrapper module and shared-envelope wiring
  - Create `scripts/entry_timing_scorer.py` with `from __future__ import annotations`, call `apply_to_openbb()` at import, and bind `_common.{safe_call, aggregate_emit, sanitize_for_json, emit, ErrorCategory}` for downstream use
  - Declare module-scope constants `HISTORICAL_LOOKBACK_DAYS = 210`, `DEFAULT_EARNINGS_WINDOW_DAYS = 45`, `DEFAULT_EARNINGS_PROXIMITY_DAYS = 5`, and `ANALYTICAL_CAVEATS` as a three-string tuple matching Req 9.9 verbatim
  - Declare `SCORER_SIGNAL_KEYS = ("clenow_126", "macd_histogram", "volume_z_20d", "inv_range_pct_52w", "oversold_rsi_14")` and `_SIGNAL_FLAG_NAME` mapping the two transformed keys back to their original-name flag strings (`inv_range_pct_52w → range_pct_52w`, `oversold_rsi_14 → rsi_14`, identity for the others) so the `basket_too_small_for_z(<signal>)` flag strings use names agents recognise from `signals.*`
  - Add `pyyaml>=6.0` as a direct dependency in `pyproject.toml` and keep `uv sync` passing; verify the file name does not shadow any stdlib module (flat-wrapper convention)
  - Forbid all side effects beyond stdout + stderr: no file writes, no network destinations, no cache / database / log files, no portfolio-trigger matching, no macro-quadrant fetching, no notifications
  - _Requirements: 8.1, 8.7, 9.9, 10.2, 10.3, 10.4, 10.5, 10.6_

- [x] 1.2 Wire argparse CLI with validation-first failure behavior
  - Add `--tickers` and `--portfolio-file` inside a required mutually-exclusive group so omitting both or supplying both fails as `error_category: "validation"` before any OpenBB call
  - Add `--context` restricted to `{watchlist, holding, unknown}` (default `unknown`); reject `--context` under `--portfolio-file` because the context comes from the YAML structure
  - Add `--provider` with closed choice set `{yfinance, fmp}` defaulting to `yfinance`
  - Add `--earnings-window-days` (`int`, `[1, 90]`, default 45), `--earnings-proximity-days` (`int`, `>= 0`, default 5), `--volume-z-estimator` (`{robust, classical}`, default `robust`), `--blend-profile` (`{trend, mean_reversion, balanced, none}`, default `none`)
  - Add the five sub-score weight flags (`--weight-trend-clenow`, `--weight-trend-macd`, `--weight-trend-volume`, `--weight-meanrev-range`, `--weight-meanrev-rsi`) with defaults `0.50 / 0.25 / 0.25 / 0.60 / 0.40`
  - Surface every bound / type violation as `error_category: "validation"` with a non-zero exit before issuing any OpenBB call; do not retry failed calendar fetches within the same invocation
  - _Requirements: 1.3, 1.4, 1.8, 2.3, 3.7, 5.4, 6.3, 6.5, 7.2, 7.6_

- [x] 1.3 Implement ticker resolver and context tagger
  - Parse `--tickers` CSV preserving input order and deduplicating while preserving first-seen order
  - Parse `--portfolio-file` through `yaml.safe_load` (never `yaml.load` / `yaml.unsafe_load`), extract `positions[].ticker` then `watchlist[].ticker` in YAML declaration order, and reject documents that do not parse to a mapping with `error_category: "validation"`
  - Read no field from `portfolio.yaml` other than `positions[].ticker` / `watchlist[].ticker`; `exit_rules`, `triggers`, `targets`, and every other key must be silently ignored
  - Tag tickers from `positions[]` as `"holding"`, tickers from `watchlist[]` as `"watchlist"`, and under `--tickers` apply the `--context` flag uniformly to every ticker
  - Resolve duplicates that appear in both `positions[]` and `watchlist[]` to `"holding"` and record `"context_duplicate_positions_and_watchlist"` for the affected ticker
  - Forward `.T`-suffixed symbols unchanged to downstream fetchers so JP equities work without a provider override; reject an empty resolved list with `error_category: "validation"` before any OpenBB call
  - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7, 1.9, 10.1, 10.2_

- [x] 1.4 Provider router
  - Map `--provider yfinance` to equity `yfinance` + calendar `nasdaq` so the default path stays keyless
  - Map `--provider fmp` to equity `fmp` + calendar `fmp` so FMP-credentialled callers reach the clean +90d earnings window without the nasdaq 403 ceiling
  - Expose the resolution as a pure function returning the `(equity_provider, calendar_provider)` tuple and never any other pairing
  - _Requirements: 2.1, 2.2, 2.3_

- [x] 2. (P) Earnings calendar single-shot fetch and index
  - Issue `obb.equity.calendar.earnings(start=TODAY, end=TODAY + window_days, provider=<calendar_provider>)` exactly once per invocation through `safe_call` with no in-process retry
  - Defensively skip rows that are not a dict or are missing `symbol` / `report_date`, coerce `report_date` via `date.fromisoformat` inside `safe_call` protection, and filter to the input-ticker set before indexing
  - Pick the earliest `report_date >= TODAY` per ticker for the index; tickers with no surviving row map to `None`
  - On calendar failure return an empty index plus a single `{provider, stage: "earnings_calendar", error, error_category}` diagnostic so the envelope layer can route it into `data.provider_diagnostics` while every per-ticker row still emits with `next_earnings_date: null`
  - Keep the no-retry invariant universal across providers — nasdaq's 403 poisons the Python process, and FMP is held to the same rule for provider-agnostic behavior; transient failures surface cleanly via `error_category: "transient"` for the operator to retry at the CLI level
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 13.3, 13.4_

- [x] 3. Per-ticker data acquisition
- [x] 3.1 Per-ticker fetcher bundle with five safe_call sites
  - For each ticker, issue `obb.equity.price.quote`, `obb.equity.price.historical(start=TODAY - 210d)`, `obb.technical.clenow(data=history.results, period=126)`, `obb.technical.rsi(data=history.results, length=14)`, `obb.technical.macd(data=history.results, fast=12, slow=26, signal=9)` through `safe_call` — exactly five calls per ticker
  - Reuse the single `history.results` list for all three technicals; never re-fetch `historical` to compute rsi/macd separately
  - Short-circuit the three technicals to a documented empty-history failure when `history.results` is empty so the per-ticker call count stays at most five on the known-null path
  - Collect per-stage failure records and derive `ok` from whether any primary artefact carries usable data, mirroring `sector_score._classify_ticker_failure`'s partial-success handling
  - Surface provider exceptions through `safe_call` so the failure carries `error_category` drawn from `{credential, plan_insufficient, transient, validation, other}`; do not introduce retry loops
  - Short-circuit subsequent equity calls after a fatal `credential` / `plan_insufficient` category to avoid burning budget needlessly; keep fetching on `transient` / `other` so partial data remains available
  - _Requirements: 2.4, 4.2, 4.3, 4.4, 4.5, 4.6, 4.8, 13.3, 13.4_

- [x] 3.2 Provider-aware quote field resolver
  - Build a closed-choice map keyed by `{yfinance, fmp}` that translates logical field names (`ma_200d`, `ma_50d`, `volume_average`, `volume_average_10d`) to provider-native quote keys
  - Under yfinance resolve `ma_200d` / `ma_50d` verbatim and both volume-average keys verbatim; under fmp resolve `ma_200d → ma200`, `ma_50d → ma50`, and both volume-average keys to unavailable (`None`)
  - Emit the extracted values under the logical names downstream regardless of provider so consumers see a single consistent schema
  - Raise at resolution time (rather than silently falling back) when a provider is missing from the map so adding a provider is an explicit contract change
  - _Requirements: 4.1, 5.7_

- [x] 3.3 Last-price fallback chain and technical-indicator extraction
  - Resolve `last_price` via the chain `quote.last_price → quote.prev_close → historical[-1].close`, returning the first non-null rung and the matching flag (`"last_price_from_prev_close"`, `"last_price_from_historical_close"`, or `"last_price_unavailable"`)
  - Coerce the Clenow `factor` string to numeric via the `_to_float` pattern used in `sector_score.fetch_clenow`; non-numeric or missing `factor` emits `clenow_126: null`
  - Extract RSI from the output record using case-insensitive suffix match on `"RSI"`, emit the result as `rsi_14`, and fall back to `null` when no matching column is present
  - Extract MACD histogram using case-sensitive suffix match on `"MACDh"` to avoid the `MACD` / `MACDs` / `MACDh` collision, emit the result as `macd_histogram`, and fall back to `null` when no matching column is present
  - Apply the fallback universally regardless of provider so bond ETFs stay usable under yfinance (where `last_price` is null) and the code path remains single-branch under fmp (where rung-1 succeeds)
  - _Requirements: 4.3, 4.4, 4.5, 4.7, 9.10_

- [x] 4. Derived indicators (range, MA distance, true 20-day volume z)
  - Compute `range_pct_52w = (last_price - year_low) / (year_high - year_low)` when inputs are non-null and the denominator is non-zero; fall back to `null` otherwise
  - Compute `ma200_distance = (last_price - ma_200d) / ma_200d` when inputs are non-null and `ma_200d != 0`
  - Compute `volume_z_20d` on the latest session using a 20-session reference window that excludes the latest session, requiring at least 21 historical rows; source `latest_volume` from `history_rows[-1].volume` so the emitted scalar and the z-score see the same session and the path stays provider-shape-invariant
  - Support both estimators: `robust` (default) uses `(log(latest) - median(log(ref))) / (1.4826 * MAD(log(ref)))` and `classical` uses `(latest - mean(ref)) / stdev(ref)`; echo `volume_z_estimator` on every row
  - Apply the narrowest-gate ordering for degenerate input — `volume_window_too_short` → `volume_non_positive` (robust only) → `volume_zero_dispersion` — emitting exactly one flag at a time and setting `volume_z_20d: null` when any gate fires
  - Emit `volume_avg_window: "20d_real"` on every row that reports a non-null `volume_z_20d` so reviewer R1's 20-day-vs-3-month ambiguity cannot recur downstream
  - Build the `volume_reference` sibling block with labels `{"window": "3m_rolling"}` and `{"window": "10d"}` from the resolver output; when a value is `None` (typically fmp) emit `value: null` while preserving the window label and append `"volume_reference_unavailable_on_provider"` exactly once per row even if both values are null
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

- [x] 5. Cross-sectional scoring and blend
- [x] 5.1 Basket-wide z-scores and sub-score composition
  - Iterate `SCORER_SIGNAL_KEYS` only — the last two keys use the transformed sign convention `(1 - range_pct_52w)` and `(50 - rsi_14)` so a higher value consistently means "more mean-reverting"
  - Compute per-signal cross-sectional z-scores with `min_basket=3`; when fewer than three tickers carry a non-null value the signal collapses to `null` on every row and each affected row receives `"basket_too_small_for_z(<signal>)"` translated via `_SIGNAL_FLAG_NAME`
  - Compose `trend_z` (clenow + macd + volume) and `mean_reversion_z` (range + rsi) with the sum-of-available-weights normalization pattern from `sector_score.build_scores` so a missing signal degrades gracefully and the sub-score is `null` only when every weight drops out
  - Map each sub-score z to 0–100 via `clip(50 + z * 25, 0, 100)` reusing `sector_score.to_100`'s transform and emit the result as `trend_score_0_100` / `mean_reversion_score_0_100`
  - Compute `basket_size` as the row-level count of tickers with `ok: true` and at least one non-null SCORER_SIGNAL_KEYS value; set `basket_size_sufficient = basket_size >= 3` per row so consumers can filter for statistically-usable rows without re-counting the basket
  - Structurally exclude earnings fields from every composite by using `SCORER_SIGNAL_KEYS` as the sole iteration source; the `z_scores` block must never include an earnings-derived key
  - When the whole basket has fewer than three rows, set every `trend_score_0_100` / `mean_reversion_score_0_100` / `blended_score_0_100` to `null`, queue a top-level warning `{symbol: null, error: "insufficient basket size for cross-sectional z-score", error_category: "validation"}`, and still emit raw per-ticker signals
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.7, 6.8, 6.11, 7.1_

- [x] 5.2 Blend profile and audit-friendly z_scores block
  - Under `--blend-profile balanced` compute `blended_score_0_100` from `0.5 * trend_z + 0.5 * mean_reversion_z` then apply the 0–100 transform so the blend sits on the same scale as the sub-scores
  - Under `--blend-profile trend` / `mean_reversion` emit the corresponding sub-score verbatim as `blended_score_0_100` so the caller has a single consistent field regardless of stance
  - Under `--blend-profile none` omit `blended_score_0_100` and `blend_profile` from the per-ticker row entirely (rather than emitting `null`) so consumers do not read a missing blend as a "score of zero"
  - Emit the `z_scores` block with the five transformed signal keys (`clenow_126`, `macd_histogram`, `volume_z_20d`, `inv_range_pct_52w`, `oversold_rsi_14`) plus `trend_z` and `mean_reversion_z` so operators can audit every composition step
  - _Requirements: 6.5, 6.6, 6.10_

- [x] 6. Earnings-proximity flag, interpretation builder, and quality flag catalog
- [x] 6.1 Earnings-proximity flag and raw field passthrough
  - Compute `days_to_next_earnings = (next_earnings_date - today).days` when a date is available, else `null`
  - Emit `earnings_proximity_warning: true` when `days_to_next_earnings` is not null and `<= --earnings-proximity-days`; emit `false` in every other case (including null), so the flag is always a boolean while the underlying date / day count may be null
  - Pass `next_earnings_date` through as an ISO date string and `days_to_next_earnings` as an integer on every row so consumers can apply a different threshold without re-fetching the calendar
  - Keep every earnings-derived field out of the trend, mean-reversion, and blended scores — the flag is a standalone gate
  - _Requirements: 7.1, 7.3, 7.4, 7.5_

- [x] 6.2 Interpretation builder with enforced negative invariant
  - Build the per-row `interpretation` object with exactly the five keys `{score_meaning, trend_polarity, mean_reversion_polarity, context, reading_for_context}` using the literal strings `"basket_internal_rank"`, `"high=stronger_trend"`, `"high=more_oversold"`
  - Map context to `reading_for_context` via `watchlist → "entry_candidate_if_high_scores"`, `holding → "hold_or_add_if_high_trend,reconsider_if_high_mean_reversion"`, `unknown → "ambiguous_without_context"`
  - Never construct a scalar named `interpretation_hint` anywhere in the module so Req 9.5's negative invariant holds by construction (enforced by absence, verified by integration test)
  - _Requirements: 9.4, 9.5, 11.7_

- [x] 6.3 Closed-enumeration quality flag catalog
  - Declare `DATA_QUALITY_FLAGS` as a fifteen-member `frozenset[str]` matching Req 9.10's enumeration verbatim
  - Append `"rsi_oversold_lt_20"` when `rsi_14 < 20`, `"basket_too_small_for_z"` when the row's `basket_size_sufficient` is false, and per-signal `"basket_too_small_for_z(<signal>)"` entries using the `_SIGNAL_FLAG_NAME` mapping so the flag strings carry the original signal names agents recognise
  - Validate every flag against the catalog at append time and raise at development time when an unknown string is appended, so the closed-enumeration contract holds structurally rather than by convention
  - _Requirements: 9.6, 9.7, 9.10_

- [x] 7. Envelope assembly and stdout emission
  - Build each `ok: true` per-ticker row with the minimum schema from Req 9.1 (`symbol`, `provider`, `ok`, `context`, `rank`, `trend_score_0_100`, `mean_reversion_score_0_100`, `signals`, `z_scores`, `basket_size`, `basket_size_sufficient`, `next_earnings_date`, `days_to_next_earnings`, `earnings_proximity_warning`, `volume_avg_window`, `volume_z_estimator`, `volume_reference`, `data_quality_flags`, `interpretation`); add `blended_score_0_100` and `blend_profile` only when the active profile is not `none`
  - Populate the `signals` block with the twelve named fields (`clenow_126`, `range_pct_52w`, `rsi_14`, `macd_histogram`, `volume_z_20d`, `ma200_distance`, `last_price`, `year_high`, `year_low`, `ma_200d`, `ma_50d`, `latest_volume`) so the underlying inputs are auditable from the same row
  - On `ok: false` rows omit `trend_score_0_100` / `mean_reversion_score_0_100` / `blended_score_0_100` / `z_scores` while still populating `{symbol, provider, context, error, error_type, error_category}`, and mirror each failure into top-level `warnings[]` via `aggregate_emit`
  - Assemble the `data` namespace with siblings `provider`, `tickers`, `weights`, `days_to_next_earnings_unit: "calendar_days"`, `earnings_window_days` (echoed flag value), `earnings_proximity_days_threshold` (echoed flag value), `missing_tickers`, and the `ANALYTICAL_CAVEATS` three-string tuple; include `provider_diagnostics` only when at least one stage failed
  - Sort `data.results[]` by the score for the active blend profile (`trend_score_0_100` under `none` / `trend`, `mean_reversion_score_0_100` under `mean_reversion`, `blended_score_0_100` under `balanced`) with null scores mapped to `-inf` so they sink, and a stable alphabetical-symbol secondary sort for tie-breaks
  - Assign `rank` as a 1-based dense rank by the active primary score; tie rows share a rank and `ok: false` / null-score rows receive `rank: null`
  - Surface an earnings-calendar diagnostic (if any) under `data.provider_diagnostics` and still emit every per-ticker row with `next_earnings_date: null` when the calendar fetch failed
  - Delegate stdout emission and exit-code determination to `_common.aggregate_emit` with `tool="entry_timing_scorer"`, forwarding the basket-size warning and per-row failure mirrors via `extra_warnings`; rely on `sanitize_for_json` for NaN / ±Inf → null and keep stdout a single JSON document with tracebacks on stderr
  - Exit 2 when every input ticker fails with the same fatal `credential` / `plan_insufficient` category (behavior `aggregate_emit` already provides), exit 0 on full success and on any partial-failure mix, exit 2 on any pre-call validation failure
  - _Requirements: 3.5, 3.6, 6.6, 6.9, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.1, 9.2, 9.3, 9.8, 9.9_

- [x] 8. (P) Companion SKILL.md, INDEX.md row, and README verification pointer
  - Write `skills/entry-timing-scorer/SKILL.md` in English (roughly 30–80 lines) referencing the shared `_envelope`, `_errors`, and `_providers` skills for cross-cutting policy rather than duplicating their prose
  - Document every CLI flag with default values — `--tickers`, `--portfolio-file`, `--context`, `--provider`, `--earnings-window-days`, `--earnings-proximity-days`, `--volume-z-estimator`, `--blend-profile`, and the five sub-score weight flags
  - Include one short real-run example invocation and one truncated output sample drawn from an actual `uv run` against the `ASC CMCL FLXS SM TLT LQD` basket (not from fixtures)
  - Spell out scope boundaries (no portfolio-trigger matching, no macro-quadrant blend, no notifications) so downstream callers do not assume omitted capabilities
  - Add an Interpretation section that reproduces the three `analytical_caveats` strings verbatim plus a Reading-by-context subsection that reproduces the three `reading_for_context` strings verbatim (watchlist vs holding vs unknown)
  - Append a matching row to `skills/INDEX.md` and add a verification-test pointer to `README.md` §1-1, shipping the wrapper, SKILL.md, INDEX row, and README update in the same commit so `tests/integration/test_verification_gate.py` continues to pass (ChangeBundleRule)
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 12.9_

- [x] 9. (P) Integration tests and JSON-contract wiring
  - Register the wrapper in `tests/integration/test_json_contract.py::WRAPPER_HAPPY_ARGV` so the auto-discovery envelope suite covers the new wrapper
  - Add `tests/integration/test_entry_timing_scorer.py` that invokes `uv run scripts/entry_timing_scorer.py --tickers <sample>` as a subprocess via `run_wrapper_or_xfail` on the keyless default provider and asserts envelope shape via the `tests/integration/_sanity.py` helpers
  - Assert sort-order parity with `--blend-profile`: `trend_score_0_100` under `none`, `mean_reversion_score_0_100` under `mean_reversion`, and `blended_score_0_100` under `balanced`, with null scores always sinking to the bottom
  - Assert `volume_avg_window == "20d_real"` on at least one `ok: true` row, that every `ok: true` row carries both `trend_score_0_100` and `mean_reversion_score_0_100`, and that they may legitimately be high on the same ticker (separate axes, not complementary halves)
  - Assert `data.days_to_next_earnings_unit == "calendar_days"`, that `data.earnings_proximity_days_threshold` echoes the CLI value, that `data.analytical_caveats` contains all three required strings, and that no per-ticker row carries a field named `interpretation_hint`
  - Run a two-invocation estimator comparison against the same basket with `--volume-z-estimator robust` and `--volume-z-estimator classical`, asserting the per-row echo of `volume_z_estimator` and that the two runs can legitimately produce different `volume_z_20d` values on at least one row
  - Add a provider-parametrized slice that runs under `--provider yfinance` and `--provider fmp`, auto-skipped when `FMP_API_KEY` is absent; for both providers assert `signals.ma_200d` / `signals.ma_50d` are non-null on at least one `ok: true` equity row (verifying the provider-aware quote resolver); under fmp assert every `ok: true` row carries `"volume_reference_unavailable_on_provider"` and `volume_reference.*.value` is `null` while preserving the `window` labels
  - Under both providers (fmp leg auto-skipped when credentials absent), assert `volume_z_20d` is non-null on at least one `ok: true` equity row so the locally-computed 20-day z is confirmed provider-independent
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 13.1, 13.2_
