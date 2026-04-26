---
name: calendars
description: >-
  Fetch the earnings, ex-dividend, or economic-indicator calendar for a
  date range. Use when an agent needs upcoming or recent corporate or
  macro events to schedule analysis.
covers_scripts:
  - scripts/calendars.py
---

## When to Use

- Listing companies reporting earnings between two dates (`earnings`).
- Listing ex-dividend / record / payment dates (`dividend`).
- Listing macro indicator releases (CPI, payrolls, FOMC, etc.) (`economic`).

Not for: aggregated analyst expectations (use `../estimates/SKILL.md`), individual quotes (use `../quote/SKILL.md`).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `earnings` | nasdaq | none | One row per company report date. |
| `dividend` | nasdaq | none | One row per ex-dividend event. |
| `economic` | nasdaq | none | One row per macro release. Provider coverage varies; empty `results` is a valid response, not an error. |

Other args: `--start <YYYY-MM-DD>` (required), `--end <YYYY-MM-DD>` (optional, defaults to provider behaviour), `--provider {fmp|fred|nasdaq|seeking_alpha|tmx|tradingeconomics}`.

See `../_providers/SKILL.md` for keyless defaults.

## Command

```bash
uv run scripts/calendars.py --type earnings --start 2026-04-26 --end 2026-04-30
uv run scripts/calendars.py --type economic --start 2026-04-26 --end 2026-04-30
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "calendars"`. Single emit. `data` adds siblings `type`, `provider`, `start`, `end`.

`data` namespace:

- `data.results[]` — events sorted by event date.
- `earnings` records: `{report_date, symbol, name, eps_previous, eps_consensus, num_estimates, period_ending, previous_report_date, reporting_time, market_cap}`.
- `dividend` records: `{ex_dividend_date, symbol, amount, name, record_date, payment_date, declaration_date, annualized_amount}`.
- `economic` records: `{date, country, event, actual?, consensus?, previous?, importance?, …}` (provider-dependent; treat all numeric fields as optional).

```json
{
  "tool": "calendars",
  "data": {
    "type": "earnings", "provider": "nasdaq",
    "start": "2026-04-26", "end": "2026-04-30",
    "results": [
      {"report_date": "2026-04-30", "symbol": "AAPL", "name": "Apple Inc.",
       "eps_previous": 1.65, "eps_consensus": 1.92, "num_estimates": 11.0,
       "reporting_time": "after-hours", "market_cap": 4014264110200}
    ]
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Missing `--start` or malformed dates → `error_category: validation`; correct and retry.
- Empty `data.results` for a date window with no events (especially `economic` over a short window) → not an error; the wrapper exits 0 with `results: []`.
- Provider transient outage → `error_category: transient`; one retry then warn.

## References

- `scripts/calendars.py`
- `README.md` § 1 row 11 (Earnings / dividend / economic calendars).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
