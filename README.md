# MarketDesk for AI Agents

A market-data desk that AI agents can sit at — a thin wrapper around the OpenBB Platform
that gives AI agents a uniform JSON-over-stdout interface for **equity quotes /
fundamentals / financials, news, earnings & economic calendars, SEC filings, insider
trading, 13F holdings, options, short interest, factor returns, commodity prices, and
structured macro surveys**.

## Scope

**In scope here**

- Per-ticker disclosure: quotes, historicals, fundamentals, estimates, news, calendars,
  SEC filings, insider (Form 4), institutional (13F), options chain / IV, short interest,
  fails-to-deliver
- Composite signals: sector ETF scoring (`sector_score.py`), momentum / technicals
  (`momentum.py`), Fama-French factor returns (`factors.py`)
- Macro: FRED-backed **structured surveys** (SLOOS, NY & TX Fed, Michigan, etc.) and
  energy (WTI / Brent / Henry Hub spot, EIA Weekly Petroleum Status, STEO)

## Skills for AI Agents

`skills/` ships **Agent Skills** — host-agnostic Markdown manuals
(not `.claude/`-specific) that tell any AI runtime how to call each wrapper and
read the JSON envelope. Full catalogue: [`skills/INDEX.md`](skills/INDEX.md).
Representative pipeline entry points:

- [`single-stock-snapshot`](skills/single-stock-snapshot/SKILL.md) — free-tier
  single-equity research pipeline (quote → fundamentals → estimates → news →
  insider → momentum).
- [`macro-snapshot`](skills/macro-snapshot/SKILL.md) — cross-asset macro read
  (surveys → commodity → factors).

## 1. Feature Matrix

Status legend:

- ✅ **Wrapper implemented** — runnable via `uv run scripts/<name>.py …`
- 📋 **Deferred** — viable provider exists but is paid-only; revisit when a key-free
  upstream becomes available

### 1-1. Feature List

Every ✅ row has been exercised end-to-end against its default provider. The README ↔
scripts bijection and the test markers below are machine-verified by
`tests/integration/test_verification_gate.py`.

