# Requirements Document — entry-timing-scorer

## Project Description (Input)

### Problem

`policy.md` §3 短期戦略（1〜6ヶ月）は「52週レンジ位置・6ヶ月モメンタム・RSI・マクロ象限整合」で**長期/中期で選定済みの銘柄に対するエントリータイミング最適化**を行うと規定されている。しかし現状は：

1. analyst が日次レポートで保有 + watchlist の各銘柄について `quote.py` + `momentum.py --indicator rsi` + `momentum.py --indicator macd` を**個別実行 → 手作業で表組み**している（直近 3 日のレポートで毎日繰り返されている作業）
2. ASC E2 のような「52w 高値 $16.91 を 3 営業日連続クローズ上抜け」というルールも、毎セッション手作業で `historical.py` から最近 10 営業日終値を引いて閾値比較
3. FLXS 4/27 の +13.1% 急騰時、出来高ラベル「20 日平均」が yfinance `volume_average` の rolling 3 ヶ月平均と取り違えられる事象（reviewer R1 で指摘）— 短期スコアリング層がないため、出来高 z-score の計算が毎回アドホック
4. `momentum.py` は単一指標 × 単一銘柄のため、複合エントリー指標（モメンタム + RSI + MACD + レンジ位置 + 出来高 z-score）を一度に出す経路がない

### Goal

ティッカーリスト（典型: `portfolio.yaml` の `positions[]` + `watchlist[]`、5〜10 銘柄）を受け取り、銘柄ごとに「エントリー適性スコア 0〜100」を複数指標の z-score 合成で出力する CLI ラッパーを追加する。日次保有監視ループの自動化と、出来高ラベル誤認のような恒久リスクの除去が目的。

### Tool placement

- 配置先: `scripts/entry_timing_scorer.py`
- skills: `skills/entry-timing-scorer/SKILL.md`（per-wrapper skill）
- INDEX 更新: `skills/INDEX.md` に追記
- スコープ整合性: `tech.md` で宣言された「ticker-level data」の範囲内、`structure.md` の thin-wrapper 設計に準拠（既存 wrapper 4 種の合成）

### Existing primitives (feasibility verified 2026-04-28)

| 必要な素材 | 既存ラッパー | 検証結果 |
|---|---|---|
| 52w レンジ位置・MA200/MA50 | `quote.py` | **2026-04-28 実機確認**: XLE で `year_high=63.46`, `year_low=39.755`, `ma_200d=48.236923`, `ma_50d=57.3338` 取得成功（yfinance）。**2026-04-30 FMP 再検証で判明**: FMP quote は MA を `ma200` / `ma50`（アンダースコアなし）で返す — データは yfinance と完全一致（TLT: `ma_200d=88.17685`, `ma200=88.17685` 完全同値）。Req 4.1 でプロバイダ別フィールドマップを必須化（Decision 11） |
| OHLCV（実 20日平均出来高計算用） | `historical.py` | OK（analyst が ASC E2 判定で `--start YYYY-MM-DD --end YYYY-MM-DD` で利用中） |
| 6ヶ月クレナウ・モメンタム | `momentum.py --indicator clenow --period 126` | OK（既存 wrapper、period デフォルト 90 を 126 = 6m に変更可） |
| RSI(14) | `momentum.py --indicator rsi --length 14` | OK |
| MACD ヒストグラム | `momentum.py --indicator macd` | OK（`latest{macd, signal, histogram}` 出力済み） |
| 出来高 3ヶ月平均（参照用） | `quote.py.volume_average` | OK（rolling 3-month、yfinance 慣習）。**2026-04-30 FMP 再検証で判明**: FMP quote は `volume_average` を `None` で返す（FMP は quote endpoint で出来高平均を露出していない）— Req 5.7 で FMP 時の null 許容 + `volume_reference_unavailable_on_provider` フラグを mandate（Decision 11） |
| 出来高 10日平均（参照用） | `quote.py.volume_average_10d` | OK（yfinance）。**2026-04-30 FMP 再検証**: FMP は同様に `None`、上段と同じフラグで包括カバー |
| 出来高 20日平均（policy 想定） | `historical.py` の OHLCV から自前計算 | 要実装、`pandas.rolling(20).mean()` 等で計算 |
| 直近・次回決算日（earnings risk フラグ用） | `calendars.py --type earnings --start <TODAY> --end <TODAY+45d>`（`obb.equity.calendar.earnings`、nasdaq keyless / fmp opt-in） | **2026-04-29 初回確認**: nasdaq provider keyless、出力 `{report_date, symbol, eps_consensus, eps_previous, num_estimates, reporting_time, market_cap}`（`shared/openbb/skills/calendars/SKILL.md`）。**2026-04-30 初回再検証**: nasdaq は窓 ≥ ~56 日で `AttributeError: NoneType.get`、≥ ~84 日で HTTP 403、403 はプロセス poisoning するためデフォルト窓を 45 日に短縮。**2026-04-30 FMP second pass で消化完了**: `provider="fmp"` は +90 日まで clean に動作（+90d で 10,559 行、+45d で 8,383 行、+84d で 9,879 行、403 poisoning なし）。出力スキーマは nasdaq と同等で `{report_date, symbol, name, eps_previous, eps_consensus, eps_actual, revenue_consensus, revenue_actual, last_updated}`。ただしスペックバスケットで +45d / +90d のヒット銘柄は同一（ASC 2026-05-06, CMCL 2026-05-11, SM 2026-05-06; FLXS / TLT / LQD はどちらの窓でもヒットなし）— 追加 45 日は Req 7.2 の 5 営業日閾値に対して運用便益ゼロ。デフォルト `N=45` を両プロバイダ共通とし、FMP 経由の `--earnings-window-days 90` は opt-in escape hatch として残す（Decision 7） |

すべての素材は既存ラッパーで取得可能であり、新規 OpenBB API 呼び出しは不要。**新規開発はこれらの合成・z-score 計算のみ**。

### Pipeline (initial design sketch)

1. `--tickers <CSV>` または `--portfolio-file <path>` でティッカーリストを受け取る
2. ティッカー横断で 1 ショット fetch（ticker 単位の重複呼び出しを避ける）：
   - `obb.equity.calendar.earnings(start=<TODAY>, end=<TODAY + N days>, provider=<args.provider>)` with `N` defaulting to `45` (overridable via `--earnings-window-days`, bounded `[1, 90]`) → 入力ティッカー集合の直近・次回決算日。出力後に input ticker 集合で内部フィルタ。2026-04-30 の実 API 検証で nasdaq は +49 日までで正常動作、+56 日以降でサーバー側エラー、+84 日以降で 403 poisoning を確認。FMP second-pass で `provider="fmp"` は +90 日まで clean に動作することを確認済みだが、スペックバスケットの +45d / +90d ヒット銘柄は同一のため、デフォルト `N=45` は両プロバイダ共通で維持。`--earnings-window-days 90` は FMP 経由でのみ実用的な opt-in escape hatch として残る（該当記述は §Existing primitives 決算日行および §Constraints FMP 再検証結果項目 1 を参照）
