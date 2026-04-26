# Skills Authoring Conventions

Governance document for `skills/`. Read this before adding, editing, or renaming a skill.

This document is **not** a skill — it is meta-documentation for skill authors and reviewers. Active skills live at `skills/<skill-name>/SKILL.md`.

## 1. Directory Convention

### 1.1 Layout — flat, host-agnostic

```
skills/
├── INDEX.md                 # catalog (one line per skill, see task 7.1)
├── AUTHORING.md             # this file
├── SMOKE_CHECKLIST.md       # manual smoke-test checklist (see task 9.1)
├── _envelope/SKILL.md       # common policy: shared JSON envelope
├── _providers/SKILL.md      # common policy: provider/credential map
├── _errors/SKILL.md         # common policy: ErrorCategory contract
├── <wrapper-name>/SKILL.md  # one per scripts/<wrapper-name>.py
├── <wrapper-name>/examples/<file>.json   # optional auxiliary samples
└── <composite-name>/SKILL.md             # composite (pipeline) skills
```

- One skill = one directory. Directory name is `kebab-case` (or `snake_case` matching the wrapper filename for per-wrapper skills) and **must equal** the `name:` field in the skill frontmatter.
- The skill body file is always exactly `SKILL.md`. Do not nest sub-skills inside a skill directory.
- No subdirectory hierarchy beyond `<skill-name>/`. The top of `skills/` is flat. Mirror the `scripts/` philosophy from `docs/steering/structure.md` — the thinner the wrapper, the more valuable it is, and the same applies to its skill.

### 1.2 Host-agnostic — no host-specific paths

- **Do not** place skills under `.claude/skills/`, `.cursor/`, `.gemini/`, or any other host-specific directory. Anthropic Agent Skills, Cursor, Gemini, and bespoke agents all read plain Markdown — keep one canonical location.
- The repository's existing `.claude/skills/` directory holds **SDD workflow skills** (sdd-spec-init, sdd-spec-impl, …) and is unrelated. Do not add OpenBB usage skills there.

### 1.3 Auxiliary files stay inside the skill directory

- Sample envelopes, fixture JSON, decision diagrams, etc. live alongside `SKILL.md` inside the same `<skill-name>/` directory (typically under `<skill-name>/examples/`).
- Do not scatter auxiliary content across the repo (no `skill-examples/`, no `docs/skills/`).
- Auxiliary files are referenced from `SKILL.md` by relative path only.

### 1.4 Scope discipline

A skill body **must not** reference or depend on resources outside this project.

Allowed references from inside a skill:

- `scripts/<name>.py`, `scripts/_common.py`, `scripts/_env.py`, `scripts/_schema.py`
- `README.md` (this repo) and `docs/steering/*` (this repo)
- Other skills under `skills/` via relative links (e.g. `../_envelope/SKILL.md`)

## 2. Frontmatter Schema

Every `SKILL.md` starts with YAML 1.2 frontmatter fenced by `---`.

### 2.1 Required keys

| Key           | Type   | Constraint                                                                                                 |
| ------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| `name`        | string | `kebab-case` or `snake_case`; **must equal the directory name**.                                           |
| `description` | string | One sentence following `<verb-led capability>. Use when <trigger condition>.` Drives host skill-selection. |

### 2.2 Optional keys (recommended for traceability)

| Key              | Type            | Purpose                                                                                                             |
| ---------------- | --------------- | ------------------------------------------------------------------------------------------------------------------- |
| `covers_scripts` | list of strings | `scripts/<name>.py` paths the skill documents. Each path **must exist on disk** (Req 8.1).                          |
| `requires_keys`  | list of strings | Env-var names the skill mentions. Allowed values: keys of `scripts/_env.py::_CREDENTIAL_MAP` plus `SEC_USER_AGENT`. |

### 2.3 Frontmatter example

```yaml
---
name: fundamentals
description: >-
  Fetch company fundamentals (overview, metrics, financials, ratios). Use when
  an agent needs balance sheet, income statement, cash flow, or ratio data
  for a single ticker.
covers_scripts:
  - scripts/fundamentals.py
requires_keys:
  - FMP_API_KEY
---
```

Rules:

- `description` must be a single sentence (line-folded YAML is fine), verb-led, with a `Use when …` trigger clause.
- Do not embed credentials, file paths outside this repo, or judgment in frontmatter.

## 3. Body Template — six sections, fixed order

Every `SKILL.md` body has exactly these six top-level sections, in this order:

1. `## When to Use`
2. `## Inputs`
3. `## Command`
4. `## Output`
5. `## Failure Handling`
6. `## References`

### 3.1 When to Use

- Trigger conditions ("call this when …").
- Anti-patterns ("not for: …").
- Keep to bullets; no narrative paragraphs.

### 3.2 Inputs

- Positional arguments, options, sub-modes (`--type`, `--scope`, `--indicator`, `--series`, …) with **default provider** and **required env vars** per sub-mode.
- For wrappers with sub-modes, use a single table: `sub-mode | default provider | requires | notes`.
- Mark paid-only sub-modes explicitly (e.g. `etf --type holdings|sectors` requires paid Starter+ FMP).

### 3.3 Command

