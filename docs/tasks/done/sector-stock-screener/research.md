# Research & Design Decisions — sector-stock-screener

---
**Purpose**: Close the open research items carried over from `gap-analysis.md` (R1, R2, R5, R6) with live probes and written trade-offs, and lock the outstanding FMP field-name / endpoint-shape questions uncovered while drafting the design surface. The output is the binding input for `/sdd-spec-design sector-stock-screener`.

**Discovery mode**: Light (per `docs/settings/rules/design-discovery-light.md`). This is an extension of an existing pattern (`scripts/sector_score.py` + `scripts/entry_timing_scorer.py`); every OpenBB primitive is already live in the repo. Live probes were executed against FMP Starter+ on 2026-05-01 to lock endpoint shapes rather than inferring from documentation.
---

## Summary

- **Feature**: `sector-stock-screener`
- **Discovery Scope**: Extension (thin-wrapper pattern precedented by `sector_score.py` and `entry_timing_scorer.py`; FMP single-provider pin removes ~150 LoC of provider-dispatch scaffolding).
- **Key Findings**:
  - **R1 resolved — batched consensus works on FMP.** `obb.equity.estimates.consensus(symbol="AAPL,MSFT,NVDA", provider="fmp")` returns one row per symbol in a single round-trip. The default 60-stock basket collapses to one call; Req 14.1's preferred branch is viable and rate-limit headroom on FMP Starter 300/min is ample.
  - **Consensus endpoint does NOT expose `number_of_analysts` or `recommendation_mean` under FMP.** The live payload is `{symbol, name, target_high, target_low, target_consensus, target_median}` only. Requirement 5.4 (extract `number_of_analysts`, `recommendation_mean`) and Requirement 6.4 / 6.5 (gate `target_upside` on `number_of_analysts ≥ 5`) therefore need a derived coverage signal. Two viable derivations exist; design will pick one — **both alternatives are documented under "Decision: analyst-coverage signal"** below. This is a genuinely new research outcome and it materially affects Req 5.4 / 6.4 / 6.5 wording, so the research phase flags it as a **requirements-reopening finding** (research does not silently pin a value).
  - **R5 resolved — `etf.holdings.updated` is a `datetime` object.** Not an ISO string, not an epoch. The parse in `etf_holdings_updated_max_age_days` is `(date.today() - row.updated.date()).days`; no `datetime.fromisoformat` call is needed.
  - **R2 resolved — inline `_zscore_min_basket(values, min_basket=3)` is the right resolution.** Same stance `entry_timing_scorer.py` took (`scripts/entry_timing_scorer.py:1250-1267`). Do not mutate `sector_score.zscore`'s n≥2 threshold.
  - **R6 resolved — Req 10.7's `stock_appears_in_multiple_top_sectors` flag is real.** Req 4.4's dedup step is the flag's trigger site; requirements body needs a one-sentence citation that appends the flag whenever `len(sector_origins) >= 2`. This is a requirements-body touchup, not a design decision.
  - **FMP metric field naming diverges from requirements.** The requirements body references `enterprise_to_ebitda`, `pe_ratio`, `roe`, `gross_margin`, `fcf_yield`. FMP's live `equity.fundamental.metrics` returns `ev_to_ebitda`, `return_on_equity`, `free_cash_flow_yield`, and emits no `pe_ratio` / `gross_margin` fields (use `earnings_yield` as PE inverse and source `gross_profit_margin` from `equity.fundamental.ratios`). Design must either alias (read FMP's names, emit under the logical names in `signals`) or update the requirements body. Aliasing is the lower-churn choice and already has precedent in `_schema.classify_metric_unit`.
  - **G1 — `obb.technical.clenow` rejects batched multi-symbol historical input.** The batched historical endpoint works and returns a stacked DataFrame (tested AAPL/MSFT/NVDA), but feeding the stacked `results` into `obb.technical.clenow` raises `InvalidIndexError: Reindexing only valid with uniquely valued Index objects`. **Clenow must run per-symbol.** This changes the call-count profile for Req 5.3 from a dream "batched path" to a hard ~60 `equity.price.historical` + ~60 `technical.clenow` reductions per run.
  - **G2 — FMP batching matrix (live-verified 2026-05-01)**: `quote`, `fundamental.metrics`, `estimates.consensus`, `estimates.price_target`, `equity.price.historical` all accept `symbol="A,B,C"` and return one row per symbol (for `historical` / `price_target`: multiple rows per symbol). `etf.holdings` is naturally per-ETF. `technical.clenow` does not batch (G1). The design must route quote / metrics / consensus / price_target through **one batched call each**, route historical through **per-symbol calls** (because of G1), and size the per-minute call budget accordingly.
  - **G3 — FMP quote rejects non-US listings used by `theme-ark` holdings.** `KWEB` constituents include `0700.HK`, `9988.HK` etc.; `obb.equity.price.quote(symbol='0700.HK', provider='fmp')` returns HTTP 402 "Special Endpoint — Premium Query Parameter" (Starter+ does not cover HKEX quotes). US-listed ADRs (e.g. `BABA`) pass. Design must decide how the wrapper treats non-US tickers when the ETF universe expands into them.
  - **G4 — `sector_score._classify_ticker_failure` is NOT wrapper-compatible.** Its signature is `(ticker, perf, clenow_90, clenow_180)` and is specialized to sector-ETF scoring. The new wrapper classifies per-stock failure across five input axes (`quote`, `metrics`, `consensus`, `price_target`, `historical/clenow`) and needs its own classifier. Gap-analysis §2 row "Req 2" marked it as "Reuse" — this has to flip to "New" in the task breakdown so the LoC estimate does not under-count.
  - **G5 — `analyst_firm` carries empty-string rows in the `price_target` response.** Any naive distinct-firm count pollutes the 90-day coverage metric with an empty-string "firm." Drop empty-string / `None` rows before the distinct-firm reduction (N2's `number_of_analysts` derivation).
  - **G6 — FMP batched-row order is not guaranteed.** Live probes did not re-order across `quote` / `metrics` / `consensus` calls, but the OpenBB adapter does not promise order preservation. All batched-response handlers must index by `row["symbol"]`, not by position.

## Research Log

### R1 — Batched consensus on FMP
- **Context**: Req 14.1 asks whether `obb.equity.estimates.consensus(provider="fmp")` accepts comma-separated `symbol` in one round-trip. Whether it does drives the call-count profile of the ~60-stock basket (1 call vs 60).
- **Sources Consulted**:
  - Live probe executed 2026-05-01 against FMP Starter+ in this repo's `.venv`:
    - `obb.equity.estimates.consensus(symbol="AAPL,MSFT,NVDA", provider="fmp")`
  - FMP API documentation: https://site.financialmodelingprep.com/developer/docs#Analyst-Consensus
- **Findings**:
  - Batched call returns exactly 3 rows, one per symbol, in a single request.
  - Return schema under FMP (fixed, live): `{symbol, name, target_high, target_low, target_consensus, target_median}`.
  - `number_of_analysts`, `recommendation_mean`, and any analyst-count field are **not** in the response — OpenBB's consensus standard-model defines those fields, but the FMP adapter leaves them `None`.
- **Implications**:
  - Req 14.1's "preferred single-call" branch is the concrete implementation; no fall-back is needed for FMP batching.
  - Req 5.4 must drop `number_of_analysts` / `recommendation_mean` as consensus-sourced fields. They need a different derivation or the requirement must narrow to what consensus exposes.
  - Req 6.4 / 6.5 (`number_of_analysts ≥ 5` gate) needs a replacement source for the analyst-count input.

### R2 — min-basket threshold reconciliation
- **Context**: `sector_score.zscore` uses n≥2. Req 7.8 requires n≥3. Gap analysis flagged the inconsistency but deferred the resolution.
- **Sources Consulted**: `scripts/entry_timing_scorer.py:1250-1267`; `scripts/sector_score.py:329-338`.
- **Findings**:
  - `entry_timing_scorer.py` already solved the identical problem by defining `_zscore_min_basket(values)` inline with `_MIN_BASKET_SIZE = 3`.
  - Promoting `min_basket` as a parameter on `sector_score.zscore` would touch `sector_score.py`'s test surface for zero functional gain outside this wrapper.
- **Implications**:
  - Implement `_zscore_min_basket(values)` inline in `scripts/sector_stock_screener.py`. Use it everywhere the wrapper needs basket-wide z-scoring. Import `sector_score.zscore` only via `sector_score.build_scores` (which is called for ETF-level sector ranking and is the only place the n≥2 threshold is semantically correct — 11 SPDR sectors is never an issue).
  - Mirror the pattern for the sector-neutral variant: `_zscore_min_basket_sector_neutral(values, sector_tags)` with the same `min_basket=3` cutoff and per-group fallback to basket-wide z (Req 7.7).

### R5 — FMP `etf.holdings.updated` field format
- **Context**: Req 4.6 requires emitting `etf_holdings_updated_max_age_days` as a non-negative integer. Need to confirm the `updated` field's runtime type before writing the parse.
- **Sources Consulted**:
  - Live probe 2026-05-01: `obb.etf.holdings(symbol="XLE", provider="fmp")`.
- **Findings**:
  - `updated` arrives as a Python `datetime.datetime` instance (e.g. `2026-04-20 09:04:02`). Row schema: `{cusip, isin, name, shares, symbol, updated, value, weight}`.
  - XLE returned 25 holdings on the probe; 2026-04-20 for the lead row vs. today 2026-05-01 → 11 days.
- **Implications**:
  - The parse is trivial: `max_age = max((today - row.updated.date()).days for row in holdings if row.updated is not None)`. No string-parsing, no ISO coercion.
  - If `updated` is ever `None` on a row, skip that row from the max-age calculation; if every row is `None`, emit `etf_holdings_updated_max_age_days: null` and append a top-level warning.
  - The probe confirms the "typically lag spot by up to a week" caveat in Req 10.6 is accurate (XLE: 11 days on 2026-05-01 — inside a two-week window, still well within the mid-term horizon's tolerance).

### R6 — `sector_origins[]` multi-sector flag scoping
- **Context**: Req 10.7's closed enumeration includes `"stock_appears_in_multiple_top_sectors"`, but Req 4.4 (dedup + `sector_origins[]` build-up) does not cite when the flag is appended. Gap analysis flagged the inconsistency.
- **Sources Consulted**: `docs/tasks/todo/sector-stock-screener/requirements.md` (Req 4.4, Req 10.7); `scripts/entry_timing_scorer.py::append_quality_flag` pattern.
- **Findings**:
  - The flag is a pure-mechanical consequence of the dedup step: iff `len(sector_origins) >= 2` for a ticker row, the flag is appended. There is no design trade-off here — every candidate phrasing lands in the same implementation.
  - Adding the citation to Req 4.4 is the cheaper branch (one sentence); removing the flag from Req 10.7 loses audit-time visibility for no gain.
- **Implications**:
  - Req 4.4 gains the trailing sentence: "Additionally, when `len(sector_origins) >= 2` the sector stock screener shall append `"stock_appears_in_multiple_top_sectors"` to the per-row `data_quality_flags[]` (closed enumeration in Req 10.7)."
  - No design decision required; the implementation assembler function appends the flag at the end of the dedup merge pass.

### N1 — FMP metric field-name mapping (new finding)
- **Context**: Req 5.2 names the metric fields as `enterprise_to_ebitda`, `pe_ratio`, `roe`, `gross_margin`, `fcf_yield`. Live probing `obb.equity.fundamental.metrics(provider="fmp")` on XOM revealed these names do not exist under FMP.
- **Sources Consulted**:
  - Live probe 2026-05-01: `obb.equity.fundamental.metrics(symbol="XOM", provider="fmp")`.
  - Live probe 2026-05-01: `obb.equity.fundamental.ratios(symbol="XOM", provider="fmp", period="annual", limit=1)`.
  - `scripts/_schema.py` (DECIMAL_RATIO_FIELDS, METRIC_UNIT_MAP).
- **Findings**:
  - FMP `metrics` endpoint exposes: `ev_to_ebitda` (not `enterprise_to_ebitda`), `return_on_equity` (not `roe`), `free_cash_flow_yield` (not `fcf_yield`), `earnings_yield` (reciprocal of pe_ratio), `market_cap`, `enterprise_value`.
  - FMP `metrics` endpoint does **not** expose `pe_ratio` or `gross_margin`. `gross_profit_margin` lives on the `ratios` endpoint.
  - `_schema.METRIC_UNIT_MAP` already normalizes `enterprise_to_ebitda` as the canonical name for the unit tag (inherited from yfinance). So the wrapper has two choices:
    (a) **Alias at fetch time**: read FMP's native names from the response, emit them under the logical names (`enterprise_to_ebitda`, `roe`, `fcf_yield`) in the per-ticker `signals` block. Matches Req 5.1's pattern for MA fields. Lowest requirements churn.
    (b) **Update requirements body** to reference FMP's native names. Higher churn, but no alias layer to maintain.
- **Implications**:
  - Design should adopt (a) — alias at fetch time. Req 5.1's MA logic is the precedent: "the extracted values shall be emitted under the logical names `ma_200d` and `ma_50d` in the per-ticker `signals` block." Extending this to `ev_to_ebitda → enterprise_to_ebitda`, `return_on_equity → roe`, `free_cash_flow_yield → fcf_yield` preserves Req 5.2's output shape verbatim.
  - For `pe_ratio` and `gross_margin`: design must decide whether to (i) drop them from `signals` (neither is scored — Req 7.x uses only `ev_ebitda_yield` and `roe` as fundamentals), (ii) source them from a second call to `equity.fundamental.ratios` (adds 60 ratio calls per run at worst), or (iii) substitute `earnings_yield` for `pe_ratio` inverse and emit `gross_margin` as `null`. **Recommendation (i)**: drop `pe_ratio` and `gross_margin` from Req 5.2 / Req 10.2 entirely — they are informational and not used in any score path, so the wrapper emits only fields it actually consumes. The analyst can pull them with `scripts/fundamentals.py --type ratios` on demand.

### N2 — Consensus endpoint fields under FMP (follow-up to R1)
- **Context**: Req 5.4 and Req 6.4 / 6.5 assume consensus exposes `number_of_analysts` and `recommendation_mean`. R1's live probe revealed the FMP adapter does not populate those fields. This is a research finding that forces a Req-body amendment or a second data source.
- **Sources Consulted**:
  - Live probe 2026-05-01: `obb.equity.estimates.consensus(symbol="AAPL", provider="fmp")` → `{symbol, name, target_high, target_low, target_consensus, target_median}`.
  - Live probe 2026-05-01: `obb.equity.estimates.price_target(symbol="AAPL", provider="fmp", limit=200)` → returns a per-analyst revision log (up to 200 rows, each with `published_date, analyst_firm, analyst_name, price_target, price_target_previous, rating_current`). The endpoint is the unit source for an `N_ANALYSTS_WINDOW`-derived analyst count.
  - `obb.equity.estimates.forward_eps`, `forward_pe`, `forward_ebitda`, `forward_sales`, `historical`, `analyst_search` (the last requires `benzinga`, not FMP).
- **Findings**:
  - **Primary option**: derive `number_of_analysts` from `obb.equity.estimates.price_target(symbol=..., provider="fmp", limit=200)` by counting distinct `analyst_firm` entries whose `published_date` falls in the last 90 days (a common window in the literature; Fidelity 90-day revision window and AAII's "active coverage" convention both use 90 days).
  - **Cost**: 60 additional FMP calls per run (one per stock). FMP Starter is 300/min — combined with the batched consensus, quote, metrics, historical, and clenow calls the wrapper remains within the 300/min budget (approx 5 calls × 60 = 300 over the whole basket, amortized over minutes).
  - **`recommendation_mean`**: the `rating_current` field on each price-target revision is a text label (e.g. "Buy", "Outperform"), not a numeric mean. Converting it to a numeric mean requires an explicit label map (Buy=1, Outperform=2, Hold=3, Underperform=4, Sell=5 — Thomson Reuters / I/B/E/S convention). Design decision: the label-to-number map is error-prone and is not used in any scoring path. **Drop `recommendation_mean` from Req 5.4** — it is informational and the label-mapping decision is a real trade-off that does not pay for itself when the wrapper does not score it.
- **Implications**:
  - Req 5.4 must be rewritten to specify: "extract `target_consensus`, `target_median` from `obb.equity.estimates.consensus(provider="fmp")`; derive `number_of_analysts` as the count of distinct `analyst_firm` values on `obb.equity.estimates.price_target(symbol=..., provider="fmp", limit=200)` rows whose `published_date` falls within the most recent 90 days."
  - Req 6.4 / 6.5 keep the `number_of_analysts ≥ 5` gate unchanged — the derivation source changes, but the threshold is still a pure integer.
  - One new FMP call per stock is added to the per-ticker data-acquisition fan-out. Call count remains well inside FMP Starter 300/min.
  - Req 10.2 must drop `recommendation_mean` from the `signals` block field list.

### G1 — `obb.technical.clenow` does not accept batched multi-symbol historical
- **Context**: Req 5.3 fan-out assumed the Clenow input could be fetched in one batched `historical` call (matching the R1 / N1 pattern) and passed once to `technical.clenow`.
- **Sources Consulted**:
  - Live probe 2026-05-01: `obb.equity.price.historical(symbol="AAPL,MSFT,NVDA", provider="fmp", start_date="2025-10-01")` → DataFrame shape `(246, 9)`, `index.names=['date']`, `columns=['open', 'high', 'low', 'close', 'volume', 'vwap', 'change', 'change_percent', 'symbol']` — a stacked long-form frame, three dates share one index value.
  - Live probe 2026-05-01: `obb.technical.clenow(data=hist.results, target='close', period=90)` on the stacked result → `OpenBBError: InvalidIndexError -> Reindexing only valid with uniquely valued Index objects`.
  - Live probe 2026-05-01: per-symbol path (`historical` + `clenow` run once per ticker) succeeds — AAPL `factor=-0.00209`, MSFT `factor=-0.00014`, NVDA `factor=0.06010`.
- **Findings**:
  - `obb.technical.clenow` internally performs `.reindex(...)` / `.sort_index(...)` operations that assume a uniquely-valued date index. Stacking multiple symbols on the same `date` index violates that invariant.
  - The only supported path is per-symbol: `for sym in pool: hist = obb.equity.price.historical(symbol=sym, provider="fmp", ...); clenow = obb.technical.clenow(data=hist.results, ...)`.
  - The precedent is already in the repo: `scripts/sector_score.py::fetch_clenow` loops one ticker at a time.
- **Implications**:
  - The Req 5.3 implementation is a tight per-symbol loop with ~60 `equity.price.historical` + ~60 `technical.clenow` calls per run. The "batched historical + one clenow" shortcut is closed.
  - The `equity.price.historical` call per symbol is not redundant with any other fetch in the pipeline: `quote` exposes spot / year_high / year_low / MA but not a full bar series, so Clenow genuinely requires its own fetch. No cost can be eliminated by sharing the historical pull with another primitive.
  - Update to the design's call-count estimate (see G2 below) and to the SKILL.md "Rate limits" note: the 300/min budget is driven by historical + clenow, not by consensus batching.

### G2 — FMP batching matrix — which endpoints accept batched symbols on FMP
- **Context**: Before N2 (price_target derivation) added a sixth per-stock fetch, the rate-budget worry was small. The actual shape of the Starter 300/min budget now depends on which endpoints batch and which do not.
- **Sources Consulted** (all live probes 2026-05-01):
  - `obb.equity.price.quote(symbol="AAPL,MSFT,NVDA", provider="fmp")` → 3 rows, distinct symbols in result.
  - `obb.equity.fundamental.metrics(symbol="AAPL,MSFT,NVDA", provider="fmp")` → 3 rows.
  - `obb.equity.estimates.consensus(symbol="AAPL,MSFT,NVDA", provider="fmp")` → 3 rows.
  - `obb.equity.estimates.price_target(symbol="AAPL,MSFT,NVDA", provider="fmp", limit=50)` → 150 rows across 3 symbols.
  - `obb.equity.price.historical(symbol="AAPL,MSFT,NVDA", provider="fmp", start_date="2026-02-01")` → 186 rows (stacked).
  - `obb.technical.clenow` — see G1.
  - `obb.etf.holdings` — naturally per-ETF (each call returns holdings of one ETF).
- **Findings — per-run call count for the default basket (top-sectors=3, top-stocks-per-sector=20 → ~60 de-duped stocks)**:
  | Stage | Endpoint | Batched? | Calls (default run) |
  | --- | --- | --- | --- |
  | Sector rank input | `equity.price.historical` (per sector ETF, used for `sector_score.build_scores` inputs) | ✅ | 1 (11 SPDR tickers batched) |
  | Sector rank input | `technical.clenow` on sector ETF | ❌ (G1) | 11 |
  | Stock pool expansion | `etf.holdings` | per-ETF | 3 |
  | Stock pool quote | `equity.price.quote` | ✅ | 1 |
  | Stock pool fundamentals | `equity.fundamental.metrics` | ✅ | 1 |
  | Stock pool consensus | `equity.estimates.consensus` | ✅ | 1 |
  | Stock pool analyst count | `equity.estimates.price_target` | ✅ | 1 (60 symbols batched, `limit=200` per symbol) |
  | Stock pool clenow input | `equity.price.historical` | ❌ (G1 path) | 60 |
  | Stock pool clenow | `technical.clenow` | ❌ (G1) | 60 |
  | **Total** | | | **~138 per run** |
- **Implications**:
  - FMP Starter 300/min has roughly 2× headroom. Worst case (`--top-sectors 11 --top-stocks-per-sector 100`) is ~220 + sector overhead, still inside. No explicit throttling is required in MVP.
  - Every batched-response handler keys by `row["symbol"]`, never by position (see G6).
  - `price_target(limit=200)` is the batch-limit dial; 200 per symbol times 60 symbols = 12 000 rows in one round-trip — `.to_df()` handles this fine but the wrapper should slim to the 90-day slice in-wrapper before the distinct-firm reduction.
  - Potential optimization (design decision, not a requirement): the sector-rank Clenow loop (11 ETFs) can be dropped if the new wrapper imports `sector_score.build_scores` and calls `sector_score.fetch_performance` / `fetch_clenow` directly — but those functions hard-code finviz + yfinance, which breaks the Req 2.1 FMP-only contract. Conclusion: the new wrapper writes its own FMP-native sector-rank fetchers (mirrors gap-analysis §2 "FMP-native performance fetcher"), incurring the 11 extra Clenow calls. Acceptable.

### G3 — FMP quote rejects non-US listings used by `theme-ark` holdings
- **Context**: `theme-ark` universe ETFs such as `KWEB` (China Internet) hold HK / Shanghai tickers. FMP Starter+ quote coverage is US-centric; unfamiliar listings return HTTP 402.
- **Sources Consulted**:
  - Live probe 2026-05-01: `obb.etf.holdings(symbol="KWEB", provider="fmp")` → 32 rows, first is `0700.HK` (Tencent).
  - Live probe 2026-05-01: `obb.equity.price.quote(symbol="0700.HK", provider="fmp")` → `UnauthorizedError: 402 -> Premium Query Parameter: 'Special Endpoint : This value set for 'symbol' is not available under your current plan'`.
  - Live probe 2026-05-01: `obb.equity.price.quote(symbol="9988.HK", provider="fmp")` → same 402.
  - Live probe 2026-05-01: `obb.equity.price.quote(symbol="BABA", provider="fmp")` → ok (ADR is fine).
- **Findings**:
  - Starter+ covers US-listed tickers (including ADRs like `BABA`) but not native non-US listings (`.HK`, `.T`, `.SW`, etc.).
  - The 402 from `quote` propagates to `safe_call` as `error_category: plan_insufficient`. If the wrapper surfaces these rows as per-stock failures, the top-level warnings channel fills with coverage failures on every `theme-ark` run.
- **Implications** (design decision — three branches, pin one in `design.md`):
  - **(a) Filter non-US tickers at pool-build time** (recommended): when building the stock pool from `etf.holdings`, drop rows whose `symbol` matches a non-US suffix pattern (`r"\.[A-Z]{2}$"`) and append a top-level `analytical_caveats` entry `"non_us_tickers_filtered_from_pool"`. Cheapest; preserves the stock pool as scorable. Matches the spirit of Req 1.5 (JP sector hard-block).
  - **(b) Pass through and let per-stock safe_call tag each row**: every filtered ticker becomes a `{ok: false, error_category: "plan_insufficient"}` row. Preserves audit trail but explodes the warnings channel (up to 30 failures on a KWEB run).
  - **(c) Fallback ADR map**: maintain a static `{"0700.HK": "TCEHY", ...}` mapping. Most audit-friendly but the mapping is maintenance overhead and no canonical source exists inside the repo.
- **Recommendation**: adopt (a). Document the filter in `analytical_caveats` so the caller never silently wonders why KWEB's 32 holdings collapsed to 15 scorable rows.

### G4 — `sector_score._classify_ticker_failure` does not fit per-stock failure classification
- **Context**: Gap-analysis §2 row on `_classify_ticker_failure` marked it as "Reuse." Live source inspection shows the signature is `(ticker, perf, clenow_90, clenow_180)` — three specific input axes tied to sector-ETF scoring.
- **Sources Consulted**: `scripts/sector_score.py:445-491`.
- **Findings**:
  - The classifier's rule is "ticker is all-failed only when every provider path yielded no data": it checks `perf` membership plus both Clenow windows. Per-stock failure in the new wrapper is determined across **five** axes (`quote`, `metrics`, `consensus`, `price_target`, `historical/clenow`).
  - The sector-rank step inside the new wrapper (which *does* use `build_scores` on sector ETFs) *can* reuse the existing classifier verbatim — but the stock-pool step needs a new classifier.
- **Implications**:
  - Two classifiers coexist in the new wrapper:
    - `_classify_ticker_failure` (imported from `sector_score`): used during the sector-rank step (ETF-level).
    - `_classify_stock_failure(symbol, fetches: dict[str, dict[str, Any]])` (new, ~30 LoC): used during per-stock scoring. Rule: the stock is all-failed only when every fetch in `{quote, metrics, historical, consensus, price_target}` returned `ok: false`. The fatal-category promotion rule mirrors `_classify_ticker_failure` (all `credential` → credential; all `plan_insufficient` → plan_insufficient; else first-seen).
  - Gap-analysis §2 needs a cross-reference note: the "Reuse" marker applies to the sector-rank step only. Task breakdown (`/sdd-spec-tasks`) should carry a separate task for `_classify_stock_failure`.

### G5 — `analyst_firm` empty-string rows pollute distinct-firm count
- **Context**: N2 pins the `number_of_analysts` derivation to the count of distinct `analyst_firm` values on `equity.estimates.price_target` rows whose `published_date` falls in the last 90 days.
- **Sources Consulted**: Live probe 2026-05-01 on `equity.estimates.price_target(symbol="AAPL", provider="fmp", limit=200)` returned 200 rows; the most recent row carried `analyst_firm=''` (empty string) and `analyst_name=''`.
- **Findings**:
  - A naive `{row["analyst_firm"] for row in recent_90d}` reduction counts `""` as one distinct firm.
  - Across 200 rows for a liquid mega-cap, the pattern repeats on several entries. The empty-string inflation can push a ticker with four real firms to `number_of_analysts=5` — a false pass of the Req 6.4 / 6.5 gate.
- **Implications**:
  - The derivation clause must filter: `distinct_firms = {f for f in (row.analyst_firm for row in recent_90d) if f and f.strip()}`. Lock this in the Req 5.4 rewrite that N2 proposes, and document in SKILL.md's "Data provenance" subsection so a future maintainer does not reintroduce the bug during a refactor.

### G6 — FMP batched-response row order is not guaranteed
- **Context**: Every batched endpoint handler needs to map response rows back to their input symbols. If ordering is preserved, position-based mapping works; otherwise the handler must look up by `row["symbol"]`.
- **Sources Consulted**: Live probes 2026-05-01 — response order across `quote`, `metrics`, `consensus`, `price_target` matched input order on every probe, but OpenBB's adapter contract makes no such guarantee and the underlying FMP endpoint documentation is silent on the point.
- **Findings**:
  - Trust-but-verify posture: assume order is unstable, always key by `row["symbol"]` (normalized to upper-case to match `UNIVERSES`).
  - The same caution applies to `equity.price.historical` batched output — a stacked frame where each symbol contributes multiple rows. The handler must split by `row["symbol"]` before feeding into `technical.clenow`.
- **Implications**:
  - Design mandate: every batched-response fetcher in `scripts/sector_stock_screener.py` builds a `dict[str, dict[str, Any]]` keyed on `symbol` immediately after the call returns. Downstream consumers never index into `r.results[i]` by position. This is a structural convention; codify it once in a private helper `_index_by_symbol(rows: list[dict]) -> dict[str, dict]`.

### G7 — `volatility_month` parity with `sector_score.py` (follow-up to Task 10.2)
- **Context**: Task 10.2 flagged a risk that `_compute_perf_record_from_rows` might compute `volatility_month` on a different basis than `scripts/sector_score.py::_compute_performance_from_history`, silently diverging the `risk_adj` sub-component of the sector composite.
- **Sources Consulted**: `scripts/sector_score.py:177-178` (yfinance-computed path) and `scripts/sector_stock_screener.py::_compute_perf_record_from_rows`.
- **Findings**:
  - `sector_score.py:177-178`: `monthly = closes.pct_change().dropna(); vol_month = float(monthly.tail(21).std()) if len(monthly) >= 5 else None`. The sample-stdev default of pandas `Series.std` (ddof=1) with no annualization and no rolling window.
  - `sector_stock_screener.py::_compute_perf_record_from_rows`: iterates the same `close[i]/close[i-1] - 1` daily returns and calls `statistics.stdev(tail[-21:]) if len(tail) >= 2`, which is also ddof=1 sample stdev with no annualization. The minimum-length check (2 vs 5) differs in edge cases but does not affect typical 260-row sector-ETF histories.
  - **Conclusion**: numerical parity on `volatility_month`; `risk_adj = one_month / volatility_month` in `build_scores` consumes identical units on both paths. Task 10.2 item #2 is closed with no code change required. If either side ever shifts to annualized vol, the other must follow in the same commit.
- **Implications**:
  - No code change. Leave a single-line comment on `_compute_perf_record_from_rows` citing parity with `sector_score.py:177-178` so future contributors do not reintroduce divergence unknowingly.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **A. Import pure helpers from `sector_score`, FMP-native fetch layer inline** | New `scripts/sector_stock_screener.py` imports `UNIVERSES, zscore, rank_desc, build_scores, _classify_ticker_failure` from `sector_score`. Writes its own FMP-native performance + Clenow + quote + metrics + consensus + price-target fetchers inline. | Matches `structure.md` thin-wrapper principle. Zero churn to `sector_score.py`. FMP-only pin slims the code (no `--provider`, no provider router, no `_QUOTE_FIELD_MAP`). Reuses ~100 LoC of precedent. | Two performance-fetch paths coexist (finviz-based in `sector_score`, FMP-native here) — acceptable because they serve different provider contracts. | **Selected**. See Decision: implementation-pattern below. |
| B. Extract `sector_score` pure helpers into `scripts/_sector_scoring.py` first | Underscore-prefix shared module for z-score / rank / build_scores. Both wrappers import from `_sector_scoring`. | Cleaner dependency graph (no `scripts/*.py → scripts/*.py` non-underscore import). | Pure structural refactor; no functional gain. Adds a third wrapper touch-point (change `sector_score.py`, add `_sector_scoring.py`, add the new wrapper) in one PR — widens blast radius. | Revisit if a third sector-screener surfaces. |
| C. Inline-copy all helpers | Duplicate `zscore`, `rank_desc`, `build_scores` bodies into the new wrapper. | No cross-file import. | Two sources of truth for the composite formula — exactly what `structure.md`'s "no business logic duplication" rule forbids. | Rejected. |

## Design Decisions

### Decision: Import Pattern
- **Context**: `sector_score.py` owns the pure composite scoring primitives (`UNIVERSES`, `zscore`, `rank_desc`, `build_scores`, `_classify_ticker_failure`). The new wrapper needs all of them plus one private function (`_classify_ticker_failure`) that carries a leading underscore.
- **Alternatives Considered**:
  1. Import directly (`from sector_score import UNIVERSES, zscore, rank_desc, build_scores, _classify_ticker_failure`) — crosses the non-underscore wrapper boundary and imports a private helper.
  2. Promote the shared primitives to `scripts/_sector_scoring.py` first (Option B above) — cleaner but adds a third touch-point.
  3. Copy the primitives inline — forbidden by `structure.md`.
- **Selected Approach**: Option 1. Import via the flat `sys.path`-root layout documented in `structure.md`.
- **Rationale**: `structure.md`'s "thin wrapper" rule is the dominant constraint. Option 1 adds ~1 import line to the new wrapper and zero lines to `sector_score.py`. Option 2 changes three files for zero functional gain. The private-helper import (`_classify_ticker_failure`) is a recognized soft-convention violation, but the function is genuinely pure and the only alternative (duplicate the 50-LoC classifier) is worse.
- **Trade-offs**: (+) Zero churn to `sector_score.py`'s test surface. (+) No new underscore module to own. (−) The non-underscore-to-non-underscore import is a minor structural violation; flag it in SKILL.md's "Dependencies" subsection and revisit if a third caller emerges.
- **Follow-up**: Add a one-line comment at the import site citing `gap-analysis.md` §3 Option A. When a third caller emerges, promote to `_sector_scoring.py` in a separate PR.

### Decision: min-basket threshold — `_zscore_min_basket` inline (R2)
- **Context**: Req 7.8 requires n<3 → null; `sector_score.zscore` uses n<2 → null. Two incompatible thresholds share a wrapper.
- **Alternatives Considered**:
  1. Define `_zscore_min_basket(values)` inline in the new wrapper with `_MIN_BASKET_SIZE = 3` (identical to `entry_timing_scorer.py:1247-1267`).
  2. Parametrize `sector_score.zscore(values, min_basket=2)` and pass `3` from the new wrapper.
- **Selected Approach**: Option 1.
- **Rationale**: Option 2 widens `sector_score.py`'s test surface (the existing zscore tests lock in n≥2 semantics). Option 1 is a 17-LoC copy of a precedent already in the repo. Every wrapper that needs the stricter threshold picks it up inline.
- **Trade-offs**: (+) Zero churn to `sector_score.py`. (+) The threshold is visible where it matters (the new wrapper). (−) Two copies of an almost-identical zscore body; acceptable because the body is small and pure.
- **Follow-up**: If a third wrapper needs `min_basket=3`, promote `_zscore_min_basket` to `_common.py`. Until then, inline.

### Decision: sector-neutral z-score — `_zscore_min_basket_sector_neutral` inline (Req 7.1, 7.7)
- **Context**: Req 7.1 computes z-scores within each GICS sector for `ev_ebitda_yield` and `roe`. When a sector group has fewer than 3 non-null values, Req 7.7 falls back to basket-wide z with a per-row flag `"sector_group_too_small_for_neutral_z(<factor>)"`.
- **Alternatives Considered**:
  1. Write `_zscore_min_basket_sector_neutral(values, sector_tags, factor_name)` inline — group values by `sector_tags`, run `_zscore_min_basket` within each group, fall back row-by-row to basket-wide z when `len(group) < 3`.
  2. Two-pass function: first basket-wide z, then post-hoc sector-group-centered adjustment.
- **Selected Approach**: Option 1.
- **Rationale**: Option 1 matches the mental model in Req 7.1 verbatim ("grouped by `gics_sector`, centered on sector median"). Option 2 is mathematically equivalent only in the degenerate case and adds a second reduction pass for no readability gain. The per-row fallback flag is appended by the fall-back branch inside `_zscore_min_basket_sector_neutral` so the caller does not have to track group membership separately.
- **Trade-offs**: (+) Flag emission is coupled to the z-score computation — no risk of a flag being forgotten when the fallback triggers. (−) The function signature carries `factor_name` just to build the flag string; acceptable cost.
- **Follow-up**: If the sector-neutral pattern appears in a third wrapper, promote to `_common.py`. Until then, inline.

### Decision: analyst-coverage signal — derive `number_of_analysts` from `price_target` revision log (new; follow-up to R1)
- **Context**: Req 5.4 assumed consensus exposes `number_of_analysts`. The live probe (R1, N2) showed FMP's consensus response does not include it. Req 6.4 / 6.5 gate `target_upside` on this field. Without a replacement, the forward-looking sub-score collapses.
- **Alternatives Considered**:
  1. Derive from `obb.equity.estimates.price_target(symbol=..., provider="fmp", limit=200)` — count distinct `analyst_firm` values whose `published_date` is within the last 90 days. One extra FMP call per stock.
  2. Keep `number_of_analysts` always `null` and always emit `target_upside` without gating. Drops the coverage-quality filter that Req 6.4 / 6.5 lock in.
  3. Switch the consensus source to yfinance (which exposes `number_of_analyst_opinions` on its quote endpoint). Breaks the FMP single-provider contract (Req 2.1) — not viable.
  4. Drop the forward-looking sub-score from the composite entirely. Throws out a full factor leg.
- **Selected Approach**: Option 1, with an explicit 90-day window matching Fidelity / AAII conventions.
- **Rationale**: Option 1 preserves the FMP single-provider contract AND the coverage-quality gate. Cost is one extra batched-or-per-stock call per ticker (60 per run at worst), well inside FMP Starter 300/min. Option 2 loses signal quality and contradicts Req 6.4's reasoning ("sparsely-covered names never contribute a noisy forward-looking signal"). Option 3 is excluded by Req 2.1. Option 4 throws out the factor entirely.
- **Trade-offs**:
  - (+) Preserves the Req 6.4 / 6.5 gate semantics.
  - (+) Richer than a simple analyst count — the `price_target` revision log also enables a future `analyst_revision_momentum` sub-score (deferred to a later spec per Req 11.5).
  - (+) Single-provider contract preserved.
  - (−) Adds 60 calls per run. Not a real limit concern at Starter 300/min but worth echoing in SKILL.md's rate-limit note.
  - (−) The 90-day window is a new CLI-unconfigurable constant. Freeze it in MVP (Req 6.4 anchor) — revisiting is a requirements change.
- **Follow-up**:
  - **Requirements amendment required**: Req 5.4 rewrites to describe the `price_target` derivation; Req 6.4 / 6.5 thresholds are unchanged. Additional top-level caveat in `data.analytical_caveats`: `"number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions"`.
  - `recommendation_mean` drops from Req 5.4 and Req 10.2 (not derivable without a label-to-number map the wrapper does not own).
  - The probe command is documented in SKILL.md under a "Data provenance" subsection so a future maintainer can re-verify the window choice.

### Decision: FMP metric-field aliasing (new; follow-up to N1)
- **Context**: Req 5.2 names five metric fields (`enterprise_to_ebitda`, `pe_ratio`, `roe`, `gross_margin`, `fcf_yield`). Live probe showed FMP's `metrics` endpoint exposes `ev_to_ebitda`, `return_on_equity`, `free_cash_flow_yield` under different names; `pe_ratio` and `gross_margin` are not on this endpoint at all.
- **Alternatives Considered**:
  1. Alias at fetch time — read FMP names, emit under logical names. Keep `pe_ratio` / `gross_margin` off the `signals` block (not scored, not consumed).
  2. Update requirements to FMP names, keep the full field set, source `pe_ratio`/`gross_margin` from `ratios` endpoint with a second call chain.
  3. Alias the three that exist; add a second `ratios` call just to source `pe_ratio` / `gross_margin`.
- **Selected Approach**: Option 1.
- **Rationale**: Req 5.1 already sets the precedent (logical names for MA fields). The wrapper emits only what it consumes — `pe_ratio` and `gross_margin` have no scoring path, so their inclusion is informational clutter.
- **Trade-offs**: (+) Preserves the Req 5.2 field-name contract for the fields that are actually used. (+) No second `ratios` call. (−) Req 5.2 loses two fields from its explicit enumeration — this is a requirements-body amendment.
- **Follow-up**: Req 5.2 rewrites to drop `pe_ratio` and `gross_margin`. Req 10.2's `signals` enumeration likewise drops both fields. Define a private constant in the wrapper: `_FMP_METRIC_ALIASES = {"ev_to_ebitda": "enterprise_to_ebitda", "return_on_equity": "roe", "free_cash_flow_yield": "fcf_yield"}` and apply it at row-extraction time (mirrors `_FINVIZ_PERF_RENAME_MAP` pattern in `sector_score.py:75-79`).

### Decision: top-level sub-score weights — default blend
- **Context**: Req 7.6 exposes four CLI-tunable top-level weights but does not prescribe defaults.
- **Alternatives Considered**:
  1. Equal-weight (0.25 each) — no prior preference, auditable.
  2. Momentum-heavy (e.g. 0.40 / 0.25 / 0.20 / 0.15) following AQR "Value and Momentum Everywhere" findings on which factor carries more cross-sectional power in quarterly horizons.
  3. Value+Quality-heavy (e.g. 0.20 / 0.30 / 0.30 / 0.20) following Asness "Quality Minus Junk" and the mid-term horizon bias toward valuation convergence.
- **Selected Approach**: Option 1 (equal-weight 0.25 each) as MVP default.
- **Rationale**: The mid-term strategy in `policy.md` §3 does not prescribe a weight profile. The analyst agent is expected to tune the weights per cycle via CLI flags. Equal-weight is the least-opinionated starting point and is trivially auditable. Academic references for Option 2 / 3 conflict across horizons (momentum wins at 3-12m, value at 2-5y; the 6m-2y "mid-term" band sits on the transition). The CLI surface already covers both tilts.
- **Trade-offs**: (+) No silent prior baked into MVP behavior. (−) Analyst must learn to tune the weights; SKILL.md includes one example.
- **Follow-up**: If the quarterly cycle usage reveals a stable analyst-preferred tilt, promote it to the default in a follow-on spec.

### Decision: Clenow fetch shape — per-symbol loop (G1)
- **Context**: Clenow input (`equity.price.historical`) batches; `technical.clenow` does not. The combined pipeline is per-symbol.
- **Alternatives Considered**:
  1. Per-symbol loop: `for sym in pool: historical(sym); clenow(...)`. Identical to `scripts/sector_score.py::fetch_clenow`.
  2. Fetch historical once in batched form, split the stacked frame by symbol in-wrapper, then loop `technical.clenow` over the split frames. Saves the historical call-count but adds a stack-splitting helper.
  3. Drop Clenow entirely and use `equity.price.performance` (which batches end-to-end on finviz). Breaks Req 2.1 FMP-only contract.
- **Selected Approach**: Option 1.
- **Rationale**: Option 2 saves 60 historical calls but adds a stack-split helper whose only user is this wrapper. Under the Starter 300/min budget the savings are immaterial. Precedent (`scripts/sector_score.py:279-326`) is Option 1. Option 3 is excluded by the provider contract.
- **Trade-offs**: (+) Matches existing precedent 1:1. (+) Simple error-propagation: each per-symbol `safe_call` yields a `{ok, factor, error_category}` record. (−) ~60 extra historical calls per run. FMP Starter headroom absorbs it.
- **Follow-up**: Inline helper `_fetch_clenow_fmp(symbol, period=90, lookback_days=180)` mirrors `sector_score.fetch_clenow` with `provider="fmp"` pinned.

### Decision: Non-US ticker handling — pool-build filter (G3)
- **Context**: FMP Starter+ quote rejects non-US listings (`0700.HK` etc.). `theme-ark` (KWEB) and `global-factor` universes can expand into them.
- **Alternatives Considered**:
  1. Pool-build filter: drop `symbol` matching `r"\.[A-Z]{1,3}$"` before issuing any per-stock call; emit `analytical_caveats` entry `"non_us_tickers_filtered_from_pool"`.
  2. Pass-through: let each non-US ticker surface as `{ok: false, error_category: "plan_insufficient"}` per Req 5 fail paths.
  3. Static ADR fallback map: `{"0700.HK": "TCEHY", ...}` in-wrapper.
- **Selected Approach**: Option 1.
- **Rationale**: Option 1 is cheapest, composes cleanly with Req 1.5's JP-sector block, and keeps the warnings channel focused on genuine failures. Option 2 floods warnings with 30+ coverage failures on a single `KWEB` run — noise that obscures real provider issues. Option 3 is maintenance overhead with no canonical source.
- **Trade-offs**: (+) Warnings channel stays actionable. (+) One-line filter; one-line `analytical_caveats` addition. (−) The filter is a static regex; a hypothetical future US ticker with `.` in the symbol would be wrongly dropped. Mitigation: the regex is restricted to 1–3 upper-case letters after the dot (the exchange-suffix convention). US tickers do not carry this pattern.
- **Follow-up**:
  - Add `"non_us_tickers_filtered_from_pool"` to Req 10.6's `analytical_caveats` list (requirements-body amendment).
  - Add `"non_us_ticker_filtered_from_pool"` to Req 10.7's closed `DATA_QUALITY_FLAGS` enumeration so callers can enumerate the catalog. (The flag is also appended at the `sector_origins[]` level on the ETF row's diagnostic — design pins the exact shape.)
  - SKILL.md scope subsection states "theme-ark ETFs containing non-US listings will see their HK/TSE/… tickers dropped from the scored pool on Starter+."

### Decision: Per-stock failure classifier — new inline function (G4)
- **Context**: `sector_score._classify_ticker_failure` is specialized to sector-ETF scoring axes and is not reusable for per-stock classification across five axes.
- **Alternatives Considered**:
  1. Write `_classify_stock_failure(symbol, fetches: dict[str, dict])` inline. Rule: a stock is all-failed only when every fetch in `{quote, metrics, historical, consensus, price_target}` returned `ok: false`. Promote the error_category via the same "all-same-fatal → fatal" rule as the existing classifier.
  2. Generalize `sector_score._classify_ticker_failure` to accept a `fetches: dict` argument. Widens `sector_score.py`'s test surface.
- **Selected Approach**: Option 1.
- **Rationale**: Two classifiers with clear scope is cleaner than one generalized classifier whose callers must compose their own input dicts. `sector_score`'s tests stay untouched. The new classifier is ~30 LoC and reuses `ErrorCategory`.
- **Trade-offs**: (+) Tight coupling between the classifier and the axes it classifies. (+) Zero churn to `sector_score.py`. (−) Two near-identical classifiers in the repo; acceptable while each has a clear axis-specific scope.
- **Follow-up**:
  - The sector-rank step keeps using `sector_score._classify_ticker_failure` verbatim.
  - The per-stock step uses the new `_classify_stock_failure` from `scripts/sector_stock_screener.py`.
  - `/sdd-spec-tasks` should carry `_classify_stock_failure` as a distinct task item (gap-analysis §2 will need a corrigendum flip from "Reuse" → "New, ~30 LoC").

### Decision: Distinct-analyst-firm reduction — strip empty / None (G5)
- **Context**: N2's `number_of_analysts` derivation depends on counting distinct `analyst_firm` values. Live data contains empty-string rows.
- **Alternatives Considered**:
  1. Filter `{f for f in firms if f and f.strip()}` before the `len(...)` reduction.
  2. Accept the noise; document in caveats.
- **Selected Approach**: Option 1.
- **Rationale**: Option 2 would produce false passes of the Req 6.4 gate (a stock with 4 real firms + 1 empty string becomes eligible). The filter is 1 LoC.
- **Trade-offs**: None material.
- **Follow-up**: Codify in the Req 5.4 rewrite: "... rows whose `published_date` falls within the most recent 90 days, excluding rows where `analyst_firm` is null, empty, or whitespace-only."

### Decision: Batched-response indexing — key by `symbol`, never by position (G6)
- **Context**: OpenBB's batched-call contract does not guarantee response order.
- **Selected Approach**: Private helper `_index_by_symbol(rows: list[dict]) -> dict[str, dict | list[dict]]` applied immediately after every batched call. Single-row-per-symbol endpoints (`quote`, `metrics`, `consensus`) yield `dict[symbol, row]`; multi-row-per-symbol endpoints (`price_target`, `historical`) yield `dict[symbol, list[row]]`.
- **Rationale**: Costs one helper function and a uniform call-site pattern; removes an entire class of order-dependence bugs before they manifest.
- **Trade-offs**: (+) Robust to upstream order changes. (−) One extra indirection per batched call. Negligible.
- **Follow-up**: Integration test (Req 13.x extension, informational — not a requirements change) asserts `data.results[]` rows have the expected `symbol` values regardless of input order. Does not need a separate test if the existing Req 13 tests already cover `symbol` on every row.

### Decision: GICS sector mapping for non-sector universes (pinned by Req 5.7 last sentence)
- **Context**: Req 5.7 last sentence covers this explicitly: `theme-ark` and `global-factor` members do not map one-to-one to GICS sectors; `gics_sector=null` and Req 7.7's basket-wide fallback applies row-wise.
- **Selected Approach**: Hard-code the SPDR mapping: `XLK→"Information Technology", XLF→"Financials", XLE→"Energy", XLV→"Health Care", XLI→"Industrials", XLP→"Consumer Staples", XLY→"Consumer Discretionary", XLU→"Utilities", XLB→"Materials", XLRE→"Real Estate", XLC→"Communication Services"`. Any other ETF origin leaves `gics_sector=null`.
- **Rationale**: SPDR sector fund suite is the canonical sector-ETF universe; the one-to-one mapping is stable (no SPDR sector split since 2018 Communication Services reclassification). Non-sector universes fall through by construction.
- **Trade-offs**: (+) Zero ambiguity, zero external lookup. (−) The map is repo-local state; moving to a sector-mapping library is overkill for 11 entries.
- **Follow-up**: None.

## Risks & Mitigations

- **Risk — FMP consensus schema drift**: If FMP starts populating `number_of_analysts` natively on the consensus endpoint, the `price_target`-based derivation becomes redundant but not incorrect. **Mitigation**: annual re-probe of the consensus response shape; if the native field appears, switch to it and drop the `price_target` call. No contract change downstream.
- **Risk — `price_target` API rate-limit consumption**: 60 calls/run at worst. Starter 300/min budget is ample but shared with other wrapper runs in the same window. **Mitigation**: Document in SKILL.md "Rate limits" subsection: sector-stock-screener runs should not overlap with other FMP-heavy wrappers in the same minute window. No code change — operator discipline.
- **Risk — `price_target` endpoint returns `limit=200` but not all analyst firms covered in the 90-day window**: If a stock has >200 revisions in 90 days, the distinct-firm count undercounts. **Mitigation**: Unlikely in practice (only mega-caps approach 200 revisions in 90 days, and those have many firms so the undercount still lands above the threshold 5). Document the edge case in the "Data provenance" subsection of SKILL.md.
- **Risk — `etf.holdings.updated` returns `None` for newly-issued ETFs**: `max_age_days` calculation would crash. **Mitigation**: Guard the computation with the "skip None rows, emit null max_age if all None" branch documented in R5.
- **Risk — sector-neutral z-score degenerates when a single SPDR sector ETF is selected**: Only one `gics_sector` group in the basket. Every value/quality z would fall back to basket-wide via Req 7.7. **Mitigation**: Req 7.7 handles this by construction; no additional code. Operator sees the per-row flags and understands the semantics.
- **Risk — `min_basket=3` discards factors on extremely narrow baskets**: `--top-sectors 1 --top-stocks-per-sector 2` has basket size 2. Every basket-wide z collapses to null. **Mitigation**: Req 4.7 already handles this — partial rows emit with `null` z-scores and a top-level validation warning. Operator sees the degenerate output.
- **Risk — G1 per-symbol Clenow loop under-performs the rate budget if a downstream spec expands the basket**: At `--top-stocks-per-sector 100 × --top-sectors 11` the pool can approach 1100 stocks; Clenow per-symbol at ~0.5 s/call crosses 9 min. **Mitigation**: Req 4.2's argparse upper bound (`[1, 100]`) and Req 3.2's `[1, 11]` sector bound keep the default runtime tractable; the extreme configuration is operator-chosen and the SKILL.md "Rate limits" subsection calls it out.
- **Risk — G3 ADR-filter false negatives**: a US ticker that happens to carry a `.` (none exist at the major exchanges today) would be filtered. **Mitigation**: the regex is `r"\.[A-Z]{1,3}$"` (exchange-suffix shape, not arbitrary dots); the watchlist for such symbols is narrow and can be audited against CRSP / NYSE / NASDAQ symbol masters if a regression is ever reported.
- **Risk — G4 classifier drift**: two near-identical classifiers diverge over time. **Mitigation**: the per-stock classifier is ~30 LoC and carries a source comment pointing back to `sector_score._classify_ticker_failure` as the sibling pattern; promote both to `_common.py` if a third caller emerges.
- **Risk — G5 empty-string pollution reappears after an FMP schema change**: if FMP starts using `None` instead of `""`, the filter still holds; if FMP changes to a sentinel string like `"UNKNOWN"`, the count inflates again. **Mitigation**: the 90-day window is small enough that a manual re-audit once per quarter catches the drift; document the check in the Req 13 integration test to fail loudly if `analyst_firm` ever arrives as a non-empty sentinel without a real firm name on a known multi-analyst stock (AAPL).
- **Risk — G6 batched-response handling regressions**: a future refactor re-introduces positional indexing. **Mitigation**: the `_index_by_symbol` helper is the single entry point; a unit test asserts it maps correctly and a source-text check (grep for `\.results\[[0-9]+\]`) in the Req 13 suite catches positional lookups.

## References

- `docs/tasks/todo/sector-stock-screener/requirements.md` — requirements body.
- `docs/tasks/todo/sector-stock-screener/gap-analysis.md` — the primary input to this research phase; the four R-items (R1, R2, R5, R6) are its deferred questions.
- `scripts/sector_score.py` — precedent for z-score / rank / composite build, and source of the helpers imported verbatim.
- `scripts/entry_timing_scorer.py` — precedent for dataclass-layered pipeline, `_zscore_min_basket`, `DATA_QUALITY_FLAGS` enumeration, `LastPriceResolution` three-rung chain, `append_quality_flag` validation.
- `scripts/_schema.py` — `classify_metric_unit`, `METRIC_UNIT_MAP`, `DECIMAL_RATIO_FIELDS` — referenced for the FMP metric-field alias discussion (N1).
- `docs/steering/structure.md` §"Organization Philosophy" — the "thinner the wrapper, the better" rule that governs the import-pattern decision.
- `docs/steering/tech.md` §"Key Libraries / External Services" — FMP provider usage guidelines.
- Fidelity Investments, "Business Cycle Framework" (referenced in requirements-body "Analytical stance") — 90-day analyst revision window justification.
- AQR Capital Management, Asness/Moskowitz/Pedersen, "Value and Momentum Everywhere" (2013) — cross-factor weight discussion for top-level sub-score defaults.
- Asness/Frazzini/Pedersen, "Quality Minus Junk" (2019) — quality factor definition rationale.
- Damodaran, "Data: EV/EBITDA by Sector" (NYU Stern, 2026-01) — the dispersion datum justifying Req 7.1's sector-neutral treatment (Software ~30× vs Energy ~5×).
- Live FMP probes 2026-05-01 (this research phase):
  - `obb.equity.estimates.consensus(symbol="AAPL,MSFT,NVDA", provider="fmp")` — R1, N2.
  - `obb.equity.estimates.price_target(symbol="AAPL", provider="fmp", limit=200)` — N2 analyst-count derivation.
  - `obb.etf.holdings(symbol="XLE", provider="fmp")` — R5.
  - `obb.equity.fundamental.metrics(symbol="XOM", provider="fmp")` — N1.
  - `obb.equity.fundamental.ratios(symbol="XOM", provider="fmp", period="annual", limit=1)` — N1.
  - `obb.equity.price.quote(symbol="XOM", provider="fmp")` — FMP quote field confirmation.
  - `obb.equity.price.historical(symbol="XOM", provider="fmp", start_date="2025-10-01")` — FMP historical shape confirmation (Clenow input).
