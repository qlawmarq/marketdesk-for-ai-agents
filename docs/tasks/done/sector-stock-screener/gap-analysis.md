# Gap Analysis — sector-stock-screener

## Summary

- **Scope fit**: natural extension of the existing thin-wrapper pattern. Every data primitive is already live (`etf.holdings`, `quote`, `fundamentals --type metrics`, `technical.clenow`, `estimates --type consensus`, `sector_score` scoring). **The wrapper is pinned to FMP as its single provider** (Req 2.1): since `etf.holdings` mandates FMP Starter+ anyway, consolidating the other four endpoints (`quote`, `metrics`, `clenow` historical, `consensus`) on FMP removes provider-dispatch code with zero functional loss, and the user already has a Starter+ key provisioned.
- **Net-new code**: (a) **sector-neutral z-score** — `sector_score.zscore` only does basket-wide normalization; a `groupby(gics_sector)` variant is required (Req 7.1, 7.7). (b) **Sector selection step inside the wrapper** — replicating `sector_score.build_scores`'s rank output from FMP-sourced inputs. (c) **GICS sector tagger** — `sector_origins[0]` → GICS mapping table for `sector-spdr` (XLK→`Information Technology`, etc.); non-sector universes (`theme-ark`, `global-factor`) tag `gics_sector=null` and fall through to basket-wide z per Req 7.7. (d) **Thin FMP-native performance fetcher** — a ~30-LoC helper that fetches `obb.equity.price.historical(provider="fmp")` and computes 3m/6m/12m/vol_month on the returned close series (pattern identical to `sector_score._compute_performance_from_history` but provider-pinned to FMP).
- **Primary risks**: (1) multi-ticker batch support in `obb.equity.estimates.consensus(provider="fmp")` (Req 14.4) needs live verification; (2) sector-neutral fallback threshold (n<3) is correct mathematically but its interaction with `sector_score.py`'s n≥2 pattern invites inconsistency → resolved inline via a private `_zscore_min_basket(values, min_basket=3)`.
- **Recommendation**: create `scripts/sector_stock_screener.py` that imports **only the pure helpers** from `sector_score.py` (`UNIVERSES`, `zscore`, `rank_desc`, `build_scores`, `_classify_ticker_failure`). `sector_score.fetch_performance` / `fetch_clenow` are **not** imported because they hard-code finviz + yfinance; the wrapper writes its own FMP-native fetch layer (slim — all primitives are single-call). Pattern-reuses `entry_timing_scorer.py`'s dataclass-layered pipeline. Size: **S–M (2–5 days)** — smaller than initial estimate because FMP-only eliminates ~150 LoC of provider-dispatch scaffolding. Risk: **Low–Medium** (sector-neutral z is the only genuinely new math; FMP batching is one live probe).

## Document Status

Gap analysis generated against the approved-but-not-signed requirements.md (`approvals.requirements.approved=false`). Findings are informational; proceed to `/sdd-spec-design sector-stock-screener` when ready.

---

## 1. Current State Investigation

### Assets directly reusable