| #   | Feature                                            | OpenBB route                                                                   | Default provider (key)                                                               | Status | Script                                                        |
| --- | -------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ | ------ | ------------------------------------------------------------- |
| 1   | Latest quote                                       | `obb.equity.price.quote`                                                       | yfinance (no key)                                                                    | ✅     | `scripts/quote.py`                                            |
| 2   | Historical prices                                  | `obb.equity.price.historical`                                                  | yfinance (no key)                                                                    | ✅     | `scripts/historical.py`                                       |
| 3   | Profile / key metrics                              | `obb.equity.profile` / `obb.equity.fundamental.metrics`                        | yfinance (no key)                                                                    | ✅     | `scripts/fundamentals.py --type overview\|metrics`            |
| 4   | Income / BS / CF                                   | `obb.equity.fundamental.{income,balance,cash}`                                 | yfinance (no key) ¹                                                                  | ✅     | `scripts/fundamentals.py --type income\|balance\|cash`        |
| 5   | Financial ratios                                   | `obb.equity.fundamental.ratios`                                                | fmp (`FMP_API_KEY`, free tier OK) ²                                                  | ✅     | `scripts/fundamentals.py --type ratios`                       |
| 6   | Analyst estimates / price targets                  | `obb.equity.estimates.consensus` / `price_target`                              | yfinance / finviz (no key)                                                           | ✅     | `scripts/estimates.py --type consensus\|price_target`         |
| 7   | Sector ETF composite score                         | `etf.price_performance` + `technical.clenow`                                   | finviz + yfinance (no key)                                                           | ✅     | `scripts/sector_score.py`                                     |
| 8   | Momentum / technicals                              | `obb.technical.{clenow,rsi,macd,cones,adx}`                                    | yfinance (no key)                                                                    | ✅     | `scripts/momentum.py`                                         |
| 8b  | Entry-timing scorer (two-axis basket analytics)    | `obb.equity.price.{quote,historical}` + `obb.technical.{clenow,rsi,macd}` + `obb.equity.calendar.earnings` | yfinance + nasdaq (no key) / fmp (`FMP_API_KEY`, opt-in)        | ✅     | `scripts/entry_timing_scorer.py`                              |
| 9   | Macro surveys (SLOOS / NY & TX Fed / Michigan / …) | `obb.economy.survey.*`                                                         | fred (`FRED_API_KEY`) / federal_reserve / oecd ³                                     | ✅     | `scripts/macro_survey.py`                                     |
| 10  | ETF info / holdings / sector breakdown             | `obb.etf.{info,holdings,sectors}`                                              | yfinance (info, no key) / fmp (holdings, sectors — paid) ⁴                           | ✅     | `scripts/etf.py --type info\|holdings\|sectors`               |
| 11  | Earnings / dividend / economic calendars           | `obb.equity.calendar.{earnings,dividend}` / `obb.economy.calendar`             | nasdaq (no key)                                                                      | ✅     | `scripts/calendars.py`                                        |
| 12  | Insider trading (Form 4)                           | `obb.equity.ownership.insider_trading`                                         | sec (`SEC_USER_AGENT`)                                                               | ✅     | `scripts/insider.py`                                          |
| 13  | Institutional holdings (13F)                       | `obb.equity.ownership.institutional`                                           | fmp (`FMP_API_KEY`, free tier OK) ⁵                                                  | ✅     | `scripts/institutional.py`                                    |
| 14  | SEC filings index                                  | `obb.equity.fundamental.filings`                                               | sec (`SEC_USER_AGENT`)                                                               | ✅     | `scripts/filings.py`                                          |
| 15  | Company / world news                               | `obb.news.company` / `obb.news.world`                                          | yfinance / fmp (free tier OK)                                                        | ✅     | `scripts/news.py --scope company\|world`                      |
| 16  | Options chain / IV surface                         | `obb.derivatives.options.chains`                                               | yfinance (no key) / cboe                                                             | ✅     | `scripts/options.py --type chain\|iv` ⁶                       |
| 17  | Fama-French factor returns                         | `obb.famafrench.factors`                                                       | famafrench (no key)                                                                  | ✅     | `scripts/factors.py`                                          |
| 18  | Commodity prices / weekly energy / STEO            | `obb.commodity.{price.spot,petroleum_status_report,short_term_energy_outlook}` | fred (price; `FRED_API_KEY`) / eia (weekly_report — keyless; steo — `EIA_API_KEY`) ⁷ | ✅     | `scripts/commodity.py --type price\|weekly_report\|steo`      |
| 19  | Short interest / fails-to-deliver                  | `obb.equity.shorts.{short_interest,fails_to_deliver}`                          | finra (no key) / sec (`SEC_USER_AGENT`)                                              | ✅     | `scripts/shorts.py --type short_interest\|fails_to_deliver` ⁸ |
| 20  | Earnings call transcripts                          | `obb.equity.fundamental.transcript`                                            | fmp (paid Starter+ only)                                                             | 📋     | —                                                             |

**Test evidence (sample)**: `tests/integration/test_json_contract.py::test_quote_yfinance_aapl_records`,
`::test_invalid_ticker_yields_json_error`, `::test_wrapper_envelope_has_data_results_list`,
`::test_wrapper_failure_rows_carry_three_error_fields`,
`tests/integration/test_fundamentals.py::test_fundamentals_single_symbol_happy_path`,
`tests/integration/test_macro_survey.py::test_macro_survey_series_happy_path`,
`tests/integration/test_sector_score.py::test_sector_score_universe_composite_integrity`,
`tests/integration/test_momentum.py::test_momentum_single_symbol_semantics`,
`tests/integration/test_options_iv.py::test_options_iv_yfinance_derives_per_expiration_rows`.
The full mapping is enforced by the verification gate above.

**Footnotes**

1. yfinance returns a subset of the fields fmp exposes for income / balance / cash
   (fewer line items on quarterly statements). Pass `--provider fmp` for a richer schema.
2. `obb.equity.fundamental.ratios` accepts only `fmp` / `intrinio`; no key-free alternative
   exists. Numeric ratio / decimal fields are tagged as `{value, unit}` with
   `unit ∈ {decimal, ratio}`; per-share and passthrough fields are raw scalars.
3. FRED-backed survey series require `FRED_API_KEY`. `--series fomc_documents` uses
   `federal_reserve` (no key); `--series cli` uses `oecd` (no key).
4. `--type holdings` and `--type sectors` require a **paid FMP subscription**
   (Starter+); the free 250-call/day key returns HTTP 402 _Restricted Endpoint_ on
   these two endpoints. `--type info` runs key-free via yfinance. `--type ratios`
   under `fundamentals.py` works on the free FMP tier.
