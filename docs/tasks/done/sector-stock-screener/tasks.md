# Implementation Plan

## Tasks

- [x] 1. Bootstrap the wrapper module with shared constants and structural guards
- [x] 1.1 Create the wrapper skeleton and fixed module-scope constants
  - Add `scripts/sector_stock_screener.py` with the `from __future__ import annotations` header, `apply_to_openbb()` module-import call, and the single-provider literal pinning every downstream OpenBB call
  - Declare the FMP metric-alias map (`ev_to_ebitda → enterprise_to_ebitda`, `return_on_equity → roe`, `free_cash_flow_yield → fcf_yield`), the eleven-entry SPDR-to-GICS sector map, the non-US exchange-suffix regex, the analyst-coverage window (90 days) and threshold (5), the min-basket size (3), default top-sectors (3), default top-stocks-per-sector (20), and price-target limit (200)
  - Declare the four-entry `SUBSCORE_SIGNAL_KEYS` mapping so no analyst-revision-momentum key is structurally reachable, the six-entry factor-to-normalization-scope map, the fixed sub-score internal-weights map, the six-entry base analytical-caveats tuple, and the closed eleven-member data-quality-flag catalog as a frozenset
  - _Requirements: 2.1, 5.2, 5.7, 5.4, 6.4, 7.1, 7.2, 7.4, 10.6, 10.7, 11.5_

- [x] 1.2 Implement the closed-catalog quality-flag appender
  - Provide the single mutation path that appends to per-row `data_quality_flags[]` and raises on strings absent from the catalog so unknown flags are caught at development time
  - Forbid appending `non_us_tickers_filtered_from_pool` here (that caveat belongs on the `data` namespace only)
  - _Requirements: 10.7_

- [x] 2. Build the CLI input resolver and fail-fast credential gate
- [x] 2.1 Parse argv into a validated configuration dataclass
  - Define argparse with a required mutually-exclusive group for `--universe` and `--tickers`; reject `--universe jp-sector` with a validation-category error before any OpenBB call
  - Add bounded integer flags `--top-sectors [1,11]` (default 3) and `--top-stocks-per-sector [1,100]` (default 20); preserve input order for `--tickers` and deduplicate while keeping first occurrence
  - Expose the six sector-score weight flags with defaults matching `sector_score.py`, and the four top-level sub-score weight flags (`--weight-sub-momentum`, `--weight-sub-value`, `--weight-sub-quality`, `--weight-sub-forward`) each defaulting to 0.25; do not define any `--provider` flag
  - Emit validation-category exits with non-zero code and no `data` block when inputs are empty, mutex-violated, or out of bounds
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 3.2, 3.5, 4.2, 7.6_

- [x] 2.2 Gate execution on the FMP credential at startup
  - After configuration is built, read the FMP key from the environment; if unset or empty, emit a credential-category error envelope and exit 2 before any OpenBB call is issued
  - State that an FMP Starter+ tier is required in the error message
  - _Requirements: 2.1, 2.2_

- [x] 3. Implement the FMP-native sector-rank layer reusing `sector_score` pure helpers
- [x] 3.1 Fetch sector-ETF performance from FMP and compute the same multi-period returns as `sector_score.py`
  - Issue one batched historical call across all input ETFs with a lookback sufficient for the 3m/6m/12m return windows and the one-month volatility; split the stacked frame by `symbol`
  - Compute `one_month`, `three_month`, `six_month`, `one_year`, and `volatility_month` per ETF in-wrapper so the downstream composite has the same shape `sector_score.build_scores` expects
  - Route every call through the safe-call wrapper; record stage-level diagnostics when any ETF's history fetch fails
  - _Requirements: 2.1, 3.1_

- [x] 3.2 Compute both Clenow windows per ETF from one historical fetch
  - For each ETF issue one historical fetch with a lookback covering both the 90-day and 180-day Clenow windows
  - Run the Clenow reduction twice against the same fetched bars (period=90, period=180); coerce the returned factor to a numeric value and record a stage-level diagnostic on failure
  - _Requirements: 2.1, 3.1_

- [x] 3.3 Run the composite via imported `sector_score` helpers and select the top sectors
  - Feed FMP-native performance and Clenow inputs through the imported `build_scores` with caller-supplied sector weights so the composite formula is single-sourced
  - Reuse the imported `_classify_ticker_failure` unchanged for sector-axis failure classification; do not substitute the per-stock classifier
  - Filter to successful, non-null-rank rows and take the top N; when fewer sectors rank than requested, proceed with the available subset and append a `top_sectors_shortfall: requested=<N>, resolved=<M>` note to the emitted `data.notes`
  - Emit the full ranked set under `data.sector_ranks[]` with `{ticker, rank, composite_score_0_100, composite_z}`
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 15.3_

