---
name: options
description: >-
  Fetch the options chain or a derived implied-volatility view for a single
  ticker. Use when an agent needs per-contract chain rows or a
  per-expiration ATM IV / put-call OI ratio summary.
covers_scripts:
  - scripts/options.py
---

## When to Use

- Reading the raw per-contract chain (`--type chain`): strikes, bid/ask, IV, OI, volume.
- Reading the per-expiration aggregate (`--type iv`): `atm_iv` and open-interest-weighted `put_call_oi_ratio`.

Not for: implied-vol surfaces beyond ATM (build externally from chain), unusual-activity scans (not exposed; see README §1-2 footnote).

## Inputs

| `--type` | Default provider | Required keys | Notes |
|---|---|---|---|
| `chain` (default) | yfinance | none | Single-symbol only. Returns 2k+ rows for a liquid US name. |
| `iv` | yfinance | none | One row per expiration; aggregated from the chain. |

Other args: `<symbol>` (single positional, multi-symbol intentionally unsupported), `--expiration YYYY-MM-DD` (post-filter on chain rows), `--provider {yfinance,cboe}` (cboe is keyless, US listings only).

See `../_providers/SKILL.md` for keyless defaults.

## Command

```bash
uv run scripts/options.py AAPL --type chain
uv run scripts/options.py AAPL --type iv
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "options"`. `data` adds siblings `provider`, `type`, `symbol`. Single emit; `data.results[]` is a flat list (no per-symbol fanout).

Per-`--type` `data.results[i]` keys:

- `chain` — `contract_symbol, underlying_symbol, underlying_price, expiration, dte, option_type, strike, bid, ask, last_trade_price, last_trade_time, volume, open_interest, implied_volatility, in_the_money, change, change_percent, currency`.
- `iv` — `expiration, atm_iv, put_call_oi_ratio`. `data.missing_fields` (list) flags any aggregate fields the underlying chain could not populate.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Multi-symbol input rejected by argparse → `error_category: validation`; submit one ticker at a time.
- Symbol with no listed options → empty `results: []`; not an error.
- Provider rate limit / transient outage → `error_category: transient`; one retry then warn.

## References

- `scripts/options.py`
- `README.md` § 1 row 16 (Options chain / IV surface).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
