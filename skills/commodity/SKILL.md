---
name: commodity
description: >-
  Fetch commodity spot prices (FRED) or EIA structured energy reports
  (Weekly Petroleum Status / Short-Term Energy Outlook). Use when an agent
  needs energy / commodity macro inputs for inflation- or energy-quadrant
  reads.
covers_scripts:
  - scripts/commodity.py
requires_keys:
  - FRED_API_KEY
  - EIA_API_KEY
---

## When to Use

- Spot prices for WTI, Brent, Henry Hub, distillates, gasoline (`--type price`).
- Weekly Petroleum Status Report: stocks, supply, demand, refinery activity (`--type weekly_report`).
- 18-month Short-Term Energy Outlook projections (`--type steo`).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `price` (default) | fred | `FRED_API_KEY` | `--symbol {wti,brent,natural_gas,jet_fuel,propane,heating_oil,diesel_*,gasoline_*,rbob,all}` (default `wti`). |
| `weekly_report` | eia | none | Public Excel file; `EIA_API_KEY` ignored even if set. `--symbol` ignored with non-fatal validation warning. |
| `steo` | eia | `EIA_API_KEY` | Hits the EIA v2 API. `--symbol` ignored with non-fatal validation warning. |

Other args: `--start YYYY-MM-DD`, `--end YYYY-MM-DD`.

See `../_providers/SKILL.md` for credential mapping.

## Command

```bash
uv run scripts/commodity.py --type weekly_report --start 2026-01-01
uv run scripts/commodity.py --symbol wti --type price --start 2024-01-01
uv run scripts/commodity.py --type steo
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "commodity"`. `data` adds siblings `provider`, `type`, optional `symbol`, optional `start`/`end`. Single emit; `data.results[]` is a flat time series.

Per-`--type` `data.results[i]` keys:

- `price` — `date, symbol, commodity, price, unit`.
- `weekly_report` — `date, symbol, table, title, value, unit, order` (long-format, one row per metric per date; group by `table`/`title` for a metric series).
- `steo` — same long-format shape as `weekly_report` (`date, symbol, table, title, value, unit, order`).

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `--type price` without `FRED_API_KEY` → exit 2 with `error_category: credential`. Set the key or skip; do not retry.
- `--type steo` without `EIA_API_KEY` → exit 2 with `error_category: credential`.
- `--symbol` combined with `--type weekly_report` or `--type steo` → non-fatal `validation` warning; symbol ignored.
- Upstream EIA / FRED transient outage → `error_category: transient`; one retry then warn.

## References

- `scripts/commodity.py`
- `README.md` § 1 row 18 (Commodity prices / weekly energy / STEO).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
