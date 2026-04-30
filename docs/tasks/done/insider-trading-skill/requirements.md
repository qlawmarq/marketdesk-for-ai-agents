# Requirements Document — insider-trading-skill

## Project Description (Input)

起票日: 2026-04-30
起票者: investment-analyst
承認: reviewer 2026-04-30 verdict agree、ユーザー承認待ち

### 目的

ユーザー保有・watchlist 銘柄、および multibagger / Piotroski 候補に対し、SEC Form 4（insider trading）データを統一 envelope で取得し、提案レポートのエビデンスとして組み込む。SEC EDGAR 直接 fetch + Python パースの手間を削減し、analyst の業務効率を向上させる。

### 入力

- `ticker`（複数可、space-separated）
- `--days N`（直近 N 日、デフォルト 90）
- `--transaction-codes`（オプション、デフォルト無フィルタ。Form 4 単一文字コードの CSV、例: `P,S` = 公開市場買い + 公開市場売り）
- `--format {json,md}`（デフォルト `json`、`md` で markdown 表）
- `--provider {sec,fmp,intrinio,tmx}`（デフォルト `sec`、key-free）
- `--limit N`（プロバイダ側 row cap、optional）

### 配置先

- `shared/openbb/skills/insider/SKILL.md`（既存、本スペックで更新）
- `shared/openbb/scripts/insider.py`（既存、本スペックで `--transaction-codes` / `--format md` / 正規化スキーマを追加）

### 言語・基盤

Python 3.12（uv）。既存 `shared/openbb` スタック準拠（`obb.equity.ownership.insider_trading`）。

### MVP 範囲

1. `uv run scripts/insider.py <TICKER> --days 90`: 直近 90 日 Form 4 一覧（既存）
2. 複数 ticker 一括: `uv run scripts/insider.py ASC CMCL FLXS --days 60`（既存）
3. `--transaction-codes P` で公開市場買いのみフィルタ（新規、provider 不変の正規化済みコードに対するフィルタ）
4. `--format md` で markdown 表出力（新規）
5. FMP プロバイダ経由（`--provider fmp`）でも同一の正規化スキーマを返す（新規・有料プラン要）

### 将来拡張（MVP 外、別タスク化）

- インサイダー連続買い銘柄スクリーニング（cluster buying 検出）
- F-Score / multibagger スクリーニング結果との統合
- 役職別集計（CEO / CFO / Director）
- 買い／売り比率の時系列指標化

### 必要コスト

- 既存 SEC 経路は keyless（`SEC_USER_AGENT` のみ要）。
- `--provider fmp` を使う場合のみ FMP 有料プラン（Starter 以上、ユーザー契約済）。

### 代替案・代替評価

- **SEC EDGAR 直接 fetch（curl + Python パース）**: Form 4 の Transaction Code 解釈・株数集計を毎回手作業で実装する必要があり、analyst の業務効率を低下させる。`obb.equity.ownership.insider_trading` の SEC プロバイダ経由なら統一 envelope で取得可能。

## Introduction

The insider-trading skill is the agent-facing surface of `scripts/insider.py`, a thin wrapper over `obb.equity.ownership.insider_trading` that emits Form 4 transaction records under the shared `_envelope` JSON contract. The wrapper already exists in MVP form (multi-symbol fetch, `--days` client-side filter, `--limit` row cap, `--provider {sec,fmp,intrinio,tmx}` switch, `safe_call`-guarded errors) and is documented at `skills/insider/SKILL.md`. This specification closes the gap between that MVP and the analyst's daily-report needs by adding two CLI flags — `--transaction-codes` for code-level filtering (P/S/A/F/M/G/D/W) and `--format md` for a markdown-table output — and by introducing a single **normalized record schema** so the FMP and SEC paths return the same field names with the same semantics. No new OpenBB endpoints are introduced.

### Live findings driving the design (verified 2026-04-30)

A live API pass against `obb.equity.ownership.insider_trading` on AAPL / FLXS / ASC / CMCL / BAC / JPM / C / MS revealed three facts that block the original "provider-native passthrough" approach and force a normalized schema:

