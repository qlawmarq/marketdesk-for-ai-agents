---
name: factors
description: >-
  Fetch Fama-French factor return time series (Mkt-RF, SMB, HML, RMW, CMA,
  momentum, reversal) by region and frequency. Use when an agent needs
  canonical risk-factor returns for regime attribution.
covers_scripts:
  - scripts/factors.py
---

## When to Use

- Pulling 5-factor (default), 3-factor, momentum, or short/long reversal portfolio returns.
- Switching region (`america`, `developed`, `emerging`, `japan`, …) or frequency (`daily`, `weekly`, `monthly`, `annual`).

Not for: per-stock factor exposures (use `../fundamentals/SKILL.md` ratios + external regression), macro surveys (use `../macro_survey/SKILL.md`).

## Inputs

| Sub-mode | Default provider | Required keys | Notes |
|---|---|---|---|
| (single mode) | famafrench | none | Public dataset; key-free. |

Other args: `--region {america,north_america,europe,japan,asia_pacific_ex_japan,developed,developed_ex_us,emerging}` (default `america`), `--frequency {daily,weekly,monthly,annual}` (default `monthly`), `--factor {5_factors,3_factors,momentum,st_reversal,lt_reversal}` (default `5_factors`), `--start YYYY-MM-DD`, `--end YYYY-MM-DD`.

See `../_providers/SKILL.md` for keyless defaults.

## Command

```bash
uv run scripts/factors.py --region america --frequency monthly
uv run scripts/factors.py --region japan --frequency monthly --factor 3_factors
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "factors"`. `data` adds siblings `provider`, `region`, `frequency`, `factor`, `defaults_applied`. Single emit; `data.results[]` is a flat time series sorted by date.

`data.results[i]` keys per `--factor` (returns are decimals, not percent):

- `5_factors` — `date, mkt_rf, smb, hml, rmw, cma, rf`.
- `3_factors` — `date, mkt_rf, smb, hml, rf`.
- `momentum` — `date, mom`.
- `st_reversal` — `date, st_rev`.
- `lt_reversal` — `date, lt_rev`.

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- Region / frequency / factor outside the choice set → argparse rejects with `error_category: validation`.
- `--start` after `--end`, or window before the dataset's earliest date → empty `results: []` or upstream `validation`; widen the window.
- Famafrench upstream outage → `error_category: transient`; one retry then warn.

## References

- `scripts/factors.py`
- `README.md` § 1 row 17 (Fama-French factor returns).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
