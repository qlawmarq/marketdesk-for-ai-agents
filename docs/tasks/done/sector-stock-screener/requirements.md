# Requirements Document — sector-stock-screener

## Project Description (Input)

### Problem

`policy.md` §3 の中期戦略（6ヶ月〜2年）は「マクロ4象限 × セクター相対モメンタム × バリュエーションZスコア」で個別株を選定すると規定されているが、現状の `sector_score.py` は **ETF レベル**でセクターをランク付けするのみで、ランク上位セクターの中から**個別株候補を機械的に絞る経路が存在しない**。analyst は SM Energy / FLXS のような中期候補を毎回手作業の SEC 開示解析で発見しており、再現性が低い。

### Goal

ランク上位のセクター ETF（既存 `sector_score.py` 出力）からその構成銘柄を取得し、銘柄ごとに「モメンタム × バリュエーション × クオリティ × レンジ位置」の複合 z-score を算出して上位候補を出力する CLI ラッパーを追加する。中期 watchlist 候補の起案フェーズを機械化することが目的。

### Tool placement

- 配置先: `scripts/sector_stock_screener.py`（既存の thin-wrapper 設計に従う）
- skills: `skills/sector-stock-screener/SKILL.md`（per-wrapper skill）
- INDEX 更新: `skills/INDEX.md` に Composite Skills として追記（pure pipeline of `scripts/*.py`、副作用なし）
- スコープ整合性: `docs/steering/product.md` および `tech.md` で宣言された "equities, fundamentals, structured macro surveys" の範囲内（個別株スクリーニング × ETF 構成銘柄展開、Russell 2000 フォーカスではないので `multibagger-alchemy` のスコープとは衝突しない）

### Existing primitives (feasibility verified 2026-04-28)

すべて OpenBB Platform の FMP provider で取得可能。`etf.holdings` が FMP Starter+ 必須なので、どのみち FMP キーが前提になる → **全段階を FMP に統一**して provider 分岐を排除し、wrapper をシンプルに保つ：

| 必要な素材                                     | OpenBB エンドポイント（provider=fmp）                                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 価格パフォーマンス（3m/6m/12m, 月次 vol）      | `obb.equity.price.historical` から wrapper 内で算出                                                                         |
| ETF 構成銘柄 + 比率 + `updated`                | `obb.etf.holdings`                                                                                                          |
| 個別株 quote（52w, `ma200`, `ma50`）           | `obb.equity.price.quote`（batched）                                                                                         |
| 個別株 fundamentals（EV/EBITDA, ROE, 他）      | `obb.equity.fundamental.metrics`（batched；`ev_to_ebitda` → `enterprise_to_ebitda` 等 logical-name alias）                   |
| Clenow momentum                                | `obb.equity.price.historical` + `obb.technical.clenow` — **per-symbol loop**（`clenow` は stacked DataFrame を受け付けない） |
| Analyst consensus（target）                    | `obb.equity.estimates.consensus`（batched）                                                                                 |
| Analyst coverage（`number_of_analysts`）       | `obb.equity.estimates.price_target`（batched；過去 90 日の distinct `analyst_firm` を wrapper 内で数える）                  |
| z-score / ランク計算                           | `sector_score.py` の純粋関数（`zscore`, `rank_desc`）                                                                       |

### Pipeline (initial design sketch)

1. `--universe` または `--tickers` で sector ETF ユニバースを受け取る（既存 `sector_score.py` と同シグネチャ）
2. 内部でセクタースコアリングを実行（全呼び出し provider=fmp） → top N セクター（既定 N=3）を確定
3. 各 top セクター ETF について `obb.etf.holdings(provider="fmp")` を呼び、`weight` 上位 M 銘柄を取得（既定 M=20）
4. 構成銘柄プールを deduplicate し、`theme-ark` / `global-factor` に含まれる非米国上場銘柄（`^.*\.[A-Z]{1,3}$` の exchange-suffix を持つもの）は FMP Starter+ の quote 対象外（HTTP 402）なので pool-build 時点で除外する。各銘柄について：
   - `obb.equity.price.quote(provider="fmp", symbol="A,B,C,...")` → 52w レンジ位置、MA200 乖離（1 batched call）
   - `obb.equity.fundamental.metrics(provider="fmp", symbol="A,B,C,...")` → `ev_to_ebitda`、`return_on_equity`、`free_cash_flow_yield` を logical name（`enterprise_to_ebitda` / `roe` / `fcf_yield`）に alias して `signals` へ（1 batched call）
   - `obb.equity.price.historical(provider="fmp")` + `obb.technical.clenow(period=90)` → モメンタム（`clenow` は stacked DataFrame を受け付けないため **per-symbol loop**。N 銘柄で historical N 回 + clenow N 回）
   - `obb.equity.estimates.consensus(provider="fmp", symbol="A,B,C,...")` → `target_consensus`, `target_median`（1 batched call）
   - `obb.equity.estimates.price_target(provider="fmp", symbol="A,B,C,...", limit=200)` → 過去 90 日の distinct `analyst_firm` を wrapper 内で数えて `number_of_analysts` を導出（空文字 / None の `analyst_firm` は除外）。`rating_current` は label-to-number map が定型化されておらず score 経路でも未使用のため `recommendation_mean` は emit しない。
