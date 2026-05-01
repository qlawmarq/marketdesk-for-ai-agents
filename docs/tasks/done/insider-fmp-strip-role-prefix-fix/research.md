# 調査結果: `_strip_role_prefix` 型エラーの事実ベース検証

## 目的

`AttributeError: 'float' object has no attribute 'rfind'` の発生源と影響範囲を、実 API データで確認する。

## 再現結果

```
$ uv run scripts/insider.py DXC --provider fmp --days 365
AttributeError: 'float' object has no attribute 'rfind'
  File "scripts/insider.py", line 164, in _strip_role_prefix
    idx = value.rfind(marker)
```

確認済 (2026-05-01、FMP_API_KEY 有効)。

## 根因データ

`obb.equity.ownership.insider_trading(symbol="DXC", provider="fmp", limit=500)` を `to_df()` すると、`owner_title` 列に `float('nan')` が混入する。

- 総行数: 500
- `owner_title` が `str` 以外 (NaN): 2 行
- 該当行の特徴: `transaction_type == ""` かつ `owner_name` が外部人物 (例: Gonzalez Anthony, Racine Karl) の外れレコード — FMP 側で role / type 欄が空白のまま返却されている
- 他の値はすべて `str` (例: `"officer: SVP, Controller and PAO"`, `"director, officer: President and CEO"`, `"director"`)

NaN は OpenBB Core → pandas DataFrame 化の過程で null 表現として付与されるもので、`to_records(df)` が `NaN` のまま dict に載せるため、スクリプト側の型前提 (`str | None`) が破れる。

## SEC との cross-check (受け入れ基準 2 の事前確認)

FMP の DXC 返却 500 行に **FERNANDEZ RAUL J, 2026-02-02, P-Purchase** を含む行が存在することを確認済:

```
transaction_date  owner_name         transaction_type  shares   price
2026-02-02        FERNANDEZ RAUL J   P-Purchase         (該当行あり)
```

従って、`_strip_role_prefix` の型ガード修正で FMP 経路の実行を通せれば、SEC 側 `Insider 1` の P トランザクションと同等のレコードが FMP でも取得できる。

FMP の直近カバレッジ:
- 日付範囲: 2017-04-03 〜 2026-02-13
- 2026 年分: 2 行 (Voci Christopher Anthony F-InKind 2026-02-13, FERNANDEZ RAUL J P-Purchase 2026-02-02)

## 既存コード読解

- `scripts/insider.py:158-174` `_strip_role_prefix`: `if not value` ガードのみで、`float('nan')` は truthy のため通過する
- `scripts/insider.py:237` `_normalize_fmp_record` の `"reporter_title": _strip_role_prefix(record.get("owner_title"))` が唯一の呼び出し元
- SEC 経路は `_normalize_sec_record` で `owner_title` をそのまま通すだけ (`scripts/insider.py:207`) なので regression リスクなし
- `fetch()` (458-480) は `safe_call` で OpenBB 呼び出しを保護するが、normalizer 内の `AttributeError` は safe_call の外で発生するため **ticker 単位で例外停止** する設計

## テスト資産

`tests/unit/test_insider_normalize.py` に `_strip_role_prefix` の既存テストが 10 件程度あり、以下のケースを保証:
- `officer:` / `director:` / `ten_percent_owner:` prefix 剥離
- `"director, officer: ..."` 形式の infix 剥離
- bare role 展開 (`director` → `Director` 等)
- `None` → `None`, `""` → `""` (現状)
- マーカーなし文字列の pass-through

既存テストに `float` / `dict` / `list` を渡すケースはない。

## 影響範囲サマリ

- 修正箇所は `_strip_role_prefix` 1 関数のみで完結
- `_normalize_sec_record` は `owner_title` を変換しないため影響なし
- `_normalize_other_record` (intrinio / tmx) も role prefix 処理を行わないため影響なし
- FMP 経路の正規化失敗件数を明示するには、`fetch()` 側で `record["owner_title"]` の型を数えるのが最小変更