- At least one runnable `uv run scripts/<name>.py …` example that works on the free tier.
- Add a second example only when a paid sub-mode has materially different invocation.
- Do **not** chain shell pipes that introduce new behavior — the skill documents one wrapper, not a pipeline (composite skills are the exception).

### 3.4 Output (envelope contract is mandatory)

- Reference the shared envelope by relative link to `_envelope`: `[shared envelope](../_envelope/SKILL.md)`. Do not re-describe the four root keys (`source`, `collected_at`, `tool`, `data`) or the four optional keys (`warnings`, `error`, `error_category`, `details`) in each per-wrapper skill.
- Document the **skill-specific `data.*` namespace**: which keys under `data` the agent should traverse (e.g. `data.results[]`, `data.universe`, `data.partial_filing_window`).
- Output examples: at most **one short literal block** per skill. Longer envelopes go to `<skill-name>/examples/<name>.json` and are referenced by relative link. Do not attach a complete output sample for every sub-mode — list `data.*` key paths instead.

### 3.5 Failure Handling (`error_category` contract is mandatory)

- Reference the `ErrorCategory` contract by relative link: `[error categories](../_errors/SKILL.md)`. Do not re-list the five values (`credential` / `plan_insufficient` / `transient` / `validation` / `other`) or their default agent responses inline.
- Document **only the wrapper-specific failure paths**: which `error_category` values this wrapper can emit and the deterministic agent response (e.g. "`etf --type holdings` returns `plan_insufficient` when FMP key is on free tier → skip sub-mode and record `skipped: paid tier required` in summary").

### 3.6 References

- `scripts/<name>.py` (must exist on disk).
- `README.md` §1-1 row identifier.
- Relative links to `_envelope`, `_providers`, `_errors` when used.
- No external URLs unless they are first-party documentation that the wrapper itself depends on.

## 4. Style Guide

### 4.1 Language — English, fixed

- **SKILL bodies are written in English.** Binding decision (no bilingual side-by-side, no Japanese comments mixed in).
- SDD meta-documents (`docs/tasks/**/{requirements,design,tasks,research,validation*}.md`, `SMOKE_CHECKLIST.md`) remain in Japanese per `spec.json.language`. This authoring document is in English because it directly governs English skill bodies.

### 4.2 Voice and tone

- Imperative, declarative, present tense ("Fetch …", "Returns …", "Use `--provider yfinance`").
- No opinion adverbs ("strongly", "definitely", "obviously").
- No investment judgment. Skills describe **how** to call OpenBB and **how** to read the result, never **whether** to buy. Forbidden vocabulary: "undervalued", "overvalued", "buy", "sell", "recommend", "bullish", "bearish".

### 4.3 Thin manual — no logic restatement

- Do not restate aggregation formulas, scoring logic, or wrapper internals. Point to `scripts/<name>.py` in `## References` instead.
- Do not transcribe `--help`. List only the inputs an agent needs to choose between.
- Do not duplicate the shared envelope schema, the `_CREDENTIAL_MAP`, or the `ErrorCategory` enum. Link to `_envelope` / `_providers` / `_errors`.

### 4.4 Minimal-Sufficient Documentation (binding)

A skill must give an AI agent the **minimum information needed** to call the wrapper correctly, read its output, and respond to failure. Comprehensive reference documentation is an explicit non-goal.

Test for every sentence: _"If I delete this line, will an agent call the wrapper incorrectly or misread the output?"_ If no, delete the line.

Forbidden in the name of completeness:

- Full transcription of `--help`.
- Wrapper-internal implementation explanations.
- Restatement of common contracts already covered by `_envelope` / `_providers` / `_errors`.
- Complete output samples for every sub-mode.

### 4.5 Length budget — 30 to 80 lines per skill

Target length (frontmatter + six sections combined): **30 lines minimum, 80 lines maximum**.

When a skill grows past 80 lines, apply one of these compression strategies:

1. Move long output samples to `<skill-name>/examples/<name>.json` and replace with a key-path enumeration in `## Output`.
2. Split into multiple skills (e.g., one skill per sub-mode) when sub-modes share little overlap.
3. Push shared content into `_envelope` / `_providers` / `_errors` and reference by relative link.

### 4.6 Output examples — short form, one example max

- At most one literal block per `## Output`, in shortened form (truncate arrays to one or two elements, keep representative keys).
- For wrappers with multiple sub-modes, the default is a `data.*` key-path list, **not** one literal example per sub-mode.
- If a literal example is essential for a paid sub-mode, place it in `<skill-name>/examples/<sub-mode>.json` and link from the body.

## 5. Authoring Workflow (summary)

1. Set up `.env` with the keys the wrapper needs (or confirm key-free path).
2. Run the wrapper with `uv run scripts/<name>.py …` — observe real envelope output. Do not write `## Output` from memory or from `--help`.
3. Draft the six sections in order, applying the length budget and minimal-sufficient principle.
4. Add relative links to `_envelope` / `_providers` / `_errors` instead of duplicating contract content.
5. Add an entry to `INDEX.md` (when that file exists, per task 7.1) in the same change.
6. Self-review against this document before opening a PR.

For end-to-end smoke verification, use `SMOKE_CHECKLIST.md` (created in task 9.1).
