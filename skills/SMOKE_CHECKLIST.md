# SMOKE_CHECKLIST.md

Manual smoke checklist for `skills/`. Run each entry against live APIs and diff stdout JSON against the SKILL's `## Output`. No automated tests — drift is caught by humans.

## Trigger

- PR touches `skills/`, `scripts/<name>.py`, `scripts/_common.py`, `scripts/_env.py`, or `README.md` §1-1 → re-run affected entries.
- Wrapper signature / sub-mode / provider / `error_category` change → re-run affected per-wrapper + composite entries.
- Monthly sweep with fixed inputs to catch silent provider drift.

## How

1. Ensure `.env` has `FMP_API_KEY` / `FRED_API_KEY` / `EIA_API_KEY` / `SEC_USER_AGENT` (free-tier-only entries skip key check).
2. Run command.
3. Diff stdout `data.*` against the linked SKILL's `## Output`.
4. On mismatch: update the SKILL's `## Output` to match observed, or fix the wrapper. Never leave drift.

## Per-Wrapper (17)

Fixed inputs: equities `AAPL`, ETFs `SPY`, macro `--series sloos`, commodity `--type weekly_report`, calendars 5-day window from today, factors `--region america --frequency monthly`.

| #    | SKILL                                                 | Command                                                                           | Expected `data.*` keys                                                                                                                      |
| ---- | ----------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | [quote](quote/SKILL.md)                               | `uv run scripts/quote.py AAPL`                                                    | `provider`, `results[].{symbol, ok, records}`                                                                                               |
| 2    | [historical](historical/SKILL.md)                     | `uv run scripts/historical.py AAPL --start 2026-01-01 --end 2026-04-25`           | `results[].{symbol, ok, records[].{date, open, high, low, close, volume}}`                                                                  |
| 3    | [fundamentals](fundamentals/SKILL.md) `overview`      | `uv run scripts/fundamentals.py AAPL --type overview`                             | `provider`, `type`, `results[].records[].{symbol, name, sector, market_cap, currency}`                                                      |
| 3'   | [fundamentals](fundamentals/SKILL.md) `ratios` (paid) | `uv run scripts/fundamentals.py AAPL --type ratios`                               | each ratio is `{value, unit}`                                                                                                               |
| 4    | [estimates](estimates/SKILL.md)                       | `uv run scripts/estimates.py AAPL --type consensus`                               | `results[].records[].{target_high, target_low, target_median, recommendation_mean, analyst_count}`                                          |
| 5    | [sector_score](sector_score/SKILL.md)                 | `uv run scripts/sector_score.py --universe sector-spdr`                           | `universe`, `tickers`, `weights`, `results[].{ticker, rank, composite_score_0_100, signals, z_scores}`                                      |
| 6    | [momentum](momentum/SKILL.md)                         | `uv run scripts/momentum.py AAPL --indicator clenow`                              | `indicator`, `provider`, `results[].{symbol, period, momentum_factor, r_squared, fit_coef, rank}`                                           |
| 7    | [macro_survey](macro_survey/SKILL.md)                 | `uv run scripts/macro_survey.py --series sloos --start 2024-01-01`                | `series`, `provider`, `unit_note`, `results[].{date, value}` (needs `FRED_API_KEY`)                                                         |
| 8    | [etf](etf/SKILL.md) `info`                            | `uv run scripts/etf.py SPY --type info`                                           | `results[].records[].{symbol, name, inception_date, currency}`                                                                              |
| 8'   | [etf](etf/SKILL.md) `holdings` (paid)                 | `uv run scripts/etf.py SPY --type holdings`                                       | `records[].{symbol, weight, shares}` or `error_category=plan_insufficient`                                                                  |
| 9    | [calendars](calendars/SKILL.md)                       | `uv run scripts/calendars.py --type earnings --start 2026-04-26 --end 2026-04-30` | `type`, `provider`, `start`, `end`, `results[].{report_date, symbol, eps_consensus}`                                                        |
| 10   | [insider](insider/SKILL.md)                           | `uv run scripts/insider.py AAPL --days 30 --limit 20`                             | `provider`, `days`, `limit`, `results[].records[].{transaction_date, transaction_type, securities_transacted}` (needs `SEC_USER_AGENT`)     |
| 11   | [institutional](institutional/SKILL.md)               | `uv run scripts/institutional.py AAPL --year 2024 --quarter 4`                    | `provider`, `year`, `quarter`, `results[].records[].{symbol, investors_holding, ownership_percent, total_invested}` (needs `FMP_API_KEY`)   |
| 12   | [filings](filings/SKILL.md)                           | `uv run scripts/filings.py AAPL --form 10-K,10-Q --limit 20`                      | `provider`, `form_filter`, `limit`, `results[].records[].{filing_date, report_type, accession_number, report_url}` (needs `SEC_USER_AGENT`) |
| 13   | [news](news/SKILL.md) `company`                       | `uv run scripts/news.py AAPL --days 7 --limit 5`                                  | `scope`, `provider`, `days`, `limit`, `results[].records[].{date, title, url, source}`                                                      |
| 13'  | [news](news/SKILL.md) `world`                         | `uv run scripts/news.py --scope world --days 3 --limit 20`                        | `results[].{date, title, source, url}` or `error_category=plan_insufficient` (needs `FMP_API_KEY`)                                          |
| 14   | [options](options/SKILL.md) `chain`                   | `uv run scripts/options.py AAPL --type chain`                                     | `provider`, `type`, `symbol`, `results[].{contract_symbol, strike, bid, ask, implied_volatility, open_interest}`                            |
| 14'  | [options](options/SKILL.md) `iv`                      | `uv run scripts/options.py AAPL --type iv`                                        | `results[].{expiration, atm_iv, put_call_oi_ratio}`                                                                                         |
| 15   | [factors](factors/SKILL.md)                           | `uv run scripts/factors.py --region america --frequency monthly`                  | `region`, `frequency`, `factor`, `defaults_applied`, `results[].{date, mkt_rf, smb, hml, rmw, cma, rf}`                                     |
| 16   | [commodity](commodity/SKILL.md) `weekly_report`       | `uv run scripts/commodity.py --type weekly_report --start 2026-01-01`             | `provider`, `type`, `results[].{date, symbol, table, title, value, unit, order}`                                                            |
| 16'  | [commodity](commodity/SKILL.md) `price`               | `uv run scripts/commodity.py --symbol wti --type price --start 2024-01-01`        | `results[].{date, symbol, commodity, price, unit}` (needs `FRED_API_KEY`)                                                                   |
| 16'' | [commodity](commodity/SKILL.md) `steo`                | `uv run scripts/commodity.py --type steo`                                         | `results[].{date, symbol, table, title, value, unit, order}` (needs `EIA_API_KEY`)                                                          |
| 17   | [shorts](shorts/SKILL.md) `short_interest`            | `uv run scripts/shorts.py AAPL --type short_interest`                             | `provider`, `type`, `results[].records[].{settlement_date, current_short_position, days_to_cover}`                                          |
| 17'  | [shorts](shorts/SKILL.md) `fails_to_deliver`          | `uv run scripts/shorts.py AAPL --type fails_to_deliver`                           | `results[].records[].{settlement_date, cusip, quantity, price}` (needs `SEC_USER_AGENT`)                                                    |