- [x] 4. Build the stock pool from top-sector holdings
- [x] 4.1 Fetch each selected sector ETF's holdings once
  - Call `etf.holdings` exactly once per selected ETF under the safe-call wrapper; capture `{symbol, name, weight, shares, value, updated}` rows with `updated` parsed as a date
  - On a per-sector failure emit `{provider, stage: "etf_holdings", symbol: <etf_ticker>, error, error_category}` into `data.provider_diagnostics` and continue with the remaining selected sectors
  - _Requirements: 2.1, 4.1, 4.5_

- [x] 4.2 Filter non-US listings, take the top-M slice per ETF, deduplicate into `sector_origins[]`
  - Drop any constituent whose symbol matches the exchange-suffix regex; accumulate the dropped rows into `data.non_us_tickers_filtered[]` as `{symbol, etf_ticker}` and append the `non_us_tickers_filtered_from_pool` caveat to `data.analytical_caveats` only when the filter removed at least one row
  - After filtering, take the top-M constituents per ETF by ETF weight descending for universe definition only (ETF weight never enters the per-stock ranking)
  - Deduplicate across ETFs on symbol, retain the first-seen row, append every subsequent ETF origin to the per-stock `sector_origins[]` list of `{etf_ticker, weight_in_etf, updated}`, and append `stock_appears_in_multiple_top_sectors` to the per-row quality-flag list whenever at least two origins are present
  - _Requirements: 4.3, 4.4, 4.8, 10.6, 10.7_

- [x] 4.3 Compute the holdings-freshness signal and carry the max-age to the envelope
  - Take the age in days of the oldest non-null `updated` across every fetched holdings row; emit it as `data.etf_holdings_updated_max_age_days`, or null when every `updated` is null
  - _Requirements: 4.6_

- [x] 4.4 Tag each pool entry with its GICS sector via the first ETF origin
  - Look up the first-seen ETF ticker in the SPDR-to-GICS map and tag the pool entry; use null when the originating ETF is not in the map (theme or factor ETFs) so the sector-neutral scorer's basket-wide fallback applies row-wise
  - _Requirements: 5.7_

- [x] 5. Execute the per-stock fetch layer with batched calls and a single symbol-indexing entry point
- [x] 5.1 Provide the single batched-row-to-symbol indexing helper
  - Expose the only supported path for mapping batched responses to symbols; support both single-row-per-symbol endpoints (yielding `{symbol: row}`) and multi-row-per-symbol endpoints (yielding `{symbol: list[row]}`) with a selector flag
  - Drop rows where `symbol` is missing defensively and normalize symbol casing to match the pool index
  - Forbid any positional batched lookup elsewhere in the module (callers use only this helper)
  - _Requirements: 5.5_

- [x] 5.2 Issue the four batched per-stock calls exactly once each
  - Issue one batched call each for quote, fundamental metrics, analyst consensus, and analyst price-target revisions (with limit=200), covering the full resolved pool in one request each and indexed by symbol through the helper from 5.1
  - From quote extract `last_price`, `year_high`, `year_low`, the FMP-native `ma200` and `ma50` fields (emitted under logical names `ma_200d` and `ma_50d`), and `prev_close`
  - From fundamental metrics extract `market_cap`, plus the three aliased fields translated to `enterprise_to_ebitda`, `roe`, and `fcf_yield`; never emit `pe_ratio`, `gross_margin`, or `recommendation_mean`
  - From consensus extract `target_consensus` and `target_median`; treat `number_of_analysts` as absent on this endpoint
  - Route every call through the safe-call wrapper so stdout-borne provider warnings are absorbed and axis-level failures become structured records
  - _Requirements: 2.1, 5.1, 5.2, 5.4, 14.1, 14.2, 14.3_

- [x] 5.3 Compute Clenow momentum per symbol from a per-symbol historical fetch
  - For every pool symbol issue one historical fetch with a 180-day lookback and one Clenow reduction with period=90 against the fetched bars; coerce the returned factor to a numeric `clenow_90` and emit null when the factor is missing or non-numeric
  - Treat the per-symbol loop as the only supported shape because the Clenow reducer rejects stacked-index batched historical frames
  - _Requirements: 2.1, 5.3, 14.4_

