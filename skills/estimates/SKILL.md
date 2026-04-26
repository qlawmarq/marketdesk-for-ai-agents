---
name: estimates
description: >-
  Fetch analyst consensus or price-target revision history for one or
  more tickers. Use when an agent needs aggregated analyst expectations
  or per-firm rating-change events for an equity.
covers_scripts:
  - scripts/estimates.py
---

## When to Use

- Aggregated analyst view: target high/low/median, recommendation mean, analyst count (`consensus`).
- Time-ordered list of per-firm rating changes and price-target revisions (`price_target`).

Not for: company fundamentals (use `../fundamentals/SKILL.md`), earnings calendar dates (use `../calendars/SKILL.md`).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `consensus` | yfinance | none | One aggregated row per symbol. |
| `price_target` | finviz | none | Multiple rows per symbol — analyst-firm revision history. |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--provider`.

See `../_providers/SKILL.md` for keyless defaults.

## Command

```bash
uv run scripts/estimates.py AAPL MSFT --type consensus
uv run scripts/estimates.py AAPL --type price_target
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "estimates"`. `data` adds siblings `provider`, `type`. `data.results[]` rows: `{symbol, type, provider, ok, records|error, error_type?, error_category?}`.

Per-`--type` `records[i]` keys:

- `consensus` — `symbol, target_high, target_low, target_consensus, target_median, recommendation, recommendation_mean, number_of_analysts, current_price, currency`. `records` length 1.
- `price_target` — `published_date, symbol, status, rating_change, analyst_company, price_target, adj_price_target`. `records` length is the per-symbol revision count; `price_target` / `adj_price_target` may be `null` when the analyst note carries no numeric target.

```json
{
  "tool": "estimates",
  "data": {
    "results": [{"symbol": "AAPL", "type": "consensus", "provider": "yfinance", "ok": true,
                 "records": [{"symbol": "AAPL", "target_high": 350.0, "target_low": 215.0, "target_consensus": 297.7,
                              "number_of_analysts": 40, "recommendation": "buy", "currency": "USD"}]}],
    "provider": "yfinance", "type": "consensus"
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Per-row `ok: false` for an unknown ticker → `error_category: other`; drop the row.
- Finviz rate limit on bulk `price_target` calls → `error_category: transient`; retry once with backoff.

## References

- `scripts/estimates.py`
- `README.md` § 1 row 6 (Analyst estimates / price targets).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