3. 各ティッカーについて以下を並列フェッチ：
   - `obb.equity.price.quote` → `last_price`, `year_high`, `year_low`, `ma_200d`, `ma_50d`, `volume_average`, `volume_average_10d`
   - `obb.equity.price.historical(period=140d)` → 直近 140 営業日 OHLCV（実 20 日平均出来高 + RSI 計算用バッファ）
   - `obb.technical.clenow(period=126)` → 6ヶ月モメンタム
   - `obb.technical.rsi(length=14)` → RSI(14)
   - `obb.technical.macd(fast=12, slow=26, signal=9)` → MACD histogram
4. 派生指標の計算：
   - `range_pct_52w = (last - year_low) / (year_high - year_low)`（policy 採用、低いほど割安）
   - `ma200_distance = (last - ma_200d) / ma_200d`
   - `volume_z_20d`（**reviewer R1 で指摘された「20日平均」の正しい実装、yfinance `volume_average` の rolling 3-month と区別**）。Req 5.4 で `--volume-z-estimator {robust|classical}` をデフォルト `robust` として公開；`robust = (log(latest_volume) - median(log(volume[-20:]))) / (1.4826 * MAD(log(volume[-20:])))`、`classical = (latest_volume - mean(volume[-20:])) / stdev(volume[-20:])`。参照窓は最新セッションを除外した 20 営業日
   - `days_to_next_earnings = next_report_date - today`（90 日以内に該当決算がない場合 NULL。営業日換算は出力フィールド `days_to_next_earnings_unit: "calendar_days"` で明示）
5. 銘柄ごとに複合 z-score（重みは `/sdd-spec-design` で詰める、初期案）：
   - `+ z(clenow_126)` 30%（6ヶ月モメンタム）
   - `+ z(1 - range_pct_52w)` 25%（レンジ位置 = 安値圏ほど高スコア）
   - `+ z(50 - rsi_14)` 15%（売られ過ぎほど高スコア、ただし `rsi < 20` は警告フラグ）
   - `+ z(macd_histogram)` 15%（モメンタム転換）
   - `+ z(volume_z_20d)` 15%（ブレイクアウト確認）

   注記: `days_to_next_earnings` は z-score 合成には**意図的に組み込まない**。代わりに `days_to_next_earnings ≤ 5` 営業日で `earnings_proximity_warning: true` の独立フラグを出力する。理由は、earnings event risk と純粋な timing シグナルを線形合成すると「決算近 + モメンタム高」がスコア中和して読み取れなくなるため（FLXS 4/20 8-K → 4/27 +13.1% 急騰のような post-earnings drift / pre-earnings positioning を直接読み取る運用的要請、§Problem #3 と整合）。
6. ランキング + 各銘柄の発火中トリガー（`portfolio.yaml.positions[].exit_rules` / `watchlist[].triggers`）の照合 → JSON envelope 出力

### Constraints / boundaries

- **読み取り専用・副作用なし**: stdout JSON のみ
- **portfolio.yaml の解釈**: MVP では portfolio.yaml の解析・トリガー照合は **行わない**。あくまで複合スコア出力に専念する（トリガー照合は analyst レイヤーの責務）。`--portfolio-file` は単に「ティッカーを抽出するための便利機能」として実装可
- **rate-limit 配慮**: 5〜10 銘柄 × 5 API 呼び出し = 30〜50 calls。yfinance 既定で 1 分以内に完了
- **provider 規約**: yfinance を既定（key-free）。fmp は `--provider fmp` でオプトイン
- **FMP 再検証結果（2026-04-30 の second pass で消化完了、FMP_API_KEY 有料版設定済み）**:
  1. **決算カレンダー窓**: FMP は +90 日まで clean に動作（10,559 行取得、403 poisoning なし）。ただしスペックバスケットの +45 日 / +90 日の該当銘柄は完全に一致（ASC / CMCL / SM hit; FLXS / TLT / LQD は 90 日窓でも earnings なし）。追加の 45 日は Req 7.2 の 5 営業日閾値に対して運用上の便益を生まないため、デフォルトは両プロバイダとも `N=45` で固定。`--earnings-window-days 90` を opt-in で使える escape hatch として残す（FMP 経由でのみ clean、yfinance 経由は nasdaq ceiling に阻まれるため使用不可）。Decision 7 記録済み
  2. **`quote.last_price` の欠損**: FMP は債券 ETF（TLT、LQD）で `last_price` を正常取得（TLT `85.70`, LQD `108.73`）。ただし Req 4.7 のフォールバックチェーンは **両プロバイダで維持**（FMP では first rung が成功するので no-op、yfinance では bond ETF を救う、将来的な provider-specific null に対するレジリエンスも保持）。Decision 9 記録済み
  3. **OpenBB 技術指標のカラム名**: FMP 経由の `history.results` に対しても `close_RSI_14` / `close_MACDh_12_26_9` / Clenow の string `factor` は完全に invariant（AAPL で numeric drift ≤0.5%、列名は完全一致）。suffix-search + `_to_float` パターンはプロバイダ非依存で確定。Decision 8 記録済み
- **FMP 再検証で新たに判明した事項（要件に追加反映済み）**:
  4. **quote の MA / 出来高フィールド名差異**: FMP quote は `ma200` / `ma50`（アンダースコアなし、`d` サフィックスなし）で 200日・50日 MA を返し、`volume_average` と `volume_average_10d` は両方 `None`。Req 4.1 でプロバイダ別フィールドリゾルバを mandate、Req 5.7 で FMP 時の `volume_reference` null 許容 + `volume_reference_unavailable_on_provider` フラグを mandate、Req 9.10 クローズド列挙に同フラグを追加済み。Decision 11 記録済み
- **出来高ラベル明示**: 出力に `volume_avg_window: "20d_real"` を必ず付与し、yfinance `volume_average`（3-month rolling）との混同を恒久排除（reviewer R1 取り込み）

### Risks / open questions