5. **13F partial filing window**: per SEC 17 CFR §240.13f-1, holders have 45 calendar
   days after each quarter end to file. For any quarter where
   `quarter_end + 45 days > today`, upstream aggregation is incomplete and the snapshot
   is partial. Every record under `data.results[*].records[*]` carries
   `partial_filing_window: bool`; when `true`, the `*_change` fields reflect filing
   progress (more filers reporting), **not** real position drift, and must not be read
   as institutional buying / selling. Pass `--year` and `--quarter` for a quarter past
   the 45-day deadline to force a fully-aggregated snapshot.
6. `options.py --type unusual` is **deferred** — the only OpenBB provider
   (`intrinio`) is paid-only.
7. `commodity --type weekly_report` is **keyless in practice**: OpenBB's `eia`
   provider downloads the public Weekly Petroleum Status Excel file directly, so
   `EIA_API_KEY` is not consulted. Only `--type steo` (EIA v2 API) requires the key.
8. `shorts.py` ships `--type short_interest` (FINRA, key-free) and `--type
fails_to_deliver` (SEC, requires `SEC_USER_AGENT`). The third sub-mode
   `obb.equity.shorts.short_volume` (provider `stockgrid`) is omitted because the
   upstream returns an empty body that raises `JSONDecodeError` inside OpenBB.

### 1-2. Feature Discovery

Features beyond the table can be explored directly:

```bash
# Interactive REPL
uv run openbb

# Dump a namespace from Python
uv run python -c "from openbb import obb; print(dir(obb.equity))"
```

## 2. Setup

### 2-1. Install

```bash
uv sync
```

`pyproject.toml` pins `openbb>=4.4.0`, `openbb-cli>=1.1.0`, `python-dotenv>=1.0.0`.
OpenBB 4.x bundles the major providers (yfinance, fmp, sec, ecb, oecd, imf, etc.).

### 2-2. API Keys

```bash
cp .env.example .env
# edit .env; only the keys you actually use need values
```

| Key              | Required for                                                                                                                                                                                          | Free tier     | Where                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | ----------------------------------------------------- |
| `FMP_API_KEY`    | `fundamentals --type ratios`, `institutional` (default), `news --scope world` (default), and `--provider fmp` opt-ins. `etf --type holdings\|sectors` need paid Starter+ (free key returns HTTP 402). | 250 calls/day | https://site.financialmodelingprep.com/developer/docs |
| `SEC_USER_AGENT` | EDGAR access: `insider`, `filings`, `shorts --type fails_to_deliver`. Format: `"Name email"`.                                                                                                         | 10 req/sec    | n/a (your own info)                                   |
| `FRED_API_KEY`   | `macro_survey` FRED series (SLOOS, NY/TX Fed, Michigan, inflation_exp, chicago_conditions, nonfarm_payrolls) and `commodity --type price`.                                                            | 120 req/min   | https://fred.stlouisfed.org/docs/api/api_key.html     |
| `EIA_API_KEY`    | `commodity --type steo` only. `--type weekly_report` is keyless.                                                                                                                                      | 5 000 req/day | https://www.eia.gov/opendata/register.php             |

Yahoo Finance, Finviz, Nasdaq Data Link, OECD, Federal Reserve, FINRA, Fama-French, and
CBOE need no key. The default wrapper set runs end-to-end without `FMP_API_KEY`.

Polygon / Tiingo / Benzinga / Finnhub / Alpha Vantage / Intrinio are bundled with OpenBB
but have no `.env.example` slot today; add per §7 when needed.

### 2-3. How `.env` is loaded

`scripts/_env.py` auto-loads `.env` and maps values onto
`obb.user.credentials`. Every wrapper calls `apply_to_openbb()` on import, so users do
not need to `export` anything.

When calling OpenBB's Python API directly:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path("scripts")))
from _env import apply_to_openbb
from openbb import obb
apply_to_openbb()
```

## 3. Usage

### 3-1. Wrapper Scripts

```bash
# --- Prices ---
uv run scripts/quote.py AAPL MSFT 7203.T
uv run scripts/historical.py AAPL --start 2025-01-01 --end 2026-04-21

# --- Fundamentals / financials (numeric fields tagged {value, unit} where applicable) ---
uv run scripts/fundamentals.py AAPL --type metrics              # market cap, FCF, etc.
uv run scripts/fundamentals.py AAPL --type ratios --limit 5     # FMP free tier
uv run scripts/fundamentals.py AAPL --type income --period quarter --limit 8

