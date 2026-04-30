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
| 52w レンジ位置・MA200/MA50 | `quote.py` | **2026-04-28 実機確認**: XLE で `year_high=63.46`, `year_low=39.755`, `ma_200d=48.236923`, `ma_50d=57.3338` 取得成功 |
| OHLCV（実 20日平均出来高計算用） | `historical.py` | OK（analyst が ASC E2 判定で `--start YYYY-MM-DD --end YYYY-MM-DD` で利用中） |
| 6ヶ月クレナウ・モメンタム | `momentum.py --indicator clenow --period 126` | OK（既存 wrapper、period デフォルト 90 を 126 = 6m に変更可） |
| RSI(14) | `momentum.py --indicator rsi --length 14` | OK |
| MACD ヒストグラム | `momentum.py --indicator macd` | OK（`latest{macd, signal, histogram}` 出力済み） |
| 出来高 3ヶ月平均（参照用） | `quote.py.volume_average` | OK（rolling 3-month、yfinance 慣習） |
| 出来高 10日平均（参照用） | `quote.py.volume_average_10d` | OK |
| 出来高 20日平均（policy 想定） | `historical.py` の OHLCV から自前計算 | 要実装、`pandas.rolling(20).mean()` 等で計算 |
| 直近・次回決算日（earnings risk フラグ用） | `calendars.py --type earnings --start <TODAY> --end <TODAY+90d>`（`obb.equity.calendar.earnings`） | **2026-04-29 確認**: nasdaq provider keyless、出力 `{report_date, symbol, eps_consensus, eps_previous, num_estimates, reporting_time, market_cap}`（`shared/openbb/skills/calendars/SKILL.md`）。日付範囲指定の 1 ショット fetch でティッカー横断、ticker 単位の重複呼び出しは不要 |

すべての素材は既存ラッパーで取得可能であり、新規 OpenBB API 呼び出しは不要。**新規開発はこれらの合成・z-score 計算のみ**。

### Pipeline (initial design sketch)

1. `--tickers <CSV>` または `--portfolio-file <path>` でティッカーリストを受け取る
2. ティッカー横断で 1 ショット fetch（ticker 単位の重複呼び出しを避ける）：
   - `obb.equity.calendar.earnings(start=<TODAY>, end=<TODAY+90d>, provider="nasdaq")` → 入力ティッカー集合の直近・次回決算日。出力後に input ticker 集合で内部フィルタ
3. 各ティッカーについて以下を並列フェッチ：
   - `obb.equity.price.quote` → `last_price`, `year_high`, `year_low`, `ma_200d`, `ma_50d`, `volume_average`, `volume_average_10d`
   - `obb.equity.price.historical(period=140d)` → 直近 140 営業日 OHLCV（実 20 日平均出来高 + RSI 計算用バッファ）
   - `obb.technical.clenow(period=126)` → 6ヶ月モメンタム
   - `obb.technical.rsi(length=14)` → RSI(14)
   - `obb.technical.macd(fast=12, slow=26, signal=9)` → MACD histogram
4. 派生指標の計算：
   - `range_pct_52w = (last - year_low) / (year_high - year_low)`（policy 採用、低いほど割安）
   - `ma200_distance = (last - ma_200d) / ma_200d`
   - `volume_z_20d = (latest_volume - avg(historical[-20:].volume)) / std(historical[-20:].volume)`（**reviewer R1 で指摘された「20日平均」の正しい実装、yfinance `volume_average` の rolling 3-month と区別**）
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

## Requirements

<!-- Will be generated in /sdd-spec-requirements phase -->