1. **`transaction_type` shape diverges by provider.** The SEC provider returns long English descriptions ("Open market or private sale of non-derivative or derivative security", "Grant, award or other acquisition pursuant to Rule 16b-3(d)"). The FMP provider returns prefix-coded strings (`"S-Sale"`, `"A-Award"`, `"M-Exempt"`, `"F-InKind"`, `"G-Gift"`, `"D-Return"`, `"W-Will"`). Neither shape is the single-letter Form 4 code that the analyst's `--transaction-codes P` filter needs.
2. **Provider-native field names diverge for several keys.** SEC emits `form` / `filing_url` / `owner_name` / `owner_title` (plain) / `securities_transacted` / `transaction_price` / `securities_owned`; FMP emits `form_type` / `url` / `owner_name` / `owner_title` (with `"officer: "` prefix) / same numerics, plus `ownership_type` as a single letter `"D"` instead of the word `"Direct"`. A passthrough wrapper forces the analyst to learn two schemas.
3. **Neither provider exposes `total_value`.** It must be computed (`shares × price`) inside the wrapper if the analyst is to read it directly from the envelope.

The wrapper therefore ships a single canonical schema (Requirement 6) populated by per-provider normalization functions. The provider-native `transaction_type` value is preserved verbatim under `transaction_type_raw` so audit trails are intact, and the derived single-letter code becomes the only field the `--transaction-codes` filter looks at — making the filter behave identically across providers.

The tool is designed for analyst-driven proposal evidence: an agent passes a small basket of tickers (typically holdings + watchlist + screening candidates, 1–10 names), reads the JSON envelope into proposal drafts, or pastes the markdown table directly into a draft report. Aggregation, screening (cluster buying), and role-level rollups are deliberately out of scope and are tracked as future tasks in the input description.

## Requirements

### Requirement 1: CLI input and ticker resolution

**Objective:** As an AI analyst agent, I want to pass one or more tickers as positional arguments and a day window, so that I can invoke the wrapper uniformly from daily holdings monitoring and ad-hoc watchlist checks.

#### Acceptance Criteria

1. The insider-trading wrapper shall accept one or more ticker symbols as positional arguments via argparse `nargs="+"`.
2. If zero positional ticker arguments are supplied, the insider-trading wrapper shall exit with `error_category: validation` and a message identifying the missing argument, before issuing any OpenBB call.
3. When the user passes `--days <N>`, the insider-trading wrapper shall accept a positive integer and apply it as the client-side window described in Requirement 3; the default value shall be `90`.
4. If `--days` is non-integer, zero, or negative, the insider-trading wrapper shall exit with `error_category: validation` before issuing any OpenBB call.
5. When the user passes `--limit <N>`, the insider-trading wrapper shall forward the integer verbatim as the upstream provider row cap; when omitted, the wrapper shall not pass a `limit` keyword to the OpenBB call so the provider's own default applies.
6. If a ticker symbol contains the `.T` suffix or any other provider-recognized suffix, the insider-trading wrapper shall forward it unchanged to every OpenBB call.

### Requirement 2: Provider selection and key-free default

**Objective:** As an operator running in CI and local environments, I want the wrapper to default to a key-free provider, so that routine runs do not require paid-plan credentials and FMP stays opt-in.

#### Acceptance Criteria

1. When `--provider` is omitted, the insider-trading wrapper shall route the OpenBB call through `sec` (which requires only the `SEC_USER_AGENT` environment variable already documented in `_providers`).
2. The insider-trading wrapper shall expose `--provider` as a closed argparse choice over `{sec, fmp, intrinio, tmx}`, matching the OpenBB-supported set already encoded in `scripts/insider.py::PROVIDER_CHOICES`.
3. When `--provider fmp` is selected and `FMP_API_KEY` is missing or invalid, the insider-trading wrapper shall surface the failure with `error_category: credential` per `_errors`, exit code `2` when every input ticker fails with this category, and exit `0` otherwise (delegating to `aggregate_emit`).
4. When `--provider fmp` is selected against a free-tier FMP key that lacks insider-trading entitlement, the insider-trading wrapper shall surface the failure with `error_category: plan_insufficient` per `_errors` rather than silently dropping rows.
5. The insider-trading wrapper shall not promote `fmp` (or any paid provider) to the default; doing so violates `docs/steering/structure.md` "default to a key-free provider" rule.
6. The insider-trading wrapper shall apply the same normalized output schema (Requirement 6) under every `--provider` choice; the schema is provider-invariant by construction so consumers do not need to branch on `data.provider`.

### Requirement 3: Client-side day-window filter

**Objective:** As an AI analyst agent, I want a deterministic recent-N-days window on transaction date, so that the response is bounded by event time even though the upstream endpoint is row-limit-driven, not date-window-driven.

#### Acceptance Criteria

