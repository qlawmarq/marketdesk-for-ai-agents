---
name: filings
description: >-
  Fetch the SEC filings index per ticker, optionally filtered by form type
  (10-K / 10-Q / 8-K / 4 / 13F-HR / …). Use when an agent needs to locate
  the underlying EDGAR documents that back a fundamentals or disclosure
  question.
covers_scripts:
  - scripts/filings.py
requires_keys:
  - SEC_USER_AGENT
---

## When to Use

- Listing recent filings for one or more tickers.
- Filtering to a specific form set via `--form 10-K,10-Q,8-K`.
- Resolving the EDGAR `report_url` / `complete_submission_url` for downstream document fetch.

Not for: parsed financial statements (use `../fundamentals/SKILL.md`), insider Form 4 transactions (use `../insider/SKILL.md`).

## Inputs

| Sub-mode | Default provider | Required keys | Notes |
|---|---|---|---|
| (single mode) | sec | `SEC_USER_AGENT` env var | sec accepts CSV `--form` server-side. |

Other args: `<symbols>...` (positional, multi-symbol via `aggregate_emit`), `--form <CSV>` (e.g. `10-K,10-Q,8-K,4`), `--limit <N>`. Alternative providers `fmp` (post-filtered), `intrinio` (single form), `nasdaq` / `tmx` (post-filtered).

The requested form filter is echoed inside each per-symbol entry as `form_filter`, distinguishing "no filings of that form" from "unknown form name".

See `../_providers/SKILL.md` for credential mapping (SEC_USER_AGENT is read directly by OpenBB).

## Command

```bash
uv run scripts/filings.py AAPL --form 10-K,10-Q --limit 20
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "filings"`. `data` adds siblings `provider`, `form_filter`, `limit`. `data.results[]` rows: `{symbol, provider, form_filter, ok, records|error, error_type?, error_category?}`.

`records[i]` keys (sec provider): `filing_date, accepted_date, report_date, act, report_type, items, primary_doc_description, primary_doc, accession_number, file_number, film_number, is_inline_xbrl, is_xbrl, size, complete_submission_url, filing_detail_url, report_url`. Other providers return a subset; treat fields as provider-dependent.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `SEC_USER_AGENT` unset on the default `sec` provider → upstream rejects the request → `error_category: credential`. Set the env var or switch provider.
- `--form` value not recognised by the provider → empty `records: []` with `form_filter` echoed back; not an error.
- Per-row `ok: false` for an unknown symbol → `error_category: other`; drop the row.

## References

- `scripts/filings.py`
- `README.md` § 1 row 14 (SEC filings index).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