- [x] 5.4 Derive analyst count from the 90-day distinct-firm revision log
  - For each symbol filter the price-target rows to the last 90 calendar days, exclude rows where the firm field is null, empty, or whitespace-only, and count the distinct firm entries to yield `number_of_analysts`
  - Return null when the price-target fetch for the symbol failed
  - _Requirements: 5.4, 6.4, 6.5_

- [x] 5.5 Resolve the per-ticker last price via the two-rung fallback chain
  - Prefer `last_price` from quote; on null fall back to `prev_close` and append `last_price_from_prev_close` to the per-row flag list
  - When both rungs are null emit null for the resolved last price and append `last_price_unavailable`
  - _Requirements: 5.6_

- [x] 6. Compute derived indicators, sector-neutral and basket-wide z-scores, sub-scores, and the composite
- [x] 6.1 (P) Compute the four derived indicators
  - Compute the 52-week range position when last price and both 52-week bounds are present and the denominator is non-zero
  - Compute the 200-day distance when last price and the 200-day moving average are both present and the denominator is non-zero
  - Invert EV/EBITDA into a yield when it is strictly positive; otherwise emit null and append `ev_ebitda_non_positive` to the per-row flag list
  - Compute analyst upside only when target consensus, last price, and a non-null analyst count of at least 5 are all present; otherwise emit null and append `analyst_coverage_too_thin` when the coverage gate fails
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 6.2 (P) Implement the minimum-basket z-score helper and its sector-neutral variant
  - Implement the cross-sectional z-score with a minimum sample size of three; emit null on every row when the basket is below the minimum, and zero for every row when dispersion is zero; propagate nulls through the input
  - Implement the sector-neutral variant that groups by GICS sector tag and applies the same z-score within each group; rows in a group smaller than three (or rows with null sector tag) fall back to basket-wide z across the full pool and receive `sector_group_too_small_for_neutral_z(<factor>)` on the row's flag list
  - For every basket-wide factor with fewer than three non-null values across the whole basket emit null z on every affected row and append `basket_too_small_for_z(<factor>)` so sub-scores still degrade gracefully
  - _Requirements: 4.7, 7.1, 7.2, 7.7, 7.8_

- [x] 6.3 Apply sector-neutral scope to value and quality factors and basket-wide scope to momentum and forward factors
  - Drive scope selection strictly from the factor-to-scope map so value and quality (EV/EBITDA yield, ROE) always attempt sector-neutral first and momentum, range position, trend, and forward upside stay cross-sectional
  - For each sector-neutral factor on each row populate exactly one of the `_sector_neutral` or `_basket` keys with the other set to null, and record the fallback with the matching quality flag so the normalization scope is auditable both via the populated key and via the flag
  - _Requirements: 7.1, 7.2, 7.7, 7.9_

- [x] 6.4 Compose sub-scores, compute the composite, and produce the 0–100 transforms
  - Compute each sub-score (momentum, value, quality, forward) as the sum-of-available-weights-normalized sum of its z-inputs using the fixed internal weights so missing signals degrade the denominator instead of the sum
  - Compute the composite z as the sum-of-available-weights sum of the four sub-score z's using the caller-tunable top-level weights
  - Apply the `clip(50 + z*25, 0, 100)` transform to every sub-score z and to the composite z to emit `momentum_score_0_100`, `value_score_0_100`, `quality_score_0_100`, `forward_score_0_100`, and `composite_score_0_100`
  - Echo both the active top-level weights and the fixed internal weights under `data.weights`
  - _Requirements: 7.3, 7.4, 7.5, 7.6_

