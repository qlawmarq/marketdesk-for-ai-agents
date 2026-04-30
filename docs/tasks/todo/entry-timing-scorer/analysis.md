# Implementation Gap Analysis — entry-timing-scorer

## Executive summary

- All five signal primitives (`obb.equity.price.quote`, `obb.equity.price.historical`, `obb.technical.{clenow,rsi,macd}`, `obb.equity.calendar.earnings`) are already live and wrapper-covered. No new OpenBB endpoints are required.
- `scripts/sector_score.py` is the direct architectural precedent: cross-sectional z-score, weight-normalized composition, 0-100 transform, `aggregate_emit` + `provider_diagnostics`, multi-source partial-failure handling. The new wrapper is a structural cousin with a different signal set and two sub-scores instead of one composite.
- **Recommended approach**: Option B (new wrapper under `scripts/entry_timing_scorer.py`), because the tool owns a distinct responsibility (ticker-level short-term timing) and extending `sector_score.py` would merge two analytical stances into one file. Reuse helper patterns from `sector_score.py` without cross-importing.
- **Concrete net-new concerns** (not yet covered by any wrapper): YAML parsing for `--portfolio-file`, per-ticker `context`/`interpretation` block, `data.analytical_caveats` string array, robust (log + MAD) volume-z estimator, `basket_size_sufficient` gating at cross-sectional-normalization time, and a negative schema invariant (`interpretation_hint` absence) in the JSON-contract test.
- **Effort / Risk**: **M (3-7 days) / Low**. No unknown tech, no new providers, no architectural shift; the new concerns are localized schema and computation work.

## Requirement-to-Asset Map