1. **複合 z-score の重み**: 初期値は仮置き、`/sdd-spec-design` で詰める。短期戦略は「長期/中期で選定済みの銘柄」前提なので、サンプル数が少ない（5〜10 銘柄）と z-score の分散が不安定になりうる。**最低 10 銘柄要求するか、絶対値ベースのスコアリング併用を検討**
2. **マクロ象限整合**: policy §3 短期戦略では「マクロ象限整合」も判断軸の一つだが、本ツールは ticker-level に閉じる（マクロは analyst レイヤーで加味）。SKILL.md にこの境界を明示
3. **エントリー適性 vs エグジット適性**: スコアが高いほど買い場と解釈されるが、保有銘柄では同スコアが「保有継続シグナル」になる。output に `interpretation_hint: 'low_score=better_entry'` を付与して誤読を防ぐ
4. **JP 株対応**: `quote.py` / `historical.py` / `momentum.py` は `.T` サフィックスで日本株対応済み、`fmp` 系の制約を受けない。MVP から JP 対応可

### Success criteria（MVP 完了の定義案）

- `uv run scripts/entry_timing_scorer.py ASC CMCL FLXS SM TLT LQD` が exit 0 で複合スコア・派生指標・出来高ラベル付きの JSON を出力
- 出力エンベロープが `_envelope/SKILL.md` 規約に準拠
- 既存統合テスト規約（`tests/integration/test_json_contract.py`）に通る
- `volume_avg_window` 明示で reviewer R1 を恒久解決
- analyst 日次保有監視ループから 1 コマンドで呼べる状態
- analyst の memory.md「ASC E2 発火条件チェックリスト」相当が、本ツール出力 + 軽量フィルタで自動生成可能になる
- 出力に `next_earnings_date`, `days_to_next_earnings`, `earnings_proximity_warning` を含み、analyst が決算前後 5 営業日のエントリー判断を機械的に検出できる

### Out of scope

- portfolio.yaml の解析・更新・トリガー照合（analyst の責務）
- 売買シグナルの最終判断（最終意思決定者はユーザー）
- バックテスト
- マクロ象限の取得・統合（analyst レイヤー）
- アラート通知（Discord 通知は analyst 側）

### Priority

**高**（analyst memory の 5 月上旬 §11 起案候補との比較で）

- 日次運用に直接効く（毎日繰り返している保有監視テーブルの自動化）
- reviewer R1（出来高ラベル）の恒久解決
- `sector-stock-screener` より実装コストが小さい（既存 wrapper の合成のみ、新規 API 呼び出しなし）
- multibagger-alchemy / Piotroski F-Score 拡張（5 月上旬予定）と独立で並列実装可能

ユーザー指示（2026-04-28）「中期 / 短期スコアリングが進んでいない、ツール拡張または専用エージェントで対策が必要」への直接的回答。

### Implementation order recommendation

`entry-timing-scorer` 先行 → `sector-stock-screener` 後行 を推奨：

1. 短期スコアリングは日次に効くため即効性が高い
2. 実装コスト最小（既存 wrapper の合成、新規 OpenBB API なし）
3. reviewer R1 恒久解決を早期に達成
4. `sector-stock-screener` は四半期サイクル（次回 7 月第1週）まで猶予あり

## Introduction

`entry-timing-scorer` is a new CLI wrapper at `scripts/entry_timing_scorer.py` that accepts a short list of tickers (typically 5–10 names already selected by the long/mid-term strategy) and emits per-ticker entry-timing analytics built from five signals: 6-month Clenow momentum, 52-week range position, RSI(14), MACD histogram, and a true 20-day volume z-score. It is a thin composition wrapper — it re-uses `obb.equity.price.quote`, `obb.equity.price.historical`, `obb.technical.{clenow,rsi,macd}`, and `obb.equity.calendar.earnings`, and adds only the local computations (range position, volume z, sub-score composition) and a shared envelope. The tool directly automates the daily holdings-monitoring table the analyst agent currently assembles by hand and permanently removes the volume-label ambiguity flagged in reviewer R1 (FLXS 4/27) by tagging the 20-day window explicitly.

### Analytical stance

The tool is designed to enable **accurate per-ticker analysis**, not to issue a single buy/sell number. Four design rules follow from that stance and are enforced by the requirements below:

- **Trend and mean-reversion signals are separated**, not blended into one opaque score. Clenow momentum, MACD histogram, and volume breakout feed a `trend_score`; 52-week range position and RSI feed a `mean_reversion_score`. A `blended_score` is emitted only as a reference under an explicit blend profile that the caller selects.
- **Every score is a basket-internal rank summary**, not an absolute strength reading. With n=5–10 tickers a cross-sectional z-score collapses to a monotonic transform of within-basket rank; the tool must document this caveat in its output and in its `SKILL.md` so consumers do not interpret "70 points" as "strong signal".
- **Per-ticker context (watchlist vs holding) changes the reading** of the same score. The tool accepts context per ticker and emits a structured interpretation block instead of a single polarity hint.
- **Thresholds and estimators that affect conclusions are configurable**, so the tool can be audited and re-tuned without code changes. This covers the earnings-proximity window and the volume z-score estimator choice.

Feasibility is confirmed: every data primitive is already live (see the table in §Existing primitives), and `sector_score.py` is the direct architectural precedent for the multi-ticker z-score blend, weighted composition, partial-failure handling, and `aggregate_emit` envelope. Usefulness is confirmed by the three concrete daily pains this tool replaces (manual quote+RSI+MACD table, manual 52-week breakout check, recurring volume-label confusion) and by the policy alignment with `policy.md` §3 short-term strategy. Out-of-scope items (macro-quadrant integration, portfolio-trigger matching, notifications) are enforced as non-requirements below to protect the thin-wrapper scope.

## Requirements

### Requirement 1: CLI input and ticker resolution

**Objective:** As an AI analyst agent, I want to pass either an inline ticker list or a portfolio file to the tool, so that I can invoke it uniformly from the daily holdings-monitoring loop and from ad-hoc watchlist checks.

#### Acceptance Criteria

1. When the user passes `--tickers <CSV>` on the command line, the entry-timing scorer shall parse the comma-separated string into a list of unique ticker symbols preserving input order.
2. When the user passes `--portfolio-file <path>` pointing at a YAML document with top-level `positions[].ticker` and `watchlist[].ticker` fields, the entry-timing scorer shall extract the union of those tickers and treat the result as the input list.
3. When both `--tickers` and `--portfolio-file` are supplied, the entry-timing scorer shall exit with error_category `validation` and a non-zero exit code without calling any OpenBB endpoint.
4. When neither `--tickers` nor `--portfolio-file` is supplied, the entry-timing scorer shall exit with error_category `validation` and a message identifying the missing argument.
5. If a ticker symbol contains the `.T` suffix, the entry-timing scorer shall forward it unchanged to every OpenBB call so that Japanese equities work without a `--provider` override.
6. If the resolved ticker list is empty after deduplication, the entry-timing scorer shall exit with error_category `validation` and a message stating that at least one ticker is required.
7. When the user passes `--portfolio-file <path>`, the entry-timing scorer shall tag each extracted ticker with `context: "holding"` for tickers drawn from `positions[]` and `context: "watchlist"` for tickers drawn from `watchlist[]`, so that downstream interpretation can distinguish entry candidates from hold/exit candidates.
8. When the user passes `--tickers <CSV>` without `--portfolio-file`, the entry-timing scorer shall accept an optional `--context {watchlist|holding|unknown}` flag defaulting to `unknown` and apply that value uniformly to every ticker in the input list.
9. When the same ticker appears in both `positions[]` and `watchlist[]` of the portfolio file, the entry-timing scorer shall resolve it to `context: "holding"` and emit a data-quality flag `"context_duplicate_positions_and_watchlist"` on that ticker row.

