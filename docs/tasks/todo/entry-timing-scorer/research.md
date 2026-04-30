# Research & Design Decisions — entry-timing-scorer

---
**Purpose**: Capture discovery findings, architectural investigations, and rationale that inform the technical design of `scripts/entry_timing_scorer.py`.

**Usage**:
- Log research activities and outcomes during the discovery phase.
- Document design decision trade-offs that are too detailed for `design.md`.
- Provide references and evidence for future audits or reuse.
---

## Summary

- **Feature**: `entry-timing-scorer`
- **Discovery Scope**: Extension (light discovery). A new wrapper under `scripts/` that composes primitives already covered by `quote.py`, `historical.py`, `momentum.py`, and `calendars.py`, with `sector_score.py` as the architectural precedent. No new OpenBB endpoints, no new providers, no shift in the envelope contract.
- **Key Findings**:
  - Every data primitive is already live. The single-fetch-feeds-three-technicals pattern is already implemented in `scripts/momentum.py::_indicator_call` and `scripts/sector_score.py::fetch_clenow` (both pass `data=history.results` into `obb.technical.{clenow,rsi,macd}`), which pins the per-ticker call budget at 1 quote + 1 historical + 3 technical = 5 calls and satisfies Requirement 13.3.
  - The novel schema surface is localized and low-risk: `context`, `interpretation` (object), `data_quality_flags[]`, `analytical_caveats` (constant tuple under `data`), `basket_size` / `basket_size_sufficient`, the robust (log + MAD) volume-z estimator, and the conditional omission of `blended_score_0_100` under `--blend-profile none`. None of these cross module boundaries beyond `scripts/entry_timing_scorer.py` and one new integration-test file.
  - The one genuinely new external surface is YAML parsing for `--portfolio-file`. The repo has no direct `yaml` dependency, no existing `portfolio.yaml` on disk, and no tests that already exercise YAML. The design must pick the dependency stance explicitly (see Decision 1). **Live verification confirms `pyyaml 6.0.3` is already installed transitively via OpenBB.**
  - **Live verification (2026-04-30) surfaced three findings that modify the design** (full details in §Live Verification below): (a) Requirement 3.1's "single `TODAY+90d` nasdaq earnings fetch" is **not feasible on the current nasdaq provider** — the server returns `AttributeError: NoneType.get` at ~55 days and 403 at ~84+ days; a reliable single-shot ceiling is ~45 days. (b) `yfinance` quote returns `last_price=None` for bond ETFs (TLT / LQD observed); the wrapper needs a documented fallback chain (`last_price → prev_close → historical close`). (c) `obb.technical.clenow` returns `factor` as a **string** ("0.63826"), not a float; `obb.technical.macd` outputs `close_MACDh_12_26_9` (not `histogram`); the wrapper must normalize both.

## Research Log

### Topic 1 — `obb.technical.{rsi,macd}` accept a pre-fetched history via `data=`

- **Context**: Requirement 4 caps the per-ticker budget at five equity/technical calls. If `obb.technical.rsi` and `obb.technical.macd` each demanded their own `obb.equity.price.historical` fetch, the budget would triple (1 quote + 3 × historical + 3 technical = 7 calls/ticker; well past the Req 13.3 implication of "≤5"). This had to be verified, not assumed.
- **Sources Consulted**:
  - `scripts/momentum.py::_indicator_call` (lines 89–126). All five supported indicators (`clenow`, `rsi`, `macd`, `cones`, `adx`) accept `data=history.results` drawn from a single `obb.equity.price.historical` call.
  - `scripts/sector_score.py::fetch_clenow` (lines 279–326). Production code already uses this pattern for Clenow; the history fetch happens once and is fed into `obb.technical.clenow(data=hist.results, target="close", period=period)`.
- **Findings**:
  - The installed OpenBB version accepts `data=list[dict]` (the shape of `history.results`) for `clenow`, `rsi`, and `macd`. One fetch suffices for all three.
  - `momentum.py` computes each indicator's own lookback heuristic (e.g., `rsi` uses `args.length * 5 + 30` days); the entry-timing scorer can instead pick a single bounded lookback large enough to satisfy the strictest consumer. Requirement 4.2's "≥140 trading days" picks up `126` (Clenow) + a warm-up buffer; in calendar days that is roughly 200.
- **Implications**:
  - Per-ticker call sequence: (1) `obb.equity.price.quote`, (2) `obb.equity.price.historical(start=today-210d)`, (3) `obb.technical.clenow(data=hist.results, period=126)`, (4) `obb.technical.rsi(data=hist.results, length=14)`, (5) `obb.technical.macd(data=hist.results, fast=12, slow=26, signal=9)`. Five calls, matching the Req 13.3 cap.
  - The design phase should fix the historical lookback at module scope (a constant, not a CLI flag) so the "≥140 trading days" invariant cannot drift by accident.

### Topic 2 — Cross-sectional z-score under small baskets (n = 5–10)

- **Context**: With only 5–10 tickers in a daily basket, a z-score collapses to a monotonic function of within-basket rank; the standard deviation becomes noisy, and absolute z values do not carry "strength" information in any external sense. Requirement 6.7 and Requirement 9.9 both hinge on this.
- **Sources Consulted**:
  - `scripts/sector_score.py::zscore` (lines 329–338). Current implementation returns `[None] * n` when `len(clean) < 2`, and emits `0.0` for every element when the standard deviation is zero. It does not surface a data-quality flag.
  - Requirement 6.7 / 6.8: basket < 3 on a given signal → `null` z for that signal and a per-row data-quality flag `basket_too_small_for_z(<signal>)`; whole basket < 3 → `null` scores + top-level warning.
- **Findings**:
  - The existing `zscore` threshold (`< 2`) is one below the threshold the entry-timing requirements demand (`< 3`). The difference is not cosmetic: with n=2 a z-score is ±1 by construction and carries no ranking information. Raising the floor to 3 materially improves the "rank summary, not absolute strength" reading.
  - A single `zscore(values, min_basket=3)` helper cleanly covers both the per-signal (Req 6.7) and whole-basket (Req 6.8) paths: per-signal passes the signal's present-value count, whole-basket passes the resolved-row count. No separate gate function is needed.
- **Implications**:
  - The design should implement a local `zscore` helper with a `min_basket` parameter defaulting to 3, and compute `basket_size` per signal (the count of non-null inputs at normalization time). `basket_size_sufficient` on the per-ticker row can then reflect the majority-signal count or the row's own success state — pick one explicitly in design.
  - The `analytical_caveats` constant must be emitted at module scope and tested for presence; it is not merely documentation (Req 9.9 + Req 12.6 lock it in).

### Topic 3 — Robust volume-z estimator (log + MAD) edge cases

