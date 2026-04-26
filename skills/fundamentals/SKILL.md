---
name: fundamentals
description: >-
  Fetch company fundamentals — overview, key metrics, three-statement
  financials, and ratios — for one or more tickers. Use when an agent
  needs profile, balance sheet, income statement, cash flow, or ratio
  data for equity analysis.
covers_scripts:
  - scripts/fundamentals.py
requires_keys:
  - FMP_API_KEY
---

## When to Use

- Profile / sector lookup (`overview`).
- Valuation, leverage, or margin ratios (`ratios`).
- Three-statement raw data (`income`, `balance`, `cash`) or key per-share metrics (`metrics`).

Not for: analyst estimates / price targets (use `../estimates/SKILL.md`), historical prices (use `../historical/SKILL.md`).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `overview` | yfinance | none | Company profile, sector, market cap, employees. |
| `metrics` | yfinance | none | Per-share metrics (EPS, FCF, payout, etc.). |
| `income` | yfinance | none | Income statement. Use `--period quarter\|annual` and `--limit`. |
| `balance` | yfinance | none | Balance sheet. Same period/limit args. |
| `cash` | yfinance | none | Cash flow statement. Same period/limit args. |
| `ratios` | fmp | `FMP_API_KEY` (free tier OK) | Returns `{value, unit}` dicts per ratio (see Output). |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--provider`, `--period {annual|quarter}`, `--limit <int>`.

See `../_providers/SKILL.md` for credential handling.

## Command

```bash
uv run scripts/fundamentals.py AAPL --type overview
uv run scripts/fundamentals.py AAPL --type ratios --limit 5
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "fundamentals"`. `data` adds siblings `provider`, `type`. `data.results[]` rows: `{symbol, type, provider, ok, records|error, error_type?, error_category?}`. `records[]` length = `--limit` (or 1 for `overview`).

Per-`--type` `records[i]` keys:

- `overview` — profile fields: `symbol, name, sector, industry_category, market_cap, shares_outstanding, shares_float, dividend_yield, beta, currency, employees, hq_*`, plus `long_description`.
- `metrics` — per-share / TTM scalars (EPS, FCF/share, payout, working capital, etc.).
- `income` / `balance` / `cash` — line items from the matching statement, plus `period_ending, fiscal_period, fiscal_year, currency`.
- `ratios` — every ratio is wrapped as `{value: <float>, unit: "decimal"|"ratio"|...}`. Read `record["<ratio>"]["value"]`, not `record["<ratio>"]`. Top-level keys: `symbol, period_ending, fiscal_period, fiscal_year, currency`.

```json
{
  "tool": "fundamentals",
  "data": {
    "results": [{"symbol": "AAPL", "type": "overview", "provider": "yfinance", "ok": true,
                 "records": [{"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology", "market_cap": 3979469914112, "currency": "USD"}]}],
    "provider": "yfinance", "type": "overview"
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `--type ratios` without `FMP_API_KEY` → exit 2 with `error_category: credential`. Set the key or skip the sub-mode; do not retry.
- `--type ratios` on a deprecated FMP plan → `error_category: plan_insufficient`. Drop the sub-mode and continue with `overview`/`metrics` from yfinance.
- Per-row `ok: false` for an unknown symbol → `error_category: other`; drop the row.

## References

- `scripts/fundamentals.py`
- `README.md` § 1 rows 3–5 (Profile / Income-BS-CF / Ratios).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