### Requirement 2: Provider selection and key-free default

**Objective:** As an operator running in CI and local environments, I want the tool to default to a key-free provider, so that routine runs do not require credential configuration and paid providers stay opt-in.

#### Acceptance Criteria

1. When `--provider` is omitted, the entry-timing scorer shall use `yfinance` for every OpenBB equity call (`quote`, `historical`, `clenow`, `rsi`, `macd`) and `nasdaq` for the earnings-calendar call (the keyless default path).
2. Where the user passes `--provider fmp`, the entry-timing scorer shall route **every OpenBB call** to `fmp` — including the earnings-calendar call — so that FMP-credentialled callers benefit from FMP's higher row counts (+45d: 8,383 vs nasdaq 3,550) and from FMP's ability to clear the full `--earnings-window-days 90` horizon cleanly without the nasdaq 403-poisoning ceiling. This per-provider routing is a direct consequence of the FMP re-verification findings (Live findings L7 / L9 in `research.md`): FMP is safe for both equity and calendar paths, and pinning the calendar to nasdaq under `--provider fmp` would re-import the nasdaq ceiling for no benefit.
3. The entry-timing scorer shall restrict `--provider` to the closed choice set `{yfinance, fmp}` via argparse.
4. If any OpenBB call raises a provider exception, the entry-timing scorer shall propagate the exception through `safe_call` so the failure carries an `error_category` drawn from `ErrorCategory` (`credential` / `plan_insufficient` / `transient` / `validation` / `other`).

### Requirement 3: Earnings-calendar single-shot fetch

**Objective:** As an AI analyst agent, I want the tool to retrieve upcoming earnings dates for all input tickers in one call, so that the tool stays inside the rate-limit budget and the 5-business-day earnings-proximity flag can be computed mechanically.

**Provider-ceiling note (live-verified 2026-04-30, both keyless and FMP-credentialled paths):** nasdaq's earnings-calendar endpoint returns `AttributeError: NoneType.get` at windows ≥ ~56 days and HTTP 403 at ≥ ~84 days; once the 403 fires it poisons every subsequent calendar call for the lifetime of the Python process (a fresh process is required to recover). The original 90-day window mandated by the pre-verification draft of this requirement is therefore not achievable against the keyless nasdaq provider. Defaults below are shortened to 45 days, which sits safely inside the working band (+49d returned 3550 rows) and still covers the next quarterly cycle for actively-reporting tickers — the only horizon the analyst layer acts on (the proximity threshold in Req 7.2 is 5 calendar days). **FMP re-verification (2026-04-30, second pass with `FMP_API_KEY` configured) resolved:** `provider="fmp"` clears the full +90d window cleanly (no ceiling, no 403 poisoning; +90d returned 10,559 rows). However, a basket-coverage parity check against the spec basket (`ASC CMCL FLXS SM TLT LQD`, `TODAY=2026-04-30`) showed **identical** hits at +45d and +90d — the extra 45 days buys no additional proximity detection for the 5-calendar-day threshold. The default therefore stays `N=45` under **both** providers; `--earnings-window-days` is the opt-in escape hatch for callers who want a wider horizon (e.g., quarterly-cycle screening at `--earnings-proximity-days 60`). The `[1, 90]` bounds and "no in-process retry" invariants stand unchanged. The `research.md` Decision 7 records the rationale for rejecting a provider-conditional default.

#### Acceptance Criteria

1. When resolving upcoming earnings, the entry-timing scorer shall call `obb.equity.calendar.earnings(start=<TODAY>, end=<TODAY + N days>, provider=<calendar_provider>)` exactly once per invocation regardless of ticker count, where `calendar_provider` is `"nasdaq"` under the default `--provider yfinance` and `"fmp"` under `--provider fmp` (per Req 2), and where `N` defaults to `45` to stay inside the live-verified nasdaq working window under yfinance routing and is overridable via `--earnings-window-days` (bounded `[1, 90]` per Req 3.7; the full 90-day ceiling is only reliably reachable under FMP routing).
2. When the earnings calendar response is received, the entry-timing scorer shall filter rows to the input-ticker set before deriving per-ticker fields.
3. When a ticker has at least one earnings row in the active window, the entry-timing scorer shall set `next_earnings_date` to the earliest `report_date` at or after `TODAY`.
4. When a ticker has no earnings row in the active window, the entry-timing scorer shall set `next_earnings_date`, `days_to_next_earnings`, and `earnings_proximity_warning` to `null`.
5. If the earnings-calendar call fails, the entry-timing scorer shall still emit a per-ticker row for each input ticker with `next_earnings_date: null` and shall record the calendar failure under `data.provider_diagnostics` with `{provider, stage: "earnings_calendar", error, error_category}`.
6. The entry-timing scorer shall emit `days_to_next_earnings_unit: "calendar_days"` at the `data` level so consumers do not mis-read the field as business days.
7. The entry-timing scorer shall expose `--earnings-window-days <N>` defaulting to `45` (calendar days), restrict `N` to integers in `[1, 90]` via argparse, echo the active value under `data` as `earnings_window_days` so consumers can audit it, and shall not retry a failed calendar fetch inside the same invocation (the nasdaq 403 failure mode persists across retries until the Python process restarts; FMP has no comparable ceiling but the no-retry invariant is kept universal for provider-agnostic behavior and because transient failures on any provider surface cleanly via `error_category: "transient"` for the operator to retry at the CLI level).

### Requirement 4: Per-ticker data acquisition

**Objective:** As an AI analyst agent, I want a single CLI invocation to gather all five signal inputs per ticker, so that the daily monitoring table is produced by one command instead of five separate wrapper calls.