| Asset | Location | Usage |
| --- | --- | --- |
| `UNIVERSES` map (`sector-spdr`, `theme-ark`, `global-factor`, `jp-sector`) | `scripts/sector_score.py:67-72` | Req 1.1 — import as-is; reject `jp-sector` via argparse-layer validation (Req 1.5). |
| `zscore`, `rank_desc`, `build_scores`, `_classify_ticker_failure` (pure helpers) | `scripts/sector_score.py:329-491` | Req 3.1 — import verbatim. **`fetch_performance` and `fetch_clenow` are NOT imported** because they hard-code finviz + yfinance; this wrapper writes an FMP-native replacement (see "FMP-native fetch layer" below). |
| `obb.etf.holdings(provider="fmp")` | `scripts/etf.py:34-44` | Req 4.1 — direct call under `safe_call`, per-sector failure isolation. |
| FMP-native performance fetcher | pattern from `sector_score._compute_performance_from_history` | Req 3.1 — ~30 LoC inline helper: call `obb.equity.price.historical(symbol, start_date, provider="fmp")`, compute 3m/6m/12m returns and monthly vol from the close series, feed into imported `build_scores`. |
| FMP-native Clenow fetcher | pattern from `sector_score.fetch_clenow` | Req 5.3 — ~20 LoC inline helper: same as existing `fetch_clenow` body but `provider="fmp"`. |
| Quote field names (FMP fixed: `ma200`, `ma50`) | `scripts/entry_timing_scorer.py:557-563` | Req 5.1 — **no provider-aware map needed**. Read `ma200` / `ma50` / `last_price` / `prev_close` / `year_high` / `year_low` directly from the FMP quote record. |
| Last-price fallback chain (2 rungs) | `scripts/entry_timing_scorer.py:622-690` | Req 5.6 — two-rung form `quote.last_price → quote.prev_close → null`; reuse the `LastPriceResolution` dataclass pattern or inline a trivial branch. |
| `obb.equity.fundamental.metrics` with unit-tagging | `scripts/_schema.py::classify_metric_unit` | Req 5.2 — call `obb.equity.fundamental.metrics(provider="fmp")` under `safe_call` and apply `classify_metric_unit` directly (the pure classifier in `_schema`). Do **not** import `fundamentals.normalize_metric_records` — that would couple two non-underscore wrappers. |
| `obb.equity.estimates.consensus(provider="fmp")` | `scripts/estimates.py:32-48` | Req 5.4 — Req 14.4 asks for batched (`symbol="A,B,C"`); need live probe on FMP. Fall-back to per-symbol loop is acceptable. |
| `aggregate_emit` with `query_meta` + `extra_warnings` | `scripts/_common.py:363-405` | Req 9.1, 9.2, 9.3, 9.4 — direct fit; assembler pattern is precisely `entry_timing_scorer.run_pipeline`'s shape. |
| Closed `DATA_QUALITY_FLAGS` frozenset + `append_quality_flag` | `scripts/entry_timing_scorer.py:78-96, 1546-1561` | Req 10.7 — copy the pattern with the Req 10.7 enumeration verbatim. |
| Envelope contract test (`test_json_contract.py`) | `tests/integration/test_json_contract.py` | Req 9.1, 9.7 — auto-discovers new wrappers; no hand-wiring needed. |
| Integration test scaffolding (`run_wrapper_or_xfail`, `assert_stdout_is_single_json`, `_sanity.assert_finite_in_range`) | `tests/integration/conftest.py`, `tests/integration/_sanity.py` | Req 13 — direct fit; skip-on-missing-FMP mirrors existing `test_etf.py` pattern. |
| Verification-gate (README §1-1 ↔ `scripts/*.py` equality) | `tests/integration/test_verification_gate.py` | Req 13.9 — new wrapper triggers README update and `Verified` column pointer in the same PR (ChangeBundleRule). |

### Conventions to respect

- **Flat `scripts/`; no subdirectory.** New file: `scripts/sector_stock_screener.py`.
- **Underscore-prefixed helpers only for cross-wrapper utility.** Per-wrapper helpers stay inside the wrapper file, dataclass-layered like `entry_timing_scorer.py`. Do not speculatively promote new helpers into `_common.py` until at least a third caller appears.
- **Single-provider pin is explicit.** The wrapper does **not** expose `--provider`. Req 2.2 startup check gates on `FMP_API_KEY` presence; Req 2.3 / 2.4 gate on per-call `error_category`. `--help` and SKILL.md state "FMP Starter+ required" up front.
- **Every OpenBB call guarded by `safe_call`; stdout absorbed via `silence_stdout` (called transitively).**
- **SKILL.md in the same PR.** `skills/sector-stock-screener/SKILL.md` + `skills/INDEX.md` row under **Composite Skills** or a new "Screeners" subsection — the exact grouping is a Design decision.
- **No relative imports.** `from sector_score import UNIVERSES, zscore, rank_desc, build_scores, _classify_ticker_failure` (flat `sys.path` root).

