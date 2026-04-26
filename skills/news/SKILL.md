---
name: news
description: >-
  Fetch company-specific or world / macro news articles with cited URLs.
  Use when an agent needs catalyst or sentiment signal for tickers, or a
  macro headline feed for regime context.
covers_scripts:
  - scripts/news.py
requires_keys:
  - FMP_API_KEY
---

## When to Use

- Per-ticker news flow over the last `--days N` (`--scope company`).
- Macro / world headlines for regime or risk reads (`--scope world`).

Not for: economic-release calendars (use `../calendars/SKILL.md`), social sentiment scoring (not exposed by this wrapper).

## Inputs

| `--scope` | Default provider | Required keys | Notes |
|---|---|---|---|
| `company` (default) | yfinance | none | `<symbols>...` required; per-symbol fanout. Other providers: benzinga, fmp, intrinio, tiingo, tmx. |
| `world` | fmp | `FMP_API_KEY` (free tier OK) | Positional `symbols` ignored with non-fatal validation warning. Other providers: benzinga, biztoc, intrinio, tiingo. |

Other args: `--days <N>` (window translated to `start_date = today - N`, default 7), `--limit <N>`.

See `../_providers/SKILL.md` for credential mapping.

## Command

```bash
uv run scripts/news.py AAPL --days 7 --limit 5
uv run scripts/news.py --scope world --days 3 --limit 20
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "news"`. `data` adds siblings `scope`, `provider`, `days`, `limit`.

- `--scope company`: `data.results[]` rows are `{symbol, provider, ok, records|error, error_type?, error_category?}` (aggregate_emit shape). `records[i]` keys (yfinance): `date, id, source, summary, text, title, url, symbol`.
- `--scope world`: `data.results[]` is a flat article list (single_emit shape). Keys (fmp): `date, title, source, author, url, excerpt, images`.

Per-item field shapes are provider-native; only the envelope is uniform across providers.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- `--scope world` without `FMP_API_KEY` (default provider) → exit 2 with `error_category: credential`. Set the key or pick a keyless provider (`biztoc`, etc.).
- `--scope world` with positional symbols → non-fatal `validation` warning; symbols are ignored.
- Per-row `ok: false` for a company with no recent news → `error_category: other`; drop the row.

## References

- `scripts/news.py`
- `README.md` § 1 row 15 (Company / world news).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
