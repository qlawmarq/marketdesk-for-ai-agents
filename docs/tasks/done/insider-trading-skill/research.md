# Research & Design Decisions — insider-trading-skill

---
**Purpose**: Capture discovery findings, architectural investigations, and rationale that inform the technical design.

**Usage**:
- Log research activities and outcomes during the discovery phase.
- Document design decision trade-offs that are too detailed for `design.md`.
- Provide references and evidence for future audits or reuse.
---

## Summary

- **Feature**: `insider-trading-skill`
- **Discovery Scope**: Extension (brownfield in-place upgrade of `scripts/insider.py` + `skills/insider/SKILL.md`; `docs/settings/rules/design-discovery-light.md` applies)
- **Key Findings**:
  - The SEC provider's `transaction_type` long-English corpus is **closed at 10 distinct strings + 1 null** under a 365-day window across 10 large-cap / mid-cap tickers (1,458 records total). Two strings — `"Conversion of derivative security"` and `"Other acquisition or disposition (describe transaction)"` — extend the canonical 8-code set in Requirement 6.3 to **10 codes** (`P/S/A/F/M/G/D/W/J/C`), and the keyword-to-code lookup can be a **frozen exact-match dict** rather than substring matching.
  - The FMP provider on a 90-day live pass against AAPL exposes exactly the field shapes documented in Requirement 6.2 — `transaction_type` as `X-Word`, `ownership_type` as a single letter, `acquisition_or_disposition` as a single letter, and `owner_title` with `"officer: "` / `"director"` / compound `"director, officer: …"` prefixes — confirming the normalization plan with one wrinkle: **compound `owner_title` values** (e.g., `"director, officer: Chief Executive Officer"`) are real and force a small extension to the prefix-stripping rule.
  - SEC already returns `acquisition_or_disposition` as `"Acquisition"` / `"Disposition"` (full word) and `ownership_type` as `"Direct"` / `"Indirect"`, while FMP returns single letters; the normalization is a one-direction collapse on each provider, **not** a synthesis from scratch.

## Research Log

### Topic 1 — SEC `transaction_type` long-English corpus (lock the keyword-to-code table)

- **Context**: Requirement 6.3 names two SEC strings observed in a prior pass and lists 8 canonical Form 4 codes (`P/S/A/F/M/G/D/W`), but explicitly leaves a forward door for "additional codes the live SEC sample reveals during research". The gap analysis (§4.1) flagged this as the single largest open research item: the design phase must lock the table before implementation freezes.
- **Sources Consulted**:
  - Live run: `uv run scripts/insider.py AAPL MSFT GOOG JPM BAC C MS XOM JNJ FLXS --days 365 --provider sec --limit 200` (2026-04-30, exit 0, 2,036,276 stdout bytes, 1,458 records across 10 symbols).
  - SEC Form 4 General Instructions, "Table I and II — Transaction Codes" (the canonical Form 4 transaction-code table).
- **Findings**: The unique non-null `transaction_type` strings under that 365-day window were exactly:
  | Count | SEC long-English `transaction_type` | Form 4 code |
  | ----: | ----------------------------------- | ----------- |
  |   454 | `"Exercise or conversion of derivative security exempted pursuant to Rule 16b-3"` | `M` |
  |   322 | `"Grant, award or other acquisition pursuant to Rule 16b-3(d)"` | `A` |
  |   262 | `"Open market or private sale of non-derivative or derivative security"` | `S` |
  |   244 | `"Payment of exercise price or tax liability by delivering or withholding securities incident to the receipt, exercise or vesting of a security issued in accordance with Rule 16b-3"` | `F` |
  |    44 | `"Conversion of derivative security"` | `C` (new) |
  |    35 | `"Bona fide gift"` | `G` |
  |    25 | `"Disposition to the issuer of issuer equity securities pursuant to Rule 16b-3(e)"` | `D` |
  |    11 | `"Other acquisition or disposition (describe transaction)"` | `J` (new) |
  |    10 | `"Open market or private purchase of non-derivative or derivative security"` | `P` |
  |     1 | `"Acquisition or disposition by will or the laws of descent and distribution"` | `W` |
  |    50 | `None` | `null` |
