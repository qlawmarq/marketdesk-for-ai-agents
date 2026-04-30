# Gap Analysis — insider-trading-skill

## 1. Analysis Summary

- **Scope shape**: an in-place upgrade of an already-shipped wrapper (`scripts/insider.py` ≈ 110 lines) plus its skill manual. No new file under `scripts/`, no new module split (Req 6.8 / 10.7).
- **Key new capabilities**: `--transaction-codes` filter (post-fetch, post-day-window), `--format md` markdown output (a first-of-its-kind non-JSON output path in this repo), and a single normalized record schema that hides SEC vs. FMP field-name and `transaction_type` divergence (Req 6.2 / 6.3).
- **Largest research item**: the `transaction_type` long-English corpus on the SEC provider — the keyword-to-code table in Req 6.3 lists eight canonical Form 4 codes (`P/S/A/F/M/G/D/W`) plus a forward door for "additional codes the live SEC sample reveals during research". The exhaustive mapping must be confirmed from a live SEC pass before the design freezes.
- **Largest design choice**: how to share the fatal-exit gate between `--format json` (delegates to `aggregate_emit`) and `--format md` (must fall back to JSON on all-rows-fatal per Req 5.8). The current `aggregate_emit` always emits JSON, so the wrapper has to inspect rows _before_ calling it on the markdown path.
- **Cross-cutting constraint**: the wrapper must continue to pass every parametrized invariant in `tests/integration/test_json_contract.py` (envelope root allowlist, strict-JSON gate, `tool == "insider"`, `data.results: list[dict]`, failure-row schema). The default `--provider sec` argv is already registered there (line 358).

Recommended approach: **Option A — extend `scripts/insider.py` in place**. The requirements explicitly forbid Option B/C variants (Req 6.8, Req 10.6, Req 10.7). The only design freedom is _how_ to thread the markdown path through the existing fatal-exit gate.

## 2. Document Status

Analysis approach: **brownfield gap analysis on an existing thin wrapper**. The current wrapper, `_common.aggregate_emit`, the integration-test invariants, and the `skills/` authoring conventions were all read directly. No external research was needed to enumerate the gaps; the live SEC `transaction_type` corpus is flagged as the one outstanding research item for the design phase.

## 3. Current State Investigation

### 3.1 Existing assets in scope

