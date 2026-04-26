---
name: historical
description: >-
  Fetch a historical OHLCV time series for a single ticker. Use when an
  agent needs daily/weekly/monthly bars for backtesting, momentum, or
  drawdown calculations.
covers_scripts:
  - scripts/historical.py
---

## When to Use

- Building a price-history series for one symbol over a fixed window.
- Computing returns, moving averages, drawdowns, or volatility from raw bars.

Not for: latest snapshot quotes (use `../quote/SKILL.md`), pre-computed momentum indicators (use `../momentum/SKILL.md`).

## Inputs

| Argument | Required | Notes |
|---|---|---|
| `<symbol>` | yes | Single ticker (positional). Yahoo suffix for non-US (e.g. `7203.T`). |
| `--start <YYYY-MM-DD>` | yes | Inclusive lower bound. |
| `--end <YYYY-MM-DD>` | no | Inclusive upper bound. Defaults to today. |
| `--interval <code>` | no | `1d` (default), `1w`, `1mo`, etc. Provider must support the interval. |
| `--provider <name>` | no | `yfinance` (default) or `fmp`. |

No env vars required for the default `yfinance` path. See `../_providers/SKILL.md`.

## Command

```bash
uv run scripts/historical.py AAPL --start 2026-01-01 --end 2026-04-25
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "historical"`. Single-symbol `aggregate_emit` — `data.results` always has length 1.

`data` namespace:

- `data.provider` — provider used.
- `data.results[0]` — `{symbol, provider, interval, ok, records|error, rows?, error_type?, error_category?}`.
- `data.results[0].records[]` — bars sorted ascending by `date`. Keys: `date, open, high, low, close, volume`. Some providers add `vwap`, `dividends`, or `split_ratio`; downstream code should treat unknown keys as optional.
- `data.results[0].rows` — record count (present on success).

```json
{
  "tool": "historical",
  "data": {
    "results": [
      {"symbol": "AAPL", "provider": "yfinance", "interval": "1d", "ok": true, "rows": 5,
       "records": [{"date": "2026-04-20", "open": 270.33, "high": 274.28, "low": 270.29, "close": 273.05, "volume": 36590200}]}
    ],
    "provider": "yfinance"
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `--start` / `--end` malformed or `start > end` → `error_category: validation`; correct the dates and retry.
- Empty `records` with `ok: true` for a non-trading window (holiday / future date) → not an error; the row simply has `rows: 0`.

## References

- `scripts/historical.py`
- `README.md` § 1 row 2 (Historical prices).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
