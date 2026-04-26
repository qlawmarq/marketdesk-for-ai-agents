---
name: single-stock-snapshot
description: >-
  Run the standard single-equity research pipeline (quote, profile,
  consensus, news, insider, momentum) and merge each step into one
  agent-readable summary. Use when an agent needs a free-tier baseline
  view of a single ticker before deeper analysis.
covers_scripts:
  - scripts/quote.py
  - scripts/fundamentals.py
  - scripts/estimates.py
  - scripts/news.py
  - scripts/insider.py
  - scripts/momentum.py
requires_keys:
  - FMP_API_KEY
---

## When to Use

- One ticker, baseline snapshot covering price, profile, consensus, recent news, insider activity, and momentum.
- A composite step inside a larger analyst routine, before any rotation / macro overlay.

Not for: multi-ticker screening (use `../sector_score/SKILL.md`), historical backtests (`../historical/SKILL.md`), portfolio analytics.

## Inputs

| Argument | Required | Notes |
|---|---|---|
| `<ticker>` | yes | Single symbol passed to every step (e.g. `AAPL`, `7203.T`). |
| `FMP_API_KEY` | optional | Enables the paid `fundamentals --type ratios` step. Free-tier key is sufficient. |

No state files, no caches. Each step is an independent CLI call.

## Command

Free-tier pipeline — run each step, capture stdout JSON, abort the step (not the pipeline) on non-zero exit:

1. `uv run scripts/quote.py <ticker>` — see `../quote/SKILL.md`.
2. `uv run scripts/fundamentals.py <ticker> --type overview` — see `../fundamentals/SKILL.md`.
3. `uv run scripts/estimates.py <ticker> --type consensus` — see `../estimates/SKILL.md`.
4. `uv run scripts/news.py <ticker> --scope company` — see `../news/SKILL.md`.
5. `uv run scripts/insider.py <ticker>` — see `../insider/SKILL.md`.
6. `uv run scripts/momentum.py <ticker> --indicator clenow` — see `../momentum/SKILL.md`.

Optional paid step (requires `FMP_API_KEY`):

7. `uv run scripts/fundamentals.py <ticker> --type ratios --limit 1` — see `../fundamentals/SKILL.md`.

## Output

Each step emits the [shared envelope](../_envelope/SKILL.md). Merge per-step `data` into one summary object keyed by:

`{ ticker, collected_at, quote, fundamentals_overview, estimates_consensus, news_company, insider, momentum_clenow, ratios? }`.

Free-tier fallback: when step 7 is skipped (no `FMP_API_KEY`, `plan_insufficient`, or `credential`), set `ratios` to `{ skipped: true, reason: "free tier" }` so the consumer schema stays stable. Per-step `data.*` namespaces are documented in the linked per-wrapper skills.

## Failure Handling

See [error categories](../_errors/SKILL.md). Composite-specific paths:

- A step exiting 2 with `credential` or `plan_insufficient` (typically step 7) → record `{ skipped: true, reason: <category> }` in that slot and continue; do not abort the pipeline.
- A step exiting 2 with `validation` → fix the argument and rerun that step only.
- A step exiting 0 with `warnings[]` → keep the data, surface the warning under its slot.
- Per-row `ok: false` is handled inside the per-wrapper skill; the composite preserves the row as-is.

## References

- Per-wrapper skills: `../quote/SKILL.md`, `../fundamentals/SKILL.md`, `../estimates/SKILL.md`, `../news/SKILL.md`, `../insider/SKILL.md`, `../momentum/SKILL.md`.
- Common policies: `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