- **Implications**:
  - The lookup table is **a frozen dict of exact strings → single-letter codes**, not substring matching. Exact-match avoids the trap that `"Conversion of derivative security"` would also substring-match `"Exercise or conversion of derivative security exempted pursuant to Rule 16b-3"` (both contain `"conversion of derivative security"` case-insensitively); two distinct codes (`C` vs `M`) hinge on that distinction.
  - The set of codes the wrapper must support is **10**, not 8 — `J` (Other acquisition or disposition, the catch-all that the SEC fills via the footnote field) and `C` (Conversion of derivative security, the non-exempt-rule counterpart of `M`) are real on big-cap insider-trading data and must be in the table on day one. The "additional codes the live SEC sample reveals" door in Requirement 6.3 is what unlocks this.
  - Roughly **3.4 %** of records (50 / 1,458) carry a null upstream `transaction_type` — those normalize to `transaction_code: null` and are dropped by `--transaction-codes` per Requirement 4.4 with `dropped_unparseable_codes` accounting.
  - On a **table miss** during implementation (a future SEC string the corpus did not surface), the wrapper emits `transaction_code: null` plus a one-line `print(..., file=sys.stderr)` of the raw string so triage is trivial; the audit trail still flows through `transaction_type_raw` per Requirement 6.2.

### Topic 2 — FMP record shape (confirm the requirements snapshot)