# --- Estimates ---
uv run scripts/estimates.py AAPL MSFT --type consensus          # yfinance
uv run scripts/estimates.py AAPL --type price_target            # finviz revision history

# --- Sector ETF composite scoring ---
uv run scripts/sector_score.py --universe sector-spdr           # rank all 11 SPDR sectors
uv run scripts/sector_score.py --universe global-factor
uv run scripts/sector_score.py --tickers XLK,XLF,XLE --benchmark SPY

# --- Momentum / technicals ---
uv run scripts/momentum.py XLK XLF XLE --indicator clenow --period 90    # cross-sectional
uv run scripts/momentum.py AAPL --indicator rsi --length 14
uv run scripts/momentum.py AAPL --indicator macd
uv run scripts/momentum.py AAPL --indicator cones                        # vol cones

# --- Macro surveys (read data.unit_note before comparing series — units differ) ---
uv run scripts/macro_survey.py --series sloos --start 2020-01-01         # bank lending
uv run scripts/macro_survey.py --series ny_manufacturing                 # Empire State
uv run scripts/macro_survey.py --series michigan                         # consumer sentiment
uv run scripts/macro_survey.py --series fomc_documents                   # FOMC docs (free)
uv run scripts/macro_survey.py --series cli --start 2024-01-01           # OECD CLI

# --- ETFs ---
uv run scripts/etf.py SPY QQQ --type info                      # yfinance
uv run scripts/etf.py XLK --type holdings                      # paid FMP only (Starter+)
uv run scripts/etf.py XLK XLF --type sectors                   # paid FMP only (Starter+)

# --- Calendars ---
uv run scripts/calendars.py --type earnings --start 2026-04-23 --end 2026-05-07
uv run scripts/calendars.py --type economic --start 2026-04-23 --end 2026-04-30

# --- Ownership / filings ---
uv run scripts/insider.py AAPL MSFT --days 90 --provider sec          # Form 4 (client-side day window)
uv run scripts/institutional.py AAPL --provider fmp                   # 13F; check partial_filing_window
uv run scripts/institutional.py AAPL --year 2025 --quarter 1          # pin a fully-aggregated past quarter
uv run scripts/filings.py AAPL --form 10-K,10-Q,8-K --limit 20        # SEC index
uv run scripts/filings.py AAPL --form 13F --provider sec

# --- News ---
uv run scripts/news.py AAPL MSFT --scope company --days 7 --limit 20    # yfinance
uv run scripts/news.py --scope world --days 1 --limit 30 --provider fmp # FMP free 250/day

# --- Options (single symbol; multi-symbol disabled to bound chain blow-up) ---
uv run scripts/options.py AAPL --type chain --provider yfinance
uv run scripts/options.py AAPL --type chain --expiration 2026-05-15
uv run scripts/options.py AAPL --type iv --provider yfinance            # ATM IV + OI-weighted P/C ratio

# --- Fama-French factors ---
uv run scripts/factors.py                                                # defaults: america + monthly + 5_factors
uv run scripts/factors.py --region japan --frequency monthly --factor 3_factors --start 2024-01-01

# --- Commodities / energy ---
uv run scripts/commodity.py --symbol wti --start 2025-01-01 --type price        # FRED spot (FRED_API_KEY)
uv run scripts/commodity.py --type weekly_report --start 2025-04-01             # EIA Weekly Petroleum (keyless)
uv run scripts/commodity.py --type steo                                         # EIA STEO (EIA_API_KEY)