- [x] 6.5 Emit per-row basket accounting and the fixed-shape `z_scores` block
  - Populate `basket_size` (count of rows with at least one populated sub-score signal), `sector_group_size` (count of rows sharing the row's GICS sector tag), and `basket_size_sufficient` (true iff basket size is at least three)
  - Emit a single fixed-key `z_scores` block per row: `_sector_neutral` and `_basket` variants for the two sector-neutral factors (mutually exclusive population per row), `_basket` keys for the four basket-wide factors, and `_basket` keys for the four sub-score z's and the composite z
  - _Requirements: 7.9, 7.10_

- [x] 7. Classify per-stock failures across the five fetch axes with fatal-category promotion
  - Inspect the per-axis records for quote, fundamental metrics, historical, consensus, and price-target for each symbol; return no failure when any axis produced usable data, and a `{error, error_type, error_category}` record when every axis failed
  - Promote the fatal category to `credential` or `plan_insufficient` only when every failed axis shares that same category so the emit-layer exit-code gate can lift it to the envelope root; for mixed or other-category collapses carry the first-seen category
  - Keep this classifier distinct from the sector-axis classifier imported from `sector_score`; the sector-rank step continues to use that imported function
  - _Requirements: 15.1, 15.2, 15.3_

- [x] 8. Assemble the envelope, sort and rank rows, and delegate stdout emission
- [x] 8.1 Build the per-ticker rows and the `data` namespace
  - For `ok: true` rows populate at minimum `symbol`, `ok`, `rank`, `gics_sector`, `sector_origins`, the five `*_score_0_100` fields, the seventeen-field `signals` block (no `pe_ratio`, `gross_margin`, or `recommendation_mean`), the fixed-shape `z_scores` block, basket accounting fields, `data_quality_flags`, and a fixed five-key `interpretation` block stating basket-internal-rank meaning, high-is-better polarity, the analyst-count gate, the sector-neutral factor list, and the basket-wide factor list
  - For `ok: false` rows omit score and z-score blocks while keeping symbol, GICS sector, sector origins, and the error trio
  - Under `data` emit the full sibling set: results, universe, tickers (the ETF universe echoed back), weights (sector, top-level sub-score, and fixed internal), sector ranks, top-sectors-requested and top-stocks-per-sector-requested echoes, holdings max-age, missing tickers, optional non-US-filtered list (only when non-empty), optional provider diagnostics (only when at least one stage failed), analytical caveats, and notes; do not emit any `provider` field on `data` or on per-row rows because the wrapper is single-provider
  - Never emit a `buy_signal` or `recommendation` field anywhere in the output
  - _Requirements: 3.4, 4.5, 4.6, 4.8, 8.3, 9.2, 9.3, 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4, 11.5_

- [x] 8.2 Compose the analytical caveats and apply sort-and-rank
  - Build the caveats list from the six base entries and append the non-US-filter caveat only when the pool-build stage actually dropped rows
  - Stable-sort `data.results[]` by `composite_score_0_100` descending with null scores sinking to the bottom, then assign 1-indexed `rank` from sorted position; emit every resolved ticker with no truncation
  - Mirror the sparse-pool warning (when the deduplicated pool is smaller than three) into the top-level warnings channel as `{symbol: null, error: "insufficient stock pool size for cross-sectional z-score", error_category: "validation"}` while still emitting per-row rows with null z-scores
  - _Requirements: 4.7, 8.1, 8.2, 8.3, 10.6_

- [x] 8.3 Delegate stdout emission through `aggregate_emit`
  - Emit the envelope via `aggregate_emit(tool="sector_stock_screener", ...)` so the root keys and exit-code gate behavior match the repo-wide JSON contract; rely on it for NaN/Inf sanitization and for the all-fatal-category promotion to exit 2 when every stock row collapses to credential or plan-insufficient
  - Mirror every per-row failure into the top-level warnings channel automatically via `aggregate_emit`
  - _Requirements: 2.3, 9.1, 9.2, 9.3_

- [x] 9. (P) Ship the agent-facing SKILL.md and register the wrapper in INDEX.md and README §1-1
  - Author `skills/sector-stock-screener/SKILL.md` in English within 30–80 lines referencing the shared `_envelope`, `_errors`, and `_providers` skills instead of duplicating their prose
  - Document every CLI flag with its default (universe, tickers, top-sectors, top-stocks-per-sector, the six sector-score weights, the four top-level sub-score weights); explicitly do not document any provider flag
  - Include one short real-run example invocation and one truncated output sample taken from an actual run, not fixtures
  - State the scope boundaries (no buy signals, no backtest, no portfolio optimization, no JP-sector coverage in MVP, no analyst-revision-momentum in MVP), include the Interpretation subsection echoing the three caveats (basket-internal rank vs absolute strength, sector-neutral vs basket-wide scope, the analyst-count-≥5 gate), and include the Provider subsection stating that the wrapper pins FMP Starter+ and fails fast on missing or insufficient credential
  - Add the row to `skills/INDEX.md` under Composite Skills and add the matching row with Verified pointer to `README.md` §1-1 in the same commit
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

- [x] 10. Lock in the contract through the integration test and verification gate
  - Add `tests/integration/test_sector_stock_screener.py` that invokes the wrapper as a subprocess with `--universe sector-spdr --top-sectors 2 --top-stocks-per-sector 5`, asserts envelope shape via the shared sanity helpers, and auto-skips when the FMP credential is absent
  - Assert that `data.results[]` is sorted by composite score descending with null scores sinking; assert that `data.analytical_caveats` contains the six required base strings (including the 90-day-distinct-firm derivation disclosure) and that no per-ticker row carries a `buy_signal` or `recommendation` field
  - Assert that every `ok: true` row carries a GICS sector and at least one sector origin, that the four sub-score-0-100 fields are present (forward may be null on thin-coverage rows), and that at least one `z_scores` key ends in `_sector_neutral` while at least one ends in `_basket`
  - Assert that `data.top_sectors_requested` and `data.top_stocks_per_sector_requested` match CLI inputs and that the holdings max-age is a non-negative integer
  - Add a source-text check that the wrapper source contains no positional batched lookup of the form `results[<integer>]`
  - Register the wrapper in the existing JSON-contract discovery list so the auto-discovery suite covers it; keep the run's FMP call count inside the default-run budget (~138 calls) to stay within the Starter 300/min rate limit
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 14.5_

- [x] 10.1 Wire the pipeline runner so `main` actually executes a sector analysis end-to-end
  - Add a `run_pipeline(config)` (modeled on `scripts/entry_timing_scorer.py::run_pipeline`) that chains: `run_sector_rank` → `select_top_sectors` → `fetch_etf_holdings` → `build_pool` → the four batched per-stock fetches + per-symbol Clenow loop → `compute_derived_indicators` + `resolve_last_price` + `derive_number_of_analysts` → `compute_cross_sectional` → `_classify_stock_failure` → row builders → `assemble_and_emit`, and have `main` return its exit code
  - Until this task lands, `uv run scripts/sector_stock_screener.py --universe sector-spdr` exits 0 with no envelope emitted — that is the observable gap the integration test (Task 10) must catch and must not go green until this runner is in place
  - _Requirements: 2.1, 2.3, 3.1, 3.3, 3.4, 4.1, 4.5, 4.6, 4.7, 4.8, 5.x, 6.x, 7.x, 8.1, 8.2, 8.3, 9.1, 9.2, 9.3, 15.1, 15.2_

- [x] 10.2 Verify concerns surfaced during Task 8 review against real FMP data before declaring the feature shippable
  - **[RESOLVED 2026-05-01] `BRK.B` false-positive on the non-US exchange-suffix filter (Req 4.8)**: replaced the catch-all `r"\.[A-Z]{1,3}$"` with an explicit closed-set non-US suffix allowlist (`_NON_US_EXCHANGE_SUFFIXES`: `.HK` / `.T` / `.L` / `.PA` / `.DE` / `.SW` / `.TO` / `.AX` / `.NS` / `.SA` / …). US-listed class-share tickers (`BRK.A`, `BRK.B`, `BF.B`, `GEF.B`, `LEN.B`, `RDS.A`) pass through. Unit tests pin both the class-share invariant and the non-US allowlist; Req 4.8 text updated to describe the allowlist approach.
  - **[RESOLVED 2026-05-01] `volatility_month` shape vs `sector_score.py` expectation**: verified parity — `sector_score.py:177-178` uses pandas `Series.std` default (ddof=1) over `tail(21)`, no annualization; `_compute_perf_record_from_rows` uses `statistics.stdev` (ddof=1) over `monthly[-21:]`, no annualization. `risk_adj = one_month / volatility_month` in `build_scores` consumes identical units. Logged as research §G7; inline parity comment added to `_compute_perf_record_from_rows` so future edits cannot reintroduce divergence unknowingly.
  - **[RESOLVED 2026-05-01] `basket_size` vs `sector_group_size` coverage asymmetry**: live integration run of `--universe sector-spdr --top-sectors 2 --top-stocks-per-sector 5` passed the `ok:true` row-shape assertion (`gics_sector` populated, `sector_origins[] >= 1`, four `*_score_0_100` fields present). Asymmetry between the two counts is documented in design.md §Cross-Sectional Scorer and did not surprise consumers in the live payload.
  - **[RESOLVED 2026-05-01] End-to-end parity with expected FMP call budget (Req 14.5)**: live integration run of `--top-sectors 2 --top-stocks-per-sector 5` completed inside the 600s timeout without any rate-limit diagnostics under `data.provider_diagnostics`. The default `--top-sectors 3 --top-stocks-per-sector 20` budget (~138 HTTP calls) remains within the FMP Starter 300/min envelope by construction; a dedicated call-count assertion is deferred to an obb-level counter instrumentation task (non-blocking for MVP).
  - **[RESOLVED 2026-05-01] `etf.holdings.updated` realism under FMP**: live integration test `test_envelope_shape_and_data_namespace_echoes` asserts `etf_holdings_updated_max_age_days` is `None` or a non-negative integer, and the test passed against live FMP. R5's datetime parse assumption holds.
  - _Requirements: 3.1, 4.6, 4.8, 7.10, 14.5_