5. 銘柄ごとに 4 本の sub-score（momentum / value / quality / forward）を z-score 合成し、top-level 重み（CLI-tunable）で `composite_z` に畳み込む（Req 7）。sub-score 内部重みは MVP で文献値固定（Req 7.4）。CLI サーフェスを小さく保つため内部重みのフラグは公開しない。

   注記: backward-looking 5 ファクター（clenow_90 / ma200_distance / 1/EV-EBITDA / ROE / range_pct_52w）に forward-looking ファクター `target_upside`（`number_of_analysts ≥ 5` で gate、Req 6.4 / 6.5）を加えることで policy.md §3 中期戦略の「バリュエーションZスコア」が想定する forward-looking 要素の欠落を埋める。ここで `number_of_analysts` は consensus エンドポイントが FMP 上で露出しないため `estimates.price_target` の 90 日 distinct-firm カウントから導出する（Req 5.4、FMP 単一 provider 契約を保ったまま coverage-quality ゲートを維持）。per-analyst revision **momentum** サブスコア（改定方向の集計）は MVP スコープ外（Req 11.5）。

6. ランキングして JSON envelope で出力（`{ticker, rank, composite_score_0_100, signals{..., target_upside, target_consensus, number_of_analysts}, sector_origin: ETF, weight_in_etf, ok}`）

### Constraints / boundaries

- **読み取り専用・副作用なし**: `_envelope/SKILL.md` 規約に従い、出力は stdout JSON のみ。永続化は呼び出し側責任
- **rate-limit 配慮**: top 3 sector × 20 銘柄 = 最大 60 銘柄（重複あり）。Clenow は per-symbol 必須（historical 60 + technical.clenow 60）だが、quote / metrics / consensus / price_target は batched 各 1 コールに畳み込めるため、既定構成で 1 run あたり計 ≈ 138 FMP コール。FMP Starter 300/min に十分収まる。
- **FMP 単一 provider**: 全呼び出しを `provider="fmp"` に固定。`--provider` フラグは持たない（provider 分岐を排除して wrapper を最薄に保つ）。`FMP_API_KEY` 不在時は起動直後に `credential` カテゴリで fail-fast
- **fallback**: `etf.holdings` が `plan_insufficient` を返した場合、wrapper は `error_category: plan_insufficient` を伝搬（既存の wrap 規約通り。FMP 課金プラン低下時の挙動を保証）

### Risks / open questions

1. **複合 z-score の重み**: top-level sub-score 重み 4 個（`--weight-sub-*`）と sector-score 重み（既存の `sector_score.py` サーフェス）のみ CLI-tunable。sub-score 内部重みは文献値固定（Yartseva 2025 / QMJ / Conservative Formula 参照）。reviewer フィードバックで `/sdd-spec-design` フェーズに固定値を確定。
2. **ファクター除外フラグ**: `multibagger-alchemy` の除外フラグ（負株主資本・営業赤字）相当を中期スクリーナーにも入れるか、運用方針判断（reviewer 弁証法ループに委ねる）
3. **JP セクター対応**: `sector_score --universe jp-sector` の構成銘柄展開は `etf.holdings` 経由で TMX 系プロバイダ（過去の `etf.py` ヘルプによれば日本 ETF は finviz 範囲外）が必要。MVP では US セクター ETF（`sector-spdr` / `theme-ark` / `global-factor`）に限定し、JP は将来拡張
4. **更新頻度との整合**: `etf.holdings` の `updated` フィールドは概ね 1 週間遅れ。中期サイクル（四半期）には十分だが、出力に明示する

### Success criteria（MVP 完了の定義案）

- `uv run scripts/sector_stock_screener.py --universe sector-spdr --top-sectors 3 --top-stocks-per-sector 20` が exit 0 で複合 z-score の付いた候補リストを JSON 出力
- 出力エンベロープが `_envelope/SKILL.md` 規約に準拠（`source`/`collected_at`/`tool`/`data` ＋ 部分失敗 `warnings[]`）
- 既存統合テスト規約（`tests/integration/test_json_contract.py`）に通る
- `skills/sector-stock-screener/SKILL.md` が AUTHORING.md 規約に準拠（30〜80 行、英語）
- analyst 側の四半期サイクル（次回 7 月第1週）で実運用可能な状態

### Out of scope

- 売買シグナル生成（あくまでスクリーニング）
- バックテスト（実装しない、論文 / 過去の analyst final と整合性で運用検証）
- ポートフォリオ最適化（Fractional Kelly はあくまで analyst 側の判断レイヤー）
- リアルタイム/イントラデイ更新（日次〜週次バッチ前提）

## Introduction

`sector-stock-screener` is a new CLI wrapper at `scripts/sector_stock_screener.py` that takes a sector / theme / factor ETF universe (same signature as `sector_score.py`), selects the top-ranked sectors, expands each top sector ETF into its constituent equities via `obb.etf.holdings`, and emits a ranked list of mid-term (6-month–2-year horizon) stock candidates scored on a multi-factor composite (momentum × value × quality × forward-looking consensus). It is a thin composition wrapper — every OpenBB call (`obb.etf.holdings`, `obb.equity.price.{historical, quote}`, `obb.equity.fundamental.metrics`, `obb.technical.clenow`, `obb.equity.estimates.consensus`) is routed to **FMP as the single provider** (Starter+ tier is already mandated by `etf.holdings`, so consolidating on FMP removes provider-dispatch code with no functional loss), it re-uses `scripts/sector_score.py`'s pure scoring helpers (`zscore`, `rank_desc`, `UNIVERSES`), and adds only the local composition (sector-neutral z-score normalization, sub-score blending) and the shared envelope. The tool mechanizes the mid-term watchlist drafting phase the analyst agent currently performs by hand (SM Energy / FLXS-type discovery from SEC filings) and closes the gap between `sector_score.py` (ETF-level ranks) and individual-name selection mandated by `policy.md` §3 mid-term strategy.

### Analytical stance

The tool is designed to enable **reproducible candidate generation**, not to issue a single buy number. Four design rules follow from industry practice (Fidelity Business Cycle Framework; AQR "Value and Momentum Everywhere"; AQR "Quality Minus Junk"; Damodaran cross-sector EV/EBITDA dispersion data) and are enforced by the requirements below:

