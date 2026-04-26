---
name: quote
description: >-
  Fetch the latest quote (price, bid/ask, volume, 52w high/low, moving
  averages) for one or more tickers. Use when an agent needs a current
  snapshot price line for equities or ETFs.
covers_scripts:
  - scripts/quote.py
---

## When to Use

- A pipeline step needs the latest price, bid/ask, day range, or moving averages for one or many tickers.
- Multi-symbol calls (`AAPL MSFT 7203.T`) — `aggregate_emit` returns one row per symbol.

Not for: historical OHLCV time series (use `../historical/SKILL.md`), analyst price targets (use `../estimates/SKILL.md`).

## Inputs

| Argument | Required | Notes |
|---|---|---|
| `<symbols>...` | yes | One or more tickers, space-separated. Non-US markets use Yahoo suffixes (e.g. `7203.T`). |
| `--provider <name>` | no | Default `yfinance`. Override only when the agent has a specific provider need. |

No env vars required (yfinance keyless). See `../_providers/SKILL.md`.

## Command

```bash
uv run scripts/quote.py AAPL MSFT 7203.T
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "quote"`.

`data` namespace:

- `data.provider` — provider used for the call (e.g. `"yfinance"`).
- `data.results[]` — one row per input symbol, shape `{symbol, provider, ok, records|error, error_type?, error_category?}`.
- `data.results[i].records[]` — on `ok: true`, a single-element list of quote records with keys:
  `symbol, asset_type, name, exchange, bid, bid_size, ask, ask_size, last_price, open, high, low, volume, prev_close, year_high, year_low, ma_50d, ma_200d, volume_average, volume_average_10d, currency`.

```json
{
  "tool": "quote",
  "data": {
    "results": [
      {"symbol": "AAPL", "provider": "yfinance", "ok": true,
       "records": [{"symbol": "AAPL", "last_price": 271.06, "volume": 38033227, "currency": "USD", "...": "..."}]}
    ],
    "provider": "yfinance"
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Per-row `ok: false` with `error_category: other` for an unknown ticker symbol → drop the row, continue.
- Provider outage → `error_category: transient`; retry once before warning.

## References

- `scripts/quote.py`
- `README.md` § 1 row 1 (Latest quote).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