### Integration surfaces

- **Provider routing**: none — every OpenBB call is `provider="fmp"` literal. No `--provider` CLI flag.
- **Startup credential check**: read `os.environ.get("FMP_API_KEY")` before building the ScorerConfig; absent → `emit_error("FMP_API_KEY is required for sector-stock-screener (FMP Starter+)", tool="sector_stock_screener", error_category="credential")`, exit 2. Matches the fail-fast pattern in `entry_timing_scorer.build_config`.
- **Error category map**: `_common.ErrorCategory` already covers `{credential, plan_insufficient, transient, validation, other}`; no new category needed.
- **Exit-code gate**: `aggregate_emit` handles "every row same fatal category → exit 2" for free (Req 2.4, Req 9.4). For the narrower case "every selected sector failed on `etf.holdings`" (Req 4.5 / Req 2.4), we need an explicit pre-aggregate gate because the per-stock rows never materialize — use `emit_error(..., tool="sector_stock_screener", error_category="plan_insufficient")` as in `entry_timing_scorer`'s `_ConfigError` path.

---

## 2. Requirement-to-Asset Map

Legend: **Reuse** (lift existing primitive), **New** (novel code in this wrapper), **Constraint** (architectural rule that shapes the solution), **Research** (open question to resolve in design).

| Requirement | Primary Asset | Status | Notes |
| --- | --- | --- | --- |
| **Req 1** CLI + universe resolution | `sector_score.UNIVERSES` | Reuse | Add `jp-sector` validation-rejection (one extra branch in argparse post-validation). |
| **Req 2** FMP single-provider + credential gate | startup env check + `safe_call` + `aggregate_emit` fatal gate | Reuse / New | Startup `FMP_API_KEY` check is **New** (~5 LoC). No `--provider` choices, no provider router. All-`etf.holdings`-fail gate is the `aggregate_emit` fatal path (Req 2.3). |
| **Req 3** Sector ranking | `sector_score.build_scores` + FMP-native perf fetcher | Reuse / New | Import `build_scores`, `zscore`, `rank_desc` verbatim. Write a thin FMP-native `fetch_performance_fmp(tickers)` (~30 LoC) replacing the finviz path. |
| **Req 3.5** `--weight-sector-*` flags | Same argparse surface as `sector_score.py` | Reuse | Wire identical flag names and defaults; pass dict through unchanged. |
| **Req 4.1** `etf.holdings` per sector | `obb.etf.holdings(provider="fmp")` | Reuse | Direct call via `safe_call`, per-sector failure isolation. |
| **Req 4.3** Universe definition ≠ ranking | n/a | Constraint | Documented negative invariant — one truthy assert in the integration test. |
| **Req 4.4** Dedup + `sector_origins[]` | n/a | New | Simple dict-merge; retain first-seen row and append to list. |
| **Req 4.6** `etf_holdings_updated_max_age_days` | FMP `updated` field | New | Parse ISO date, compute `(today - min(updated)).days`, emit at `data` level. Format confirmed live in research phase (R5). |
| **Req 4.7** Fewer than 3 tickers fallback | n/a | New | All z-scores to null + top-level warning via `aggregate_emit`'s `extra_warnings`. |
| **Req 5.1** FMP quote field names | n/a | Reuse | Read `ma200` / `ma50` / `year_high` / `year_low` / `last_price` / `prev_close` directly. No provider-aware map. |
| **Req 5.2** `fundamentals --type metrics` | `_schema.classify_metric_unit` | Reuse | Call `obb.equity.fundamental.metrics(provider="fmp")` under `safe_call`; apply `classify_metric_unit` from `_schema` directly. Do not couple to `fundamentals.normalize_metric_records`. |
| **Req 5.3** Clenow period=90 | pattern from `sector_score.fetch_clenow` | New (thin) | Copy the body, pin `provider="fmp"`, inline. |
| **Req 5.4** Batched consensus | `obb.equity.estimates.consensus(provider="fmp")` | **Research** | R1 — live probe whether FMP accepts `symbol="A,B,C"` batching. Fall-back to per-symbol loop is acceptable. |
| **Req 5.6** Last-price fallback | `entry_timing_scorer.LastPriceResolution` pattern | Reuse | Two-rung form (`quote.last_price → prev_close → null`). |
| **Req 5.7** `gics_sector` tagging | n/a | New | Static map `ETF → GICS sector` for `sector-spdr`. `theme-ark` / `global-factor` emit `gics_sector=null` and fall through to basket-wide z (now pinned in Req 5.7 last sentence, no longer a research item). |
| **Req 6.x** Derived indicators | `entry_timing_scorer.compute_range_pct_52w`, `compute_ma200_distance` | Reuse | Copy or reimplement (same math, trivial). `ev_ebitda_yield` and `target_upside` are new but trivial. |
| **Req 6.4 (threshold=5)** | constant | Reuse | MVP fixes the analyst-coverage threshold at 5; no CLI flag. |
| **Req 7.1** Sector-neutral z-score | `sector_score.zscore` (basket-wide only) | **New** | Add `_sector_neutral_zscore(values, sector_tags)` — group by `sector_tags`, z-score within each group with `min_basket=3` fallback to basket-wide (Req 7.7). |
| **Req 7.2** Basket-wide z for momentum / range / ma200 / upside | `sector_score.zscore` | Reuse | As-is; n≥2 gate in `sector_score.zscore` is fine but Req 7.8 wants n<3 → null, so **tighten to n≥3** for this wrapper. See Research Item R2. |
| **Req 7.3** Sub-score composition | `entry_timing_scorer._weighted_subscore` | Reuse | Copy — sum-of-available-weights is the exact pattern. |
| **Req 7.4** Sub-score internal weights | constants | Reuse | Fixed literature-anchored defaults; echoed to `data.weights`. No CLI flags. |
| **Req 7.5** `clip(50 + z*25, 0, 100)` transform | `sector_score.to_100` / `entry_timing_scorer._to_100` | Reuse | Same math; inline helper. |
| **Req 7.6** Top-level composite weights | argparse | New | Four new flags (`--weight-sub-momentum`, `--weight-sub-value`, `--weight-sub-quality`, `--weight-sub-forward`). |
| **Req 7.9** `z_<field>_sector_neutral` / `_basket` tagging | n/a | New | Naming convention lives entirely inside this wrapper. |
| **Req 7.10** `basket_size`, `sector_group_size`, `basket_size_sufficient` | `entry_timing_scorer._compute_basket_size` | Reuse | Extend to also emit `sector_group_size`. |
| **Req 8.x** Ranking (no truncation) | `sector_score.rank_desc` | Reuse | No `--max-output` flag; emit every resolved row. Callers slice with `jq '.data.results[:N]'`. |
| **Req 9.x** Envelope contract | `aggregate_emit` + `test_json_contract.py` | Reuse | No new code; automatic. |
| **Req 10.x** Per-ticker schema | n/a | New | Assembler function modelled on `entry_timing_scorer.build_ok_row`. |
| **Req 10.4** Negative invariant `buy_signal`/`recommendation` absent | n/a | Constraint | Enforced by construction + integration-test field-absence assertion (Req 13.4). |
| **Req 10.6** `data.analytical_caveats` | `entry_timing_scorer.ANALYTICAL_CAVEATS` | Reuse | Copy pattern; five literal strings per Req 10.6. |
| **Req 10.7** Closed `DATA_QUALITY_FLAGS` enum | `entry_timing_scorer.DATA_QUALITY_FLAGS` | Reuse | Copy pattern with the Req 10.7 catalog. |
| **Req 11.x** Scope boundaries | n/a | Constraint | Mostly negative invariants; no code beyond "don't add that feature". |
| **Req 12.x** SKILL.md + INDEX update | `skills/entry-timing-scorer/SKILL.md` | Reuse | Template; ChangeBundleRule enforces same-PR delivery. |
| **Req 13.x** Integration test | `tests/integration/test_entry_timing_scorer.py` | Reuse | Template; auto-skip when `FMP_API_KEY` absent mirrors `test_etf.py`. |
| **Req 14.1** Batched consensus | `obb.equity.estimates.consensus(provider="fmp")` | **Research** | R1 — one-line live probe; fall-back to per-symbol loop is acceptable. |