**OpenBB output-shape note (live-verified 2026-04-30 against both yfinance and FMP paths):** the three `obb.technical.*` endpoints do not expose the clean field names the pre-verification draft assumed. `obb.technical.clenow` returns `factor` as a **string** (observed: `"0.63826"` on yfinance, `"-0.02842"` on FMP), not a float; arithmetic on it raises without an explicit coercion step. `obb.technical.rsi` emits the output column as `close_RSI_14` (not `rsi`). `obb.technical.macd` emits `close_MACDh_12_26_9` / `close_MACDs_12_26_9` / `close_MACD_12_26_9` (not `histogram` / `signal` / `macd`). The suffix/substring-search pattern already used by `scripts/momentum.py::_last_with` and the `_to_float` coercion already used by `scripts/sector_score.py::fetch_clenow` are the authoritative precedents. These shape quirks are provider-level artifacts of the technical library OpenBB bundles, not of yfinance itself, so they apply identically regardless of which price provider feeds `obb.equity.price.historical`. **FMP re-verification (2026-04-30) resolved:** the `provider="fmp"` path was exercised end-to-end and produced the **identical** output-column set (`close_RSI_14`, `close_MACDh_12_26_9`, `factor`-as-string); FMP-fed historical carries extra pass-through columns (`symbol`, `change`, `change_percent`, `vwap`) but the technical stack adds the same suffix columns regardless. Numeric values drift slightly (~0.07% on RSI, ~0.5% on MACD-histogram) due to FMP's adjusted-close values differing from yfinance's — expected and immaterial. No provider-specific branching is required for technical-indicator extraction.

**Quote-endpoint `last_price` note (live-verified 2026-04-30 against both yfinance and FMP):** `obb.equity.price.quote(provider="yfinance")` returns `last_price=None` for bond ETFs (TLT and LQD observed; `prev_close`, `open`, `high`, `low`, `year_high`, `year_low`, `ma_200d`, `ma_50d` were all populated for the same calls). Without a fallback, `range_pct_52w` and `ma200_distance` silently collapse to `null` for every bond-ETF basket member. **FMP re-verification (2026-04-30) resolved:** `provider="fmp"` populates `last_price` for the same bond ETFs cleanly (TLT `85.70`, LQD `108.73`). The fallback could therefore be scoped to "yfinance only", but Decision 9 in `research.md` keeps the fallback **universal** as a resilience layer — on FMP the fallback is a no-op (the first rung succeeds), on yfinance it keeps bond ETFs usable, and the single code path survives future provider-specific `last_price` nulls (halted sessions, pre-market partials, other asset classes). The fallback therefore applies universally, regardless of `--provider`.

**Quote-endpoint MA/volume field-name note (live-verified 2026-04-30; FMP second pass):** `obb.equity.price.quote(provider="yfinance")` exposes the 200d / 50d moving averages under `ma_200d` / `ma_50d` and the 3-month / 10-day average volumes under `volume_average` / `volume_average_10d`. `obb.equity.price.quote(provider="fmp")` exposes the **same moving-average data** under `ma200` / `ma50` (no underscore, no `d` suffix — numerically identical values), and returns `None` for both `volume_average` and `volume_average_10d` (FMP does not populate volume-average fields in its quote response). A wrapper that hard-codes the yfinance keys silently loses `ma200_distance` and the `volume_reference` block for every FMP-fed ticker without firing any data-quality flag. Req 4.1 mandates a provider-aware field resolver; Req 5.7 permits null `volume_reference` under FMP with a `volume_reference_unavailable_on_provider` data-quality flag (Req 9.10 catalog). Decision 11 in `research.md` records the rationale and the key-map pattern.

#### Acceptance Criteria

1. For every input ticker, the entry-timing scorer shall call `obb.equity.price.quote` once and extract `last_price`, `year_high`, `year_low`, the 200-day and 50-day moving averages, and the provider's native 3-month-rolling and 10-day average volumes, **through a provider-aware field-name resolver** that maps logical field names to the actual quote-record keys per `--provider`: under `--provider yfinance` the resolver reads `ma_200d`, `ma_50d`, `volume_average`, `volume_average_10d` verbatim; under `--provider fmp` it reads `ma200` and `ma50` for the moving averages (numerically identical to yfinance's `ma_200d` / `ma_50d`) and shall treat `volume_average` / `volume_average_10d` as unavailable (both logical fields resolve to `None`). The resolver shall be a closed-choice map aligned with the `{yfinance, fmp}` provider set defined in Req 2.3; adding a provider requires extending the map explicitly. The extracted values shall be emitted under the logical names `ma_200d`, `ma_50d`, `volume_average`, `volume_average_10d` in the per-ticker `signals` block (Req 9.3), regardless of which provider-native key they were read from, so downstream consumers see a single consistent schema.
2. For every input ticker, the entry-timing scorer shall call `obb.equity.price.historical` with a lookback of at least 140 trading days so that the 20-day volume window and the RSI(14) warm-up period are both satisfied.
3. For every input ticker, the entry-timing scorer shall compute Clenow momentum via `obb.technical.clenow` with `period=126` (approximately six months of trading days) and coerce the returned `factor` (emitted by OpenBB as a stringified float) to a numeric `clenow_126` field via the `_to_float` helper pattern used in `scripts/sector_score.py::fetch_clenow`; a non-numeric or missing `factor` shall emit `clenow_126: null`.
4. For every input ticker, the entry-timing scorer shall compute RSI(14) via `obb.technical.rsi` with `length=14` and extract the RSI value from the output record's `close_RSI_14` column (case-insensitive suffix match on `"RSI"` to tolerate minor OpenBB column-naming drift), emitting it as `rsi_14`; when no matching column is present, `rsi_14` shall be `null`.
5. For every input ticker, the entry-timing scorer shall compute MACD via `obb.technical.macd` with `fast=12`, `slow=26`, `signal=9` and extract the histogram value from the output record's `close_MACDh_12_26_9` column (case-sensitive suffix match on `"MACDh"`, which avoids the `MACD` / `MACDs` / `MACDh` collision) into a `macd_histogram` field; when no matching column is present, `macd_histogram` shall be `null`.
6. The entry-timing scorer shall guard every OpenBB call with `_common.safe_call` so stdout-borne provider warnings are absorbed and failures become structured `{ok:false, error, error_type, error_category}` records.
7. The entry-timing scorer shall resolve the per-ticker `last_price` used in Req 5.1 / 5.2 derivations via the fallback chain `quote.last_price → quote.prev_close → historical[-1].close`; when the first rung is null the wrapper shall fall through to the next rung and append a per-ticker `data_quality_flags` entry identifying which rung was used (`"last_price_from_prev_close"` or `"last_price_from_historical_close"`). When every rung is unavailable `last_price` shall be `null` and a `"last_price_unavailable"` flag shall be appended.
8. For every input ticker, the entry-timing scorer shall issue the five OpenBB calls from Req 4.1–4.5 as a single sequence (one `quote` + one `historical` + three technicals consuming `data=history.results`), meaning the per-ticker OpenBB-call count is exactly five; the wrapper shall not re-fetch `historical` to compute rsi/macd separately.

