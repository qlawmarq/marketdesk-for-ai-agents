---
name: etf
description: >-
  Fetch ETF metadata, holdings list, or sector breakdown for one or more
  ETF tickers. Use when an agent needs fund profile, constituents, or
  sector exposure for an ETF.
covers_scripts:
  - scripts/etf.py
requires_keys:
  - FMP_API_KEY
---

## When to Use

- ETF profile (expense ratio, AUM, inception, NAV, returns) via `info`.
- Constituent-level holdings list with weights / shares / values via `holdings`.
- Sector allocation breakdown via `sectors`.

Not for: equity company fundamentals (use `../fundamentals/SKILL.md`), sector ETF ranking (use `../sector_score/SKILL.md`).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `info` | yfinance | none | Free-tier path. Returns one record per symbol. |
| `holdings` | fmp | `FMP_API_KEY` (paid Starter+) | Free key returns HTTP 402; treat as `plan_insufficient`. |
| `sectors` | fmp | `FMP_API_KEY` (paid Starter+) | Same paid-tier requirement. |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--provider`.

See `../_providers/SKILL.md` for credential mapping.

## Command

```bash
uv run scripts/etf.py SPY QQQ --type info
uv run scripts/etf.py XLK --type holdings
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "etf"`. `data` adds siblings `provider`, `type`. `data.results[]` rows: `{symbol, type, provider, ok, records|error, error_type?, error_category?}`.

Per-`--type` `records[i]` keys:

- `info` — `symbol, name, description, inception_date, fund_type, fund_family, category, exchange, currency, nav_price, total_assets, trailing_pe, dividend_yield, dividend_rate_ttm, dividend_yield_ttm, year_high, year_low, ma_50d, ma_200d, return_ytd, return_3y_avg, return_5y_avg, beta_3y_avg, volume_avg, volume_avg_10d, bid, ask, open, high, low, volume, prev_close`. `records` length 1.
- `holdings` — `symbol, name, cusip, weight, shares, value, updated`. `records` length is the constituent count (e.g. ~75 for XLK).
- `sectors` — `symbol, sector, weight`. `records` length is the sector count (typically 1–11; varies by index).

```json
{
  "tool": "etf",
  "data": {
    "provider": "yfinance", "type": "info",
    "results": [{"symbol": "SPY", "type": "info", "provider": "yfinance", "ok": true,
                 "records": [{"symbol": "SPY", "name": "State Street SPDR S&P 500 ETF Trust",
                              "inception_date": "1993-01-22", "total_assets": null,
                              "ma_50d": null, "currency": "USD"}]}]
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `--type holdings` or `--type sectors` on a free FMP key → `error_category: plan_insufficient`. Skip the sub-mode and record `skipped: paid tier required` in the consumer's summary; do not retry.
- `--type holdings` / `sectors` without `FMP_API_KEY` set → `error_category: credential`; set the key or skip.
- Per-row `ok: false` for an unknown symbol → `error_category: other`; drop the row.

## References

- `scripts/etf.py`
- `README.md` § 1 row 10 (ETF info / holdings / sector breakdown).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