1. After a successful OpenBB call, the insider-trading wrapper shall keep only those records whose normalized `transaction_date` (or normalized `filing_date` when `transaction_date` is missing or unparseable) is on or after `TODAY - days`.
2. The insider-trading wrapper shall compute `TODAY` from the local machine clock at the start of `main()` so the window is reproducible from `collected_at` in the envelope.
3. If a record carries neither a parseable `transaction_date` nor a parseable `filing_date`, the insider-trading wrapper shall keep the record (matching the existing `_filter_by_days` behavior) and shall not silently drop it.
4. The insider-trading wrapper shall apply the day-window filter regardless of `--provider`, so callers see consistent recency semantics across `sec`, `fmp`, `intrinio`, and `tmx`.
5. The insider-trading wrapper shall apply the day-window filter **before** the transaction-code filter (Requirement 4), so the `dropped_unparseable_codes` count emitted under `data` reflects only the in-window rows that the code filter rejected.

### Requirement 4: Transaction-code filter on the normalized code

**Objective:** As an AI analyst agent assessing buy-side conviction, I want one CLI flag that filters Form 4 records to specific transaction codes (open-market purchase, sale, etc.) and behaves identically across providers, so that "insider buying only" is a one-flag query irrespective of `--provider`.

#### Acceptance Criteria

1. The insider-trading wrapper shall expose `--transaction-codes <CSV>` accepting a comma-separated list of one-letter Form 4 transaction codes (for example `P`, `P,S`, `P,S,G,W,D`).
2. When `--transaction-codes` is omitted, the insider-trading wrapper shall return all in-window records irrespective of code (no filter applied).
3. When `--transaction-codes` is supplied, the insider-trading wrapper shall keep only records whose **normalized** `transaction_code` (Requirement 6.3) matches one of the supplied codes (case-insensitive).
4. When `--transaction-codes` is supplied, the insider-trading wrapper shall drop rows whose normalized `transaction_code` is `null` (the upstream `transaction_type` was empty, was a SEC English description that did not match the keyword-to-code lookup table, or was an FMP value not in the `X-Word` shape) and shall report the per-ticker dropped count under that ticker's row meta as `dropped_unparseable_codes` (integer ≥ 0). When the filter is omitted, this field shall be `0` regardless of how many rows had `null` codes.
5. The insider-trading wrapper shall validate every supplied code matches the regex `^[A-Za-z]$` and shall exit with `error_category: validation` if any code is empty, multi-character, or non-alphabetic, before issuing any OpenBB call.
6. The insider-trading wrapper shall echo the active filter under `data` as `transaction_codes` (a list of uppercase letters, or `null` when the filter is inactive) so consumers can audit the active configuration directly from the envelope.
7. The insider-trading wrapper shall not introduce a separate `--side {buy,sell,all}` flag; "buy only" is expressed as `--transaction-codes P`, "sell only" as `--transaction-codes S`, and the SKILL.md (Requirement 8) shall document the typical code combinations rather than splitting the surface into two flags.

### Requirement 5: Output formats — JSON envelope and markdown table

**Objective:** As an AI analyst agent that switches between programmatic ingestion and human-readable proposal drafts, I want one CLI to emit either the shared JSON envelope or a markdown table, so that the same evidence is reusable across both paths without external formatters.

#### Acceptance Criteria

1. The insider-trading wrapper shall expose `--format` as a closed argparse choice over `{json, md}` defaulting to `json`.
2. When `--format json` is active (or `--format` omitted), the insider-trading wrapper shall emit stdout via `_common.aggregate_emit` with `tool="insider"` so the output complies with the shared envelope contract enforced by `tests/integration/test_json_contract.py`.
3. When `--format md` is active, the insider-trading wrapper shall emit a single markdown document on stdout containing one section per input ticker, with a level-2 heading `## <SYMBOL>` and a markdown table of that symbol's normalized records; the markdown shall include no JSON envelope wrapping.
4. When `--format md` is active and a per-ticker fetch failed (`ok: false`), the insider-trading wrapper shall render that ticker's section as the heading plus a single line `_error_category_: <category> — <error>` (no table), so the failure is visible inline rather than silently omitted.
5. When `--format md` is active and a per-ticker fetch returned zero records (whether due to an empty provider response, the `--days` filter, or the `--transaction-codes` filter), the insider-trading wrapper shall render that ticker's section as the heading plus a single line `_no records in window_`.
6. The insider-trading wrapper shall draw markdown-table columns from the closed normalized-schema set `{filing_date, transaction_date, reporter_name, reporter_title, transaction_code, transaction_code_label, shares, price, total_value, shares_after, url}`; columns whose value is `None` for a given row shall render as an empty cell, not the literal string `"None"`. The column set is provider-invariant because the schema is normalized (Requirement 6).
7. The insider-trading wrapper shall escape pipe (`|`) and newline characters in cell values so the markdown-table structure cannot be broken by free-text fields (for example a footnote or a long English transaction description).
8. When `--format md` is active and any input fails fatally on the `aggregate_emit` policy (every input failed with the same `credential` or `plan_insufficient` category), the insider-trading wrapper shall fall back to JSON-envelope emission for that fatal exit (so exit code 2 still carries machine-readable `error` / `error_category`); markdown is not emitted on fatal exits.
9. The insider-trading wrapper shall emit a single trailing newline at end-of-output for both formats so downstream pagers and pipe consumers behave predictably.

