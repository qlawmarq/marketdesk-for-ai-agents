# Institutional Ownership SKILL（FMP 13F）実装要件

起票日: 2026-04-30
起票者: investment-analyst
承認: reviewer 2026-04-30 verdict agree、ユーザー承認待ち

## 目的

ユーザー保有・watchlist 銘柄に対し、機関投資家の 13F 保有データを統一 IF で取得し、保有変化（QoQ / YoY）を提案レポートのエビデンスとして組み込む。「インサイダー買い × 機関投資家保有増」の二重シグナルで品質確認。

## 入力

- `ticker`（複数可）
- `--top N`（デフォルト 20、上位機関投資家数）
- `--quarters N`（デフォルト 4、QoQ 比較用）

## 出力

envelope 規約準拠 JSON:

```json
{
  "source": "fmp",
  "collected_at": "ISO-8601",
  "tool": "openbb.equity.ownership.institutional",
  "data": {
    "<TICKER>": {
      "provider": "fmp",
      "ok": true,
      "records": {
        "as_of_quarter": "2025-Q4",
        "top_holders": [
          {
            "rank": 1,
            "investor_name": "Vanguard Group Inc",
            "shares_held": 15000000,
            "shares_held_change_qoq": 200000,
            "shares_held_change_pct_qoq": 0.0135,
            "ownership_pct": 0.082,
            "filing_date": "2026-02-14",
            "form_type": "13F-HR"
          }
        ],
        "summary": {
          "total_institutional_pct": 0.78,
          "qoq_change_total_shares": 500000,
          "qoq_change_total_pct": 0.012,
          "new_positions_count": 5,
          "closed_positions_count": 2
        }
      }
    }
  }
}
```

## 配置先

- `shared/openbb/skills/ownership/SKILL.md`（insider と共通）
- `shared/openbb/scripts/institutional.py`（CLI エントリポイント）

## 言語・基盤

Python（uv / Typer）、`obb.equity.ownership.institutional` を FMP provider で呼び出し。

## MVP 範囲

1. `uv run scripts/institutional.py <TICKER>`: 最新四半期 top 20 保有者
2. `--quarters 4` で過去 4 四半期トレンド表示（top holder の保有株数変化）
3. `--format md` で markdown 表出力
4. summary（機関保有率、QoQ 変化、新規 / 撤退ポジション数）

## 将来拡張（MVP 外）

- 保有変化が大きい機関投資家のフラグ（例: Berkshire / Tiger Global / Renaissance のスマートマネー追跡）
- F-Score / multibagger 連携で「品質 + 機関保有増」の二重フィルタ
- セクター ETF・大型ベンチマーク保有との重複度（過剰流入リスク検出）

## エラー処理

error_category 規約準拠（insider-trading-skill と同じ）。

- 13F は四半期ごとの提出（45 日 lag）。直近四半期データ未公開の場合は前四半期返却
- FMP プラン制約に注意（13F は一部 Starter プランで制限あり、要確認）

## 必要コスト

- FMP 有料プラン（ユーザー契約済）
- 13F が Starter プラン対象外の場合、上位プランへのアップグレード可否を提案時に明示

## 代替案

- **SEC EDGAR 13F-HR 直接 fetch**: 13F の XML 構造を Python でパース必要、複数機関の集計・QoQ 計算もすべて手作業。analyst の業務効率を著しく低下
- **WhaleWisdom / HoldingsChannel 等の Web スクレイピング**: 利用規約・robots.txt の遵守問題あり、不採用

## 受け入れ基準

1. `uv run scripts/institutional.py NUS` で envelope 形式の最新四半期 top 20 が取得できる
2. `--quarters 4` で過去 4 四半期の top holder 推移が出力できる
3. summary フィールドが正しく集計される
4. `--format md` で markdown 表出力
5. error_category 分類対応
