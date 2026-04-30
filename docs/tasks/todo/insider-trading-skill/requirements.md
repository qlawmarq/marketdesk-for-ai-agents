# Insider Trading SKILL（FMP Form 4）実装要件

起票日: 2026-04-30
起票者: investment-analyst
承認: reviewer 2026-04-30 verdict agree、ユーザー承認待ち

## 目的

ユーザー保有・watchlist 銘柄、および multibagger / Piotroski 候補に対し、SEC Form 4（insider trading）データを統一 IF で取得し、提案レポートのエビデンスとして組み込む。SEC EDGAR 直接 fetch + Python パースの手間を削減し、analyst の業務効率を向上させる。

## 入力

- `ticker`（複数可、CSV または space-separated）
- `--days N`（直近 N 日、デフォルト 90）
- `--transaction-codes`（オプション、デフォルト P,S,D = 買い / 売り / その他処分）

## 出力

`shared/openbb/skills/_envelope/SKILL.md` の envelope 規約に準拠:

```json
{
  "source": "fmp",
  "collected_at": "ISO-8601",
  "tool": "openbb.equity.ownership.insider_trading",
  "data": {
    "<TICKER>": {
      "provider": "fmp",
      "ok": true,
      "records": [
        {
          "filing_date": "2026-04-15",
          "transaction_date": "2026-04-12",
          "reporter_name": "John Doe",
          "reporter_title": "CFO",
          "transaction_code": "P",
          "shares": 5000,
          "price": 12.34,
          "total_value": 61700,
          "shares_after": 50000,
          "form_type": "4",
          "url": "https://..."
        }
      ]
    }
  }
}
```

CLI 出力: JSON（既存 quote.py 等と同じ pretty-print）+ 任意で `--format md` で markdown 表（提案レポート貼付用）。

## 配置先

- `shared/openbb/skills/ownership/SKILL.md`（共通 skill ドキュメント）
- `shared/openbb/scripts/insider.py`（CLI エントリポイント）

## 言語・基盤

Python（uv / Typer）。既存 `shared/openbb` スタック準拠（`obb.equity.ownership.insider_trading` を FMP provider で呼び出し）。

## MVP 範囲

1. `uv run scripts/insider.py <TICKER> --days 90`: 直近 90 日 Form 4 一覧
2. 複数 ticker 一括: `uv run scripts/insider.py ASC CMCL FLXS --days 60`
3. `--format md` で markdown 表出力
4. `--transaction-codes P` で買いのみフィルタ

## 将来拡張（MVP 外、別タスク化）

- インサイダー連続買い銘柄スクリーニング（cluster buying 検出）
- F-Score / multibagger スクリーニング結果との統合
- 役職別集計（CEO / CFO / Director）
- 買い／売り比率の時系列指標化

## エラー処理

`shared/openbb/skills/_errors/SKILL.md` の error_category 5 値分類規約に準拠。

- `credential`: FMP API キー不正
- `plan_insufficient`: FMP プラン不足（Insider trading は Starter プランで利用可能のはず、要確認）
- `transient`: ネットワーク障害・5xx
- `validation`: ticker 形式不正、days 範囲外
- `other`: 上記以外

## 必要コスト

- FMP 有料プラン（Starter 以上、ユーザー契約済）
- 追加コストなし

## 代替案・代替評価

- **SEC EDGAR 直接 fetch（curl + Python パース）**: 既に `historical.py` 等で運用中の手法だが、Form 4 の Transaction Code 解釈・株数集計を毎回手作業で実装する必要があり、analyst の業務効率を低下させる。FMP 経由なら統一 envelope で取得可能で、複数 ticker の並列取得も可能。

## 受け入れ基準

1. `uv run scripts/insider.py ASC --days 90` で envelope 規約準拠の JSON が取得できる
2. `--format md` で markdown 表が出力され、レポートに貼付可能
3. 複数 ticker（最低 5 銘柄）を 1 コマンドで取得できる
4. error_category が 5 値で分類される
5. テストカバレッジ: provider モック + envelope 形式チェック