- **Value and quality factors are sector-neutral, momentum is cross-sectional.** EV/EBITDA dispersion across GICS sectors exceeds 10× (Software ~30x vs Energy ~5x; Damodaran 2026-01), so raw cross-basket z-scores on value/quality collapse the signal into a sector bet. Value and quality z-scores are therefore computed **within sector** (sector-median-centered), while momentum and technical signals stay cross-sectional so sector-level trends are captured by construction.
- **Trend and mean-reversion are separate axes**, not averaged. `ma_200d_distance` (trend) and `(1 - range_pct_52w)` (mean-reversion) cancel under a single positive-weighted composite. They feed distinct sub-scores, and a `blended_score` is emitted only when the caller selects an explicit blend profile.
- **Forward-looking inputs use consensus level with minimum coverage**, not single-analyst guesses. `target_upside = (target_consensus - last_price) / last_price` is admitted only when `number_of_analysts ≥ 5` (coverage-weak names surface the field as `null` rather than noise); the analyst-revision-momentum signal (per-analyst price-target revisions) is deferred as a future extension because the free-tier source for that series is finviz and introducing a second provider would break this wrapper's FMP single-provider contract (Req 2.1).
- **Stock selection decouples from ETF constituent weight.** Market-cap-weighted holdings would bias the candidate pool toward mega-caps and contradict the goal of surfacing SM Energy / FLXS-type mid-caps. ETF weight defines the universe; the composite z-score alone defines the ranking.

Feasibility is confirmed: every data primitive is already live (see the table in §Existing primitives) and has been exercised end-to-end on 2026-04-28 and 2026-04-29 (XLE holdings, quote, fundamentals, clenow, consensus). `sector_score.py` is the direct architectural precedent for multi-ticker z-score blending, weighted composition, partial-failure handling, and `aggregate_emit` envelope. Out-of-scope items (buy/sell signal generation, backtesting, portfolio optimization, JP sector coverage in MVP) are enforced as non-requirements below to protect the thin-wrapper scope.

## Requirements

### Requirement 1: CLI input and universe resolution

**Objective:** As an AI analyst agent, I want to pass a sector ETF universe or an explicit ticker list to the tool, so that I can invoke it uniformly from the quarterly mid-term screening loop and from ad-hoc sector probes.

#### Acceptance Criteria

1. When the user passes `--universe <key>` where `<key>` is one of `{sector-spdr, theme-ark, global-factor}`, the sector stock screener shall resolve the universe through the same `UNIVERSES` map used by `scripts/sector_score.py`.
2. When the user passes `--tickers <CSV>`, the sector stock screener shall parse the comma-separated string into a list of unique ETF tickers preserving input order.
3. When both `--universe` and `--tickers` are supplied, the sector stock screener shall exit with error_category `validation` and a non-zero exit code without calling any OpenBB endpoint.
4. When neither `--universe` nor `--tickers` is supplied, the sector stock screener shall exit with error_category `validation` and a message identifying the missing argument.
5. Where the user passes `--universe jp-sector`, the sector stock screener shall exit with error_category `validation` and a message stating that JP sector coverage is out of scope for MVP (bound to the FMP-only `etf.holdings` path, which finviz and yfinance do not cover for TSE-listed ETFs).
6. If the resolved ETF list is empty after deduplication, the sector stock screener shall exit with error_category `validation` and a message stating that at least one ETF ticker is required.

### Requirement 2: FMP single-provider contract and credential gating

**Objective:** As an operator running screening, I want every OpenBB call pinned to FMP with fail-fast credential checking, so that there is no provider-dispatch code path to audit and no silent degradation when the paid tier is unreachable.

#### Acceptance Criteria

1. The sector stock screener shall route every OpenBB call it issues (`etf.holdings`, `equity.price.historical`, `equity.price.quote`, `equity.fundamental.metrics`, `technical.clenow`, `equity.estimates.consensus`) to `provider="fmp"`. It shall not expose a `--provider` CLI flag.
2. When `FMP_API_KEY` is unset at startup, the sector stock screener shall fail-fast before any OpenBB call: exit with code 2, emit a top-level `{error, error_category: "credential", tool: "sector_stock_screener"}` payload, and state that an FMP Starter+ key is required.
3. When every sector's `etf.holdings` call fails with the same fatal category (`credential` or `plan_insufficient`), the sector stock screener shall exit with code 2 via `aggregate_emit`'s existing fatal gate and surface the condition at the envelope root.

### Requirement 3: Sector ranking and top-N selection

**Objective:** As an AI analyst agent, I want the tool to reproduce `sector_score.py`'s ETF-level ranking internally, so that the sector-selection step stays identical to the standalone sector-rotation workflow and the two tools never disagree on the top sectors.

#### Acceptance Criteria

1. When ranking the ETF universe, the sector stock screener shall reuse `scripts/sector_score.py`'s pure scoring helpers (`zscore`, `rank_desc`, `UNIVERSES`, the `build_scores` compositor and its weight surface) so the composite-score formula and the rank assignment stay single-sourced. Raw input differences between the two wrappers are expected (`sector_score.py` fetches via finviz + yfinance, this wrapper fetches historical bars via FMP and computes the same multi-period returns in-wrapper), so numerical identity is **not** required — logic identity is.
2. The sector stock screener shall accept `--top-sectors <N>` with default `N=3`, bounded `[1, 11]` via argparse, and echo the active value under `data.top_sectors_requested`.
3. When fewer sector ETFs than `--top-sectors` rank successfully (e.g. partial provider failures), the sector stock screener shall proceed with the available subset and record the shortfall under `data.notes` as `"top_sectors_shortfall: requested=<N>, resolved=<M>"`.
4. The sector stock screener shall emit the selected ETF set with their composite scores under `data.sector_ranks[]` (`{ticker, rank, composite_score_0_100, composite_z}`), so the sector step stays auditable from the same envelope.
5. The sector stock screener shall accept `--weight-sector-*` flags identical to `sector_score.py`'s composite weights, so the two tools share a single configuration surface; defaults match `sector_score.py`.

