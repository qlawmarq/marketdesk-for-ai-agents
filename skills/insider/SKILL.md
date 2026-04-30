---
name: insider
description: >-
  Fetch Form 4 insider-trading records (officers / directors / 10%-owners)
  per ticker, normalized to a provider-invariant 19-field schema and
  optionally narrowed to a chosen set of single-letter Form 4 codes
  (P/S/A/F/M/C/G/D/J/W). Outputs JSON or a pasteable markdown table.
covers_scripts:
  - scripts/insider.py
requires_keys:
  - SEC_USER_AGENT
---

## When to Use

- Reading recent insider transactions on one or more tickers.
- Conviction filter: `--transaction-codes P` for open-market purchases only.
- Discretionary-trade filter: `--transaction-codes P,S` to drop tax-withholding (F), grants (A), and option-exercise (M) records.
- Pasting a per-ticker activity table directly into a draft via `--format md`.

Not for: 13F institutional holdings (use `../institutional/SKILL.md`), the underlying SEC filings index (use `../filings/SKILL.md`).

## Inputs

Default provider: `sec` (requires `SEC_USER_AGENT`). Args: `<symbols>...` (positional, multi-symbol fan-out), `--days <N>` (client-side window on `transaction_date` / `filing_date`, default 90), `--limit <N>` (upstream row cap, optional), `--provider {sec,fmp,intrinio,tmx}` (`fmp` requires `FMP_API_KEY`), `--transaction-codes <CSV>` (single-letter codes, case-insensitive, uppercased), `--format {json,md}` (default json). See `../_providers/SKILL.md` for credential mapping.

## Command

```bash
uv run scripts/insider.py AAPL --days 90 --transaction-codes P,S --format md
```

## Output

JSON path: [shared envelope](../_envelope/SKILL.md). `tool = "insider"`. `data` adds siblings `provider`, `days`, `transaction_codes` (uppercased list or `null`), and `limit` when supplied. `data.results[]` rows: `{symbol, provider, ok, records, dropped_unparseable_codes}` on success; `{symbol, provider, ok: false, error, error_type, error_category}` on failure (no `records` / `dropped_unparseable_codes`).

Each `records[i]` carries the same 19 keys across every provider: `filing_date, transaction_date, reporter_name, reporter_title, transaction_code, transaction_code_label, transaction_type_raw, acquisition_or_disposition, shares, price, total_value, shares_after, form_type, url, ownership_type, security_type, company_cik, owner_cik, footnote`. Missing upstream values render as `null`, never absent.

`transaction_code` is one of:

| Code | Meaning |
|---|---|
| P | Open Market Purchase |
| S | Open Market Sale |
| A | Grant or Award |
| F | Tax Withholding Payment |
| M | Exempt Exercise or Conversion |
| C | Derivative Conversion |
| G | Bona Fide Gift |
| D | Disposition to Issuer |
| J | Other (see footnote) |
| W | Will or Inheritance |

Markdown path (`--format md`): one `## <SYMBOL>` heading per ticker, then either a fixed-column table (`filing_date | transaction_date | reporter_name | reporter_title | transaction_code | transaction_code_label | shares | price | total_value | shares_after | url`), or `_no records in window_` (with optional ` (dropped <N> unparseable codes)` suffix when a code filter is active), or `_error_category_: <category> — <error>`.

## Reading Guidance

- **Discretionary vs accounting flow**: only `P` and `S` reflect a discretionary open-market decision. `M` (exercise) classifies as `acquisition_or_disposition: "A"` and `F` (tax withholding) as `"D"`, but both are mechanical settlement legs of an RSU / option event, not directional buys/sells. Filter to `transaction_code in {"P","S"}` to isolate intent; reading `acquisition_or_disposition` alone conflates intent with mechanics.
- **One filing → multiple rows**: a single Form 4 commonly emits several records. A typical vesting + tax-withhold + market sale appears as `M + F + S` rows sharing one `filing_date` and `reporter_name`. Count distinct `(filing_date, reporter_name)` pairs — not raw row count — when summarising transaction frequency.
- **10b5-1 plans live in `footnote`**: a pre-scheduled trade is identifiable only by the `footnote` text (look for `"10b5-1 trading plan"`). `footnote` is **not** in the markdown column set, so any `--format md` summary must be cross-referenced against the JSON envelope before treating an `S` row as a discretionary sell signal.
- **`shares_after` is per `security_type`**: an `M` row on `Restricted Stock Unit` and an `S` row on `Common Stock` report different `shares_after` values for the same insider. Pair the field with `security_type` before inferring position size.
- **`--limit` interacts with `--days`**: `--limit` is applied upstream before the client-side day window. With a small `--limit` and a wide `--days`, older rows are silently truncated and "no records in window" can mean "truncated" rather than "no activity". Omit `--limit` to keep the window faithful.
- **Provider coverage differs**: SEC is canonical for US Form 4 and the keyless default. FMP can be delayed and may return a subset. Record counts are not directly comparable across providers even though the 19-key schema matches.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `SEC_USER_AGENT` unset on the default `sec` provider → `error_category: credential`. Set the env var or switch provider; do not retry.
- All rows credential-fatal under `--format md` → wrapper falls back to the JSON envelope with exit 2 so the recovery hint reaches the agent.
- Empty `records: []` for a valid ticker / window → not an error; exit 0.

## References

- `scripts/insider.py`
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
