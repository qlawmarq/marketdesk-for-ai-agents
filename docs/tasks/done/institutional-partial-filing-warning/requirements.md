# `institutional.py` partial filing window 警告強化要件

起票日: 2026-05-01
起票者: investment-analyst
承認: 2026-05-01 ユーザー指示（inbox.md「正確に事実ベースのデータに基づき誤解することなく解析を行うためにはどのような動作が望ましいのか考えて起票」）、reviewer 5/1 verdict agree

## 目的

`shared/openbb/scripts/institutional.py` は既に `partial_filing_window: true` フラグをレコード単位で付与している（`institutional.py:37-50`, `:65-69`、SEC §240.13f-1 の 45 日 filing window 準拠）。

しかし**現状の出力は raw JSON のみで、フラグが per-record の深い位置にあるため、analyst が見落として誤解する事例が発生**した。

具体例: 2026-05-01 セッションで DXC `2026-03-31` レポートの `ownership_percent: 3.21%`（前期 90.10% から急落）を観測。本フラグは `true` だったが、analyst が初期解析で **「インスティテューショナル退出」と誤解**しかけた。reviewer 独立検証で「filing-window artifact」と確定し、提案根拠から除外された。

**事実ベース解析の観点での意義**:

- 13F 出力の **raw 数値** が 1 桁・2 桁単位で「実態と異なる方向」を示す可能性があるため、フラグの強調表示は単なる UX 改善ではなく**判断基準の信頼性に直結**
- analyst が誤解 → reviewer が訂正 という事後修正に依存する現運用は、reviewer が常時並走する保証がない場面（自動化バッチ、深夜ループ）で機能しない
- 本要件は data の正しさではなく **data の解釈リスク** を構造的に低減する

## 現状の挙動（不具合ではなく改善余地）

### 既に実装済（保持）

- `_is_partial_filing_window(record_date, today)` で `record_date + 45 days > today` のレコードを判定
- `fetch()` が `rec["partial_filing_window"] = True/False` を per-record に付与
- ヘルプ文に caveat 記載済（`institutional.py:77-83`）

### 改善余地

1. JSON のみ出力 — `--format md` がない（`insider.py` には実装済）
2. partial=true 件数の **集計サマリ** がない（per-ticker でいくつ partial か analyst が手動カウント）
3. **stderr 警告がない** — partial レコードを返却した際に標準エラーへ「⚠ partial filing window: 〜」を出さないため、CLI 利用時に見落としやすい
4. partial=true レコードの数値（ownership_percent, investors_holding 等）に**「不完全集計」を示すマーカー**がない（数値だけ見ると正規データと区別不能）

## 望ましい挙動（accuracy-first）

1. **stderr 警告**: 1 件以上 `partial_filing_window=true` を返却する場合、stderr に以下を出力
   ```
   ⚠ institutional: <TICKER> <YYYY-MM-DD> is in 13F filing window
     (deadline ≈ <YYYY-MM-DD>); ownership_percent / investors_holding may be
     materially understated. Treat as preliminary; refresh after deadline.
   ```
2. **JSON 出力にサマリ追加**: per-ticker `notes` に
   ```json
   {
     "partial_filing_window_records": [
       {"date": "2026-03-31", "filing_deadline": "2026-05-15"}
     ]
   }
   ```
   を追加（既に per-record に true があるため、集計サマリを並置）
3. **`--format md` 実装**: `insider.py` の md 出力を範形に、partial=true 行は **行頭に ⚠ 記号 + 「(filing window: deadline YYYY-MM-DD)」注記** を入れる
4. **重要数値のサプレッション選択肢**: `--hide-partial` フラグ（または逆で `--include-partial`、デフォルト挙動はユーザー判断）で、partial レコードの ownership_percent / 13f_shares / total_invested を `null` に置換した出力を返す（数値の誤読を構造的に防止）。フラグ既定値はチケット実装時に reviewer と相談

## 受け入れ基準

1. `uv run scripts/institutional.py DXC` で 5/15 前のクエリ実行時、stderr に上記警告が出る
2. JSON 出力の per-ticker サマリに `partial_filing_window_records[]` が追加される
3. `--format md` で markdown 表が返り、partial=true 行に ⚠ + 注記が付く
4. `--hide-partial` フラグで partial レコードの数値が `null` 化される（メタ情報は保持）
5. 既存 JSON スキーマに対する後方互換: 追加フィールドのみで既存フィールドは変更しない
6. 単体テスト: `_is_partial_filing_window` の境界値（filing_deadline ちょうど、deadline + 1 日、deadline - 1 日）

## 再現シナリオ（誤解事例）

```bash
cd shared/openbb
uv run scripts/institutional.py DXC --provider fmp
```

返却 JSON に以下が含まれた:

| date | investors_holding | ownership_percent | 13f_shares | total_invested | partial_filing_window |
|---|---|---|---|---|---|
| 2026-03-31 | 122 | 0.0321 (= 3.21%) | 5.7M | $71.21M | **true** |
| 2025-12-31 | 415 | 0.9010 (= 90.10%) | 159.66M | $2.34B | false |

→ analyst が partial フラグを見落とし、初期解析で「institutional exodus -86.89pp」と誤解しかけた。reviewer の独立検証で「5/15 filing deadline 前の不完全集計が支配的」と訂正。

→ 本要件適用後の期待:
1. stderr に ⚠ 警告が出力される
2. md 出力で `2026-03-31` 行が `⚠ 2026-03-31 (filing window: deadline 2026-05-15)` と表示
3. `--hide-partial` 指定時、3.21% / 122 / 5.7M / $71.21M が `null` 化され**比較不可能であること自体が明示**される

## 優先度

**中-高**

- データ品質ではなく **解釈品質** の問題のため、放置すると今後も同種の誤解が再発
- multibagger / sector_score のスコアリングと違い、13F は「誤った数値が比較可能に見える」性質があり誤解リスクが構造的に高い
- 一方で SEC §240.13f-1 の 45 日 window は四半期 1 度の事象で頻度は低く、緊急ではない

## 関連タスク

- `shared/openbb/docs/tasks/todo/insider-fmp-strip-role-prefix-fix/`（同日起票、analyst 誤解防止系列）
- `shared/openbb/docs/tasks/done/institutional-ownership-skill/`（本スクリプト初出、partial_filing_window フラグの実装はここで導入済）

## 代替案（不採用）

- **analyst 側の運用ルールで対処**（caveat を SOP 化）: 既に caveat はヘルプに記載済だが本セッションで誤解が発生した。SOP 強化のみではヒューマンエラーを構造的に防げない
- **partial レコードを返却しない**: filing window 中でも他の有用情報（filing_date 自体の存在）を提供しないのは過剰。フラグ + マスキングで対応する方が情報量が多い