### Requirement 4: ETF holdings expansion and stock-pool construction

**Objective:** As an AI analyst agent, I want the constituent list of each top-ranked sector ETF expanded into a deduplicated stock pool, so that individual-name scoring runs on a well-defined universe.

#### Acceptance Criteria

1. For each top-ranked sector ETF, the sector stock screener shall call `obb.etf.holdings(provider="fmp")` exactly once and record the fetched `{symbol, name, weight, shares, value, updated}` rows.
2. The sector stock screener shall accept `--top-stocks-per-sector <M>` with default `M=20`, bounded `[1, 100]` via argparse, and echo the active value under `data.top_stocks_per_sector_requested`.
3. When expanding an ETF, the sector stock screener shall take the top `M` constituents by ETF `weight` for the **universe definition only** (i.e. to bound the fetch cost and keep the per-stock data-fetch budget predictable); per-stock ranking is performed separately under Requirement 7 and does not re-use `weight` as an input.
4. When the same ticker appears in constituents of multiple selected ETFs, the sector stock screener shall deduplicate on `symbol`, retain the first-seen row, and record the other ETF origins in a per-stock `sector_origins[]` list of `{etf_ticker, weight_in_etf, updated}` so the cross-sector membership is auditable. When `len(sector_origins) >= 2`, the sector stock screener shall append `"stock_appears_in_multiple_top_sectors"` to the per-row `data_quality_flags[]` (member of the Req 10.7 closed enumeration).
5. If `etf.holdings` returns `error_category: plan_insufficient` for any selected sector, the sector stock screener shall propagate the failure under `data.provider_diagnostics` with `{provider, stage: "etf_holdings", error, error_category, symbol: <etf ticker>}`, continue expanding the remaining selected sectors, and — if every selected sector fails — exit per Requirement 2.3.
6. The sector stock screener shall emit `etf_holdings_updated_max_age_days` at `data` level computed from the oldest `updated` date across all fetched holdings (the FMP adapter returns `updated` as a Python `datetime`; the age is `(today - row.updated.date()).days`). Rows where `updated` is null are skipped from the max-age reduction; if every row is null, the field shall be emitted as `null`.
7. If the expanded stock pool has fewer than three tickers after deduplication and partial failures, the sector stock screener shall still emit per-ticker rows for every resolved ticker with all cross-sectional z-scores set to `null` (because n<3 cross-sectional z-scores are degenerate), and append a top-level warning `{symbol: null, error: "insufficient stock pool size for cross-sectional z-score", error_category: "validation"}`.
8. When expanding an ETF, the sector stock screener shall drop any constituent `symbol` whose trailing `.XXX` suffix is a member of an explicit non-US exchange-suffix allowlist (e.g. `.HK`, `.T`, `.L`, `.PA`, `.DE`, `.SW`, `.TO`, `.AX`, `.NS`, `.SA`, …) before adding it to the stock pool, because FMP Starter+ quote rejects those listings with HTTP 402 "Premium Query Parameter". The filter shall NOT match US-listed class-share tickers whose suffix is a single letter (`BRK.A`, `BRK.B`, `BF.B`, `GEF.B`, `LEN.B`, `RDS.A`, …); those names are real SPDR-ETF constituents and must reach per-stock scoring. Filtered tickers are recorded under `data.non_us_tickers_filtered[]` as `{symbol, etf_ticker}` entries and the top-level `analytical_caveats` list includes `"non_us_tickers_filtered_from_pool"` whenever any row is filtered (see Req 10.6). The allowlist is a closed frozenset defined at module scope (`_NON_US_EXCHANGE_SUFFIXES`) so reviewers can enumerate every exchange the wrapper filters without reading the matching logic.

### Requirement 5: Per-ticker data acquisition

**Objective:** As an AI analyst agent, I want one CLI invocation to gather every factor input per constituent ticker, so that the quarterly mid-term screening runs in a single command instead of one wrapper-call chain per name.

#### Acceptance Criteria

