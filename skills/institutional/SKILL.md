---
name: institutional
description: >-
  Fetch aggregate 13F institutional-ownership statistics per ticker for a
  given quarter. Use when an agent needs concentration / drift signals
  from the most-recent (or a historical) 13F vintage.
covers_scripts:
  - scripts/institutional.py
requires_keys:
  - FMP_API_KEY
---

## When to Use

- Reading the current 13F aggregate (investors holding, ownership %, put/call ratio, total invested) for one or more tickers.
- Pulling a stable historical snapshot via `--year` / `--quarter`.

Not for: per-fund holdings rosters (not exposed by this wrapper), insider Form 4 (use `../insider/SKILL.md`).

## Inputs

| Sub-mode | Default provider | Required keys | Notes |
|---|---|---|---|
| (single mode) | fmp (only choice) | `FMP_API_KEY` (free tier OK) | One row per ticker per quarter. |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--year <YYYY>`, `--quarter <1-4>`. Omit both for the current calendar quarter. Records inside the in-flight 45-day filing window carry `partial_filing_window: true`; pass a quarter older than 1 year for a stable snapshot.

Partial-filing-window flags:

- `--format {json,md}` (default `json`). `md` emits per-ticker `## <SYMBOL>` sections with a fixed-column table (`date | investors_holding | ownership_percent | number_of_13f_shares | total_invested | put_call_ratio | notes`); partial rows are prefixed `⚠ ` and get `filing window: deadline YYYY-MM-DD` in the trailing `notes` cell.
- `--hide-partial` (default **off**). When set, replaces current-period numeric fields on `partial_filing_window=true` records with `null` while preserving `last_*` prior-period values and the `partial_filing_window` flag itself. Default is off so the three-layer warning (stderr ⚠ + md `⚠` + per-ticker `partial_filing_window_records[]`) can do its job without breaking the legacy numeric contract; enable explicitly in batch / CI where a consumer might read raw numbers without seeing the warnings.
- `--no-stderr-warn` (default off). Suppresses the `⚠ institutional:` stderr block described under Failure Handling. Reserved for batch / CI; analysts and reviewers should leave it off so the warning stays visible.

See `../_providers/SKILL.md` for credential mapping.

## Command

```bash
uv run scripts/institutional.py AAPL --year 2024 --quarter 4
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "institutional"`. `data` adds siblings `provider`, `hide_partial` (echoes the flag), `year`, `quarter`. `data.results[]` rows: `{symbol, provider, ok, records|error, partial_filing_window_records?, error_type?, error_category?}`. `records` length is 1 per ticker per quarter.

`records[i]` keys: `symbol, cik, date, partial_filing_window, investors_holding, last_investors_holding, investors_holding_change, new_positions, last_new_positions, new_positions_change, increased_positions, last_increased_positions, increased_positions_change, closed_positions, last_closed_positions, closed_positions_change, reduced_positions, last_reduced_positions, reduced_positions_change, total_calls, last_total_calls, total_calls_change, total_puts, last_total_puts, total_puts_change, put_call_ratio, last_put_call_ratio, put_call_ratio_change, number_of_13f_shares, last_number_of_13f_shares, number_of_13f_shares_change, ownership_percent, last_ownership_percent, ownership_percent_change, total_invested, last_total_invested, total_invested_change`.

`partial_filing_window_records[]` (new, additive — `ok: True` rows only, always present even when empty): per-ticker summary of records currently inside the SEC 45-day filing window. Each entry is `{date: "YYYY-MM-DD", filing_deadline: "YYYY-MM-DD"}` where `filing_deadline = date + 45 days`. Existing `records[i]` keys are unchanged; consumers that ignore the new field continue to work.

**Envelope top-level `warnings[]` — agent-facing partial marker**: when any record carries `partial_filing_window: true`, the envelope root `warnings[]` also gains one entry per partial record, shaped `{symbol, warning_type: "partial_filing_window", date, filing_deadline}`. This is the **primary signal a JSON consumer must check before reading `data.results[i].records[j]` numerics** — a partial record's `ownership_percent` / `investors_holding` / `*_change` fields reflect an in-flight 45-day filing window and are not comparable to prior-period aggregates. The entry omits `error` / `error_category` to stay distinct from `aggregate_emit`'s row-failure warnings (see `../_envelope/SKILL.md`). This JSON signal is independent of the stderr `⚠` block; `--no-stderr-warn` does **not** suppress it.

Markdown output (`--format md`): each ticker renders as a `## <SYMBOL>` section followed by a fixed-column table `date | investors_holding | ownership_percent | number_of_13f_shares | total_invested | put_call_ratio | notes`. Numeric cells keep JSON raw values (no percent / k-M-B conversion) so md and JSON match byte-for-byte on shared fields. Partial-window rows are prefixed `⚠ ` and their `notes` cell reads `filing window: deadline YYYY-MM-DD`; non-partial rows have no prefix and an empty `notes` cell.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `FMP_API_KEY` unset → exit 2 with `error_category: credential`. Set the key or skip; do not retry.
- `--quarter` outside `1..4` → `error_category: validation`; correct and retry.
- `partial_filing_window: true` in a record → not an error; treat the row as preliminary. **Agents parsing JSON must inspect envelope top-level `warnings[]` for entries with `warning_type == "partial_filing_window"` before reading `data.results[].records[]` numerics**; a partial row's current-period values are not comparable to `last_*` prior-period aggregates.
- `partial_filing_window: true` also triggers a three-line `⚠ institutional:` block on **stderr** (one block per partial record):

  ```
  ⚠ institutional: <TICKER> <YYYY-MM-DD> is in 13F filing window
    (deadline ≈ <YYYY-MM-DD>); ownership_percent / investors_holding may be
    materially understated. Treat as preliminary; refresh after deadline.
  ```

  stdout stays clean (JSON or markdown only). Pass `--no-stderr-warn` to silence the block (batch / CI only); analysts and reviewers should leave it on.
- For CI / automated batch runs that cannot surface stderr to a human, either pass `--hide-partial` (so raw current-period numerics cannot be misread as stable) or explicitly tee / monitor stderr for the `⚠ institutional:` marker.

## References

- `scripts/institutional.py`
- `README.md` § 1 row 13 (Institutional holdings (13F)).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
