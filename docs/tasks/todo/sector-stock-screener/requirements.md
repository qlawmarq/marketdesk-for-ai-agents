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

すべて既存ラッパーで素材が揃っていることを実機で確認済み：

| 必要な素材 | 既存ラッパー | 検証結果 |
|---|---|---|
| Top sector ETFs ランキング | `sector_score.py --universe sector-spdr` | OK（`composite_score_0_100`, `rank` 出力済み） |
| ETF 構成銘柄 + 比率 | `etf.py SYMBOL --type holdings` (FMP Starter) | **2026-04-28 実機確認**: XLE で `symbol`, `weight`, `shares`, `value`, `updated` 取得成功（exit 0、provider=fmp） |
| 個別株 quote（52w 位置・MA） | `quote.py` | OK（`year_high`, `year_low`, `ma_200d`, `ma_50d` 全て取得可） |
| 個別株 fundamentals（Quality / Value） | `fundamentals.py --type metrics` (yfinance default) | OK（`market_cap`, `enterprise_to_ebitda`, `forward_pe`, `roe`, `gross_margin` など unit-tagged で出力） |
| クレナウ・モメンタム | `momentum.py --indicator clenow` | OK（`momentum_factor`, `r_squared` 出力済み） |
| アナリスト・コンセンサス目標株価（forward-looking valuation 用） | `estimates.py --type consensus`（`obb.equity.estimates.consensus`、yfinance default） | **2026-04-29 確認**: 出力 `{symbol, target_high, target_low, target_consensus, target_median, recommendation, recommendation_mean, number_of_analysts, current_price, currency}`（`shared/openbb/skills/estimates/SKILL.md`）。aggregate_emit で multi-ticker 一括取得対応、provider keyless（yfinance）|
| z-score / ランク計算 | `sector_score.py` 内の `zscore()` / `rank_desc()` | 流用または `_common.py` への昇格を検討 |

### Pipeline (initial design sketch)

1. `--universe` または `--tickers` で sector ETF ユニバースを受け取る（既存 `sector_score.py` と同シグネチャ）
2. 内部で `sector_score` のスコアリングロジックを実行 → top N セクター（既定 N=3）を確定
3. 各 top セクター ETF について `obb.etf.holdings(provider="fmp")` を呼び、`weight` 上位 M 銘柄を取得（既定 M=20）
4. 構成銘柄プールを deduplicate し、各銘柄について並列に：
   - `obb.equity.price.quote` → 52w レンジ位置、MA200 乖離
   - `obb.equity.fundamental.metrics` → P/E、EV/EBITDA、ROE、FCF Yield
   - `obb.technical.clenow(period=90)` → モメンタム
   - `obb.equity.estimates.consensus` → `target_consensus`, `number_of_analysts`（yfinance、aggregate_emit で multi-ticker バッチ可）
5. 銘柄ごとに複合 z-score を計算（重み付け既定値は議論余地あり、`/sdd-spec-design` で詰める）：
   - `+ z(clenow_90)` 30%（モメンタム）
   - `+ z(1/EV/EBITDA)` 25%（バリュー、trailing）
   - `+ z(roe)` 20%（クオリティ）
   - `+ z(1 - range_pct_52w)` 15%（レンジ位置 = 安く買える余地）
   - `+ z(ma_200d_distance)` 10%（200dMA 乖離 = ブレイクアウト圏）
   - `+ z(target_upside)` TBD%（**forward-looking valuation**、`target_upside = (target_consensus - last_price) / last_price`、`number_of_analysts < 3` の銘柄は NULL 扱いで合成対象外）

   注記: 上記 5 ファクター（clenow_90 / 1/EV-EBITDA / ROE / range_pct_52w / ma_200d_distance）はすべて **過去の価格・過去の P&L から導かれる backward-looking 指標**であり、policy.md §3 中期戦略が想定する「バリュエーションZスコア」のうち forward-looking 要素（アナリスト期待・consensus 改定）が完全に欠落していた。`target_upside` を 6 番目のファクターとして組み込むことでこのギャップを埋める。重み（TBD%）は `/sdd-spec-design` で他 5 ファクターと併せて再配分する（既存案 30/25/20/15/10 の合計 100% を再正規化）。なお analyst 改定モメンタム（finviz `--type price_target` ベース）は MVP では採用せず、将来拡張余地として残す（finviz は rate limit に弱く、MVP では yfinance consensus に絞ることでバッチ堅牢性を優先する）。
6. ランキングして JSON envelope で出力（`{ticker, rank, composite_score_0_100, signals{..., target_upside, target_consensus, number_of_analysts}, sector_origin: ETF, weight_in_etf, ok}`）

### Constraints / boundaries

- **読み取り専用・副作用なし**: `_envelope/SKILL.md` 規約に従い、出力は stdout JSON のみ。永続化は呼び出し側責任
- **rate-limit 配慮**: top 3 sector × 20 銘柄 = 最大 60 銘柄（重複あり）。FMP Starter 300/min なので順次フェッチで 1〜2 分以内
- **fallback**: `etf.holdings` が `plan_insufficient` を返した場合、wrapper は `error_category: plan_insufficient` を伝搬（既存の wrap 規約通り。FMP 課金プラン低下時の挙動を保証）
- **provider 規約**: `sector_score` 同様、free-tier 既定（finviz/yfinance）で実行可、`etf.holdings` のみ FMP 強制を `--help` と SKILL.md に明示

### Risks / open questions

1. **複合 z-score の重み**: 上記初期値は仮置き。reviewer フィードバックで `/sdd-spec-design` フェーズで詰める（過去の Yartseva 2025 / QMJ / Conservative Formula の重み実例を参照する余地あり）
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

### Priority

**中**（analyst memory の 5 月上旬 §11 起案候補との比較で）

- 5 月上旬の `multibagger-alchemy` 年次フルスクリーニング再実行 → 長期候補
- 5 月上旬の Piotroski F-Score MVP（multibagger-alchemy 拡張）→ 長期品質ゲート
- **本タスク**（sector-stock-screener）→ 中期セクターローテーション
- 並行する `entry-timing-scorer` → 短期エントリータイミング、優先度高（日次運用に直接効く）

ユーザーから「中期 / 短期スコアリングが進んでいない」と明示問題提起あり（2026-04-28）、analyst 提案で本タスクと entry-timing-scorer を 2 本立てで起案する方針で合意済み。

## Requirements

<!-- Will be generated in /sdd-spec-requirements phase -->
