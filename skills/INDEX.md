# Skills Catalog

This is the entry point for every Agent Skill in `skills/`. Each
skill is a host-agnostic Markdown manual telling an AI agent how to call one
OpenBB wrapper (or compose several) correctly.

**Maintenance rule (ChangeBundleRule):** when a skill is added, renamed, or
removed, this `INDEX.md` MUST be updated in the same change. New
`scripts/<name>.py` wrappers require a matching per-wrapper skill in the same
PR. Authoring conventions live in [AUTHORING.md](AUTHORING.md); the manual
smoke checklist lives in [SMOKE_CHECKLIST.md](SMOKE_CHECKLIST.md).

## Common Policies

Cross-cutting reference skills that per-wrapper and composite skills link to
instead of duplicating contract prose.

| Skill                               | Purpose                                                                                                                                      | Covers                              |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| [`_envelope`](_envelope/SKILL.md)   | Read the shared `{source, collected_at, tool, data}` JSON envelope, partial-failure rows, and exit-code semantics.                           | `scripts/_common.py`                |
| [`_providers`](_providers/SKILL.md) | Map `.env` keys to OpenBB credential attributes; identify keyless wrappers and sub-modes.                                                    | `scripts/_env.py`                   |
| [`_errors`](_errors/SKILL.md)       | Interpret `error_category` (`credential` / `plan_insufficient` / `transient` / `validation` / `other`) and apply the default agent response. | `scripts/_common.py::ErrorCategory` |

## Per-Wrapper Skills

One skill per ✅ wrapper in `README.md` §1-1. Grouped by capability; ordering
inside each group follows the README feature numbering.

### Price & history

| Skill                               | Purpose                                                                           | Covers                  |
| ----------------------------------- | --------------------------------------------------------------------------------- | ----------------------- |
| [`quote`](quote/SKILL.md)           | Latest quote line (price, bid/ask, volume, 52w high/low) for one or more tickers. | `scripts/quote.py`      |
| [`historical`](historical/SKILL.md) | Historical OHLCV bars for a single ticker over a date range.                      | `scripts/historical.py` |

### Fundamentals & estimates

| Skill                                   | Purpose                                                                  | Covers                    |
| --------------------------------------- | ------------------------------------------------------------------------ | ------------------------- |
| [`fundamentals`](fundamentals/SKILL.md) | Profile, key metrics, three-statement financials, and ratios per ticker. | `scripts/fundamentals.py` |
| [`estimates`](estimates/SKILL.md)       | Analyst consensus and price-target revision history per ticker.          | `scripts/estimates.py`    |

### Sector / technical / factor

| Skill                                   | Purpose                                                                      | Covers                    |
| --------------------------------------- | ---------------------------------------------------------------------------- | ------------------------- |
| [`sector_score`](sector_score/SKILL.md)               | Composite momentum + risk score over a sector / theme / factor ETF universe.                                   | `scripts/sector_score.py`        |
| [`momentum`](momentum/SKILL.md)                       | Single technical indicator (clenow / rsi / macd / cones / adx) per ticker.                                     | `scripts/momentum.py`            |
| [`entry-timing-scorer`](entry-timing-scorer/SKILL.md) | Per-ticker entry-timing analytics (trend axis + mean-reversion axis + earnings-proximity flag) over a basket.  | `scripts/entry_timing_scorer.py` |
| [`factors`](factors/SKILL.md)                         | Fama-French factor return time series by region and frequency.                                                 | `scripts/factors.py`             |

### Macro & commodity

| Skill                                   | Purpose                                                                                                         | Covers                    |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------- |
| [`macro_survey`](macro_survey/SKILL.md) | Named macro / survey series (SLOOS, regional Fed, Michigan, payrolls, FOMC docs, OECD CLI, dealer positioning). | `scripts/macro_survey.py` |
| [`commodity`](commodity/SKILL.md)       | Commodity spot prices and EIA weekly / STEO energy reports.                                                     | `scripts/commodity.py`    |

### ETF

| Skill                 | Purpose                                                            | Covers           |
| --------------------- | ------------------------------------------------------------------ | ---------------- |
| [`etf`](etf/SKILL.md) | ETF metadata, holdings list, and sector breakdown per fund ticker. | `scripts/etf.py` |

### Calendars & news

| Skill                             | Purpose                                                                    | Covers                 |
| --------------------------------- | -------------------------------------------------------------------------- | ---------------------- |
| [`calendars`](calendars/SKILL.md) | Earnings, ex-dividend, and economic-indicator calendars over a date range. | `scripts/calendars.py` |
| [`news`](news/SKILL.md)           | Company-specific or world / macro news articles with cited URLs.           | `scripts/news.py`      |

### Disclosure & ownership

| Skill                                     | Purpose                                                                | Covers                     |
| ----------------------------------------- | ---------------------------------------------------------------------- | -------------------------- |
| [`insider`](insider/SKILL.md)             | Form 4 insider-trading records per ticker.                             | `scripts/insider.py`       |
| [`institutional`](institutional/SKILL.md) | Aggregate 13F institutional-ownership statistics per ticker / quarter. | `scripts/institutional.py` |
| [`filings`](filings/SKILL.md)             | SEC EDGAR filings index per ticker, optionally filtered by form type.  | `scripts/filings.py`       |

### Options & shorts

| Skill                         | Purpose                                                                       | Covers               |
| ----------------------------- | ----------------------------------------------------------------------------- | -------------------- |
| [`options`](options/SKILL.md) | Options chain rows or per-expiration ATM IV / put-call OI summary per ticker. | `scripts/options.py` |
| [`shorts`](shorts/SKILL.md)   | FINRA short-interest aggregates and SEC fails-to-deliver records per ticker.  | `scripts/shorts.py`  |

## Composite Skills

Pure pipelines of `scripts/*.py` calls — no state files, no caches, no
side-effects. Each composite SKILL references the per-wrapper skills it wires
together.

| Skill                                                           | Purpose                                                                                                                                          | Covers                                                                                                                                |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| [`single-stock-snapshot`](single-stock-snapshot/SKILL.md)       | Free-tier single-equity baseline: quote → fundamentals overview → estimates consensus → news → insider → momentum (with optional ratios on FMP). | `scripts/quote.py`, `scripts/fundamentals.py`, `scripts/estimates.py`, `scripts/news.py`, `scripts/insider.py`, `scripts/momentum.py` |
| [`sector-rotation-snapshot`](sector-rotation-snapshot/SKILL.md) | Sector ETF universe scoring → momentum on top / bottom ranked sectors → single rotation summary.                                                 | `scripts/sector_score.py`, `scripts/momentum.py`                                                                                      |
| [`sector-stock-screener`](sector-stock-screener/SKILL.md)       | Sector ETF universe → top-N sector ranking → constituent expansion → per-stock momentum x value x quality x forward-consensus composite ranking. | `scripts/sector_stock_screener.py`, `scripts/sector_score.py`                                                                         |
| [`macro-snapshot`](macro-snapshot/SKILL.md)                     | Cross-asset macro read: macro_survey series → commodity weekly / price → Fama-French factor returns.                                             | `scripts/macro_survey.py`, `scripts/commodity.py`, `scripts/factors.py`                                                               |