**Tagged gaps:**
- **R1 (Missing → Research)**: batch support of `obb.equity.estimates.consensus` under yfinance.
- **R2 (Constraint → Research)**: min-basket threshold — `sector_score` uses n≥2, `entry_timing_scorer` uses n≥3. Req 7.8 requires n≥3 for this wrapper; confirm this is compatible with importing `sector_score.zscore` (it is not — `sector_score.zscore` gates at n≥2). See §3 for resolution options.
- **R3 (Missing → Research)**: GICS sector mapping for `theme-ark` / `global-factor` universes. Req 5.7 leans on "SPDR sector ETFs map one-to-one to GICS sectors" — unambiguous for `sector-spdr` (11 funds, 11 GICS sectors). For `theme-ark` (ARKK, SMH, KWEB…) and `global-factor` (QUAL, MTUM, VLUE…) the concept of "GICS sector" does not cleanly apply. Does the wrapper fall back to basket-wide z for every value/quality factor in those universes? Design decision.
- **R4 (Unknown → Research)**: should `sector_score.py`'s scoring primitives be promoted to `_sector_scoring.py` to avoid `scripts/sector_stock_screener.py` importing from another non-underscore script? Promotion adds churn to `sector_score.py`'s tests; inline import adds a light coupling. See §3 Option A vs B.