- **Context**: The requirements snapshot of the FMP shape (`X-Word` `transaction_type`, `"officer: "` / `"director: "` / `"ten_percent_owner: "` `owner_title` prefixes, single-letter `ownership_type` `"D"` / `"I"`) was based on a pre-spec live pass; the design phase needs a refresh-before-freeze (gap-analysis §4.1.3).
- **Sources Consulted**: Live run `uv run scripts/insider.py AAPL --days 90 --provider fmp --limit 50` (2026-04-30, exit 0, 50 records, no stderr output, no plan-insufficient gate triggered on the operator's keyed plan).
- **Findings**:
  - `transaction_type` shapes observed: `"M-Exempt"` (26), `"S-Sale"` (9), `"A-Award"` (7), `"F-InKind"` (6), `"G-Gift"` (1), `""` (1, empty). The leading-letter regex `^[A-Z]-` covers every non-empty case; the empty string normalizes to `null` per Requirement 6.3.
  - `acquisition_or_disposition` is **already a single letter** on FMP (`"D"` for every record in the AAPL 90-day window) — no normalization needed on this provider; SEC's full-word `"Acquisition"` / `"Disposition"` is what gets normalized to first-letter.
  - `ownership_type` is **always `"D"`** in this 90-day AAPL slice; the `"I"` variant from the requirements snapshot was not observed but the expansion table `{"D": "Direct", "I": "Indirect"}` covers both.
  - `owner_title` distributions include compound shapes the requirements did not enumerate: `"director, officer: Chief Executive Officer"` (11 of 50), `"director"` (12), plus the documented `"officer: <title>"` shapes. Pure `"ten_percent_owner: "` prefix was not seen on AAPL in this window but the SEC equivalent (the `ten_percent_owner: True` boolean) was observed elsewhere on FLXS / ASC in the SEC pass.
  - Field-name divergences match the requirements: `form_type` vs SEC's `form`, `url` vs SEC's `filing_url`. FMP records carry **no** `footnote`, `company_name`, `officer` / `director` / `ten_percent_owner` boolean flags, `underlying_security_*`, or `nature_of_ownership` — the normalized schema's `footnote: null` on FMP per Requirement 6.2 is correct and the design must not rely on those fields existing on FMP.
- **Implications**:
  - The FMP normalization function is a straight rename + small expansion table, no fallbacks required.
  - The compound `owner_title` value (`"director, officer: <title>"`) is a real wrinkle. The cleanest rule is "strip a leading `"officer: "` / `"director: "` / `"ten_percent_owner: "`; if the value still contains a `", officer: "` / `", director: "` infix, keep the bare title after the last such marker". This is captured as Decision 4 below.
  - No new credential, no new endpoint, no new provider behaviour. The `--provider fmp` path remains a paid-plan opt-in.

### Topic 3 — Existing wrapper baseline and integration-test hooks

- **Context**: Confirm the current shape of `scripts/insider.py`, `_common.aggregate_emit`, and `tests/integration/test_json_contract.py` before the implementation phase plans the diff.
- **Sources Consulted**: `scripts/insider.py` (110 lines), `scripts/_common.py`, `tests/integration/test_json_contract.py` (the registered insider argv at line 358 / 411 per gap-analysis §3.1).
- **Findings**:
  - The baseline already implements: `nargs="+"` symbols, `--days` / `--limit` / `--provider`, `safe_call`, `aggregate_emit` with `tool="insider"`, `_filter_by_days` on `transaction_date` then `filing_date`, and the `query_meta = {"provider", "days", "limit?"}` echo under `data`. Requirements 1.1, 1.3, 1.5, 1.6, 2.1, 2.2, 3.1–3.4, 5.2, 7.1–7.6 are all already satisfied by the existing baseline.
  - The fields the new schema needs to source — `securities_transacted`, `transaction_price`, `securities_owned`, `owner_name`, `owner_title`, `acquisition_or_disposition`, `transaction_type`, `security_type`, `company_cik`, `owner_cik`, `filing_date`, `transaction_date`, `footnote`, plus `form` / `filing_url` (SEC) and `form_type` / `url` (FMP) — **all** appear on the live record samples; the wrapper does not need to invent or compute any field except `total_value` (`shares × price`).
  - `aggregate_emit` always emits JSON and decides exit code via `_decide_exit_and_warnings(rows)`. For the markdown path (Requirement 5.8 / gap-analysis §7.4), the cleanest shape is to **call `_decide_exit_and_warnings` directly before choosing the emitter**: if it returns a fatal exit, delegate to `aggregate_emit` (so JSON + exit 2 still happen); else render markdown and return 0. This re-uses the existing fatal taxonomy without duplicating policy.
- **Implications**:
  - The diff is concentrated in `fetch()` (now also calls a normalizer per provider, returns the day-window-then-code-filter sequence, and surfaces `dropped_unparseable_codes`) and `main()` (new argparse flags, new query_meta echo, fatal-gate inspection, branch into JSON vs markdown emitter).
  - Helpers stay private to `scripts/insider.py` per Requirement 6.8 / 10.7 — no new file under `scripts/`.

### Topic 4 — Markdown output as a structural first

- **Context**: Requirement 5 introduces `--format md`, the **first non-JSON stdout path** in any wrapper (gap-analysis §3.3). The cross-wrapper invariant suite (`test_json_contract.py`, `test_stdout_hygiene.py`) assumes stdout is JSON.
- **Sources Consulted**: `tests/integration/test_json_contract.py` (the parametrized gates are keyed off `WRAPPER_HAPPY_ARGV`, which for `insider` does **not** include `--format md`), the project memory note `feedback_skill_authoring_no_tests.md`, and the design principle in `docs/steering/structure.md` § Code Organization Principles ("the caller owns persistence; wrappers only emit JSON to stdout").
- **Findings**:
  - The strict-JSON gate continues to apply to the default invocation because the registered argv does not pass `--format md`. The new tests (Requirement 9.5) cover markdown invariants only when `--format md` is passed; the cross-wrapper suite is **not** weakened.
  - The fatal-gate fall-back (Requirement 5.8) ensures even the markdown path emits **JSON** on exit 2 — so any agent that ingests `insider --format md` and gets exit 2 still gets a parseable failure envelope. This preserves the agent-friendly JSON contract under the only condition (all-rows-fatal) where markdown would be useless anyway.
  - The pipe / newline escape pass (Requirement 5.7) needs to handle long English `transaction_type_raw` values — strings up to 244 characters were observed in the SEC corpus, none containing pipes or newlines, but `footnote` strings on the SEC provider regularly run multi-paragraph (the AAPL sample row carried a 232-char footnote and additional rows in the corpus exceed 800 characters with embedded newlines). The escape rule must apply to **every** rendered cell, not only `transaction_type_raw`.
- **Implications**:
  - Markdown is a localized addition to insider; do **not** extract a `_format.py` cross-cutting helper today (gap-analysis Option C). Re-extract only when a second wrapper takes a `--format md`. Confirmed in Decision 5 below.
  - The escape helper must be a closed `str.translate` over `{"|": "\\|", "\n": " ", "\r": ""}` (or equivalent) so cell content cannot break the table.

### Topic 5 — Backward-compatibility posture on record-schema field renames

- **Context**: Requirement 6.2 mandates a single normalized schema. That changes record-level field names from provider-native (`securities_transacted`, `transaction_price`, `filing_url`, …) to canonical (`shares`, `price`, `url`, …). Gap-analysis §6 flagged this as the medium-risk item: any caller that read provider-native keys will break.
- **Sources Consulted**: `git log --all -- scripts/insider.py skills/insider/SKILL.md` (existing wrapper has been live since the first cut; no in-tree caller currently parses `records[]`), grep for `securities_transacted` / `transaction_price` / `filing_url` across `scripts/`, `skills/`, `tests/` and the `investment-analyst` / `investment-reviewer` agent directories under the project root.
- **Findings**:
  - There are zero in-tree consumers of `records[]` field names today. The existing tests inspect envelope-level keys only; the existing `skills/insider/SKILL.md` documents the provider-native field paragraph but no automation reads it.
  - The downstream consumers are the analyst / reviewer agents, whose proposal-draft templates read the envelope ad-hoc per session. A hard cut means one prompt-template update per agent at first use; a deprecation shim means carrying both schemas until manually retired.
- **Implications**: A **hard cut** is the simplest read of the requirements (Requirement 6.2 is exhaustive on field names; nothing about preserving old keys). The SKILL.md rewrite (Requirement 8.2) is the single source of truth for the new shape. Confirmed in Decision 1 below.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| **A — Extend `scripts/insider.py` in place** ★ | Add normalizers, code filter, markdown emitter, fatal-gate path, validation callables to the existing thin wrapper. SKILL.md rewritten in place. | Matches mandated layout (Req 6.8, Req 10.7); reuses every existing helper; tests stay in conventional locations. | Wrapper grows ~110 → ~300–400 lines (still within precedent — `sector_score.py`, `entry_timing_scorer.py` are larger). Hard cut on field names breaks any out-of-tree caller that read `securities_transacted` directly. | Recommended. Forbidden alternatives B/C below. |
| B — Move normalization to `scripts/_insider_normalize.py` | Hoist the per-provider normalization functions and the SEC keyword-to-code dict into a new helper module. | Smaller `insider.py`. | **Explicitly forbidden** by Requirement 10.7 ("shall not split the normalization into a separate module file"). | Rejected. |
| C — Introduce `scripts/_format.py` cross-cutting markdown helper | Extract the markdown emitter into a shared helper anticipating future wrappers with `--format md`. | Reusable for the next wrapper that needs markdown. | Premature abstraction — only insider needs markdown today; `docs/steering/structure.md` says "Anything wider than one wrapper belongs in a helper, not duplicated across scripts" but the rule fires on duplication, not anticipation. | Deferred. Re-extract when a second wrapper takes `--format md`. |

## Design Decisions

### Decision 1: Hard cut on record-schema field names (no deprecation shim)

- **Context**: Requirement 6.2 mandates a closed normalized schema. Provider-native keys (`securities_transacted`, `transaction_price`, `filing_url`, `form`, …) disappear from `records[]`; canonical keys (`shares`, `price`, `url`, `form_type`, …) replace them.
- **Alternatives Considered**:
  1. Hard cut — emit only the new schema in one PR; SKILL.md rewrite is the breaking-change announcement.
  2. Deprecation shim — emit both old and new keys for one release, deprecate the old keys in a follow-up PR.
- **Selected Approach**: Hard cut. The new schema replaces the old in the same change; no dual emission.
- **Rationale**: Topic 5's grep found zero in-tree consumers of `records[]` field names. The downstream consumers (analyst / reviewer agents) read the envelope ad-hoc per session; a SKILL.md rewrite with a clear "schema changed" line in the migration commit body is enough. A deprecation shim doubles the record-payload size for one release with no real consumer that benefits.
- **Trade-offs**: One-shot prompt-template breakage for any external caller that read provider-native keys directly (exchange: a single schema, one parser, no branching on `data.provider`). The `transaction_type_raw` field preserves the provider-native value for audit, so the cut is replayable.
- **Follow-up**: Surface the break in the implementation PR description; check `skills/INDEX.md` to see if the row needs a "schema changed 2026-04-30" annotation.

### Decision 2: SEC keyword-to-code lookup is an exact-match frozen dict (10 keys)

- **Context**: Requirement 6.3 lets the SEC normalizer use any "keyword-to-code table aligned with the official Form 4 code set". Topic 1 surfaced 10 distinct strings + 1 null across 1,458 records.
- **Alternatives Considered**:
  1. Substring matching — scan each long-English string for keywords like "purchase", "sale", "gift".
  2. Exact-match dict — frozen mapping of the 10 known strings to single-letter codes; misses → `null` + stderr log.
  3. Regex-per-code — a list of `(re.compile(pattern), code)` pairs.
- **Selected Approach**: Exact-match dict (Option 2). Implementation:
  ```
  _SEC_TYPE_TO_CODE = {
      "Open market or private purchase of non-derivative or derivative security": "P",
      "Open market or private sale of non-derivative or derivative security": "S",
      "Grant, award or other acquisition pursuant to Rule 16b-3(d)": "A",
      "Payment of exercise price or tax liability by delivering or withholding securities "
      "incident to the receipt, exercise or vesting of a security issued in accordance with Rule 16b-3": "F",
      "Exercise or conversion of derivative security exempted pursuant to Rule 16b-3": "M",
      "Conversion of derivative security": "C",
      "Bona fide gift": "G",
      "Disposition to the issuer of issuer equity securities pursuant to Rule 16b-3(e)": "D",
      "Other acquisition or disposition (describe transaction)": "J",
      "Acquisition or disposition by will or the laws of descent and distribution": "W",
  }
  ```
- **Rationale**: Substring matching is unsafe — `"Conversion of derivative security"` is a substring of `"Exercise or conversion of derivative security exempted pursuant to Rule 16b-3"` (case-insensitive after the leading word boundary), and the two map to **different codes** (`C` vs `M`). Exact match avoids the ambiguity. A regex list is more flexible than exact match but with no current need to fuzz-match (the 365-day corpus is closed), the simpler structure wins.
- **Trade-offs**: A future SEC string the corpus did not surface emits `null` + a stderr log line of the raw string; the agent receives `transaction_code: null`, sees the row dropped from any `--transaction-codes` query, but can still read `transaction_type_raw` from the envelope. The maintenance cost of adding a new key to the dict is one-line, gated by a re-run of the live SEC pass.
- **Follow-up**: Implementation surfaces unmapped strings to stderr exactly once per session (a module-level `set` of seen-and-warned strings) so a long-running batch does not spam stderr; integration tests assert the 10-key dict shape.

### Decision 3: `transaction_code_label` is a closed dict over the 10 codes (plus the canonical 8 already in scope)

- **Context**: Requirement 6.2 names `transaction_code_label` as a human-readable label; Requirement 8.2 wants the SKILL.md to document `P`/`S`/`A`/`F`/`M`/`G`/`D`/`W` with their labels. Topic 1 added `J` and `C`.
- **Alternatives Considered**:
  1. Reuse the SEC long-English description verbatim as the label.
  2. Author short canonical labels (10–25 chars) per code, decoupled from the upstream English.
- **Selected Approach**: Option 2. Authored labels:
  | Code | `transaction_code_label` |
  | ---- | ------------------------ |
  | `P` | `"Open Market Purchase"` |
  | `S` | `"Open Market Sale"` |
  | `A` | `"Grant or Award"` |
  | `F` | `"Tax Withholding Payment"` |
  | `M` | `"Exempt Exercise or Conversion"` |
  | `C` | `"Derivative Conversion"` |
  | `G` | `"Bona Fide Gift"` |
  | `D` | `"Disposition to Issuer"` |
  | `J` | `"Other (see footnote)"` |
  | `W` | `"Will or Inheritance"` |
- **Rationale**: The SEC long-English description is verbose (one entry runs 244 chars) and would dominate the markdown table; FMP's `X-Word` post-dash text is too terse and inconsistent (`"InKind"` / `"Exempt"`). Authored labels give the analyst a stable two-to-five-word description aligned with industry usage and fit the markdown column without truncation. A `null` `transaction_code` continues to map to a `null` label (Requirement 6.2).
- **Trade-offs**: Two sources of truth (SEC long-English + authored label) — but `transaction_type_raw` preserves the SEC version verbatim, so the audit trail is not lost; the authored label is purely a presentation aid.
- **Follow-up**: SKILL.md (Requirement 8.2) documents this table once; integration tests assert that for every non-null `transaction_code` in a live record, the label is from the closed 10-value set.

### Decision 4: Owner-title prefix-stripping rule (handle compound titles)

- **Context**: Requirement 6.2 says strip the FMP-only `"officer: "` / `"director: "` / `"ten_percent_owner: "` prefixes. Topic 2 surfaced compound values like `"director, officer: Chief Executive Officer"` that the requirements did not enumerate.
- **Alternatives Considered**:
  1. Strip only a single leading prefix; leave compound values as-is.
  2. Strip the **last** occurrence of `", officer: "` / `", director: "` / `", ten_percent_owner: "` infix, otherwise strip the leading prefix.
  3. Strip every prefix, even nested.
- **Selected Approach**: Option 2. Pseudocode:
  ```python
  def _strip_role_prefix(s: str | None) -> str | None:
      if not s:
          return s
      for marker in (", officer: ", ", director: ", ", ten_percent_owner: "):
          idx = s.rfind(marker)
          if idx != -1:
              return s[idx + len(marker):].strip() or None
      for prefix in ("officer: ", "director: ", "ten_percent_owner: "):
          if s.startswith(prefix):
              return s[len(prefix):].strip() or None
      if s in ("officer", "director", "ten_percent_owner"):
          return s.title()  # bare role → "Director" etc.
      return s
  ```
- **Rationale**: The compound case `"director, officer: Chief Executive Officer"` is the more interesting role for analyst evidence (CEO who is also a director); the bare role `"director"` is informative enough to keep as the title when no specific officer role appears. Option 1 leaves a markdown cell that reads as a sentence; Option 3 risks dropping the substantive title when the prefix order varies.
- **Trade-offs**: A future FMP shape that uses a new role marker (e.g. `"ten_percent_owner: "` standalone — not yet observed on AAPL) would still be handled by the leading-prefix branch. A truly novel shape falls through to "return as-is", which the analyst can still read.
- **Follow-up**: Unit test (Requirement 9.7) covers all four shapes — bare prefix, compound infix, bare role-only, no marker.

### Decision 5: Fatal-gate ordering on the markdown path — add a thin public helper `is_fatal_aggregate`

- **Context**: Requirement 5.8 mandates that an all-rows-fatal exit (every input failed with `credential` or `plan_insufficient`) falls back to JSON-envelope emission for that exit; markdown is not emitted on fatal exits. The current `aggregate_emit` always emits JSON. The markdown path needs to **peek at the fatal status before choosing the emitter** without leaking `_common.py`'s private names into wrapper code.
- **Alternatives Considered**:
  1. Add a `success_renderer: Callable[[list[dict], dict], None] | None = None` parameter to `aggregate_emit`. The fatal branch keeps emitting JSON via `emit_error`; the success branch invokes the renderer when supplied, else emits JSON.
  2. Add a thin public helper `is_fatal_aggregate(rows) -> ErrorCategory | None` to `_common.py` that wraps the existing private `_decide_exit_and_warnings(rows)[0]`. Wrappers that need a non-JSON success path call it before deciding which emitter to invoke.
  3. Import the existing private `_decide_exit_and_warnings` directly from `_common`.
  4. Rename `_decide_exit_and_warnings` to public `decide_exit_and_warnings`, exposing the (fatal, warnings) tuple to callers.
- **Selected Approach**: Option 2. Add to `scripts/_common.py`:
  ```python
  def is_fatal_aggregate(rows: list[dict[str, Any]]) -> ErrorCategory | None:
      """Return the fatal category iff every row failed with the same
      `CREDENTIAL` / `PLAN_INSUFFICIENT` category, else None.

      Mirrors the gate `aggregate_emit` uses internally so wrappers with
      a non-JSON success path (e.g. `--format md`) can peek before
      rendering and delegate to `aggregate_emit` on the fatal branch.
      """
      fatal, _ = _decide_exit_and_warnings(rows)
      return fatal
  ```
  The `scripts/insider.py::main()` flow becomes:
  ```python
  results = [fetch(sym, …) for sym in args.symbols]
  if args.format == "md":
      if is_fatal_aggregate(results) is not None:
          return aggregate_emit(results, tool="insider", query_meta=meta)
      sys.stdout.write(_render_markdown(results, meta) + "\n")
      return 0
  return aggregate_emit(results, tool="insider", query_meta=meta)
  ```
- **Rationale**:
  1. **Matches `_common.py`'s established public/private boundary.** The module already exposes free functions for everything wrappers compose with (`safe_call`, `wrap`, `emit`, `aggregate_emit`, `single_emit`) and keeps internal-only helpers underscore-prefixed (`_decide_exit_and_warnings`, `_all_rows_in_category`, `_fatal_error_message`, …). A new public free function next to `aggregate_emit` is the natural extension; it does not introduce a new calling convention.
  2. **Minimal complexity.** The implementation is 2 lines and adds zero parameters to existing public APIs. Option 1's `success_renderer` callback expands the signature of a widely-used public function for one wrapper's need; the chokepoint argument fails the cost-benefit test today since insider is the only `--format md` user (gap-analysis Option C, deferred).
  3. **Future-extensible without bespoke per-format APIs.** Any later wrapper that takes a non-JSON success path uses the same `peek → branch → delegate` idiom: `if is_fatal_aggregate(rows) is not None: return aggregate_emit(...); else: render-in-your-format; return 0`. No new helper is needed per format.
  4. **Zero blast radius.** Existing wrappers and tests are unchanged; no existing call site of `aggregate_emit` sees a new parameter; `_common.py`'s private internals stay private. Option 3 leaks an underscore-private name into wrapper code — sets the wrong precedent. Option 4 widens the public API more than needed (the warnings tuple-tail is irrelevant to peeking callers).
  5. **No correctness cost.** `_decide_exit_and_warnings` is pure and stateless; calling it twice on a fatal-mark followed by `aggregate_emit` delegation costs microseconds and avoids any state divergence between the peek and the actual emit.
- **Trade-offs**: One extra public name in `_common.py` (acceptable — it sits next to `aggregate_emit` semantically). Slight redundancy when `aggregate_emit` is delegated to (the fatal check runs twice). No drawback worth designing around.
- **Follow-up**:
  - The `is_fatal_aggregate` addition is part of the insider implementation PR (one new function, ~6 lines including docstring). Do not split into a separate `_common.py`-only PR — the addition has no consumer outside insider yet, so a standalone PR would land dead code.
  - Unit-test under `tests/unit/test_insider_normalize.py` (or alongside the existing `_common` tests if any) covers the four cases: empty rows → None, all-success → None, mixed (success + credential failure) → None, all-credential → `ErrorCategory.CREDENTIAL`.
  - Integration test (Requirement 9.5) covers markdown happy path; an additional case under `tests/integration/test_insider.py` covers all-rows-fatal under `--format md` and asserts the output is valid JSON with exit 2 (i.e., the fall-back fired).

### Decision 6: Markdown column ordering (pin the test invariant)

- **Context**: Requirement 5.6 lists the closed column set `{filing_date, transaction_date, reporter_name, reporter_title, transaction_code, transaction_code_label, shares, price, total_value, shares_after, url}` but does not pin the order. Requirement 9.5's invariant assertion needs an unambiguous order.
- **Alternatives Considered**: (a) Alphabetical, (b) Schema-declaration order from Requirement 6.2, (c) Reading-order chosen for analyst proposal drafts.
- **Selected Approach**: Reading-order. Final column order:
  `filing_date | transaction_date | reporter_name | reporter_title | transaction_code | transaction_code_label | shares | price | total_value | shares_after | url`
- **Rationale**: Time first (filing then transaction date), then who (name + title), then what (code + label), then magnitudes (shares, price, total, post-trade balance), then the audit link. This is the order an analyst reads the row left-to-right when scanning a draft.
- **Trade-offs**: Differs from Requirement 6.2's schema-declaration order (which interleaves audit-only fields like `transaction_type_raw`, `acquisition_or_disposition`, `form_type`, `ownership_type`, CIKs); those audit-only fields stay in the JSON envelope and are not emitted in the markdown table.
- **Follow-up**: Integration test for `--format md` asserts the header line begins with `filing_date | transaction_date | …` exactly.

### Decision 7: Markdown empty-section disambiguation when the code filter is active

- **Context**: Requirement 5.5 says render `_no records in window_` when the per-ticker section has zero rows. But a heavy filter (10 dropped, 0 kept) reads identically to "no upstream activity at all". Gap-analysis §7.5 flagged this.
- **Alternatives Considered**:
  1. Always render `_no records in window_`.
  2. When the `--transaction-codes` filter is active and rejected non-zero rows, render `_no records in window_ (dropped <N> unparseable codes)` plus the rejected count.
  3. Add a "rejected by filter" count separate from "unparseable" — rejected by filter means the code didn't match the user's CSV; unparseable means the code normalized to null.
- **Selected Approach**: Option 2. Render `_no records in window_` by default; when `args.transaction_codes` is non-null and `dropped_unparseable_codes > 0`, append ` (dropped <N> unparseable codes)`.
- **Rationale**: The "rejected by filter" count is implicit in the difference between `len(records_after_day_window) - len(records_after_code_filter) - dropped_unparseable_codes`; surfacing it would require a second per-row counter. The unparseable count is the one that signals data-quality issues to the analyst (the wrapper could not figure out the code), and it is the only one that maps onto an actionable next step (re-run with `--provider sec` if on intrinio, or report the unmapped string).
- **Trade-offs**: Minor — the analyst still cannot tell from the markdown whether a non-empty-window-but-zero-rows result was "filter rejected everything" vs "no activity"; only the JSON envelope carries the full counts.
- **Follow-up**: Integration test asserts the parenthetical appears only when `--transaction-codes` is active and `dropped_unparseable_codes > 0`.

### Decision 8: SKILL.md rewrite stays English-only and minimal-sufficient

- **Context**: Requirement 8.3 caps SKILL.md at ~30–80 lines, English only, one short output sample. Project memory `feedback_skill_minimal_sufficient.md` and `feedback_skill_authoring_no_tests.md` reinforce this; the current SKILL.md is 55 lines.
- **Alternatives Considered**: (a) Add the full transaction-code label table; (b) Add code table + collapse other sections; (c) Reuse the existing structure, replace the per-provider record-key paragraph with a single normalized-schema paragraph and the 10-row code table.
- **Selected Approach**: Option (c). The SKILL.md sections are: header (3–4 lines), `Quick start` (4 lines, one example), `Flags` (one bullet per flag — `--days`, `--transaction-codes`, `--limit`, `--provider`, `--format`), `Record schema` (one paragraph naming the 17 normalized fields, no per-field-bytes paragraph), `Transaction codes` (the 10-row table from Decision 3), `Output sample` (one short markdown table from a live run), `Errors` (1 line linking to `_errors`).
- **Rationale**: Hits the 30–80 line target; keeps the page scannable; the 10-row table is the highest-density piece of new information and earns its space.
- **Trade-offs**: The full long-English SEC corpus from Topic 1 does **not** go in SKILL.md (audit detail belongs here in research.md, not in the agent-facing manual).
- **Follow-up**: Author SKILL.md from a live `uv run scripts/insider.py AAPL --days 90 --transaction-codes P,S --format md` run after implementation freezes (Requirement 8.6).

## Risks & Mitigations

- **Risk: SEC `transaction_type` corpus drift** — A future filing introduces a new long-English string the dict does not cover. *Mitigation*: emit `transaction_code: null` per Requirement 6.3, log the unmapped raw string to stderr exactly once per session, preserve the audit trail under `transaction_type_raw`. The dict is one-line to extend in a follow-up PR.
- **Risk: Hard cut on record-schema field names breaks an out-of-tree caller** — An analyst-side prompt template that quotes `securities_transacted` directly will fail. *Mitigation*: SKILL.md rewrite is the documentation; commit message of the implementation PR calls out the schema change; `transaction_type_raw` and audit-only fields preserve the replay path.
- **Risk: `--format md` is the first non-JSON stdout in the repo** — A future cross-wrapper invariant might assume JSON unconditionally. *Mitigation*: the existing parametrized invariants (`test_json_contract.py`, `test_stdout_hygiene.py`) are keyed off `WRAPPER_HAPPY_ARGV` which does not pass `--format md`; new tests cover markdown invariants in isolation; the fatal-gate fall-back keeps exit 2 on JSON.
- **Risk: Compound `owner_title` parsing edge cases on FMP** — A novel role-marker order or an empty string after stripping. *Mitigation*: Decision 4's helper falls through to "return as-is" on the truly novel shape; unit tests in `tests/unit/test_insider_normalize.py` cover the four documented variants.
- **Risk: SEC mass-download warning** — The 365-day live pass surfaced "Warning: This function is not intended for mass data collection" on stderr from OpenBB. *Mitigation*: the production invocation uses `--days 90` (not 365) per the SKILL.md sample; the warning was a side-effect of the research pass and does not affect normal operation. The wrapper's `safe_call` already absorbs upstream stdout warnings; OpenBB writes this particular line to stdout (visible in the research-pass stderr only because of how the CLI's progress reporting works).
- **Risk: `dropped_unparseable_codes` accidentally appears on `ok: false` rows** — Requirement 6.5 explicitly forbids it. *Mitigation*: row construction must branch on `ok` and only set `dropped_unparseable_codes` on success rows; integration assertion in the new `tests/integration/test_insider.py` covers the failure-row case.
- **Risk: FMP free-tier path never tested locally because the operator is on a paid plan** — The `plan_insufficient` classification (Requirement 2.4) cannot be verified end-to-end on this machine. *Mitigation*: the `_PLAN_MESSAGE_RE` regex in `_common.py` is unit-tested elsewhere in the repo; the integration suite skip-gates FMP cases on `FMP_API_KEY` so absent-key behaviour is exercised.