### Requirement 6: Normalized record schema (provider-invariant)

**Objective:** As an AI analyst agent reading the JSON programmatically, I want every record to carry the same field names with the same semantics regardless of `--provider`, so that I can write one parser and one proposal-draft template instead of branching on `data.provider`.

#### Acceptance Criteria

1. The insider-trading wrapper shall place per-ticker rows under `data.results[]` using `aggregate_emit`, where each row has shape `{symbol, provider, ok, records|error, error_type?, error_category?, dropped_unparseable_codes}` per Requirement 7.
2. On success, the insider-trading wrapper shall populate `records[]` where each record carries the following normalized fields, all named identically and with the same semantics across `--provider sec` and `--provider fmp`:
   - `filing_date` (ISO date string or `null`)
   - `transaction_date` (ISO date string or `null`)
   - `reporter_name` (string or `null`; sourced from `owner_name`)
   - `reporter_title` (string or `null`; sourced from `owner_title`, with the FMP-only `"officer: "` / `"director: "` / `"ten_percent_owner: "` prefixes stripped so the value is the bare title)
   - `transaction_code` (single uppercase letter `A`/`P`/`S`/`F`/`M`/`G`/`D`/`W`/etc., or `null` when unparseable)
   - `transaction_code_label` (human-readable label such as `"Open Market Purchase"`, `"Sale"`, `"Award"`, `"Exempt Exercise"`, `"In-Kind Tax Withholding"`, `"Gift"`, `"Return to Issuer"`, `"Will or Inheritance"`, or `null` when `transaction_code` is `null`)
   - `transaction_type_raw` (the upstream provider-native `transaction_type` value verbatim — long English on `sec`, `"X-Word"` on `fmp` — preserved for audit so the normalization is replayable)
   - `acquisition_or_disposition` (single uppercase letter `"A"` or `"D"`, or `null`; FMP returns it in this shape natively, SEC's `"Acquisition"` / `"Disposition"` is normalized to the first letter)
   - `shares` (number or `null`; sourced from `securities_transacted`)
   - `price` (number or `null`; sourced from `transaction_price`)
   - `total_value` (number or `null`; computed as `shares * price` when both are non-null and `price > 0`, else `null`)
   - `shares_after` (number or `null`; sourced from `securities_owned`)
   - `form_type` (string or `null`; sourced from `form` on `sec`, `form_type` on `fmp`)
   - `url` (string or `null`; sourced from `filing_url` on `sec`, `url` on `fmp`)
   - `ownership_type` (string `"Direct"` or `"Indirect"` or `null`; FMP's `"D"` / `"I"` is expanded to the long form so the field is provider-invariant)
   - `security_type` (string or `null`)
   - `company_cik` (zero-padded string or `null`)
   - `owner_cik` (zero-padded string or `null`)
   - `footnote` (string or `null`; populated by `sec`, `null` on `fmp` which does not expose footnotes)
3. The insider-trading wrapper shall derive `transaction_code` per provider as follows:
   - **`fmp` path**: extract the leading letter of `transaction_type` when the value matches the regex `^[A-Z]-`; otherwise (empty string, missing key, unrecognized shape) emit `null`.
   - **`sec` path**: look the upstream `transaction_type` long-English description up in a provider-internal keyword-to-code table aligned with the official Form 4 code set (`P` Open Market Purchase, `S` Open Market Sale, `A` Grant/Award, `F` Payment of exercise price or tax liability by withholding, `M` Exercise or conversion of derivative security, `G` Bona fide gift, `D` Disposition to issuer, `W` Transfer pursuant to will or laws of descent, plus any additional codes the live SEC sample reveals during research); a description that does not match any keyword shall emit `null`.
   - **`intrinio` / `tmx` paths**: the wrapper shall treat the upstream `transaction_type` shape as undefined and emit `null` for `transaction_code` until live-verification adds explicit support; the `--transaction-codes` filter therefore drops every record on these providers (Requirement 4.4) and `dropped_unparseable_codes` reports the count.
4. The insider-trading wrapper shall echo per-query metadata as siblings of `results` under `data`: `provider`, `days`, `transaction_codes` (Requirement 4.6), and `limit` (only when supplied), so consumers can replay the exact request from the envelope alone.
5. When a per-ticker row carries `ok: false`, the insider-trading wrapper shall omit `records` and `dropped_unparseable_codes` from that row while still populating `symbol`, `provider`, `error`, `error_type`, and `error_category`, and shall mirror the failure into top-level `warnings[]` via `aggregate_emit`.
6. The insider-trading wrapper shall sanitize NaN / Infinity floats to `null` via `_common.emit` / `sanitize_for_json` so downstream parsers (jq, Node, Go) remain strict-JSON compliant.
7. The insider-trading wrapper shall not introduce a `"source"` constant of `"fmp"` (as the input description's first sketch suggested); per `_envelope`, the envelope-level `source` is fixed at `"marketdesk-for-ai-agents"` and the active provider is reported under `data.provider`.
8. The insider-trading wrapper shall implement the normalization as private functions inside `scripts/insider.py` (no new module file) and shall keep the per-provider branch to a single `if provider == "sec": ... else: ...` site so the call-site wiring stays linear and reviewable.

### Requirement 7: Error handling and exit-code contract

**Objective:** As an agent integrating the wrapper into an automated pipeline, I want the failure surface to be the closed five-value `error_category` taxonomy and the standard exit-code contract, so that retry / fallback / skip / stop policy is dispatchable from the envelope alone.

#### Acceptance Criteria

1. The insider-trading wrapper shall guard every OpenBB call with `_common.safe_call` so stdout-borne provider warnings are absorbed and failures become structured `{ok:false, error, error_type, error_category}` records.
2. The insider-trading wrapper shall surface only `error_category` values drawn from the closed `ErrorCategory` enum: `{credential, plan_insufficient, transient, validation, other}` per `_errors`.
3. When `SEC_USER_AGENT` is unset on the default `--provider sec` path, the insider-trading wrapper shall surface the upstream rejection with `error_category: credential` and shall not retry.
4. When the upstream response is empty for a valid ticker / window (no insider activity), the insider-trading wrapper shall return `ok: true` with `records: []` (not a row-level `ok: false`) so consumers can distinguish "no activity" from "fetch failed".
5. The insider-trading wrapper shall exit with code `0` on full success and on any partial-failure mix, and with code `2` only when every input ticker fails with the same fatal category (`credential` or `plan_insufficient`), or when argparse / input validation rejects the invocation.
6. The insider-trading wrapper shall keep stdout a single document (envelope JSON or markdown per `--format`) and send any traceback to stderr, matching the cross-wrapper invariant enforced by `tests/integration/test_json_contract.py`.

### Requirement 8: Skill documentation

**Objective:** As an AI agent reading the host-agnostic skill manual, I want `skills/insider/SKILL.md` to document the new flags and the single normalized record schema, so that I can invoke the wrapper correctly without reading wrapper source and without learning two provider schemas.

#### Acceptance Criteria

1. The insider-trading skill shall update `skills/insider/SKILL.md` to document `--transaction-codes`, `--format`, and the markdown-table column set (Requirement 5.6).
2. The insider-trading skill shall replace the existing per-provider record-key paragraph with a **single "Normalized record schema" section** listing the field set from Requirement 6.2 once, and shall document the Form 4 transaction-code letters (`P`/`S`/`A`/`F`/`M`/`G`/`D`/`W`) with their `transaction_code_label` mapping plus typical analyst usage (e.g., `--transaction-codes P` for insider-buying conviction, `--transaction-codes P,S` for discretionary trades excluding awards/withholding/exempt-exercises).
3. The insider-trading skill shall keep `skills/insider/SKILL.md` within the AUTHORING budget (~30–80 lines, English only, at most one short output sample) per `docs/steering/structure.md` § Agent Skills.
4. The insider-trading skill shall update `skills/INDEX.md` only if the per-wrapper INDEX row needs new flag mentions; the wrapper folder `skills/insider/` already exists and shall not be re-registered.
5. The insider-trading skill shall not introduce a test file under `skills/`; per the user feedback in memory and `skills/AUTHORING.md`, SKILL.md is documentation, not tests.
6. The insider-trading skill shall write `skills/insider/SKILL.md` from observed live-run output (Requirement 9 covers the live runs that produce the sample) and shall not embed test-fixture data.

### Requirement 9: Integration-test coverage

**Objective:** As the JSON-contract and per-wrapper integration suites, I want the wrapper to pass `tests/integration/test_json_contract.py` and to grow targeted assertions for the new flags and the normalized schema, so that envelope regressions, code-filter regressions, markdown-format regressions, and cross-provider schema drift are caught automatically on the next `uv run pytest -m integration`.

#### Acceptance Criteria

1. The insider-trading wrapper shall pass `tests/integration/test_json_contract.py` unmodified under `--provider sec` (the keyless default), so the cross-wrapper envelope contract continues to apply automatically.
2. The integration suite shall cover, under `--provider sec`, at minimum: a happy-path multi-symbol fetch with `--days`, a `--transaction-codes` filter that returns a non-empty subset for at least one ticker known to have insider activity in the window, an invalid `--days 0` rejection that exits 2 with `error_category: validation`, and an invalid `--transaction-codes "PP"` rejection that exits 2 with `error_category: validation`.
3. The integration suite shall cover, under `--provider fmp`, at minimum: a happy-path single-symbol fetch that returns the envelope and a `--format md` invocation that returns a non-empty markdown document; both tests shall be skip-gated on `FMP_API_KEY` per the existing per-wrapper convention so the `-m integration` run stays green when the key is absent.
4. The integration suite shall include a **cross-provider schema-consistency test** that runs the same single-symbol query (a ticker known to have records on both providers, e.g. `AAPL`) under `--provider sec` and `--provider fmp`, then asserts the normalized field set in Requirement 6.2 is present (as keys) on every record from both providers and that `transaction_code`, when non-null, is a single uppercase letter on both. The FMP half of this test shall be skip-gated on `FMP_API_KEY`.
5. The integration suite shall assert markdown-format invariants under `--format md`: stdout is not valid JSON, contains a `## <SYMBOL>` heading per input ticker, and contains either a markdown table, the `_error_category_:` line, or the `_no records in window_` line for each ticker.
6. The integration suite shall assert that `data.transaction_codes` echoes the active filter (uppercased list, or `null` when omitted) and that per-row `dropped_unparseable_codes` is `0` when no filter is active.
7. The integration suite shall not introduce mocked-OpenBB-provider unit tests for the wrapper; per `docs/steering/tech.md`, the OpenBB-call layer is integration-only. The pure normalization functions (SEC keyword-to-code lookup, FMP leading-letter extraction, `total_value` computation, `"officer: "` prefix stripping) are deterministic and stdlib-only and **may** be covered by `tests/unit/` cases on representative input strings.

### Requirement 10: Scope boundaries — what the tool must not do

**Objective:** As a reviewer enforcing the thin-wrapper architecture in `docs/steering/structure.md`, I want scope drift explicitly forbidden, so that screening, role-level aggregation, and notification concerns stay out of this wrapper.

#### Acceptance Criteria

1. The insider-trading wrapper shall not detect cluster buying, sustained insider buying, or any cross-ticker pattern; that work is enumerated in the input description as a future task.
2. The insider-trading wrapper shall not aggregate by role (CEO / CFO / Director) or compute buy-vs-sell ratios; the records are returned one per Form 4 transaction.
3. The insider-trading wrapper shall not write to any file or network destination other than stdout and stderr, and shall not persist state between invocations (no cache files, no database, no log files written by the wrapper).
4. The insider-trading wrapper shall not emit Discord notifications or any side effects beyond stdout / stderr; notification is the analyst agent's responsibility.
5. The insider-trading wrapper shall not parse or interpret `portfolio.yaml`; the caller is responsible for resolving holdings and watchlist into the positional ticker list.
6. The insider-trading wrapper shall not introduce business-logic helpers beyond the `_common` envelope path, the `--transaction-codes` filter, the `--format` switch, and the per-provider normalization functions called out in Requirement 6.8; aggregation logic that would warrant a dedicated script (analogous to `sector_score.py`) shall not be embedded here.
7. The insider-trading wrapper shall not split the normalization into a separate module file (e.g. `_insider_normalize.py`); the logic stays inside `scripts/insider.py` to preserve the flat-directory wrapper convention in `docs/steering/structure.md` and to keep the wrapper readable end-to-end without cross-file navigation.