---

## 3. Implementation Approach Options

### Option A (recommended): Import pure helpers from `sector_score.py`, write FMP-native fetch layer

**Summary**: New file `scripts/sector_stock_screener.py` that does `from sector_score import UNIVERSES, zscore, rank_desc, build_scores, _classify_ticker_failure`. Writes its own FMP-native performance + Clenow fetchers (thin — each ~20–30 LoC) because the existing `sector_score.fetch_performance` / `fetch_clenow` hard-code finviz + yfinance. Adds `_zscore_min_basket(values, min_basket=3)` inline to resolve Req 7.8's stricter threshold (R2). Adds `_sector_neutral_zscore(values, sector_tags)` inline for Req 7.1. Layers the wrapper like `entry_timing_scorer.py`.

**Files changed:**
- `scripts/sector_stock_screener.py` (new, estimated ~650–850 LoC — smaller than initial estimate because FMP-only eliminates provider-dispatch scaffolding)
- `skills/sector-stock-screener/SKILL.md` (new)
- `skills/INDEX.md` (new row)
- `README.md` §1-1 matrix row + `Verified` pointer
- `tests/integration/test_sector_stock_screener.py` (new)
- `scripts/sector_score.py` — **zero change**

**Trade-offs:**
- ✅ Matches the "thinner the wrapper, the better" principle in `structure.md`.
- ✅ No churn to `sector_score.py` → existing test surface untouched.
- ✅ FMP-only further slims the code (no `_QUOTE_FIELD_MAP`, no provider router, no `--provider` flag, simpler last-price fallback).
- ❌ Two sector-performance fetch paths coexist (finviz-based in `sector_score.py`, FMP-based here). Acceptable because they serve different wrappers with different provider contracts; DRY-ing would force one wrapper into the other's provider assumptions.

### Option B (not recommended here): Refactor into `scripts/_sector_scoring.py`

Extract the pure helpers into an underscore module first. Cleaner dependency graph, but pure upside is structural — no functional change. Revisit only if a third sector-screener surfaces.

### Option C (strictly worse): Inline-copy all helpers

Duplicates ~100 LoC of z-score / rank math. Two sources of truth for the composite formula. Rejected.

### R2 resolution