1. The sector stock screener shall fetch quote data for the full resolved pool via a single batched call `obb.equity.price.quote(provider="fmp", symbol="A,B,C,...")` and, per ticker, extract `last_price`, `year_high`, `year_low`, the 200-day moving average (FMP field `ma200`), the 50-day moving average (FMP field `ma50`), and `prev_close`; the moving averages shall be emitted under the logical names `ma_200d` and `ma_50d` in the per-ticker `signals` block. Because the wrapper pins a single provider, no provider-aware field-name map is required. The response rows shall be indexed by `row["symbol"]` rather than by position, because the OpenBB adapter makes no response-order guarantee.
2. The sector stock screener shall fetch fundamental metrics for the full resolved pool via a single batched call `obb.equity.fundamental.metrics(provider="fmp", symbol="A,B,C,...")` and, per ticker, extract `market_cap`, `ev_to_ebitda`, `return_on_equity`, `free_cash_flow_yield` into the `signals` block under the logical names `enterprise_to_ebitda`, `roe`, and `fcf_yield` (FMP's native field names differ from the canonical yfinance names that `scripts/_schema.py::classify_metric_unit` tags; aliasing at fetch time preserves the single-source unit-tagging surface). `pe_ratio` and `gross_margin` are not emitted because FMP's `metrics` endpoint does not expose them and the wrapper's scoring paths (Req 7.x) do not consume them; callers that need the informational fields shall pull them via `scripts/fundamentals.py --type ratios`. Responses shall be indexed by `row["symbol"]`.
3. For every stock in the resolved pool, the sector stock screener shall compute Clenow momentum via `obb.technical.clenow` with `period=90` applied to a per-symbol historical bar series fetched with `obb.equity.price.historical(provider="fmp", symbol=<symbol>, start_date=<today - 180 days>)`, and coerce the returned `factor` to a numeric `clenow_90` field via the `_to_float` helper pattern used in `scripts/sector_score.py::fetch_clenow`; a non-numeric or missing `factor` shall emit `clenow_90: null`. The historical bar fetch is performed per-symbol (not via a batched multi-symbol historical call) because `obb.technical.clenow` rejects the stacked-index DataFrame that batched historical calls produce.
4. The sector stock screener shall fetch analyst-consensus levels for the full resolved pool via a single batched call `obb.equity.estimates.consensus(provider="fmp", symbol="A,B,C,...")` and, per ticker, extract `target_consensus`, `target_median` into the `signals` block. The consensus endpoint under FMP does not populate `number_of_analysts` or `recommendation_mean`, so the sector stock screener shall derive `number_of_analysts` from a separate batched call `obb.equity.estimates.price_target(provider="fmp", symbol="A,B,C,...", limit=200)`: the value is the count of distinct `analyst_firm` entries whose `published_date` falls within the most recent 90 calendar days, after excluding rows where `analyst_firm` is null, empty, or whitespace-only. `recommendation_mean` shall not be emitted (no label-to-numeric map is defined by OpenBB on FMP, and no Req 7.x sub-score consumes it). Both batched responses shall be indexed by `row["symbol"]`.
5. The sector stock screener shall issue every OpenBB call under `_common.safe_call` so stdout-borne provider warnings are absorbed and failures become structured `{ok: false, error, error_type, error_category}` per-row records. A private helper `_index_by_symbol(rows)` shall be the single entry point for batched-response row-to-symbol indexing so that no call site reads `results[<integer>]` by position.
6. The sector stock screener shall resolve the per-ticker `last_price` used in Requirement 6 derivations via the fallback chain `quote.last_price → quote.prev_close → null`; the second rung shall append a per-ticker `data_quality_flags` entry `"last_price_from_prev_close"`, and full unavailability shall append `"last_price_unavailable"`.
7. The sector stock screener shall tag every stock with its resolved GICS sector via the selected ETF origin (SPDR sector ETFs map one-to-one to GICS sectors; a constituent's sector is taken from the first ETF in `sector_origins[]`), emit the tag as `gics_sector` in the per-ticker row, and use it as the grouping key for the sector-neutral z-scores in Requirement 7. For ETF universes whose members do not map one-to-one to a GICS sector (`theme-ark`, `global-factor`), the tag shall be `null` and the Requirement 7.7 fallback (basket-wide z with a `sector_group_too_small_for_neutral_z(<factor>)` flag) shall apply row-wise.

### Requirement 6: Derived indicators

**Objective:** As an AI analyst agent, I want the local indicators the policy requires computed uniformly, so that the tool stops delegating arithmetic to callers.

#### Acceptance Criteria

1. When `last_price` (resolved via Requirement 5.6's fallback chain), `year_high`, and `year_low` are all present, the sector stock screener shall compute `range_pct_52w = (last_price - year_low) / (year_high - year_low)` per ticker.
2. When `last_price` and `ma_200d` are both present, the sector stock screener shall compute `ma200_distance = (last_price - ma_200d) / ma_200d` per ticker.
3. When `enterprise_to_ebitda` is present and greater than zero, the sector stock screener shall compute `ev_ebitda_yield = 1 / enterprise_to_ebitda` per ticker (inverted so higher is cheaper, matching other z-scored value factors); negative, zero, or missing `enterprise_to_ebitda` shall emit `ev_ebitda_yield: null` and append `"ev_ebitda_non_positive"` to `data_quality_flags[]`.
4. When `target_consensus`, `last_price`, and `number_of_analysts` are all present **and** `number_of_analysts ≥ 5`, the sector stock screener shall compute `target_upside = (target_consensus - last_price) / last_price` per ticker. The threshold `5` is fixed in the MVP (analyst-coverage gate is not CLI-tunable); revisiting the value is a requirements change.
5. When `number_of_analysts < 5`, the sector stock screener shall emit `target_upside: null` and append `"analyst_coverage_too_thin"` to `data_quality_flags[]` on that ticker row, so a sparsely-covered name never contributes a noisy forward-looking signal to the composite.

### Requirement 7: Sector-neutral normalization and sub-scores

**Objective:** As an AI analyst agent, I want value and quality z-scores computed within each GICS sector while momentum stays cross-sectional, so that sector-level valuation dispersion (EV/EBITDA Software ~30× vs Energy ~5×, per Damodaran 2026-01) does not silently collapse the composite into a sector bet.

#### Acceptance Criteria

1. The sector stock screener shall compute **sector-neutral cross-sectional z-scores** (grouped by `gics_sector`, centered on sector median, scaled by sector MAD or std per the `sector_score.py` pattern) for the **value factor** `ev_ebitda_yield` and for the **quality factor** `roe`.
2. The sector stock screener shall compute **basket-wide cross-sectional z-scores** (single group = all resolved tickers across all selected sectors) for the **momentum factor** `clenow_90`, the **range-position factor** `(1 - range_pct_52w)`, the **trend factor** `ma200_distance`, and the **forward-looking factor** `target_upside`.
3. The sector stock screener shall compute a `momentum_z` sub-score as the weight-normalized sum of `z(clenow_90)` and `z(ma200_distance)` (both trend-direction signals), a `value_z` sub-score as the weight-normalized sum of sector-neutral `z(ev_ebitda_yield)` and cross-sectional `z(1 - range_pct_52w)` (mean-reversion and price-to-fundamentals, both directionally "cheap"), a `quality_z` sub-score from sector-neutral `z(roe)`, and a `forward_z` sub-score from `z(target_upside)`; each sub-score shall use the sum-of-available-weights normalization from `sector_score.py` so a missing signal degrades gracefully.
4. The sector stock screener shall fix the sub-score internal weights in the MVP (literature-anchored defaults, e.g. equal-weight within `momentum_z` across `clenow_90` and `ma200_distance`, equal-weight within `value_z` across `ev_ebitda_yield` and `(1 - range_pct_52w)`) and echo the active values under `data.weights` for auditability. Sub-score internal weights are not CLI-tunable; revisiting them is a requirements change. Only the top-level sub-score composition weights are CLI-tunable (Requirement 7.6).
5. The sector stock screener shall map each sub-score z to 0-100 via `clip(50 + z * 25, 0, 100)` and emit them as `momentum_score_0_100`, `value_score_0_100`, `quality_score_0_100`, and `forward_score_0_100` (matching the `sector_score.py` transform).
6. The sector stock screener shall compute the top-level `composite_z` as the weight-normalized sum of the four sub-score z-scores with caller-tunable top-level weights (`--weight-sub-momentum`, `--weight-sub-value`, `--weight-sub-quality`, `--weight-sub-forward`) with documented defaults summing to 1.0, and map it to `composite_score_0_100` via the same transform.
7. If fewer than three tickers within a given `gics_sector` carry a non-null value for a sector-neutral factor, the sector stock screener shall fall back to basket-wide z-scoring for that factor on those rows and append `"sector_group_too_small_for_neutral_z(<factor>)"` to `data_quality_flags[]` on every affected row.
8. If fewer than three tickers in the entire basket carry a non-null value for a basket-wide factor, the sector stock screener shall emit `null` for that factor's z on every row and append `"basket_too_small_for_z(<factor>)"` to `data_quality_flags[]` on every affected row; the surrounding sub-scores shall still be computed from the available signals under the same weight-normalization rule.
9. The sector stock screener shall include a per-ticker `z_scores` block containing both the individual signal z-scores (with each field tagged `z_<field>_sector_neutral` or `z_<field>_basket` to make the normalization scope unambiguous) and the four sub-score z-scores, so operators can audit every step of the composition.
10. Every per-ticker row shall carry `basket_size` (count of tickers that entered basket-wide z-scoring), `sector_group_size` (count of tickers in the same `gics_sector` group), and `basket_size_sufficient` (boolean, true iff `basket_size >= 3`) so consumers can filter for statistically-usable rows without re-counting.

### Requirement 8: Ranking and output ordering

**Objective:** As an AI analyst agent, I want `data.results[]` sorted by the composite score so the top candidates are the first rows I read.

#### Acceptance Criteria

1. The sector stock screener shall sort `data.results[]` by `composite_score_0_100` descending, with `null` scores sinking to the bottom.
2. The sector stock screener shall populate a `rank` field per ticker reflecting the sorted position (1-indexed).
3. The sector stock screener shall emit every resolved ticker row — no output truncation. Callers that want the top-N slice apply `jq '.data.results[:N]'` at the shell level.

### Requirement 9: Shared envelope and JSON contract compliance

**Objective:** As the JSON-contract integration test, I want the new wrapper to pass `tests/integration/test_json_contract.py` unmodified, so that envelope regressions are caught automatically.

#### Acceptance Criteria

1. The sector stock screener shall emit stdout via `_common.aggregate_emit` with `tool="sector_stock_screener"` so the root keys are exactly `{source, collected_at, tool, data}` plus the optional `{warnings, error, error_category, details}` slots.
2. The sector stock screener shall place per-stock rows under `data.results[]` and per-query metadata (`universe`, `tickers` (the ETF universe echoed back), `weights`, `sector_ranks`, `top_sectors_requested`, `top_stocks_per_sector_requested`, `etf_holdings_updated_max_age_days`, `missing_tickers`, optional `provider_diagnostics`, `analytical_caveats`, `notes`) as siblings of `results` under `data`. `provider` is omitted from `data` because the wrapper is pinned to FMP.
3. The sector stock screener shall shape per-row failures as `{symbol, ok: false, error, error_type, error_category, sector_origins}` and mirror each failure into top-level `warnings[]` via `aggregate_emit`. `provider` is not emitted per row (see Requirement 9.2).

Envelope invariants that `_common.aggregate_emit` / `test_json_contract.py` already enforce repo-wide (exit-code gate on all-fatal batches, NaN/Inf sanitization, stdout JSON purity, partial-failure exit 0) are inherited by construction and are not restated here.

### Requirement 10: Per-ticker output schema

**Objective:** As an AI analyst agent reading the JSON programmatically, I want every stock row to carry the same named fields with explicit provenance metadata, so that I can traverse the response without schema inference.

#### Acceptance Criteria

1. The sector stock screener shall emit, for every resolved stock, a row containing at minimum: `symbol`, `ok`, `rank`, `gics_sector`, `sector_origins`, `composite_score_0_100`, `momentum_score_0_100`, `value_score_0_100`, `quality_score_0_100`, `forward_score_0_100`, `signals`, `z_scores`, `basket_size`, `sector_group_size`, `basket_size_sufficient`, `data_quality_flags`, `interpretation`. `provider` is not emitted per row (single-provider wrapper; see Req 9.2).
2. The sector stock screener shall populate `signals` with `{last_price, year_high, year_low, ma_200d, ma_50d, range_pct_52w, ma200_distance, market_cap, enterprise_to_ebitda, ev_ebitda_yield, roe, fcf_yield, clenow_90, target_consensus, target_median, target_upside, number_of_analysts}` so the underlying inputs are auditable from the same row. `pe_ratio` and `gross_margin` are intentionally absent because FMP's `metrics` endpoint does not expose them (see Req 5.2) and no Req 7.x sub-score consumes them.
3. The sector stock screener shall emit `interpretation` as an object containing at minimum `{score_meaning: "basket_internal_rank", composite_polarity: "high=better_candidate", forward_looking_component_gated_on: "number_of_analysts>=5", sector_neutral_factors: ["ev_ebitda_yield", "roe"], basket_wide_factors: ["clenow_90", "range_pct_52w", "ma200_distance", "target_upside"]}`, so downstream consumers never misread the normalization scope.
4. The sector stock screener shall never emit a single field named `buy_signal` or `recommendation` that asserts a final trading decision, because the output is a candidate-generation tool and the final call rests with the analyst; this is a negative invariant that the JSON-contract test shall check via field absence.
5. When a per-ticker row has `ok: false`, the sector stock screener shall omit all `*_score_0_100` fields and `z_scores` from that row, while still populating `symbol`, `gics_sector`, `sector_origins`, `error`, `error_type`, and `error_category`.
6. Under `data`, the sector stock screener shall emit an `analytical_caveats` string array containing at minimum the entries `"scores_are_basket_internal_ranks_not_absolute_strength"`, `"value_and_quality_are_sector_neutral_z_scores"`, `"momentum_and_forward_are_basket_wide_z_scores"`, `"etf_holdings_may_lag_spot_by_up_to_one_week"`, `"forward_score_requires_number_of_analysts_ge_5"`, and `"number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions"`, so the caveats travel with every response instead of living only in documentation. Additionally, whenever Req 4.8's non-US-ticker filter drops any constituent during pool build, the `"non_us_tickers_filtered_from_pool"` caveat shall also appear in the same list.
7. The `data_quality_flags[]` array on each per-ticker row shall draw exclusively from the closed enumeration `{"last_price_from_prev_close", "last_price_unavailable", "ev_ebitda_non_positive", "analyst_coverage_too_thin", "sector_group_too_small_for_neutral_z(ev_ebitda_yield)", "sector_group_too_small_for_neutral_z(roe)", "basket_too_small_for_z(clenow_90)", "basket_too_small_for_z(range_pct_52w)", "basket_too_small_for_z(ma200_distance)", "basket_too_small_for_z(target_upside)", "stock_appears_in_multiple_top_sectors"}`, so downstream consumers can enumerate the catalog without reverse-engineering the wrapper source. The `"non_us_tickers_filtered_from_pool"` string is not a per-row flag (the filtered tickers never reach per-row scoring) and lives on the `data.analytical_caveats` list only, per Req 10.6.

### Requirement 11: Scope boundaries — what the tool must not do

**Objective:** As a reviewer enforcing the thin-wrapper architecture in `structure.md`, I want scope drift explicitly forbidden, so that trading-signal synthesis, backtesting, and portfolio optimization stay outside this tool.

#### Acceptance Criteria

1. The sector stock screener shall not emit buy / sell / hold recommendations; the tool's contract ends at ranked candidates.
2. The sector stock screener shall not perform backtesting or historical replay of any score.
3. The sector stock screener shall not blend macro-quadrant data; macro integration remains the analyst agent's responsibility, and the tool's sector selection is anchored in the backward-looking `sector_score` composite only.
4. The sector stock screener shall not write to any file or network destination other than stdout and stderr, and shall not persist state between invocations (no cache files, no database, no log files).
5. The sector stock screener shall not compute an analyst-revision-**momentum** sub-score (i.e. aggregated direction/magnitude of price-target changes, analyst-firm entries, or upgrades/downgrades) in MVP; the `target_upside` level gated on `number_of_analysts ≥ 5` is the only forward-looking sub-score. The wrapper does call `estimates.price_target` per Req 5.4, but only to derive the integer count `number_of_analysts` (distinct `analyst_firm` in the last 90 days) that gates `target_upside`; no revision-direction or revision-magnitude feature enters the composite. Revisiting the extension to a full revision-momentum sub-score requires a requirements change.

### Requirement 12: Skill documentation and INDEX update

**Objective:** As a future AI agent invoking this tool for the first time, I want a `SKILL.md` that tells me exactly how to call it, so that I do not guess flags or mis-read the envelope.

#### Acceptance Criteria

1. The sector stock screener change set shall deliver `scripts/sector_stock_screener.py`, `skills/sector-stock-screener/SKILL.md`, a `skills/INDEX.md` row, and a `README.md` §1-1 matrix row with verification-test pointer in the same commit, per the repo-wide ChangeBundleRule.
2. The SKILL.md shall be written in English, stay within roughly 30-80 lines, and reference the shared `_envelope`, `_errors`, and `_providers` skills for cross-cutting policy rather than duplicating their prose.
3. The SKILL.md shall document every CLI flag (`--universe`, `--tickers`, `--top-sectors`, `--top-stocks-per-sector`, sector-score weight flags, top-level sub-score weight flags) with default values. No `--provider` flag is documented because the wrapper is pinned to FMP (see Req 2.1).
4. The SKILL.md shall include one short example invocation and one truncated output sample taken from an actual run, not from test fixtures.
5. The SKILL.md shall state the explicit scope boundaries (no buy signals, no backtest, no portfolio optimization, no JP-sector MVP, no revision-momentum MVP) so downstream callers do not assume omitted capabilities.
6. The SKILL.md shall contain an **Interpretation** section that spells out the three caveats echoed in `data.analytical_caveats`: (a) scores are basket-internal rank summaries, not absolute strength readings; (b) value and quality factors are sector-neutral z-scores, while momentum / range / forward factors are basket-wide z-scores — mixing the two scopes without awareness inverts the reading; (c) the forward-looking sub-score is gated on `number_of_analysts ≥ 5` so sparsely-covered names surface as `null` rather than noise.
7. The SKILL.md shall include a short "Provider" subsection that makes explicit that the wrapper pins every OpenBB call to FMP (Starter+ tier) and that a missing or insufficient `FMP_API_KEY` fails fast per Req 2.2 / 2.3.

### Requirement 13: Integration-test coverage

**Objective:** As the project's verification gate, I want the new wrapper covered by the existing integration-test style, so that regressions are caught without bespoke test scaffolding.

#### Acceptance Criteria

1. The sector stock screener change set shall add at least one `pytest -m integration` test that invokes `uv run scripts/sector_stock_screener.py --universe sector-spdr --top-sectors 2 --top-stocks-per-sector 5` as a subprocess and asserts the envelope shape via `tests/integration/_sanity.py` helpers. The test shall auto-skip when `FMP_API_KEY` is absent (matches `tests/integration/test_etf.py` pattern).
2. The integration test shall assert that `data.results[]` is sorted by `composite_score_0_100` descending with `null` scores sinking to the bottom.
3. The integration test shall assert that `data.analytical_caveats` contains all five required caveat strings defined in Requirement 10.6, and that no per-ticker row carries a field named `buy_signal` or `recommendation` (Requirement 10.4 negative invariant).
4. The integration test shall assert that every `ok: true` row carries `gics_sector` and at least one entry in `sector_origins[]`, and that `composite_score_0_100`, `momentum_score_0_100`, `value_score_0_100`, `quality_score_0_100` are present (the `forward_score_0_100` may be `null` on thin-coverage rows).
5. The integration test shall assert that `data.top_sectors_requested` and `data.top_stocks_per_sector_requested` are emitted and match the CLI-supplied values, and that `data.etf_holdings_updated_max_age_days` is a non-negative integer.
6. The integration test shall assert that at least one ticker row carries `z_scores` fields tagged with both `_sector_neutral` and `_basket` suffixes (Requirement 7.9), locking the normalization-scope disclosure in place.
7. The integration test shall assert that `data.analytical_caveats` contains `"number_of_analysts_is_90d_distinct_firm_count_from_price_target_revisions"` on every successful run, locking the derivation disclosure (Req 5.4, Req 10.6) in place.
8. The integration test suite shall include a source-text check that `scripts/sector_stock_screener.py` contains no positional batched-response lookup of the form `results[<integer>]` (Req 5.5 / G6 invariant); `_index_by_symbol` is the only permitted path.

### Requirement 15: Per-stock failure classification

**Objective:** As the envelope-contract consumer, I want per-stock failure classification to aggregate across all five per-stock fetch axes, so that a credential rotation does not leak through as a mixed-category warnings storm.

#### Acceptance Criteria

1. The sector stock screener shall implement an inline classifier `_classify_stock_failure(symbol, fetches: dict[str, dict[str, Any]])` where `fetches` carries the per-stock `{ok, error, error_type, error_category}` record for each of the five axes `{quote, metrics, historical, consensus, price_target}`. The classifier shall return `None` when any axis produced usable data, and a `{error, error_type, error_category}` record when every axis failed.
2. When every failed axis carries the same fatal `error_category` (`credential` or `plan_insufficient`), the returned record shall carry that fatal category so `_common.aggregate_emit`'s exit-code gate can promote the batch (Req 2.3). Mixed-category or `other`-category failures carry the first-seen category so the warnings channel preserves the first diagnosis.
3. The sector-rank step (Req 3) shall continue to use the imported `sector_score._classify_ticker_failure` (which is specialized to the three sector-ETF axes `perf`, `clenow_90`, `clenow_180`); the per-stock classifier in Req 15.1 is a separate function with its own axes and is not a drop-in replacement.

### Requirement 14: FMP call-shape matrix

**Objective:** As an operator running the default 60-stock basket against FMP Starter 300/min, I want the fetch layer to exploit every batched endpoint FMP supports and keep the per-symbol fan-out constrained to the endpoints that genuinely require it, so the run stays within rate limit without bespoke throttling.

#### Acceptance Criteria

1. The sector stock screener shall issue exactly one batched `obb.equity.estimates.consensus(provider="fmp", symbol="A,B,C,...")` call per run, covering the full resolved stock pool. FMP's consensus endpoint accepts comma-separated `symbol` (verified via research-phase live probe 2026-05-01) and returns one row per symbol in one round-trip.
2. The sector stock screener shall issue exactly one batched `obb.equity.price.quote(provider="fmp", symbol="A,B,C,...")` call and one batched `obb.equity.fundamental.metrics(provider="fmp", symbol="A,B,C,...")` call per run, each covering the full resolved stock pool. Both endpoints accept comma-separated `symbol` under FMP (verified 2026-05-01).
3. The sector stock screener shall issue exactly one batched `obb.equity.estimates.price_target(provider="fmp", symbol="A,B,C,...", limit=200)` call per run and compute `number_of_analysts` per ticker from the returned rows per Req 5.4. The `limit=200` is a fixed constant; revisiting it is a requirements change.
4. The sector stock screener shall issue `obb.equity.price.historical` and `obb.technical.clenow` **per symbol** (one `historical` + one `clenow` reduction per stock in the pool). Batched multi-symbol historical input is rejected by `obb.technical.clenow` (stacked-index error verified via research-phase live probe 2026-05-01); the per-symbol loop is the only supported shape.
5. For a default run (`--top-sectors 3 --top-stocks-per-sector 20`, ~60 deduplicated stocks) the total FMP call count shall be ≈138 (11 sector-ETF Clenow loops + 3 `etf.holdings` + 60 stock-level historical + 60 stock-level `clenow` + 4 batched fetches), well within the FMP Starter 300/min budget.