| Asset                                                        | Lines / Shape       | Relevance                                                                                                                                                                    |
| ------------------------------------------------------------ | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/insider.py`                                         | ~110                | The wrapper to extend. Already does multi-symbol fetch, `--days` client-side filter, `--limit`, `--provider` over `{sec,fmp,intrinio,tmx}`, `safe_call`, `aggregate_emit`.   |
| `scripts/_common.py`                                         | ~447                | Provides `safe_call`, `aggregate_emit`, `single_emit`, `emit`, `emit_error`, `wrap`, `sanitize_for_json`, `ErrorCategory`, `_decide_exit_and_warnings`, `_FATAL_CATEGORIES`. |
| `scripts/_env.py`                                            | helper              | `apply_to_openbb()` boilerplate; unchanged.                                                                                                                                  |
| `skills/insider/SKILL.md`                                    | 55 lines            | Documents the MVP. Will be rewritten to document `--transaction-codes`, `--format`, and the normalized record schema (Req 8).                                                |
| `skills/_envelope/SKILL.md`, `_errors`, `_providers`         | policy skills       | Cross-cutting; nothing changes here, the insider SKILL.md links to them per the minimal-sufficient rule.                                                                     |
| `tests/integration/test_json_contract.py`                    | 925                 | Already covers insider via `WRAPPER_HAPPY_ARGV["insider"]` (line 358) and `WRAPPER_INVALID_ARGV["insider"]` (line 411). New per-wrapper tests will live in a new file.       |
| `tests/integration/_sanity.py`, `tests/integration/conftest` | helpers             | `run_wrapper_or_xfail`, `assert_stdout_is_single_json`, `WRAPPERS`, env-override plumbing. Reusable as-is.                                                                   |
| `tests/unit/`                                                | 13 files, no insider | Existing pattern for pure-helper unit tests (e.g. `test_fundamentals_ratios.py`, `test_options_iv.py`, `test_sector_score.py`).                                              |

### 3.2 Conventions / constraints lifted from steering

- **Flat directory** (`docs/steering/structure.md`): one file per OpenBB capability; no new module file allowed for the normalization functions (Req 10.7 enforces this explicitly).
- **Default to a key-free provider**: SEC stays the default; FMP stays opt-in. The current wrapper already complies (`DEFAULT_PROVIDER = "sec"`).
- **Closed `--provider` choices**: `{sec, fmp, intrinio, tmx}` already declared.
- **Stdout = single JSON document; stderr = errors only**: the `--format md` change perforates this slightly — `--format md` emits a single markdown document instead. The cross-wrapper invariant in `test_stdout_hygiene.py` and `test_json_contract.py` only asserts JSON when the wrapper's happy argv produces JSON; the parametrized `WRAPPER_HAPPY_ARGV` for insider does not include `--format md`, so the strict-JSON gate keeps applying to the default invocation.
- **Skills are English-only, ~30–80 lines, one short output sample, no test harness** (`docs/steering/structure.md` § Agent Skills, plus user memories `feedback_skill_minimal_sufficient.md` and `feedback_skill_authoring_no_tests.md`). Req 8.3 / 8.5 / 8.6 already encode this.
- **OpenBB-call layer is integration-only; pure helpers may live under `tests/unit/`** (`docs/steering/tech.md`). Req 9.7 mirrors this — normalization helpers may be unit-tested.

### 3.3 Patterns reused vs. patterns introduced

- **Reused**: `argparse` boilerplate, `safe_call`, `aggregate_emit`, `_filter_by_days` semantics (extended to also pre-cede the code filter), `ErrorCategory` taxonomy, multi-symbol sequential fetch, exit-code contract, NaN sanitization, env-override-driven credential gates.
- **New to this wrapper**:
  - A normalized record schema (Req 6.2, ~20 fields).
  - Per-provider normalization functions (`_normalize_sec_record`, `_normalize_fmp_record`) called inside `fetch()`.
  - A SEC-side keyword-to-code lookup table for `transaction_type` long-English → single-letter code.
  - An FMP-side leading-letter regex (`^[A-Z]-`).
  - A `--transaction-codes` filter applied **after** `--days` (Req 3.5).
  - A `dropped_unparseable_codes` per-row counter, populated only when the filter is active and only on success rows (Req 4.4 / 6.5).
- **New to the repo entirely**:
  - `--format md` is the **first non-JSON stdout path** in any wrapper. This is a structural precedent worth flagging — every existing test (`test_stdout_hygiene.py`, `test_json_contract.py`'s strict-JSON gate) assumes stdout is JSON. Mitigation: the parametrized invariants are keyed off `WRAPPER_HAPPY_ARGV`, which does not pass `--format md` for insider, so they remain valid; new `tests/integration/test_insider.py` cases assert markdown invariants only when `--format md` is passed.
  - A pipe / newline escape pass on free-text fields for markdown safety (Req 5.7).

## 4. Requirement-to-Asset Map

Tags: **(reuse)** = existing code/pattern fits as-is; **(extend)** = existing site needs targeted edits; **(new)** = no current implementation; **(unknown)** = research needed before design freeze.

| Req                                                            | What it asks for                                                  | Asset                                              | Tag                                                                                       |
| -------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 1.1 multi-ticker `nargs="+"`                                   | argparse positional                                                | `scripts/insider.py:79`                            | reuse                                                                                     |
| 1.2 zero-ticker rejection                                      | argparse default behaviour                                         | argparse                                           | reuse                                                                                     |
| 1.3 `--days` default 90                                        | argparse `int`                                                     | `scripts/insider.py:86-90`                         | reuse                                                                                     |
| 1.4 `--days` ≤ 0 rejection                                     | argparse `type=` callable + early validation                       | none                                               | new — needs a `_positive_int` guard mirroring `entry_timing_scorer.py` patterns           |
| 1.5 `--limit` passthrough                                      | argparse + kwarg branch                                            | `scripts/insider.py:91-96, 64-67`                  | reuse                                                                                     |
| 1.6 `.T` suffix passthrough                                    | wrapper does not transform symbols                                 | implicit                                           | reuse                                                                                     |
| 2.1 SEC default                                                | `DEFAULT_PROVIDER = "sec"`                                        | `scripts/insider.py:36`                            | reuse                                                                                     |
| 2.2 closed provider choices                                    | `PROVIDER_CHOICES = ["sec","fmp","intrinio","tmx"]`               | `scripts/insider.py:35`                            | reuse                                                                                     |
| 2.3 FMP missing key → `credential` exit 2                       | `safe_call` + `aggregate_emit` fatal gate                          | `_common.py`                                       | reuse                                                                                     |
| 2.4 FMP free-tier no entitlement → `plan_insufficient`          | `_PLAN_MESSAGE_RE` already classifies 402                          | `_common.py:104-110`                               | reuse                                                                                     |
| 2.5 FMP not promoted to default                                | already enforced                                                   | `scripts/insider.py:36`                            | reuse                                                                                     |
| 2.6 schema invariant under every `--provider`                  | normalization functions                                            | none                                               | new                                                                                       |
| 3.1 / 3.2 / 3.3 day-window on `transaction_date` / `filing_date` | `_filter_by_days`                                                  | `scripts/insider.py:52-61`                         | reuse                                                                                     |
| 3.4 day-window across providers                                | already provider-agnostic                                          | `scripts/insider.py:52-61`                         | reuse                                                                                     |
| 3.5 day-window before code filter                              | sequencing change                                                  | `fetch()`                                          | extend — re-order: fetch → normalize → day-window → code-filter                          |
| 4.1 `--transaction-codes <CSV>`                                | argparse                                                           | none                                               | new                                                                                       |
| 4.2 unfiltered when omitted                                    | branch when `args.transaction_codes is None`                       | none                                               | new                                                                                       |
| 4.3 case-insensitive match on normalized code                  | filter helper                                                      | none                                               | new                                                                                       |
| 4.4 drop null-code rows + per-row `dropped_unparseable_codes`   | filter helper that returns `(kept, dropped_count)`                  | none                                               | new                                                                                       |
| 4.5 regex `^[A-Za-z]$` validation                              | argparse `type=` callable                                          | none                                               | new                                                                                       |
| 4.6 echo `transaction_codes` under `data`                       | `query_meta` extension                                             | `scripts/insider.py:103`                           | extend                                                                                    |
| 4.7 no `--side` flag                                           | scope rule; nothing to add                                         | n/a                                                | reuse                                                                                     |
| 5.1 `--format {json,md}` choice                                | argparse                                                           | none                                               | new                                                                                       |
| 5.2 default JSON via `aggregate_emit`                          | existing path                                                      | `scripts/insider.py:106`                           | reuse                                                                                     |
| 5.3 `--format md` per-ticker section + table                   | new emitter                                                        | none                                               | new                                                                                       |
| 5.4 markdown error-line rendering                              | new emitter                                                        | none                                               | new                                                                                       |
| 5.5 markdown empty-section rendering                           | new emitter                                                        | none                                               | new                                                                                       |
| 5.6 closed column set drawn from normalized schema              | new emitter                                                        | none                                               | new                                                                                       |
| 5.7 escape `|` and newline in cells                             | helper inside emitter                                              | none                                               | new                                                                                       |
| 5.8 markdown fatal fall-back to JSON                           | pre-emit fatal-detection                                           | `_common._decide_exit_and_warnings`                | extend — call-site needs to consult fatal status before choosing emitter                  |
| 5.9 single trailing newline                                    | both emitters                                                      | `_common.emit` already adds `\n`                   | reuse for JSON; new for markdown                                                          |
| 6.1 row shape with `dropped_unparseable_codes`                 | `fetch()` row construction                                         | extend                                             | extend                                                                                    |
| 6.2 normalized record schema (~20 fields)                      | per-provider normalization                                         | none                                               | new                                                                                       |
| 6.3 SEC keyword-to-code table                                  | constant + lookup                                                  | none                                               | new + **unknown** — exact long-English corpus                                              |
| 6.3 FMP `^[A-Z]-` extraction                                   | regex                                                              | none                                               | new                                                                                       |
| 6.3 intrinio / tmx → null                                      | default branch in normalizer                                       | none                                               | new                                                                                       |
| 6.4 echo `provider/days/transaction_codes/limit` under `data`   | `query_meta` extension                                             | `scripts/insider.py:103-105`                       | extend                                                                                    |
| 6.5 ok=false rows omit `records` / `dropped_unparseable_codes`  | row builder branch                                                 | extend                                             | extend                                                                                    |
| 6.6 NaN / Inf sanitization                                     | `sanitize_for_json` runs inside `emit`                             | `_common.py:194-217`                               | reuse                                                                                     |
| 6.7 no `"source"` constant added                               | rule                                                               | n/a                                                | reuse                                                                                     |
| 6.8 normalization stays in `scripts/insider.py`                | rule                                                               | n/a                                                | constraint                                                                                |
| 7.1 `safe_call` guards every OpenBB call                       | already does                                                       | `scripts/insider.py:68`                            | reuse                                                                                     |
| 7.2 closed `error_category` taxonomy                           | already enforced                                                   | `_common.ErrorCategory`                            | reuse                                                                                     |
| 7.3 SEC_USER_AGENT unset → `credential` no retry                | `safe_call` classification                                         | `_common.py`                                       | reuse                                                                                     |
| 7.4 empty upstream → `ok:true, records:[]`                     | `to_records` returns `[]`                                          | `_common.py:46-71`                                 | reuse                                                                                     |
| 7.5 exit code contract                                         | `aggregate_emit`                                                   | `_common.py`                                       | reuse                                                                                     |
| 7.6 stdout single document, traceback to stderr                | wrapper invariant                                                  | reuse                                              | reuse                                                                                     |
| 8.1 / 8.2 SKILL.md updates                                     | rewrite                                                            | `skills/insider/SKILL.md`                          | extend                                                                                    |
| 8.3 ~30–80 lines, English only                                 | authoring rule                                                     | `skills/AUTHORING.md`                              | constraint                                                                                |
| 8.4 INDEX.md only if mention needed                            | check                                                              | `skills/INDEX.md`                                  | extend (likely)                                                                           |
| 8.5 / 8.6 no skill tests, live-run sample                       | rule                                                               | n/a                                                | reuse                                                                                     |
| 9.1 pass `test_json_contract.py` under `--provider sec`         | already registered                                                 | `test_json_contract.py:358, 411`                   | reuse                                                                                     |
| 9.2 SEC integration-test cases                                 | new file                                                           | none                                               | new                                                                                       |
| 9.3 FMP integration-test cases (skip-gated)                    | new file + `pytest.skip` on `FMP_API_KEY`                          | none                                               | new                                                                                       |
| 9.4 cross-provider schema-consistency test                     | new file                                                           | none                                               | new                                                                                       |
| 9.5 markdown-format invariants                                 | new file                                                           | none                                               | new                                                                                       |
| 9.6 `data.transaction_codes` and `dropped_unparseable_codes` echoes | new file                                                           | none                                               | new                                                                                       |
| 9.7 unit tests on pure normalizers                             | new file under `tests/unit/`                                       | none                                               | new                                                                                       |
| 10.1 – 10.7 scope boundaries                                   | rules; no code change beyond compliance                            | n/a                                                | constraint                                                                                |

### 4.1 Open research items (carry into design)

1. **SEC provider `transaction_type` corpus** — _Unknown_. Req 6.3 already names two strings observed in the live pass ("Open market or private sale of non-derivative or derivative security", "Grant, award or other acquisition pursuant to Rule 16b-3(d)") and lists the eight canonical Form 4 codes that the lookup must cover, but the **complete keyword list per code** is not yet captured. The design phase should capture, e.g., five to ten target tickers (those listed in the requirements: AAPL / FLXS / ASC / CMCL / BAC / JPM / C / MS) under `--days 365`, dump the unique `transaction_type` strings, and lock the keyword → code table in design.md before implementation.
2. **Intrinio / TMX `transaction_type` shape** — _Unknown_. Req 6.3 defers these to "undefined" and emits `null`, which is acceptable for MVP; the design phase only needs to confirm the wrapper does not crash on whatever shape they return (most likely a passthrough through `to_records`).
3. **FMP-only field corpus** — _Mostly known_ from the requirements (the two prefix shapes `S-Sale` / `M-Exempt` / etc. and the `"officer: " / "director: " / "ten_percent_owner: "` prefixes). Confirm in design that the FMP field set the wrapper expects (`form_type`, `url`, `ownership_type` `"D"` / `"I"`) is still the live shape on a date close to design.md sign-off.
4. **Markdown emitter shape across all eight Form 4 codes** — _Known requirement, unknown sample_. The SKILL.md sample (Req 8.6) must come from a live run. Design should pre-pick one or two candidate tickers known to carry both `P` and `S` rows in the `--days 90` window so the SKILL sample exercises the typical `--transaction-codes P,S` flag.

## 5. Implementation Approach Options

### Option A — Extend `scripts/insider.py` and rewrite `skills/insider/SKILL.md` in place ★ recommended

**When applies**: Req 6.8 mandates this layout; Req 10.7 explicitly forbids splitting normalization out. There is no policy room for B or C.

- **Scope**: `scripts/insider.py` grows from ~110 → ~300–400 lines: new normalizer pair, code-filter helper, markdown emitter, argparse extensions, fatal-gate inspection on the markdown path, validation callables.
- **Compatibility**: Default invocation (no new flags) keeps the same envelope shape **except** that records now carry the canonical schema, not provider-native keys. The current `skills/insider/SKILL.md` users are AI agents, not human callers; the analyst-side prompt templates that quote `securities_transacted` / `transaction_price` will break — but the requirements consciously accept this break in exchange for a single parser. **Backward-compatibility note** worth surfacing in design: do we ship a deprecation shim, a one-time migration note, or a hard cut? The requirements imply a hard cut (Req 6.2 is exhaustive on field names; nothing about preserving old keys). Confirm in design.
- **Test impact**: existing parametrized invariants keep passing because they only inspect envelope-level keys. New behaviour is covered by a new `tests/integration/test_insider.py` (Req 9.2 / 9.3 / 9.4 / 9.5 / 9.6) plus targeted unit tests under `tests/unit/test_insider_normalize.py` (Req 9.7).
- **Complexity / maintainability**: ~3× line growth on a previously-tiny wrapper is the main risk. Mitigations:
  - Keep the per-provider branch a single `if provider == "sec": ... else: ...` site (Req 6.8).
  - Pure helpers (`_normalize_sec_record`, `_normalize_fmp_record`, `_strip_role_prefix`, `_extract_fmp_code`, `_lookup_sec_code`, `_compute_total_value`, `_apply_code_filter`, `_render_markdown`, `_escape_md_cell`) keep `fetch()` and `main()` linear and reviewable.
  - The `_SEC_KEYWORD_TO_CODE` lookup is a frozen module-level dict — it will be the single biggest constant in the file, but it's data, not control flow.

**Trade-offs**:

- ✅ Matches mandated structure (Req 6.8, Req 10.7).
- ✅ Reuses every existing helper (`safe_call`, `aggregate_emit`, `sanitize_for_json`, `ErrorCategory`).
- ✅ All tests stay in conventional locations (`tests/integration/test_insider.py`, `tests/unit/test_insider_normalize.py`).
- ❌ `scripts/insider.py` ends up the second-thickest wrapper after `sector_score.py` (~500 lines) and `entry_timing_scorer.py` (~600 lines). Still within precedent.
- ❌ Hard cut on the record-schema field names is a breaking change for any caller that read provider-native keys directly from `records[]`.

### Option B — Move normalization to a `_insider_normalize.py` helper module

**When would apply**: if the wrapper exceeded ~500 lines or the normalization were reused across multiple wrappers.

- **Explicitly forbidden** by Req 10.7: "shall not split the normalization into a separate module file (e.g. `_insider_normalize.py`); the logic stays inside `scripts/insider.py` to preserve the flat-directory wrapper convention".
- Listed here only for completeness; rejected.

### Option C — Introduce a `scripts/_format.py` cross-cutting markdown helper

**When would apply**: if multiple wrappers were going to grow `--format md`.

- Today only insider needs markdown. The repo's house style (`docs/steering/structure.md`) says "Anything wider than one wrapper belongs in a helper, not duplicated across scripts." Extracting prematurely would be an abstraction-on-spec rather than abstraction-on-need.
- Recommended posture: keep `_render_markdown` private to `scripts/insider.py` for now, and re-extract only when a second wrapper takes a `--format md`. The cost of re-extracting later is one PR; the cost of an unused-helper today is dead code on every other wrapper's import graph.
- Listed as a deferred future option, not the design path.

## 6. Effort & Risk

- **Effort: M (3–7 days)**
  - Wrapper code: ~1.5 days (argparse, normalizers, code filter, markdown emitter, fatal-gate path, validation).
  - SEC keyword-to-code table: ~0.5 day (live pass on 6–10 tickers, lock the table in design.md).
  - SKILL.md rewrite from a live run: ~0.5 day (Req 8 + minimal-sufficient budget).
  - Integration tests (Req 9.1–9.6): ~1.5 days, including the FMP skip-gate plumbing and the cross-provider consistency test.
  - Unit tests (Req 9.7): ~0.5 day.
  - One-off live-verification gate per project memory `feedback_verification_and_simplicity.md`: ~0.5 day; numbers go into the design / completion notes.
- **Risk: Medium**
  - **High-risk item**: SEC `transaction_type` long-English corpus is incomplete in the requirements; the lookup table will need iteration when an unmapped string surfaces. Mitigation: emit `null` (per Req 6.3) on miss instead of crashing; log the unmapped raw string under stderr for triage; tighten the table in a follow-up. The `transaction_type_raw` field (Req 6.2) preserves the audit trail so misses are diagnosable from the envelope alone.
  - **Medium-risk item**: `--format md` is a structural first for this repo. Mitigation: the parametrized envelope-invariant suite is keyed on JSON-only argv, so a markdown emitter cannot regress JSON callers; the new test cases (Req 9.5) close the markdown-shape side.
  - **Medium-risk item**: hard cut on record-schema field names breaks any caller that read `securities_transacted` directly. Mitigation: SKILL.md update plus the cross-provider consistency test; surface the break in design.md and PR description.
  - **Low-risk item**: `dropped_unparseable_codes` per-row counter must not appear on `ok:false` rows (Req 6.5) — easy to forget. Mitigation: an integration assertion (Req 9.6 covers `0` when filter inactive; the design should add one for the failure-row case).

## 7. Recommendations for Design Phase

1. **Lock the SEC keyword-to-code table in design.md.** Run `uv run scripts/insider.py AAPL FLXS ASC CMCL BAC JPM JNJ XOM MSFT GOOG --days 365 --provider sec`, extract the unique `transaction_type` strings, map each to one of `P/S/A/F/M/G/D/W` (or to `null` with rationale), and embed the table verbatim in design.md so the implementation phase has a fixed target.
2. **Decide the backward-compatibility posture on field names.** The simplest read of the requirements is a hard cut. Confirm in design.md (one paragraph) and call it out in the SKILL.md migration line.
3. **Pin the markdown emitter's column ordering.** Req 5.6 lists the closed column set; the design should pin the **column order** so the test in Req 9.5 can assert it without ambiguity (suggested ordering: `filing_date, transaction_date, reporter_name, reporter_title, transaction_code, transaction_code_label, shares, price, total_value, shares_after, url`).
4. **Spell out the fatal-gate path on the markdown branch.** The cleanest shape is: build per-ticker rows → call `_decide_exit_and_warnings(rows)` → if `fatal is not None`, delegate to `aggregate_emit` (which emits JSON and exits 2); else render markdown to stdout and return 0. Confirm this in design so the implementer does not invent a parallel branch.
5. **Decide whether `dropped_unparseable_codes` appears in markdown.** Req 5.6's column set does not include it, and Req 5.5's empty-section line says `_no records in window_` — but a heavy filter rejection (10 dropped, 0 kept) reads identically to "no upstream activity". Suggested resolution: render `_no records in window_ (dropped <N> unparseable codes)` when the filter is active and the table is empty due to drops; otherwise just `_no records in window_`. Confirm in design.
6. **Capture one live-run sample for SKILL.md.** Run `uv run scripts/insider.py AAPL --days 90 --transaction-codes P,S --format md` after implementation, paste the first three rows of the output into SKILL.md (per the "at most one short output sample" budget), and check the line count stays in the 30–80 range.
7. **Verification gate (one-off).** Per the user-memory feedback `feedback_verification_and_simplicity.md`, the completion phase must run the full `uv run pytest -m integration` suite end-to-end (not the offline gate) and report the actual pass / xfail / skip counts in the completion artefact, not infer them from a static gate.

## 8. Out-of-Scope Confirmation

The following are **not** in scope for this skill (Req 10) and must not creep into design or implementation:

- Cluster-buying / cross-ticker pattern detection.
- Role-level aggregation (CEO / CFO / Director rollups) and buy-vs-sell ratios.
- Persistence (cache files, DB writes, log files).
- Discord notification or any side effect beyond stdout / stderr.
- `portfolio.yaml` parsing or holdings resolution inside the wrapper.
- A second module file for normalization helpers.
- A `--side {buy,sell,all}` flag (Req 4.7 mandates `--transaction-codes P` / `--transaction-codes S` instead).

These are explicitly enumerated as future tasks in the input description (`shared/openbb/docs/tasks/todo/insider-trading-skill/requirements.md` § "将来拡張").

## 9. Next Steps

- Review this gap analysis with the operator.
- Run `/sdd-spec-design insider-trading-skill` (or `… -y` to fast-track requirements approval) to draft `design.md` covering: SEC keyword-to-code table, fatal-gate / markdown path interaction, column ordering, backward-compat posture, and the verification plan.
- After design freeze, run `/sdd-spec-tasks insider-trading-skill` to slice the work into reviewable PR units (suggested split: argparse + normalizers + day/code filters in PR1; markdown emitter + SKILL.md rewrite in PR2; integration + unit tests + verification-gate run in PR3).
