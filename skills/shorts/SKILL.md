---
name: shorts
description: >-
  Fetch FINRA short-interest aggregates or SEC fails-to-deliver records
  per ticker. Use when an agent needs contrarian / squeeze signal inputs.
covers_scripts:
  - scripts/shorts.py
requires_keys:
  - SEC_USER_AGENT
---

## When to Use

- Reading FINRA short interest, days-to-cover, and reporting period (`--type short_interest`).
- Reading SEC daily fails-to-deliver rows (`--type fails_to_deliver`).

Not for: short-volume / dark-pool feeds (the `short_volume` sub-mode is intentionally unexposed; upstream broken since 2026-04-25).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `short_interest` (default) | finra | none | Bi-monthly settlement-period rows. |
| `fails_to_deliver` | sec | `SEC_USER_AGENT` env var | Daily settlement-failure rows. |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`).

See `../_providers/SKILL.md` for credential mapping (SEC_USER_AGENT is read directly by OpenBB).

## Command

```bash
uv run scripts/shorts.py AAPL --type short_interest
uv run scripts/shorts.py AAPL --type fails_to_deliver
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "shorts"`. `data` adds siblings `provider`, `type`. `data.results[]` rows: `{symbol, type, provider, ok, records|error, error_type?, error_category?}`.

Per-`--type` `records[i]` keys:

- `short_interest` (finra) — `settlement_date, symbol, issue_name, market_class, current_short_position, previous_short_position, change, change_pct, avg_daily_volume, days_to_cover`.
- `fails_to_deliver` (sec) — `settlement_date, symbol, cusip, description, quantity, price`.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `--type fails_to_deliver` without `SEC_USER_AGENT` → upstream rejects → `error_category: credential`. Set the env var or skip the sub-mode.
- Per-row `ok: false` for an unknown / illiquid symbol → `error_category: other`; drop the row.
- Empty `records: []` for a symbol with no recent FTD activity → not an error; exit 0.

## References

- `scripts/shorts.py`
- `README.md` § 1 row 19 (Short interest / fails-to-deliver).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