- **Context**: Requirement 5.4 introduces a `--volume-z-estimator` flag with `robust` as the default. The robust estimator uses `(log(latest_volume) - median(log(volume[-20:]))) / (1.4826 * MAD(log(volume[-20:])))`. Zero-volume halt days, JP market closures, and missing data break the log transform; Requirement 5.5 enumerates the expected flags (`volume_non_positive`, `volume_zero_dispersion`, `volume_window_too_short`). No existing wrapper implements a log-MAD estimator, so the concrete edge-case behavior needs a pinned policy before the design phase.
- **Sources Consulted**:
  - Requirement 5.5 enumerates three flags: `volume_window_too_short`, `volume_zero_dispersion`, `volume_non_positive`.
  - `scripts/sector_score.py::zscore` and the `stdev == 0` branch (line 336) — the zero-dispersion guard is already idiomatic in this codebase.
  - Python stdlib: `statistics.median` (3.4+), `math.log` (raises `ValueError` on `<= 0`). No numpy dependency is required for the estimator itself; the historical response is already ingested as a list of dicts by `to_records`.
- **Findings**:
  - The robust estimator's three failure modes map cleanly onto the three flags:
    - window rows < 21 → `volume_window_too_short` (Req 5.5, shared with classical path).
    - any `volume[-20:]` entry ≤ 0 → `volume_non_positive` (robust only; classical can still compute mean/stdev on non-positive values, so this flag fires only when `--volume-z-estimator robust` is active).
    - `MAD == 0` (all log-volumes identical) → `volume_zero_dispersion` (shared with classical `stdev == 0`).
  - `1.4826 * MAD` is the standard consistency constant that makes MAD an unbiased estimator of σ for Gaussian data. Using `log(volume)` rather than raw volume compresses the right-tail outliers that contaminate trading-volume distributions (closing auctions, options-expiry days), which is the stated purpose of the robust option.
- **Implications**:
  - Design must (a) decide which of the three flags takes precedence when multiple apply (recommend: check `window_too_short` first, then `non_positive`, then `zero_dispersion` — narrowest condition wins), (b) emit `volume_z_estimator` at the per-ticker level so integration tests can assert the two-run estimator switch (Req 12.8) without log-diving, and (c) keep the estimator math inline (no new helper module) until a second wrapper needs it.

### Topic 4 — `--portfolio-file` YAML: dependency stance

