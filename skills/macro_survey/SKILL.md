---
name: macro_survey
description: >-
  Fetch a macro survey or indicator series (SLOOS, regional Fed manufacturing,
  Michigan, inflation expectations, payrolls, FOMC docs, OECD CLI, primary
  dealer positioning). Use when an agent needs a single named macro time
  series for regime or stance analysis.
covers_scripts:
  - scripts/macro_survey.py
requires_keys:
  - FRED_API_KEY
---

## When to Use

- Reading one named survey/indicator series for regime classification.
- Fetching FOMC document metadata (urls, dates) for downstream summarisation.

Not for: equity calendars (use `../calendars/SKILL.md`), Fama-French factors (use `../factors/SKILL.md`).

## Inputs

| `--series` | Default provider | Required keys | Notes |
|---|---|---|---|
| `sloos` | fred | `FRED_API_KEY` | Net % banks tightening; decimals (`0.306` == 30.6%). |
| `ny_manufacturing` / `tx_manufacturing` | fred | `FRED_API_KEY` | Regional Fed diffusion index, raw points. |
| `michigan` | fred | `FRED_API_KEY` | Sentiment is index points; inflation-expectation fields are decimals. |
| `inflation_exp` | federal_reserve | none | Raw percent (`2.99` == 2.99%). |
| `chicago_conditions` | fred | `FRED_API_KEY` | Chicago Fed conditions index. |
| `nonfarm_payrolls` | fred | `FRED_API_KEY` | Absolute headcount value. |
| `fomc_documents` | (default) | none | Document metadata only — no numeric values. |
| `cli` | oecd | none | OECD CLI; index (100 = long-run trend). |
| `dealer_positioning` | (default) | none | Net position in millions of USD. |

Other args: `--start <YYYY-MM-DD>`, `--end <YYYY-MM-DD>`, `--provider <name>`. See `../_providers/SKILL.md`.

## Command

```bash
uv run scripts/macro_survey.py --series sloos --start 2024-01-01
uv run scripts/macro_survey.py --series fomc_documents
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "macro_survey"`. Single emit. `data` adds siblings `series`, `provider`, `unit_note` (one-line scale hint per series).

`data` namespace:

- `data.results[]` — observations sorted ascending by `date`.
- Numeric series (`sloos`, `*_manufacturing`, `michigan`, `chicago_conditions`, `inflation_exp`, `nonfarm_payrolls`, `cli`, `dealer_positioning`): `{date, symbol?, value, title?, …}`. Read `unit_note` before interpreting `value`.
- `fomc_documents`: `{date, doc_type, doc_format, url}` — no `value` field.

```json
{
  "tool": "macro_survey",
  "data": {
    "series": "sloos", "provider": "fred",
    "unit_note": "FRED value normalized to decimal (0.306 == 30.6%)",
    "results": [
      {"date": "2024-01-01", "symbol": "DRISCFLM", "value": 0.306,
       "title": "Net Percentage of Domestic Banks Increasing Spreads ..."}
    ]
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- FRED-backed series without `FRED_API_KEY` → exit 2 with `error_category: credential`. Set the key or pick a non-FRED series; do not retry.
- `--start` malformed or after `--end` → `error_category: validation`; correct and retry.

## References

- `scripts/macro_survey.py`
- `README.md` § 1 row 9 (Macro surveys).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