| Req | Existing asset that covers it | Gap tag | Notes |
|---|---|---|---|
| 1.1 `--tickers` CSV | `sector_score.py::main` (`--tickers` CSV split) | — | Direct pattern reuse. |
| 1.2 `--portfolio-file` YAML | **none** | **Missing** | No existing wrapper parses YAML; `pyproject.toml` has no `pyyaml` dep. See Research Items. |
| 1.3-1.6 validation gates | `_common.safe_call` + argparse mutually-exclusive pattern | Constraint | argparse handles 1.3/1.4/1.6 natively; validation exit 2 is already the `ErrorCategory.VALIDATION` path. |
| 1.7-1.9 `context` tagging | **none** | **Missing** | Novel per-ticker field. Needs to survive portfolio-file parsing and the `--context` flag path, with the `holding` overrides `watchlist` rule for 1.9 plus a `context_duplicate_positions_and_watchlist` data-quality flag. |
| 2.x provider selection | `sector_score.py` (`--provider` closed choices), `calendars.py` (per-type default provider routing) | — | Mix the two: equity calls on `args.provider`, earnings-calendar pinned to `nasdaq`. |
| 3.x earnings calendar, single-shot | `scripts/calendars.py` + `obb.equity.calendar.earnings` | — | Already verified keyless via nasdaq; filter client-side on the input-ticker set. Mirror failure via `data.provider_diagnostics` like `sector_score.py` does for Finviz stages. |
| 4.1 `quote` per ticker | `scripts/quote.py` + `obb.equity.price.quote` | — | Direct call pattern (not shell-out). |
| 4.2 `historical` 140d | `scripts/historical.py` + `obb.equity.price.historical` | — | Same pattern as `sector_score._compute_performance_from_history`. |
| 4.3-4.5 clenow/rsi/macd | `scripts/momentum.py` (`_indicator_call`) | Constraint | `momentum.py` does one indicator per ticker; entry-timing needs all three. Reuse OpenBB calls directly; one `obb.equity.price.historical` fetch can feed all three technicals via `data=hist.results` (see `sector_score.fetch_clenow`). |
| 4.6 `safe_call` guard | `_common.safe_call` | — | Use verbatim. |
| 5.1 `range_pct_52w` | Derived arithmetic; primitives from `obb.equity.price.quote` | — | Pure math. |
| 5.2 `ma200_distance` | Same | — | Pure math. |
| 5.3 `volume_z_20d` classical | Historical series from `obb.equity.price.historical` | — | `pandas.Series.rolling(20).mean()`/`.std()` pattern (stdlib `statistics` also sufficient). |
| 5.4 `--volume-z-estimator robust` (log + MAD) | **none** | **Missing** | No current wrapper computes a log-MAD estimator. Pure Python/stdlib `statistics.median` + list comprehension (no new dep). See Research Items re: zero/non-positive volume handling. |
| 5.5 null + data-quality flag | Pattern analog: `sector_score.py` emits `composite_score_0_100: null` + signal `None`, but **no wrapper currently emits a `data_quality_flags[]` array** | **Missing (novel schema)** | New per-row list field. Low implementation cost but needs explicit documentation in the new wrapper's envelope contract. |
| 5.6 `volume_avg_window: "20d_real"` | **none** | **Missing (novel schema)** | Reviewer R1 fix; verbatim per-row tag. |
| 5.7 `volume_reference` sub-block | Provider-native fields from `obb.equity.price.quote` | — | Rename/wrap; no new fetch. |
| 6.1-6.4 cross-sectional z, sub-scores, 0-100 | `sector_score.zscore` + `sector_score.build_scores` (0-100 transform `50 + z * 25`) | Constraint | Almost direct reuse; need the `basket_size < 3 → null` gate (current `zscore` returns all-None when `< 2`; threshold change is one line). |
| 6.5-6.6 `--blend-profile` gated emission | **none** | **Missing (novel CLI/schema)** | New flag; no existing wrapper emits a score conditionally on a profile arg. Straightforward but requires the "omit vs null" invariant to survive JSON serialization. |
| 6.7-6.8 basket-size gating + warning row | `_common.aggregate_emit` handles warnings; null-emit pattern exists in `sector_score.py` | — | Combine both: per-signal null + whole-basket null. |
| 6.9 sort by active blend profile | `sector_score.build_scores` end (sorted by `rank`) | — | Extend with profile dispatch. |
| 6.10 per-ticker `z_scores` block | `sector_score.build_scores` (per-row `z_scores` dict) | — | Direct shape reuse. |
| 6.11 `basket_size` / `basket_size_sufficient` | **none** | **Missing (novel schema)** | Easy to compute (count of non-null inputs at z-score time); new field names. |
| 7.x earnings-proximity flag (not in score) | Primitives only | **Missing (novel schema + negative invariant)** | The "flag but not a score component" rule needs a guard somewhere that the trend/mean-reversion/blended math cannot silently include `days_to_next_earnings`. Design-phase decision: hard-code field-list or enforce via structure. |
| 8.x envelope / JSON contract | `_common.aggregate_emit`, `sanitize_for_json`, `emit`, `ErrorCategory` | — | Verbatim reuse. `tests/integration/test_json_contract.py` auto-discovers `scripts/*.py` so the new file is automatically held to the contract. |
| 9.x per-ticker output schema | `sector_score.py::build_scores` (partial), `quote.py` (symbol/provider/ok shape) | Mixed | Most fields match existing patterns; `context`, `interpretation` (object), `data_quality_flags[]`, `analytical_caveats` (under `data`), negative invariant on `interpretation_hint` absence are all novel. |
| 10.x scope boundaries | `docs/steering/tech.md` + `docs/steering/structure.md` (thin-wrapper principle); `scripts/sector_score.py` as scope precedent | — | Enforce through code (no file writes, no `import yaml` beyond `--portfolio-file` parsing, no alert hooks) and through SKILL.md prose. |
| 11.x SKILL.md + INDEX update | `skills/sector_score/SKILL.md`, `skills/INDEX.md`, `skills/AUTHORING.md` | Constraint | ChangeBundleRule is mechanically enforced by `tests/integration/test_verification_gate.py::test_readme_rows_and_scripts_are_in_bijection`. SKILL.md **must be English**, 30-80 lines, reference `_envelope` / `_errors` / `_providers`. |
| 12.x integration test | `tests/integration/test_sector_score.py` (parametrized template), `tests/integration/conftest.py::run_wrapper_or_xfail`, `_sanity.assert_finite_in_range` | — | New file `tests/integration/test_entry_timing_scorer.py`; patterns identical to sector_score. `WRAPPER_HAPPY_ARGV` in `test_json_contract.py` **must** get a new entry (parametrized suite will fail otherwise — guarded by `test_wrapper_declares_happy_argv`). |
| 13.x performance budget | `sector_score.py` handles 11 tickers × 3-4 calls within analogous time; timeout in test harness already has a per-wrapper override | Constraint | One-earnings-call rule (Req 3.1) and five-per-ticker cap (Req 4) already bound the call count; MVP need not add parallelism but it is the obvious lever if the live P95 blows the 90s budget. |