# --- Short interest / FTD ---
uv run scripts/shorts.py AAPL MSFT --type short_interest --provider finra       # FINRA days_to_cover (no key)
uv run scripts/shorts.py AAPL --type fails_to_deliver --provider sec            # SEC FTD (SEC_USER_AGENT)
```

All scripts emit JSON to **stdout**. Capture with `> out.json`. Treat **stderr as plain
log output** (upstream libraries occasionally print HTTP errors there); do not parse it
as JSON or merge it with stdout.

### 3-2. Stdout Envelope

Every wrapper emits the same envelope shape. **Branch on top-level `error` and exit code
first; only then dereference `data`.**

**Success / partial failure (exit 0)**

```json
{
  "source": "openbb",
  "collected_at": "<ISO-8601>",
  "tool": "<file stem of scripts/<tool>.py>",
  "data": {
    "results": [
      /* list[dict] — may be empty, never absent on this path */
    ]
    /* per-wrapper query echo / meta as siblings of `results`:
       type, series, provider, start, end, tickers, weights, benchmark, universe,
       missing_tickers, defaults_applied, unit_note, … */
  },
  "warnings": [
    /* present iff at least one row failed */
  ]
}
```

**Fatal failure: every input failed on `credential` or `plan_insufficient` (exit 2)**

```json
{
  "error": "CredentialError: Missing credential 'fred_api_key'. …",
  "collected_at": "<ISO-8601>",
  "tool": "<stem>",
  "error_category": "credential" | "plan_insufficient",
  "details": [ /* optional, per failed input */ ]
}
```

The `data` key is **absent** on the fatal-failure path. Argparse / input-validation
rejection also exits 2 but writes its message to stderr (no envelope).

**Invariants (enforced by `tests/integration/test_json_contract.py`)**

| #   | Invariant                                                                                                                                                                                    |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | On exit 0, `payload["data"]["results"]` is `list[dict]` (possibly empty). On exit 2 (fatal), `data` is absent and a top-level `error` string is present.                                     |
| 2   | Per-row failure carries exactly `{ok: false, error, error_type, error_category}` with `error_category ∈ {credential, plan_insufficient, transient, validation, other}`.                      |
| 3   | Partial failures aggregate to top-level `warnings[]` (`{symbol, error, error_category}`); `warnings` is omitted when empty.                                                                  |
| 4   | Exit 0 covers full success and partial failure. Exit 2 covers (a) all inputs failed on `credential` or `plan_insufficient`, (b) argparse / input-validation rejection.                       |
| 5   | `payload["tool"]` equals the file stem of the script.                                                                                                                                        |
| 6   | Envelope root is exactly `{source, collected_at, tool, data}` plus optional `{warnings, error, error_category, details}`. Per-wrapper meta (e.g., `provider`, `series`) lives under `data/`. |

**Error-category recovery**

- `credential` (`CredentialError:` prefix) → fix by setting / rotating the API key
- `plan_insufficient` (`PlanError:` prefix, e.g., FMP free key on `etf --type holdings`)
  → fix by upgrading the subscription
- `transient` → retry
- `validation` → fix the call site
- `other` → log and inspect

**`sector_score.py` scoring details**

- Pulls 3m / 6m / 12m price momentum (Finviz) and Clenow 90d / 180d (yfinance + `obb.technical.clenow`)
- z-scores each signal → weighted composite → maps to 0–100
- Default weights: Clenow90 25 %, Clenow180 25 %, 6m 20 %, 3m 15 %, 12m 10 %, risk-adj 5 %
- `composite_score_0_100` is `null` when too many input signals are missing; `null` is
  preserved on the row (not coerced to 0) so callers can distinguish "missing inputs"
  from "zero score". Provider-stage failures live under `data.provider_diagnostics[]`;
  `warnings[]` carries per-row failures only.

### 3-3. Python API (Arbitrary Queries)

For features without a wrapper:

```bash
uv run python - <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path("scripts")))
from _env import apply_to_openbb
from openbb import obb
apply_to_openbb()