### Requirement 5: Derived indicators — range, MA, and true 20-day volume

**Objective:** As an AI analyst agent, I want the tool to compute the local indicators the policy requires — including the true 20-day volume z-score that is not exposed by any provider — so that I stop recomputing them by hand.

#### Acceptance Criteria

1. When `last_price` (resolved via Req 4.7's fallback chain), `year_high`, and `year_low` are all present, the entry-timing scorer shall compute `range_pct_52w = (last_price - year_low) / (year_high - year_low)` and emit it per ticker.
2. When `last_price` (resolved via Req 4.7's fallback chain) and `ma_200d` are both present, the entry-timing scorer shall compute `ma200_distance = (last_price - ma_200d) / ma_200d` and emit it per ticker.
3. When the historical response yields at least 21 trading days, the entry-timing scorer shall compute a trailing-20-session volume z-score `volume_z_20d` on the latest session excluding that session from the reference window.
4. The entry-timing scorer shall expose a `--volume-z-estimator {robust|classical}` flag defaulting to `robust`, where `robust` applies `(log(latest_volume) - median(log(volume[-20:]))) / (1.4826 * MAD(log(volume[-20:])))` and `classical` applies `(latest_volume - mean(volume[-20:])) / stdev(volume[-20:])`; the output shall carry `volume_z_estimator` at the per-ticker level echoing the active choice.
5. If the chosen estimator's denominator is zero, any `volume[-20:]` entry is non-positive (blocking the log transform under `robust`), or the historical response contains fewer than 21 rows, the entry-timing scorer shall emit `volume_z_20d: null` and append a per-ticker `data_quality_flag` entry naming the missing input (for example `"volume_window_too_short"`, `"volume_zero_dispersion"`, `"volume_non_positive"`).
6. The entry-timing scorer shall emit `volume_avg_window: "20d_real"` at the per-ticker level for every row that reports `volume_z_20d`, so reviewer R1's 20-day-vs-3-month ambiguity cannot recur downstream.
7. Where the provider-aware resolver in Req 4.1 produces non-null values for `volume_average` (3-month rolling) or `volume_average_10d` (typically under `--provider yfinance`), the entry-timing scorer shall forward those values verbatim into a sibling `volume_reference` block labelled `{window: "3m_rolling"}` and `{window: "10d"}` respectively so the provider-native fields remain distinct from the locally computed 20-day window. When either value resolves to `None` (typically under `--provider fmp`, which does not populate volume-average fields in its quote response), the entry-timing scorer shall emit the corresponding `volume_reference` entry with `value: null` preserving the `window` label, and append a per-ticker `data_quality_flags` entry `"volume_reference_unavailable_on_provider"` exactly once per row (even if both the 3-month and 10-day values are null). The locally computed `volume_z_20d` (Req 5.3 / 5.4) is computed from `historical.results` independently and remains unaffected by this provider-native absence.

### Requirement 6: Sub-scores and optional blended score

**Objective:** As an AI analyst agent, I want trend-following and mean-reversion evidence surfaced separately, so that strongly-trending names and oversold bounce candidates are distinguishable instead of being averaged into a neutral composite.

#### Acceptance Criteria

1. The entry-timing scorer shall compute per-signal cross-sectional z-scores over the input-ticker set for `clenow_126`, `macd_histogram`, `volume_z_20d`, `(1 - range_pct_52w)`, and `(50 - rsi_14)`.
2. The entry-timing scorer shall compute a `trend_z` as the weight-normalized sum of `z(clenow_126)`, `z(macd_histogram)`, and `z(volume_z_20d)`, and a `mean_reversion_z` as the weight-normalized sum of `z(1 - range_pct_52w)` and `z(50 - rsi_14)`, using the sum-of-available-weights normalization pattern from `sector_score.py` so a missing signal degrades gracefully.
3. The entry-timing scorer shall expose weight flags within each sub-score (`--weight-trend-clenow`, `--weight-trend-macd`, `--weight-trend-volume`, `--weight-meanrev-range`, `--weight-meanrev-rsi`) with defaults `0.50`, `0.25`, `0.25` for trend and `0.60`, `0.40` for mean-reversion, so the internal composition of each sub-score is auditable and tunable without code changes.
4. The entry-timing scorer shall map each sub-score z to 0-100 via `clip(50 + z * 25, 0, 100)` and emit them as `trend_score_0_100` and `mean_reversion_score_0_100` (matching the `sector_score.py` transform).
5. The entry-timing scorer shall expose `--blend-profile {trend|mean_reversion|balanced|none}` defaulting to `none`, and compute `blended_score_0_100` only when the profile is not `none`; under `balanced`, weights shall be 0.5/0.5 between trend and mean-reversion sub-scores; under `trend` and `mean_reversion`, the blend shall emit the corresponding sub-score verbatim so the caller has a single consistent field regardless of stance.
6. When `--blend-profile none` is active, the entry-timing scorer shall omit `blended_score_0_100` from per-ticker rows (rather than emitting `null`) so consumers do not accidentally read a missing blend as a "score of zero".
7. If fewer than three tickers carry a non-null value for a given signal, the entry-timing scorer shall emit `null` for that signal's z and flag every affected row with a data-quality entry `"basket_too_small_for_z(<signal>)"`; the surrounding sub-scores shall still be computed from the available signals under the same weight-normalization rule.
8. If the entire input basket has fewer than three tickers resolved successfully, the entry-timing scorer shall set `trend_z`, `trend_score_0_100`, `mean_reversion_z`, `mean_reversion_score_0_100`, and any `blended_score_0_100` to `null` for every row, append a top-level warning `{symbol: null, error: "insufficient basket size for cross-sectional z-score", error_category: "validation"}`, and still emit the raw per-ticker indicators so the operator can inspect the underlying data.
9. The entry-timing scorer shall sort `data.results[]` by `trend_score_0_100` descending with `null` scores sinking to the bottom when `--blend-profile` is `none` or `trend`, by `mean_reversion_score_0_100` descending when the profile is `mean_reversion`, and by `blended_score_0_100` descending when the profile is `balanced` — so the top of the list always matches the active analytical stance.
10. The entry-timing scorer shall include a per-ticker `z_scores` block containing both the individual signal z-scores and the two sub-score z-scores, so operators can audit every step of the composition.
11. Every per-ticker row shall carry `basket_size` (the count of tickers that entered cross-sectional normalization) and `basket_size_sufficient` (boolean, true iff `basket_size >= 3`) so consumers can filter for statistically-usable rows without re-counting the basket.

### Requirement 7: Earnings-proximity flag kept outside the composite

**Objective:** As an AI analyst agent, I want earnings-proximity risk surfaced as an independent flag rather than blended into the score, so that "earnings close + momentum high" does not mute to a neutral score and post-earnings drift stays readable (FLXS-type case).

#### Acceptance Criteria

1. The entry-timing scorer shall not include `days_to_next_earnings` or any earnings-derived term in the trend, mean-reversion, or blended scores defined in Requirement 6.
2. The entry-timing scorer shall expose `--earnings-proximity-days <N>` defaulting to `5` and treat the argument as a calendar-day threshold; the active threshold shall be echoed under `data` as `earnings_proximity_days_threshold` so consumers can audit the active configuration.
3. When `days_to_next_earnings` is present and less than or equal to the configured threshold, the entry-timing scorer shall emit `earnings_proximity_warning: true` on that ticker row.
4. When `days_to_next_earnings` is greater than the configured threshold or is `null`, the entry-timing scorer shall emit `earnings_proximity_warning: false`.
5. The entry-timing scorer shall emit the raw `next_earnings_date` (ISO date string or `null`) and `days_to_next_earnings` (integer or `null`) alongside the flag so consumers can apply a different threshold without re-fetching the calendar.
6. If `--earnings-proximity-days` is negative or non-integer, the entry-timing scorer shall exit with error_category `validation` before issuing any OpenBB call.

### Requirement 8: Shared envelope and JSON contract compliance

**Objective:** As the JSON-contract integration test, I want the new wrapper to pass `tests/integration/test_json_contract.py` unmodified, so that envelope regressions are caught automatically.

#### Acceptance Criteria

1. The entry-timing scorer shall emit stdout via `_common.aggregate_emit` with `tool="entry_timing_scorer"` so the root keys are exactly `{source, collected_at, tool, data}` plus the optional `{warnings, error, error_category, details}` slots.
2. The entry-timing scorer shall place per-ticker rows under `data.results[]` and per-query metadata (`provider`, `weights`, `tickers`, `days_to_next_earnings_unit`, `missing_tickers`, optional `provider_diagnostics`) as siblings of `results` under `data`.
3. The entry-timing scorer shall shape per-row failures as `{symbol, provider, ok: false, error, error_type, error_category}` exactly as `sector_score.py` does, and mirror each failure into top-level `warnings[]` via `aggregate_emit`.
4. If every input ticker fails with the same fatal `error_category` (`credential` or `plan_insufficient`), the entry-timing scorer shall exit with code 2 and a top-level `{error, error_category}` payload (the behavior `aggregate_emit` already provides).
5. The entry-timing scorer shall exit with code 0 on full success and on any partial-failure mix.
6. The entry-timing scorer shall sanitize all NaN / Infinity floats to `null` via `_common.emit` / `sanitize_for_json` so downstream parsers (jq, Node, Go) remain strict-JSON compliant.
7. The entry-timing scorer shall keep stdout a single JSON document and send any traceback to stderr, matching the cross-wrapper invariant enforced by `tests/integration/test_json_contract.py`.

### Requirement 9: Per-ticker output schema

**Objective:** As an AI analyst agent reading the JSON programmatically, I want every ticker row to carry the same named fields with context-aware interpretation metadata, so that I can traverse the response without schema inference and without silently inverting the score direction.

#### Acceptance Criteria

1. The entry-timing scorer shall emit, for every input ticker, a row containing at minimum: `symbol`, `provider`, `ok`, `context`, `rank`, `trend_score_0_100`, `mean_reversion_score_0_100`, `signals`, `z_scores`, `basket_size`, `basket_size_sufficient`, `next_earnings_date`, `days_to_next_earnings`, `earnings_proximity_warning`, `volume_avg_window`, `volume_z_estimator`, `volume_reference`, `data_quality_flags`, and `interpretation`.
2. When `--blend-profile` is not `none`, every row shall additionally carry `blended_score_0_100` and `blend_profile` (echoing the active profile).
3. The entry-timing scorer shall populate `signals` with `{clenow_126, range_pct_52w, rsi_14, macd_histogram, volume_z_20d, ma200_distance, last_price, year_high, year_low, ma_200d, ma_50d, latest_volume}` so the underlying inputs are auditable from the same row.
4. The entry-timing scorer shall emit `interpretation` as an object containing at minimum `{score_meaning: "basket_internal_rank", trend_polarity: "high=stronger_trend", mean_reversion_polarity: "high=more_oversold", context: <ticker context>, reading_for_context: <string>}`, where `reading_for_context` is `"entry_candidate_if_high_scores"` for `watchlist`, `"hold_or_add_if_high_trend,reconsider_if_high_mean_reversion"` for `holding`, and `"ambiguous_without_context"` for `unknown`.
5. The entry-timing scorer shall never emit a single field named `interpretation_hint` that asserts a fixed score-direction, because the reading depends on both `context` and which sub-score is high; this is a negative invariant that the JSON-contract test shall check via field absence.
6. If `rsi_14` is less than 20, the entry-timing scorer shall append `"rsi_oversold_lt_20"` to `data_quality_flags[]` on that row.
7. If a row's `basket_size_sufficient` is `false`, the entry-timing scorer shall append `"basket_too_small_for_z"` to `data_quality_flags[]` on that row.
8. When a per-ticker row has `ok: false`, the entry-timing scorer shall omit `trend_score_0_100`, `mean_reversion_score_0_100`, `blended_score_0_100`, and `z_scores` from that row, while still populating `symbol`, `provider`, `context`, `error`, `error_type`, and `error_category`.
9. Under `data`, the entry-timing scorer shall emit an `analytical_caveats` string array containing at minimum the entries `"scores_are_basket_internal_ranks_not_absolute_strength"`, `"trend_and_mean_reversion_are_separate_axes"`, and `"earnings_proximity_is_flag_not_score_component"`, so the caveats travel with every response instead of living only in documentation.
10. The `data_quality_flags[]` array on each per-ticker row shall draw exclusively from the closed enumeration `{"rsi_oversold_lt_20", "basket_too_small_for_z", "basket_too_small_for_z(clenow_126)", "basket_too_small_for_z(macd_histogram)", "basket_too_small_for_z(volume_z_20d)", "basket_too_small_for_z(range_pct_52w)", "basket_too_small_for_z(rsi_14)", "volume_window_too_short", "volume_zero_dispersion", "volume_non_positive", "volume_reference_unavailable_on_provider", "last_price_from_prev_close", "last_price_from_historical_close", "last_price_unavailable", "context_duplicate_positions_and_watchlist"}`, so downstream consumers can enumerate the catalog without reverse-engineering the wrapper source.

### Requirement 10: Scope boundaries — what the tool must not do

**Objective:** As a reviewer enforcing the thin-wrapper architecture in `structure.md`, I want scope drift explicitly forbidden, so that portfolio orchestration, macro integration, and notification concerns stay in the analyst layer.

#### Acceptance Criteria

1. The entry-timing scorer shall not parse `portfolio.yaml` beyond extracting the ticker list under `positions[].ticker` and `watchlist[].ticker` (Requirement 1.2).
2. The entry-timing scorer shall not read, match, or emit exit-rule or trigger fields from `portfolio.yaml` (for example `exit_rules`, `triggers`, `targets`).
3. The entry-timing scorer shall not write to any file or network destination other than stdout and stderr.
4. The entry-timing scorer shall not fetch or blend macro-quadrant data; macro integration remains the analyst agent's responsibility.
5. The entry-timing scorer shall not emit alerts, Discord notifications, or side effects beyond JSON on stdout.
6. The entry-timing scorer shall not persist state between invocations (no cache files, no database, no log files written by the tool).

### Requirement 11: Skill documentation and INDEX update

**Objective:** As a future AI agent invoking this tool for the first time, I want a `SKILL.md` that tells me exactly how to call it, so that I do not guess flags or mis-read the envelope.

#### Acceptance Criteria

1. The entry-timing scorer change set shall add `skills/entry-timing-scorer/SKILL.md` in the same commit as `scripts/entry_timing_scorer.py` so the ChangeBundleRule stays satisfied.
2. The SKILL.md shall be written in English, stay within roughly 30-80 lines, and reference the shared `_envelope`, `_errors`, and `_providers` skills for cross-cutting policy rather than duplicating their prose.
3. The SKILL.md shall document every CLI flag (`--tickers`, `--portfolio-file`, `--context`, `--provider`, sub-score weight flags, `--blend-profile`, `--volume-z-estimator`, `--earnings-proximity-days`) with default values.
4. The SKILL.md shall include one short example invocation and one truncated output sample taken from an actual run, not from test fixtures.
5. The SKILL.md shall state the explicit scope boundaries (no portfolio-trigger matching, no macro-quadrant blend, no notifications) so downstream callers do not assume omitted capabilities.
6. The SKILL.md shall contain an **Interpretation** section that spells out the three caveats echoed in `data.analytical_caveats`: (a) scores are basket-internal rank summaries, not absolute strength readings; (b) `trend_score_0_100` and `mean_reversion_score_0_100` are separate axes — a name can rank high on both, and averaging them hides the split; (c) `earnings_proximity_warning` is a standalone gate, never a score component.
7. The SKILL.md shall include a short "Reading by context" subsection that explains how `context: "watchlist"` vs `context: "holding"` changes the reading of the same scores (entry candidacy vs hold/trim candidacy).
8. The entry-timing scorer change set shall add a matching row to `skills/INDEX.md` so the catalog stays in lock-step with the wrappers.

### Requirement 12: Integration-test coverage

**Objective:** As the project's verification gate, I want the new wrapper covered by the existing integration-test style, so that regressions are caught without bespoke test scaffolding.

#### Acceptance Criteria

1. The entry-timing scorer change set shall add at least one `pytest -m integration` test that invokes `uv run scripts/entry_timing_scorer.py --tickers <sample>` as a subprocess against the keyless default provider and asserts the envelope shape via `tests/integration/_sanity.py` helpers.
2. When the FMP key is absent, the entry-timing scorer's FMP-specific integration tests (if any) shall be auto-skipped so the free-tier `-m integration` run stays green.
3. The integration test shall assert that `data.results[]` is sorted by the score field that matches the active `--blend-profile`: `trend_score_0_100` under the default (`none`), `mean_reversion_score_0_100` under `mean_reversion`, and `blended_score_0_100` under `balanced`, with `null` scores always sinking to the bottom.
4. The integration test shall assert that `volume_avg_window == "20d_real"` on at least one non-failing row, locking reviewer R1's fix in place.
5. The integration test shall assert that `days_to_next_earnings_unit == "calendar_days"` under `data`, and that `earnings_proximity_days_threshold` is emitted under `data` and matches the CLI-supplied value.
6. The integration test shall assert that `data.analytical_caveats` contains all three required caveat strings defined in Requirement 9.9, and that no per-ticker row carries a field named `interpretation_hint` (Requirement 9.5 negative invariant).
7. The integration test shall assert that `trend_score_0_100` and `mean_reversion_score_0_100` are both present on every `ok: true` row and may legitimately be high simultaneously for the same ticker (they are separate axes, not complementary halves of one score).
8. The integration test shall run the tool twice against the same basket with `--volume-z-estimator robust` and `--volume-z-estimator classical`, and assert that `volume_z_estimator` in the output echoes the flag and that the two runs can produce different `volume_z_20d` values (confirming the estimator choice is wired through rather than silently ignored).
9. `tests/integration/test_verification_gate.py` shall continue to pass, which implies the new wrapper is documented in `README.md` §1-1 with a verification-test pointer in the same change set.
10. The integration test shall include a provider-parametrized slice that runs the tool against the same equity basket under `--provider yfinance` and `--provider fmp`, auto-skipping the FMP leg when `FMP_API_KEY` is absent (Req 12.2 pattern); for both providers the test shall assert that `signals.ma_200d` and `signals.ma_50d` are non-null on at least one `ok: true` equity row (confirming the provider-aware quote field resolver from Req 4.1 reads the correct key per provider); under `--provider fmp` the test shall additionally assert that every `ok: true` row carries `"volume_reference_unavailable_on_provider"` in `data_quality_flags[]` and that the `volume_reference` block emits `value: null` while preserving the `window` labels (Req 5.7 contract).
11. The integration test shall assert that `volume_z_20d` is non-null on at least one `ok: true` equity row **under both `--provider yfinance` and `--provider fmp`** (auto-skipping the FMP leg when `FMP_API_KEY` is absent), confirming that the locally computed 20-day volume z-score is provider-independent and that the FMP-specific `volume_reference_unavailable_on_provider` flag does not bleed into the scorer signal itself.

### Requirement 13: Performance and rate-limit budget

**Objective:** As an operator running the daily holdings-monitoring loop, I want a predictable runtime, so that the tool fits into the morning routine without special throttling.

#### Acceptance Criteria

1. When invoked with 10 tickers on the default provider, the entry-timing scorer shall complete within 90 seconds on a standard local network.
2. When invoked with a single ticker, the entry-timing scorer shall complete within 20 seconds on a standard local network.
3. The entry-timing scorer shall issue at most one earnings-calendar call per invocation (Requirement 3.1) and at most five equity/technical calls per ticker (Requirement 4).
4. The entry-timing scorer shall not introduce retry loops that multiply the per-ticker call count; transient failures stay a single `safe_call` attempt and surface as `error_category: "transient"` for the operator to retry at the CLI level.
