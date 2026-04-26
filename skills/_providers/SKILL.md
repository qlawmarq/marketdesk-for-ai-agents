---
name: _providers
description: >-
  Map env-var names to OpenBB credential attributes and identify which
  wrappers run keyless. Use when deciding which `.env` keys must be set
  before invoking a wrapper or sub-mode.
---

## When to Use

- Before running a wrapper for the first time on a new environment.
- When a wrapper returns `error_category: credential` and the agent needs to know which env-var to populate.
- When choosing between key-free and keyed providers for a sub-mode.

Not for: per-wrapper sub-mode arguments, or HTTP / plan-tier failure handling (see `../_errors/SKILL.md`).

## Inputs

None. This skill describes provider/credential conventions.

## Command

None. Wrappers call `apply_to_openbb()` themselves on import.

## Output

Keyless defaults — most wrappers run end-to-end without any key:

- `yfinance` — `quote`, `historical`, `fundamentals --type overview|metrics|income|balance|cash`, `estimates`, `momentum`, `etf --type info`, `options`, default fallbacks.
- `finviz` — `estimates --type price_target`, `sector_score` (price-performance leg).
- `nasdaq` — `calendars`.
- `oecd` — `macro_survey --series cli`.
- `federal_reserve` — `macro_survey --series fomc_documents | dealer_positioning`.
- `famafrench` — `factors`.
- `finra` — `shorts --type short_interest`.
- `eia` (Weekly Petroleum Status) — `commodity --type weekly_report` downloads the public Excel file directly; `EIA_API_KEY` is not consulted on this sub-mode.

Credential map (`scripts/_env.py::_CREDENTIAL_MAP` — env-var → `obb.user.credentials.<attr>`):

| Env var | Credentials attribute | Used by |
|---|---|---|
| `FMP_API_KEY` | `fmp_api_key` | `fundamentals --type ratios` (free OK), `institutional`, `news --scope world`, `etf --type holdings\|sectors` (paid Starter+) |
| `FRED_API_KEY` | `fred_api_key` | `macro_survey --series sloos\|ny_manufacturing\|tx_manufacturing\|michigan\|inflation_exp\|chicago_conditions\|nonfarm_payrolls`, `commodity --type price` |
| `EIA_API_KEY` | `eia_api_key` | `commodity --type steo` only |

`SEC_USER_AGENT` is **not** in `_CREDENTIAL_MAP`. OpenBB's `sec` provider reads the env-var directly, so `_env.py` only needs `load_dotenv` for it. Required by `insider`, `filings`, `shorts --type fails_to_deliver`. Format: `"Name email@example.com"`.

Do not record the key value itself in any skill. Skills name the env-var; `.env` holds the value.

## Failure Handling

Missing key → wrappers emit `error_category: credential`. Plan-tier rejection (e.g., `etf --type holdings` on a free FMP key) → `error_category: plan_insufficient`. Map both via `../_errors/SKILL.md`.

## References

- `scripts/_env.py` — `_CREDENTIAL_MAP`, `apply_to_openbb`.
- `README.md` § 2-2 (API keys) and § 5 (.env conventions).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`.