## Common Policy (3)

Verify SKILL text against current source — no wrapper run.

| SKILL                              | Check against                                                                             |
| ---------------------------------- | ----------------------------------------------------------------------------------------- |
| [\_envelope](_envelope/SKILL.md)   | `scripts/_common.py` envelope shape, exit-code contract, `single_emit` / `aggregate_emit` |
| [\_providers](_providers/SKILL.md) | `scripts/_env.py::_CREDENTIAL_MAP` entries, `SEC_USER_AGENT` special case                 |
| [\_errors](_errors/SKILL.md)       | `scripts/_common.py::ErrorCategory` 5 values, `CredentialError:` / `PlanError:` prefixes  |

## Composite (3)

Run each pipeline end-to-end; confirm exit 0 per step and merged summary keys.

| SKILL                                                         | Pipeline                                                                                                                                                                                | Summary keys                                                                                                                                                                                 |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [single-stock-snapshot](single-stock-snapshot/SKILL.md)       | `quote AAPL` → `fundamentals --type overview` → `estimates --type consensus` → `news --scope company` → `insider` → `momentum --indicator clenow` → (paid) `fundamentals --type ratios` | `{ticker, collected_at, quote, fundamentals_overview, estimates_consensus, news_company, insider, momentum_clenow, ratios?}`; free-tier sets `ratios = {skipped: true, reason: "free tier"}` |
| [sector-rotation-snapshot](sector-rotation-snapshot/SKILL.md) | `sector_score --universe sector-spdr` → top/bottom 3 tickers → `momentum <top> --indicator clenow` → `momentum <bottom> --indicator clenow`                                             | `{collected_at, sector_scores, top_sector_momentum[], bottom_sector_momentum[]}`                                                                                                             |
| [macro-snapshot](macro-snapshot/SKILL.md)                     | `macro_survey` for `sloos`, `michigan`, `inflation_exp` → `commodity --type weekly_report` → `commodity --type price` → `factors --region america --frequency monthly`                  | `{collected_at, macro_series, commodity_weekly, commodity_price, factors}`; missing key sets affected slot to `{skipped: true, reason: "key required"}`                                      |

## Maintenance Rules

- **ChangeBundle** — bundle into one PR: SKILL add/rename/delete with `INDEX.md`; new wrapper with its per-wrapper SKILL; `_common.py` envelope/`ErrorCategory` change with `_envelope`/`_errors`; `_env.py::_CREDENTIAL_MAP` change with `_providers` and affected `requires_keys`; `README.md` §1-1 sub-mode/provider change with the per-wrapper SKILL's `## Inputs`.
- **ReferenceIntegrity** — every `## References` `scripts/<name>.py` and `covers_scripts:` path must exist on disk. Verify with `ls scripts/<name>` at review.
- **DeadLinkPrevention** — consumer `agent/instruction.md` SKILL links land only after the SKILL is merged. SKILL first, slim second.
- **DriftResponse** — `## Output` mismatch with live run: update the SKILL or fix the wrapper. Never leave drift.
- **NoAutomatedTests** — no `tests/` additions for `skills/`. Integrity is held by ChangeBundle review, this checklist, and the once-at-authoring live run (AUTHORING.md §5).