## References

- Live research output:
  - `/tmp/insider_sec.json` — 10 symbols × 365 days × `--provider sec`, 1,458 records, source for Topic 1's keyword-to-code corpus (regenerated on 2026-04-30).
  - `/tmp/insider_fmp.json` — AAPL × 90 days × `--provider fmp`, 50 records, source for Topic 2's FMP-shape confirmation (regenerated on 2026-04-30).
- Repository assets:
  - `scripts/insider.py` — current 110-line baseline.
  - `scripts/_common.py` — `safe_call`, `aggregate_emit`, `_decide_exit_and_warnings`, `ErrorCategory`, `sanitize_for_json`.
  - `tests/integration/test_json_contract.py` — line 358 (`WRAPPER_HAPPY_ARGV["insider"]`), line 411 (`WRAPPER_INVALID_ARGV["insider"]`).
  - `skills/insider/SKILL.md` — current 55-line manual to be rewritten.
  - `skills/AUTHORING.md`, `skills/SMOKE_CHECKLIST.md` — authoring rules ("English only, ~30–80 lines, one short output sample, no test harness").
- Steering:
  - `docs/steering/structure.md` — flat-directory wrapper convention; `--format md` constraints.
  - `docs/steering/tech.md` — JSON envelope contract, exit-code contract, integration-only OpenBB-call testing.
  - `docs/steering/product.md` — narrow scope (equities / fundamentals / structured macro surveys).
- External:
  - SEC Form 4 General Instructions, "Table I and II — Transaction Codes" (canonical Form 4 transaction-code definitions used for `P`/`S`/`A`/`F`/`M`/`C`/`G`/`D`/`J`/`W` mapping in Decision 2).
  - OpenBB Platform `obb.equity.ownership.insider_trading` reference (provider parameter set, return-shape divergence between SEC and FMP that motivates Requirement 6).
- Spec artefacts:
  - `requirements.md` — 10 requirements covering CLI, provider gate, day window, code filter, output formats, normalized schema, error contract, SKILL doc, integration tests, scope boundaries.
  - `gap-analysis.md` — brownfield gap analysis recommending Option A (extend in place); flagged the SEC corpus and FMP refresh as the two outstanding research items, both resolved by Topics 1 and 2 above.