## Implementation Approach Options

### Option A — Extend `scripts/sector_score.py`

Add an `--analytical-mode {sector|entry_timing}` switch and a second set of weights. Both tools live in one file.

- ✅ Maximal reuse of `zscore`, `build_scores`, partial-failure scaffolding.
- ❌ Violates single-responsibility: `sector_score` is explicitly a **mid-term sector ETF** tool (`policy.md` §2), the new tool is **short-term per-ticker timing** (`policy.md` §3). Different universes, different signals, different context conventions.
- ❌ Fails the thin-wrapper spirit in `structure.md` — the file already sits at ~600 lines and carries two preset dictionaries; cramming a second tool in would push it past the wrapper-size norm.
- ❌ `interpretation` / `context` / `analytical_caveats` / `basket_size_sufficient` are entry-timing-specific; bolting them into `sector_score.py` would leak short-term-timing concepts into the sector wrapper's schema.

**Not recommended.**

### Option B — New wrapper `scripts/entry_timing_scorer.py` (recommended)

A sibling file modeled closely on `sector_score.py`'s structure: direct `obb.*` calls guarded by `safe_call`, cross-sectional z-score helpers, 0-100 transform, `aggregate_emit`, `data.provider_diagnostics` for the earnings-calendar stage.

- ✅ Clean separation of concerns; respects the flat-wrappers organization principle.
- ✅ Small cross-file coupling: only `_common` (envelope/safe_call/ErrorCategory) and possibly a small helper module if the z-score primitives get genuinely shared.
- ✅ Automatic coverage by `test_json_contract.py` parametrized invariants the moment the file lands under `scripts/*.py`.
- ❌ Some helper duplication vs `sector_score.py` (a `zscore` function, a `to_100` transform, a lookback-day computation). If the duplication feels excessive in design phase, extract to `scripts/_common.py` — but **only after** the second implementation is complete, to avoid speculative generalization from a single data point.

**Recommended.** Mirrors how `sector_score.py` was added to the existing flat directory.

### Option C — Hybrid: new wrapper + shared helper module

Land `scripts/entry_timing_scorer.py` (as B) plus factor out `zscore`, `rank_desc`, and the 0-100 transform into a new `scripts/_scoring.py` helper consumed by both wrappers. `sector_score.py` gets a follow-up refactor.

- ✅ Removes duplication at the z-score layer right away.
- ❌ Two-file change to an existing production wrapper (`sector_score.py`) with a stable test suite increases risk and blast radius for what is a single new feature.
- ❌ Violates the project convention that helpers in `scripts/_*.py` are only created when at least two wrappers would consume them; if the entry-timing design ends up with a different z-score signature (different basket-size threshold, robust variant), premature extraction would be undone immediately.

Reasonable **after** the new wrapper has shipped and a refactor pass reveals the shared surface area. Not a Phase-1 choice.

## Effort & Risk

- **Effort: M (3-7 days)** — Single new wrapper (~400-600 lines) + SKILL.md + INDEX.md row + README §1-1 row + one integration test file. No new providers, no credentials, no architectural work. `sector_score.py` removes almost all implementation uncertainty.
- **Risk: Low** — Every primitive is already verified live (per requirements §Existing primitives); the envelope and test gates are mechanically enforced; the novel items (robust estimator, `context` block, `analytical_caveats`) are local schema/computation, not integration surface.

## Research Items (carry to design phase)

