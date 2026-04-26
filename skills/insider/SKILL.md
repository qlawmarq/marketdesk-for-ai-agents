---
name: insider
description: >-
  Fetch Form 4 insider-trading records (officers / directors / 10%-owners)
  per ticker. Use when an agent needs recent buy/sell/exercise activity by
  insiders for a single name or a small batch of tickers.
covers_scripts:
  - scripts/insider.py
requires_keys:
  - SEC_USER_AGENT
---

## When to Use

- Reading recent insider transactions on one or more tickers.
- Checking the `--days` window (client-side filter on `transaction_date` / `filing_date`) for fresh activity.

Not for: 13F institutional holdings (use `../institutional/SKILL.md`), the underlying filing index (use `../filings/SKILL.md`).

## Inputs

| Sub-mode | Default provider | Required keys | Notes |
|---|---|---|---|
| (single mode) | sec | `SEC_USER_AGENT` env var | `fmp` / `intrinio` / `tmx` selectable via `--provider`. |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--days <N>` (client-side window, default 90), `--limit <N>` (upstream row cap).

See `../_providers/SKILL.md` for credential mapping (SEC_USER_AGENT is read directly by OpenBB).

## Command

```bash
uv run scripts/insider.py AAPL --days 30 --limit 20
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "insider"`. `data` adds siblings `provider`, `days`, `limit`. `data.results[]` rows: `{symbol, provider, ok, records|error, error_type?, error_category?}`.

`records[i]` keys (sec provider): `symbol, company_name, company_cik, owner_name, owner_cik, owner_title, officer, director, ten_percent_owner, form, filing_date, filing_url, transaction_date, transaction_type, acquisition_or_disposition, securities_transacted, securities_owned, transaction_price, ownership_type, security_type, underlying_security_title, underlying_security_shares, exercise_date, expiration_date, footnote, other`. Fields are provider-native; non-sec providers may omit some keys.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `SEC_USER_AGENT` unset on the default `sec` provider → upstream rejects the request → `error_category: credential`. Set the env var or switch provider; do not retry.
- Per-row `ok: false` for a symbol with no Form 4 history in window → `error_category: other`; drop the row.
- Empty `records: []` for a valid ticker / window with no insider activity → not an error; exit 0.

## References

- `scripts/insider.py`
- `README.md` § 1 row 12 (Insider trading (Form 4)).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