- **Context**: Requirement 1.2 mandates YAML parsing of `portfolio.yaml`'s `positions[].ticker` and `watchlist[].ticker`. `pyproject.toml` has no `yaml` dep today, `rg 'import yaml'` across `scripts/` / `skills/` / `tests/` returns nothing, and `find` for `portfolio.yaml` / `portfolio.yml` returns nothing in the repo. The Problem statement and the requirement-side "Constraints" language conflict: the Constraints say MVP does not interpret `portfolio.yaml`, while Req 1.2 mandates parsing the ticker subset. Analysis Research Item #1 flagged this explicitly.
- **Sources Consulted**:
  - `pyproject.toml` (lines 6–10): dependencies are `openbb>=4.4.0`, `openbb-cli>=1.1.0`, `python-dotenv>=1.0.0`. No yaml.
  - `requirements.md` Constraints line: "portfolio.yaml の解釈: MVP では portfolio.yaml の解析・トリガー照合は **行わない**" — the constraint is about trigger matching, not ticker extraction (ticker extraction is then explicitly re-permitted in the same bullet as a "convenience feature").
  - Requirement 1.2 (parse `positions[].ticker` + `watchlist[].ticker`) and Requirement 10.1 (do not parse beyond those fields). The intent is clearly narrow: `--portfolio-file` is a ticker source, not an orchestration input.
  - `openbb` transitively depends on `pyyaml` (via `openbb-core`'s config layer). `python -c "import yaml; print(yaml.__version__)"` resolves on a synced env. Pulling `pyyaml` into the direct dep list carries a near-zero additional install cost.
- **Findings**:
  - Three real options survive:
    - (a) add `pyyaml` as a direct dependency, use `yaml.safe_load`. Clean, standard, and honest about the transitive dependency.
    - (b) drop `--portfolio-file` from MVP. Contradicts Req 1.2 — requires a requirements change.
    - (c) implement a narrow stdlib parser for `positions[].ticker` / `watchlist[].ticker` only. Fragile to YAML features (multi-doc streams, anchors, quoting styles, comments), re-introduces maintenance burden the analyst agent is explicitly trying to escape, and fails on the first non-trivial portfolio.yaml.
  - The transitive-dep observation resolves the friction: `pyyaml` is already installed into the env via OpenBB, so declaring it directly is **documentation**, not a new install footprint.
- **Implications**:
  - Decision 1 below: add `pyyaml` to `[project].dependencies`. Use `yaml.safe_load` (never `yaml.load`) to keep code-execution vectors closed. The SKILL.md must state the narrow contract (only `positions[].ticker` and `watchlist[].ticker` are read; all other fields are ignored) so future portfolio.yaml schema extensions do not create silent coupling.

### Topic 5 — Earnings-calendar single-shot fetch and client-side filtering

- **Context**: Req 3 says exactly one `obb.equity.calendar.earnings` call per invocation regardless of ticker count, and the response must be filtered client-side on the input-ticker set.
- **Sources Consulted**:
  - `scripts/calendars.py` (existing wrapper) and `skills/calendars/SKILL.md`. `obb.equity.calendar.earnings(start, end, provider="nasdaq")` returns `{report_date, symbol, eps_consensus, eps_previous, num_estimates, reporting_time, market_cap}` per row, across all tickers reporting in the window.
  - `scripts/sector_score.py::fetch_performance` (lines 203–276). Shows the `data.provider_diagnostics` pattern with `{provider, stage, error, error_category}` entries; per-stage failures surface there rather than mixing with per-row failures. The entry-timing scorer should mirror the exact shape.
  - **Live verification 2026-04-30** (see Live finding L4). Binary-search of the keyless nasdaq provider revealed a hard ceiling at ~49 days; windows ≥56d return `AttributeError: NoneType.get` and ≥84d return a process-poisoning HTTP 403. The pre-verification assumption that "`TODAY+90d` works on nasdaq" is **falsified**; requirements were amended (Req 3.1 + new Req 3.7).
- **Findings**:
  - The short-term scorer's only time-sensitive earnings check is the 5-calendar-day proximity flag (Req 7.2); a 45-day window is more than sufficient.
  - On calendar failure, Req 3.5 keeps per-ticker rows emitting with `next_earnings_date: null`, and the failure surfaces under `data.provider_diagnostics` with `stage: "earnings_calendar"`. This is a direct transplant of `sector_score.fetch_performance`'s stage-level warning pattern.
  - A single-shot fetch means "ticker with no row in window" and "calendar call failed" look identical at the per-row level unless the diagnostics channel is populated. The design must therefore always emit the `provider_diagnostics` entry on calendar failure; absence of the entry is how consumers distinguish "calendar returned no hits" from "calendar unreachable".
- **Implications**:
  - Design must reserve `data.provider_diagnostics` as a list (even when no stages failed, emit `[]` or omit the key) and keep the per-ticker row shape unconditional on calendar outcome. Integration test Req 12.5 asserts `earnings_proximity_days_threshold` under `data` — put `days_to_next_earnings_unit: "calendar_days"`, `earnings_window_days`, and the threshold in the same `data` namespace as `provider_diagnostics` for consistency.
  - The FMP re-verification called out in Req §Constraints item 1 may restore `N=90` as the default under `--provider fmp`; until then, the default stays `N=45` (Decision 7).

### Topic 6 — `interpretation` block and the negative invariant on `interpretation_hint`

- **Context**: Requirement 9.4 defines a structured `interpretation` object per ticker. Requirement 9.5 + 12.6 define a negative invariant: no row may carry a scalar field named `interpretation_hint`. No existing wrapper emits a `data.results[*].interpretation` object, so the shape has no precedent and must be fixed here.
- **Sources Consulted**:
  - Requirement 9.4: four mandatory keys — `score_meaning`, `trend_polarity`, `mean_reversion_polarity`, `context`, `reading_for_context` — plus the three `reading_for_context` string values for watchlist / holding / unknown.
  - Requirement 9.5: `interpretation_hint` is explicitly forbidden because reading depends on both `context` and which sub-score is high.
  - Requirement 12.6: integration test asserts field absence across every row.
- **Findings**:
  - The `interpretation` block is deliberately verbose — it trades schema size for reader safety. The three `reading_for_context` strings are not free text: they are the only sanctioned values and must be compared literally in the integration test.
  - The negative invariant (`interpretation_hint` absence) is easiest enforced by simply never creating the key anywhere in the wrapper. No guard helper is needed; the integration-test `for row in rows: assert "interpretation_hint" not in row` is sufficient and avoids premature abstraction. If a second wrapper in the future needs the same negative invariant, the helper can move to `tests/integration/_sanity.py` at that point.
- **Implications**:
  - Design must define the `interpretation` block as a module-scope constant builder (`_build_interpretation(context: str) -> dict[str, str]`) so the string values for `reading_for_context` are exact literals, not f-string concatenations that drift under refactoring.
  - The SKILL.md "Reading by context" subsection (Requirement 11.7) must reproduce the three strings verbatim so agents can match on them without reading the wrapper source.

### Topic 7 — `basket_size_sufficient` semantics under mixed-success rows

- **Context**: A ticker that succeeded on `quote` but failed on `historical` will have `clenow_126`, `rsi_14`, `macd_histogram`, `volume_z_20d` all null while `range_pct_52w` and `ma200_distance` are populated. Requirement 6.11 defines `basket_size` as "the count of tickers that entered cross-sectional normalization". The words "entered normalization" are ambiguous across signals when ticker success is partial.
- **Sources Consulted**:
  - Requirement 6.7: per-signal null + `basket_too_small_for_z(<signal>)` flag when that signal's basket < 3.
  - Requirement 6.8: whole-basket null + top-level warning when resolved-row count < 3.
  - Requirement 6.11: `basket_size_sufficient` boolean, true iff `basket_size >= 3`.
- **Findings**:
  - Two coherent interpretations:
    - (i) per-signal `basket_size` (one count per signal): precise, auditable, and matches the per-signal null-flag rule exactly. Costs one integer per signal per row.
    - (ii) row-level `basket_size` as the count of rows with `ok: true` that contributed at least one signal to normalization: coarser, but aligns with Req 6.11's singular noun ("basket_size") and Req 6.8's whole-basket gate.
  - The integration tests required by Req 12.x do not assert per-signal basket sizes — only the presence of the two fields and the sub-score computation. Interpretation (ii) is sufficient for the tests and the SKILL.md reader; interpretation (i) adds precision that no current consumer requires.
- **Implications**:
  - Decision 3 below: row-level `basket_size` equals the count of rows that entered *any* signal's normalization (equivalently, the count of rows that have `ok: true` and at least one non-null signal value among the five scorer inputs). `basket_size_sufficient` is a simple `basket_size >= 3`. The per-signal `basket_too_small_for_z(<signal>)` flag is emitted independently, per Req 6.7. The design doc should state this tradeoff explicitly.

### Topic 8 — Blend-profile sort stability and tied-score ordering

- **Context**: Requirement 6.9 specifies the sort key per `--blend-profile`. When two rows tie on the sort key (e.g., both `null`, or both 50.0 on a two-row basket below the z-score threshold), the secondary order is unspecified.
- **Sources Consulted**:
  - `scripts/sector_score.py::build_scores` line 441: `records.sort(key=lambda r: (r["rank"] if r["rank"] is not None else 10**9))`. Single-key sort; Python's `sort` is stable, so input order is preserved on ties. No explicit secondary key.
  - Requirement 6.9: `null` scores sink to the bottom; no tie-breaker defined.
- **Findings**:
  - Python's `sorted`/`list.sort` is guaranteed stable (CPython timsort). Preserving input order on ties is the zero-cost default.
  - Symbol-alphabetical tie-breaker is cosmetic but adds deterministic output regardless of input order — useful for snapshot-diff tests and for agent readability when the same basket is scored twice.
- **Implications**:
  - Decision 4 below: primary key per Req 6.9, secondary key `symbol` alphabetical, tertiary key preserves input order (implicit via stable sort). Document the tie-break in SKILL.md so agents do not assume input-order preservation.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| A — Extend `scripts/sector_score.py` with an `--analytical-mode` switch | One file, two modes (sector composite / entry timing) | Maximal reuse of `zscore`, `build_scores`, partial-failure scaffolding | Merges two distinct analytical stances (mid-term sector vs. short-term ticker); file already ~600 lines; leaks short-term-only concepts (`context`, `interpretation`, `analytical_caveats`) into the sector wrapper's schema | Violates single-responsibility and the thin-wrapper norm in `structure.md`; **not recommended** |
| B — New wrapper `scripts/entry_timing_scorer.py` modeled on `sector_score.py` | Sibling file; direct `obb.*` calls guarded by `safe_call`; cross-sectional z helpers; `aggregate_emit`; `data.provider_diagnostics` | Clean separation of concerns; respects flat-wrapper convention; auto-covered by `test_json_contract.py` parametrized invariants the moment the file lands under `scripts/*.py` | Some helper duplication vs. `sector_score.py` (`zscore`, `to_100` transform, lookback computation) | **Recommended**. Extracting shared helpers prematurely from a single data point (sector_score) is Option C and is a follow-up, not a Phase-1 call |
| C — Hybrid: new wrapper + shared helper module `scripts/_scoring.py` | New wrapper + factor out `zscore`, `rank_desc`, 0-100 transform into a helper consumed by both wrappers; `sector_score.py` gets a follow-up refactor | Removes duplication at the z-score layer immediately | Two-file change to a production wrapper with a stable test suite enlarges blast radius for a single new feature; entry-timing's `zscore(min_basket=3)` differs subtly from sector_score's `zscore(min_basket=2)`, so premature extraction risks immediate undo | Reasonable **after** entry-timing ships and a refactor pass reveals the shared surface area; not a Phase-1 choice |

## Design Decisions

### Decision 1: `--portfolio-file` YAML parsing via `pyyaml`

- **Context**: Requirement 1.2 mandates YAML parsing of a narrow subset. Repo has no direct `yaml` dep but `pyyaml` is transitively installed via `openbb` (live verification 2026-04-30: `uv run python -c "import yaml; print(yaml.__version__)"` → `6.0.3`). Requirement 10.1 caps the parse scope at `positions[].ticker` + `watchlist[].ticker`.
- **Alternatives Considered**:
  1. Add `pyyaml` as a direct dependency; use `yaml.safe_load`.
  2. Drop `--portfolio-file` from MVP (requires a Req 1.2 amendment).
  3. Stdlib-subset hand-rolled parser (fragile against schema / quoting variations).
- **Selected Approach**: Option 1. Add `pyyaml>=6.0` to `[project].dependencies` in `pyproject.toml`. Parse via `yaml.safe_load(path.read_text())`. Extract only `positions[*].ticker` and `watchlist[*].ticker`; ignore all other keys even if present.
- **Rationale**: The install footprint is already present transitively, so declaring the dep is honest documentation rather than a new cost. `safe_load` closes the code-execution vector that plagues `yaml.load`. Narrow extraction preserves the thin-wrapper invariant (Req 10.1) and keeps portfolio.yaml schema drift from coupling silently to this tool.
- **Trade-offs**:
  - (+) Robust to YAML syntax variations (anchors, comments, multi-line strings).
  - (+) Matches user expectation — `pyyaml` is the Python ecosystem default.
  - (−) One new direct dependency. Mitigated by transitive presence.
- **Follow-up**: Verify `uv sync` installs the explicit `pyyaml` without version conflict against OpenBB's transitive pin. Document the narrow parse scope in `skills/entry-timing-scorer/SKILL.md` so future readers know other portfolio.yaml keys are deliberately ignored.

### Decision 2: Per-ticker call flow — one `historical` fetch feeds three technicals

- **Context**: Req 13.3 caps per-ticker calls at 5. Req 4 calls for `quote`, `historical`, `clenow`, `rsi`, `macd`. Without reuse, `rsi` and `macd` would re-fetch history (7 calls/ticker).
- **Alternatives Considered**:
  1. Single `historical` fetch shared across `clenow`, `rsi`, `macd` via `data=hist.results` (the pattern already in `momentum.py` and `sector_score.py`).
  2. Independent `historical` fetch per indicator (3× the quota, but isolates per-indicator failure modes).
  3. Direct pandas computation of RSI / MACD / Clenow inside the wrapper without calling `obb.technical.*`.
- **Selected Approach**: Option 1. Fetch history once per ticker at a module-scope lookback constant (≈210 calendar days, covering the 140-trading-day minimum from Req 4.2) and feed the list-of-dicts result into all three `obb.technical.*` calls.
- **Rationale**: Matches the in-repo precedent, meets the Req 13.3 budget exactly (1+1+3=5 per ticker), keeps the OpenBB technicals as the single source of truth (no risk of Clenow definition drift between wrappers).
- **Trade-offs**:
  - (+) Honors the 5-call budget naturally; no need for custom throttling.
  - (+) Single point of failure for "no history" — simpler error taxonomy.
  - (−) A single transient historical failure blocks all three technicals for that ticker. Acceptable: the three fail identically in practice anyway.
- **Follow-up**: Fix the historical lookback as a module-scope constant (`HISTORICAL_LOOKBACK_DAYS = 210` or similar) to prevent silent drift below the Req 4.2 floor.

### Decision 3: Row-level `basket_size`, per-signal flagging handled independently

- **Context**: Req 6.11 defines `basket_size` singularly. Req 6.7 requires per-signal null + flag when a signal's basket drops below 3. These could be reconciled either by per-signal or per-row counting.
- **Alternatives Considered**:
  1. Row-level `basket_size` = count of rows with `ok: true` contributing at least one signal; per-signal null + flag is computed independently at z-score time.
  2. Per-signal `basket_size` dict (five integers per row), no singular field; `basket_size_sufficient` becomes `all(counts >= 3)`.
- **Selected Approach**: Option 1. Emit `basket_size` (int) and `basket_size_sufficient` (bool) per the literal Req 6.11 shape. Per-signal small-basket flags (`basket_too_small_for_z(<signal>)`) are independently appended to `data_quality_flags[]` at z-score compute time per Req 6.7.
- **Rationale**: Literal fidelity to Req 6.11's singular noun. Per-signal precision is retained through the flag mechanism without bloating the schema. Integration tests (Req 12.x) do not inspect per-signal counts, so the extra precision has no current consumer.
- **Trade-offs**:
  - (+) Schema size bounded; one integer + one bool per row.
  - (+) Flag channel stays the authoritative per-signal diagnostic.
  - (−) Consumers cannot recover the per-signal count from the payload; they would have to re-count by scanning the flags array. Acceptable — if a future consumer needs per-signal counts, upgrade the schema at that point.
- **Follow-up**: `design.md` must state this interpretation explicitly so the SKILL.md and the integration tests do not assume per-signal counting.

### Decision 4: Sort stability — primary per Req 6.9, secondary alphabetical by symbol

- **Context**: Req 6.9 specifies primary sort key per `--blend-profile` but leaves tie-break unspecified. With null scores sinking to the bottom, ties are common (all-null rows, all-50 rows under tiny baskets).
- **Alternatives Considered**:
  1. Primary key per Req 6.9, secondary `symbol` alphabetical, tertiary input order (via stable sort).
  2. Primary key per Req 6.9, preserve input order on ties (Python stable sort default).
- **Selected Approach**: Option 1. Use a tuple sort key `(score_for_profile_or_inf_for_null, symbol)` with descending on the first element — Python's sort does not support mixed ascending/descending across keys in a single tuple, so the common idiom is to negate-the-score where meaningful (or use `key` with an explicit `reverse=True` and include a symbol-stable tie-break via a prior alphabetical sort pass).
- **Rationale**: Deterministic output regardless of input order is better for snapshot-diff tests, for SKILL.md sample output (which must remain reproducible), and for human review. The cost is one sorted-by-symbol pass before the main sort — negligible on n=5–10 baskets.
- **Trade-offs**:
  - (+) Snapshot-diffable; independent of input permutation.
  - (+) No ambiguity for agents comparing two runs of the same basket.
  - (−) Slight deviation from `sector_score.py`'s single-key sort. Acceptable; `sector_score`'s n=11 basket rarely produces ties on `composite_score_0_100`.
- **Follow-up**: Document the tie-break in SKILL.md's "Sort order" blurb so agents do not assume input-order preservation.

### Decision 5: Earnings fields kept structurally outside the composite math

- **Context**: Req 7.1 forbids `days_to_next_earnings` from contributing to trend / mean-reversion / blended scores. A string-based grep would not catch a subtle miswire; the guard rail must be structural.
- **Alternatives Considered**:
  1. Code-level whitelist: `build_scores` accepts only the explicit signal key-set `{clenow_126, macd_histogram, volume_z_20d, range_pct_52w, rsi_14}` and ignores anything else passed in.
  2. Runtime assertion at emit time that `earnings_proximity_*` keys are not in the `z_scores` dict.
  3. Documentation-only rule enforced by code review.
- **Selected Approach**: Option 1. Define a module-scope constant `SCORER_SIGNAL_KEYS = ("clenow_126", "macd_histogram", "volume_z_20d", "inv_range_pct_52w", "oversold_rsi_14")` and have the z-score / sub-score functions iterate only over that tuple. Earnings-proximity fields live in a separate per-row namespace (`next_earnings_date`, `days_to_next_earnings`, `earnings_proximity_warning`) and are never included in the `z_scores` block.
- **Rationale**: Structural invariants beat runtime checks beat doc comments. The key-set is small enough that hard-coding is clearer than any meta-programming.
- **Trade-offs**:
  - (+) A future contributor adding a new signal cannot accidentally mix earnings into the composite without consciously editing `SCORER_SIGNAL_KEYS`.
  - (+) The integration test Req 12.6 (negative invariant) has a natural code anchor to point at.
  - (−) Adding a new scorer signal requires two touches: the key-set and the per-signal computation. Acceptable.
- **Follow-up**: The `design.md` pseudocode must show the signal iteration reading from `SCORER_SIGNAL_KEYS`, not from `signals.keys()`.

### Decision 6: `analytical_caveats` as a module-scope constant tuple

- **Context**: Req 9.9 requires `data.analytical_caveats` to contain three specific strings. Req 12.6 asserts their presence in integration tests. These strings must round-trip JSON cleanly and must be identical across every response.
- **Alternatives Considered**:
  1. Module-scope constant tuple: `ANALYTICAL_CAVEATS = ("scores_are_basket_internal_ranks_not_absolute_strength", "trend_and_mean_reversion_are_separate_axes", "earnings_proximity_is_flag_not_score_component")`.
  2. Build the list dynamically in `main()` from string literals.
- **Selected Approach**: Option 1. Define at module scope; include in every `aggregate_emit` `query_meta` payload.
- **Rationale**: A single source of truth is the simplest way to guarantee identical strings across every response. Constants at module scope are also the easiest to grep for and to import into a future integration-test helper if the negative-invariant check gets shared.
- **Trade-offs**: None meaningful.
- **Follow-up**: `skills/entry-timing-scorer/SKILL.md`'s "Interpretation" subsection (Req 11.6) must reproduce the three strings verbatim.

## Live Verification (2026-04-30)

Ran a full end-to-end prototype against the Req §Success-criteria basket `ASC CMCL FLXS SM TLT LQD` with `provider=yfinance` + `provider=nasdaq` (keyless defaults). Results inform four **new** decisions (7–10 below) and modify two earlier assumptions.

### Live finding L1 — Per-ticker pipeline works end-to-end in ~15s / 6 tickers

All five stages per ticker (`quote`, `historical(210d)`, `clenow(126)`, `rsi(14)`, `macd(12,26,9)`) succeeded for every basket member. The single-fetch-feeds-three-technicals pattern is confirmed live: one `obb.equity.price.historical` call produced `hist.results` (a list of pydantic records), and `obb.technical.{clenow,rsi,macd}(data=hist.results, …)` each consumed that list directly. This pins the Req 13.3 budget (≤5 calls/ticker) as achievable.

### Live finding L2 — OpenBB field names are **not** what the pre-verification draft assumed

Three column-name mismatches, each silently producing `None` if the wrapper uses the naïve field name:

| Pre-verification assumption | Actual OpenBB field (live 2026-04-30) | Consequence if unhandled |
|---|---|---|
| `obb.technical.macd` → `histogram` | `close_MACDh_12_26_9` (also `close_MACD_12_26_9`, `close_MACDs_12_26_9`) | Silent `macd_histogram=None` across every ticker |
| `obb.technical.rsi` → `rsi` | `close_RSI_14` | Silent `rsi_14=None` |
| `obb.technical.clenow` → `factor` (float) | `factor` (string, e.g. `"0.63826"`) | `_to_float` fix needed; arithmetic on the string raises |

Requirements amended post-verification: Req 4.3 now mandates the `_to_float` coercion on the Clenow `factor` string; Req 4.4 now names `close_RSI_14` (case-insensitive suffix match on `"RSI"`) as the RSI extraction target; Req 4.5 now names `close_MACDh_12_26_9` (case-sensitive suffix match on `"MACDh"`) as the MACD histogram extraction target. `sector_score.py::fetch_clenow` (line 302) already handles the clenow-is-string case with `_to_float(last.get("factor"))`; the new wrapper mirrors that pattern and adopts the suffix-based column lookup for RSI / MACD that `momentum.py::_last_with` (lines 74–86) demonstrates.

**FMP re-verification pending**: the OpenBB technical stack computes indicators locally from whatever `history.results` the price provider produced, so these shape quirks are expected to be provider-invariant. Design phase should still re-run the column-name probe against `--provider fmp` to confirm the assumption rather than inherit it; see Req §Constraints FMP re-verification item 3.

### Live finding L3 — `yfinance` quote returns `last_price=None` for bond ETFs

TLT and LQD both returned `last_price=None` from `obb.equity.price.quote(provider="yfinance")` while `prev_close`, `open`, `high`, `low`, `year_high`, `year_low`, `ma_200d`, `ma_50d` were all populated for the same calls. The historical endpoint has the correct latest close (`TLT: 85.70`, `LQD: 108.73`) on the same date. Without a fallback, `range_pct_52w` and `ma200_distance` silently become `None` on bond ETFs — breaking the mean-reversion sub-score for a very common basket inclusion.

Fallback priority (in order of recency), mandated by the amended Req 4.7 with per-rung `data_quality_flags` entries:
1. `quote.last_price` — when populated, use it (intraday-most-recent)
2. `quote.prev_close` → `data_quality_flags: "last_price_from_prev_close"`
3. `historical[-1].close` → `data_quality_flags: "last_price_from_historical_close"`
4. none of the above → `last_price: null` + `data_quality_flags: "last_price_unavailable"`

**FMP re-verification pending**: this null-`last_price` behavior was observed against `provider="yfinance"` only. If FMP populates `last_price` for bond ETFs cleanly, Req 4.7's fallback can be narrowed to yfinance-only at a future amendment. See Req §Constraints FMP re-verification item 2.

### Live finding L4 — 90-day single-shot earnings fetch is **not reliable on the keyless nasdaq provider**

Binary-search of the nasdaq earnings window produced a hard-to-miss ceiling:

| Window | Outcome |
|---|---|
| +7d … +49d | OK, 1707 → 3550 rows |
| +56d | FAILED: `AttributeError: NoneType.get` (server returns unexpected payload) |
| +63d, +70d, +77d | same `NoneType.get` |
| +84d, +90d | FAILED: `ContentTypeError 403` (rate-limit / WAF block; did not clear after 30s pause) |

The 403 also **poisoned subsequent small-window calls for the rest of the process**: after a `+84d` 403, even a `+7d` retry failed until the Python process restarted. This means a naive "retry on failure" loop cannot recover the call; the strategy must be "get it right the first time, or accept partial-window coverage".

The default-provider fallback behavior is also unusable: `provider=seeking_alpha` (the keyless default chosen by OpenBB when `provider=` is omitted) succeeded at +90d but returned only **39 rows** across the entire window — far too sparse to be useful (nasdaq +49d returned 3550 rows for comparison).

Requirements amended post-verification: Req 3.1 now takes the window from the new Req 3.7 flag `--earnings-window-days <N>` defaulting to `45` and bounded `[1, 90]`; Req 3.7 also bans in-process retry on failure. The pre-verification `TODAY+90d` literal has been retired.

**FMP re-verification pending**: `provider=fmp` was not exercised (it requires `FMP_API_KEY`, confirmed live: `Missing credential 'fmp_api_key'`). FMP is known to host a bulk earnings-calendar endpoint that typically tolerates 90-day windows. Design phase should inject `FMP_API_KEY` and repeat the binary-search against `--provider fmp`; if FMP returns clean 90-day results, Req 3 should be amended a second time to restore 90 as the default under `--provider fmp` while keeping 45 for the default `--provider yfinance` (which routes calendar calls to nasdaq per Req 2.1). See Req §Constraints FMP re-verification item 1.

Concrete basket impact (45-day nasdaq window, 2026-04-30 → 2026-06-14):

| Ticker | Earnings hit |
|---|---|
| ASC | 2026-05-06 (days_to = 6) |
| CMCL | 2026-05-11 (days_to = 11) |
| FLXS | — |
| SM | 2026-05-06 (days_to = 6) |
| TLT | — (bond ETF, no earnings) |
| LQD | — (bond ETF, no earnings) |

All the "missing" hits on FLXS / TLT / LQD are genuine absences, not data gaps. For the holdings-monitoring loop the 45-day window covers the next quarterly cycle for actively-reporting equities.

### Live finding L5 — Scoring produces interpretable, differentiated output

The end-to-end run produced a sensible ranking on n=6:

```
trend_0-100 (desc):
  #1 SM    67.4   mrev 34.4   (uptrend, not oversold)
  #2 FLXS  64.1   mrev 27.8   (uptrend, not oversold, recent volume)
  #3 ASC   60.8   mrev 21.5   (uptrend, overbought — RSI 74)
  #4 CMCL  41.3   mrev 75.1   (mild downtrend, deeply oversold — RSI 37)
  #5 TLT   38.8   mrev 76.1   (mild downtrend, oversold — bond ETF)
  #6 LQD   27.6   mrev 65.1   (weak trend, mildly oversold — bond ETF)
```

The split produces exactly the readout the `analytical_caveats` promise: ASC ranks high on trend (strong +ve clenow and breakout volume) **and** low on mean-reversion (RSI overbought, near 52w high — expected after a breakout). CMCL ranks high on mean-reversion (low 52w range position, low RSI) **and** low on trend (slightly negative clenow). A blended 50/50 score would have averaged these into the noise floor. The two-axis output is doing the work the design claimed.

Robust vs classical volume-z estimator divergence is non-trivial on one ticker (`CMCL: robust +1.66 vs classical +2.54`, diff −0.88) — a realistic scenario where a single volume outlier inflates the classical z. The robust path is audibly tempering the outlier, which is what the design intended. Other tickers showed diffs ≤ 0.31.

### Live finding L6 — No `data_quality_flags` fired on this basket

All six tickers had ≥ 21 historical rows, non-zero volume dispersion, and positive volumes. The Req 5.5 flag paths (`volume_window_too_short`, `volume_zero_dispersion`, `volume_non_positive`) did not exercise. This is expected on a well-populated US/Liquid basket; the flags' value proposition is in edge cases (halted sessions, new listings, JP tickers with low ADV), which this probe did not cover and should be a follow-up test during implementation.

## Design Decisions (continued — added from live verification)

### Decision 7: Default earnings-calendar window to 45 days on the keyless nasdaq path (FMP revisit pending)

- **Context**: Live finding L4 shows nasdaq fails at ≥56d and the 403 poisons subsequent calls. Requirement 3.1's pre-verification text said 90 days. The 45-day window covers the next-quarter cycle for normal reporters and is well inside the failure ceiling. Requirements amended post-verification: Req 3.1 now takes the window from Req 3.7's `--earnings-window-days <N>` flag (default 45, range `[1, 90]`); Req 3.7 also forbids in-process retry on failure.
- **Alternatives Considered**:
  1. Cap the default window at 45 days; expose `--earnings-window-days` for callers who need more and accept the failure risk.
  2. Tile the 90-day range into chunks and merge (live-tested; fails — first chunk causes 403 that poisons subsequent chunks).
  3. Switch to FMP (requires credential; not keyless).
  4. Switch to seeking_alpha (returned 39 rows in 90d — too sparse to be useful).
  5. Keep the 90-day default but expect and tolerate calendar failure via the existing `data.provider_diagnostics` channel.
- **Selected Approach**: Option 1. Set the default `N=45` and bound `[1, 90]` via argparse per Req 3.7. Expose `--earnings-window-days` so FMP-credentialled callers (or future nasdaq-ceiling improvements) can opt back in to 90.
- **Rationale**: Chooses reliability over literal-requirement-compliance on the keyless path. The pre-verification "90d" was written before the failure mode was known; the post-verification choice is to prefer a working default and surface the knob. The analyst never acts on earnings 90 days out — the `earnings_proximity_days` threshold is 5 business days (Req 7.2).
- **Trade-offs**:
  - (+) Reliable default on the keyless provider — no calendar failures on normal use.
  - (+) No degraded-state recovery logic in the wrapper.
  - (−) Requirements needed an amendment (delivered: Req 3.1 + Req 3.7).
- **FMP re-verification pending** (per Req §Constraints item 1): if a design-phase probe with `FMP_API_KEY` confirms that `provider="fmp"` clears the 90-day window cleanly, amend Req 3 a second time so the default becomes 90 under `--provider fmp` while 45 remains the default under `--provider yfinance` (the keyless path).
- **Follow-up**: `design.md` must document the flag behavior; integration test Req 12.5 asserts the default-window behavior.

### Decision 8: MACD/RSI field extraction via suffix search, Clenow factor via `_to_float` (provider-invariant, FMP revisit pending)

- **Context**: Live finding L2 showed the pre-verification field-name assumptions return `None`. The existing `momentum.py::_last_with` pattern already solves this by searching keys for a substring; `sector_score.py` already treats `factor` as string-needing-coercion. Requirements amended post-verification: Req 4.3 / 4.4 / 4.5 now name the actual output shapes.
- **Alternatives Considered**:
  1. Suffix-search for `MACDh` / `RSI_14` substrings in the last record; coerce Clenow `factor` via `_to_float` (mandated by amended Req 4.3–4.5).
  2. Hard-code the field names `close_MACDh_12_26_9` / `close_RSI_14` (brittle against OpenBB version bumps that rename conventions).
  3. Use `obb.technical.*(…)`'s `.to_df()` instead of `.results`, then iloc[-1] by column regex.
- **Selected Approach**: Option 1. Per amended Req 4.5, case-sensitive suffix match on `"MACDh"` (to avoid the `MACD` / `MACDs` / `MACDh` collision); per amended Req 4.4, case-insensitive suffix match on `"RSI"`. Coerce Clenow `factor` via the existing `_to_float` helper pattern from `sector_score.py`.
- **Rationale**: Matches the in-repo precedent (`momentum.py::_last_with`), robust to minor OpenBB renames, and consistent across wrappers.
- **Trade-offs**:
  - (+) Version-tolerant.
  - (−) One extra iteration per technical result — negligible for n=6.
- **FMP re-verification pending** (per Req §Constraints item 3): OpenBB technicals are computed locally from `history.results`, so the output shape is expected to be price-provider-invariant — but verify this with `--provider fmp` in design phase before relying on the assumption.
- **Follow-up**: `design.md` pseudocode must show the suffix-search helper and the `_to_float` wrapping on Clenow output.

### Decision 9: `last_price` fallback chain (yfinance `last_price=None` on bond ETFs; FMP revisit pending)

- **Context**: Live finding L3 showed bond ETFs (TLT, LQD) return `last_price=None` from `obb.equity.price.quote(provider="yfinance")`. Requirements amended post-verification: Req 4.7 mandates the fallback chain and per-rung `data_quality_flags`; Req 9.10 enumerates the three new flag values in the closed-enum catalog.
- **Alternatives Considered**:
  1. Three-step fallback chain `quote.last_price → quote.prev_close → historical[-1].close`, with a `data_quality_flag` emitted on each fallback rung (so auditors know the `last_price` is not intraday-fresh).
  2. Fail the ticker row if `quote.last_price` is None.
  3. Always use `historical[-1].close` and ignore `quote.last_price` (loses intraday freshness for equities).
- **Selected Approach**: Option 1, mandated by amended Req 4.7. Emit `last_price_from_prev_close` / `last_price_from_historical_close` / `last_price_unavailable` into `data_quality_flags[]` as appropriate; the catalog of admissible flag values is Req 9.10.
- **Rationale**: Preserves the common-case intraday-freshness of the yfinance quote endpoint while letting the wrapper degrade gracefully on bond ETFs and other asset types where yfinance leaves `last_price` null. The flag preserves the auditability that Req §Analytical-stance emphasizes.
- **Trade-offs**:
  - (+) Bond ETFs, halted sessions, and pre-market partials continue to produce usable `range_pct_52w` and `ma200_distance`.
  - (+) New flag types expand the `data_quality_flags` enumeration (Req 9.10) but do not change the schema shape.
  - (−) `data_quality_flags` closed-enum catalog grew by three values (now formalized in Req 9.10).
- **FMP re-verification pending** (per Req §Constraints item 2): the `last_price=None` behavior was observed against `provider="yfinance"` only. If FMP populates `last_price` for bond ETFs, amend Req 4.7 at design phase to scope the fallback to `--provider yfinance`. Until then, the fallback applies universally.
- **Follow-up**: `design.md` enumerates the expanded `data_quality_flags` catalog; `skills/entry-timing-scorer/SKILL.md` lists the three new flag strings verbatim.

### Decision 10: Per-ticker `safe_call` protection around the earnings-calendar parse step, not just the fetch

- **Context**: Live finding L4 showed that nasdaq's 403 produces a top-level `OpenBBError` with the original `AttributeError: NoneType.get` as cause. The existing `safe_call` wrapper catches this correctly, but the wrapper also needs to survive partial-window failures (e.g., calendar succeeded but `row.report_date` is None on some entries).
- **Alternatives Considered**:
  1. Guard both the fetch (`safe_call(obb.equity.calendar.earnings)`) and the per-row iteration (skip rows where `report_date is None` or `symbol not in basket`).
  2. Guard only the fetch; let row-iteration exceptions propagate (might crash the wrapper on malformed rows).
- **Selected Approach**: Option 1. Guard the fetch with `safe_call`; inside the success branch, iterate records defensively (skip rows without `report_date` or `symbol`). Mirror `sector_score.fetch_performance`'s pattern of a `data.provider_diagnostics[{provider, stage, error, error_category}]` entry on fetch failure.
- **Rationale**: Matches the single established failure-handling pattern in the codebase. The defensive row iteration prevents a single malformed row from blocking the other basket tickers.
- **Trade-offs**: None meaningful.
- **Follow-up**: Integration test Req 12 should include a path where the calendar fetch succeeds but the basket has zero hits (e.g., bond-ETF-only basket), asserting that `provider_diagnostics` is empty / absent while every row still emits `next_earnings_date: null`.

## Risks & Mitigations

- **Risk**: `pyyaml` direct-dependency add triggers an unexpected `uv sync` resolution conflict with OpenBB's transitive pin.
  - **Mitigation**: Live-verified 2026-04-30 — `pyyaml 6.0.3` is already installed transitively; declaring it directly is a no-op install-footprint-wise. If a future OpenBB upgrade drops the transitive dep, the direct declaration ensures the wrapper keeps working.
- **Risk (surfaced live, requirements amended)**: Nasdaq earnings calendar fails at windows ≳55 days with `AttributeError: NoneType.get`, and a 403 on ≳84 days poisons subsequent calls in the same process.
  - **Mitigation**: Amended Req 3.1 + new Req 3.7 cap the default window at 45 days, bound overrides to `[1, 90]`, and forbid in-process retry. Decision 7 records the rationale. **FMP re-verification pending** (Req §Constraints item 1) may restore 90 as the default under `--provider fmp`.
- **Risk (surfaced live, requirements amended)**: `yfinance` quote returns `last_price=None` for bond ETFs and other asset classes that don't have an intraday trade snapshot exposed.
  - **Mitigation**: Amended Req 4.7 mandates the `last_price → prev_close → historical[-1].close` fallback with per-rung `data_quality_flags`; amended Req 9.10 extends the closed-enum flag catalog with `last_price_from_prev_close`, `last_price_from_historical_close`, `last_price_unavailable`. Decision 9 records the rationale. **FMP re-verification pending** (Req §Constraints item 2) may scope this fallback to `--provider yfinance` only.
- **Risk (surfaced live, requirements amended)**: OpenBB's technical indicator outputs use non-obvious field names (`close_MACDh_12_26_9`, `close_RSI_14`) and Clenow returns `factor` as a string. A naive wrapper reading `record["histogram"]` or `float(record["factor"])` silently produces `None` or raises.
  - **Mitigation**: Amended Req 4.3 / 4.4 / 4.5 name the actual extraction columns and mandate `_to_float` on Clenow. Decision 8 records the rationale. Integration test Req 12.3 / 12.7 must assert that `signals.macd_histogram` and `signals.rsi_14` are non-null on a known-active equity (not just "field present"). **FMP re-verification pending** (Req §Constraints item 3) is expected to be a no-op since technicals are computed locally.
- **Risk**: Robust (log + MAD) volume-z estimator produces surprising values on low-volume JP (`.T`) tickers or pre-market US sessions.
  - **Mitigation**: The three Req 5.5 data-quality flags (`volume_non_positive`, `volume_zero_dispersion`, `volume_window_too_short`) cover the expected edge cases; the output contract is "emit null + flag", not "silently return a degenerate number". Integration test Req 12.8 runs both estimators against the same basket and locks in behavior.
- **Risk**: Cross-sectional z-score on tiny baskets (n < 5) produces interpretable-looking 0–100 scores that agents over-weight.
  - **Mitigation**: The three `analytical_caveats` travel with every response and the "basket_internal_rank" string explicitly says these are rank summaries. The SKILL.md "Interpretation" section (Req 11.6) reproduces them verbatim. The `basket_size_sufficient` boolean lets callers filter machine-checkably.
- **Risk**: Earnings-calendar single-shot fetch masks "no earnings in the active window" vs. "call failed" at the per-row level.
  - **Mitigation**: Requirement 3.5 mandates the `data.provider_diagnostics` entry on calendar failure with `stage: "earnings_calendar"`; the presence of that entry is the machine-readable distinction. `sector_score.fetch_performance` demonstrates the pattern.
- **Risk**: A subtle refactor adds `days_to_next_earnings` to `z_scores` or the composite, silently violating Req 7.1.
  - **Mitigation**: Decision 5's `SCORER_SIGNAL_KEYS` module-scope constant makes the signal set an explicit code-level invariant. Integration test Req 12.7 asserts trend and mean-reversion can both be high for the same ticker (i.e., they are separate axes), which indirectly confirms the earnings field is not tangled in.
- **Risk**: The `interpretation_hint` negative invariant is quietly reintroduced in a future refactor.
  - **Mitigation**: Integration test Req 12.6 asserts field absence on every row. The test is part of the MVP change set per Req 12.1.

## References

- `scripts/sector_score.py` — architectural precedent for cross-sectional z-score, weight-normalized composition, 0-100 transform, `aggregate_emit`, `data.provider_diagnostics`, per-stage warning shape.
- `scripts/momentum.py::_indicator_call` (lines 89–126) — canonical single-fetch-feeds-technicals pattern (`data=history.results` passed to `obb.technical.{clenow,rsi,macd}`).
- `scripts/_common.py` — shared envelope (`aggregate_emit`, `single_emit`, `wrap`), `safe_call`, `ErrorCategory` (stable taxonomy), `sanitize_for_json` (NaN/Inf → null), `silence_stdout`.
- `scripts/calendars.py` + `skills/calendars/SKILL.md` — earnings calendar endpoint contract (`obb.equity.calendar.earnings(start, end, provider="nasdaq")`), output fields, keyless-provider confirmation.
- `docs/steering/tech.md` — JSON output contract, envelope invariants, exit-code contract (0 for success / partial failure; 2 for all-fatal or validation rejection), provider-selection principle.
- `docs/steering/structure.md` — flat-wrapper organization, underscore-prefixed helper conventions, SKILL.md authoring norms (English-only, 30–80 lines, reference `_envelope`/`_errors`/`_providers`).
- `docs/tasks/todo/entry-timing-scorer/requirements.md` — the thirteen requirements driving this research.
- `docs/tasks/todo/entry-timing-scorer/analysis.md` — requirement-to-asset gap map and recommended approach (Option B).
- `tests/integration/test_json_contract.py::WRAPPER_HAPPY_ARGV` (lines 335+) + `test_wrapper_declares_happy_argv` — the parametrized suite that auto-discovers `scripts/*.py`; adding the new wrapper requires a new `WRAPPER_HAPPY_ARGV` entry in the same commit.
- `tests/integration/_sanity.py` — shared assertion helpers (`assert_finite_in_range`, ordered-date checks) available to the new integration-test file.
- `tests/integration/conftest.py::run_wrapper_or_xfail` — xfail-on-transient-failure helper; the new test must invoke the wrapper through this.
- `pyproject.toml` — direct-dependency list to amend (`pyyaml>=6.0`; live-verified 6.0.3 already transitively present).
- Live verification artifacts (2026-04-30, retained under `/tmp/`): `probe_scorer.py` (initial full probe), `probe_columns.py` (field-name diagnosis), `probe_earnings{,2,3,4}.py` (nasdaq window binary-search), `probe_full.py` (end-to-end run reproducing the ranked output).
