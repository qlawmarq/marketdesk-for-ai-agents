# `insider.py` FMP provider: `_strip_role_prefix` 型エラー修正要件

起票日: 2026-05-01
起票者: investment-analyst
承認: 2026-05-01 ユーザー指示（inbox.md「ツールのバグについては shared/openbb/docs/tasks/todo に起票」）、reviewer 5/1 verdict agree（補強4 で記載項目推奨）

## 目的

`shared/openbb/scripts/insider.py` の FMP provider 経路で稀に発生する `AttributeError: 'float' object has no attribute 'rfind'` を解消し、FMP 補完を SEC と並列して安全に実行できる状態へ復旧する。

**事実ベースの解析品質の観点での意義**: 現状、FMP 経路が落ちた場合に SEC で代替する運用は可能だが、

1. provider 二重照合（FMP × SEC）による cross-check ができない（取り違え・欠損リスクの監査が片肺）
2. CI / バッチ実行で例外停止すると、後続のスコアリング・通知パイプラインがサイレントにスキップされる懸念
3. analyst の手動切替が必要 → 「データ取得失敗」を欠損として明示的に扱える状態にしないと、提案根拠の透明性が損なわれる

## 症状

```
$ uv run scripts/insider.py DXC --provider fmp --days 365
AttributeError: 'float' object has no attribute 'rfind'
  File "shared/openbb/scripts/insider.py", line 164, in _strip_role_prefix
    idx = value.rfind(marker)
```

- 発生条件: FMP 返却データの `owner_title` フィールドが文字列ではなく数値（典型的には `NaN` を float として返す pandas / pydantic 経路）
- 影響: ティッカー単位で例外停止、partial 結果も返らない（aggregate_emit までたどり着かない）

## 原因

`_strip_role_prefix(value: str | None)` のシグネチャは `str | None` を前提とするが、上流（FMP → openbb-core → 本スクリプト）で型保証が破れている。`if not value` ガードは truthy 判定のみで、`value=float('nan')` (truthy) は通過し、後続の `value.rfind(marker)` で AttributeError。

該当箇所（参考、修正は別タスクで実施）:
- `shared/openbb/scripts/insider.py:158-174` `_strip_role_prefix`

## 望ましい挙動（accuracy-first）

事実ベース解析・誤解防止の観点から、以下を要件とする。

1. **例外を投げず欠損として扱う**: `owner_title` が str/None 以外の場合、`None` を返し、当該レコードは `owner_title=null` として正常出力する。fail-fast よりも `data_completeness` を優先（インサイダー P/S の主要シグナルは `transaction_code` / `shares` / `price` であり、role 文字列は補助的）
2. **欠損は集計メタへ明示**: `aggregate_emit` の per-ticker `notes`（または同等フィールド）に `owner_title_normalized: false` 件数を出し、analyst が「正規化失敗で role 未取得 N 件」と認識可能にする
3. **provider 比較を支援**: 同一 ticker を SEC + FMP で取得した際、FMP の正規化失敗件数が SEC の Form 4 件数より顕著に少ないなら、FMP 経路の信頼性が落ちていることを analyst が即時識別できる
4. **回帰テスト**: float (NaN, 1.0)、None、空文字、想定外の dict / list を `_strip_role_prefix` に渡しても AttributeError が出ないことを単体テストでカバー

## 修正方針（参考実装案）

`_strip_role_prefix` 冒頭に型ガード:

```python
def _strip_role_prefix(value):
    if not isinstance(value, str):
        return None  # NaN / float / None / dict 全て吸収
    if not value:
        return value
    ...（以降は変更なし）
```

ロジック自体の変更ではなく**入力サニタイズの強化**。挙動互換性は維持される（str 入力は従来通り）。

## 再現コマンド

```bash
cd shared/openbb
SEC_USER_AGENT="..." uv run scripts/insider.py DXC --provider fmp --days 365 --format md
```

DXC 以外でも、FMP 13F 報告者の `owner_title` が NaN を含む銘柄で再現する。

## 受け入れ基準

1. 上記再現コマンドが AttributeError を出さず、JSON / md 出力が正常に返る
2. SEC provider で取得済の `Insider 1`（FERNANDEZ RAUL J, P, 16,446 株, $15.2442, 2026-02-02）が FMP 経路でも同等に取得できる（ある場合）
3. `owner_title` が float 由来で正規化失敗したレコードは `owner_title=null` で出力され、aggregate メタに件数が記録される
4. 単体テスト: `_strip_role_prefix(float('nan')) is None`、`_strip_role_prefix(1.0) is None`、`_strip_role_prefix(None) is None`、`_strip_role_prefix({}) is None`、`_strip_role_prefix("CFO") == "CFO"`、`_strip_role_prefix("Officer - CFO") == "CFO"`（既存挙動）
5. 既存 SEC 経路の出力に regression なし

## 優先度

**中**

- 運用上は SEC provider で迂回可能（DXC で実証済）
- ただし FMP の Form 4 補完が無効化されると provider redundancy / cross-check が失われ、誤解検出能力が低下
- 大規模 watchlist（>20 銘柄）でバッチ実行する場面で、1 銘柄の例外停止が後続を巻き込まないようにする観点でも修正優先度は中

## 関連タスク

- `shared/openbb/docs/tasks/todo/institutional-partial-filing-warning/`（同日起票、analyst の誤解防止系列）
- `shared/openbb/docs/tasks/done/insider-trading-skill/`（本スクリプト初出、SEC canonical の方針はそのまま継続）

## 代替案（不採用）

- **FMP provider を削除し SEC のみ**: provider redundancy が失われ、SEC API レート制限時の fallback がなくなる。不採用
- **try/except で例外丸呑み**: 失敗が外部から見えなくなり「サイレントな根拠欠損」を生む。本要件は欠損を**メタで明示**する方針なので不採用
