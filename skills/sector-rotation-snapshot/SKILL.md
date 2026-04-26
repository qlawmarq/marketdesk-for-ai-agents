---
name: sector-rotation-snapshot
description: >-
  Score a sector ETF universe and run momentum on the top/bottom ranked
  sectors, producing a single rotation summary. Use when an agent needs a
  quick read on which US sectors are leading or lagging before drilling
  into single-name ideas.
covers_scripts:
  - scripts/sector_score.py
  - scripts/momentum.py
---

## When to Use

- Periodic sector-rotation read on the eleven SPDR sectors (or any universe `sector_score` accepts).
- Generating a shortlist of sector ETFs for a follow-up `single-stock-snapshot` on individual constituents.

Not for: single-name analysis (use `../single-stock-snapshot/SKILL.md`), macro / cross-asset views (`../macro-snapshot/SKILL.md`), backtesting strategies.

## Inputs

| Argument | Required | Notes |
|---|---|---|
| `--universe <name>` | yes | Default `sector-spdr`. Forwarded to step 1; see `../sector_score/SKILL.md` for valid values. |
| `<top_n>` / `<bottom_n>` | optional | Caller chooses how many leaders / laggards to drill into (default 3 each). |

No env vars required (sector_score and momentum are keyless). Stateless — no caches, no files.

## Command

Pipeline — run each step, capture stdout JSON, propagate per-step exit codes:

1. `uv run scripts/sector_score.py --universe sector-spdr` — see `../sector_score/SKILL.md`.
2. From step 1's `data.results[]` (already sorted ascending by `rank`), take the first `top_n` and last `bottom_n` `ticker` values.
3. `uv run scripts/momentum.py <top_tickers...> --indicator clenow` — see `../momentum/SKILL.md`.
4. `uv run scripts/momentum.py <bottom_tickers...> --indicator clenow` — same skill.

`momentum` accepts multiple symbols in one invocation, so steps 3 and 4 are single calls each.

## Output

Each step emits the [shared envelope](../_envelope/SKILL.md). Merge into one summary keyed by:

`{ collected_at, sector_scores, top_sector_momentum[], bottom_sector_momentum[] }`

- `sector_scores` — step 1's full `data` (universe, weights, results, missing_tickers).
- `top_sector_momentum[]` — step 3's `data.results[]` rows (one per top ticker).
- `bottom_sector_momentum[]` — step 4's `data.results[]` rows.

Per-step `data.*` namespaces are documented in the linked per-wrapper skills.

## Failure Handling

See [error categories](../_errors/SKILL.md). Composite-specific paths:

- Step 1 exits 2 → abort the pipeline; without rankings the momentum drill-downs have no input.
- Step 3 or 4 returns per-row `ok: false` for a ticker → preserve the row as-is in the corresponding momentum array.
- A `transient` error on any step → retry that step once before recording it.
- Step 1 leaves tickers in `data.missing_tickers` → those tickers are excluded from the rank list and therefore never reach steps 3 / 4; no special handling needed.

## References

- Per-wrapper skills: `../sector_score/SKILL.md`, `../momentum/SKILL.md`.
- Common policies: `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