result = obb.equity.price.performance(
    symbol="XLK,XLF,XLE,XLV,XLI,XLP,XLY,XLU,XLB,XLRE,XLC",
    provider="finviz",
)
print(result.to_df().to_json(orient="records"))
PY
```

### 3-4. Interactive REPL

```bash
uv run openbb
```

Supports calls like `/equity/price/historical --symbol AAPL --provider yfinance`.
Intended for manual exploration; **agent-scheduled tasks should use 3-1 or 3-3.**

## 4. Agent Invocation

```bash
uv run scripts/quote.py AAPL MSFT > /tmp/quote.json
```

## 5. Provider / Key Matrix

Every wrapper defaults to a no-key or free-tier-key provider. Paid providers are reachable
only via explicit `--provider`, never as a silent default.

| Provider         | Key                                                           | Default for                                                                                                                                                                             |
| ---------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Yahoo Finance    | none                                                          | `quote`, `historical`, `fundamentals` (overview/metrics/income/balance/cash), `estimates --type consensus`, `momentum`, `etf --type info`, `news --scope company`, `options`            |
| Finviz           | none                                                          | `estimates --type price_target`, `sector_score` (price_performance)                                                                                                                     |
| Nasdaq Data Link | none                                                          | `calendars` (earnings / dividend / economic)                                                                                                                                            |
| SEC EDGAR        | `SEC_USER_AGENT`                                              | `insider`, `filings`, `shorts --type fails_to_deliver`                                                                                                                                  |
| FMP              | `FMP_API_KEY` (free 250/day)                                  | `fundamentals --type ratios`, `institutional`, `news --scope world`. `etf --type holdings\|sectors` need **paid** Starter+; free key returns 402 → `error_category: plan_insufficient`. |
| FRED             | `FRED_API_KEY` (120 req/min)                                  | `macro_survey` FRED series; `commodity --type price`                                                                                                                                    |
| OECD             | none                                                          | `macro_survey --series cli`                                                                                                                                                             |
| Federal Reserve  | none                                                          | `macro_survey --series fomc_documents` / `--series dealer_positioning`                                                                                                                  |
| EIA              | `EIA_API_KEY` (5 000/day) for STEO; keyless for weekly_report | `commodity --type steo` (gated) / `--type weekly_report` (keyless)                                                                                                                      |
| FINRA            | none                                                          | `shorts --type short_interest`                                                                                                                                                          |
| Fama-French      | none                                                          | `factors`                                                                                                                                                                               |
| CBOE             | none                                                          | `options --provider cboe` (opt-in)                                                                                                                                                      |

## 6. Testing

The test suite lives under `tests/`:

- **Unit** (`tests/unit/`) — pure helpers in `scripts/_common.py`, `scripts/_env.py`, the
  `sector_score.py` aggregator, and the integration sanity helpers. Offline; no network,
  no credentials.
- **Integration** (`tests/integration/`) — runs each wrapper as a CLI subprocess against
  its default free-tier provider, asserting the JSON envelope, exit codes, and per-payload
  sanity (finite numbers, ascending dates, multi-symbol integrity). Cases gated on a paid
  credential auto-skip when the key is absent.

`pytest` is in an opt-in dependency group:

```bash
uv sync --group dev               # install once
uv run pytest -m unit             # offline, fast
uv run pytest -m integration      # real provider calls; on-demand only (~10 min)
uv run pytest                     # full
```

The `-m integration` tier hits real provider APIs — many cases take tens of seconds. Do
not loop on it.

**Audit trail.** README ↔ scripts ↔ test bijection is enforced by
`tests/integration/test_verification_gate.py`. The cross-wrapper envelope contract is
enforced by `tests/integration/test_json_contract.py`. Documentation drift surfaces as a
red test, not as stale prose.

**Contributor rule.** When adding a wrapper under `scripts/`, add at least one
integration test exercising `--help` (auto-covered by the `--help` parametrization in
`tests/integration/test_cli_help.py`) and one happy-path invocation against a key-free
provider.

## 7. Extension Notes

- **New provider** (Polygon / Tiingo / Benzinga / Finnhub / …): OpenBB already bundles
  these, so no `pip` / `uv` change is needed:
  1. Add a slot to `.env.example`
  2. Set the key in your local `.env`
  3. Add `{env var: credentials attribute}` to `_CREDENTIAL_MAP` in `scripts/_env.py`
     (find the attribute name with
     `uv run python -c "from openbb import obb; print(sorted(obb.user.credentials.__dict__))"`)
- **New wrapper script**: drop it in `scripts/` following the pattern in any existing
  script (`from _env import apply_to_openbb` → `argparse` → JSON to stdout). Avoid
  filenames that collide with stdlib modules (e.g., `calendar.py`) — shadowing can break
  OpenBB imports.
- **Version bump**: `uv lock --upgrade-package openbb`.

**New-wrapper contract checklist** (machine-verified by `test_json_contract.py`, which
auto-discovers wrappers from `scripts/*.py` via `WRAPPERS` in
`tests/integration/conftest.py`):

1. On exit 0, emits `data.results: list[dict]`.
2. Per-row failures carry `{ok: false, error, error_type, error_category}` with
   `error_category` drawn from `_common.ErrorCategory`. Multi-symbol wrappers route
   through `_common.aggregate_emit`; single-query wrappers through `_common.single_emit`.
3. Partial failures surface on top-level `warnings[]` (not silently dropped, not inlined
   into `data`).
4. Exit code 2 is reserved for (a) all inputs failed on `credential` or
   `plan_insufficient`, (b) argparse / input-validation rejection.
5. `payload["tool"]` equals the file stem.

## 8. License

OpenBB Platform / OpenBB CLI are AGPL-3.0. Local use to support personal investment
decisions, as in this project, has no constraint.
