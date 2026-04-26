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

See `../_providers/SKILL.md` for credential mapping.

## Command

```bash
uv run scripts/institutional.py AAPL --year 2024 --quarter 4
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "institutional"`. `data` adds siblings `provider`, `year`, `quarter`. `data.results[]` rows: `{symbol, provider, ok, records|error, error_type?, error_category?}`. `records` length is 1 per ticker per quarter.

`records[i]` keys: `symbol, cik, date, partial_filing_window, investors_holding, last_investors_holding, investors_holding_change, new_positions, last_new_positions, new_positions_change, increased_positions, last_increased_positions, increased_positions_change, closed_positions, last_closed_positions, closed_positions_change, reduced_positions, last_reduced_positions, reduced_positions_change, total_calls, last_total_calls, total_calls_change, total_puts, last_total_puts, total_puts_change, put_call_ratio, last_put_call_ratio, put_call_ratio_change, number_of_13f_shares, last_number_of_13f_shares, number_of_13f_shares_change, ownership_percent, last_ownership_percent, ownership_percent_change, total_invested, last_total_invested, total_invested_change`.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `FMP_API_KEY` unset → exit 2 with `error_category: credential`. Set the key or skip; do not retry.
- `--quarter` outside `1..4` → `error_category: validation`; correct and retry.
- `partial_filing_window: true` in a record → not an error; treat the row as preliminary.

## References

- `scripts/institutional.py`
- `README.md` § 1 row 13 (Institutional holdings (13F)).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