Define a private `_zscore_min_basket(values, min_basket=3)` inline. Do **not** modify `sector_score.zscore`'s n≥2 threshold because `sector_score.py`'s own tests rely on it. Identical pattern to `entry_timing_scorer._zscore_min_basket` (`scripts/entry_timing_scorer.py:1250-1267`).

---

## 4. Research Needed — investigate in the research phase (`/sdd-spec-research`)

The four items below are **explicitly deferred to the research phase** per `gap-analysis.md`'s "Information over Decisions" principle. Each carries a concrete probe command or decision axis so the research-phase output can close it with a single live check or a short written trade-off. None is a blocker for requirements sign-off; they shape the design phase, not the requirements text. (The earlier R3 / R4 items were resolved in the FMP-only pivot and are no longer open: R3 is now pinned by Req 5.7 last sentence; R4 is now pinned by the asset map to `_schema.classify_metric_unit`.)

- **R1 — batched consensus on FMP** *(investigate in research phase; live probe)*: verify `obb.equity.estimates.consensus(symbol="AAPL,MSFT,NVDA", provider="fmp")` returns one row per symbol in a single round-trip. If yes, implement as one call (Req 14.4). If no, accept O(N) per-symbol loop and document the call-count exception under `analytical_caveats`. Probe: run `uv run python -c "from _env import apply_to_openbb; from openbb import obb; apply_to_openbb(); print(obb.equity.estimates.consensus(symbol='AAPL,MSFT,NVDA', provider='fmp').to_df())"` from the `scripts/` directory.
- **R2 — min-basket threshold reconciliation** *(investigate in research phase; design trade-off)*: confirm inline `_zscore_min_basket` with `min_basket=3` is the right resolution (same stance `entry_timing_scorer.py` took). Alternative: expose `min_basket` as a parameter on `sector_score.zscore` and pass `3` from this wrapper — but that requires touching `sector_score.py`'s test surface. Preferred default is inline; research-phase output should either ratify or override with justification.
- **R5 — FMP `etf.holdings` `updated` field format** *(investigate in research phase; live probe)*: Req 4.6 requires an `etf_holdings_updated_max_age_days` integer. Need to confirm `updated` comes back as an ISO date string (vs. epoch, vs. datetime) to lock the parse. Probe: `uv run scripts/etf.py XLE --type holdings | jq '.data.results[0].records[0].updated'`.
- **R6 — `sector_origins[]` multi-sector flag scoping** *(investigate in research phase; requirements clarification)*: Req 10.7 catalog includes `"stock_appears_in_multiple_top_sectors"` but the requirements body (Req 4.4) does not cite this flag. Research-phase output should either add the citation to Req 4.4 or remove the entry from the Req 10.7 catalog; both sides must agree.

---

## 5. Effort & Risk

- **Effort**: **S–M (2–5 days)**. Justification: wrapper structure and >80% of the primitives are precedented; novel surface is ~200 LoC (sector-neutral z, GICS mapping, FMP-native perf/clenow fetchers, two assembler helpers, integration test). FMP-only pin removes the ~150 LoC of provider-dispatch scaffolding that `entry_timing_scorer.py` carries.
- **Risk**: **Low–Medium**. Justification: sector-neutral z is the only genuinely new math, and it has a clean degenerate-basket fallback already spelt out in Req 7.7. FMP batching (R1) is one live probe; fall-back to per-symbol loop is acceptable. Every other path is either imported verbatim from `sector_score.py` or pattern-copied from `entry_timing_scorer.py`.

---

## Next Steps

1. Run **`/sdd-spec-research sector-stock-screener`** to close R1 / R2 / R5 / R6. R1 and R5 are one-line live probes against FMP; R2 and R6 are written decision items. The research phase is the correct place to resolve them — gap analysis deliberately stops at flagging them.
2. Run `/sdd-spec-design sector-stock-screener` after research completes to convert the Option-A shape plus the R1 / R2 / R5 / R6 resolutions into a full technical design.