1. **`--portfolio-file` YAML parsing.** Three options: (a) add `pyyaml` to `pyproject.toml`; (b) defer YAML to a follow-up change and accept only `--tickers` in MVP; (c) lightweight stdlib parser for the narrow `positions[].ticker` / `watchlist[].ticker` subset. Design should pick explicitly — the call changes scope and dependency surface. Requirements §Constraints says "portfolio.yaml の解釈は MVP では行わない" but Req 1.2 still mandates parsing; reconcile.
2. **Robust volume-z estimator behavior at zero-volume halt days.** Req 5.5 enumerates `volume_non_positive` as a data-quality flag; verify the log transform's domain handling across both JP (`.T`) halted sessions and US pre-market partials before wiring. No existing test fixture covers this.
3. **One-fetch-feeds-all-three-technicals pattern.** `sector_score.fetch_clenow` fetches history once and feeds `obb.technical.clenow` from `hist.results`. Confirm `obb.technical.rsi` and `obb.technical.macd` accept the same `data=` parameter in the installed openbb version; if not, three independent `historical` calls per ticker (10 tickers × 3 × 2 = 60 calls; double the Req 13.3 cap of 5 per ticker). Design must decide between the single-fetch pattern and explicit per-indicator fetches.
4. **`interpretation` schema precedent check.** No current wrapper emits a structured per-row `interpretation` object. Design should settle the exact key set (Req 9.4 enumerates four keys but leaves `reading_for_context` content open for `unknown`) and whether it lives under `data.results[*].interpretation` or a sibling block.
5. **Negative invariant: `interpretation_hint` absence.** Req 9.5 + 12.6 require the integration test to assert the field is absent. Decide whether to enforce via a dedicated assertion helper in `tests/integration/_sanity.py` or inline in the new test file — the former buys re-use if future wrappers want the same negative invariant.
6. **`basket_size_sufficient` under mixed success/failure rows.** Req 6.11 defines `basket_size` as "the count of tickers that entered cross-sectional normalization". A ticker that succeeded on `quote` but failed on `historical` (no `clenow_126` / `rsi_14`) may enter normalization for some signals but not others. Design should pick between a per-signal `basket_size` (complex but precise) and the whole-row `basket_size` (simple but coarse).
7. **Blend-profile sort stability.** Req 6.9 specifies the sort key per profile. When two rows tie on the score (both `null`, or both `50.0`), the expected secondary order is unstated. Pick: preserve input order, or sort by symbol alphabetically. Document the choice in SKILL.md.

## Recommendations for design phase

- Go with **Option B**: new wrapper at `scripts/entry_timing_scorer.py` modeled on `sector_score.py`. Do not extract shared helpers in Phase 1; revisit after the wrapper ships.
- Pin the primitive-call flow to **one `historical` fetch per ticker feeding clenow/rsi/macd** via `obb.technical.*(data=hist.results, ...)`, and **one earnings-calendar fetch per invocation** filtered client-side — matching the Req 13.3 budget (≤1 calendar call + ≤5 equity/technical calls per ticker = 1 quote + 1 historical + clenow + rsi + macd = 5).
- Default `--portfolio-file` handling decision: **add `pyyaml` as a lightweight dep** (pure Python, already common in the broader ecosystem, ~100KB). The alternative "drop YAML from MVP" would force the analyst agent to pre-extract tickers, re-introducing manual work that the tool is explicitly meant to remove (Problem #1). The stdlib-subset option is fragile against portfolio.yaml schema changes.
- Adopt `sector_score.py`'s **`data.provider_diagnostics`** pattern verbatim for the earnings-calendar failure branch (Req 3.5) — same shape (`{provider, stage, error, error_category}`), same well-formed-diagnostics test helper.
- Define the **"earnings fields never enter the composite"** invariant (Req 7.1) at the code level by making `build_scores` accept an explicit whitelist of signal keys rather than iterating over all `signals[]`. This is Option-B's structural equivalent of a guard rail.
- Write the **integration test first** following the `test_sector_score.py` parametrized template; the invariants for the negative `interpretation_hint` check (Req 12.6) and the two-run `--volume-z-estimator` check (Req 12.8) are straightforward once the happy-path scaffolding is in place.
- Treat the **three data.analytical_caveats strings** as a constant tuple at module scope — they must travel with every response (Req 9.9) and must survive JSON round-trip (Req 12.6 asserts their presence).
