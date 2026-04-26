---
name: momentum
description: >-
  Compute a single technical indicator (clenow / rsi / macd / cones / adx)
  for one or more tickers. Use when an agent needs a pre-computed momentum
  or volatility-cone signal rather than raw OHLCV bars.
covers_scripts:
  - scripts/momentum.py
---

## When to Use

- Ranking tickers by clenow exponential-regression momentum (`clenow`).
- Reading the latest RSI / MACD / ADX value or full short series for confirmation signals.
- Plotting realised volatility cones across tenors (`cones`).

Not for: raw OHLCV bars (use `../historical/SKILL.md`), composite multi-indicator scoring (use `../sector_score/SKILL.md`).

## Inputs

| `--indicator` | Default provider | Required keys | Notes |
|---|---|---|---|
| `clenow` | yfinance | none | `momentum_factor`, `r_squared`, `fit_coef` per symbol. Tunable via `--period` (default 90). |
| `rsi` | yfinance | none | Tunable via `--length` (default 14). |
| `macd` | yfinance | none | Tunable via `--fast`/`--slow`/`--signal` (12/26/9). |
| `cones` | yfinance | none | Tunable via `--lower-q`/`--upper-q` (0.25/0.75). |
| `adx` | yfinance | none | Tunable via `--length` (default 14). |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--provider`, `--start <YYYY-MM-DD>` (auto-selected per indicator if omitted).

See `../_providers/SKILL.md` for keyless yfinance defaults.

## Command

```bash
uv run scripts/momentum.py AAPL MSFT --indicator clenow
uv run scripts/momentum.py AAPL --indicator rsi --length 14
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "momentum"`. `data` adds siblings `indicator`, `provider`, `start`. `data.results[]` rows: `{symbol, indicator, ok, <indicator-specific fields>, error?, error_type?, error_category?}`.

Per-`--indicator` `data.results[i]` keys:

- `clenow` — `period, momentum_factor, r_squared, fit_coef, rank` (rank assigned across the input symbols).
- `rsi` — `length, latest_value, latest_date, history[]` (each `{date, rsi}`).
- `macd` — `fast, slow, signal, latest{macd, signal, histogram}, latest_date, history[]`.
- `cones` — `lower_q, upper_q, latest_realized_vol, cones[]` (per-tenor `{window, p_lower, p_median, p_upper}`).
- `adx` — `length, latest_value, latest_date, history[]` (each `{date, adx}`).

```json
{
  "tool": "momentum",
  "data": {
    "indicator": "clenow", "provider": "yfinance",
    "results": [{"symbol": "AAPL", "indicator": "clenow", "ok": true,
                 "period": 90, "momentum_factor": -0.00834, "r_squared": 0.09058,
                 "fit_coef": -0.09203, "rank": 1}]
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Missing price history for a symbol → per-row `ok: false`, `error_category: other`; drop the row and continue.
- `--start` after today / `start > end` → `error_category: validation`; correct dates and retry.

## References

- `scripts/momentum.py`
- `README.md` § 1 row 8 (Momentum / technicals).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
