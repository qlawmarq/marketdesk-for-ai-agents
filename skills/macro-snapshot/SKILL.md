---
name: macro-snapshot
description: >-
  Pull a small set of macro-survey series, weekly commodity report and
  price, and Fama-French factor returns into one cross-asset macro
  snapshot. Use when an agent needs a baseline read on macro conditions
  before sizing a position or shifting allocation.
covers_scripts:
  - scripts/macro_survey.py
  - scripts/commodity.py
  - scripts/factors.py
requires_keys:
  - FRED_API_KEY
  - EIA_API_KEY
---

## When to Use

- Periodic macro check before single-name or sector decisions.
- Establishing the macro context column for an analyst memo / portfolio review.

Not for: live tick prices (`../quote/SKILL.md`), single-issuer fundamentals (`../fundamentals/SKILL.md`), pure rotation views (`../sector-rotation-snapshot/SKILL.md`).

## Inputs

| Argument | Required | Notes |
|---|---|---|
| `<series_set>` | optional | Caller picks the macro series list. Default suggestion: `sloos`, `michigan`, `inflation_exp`. Forwarded to step 1 one series per call. |
| `FRED_API_KEY` | optional | Required for `commodity --type price` and any FRED-backed `macro_survey` series; without it those slots degrade. |
| `EIA_API_KEY` | optional | Required for `commodity --type steo` if the caller adds it. Not needed for the default pipeline below. |

Stateless — no caches, no files. Each step is independent.

## Command

Pipeline — run each step, capture stdout JSON, abort the step (not the pipeline) on non-zero exit:

1. For each series in `<series_set>`: `uv run scripts/macro_survey.py --series <series>` — see `../macro_survey/SKILL.md`.
2. `uv run scripts/commodity.py --type weekly_report` — keyless, see `../commodity/SKILL.md`.
3. `uv run scripts/commodity.py --type price` — requires `FRED_API_KEY`; same skill.
4. `uv run scripts/factors.py --region america --frequency monthly` — see `../factors/SKILL.md`.

## Output

Each step emits the [shared envelope](../_envelope/SKILL.md). Merge into one summary keyed by:

`{ collected_at, macro_series, commodity_weekly, commodity_price, factors }`

- `macro_series` — object keyed by series name, each value is that step's `data` block.
- `commodity_weekly` — step 2's `data` (keyless EIA weekly petroleum report).
- `commodity_price` — step 3's `data`.
- `factors` — step 4's `data` (Fama-French region/frequency/factor metadata + `results[]` time series).

Free-tier fallback: when `FRED_API_KEY` (or `EIA_API_KEY` for any caller-added series) is missing or returns `credential` / `plan_insufficient`, set the affected slot to `{ skipped: true, reason: "key required" }` and keep the rest of the summary. Per-step `data.*` namespaces are in the linked per-wrapper skills.

## Failure Handling

See [error categories](../_errors/SKILL.md). Composite-specific paths:

- Any step exits 2 with `credential` (missing `FRED_API_KEY` / `EIA_API_KEY`) → record `{ skipped: true, reason: "key required" }` in that slot and continue.
- A `transient` error on any step → retry that step once before recording it.
- Per-series `ok: false` rows inside `macro_series` are preserved as-is — handled by `../macro_survey/SKILL.md`.
- `validation` (e.g. unknown series name) → fix the argument and rerun that step only.

## References

- Per-wrapper skills: `../macro_survey/SKILL.md`, `../commodity/SKILL.md`, `../factors/SKILL.md`.
- Common policies: `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
