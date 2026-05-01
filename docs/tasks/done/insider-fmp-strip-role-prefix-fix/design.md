# 設計: `_strip_role_prefix` 型ガード修正

## 方針

`_strip_role_prefix` の入口で非 `str` 入力を一律 `None` として吸収する。FMP の pandas 経路で混入する `float('nan')` を欠損として正常出力に流し、例外停止による ticker 単位の全損を避ける。ロジック本体は変更しない（挙動互換）。

## 変更点

### 1. `scripts/insider.py:158-174` `_strip_role_prefix`

```python
def _strip_role_prefix(value: Any) -> str | None:
    """Remove FMP role prefixes / infixes from a reporter title.

    Non-string inputs (``float('nan')``, numeric, dict/list, ``None``)
    are treated as missing and collapsed to ``None``. The FMP upstream
    returns ``owner_title`` as a pandas-backed column where null rows
    surface as ``NaN`` floats rather than Python ``None`` — those must
    not raise ``AttributeError`` in the rfind / startswith branches.
    """
    if not isinstance(value, str):
        return None
    if not value:
        return value
    # 以降 (marker rfind / prefix startswith / _BARE_ROLES) は現状維持
```

- シグネチャ: `value: str | None` → `value: Any` （型ガードが関数の責務に含まれる明示）
- 空文字の扱い: 既存挙動を維持（`""` → `""`）。欠損件数の可視化は §2 の独立カウンタ `owner_title_normalized_failed` が担うため、戻り値のセマンティクスを変える必要はなく、requirements.md の参考実装案とも整合する

### 2. `scripts/insider.py:458-480` `fetch()` に正規化失敗カウンタ

FMP 経路のときのみ `owner_title` が非 str で non-None の行数を数え、戻り値に載せる。

```python
owner_title_normalized_failed = 0
if provider == "fmp":
    owner_title_normalized_failed = sum(
        1
        for r in call_result["records"]
        if r.get("owner_title") is not None
        and not isinstance(r.get("owner_title"), str)
    )
normalized = [_normalize_record(r, provider) for r in call_result["records"]]
...
return {
    "symbol": symbol,
    "provider": provider,
    "ok": True,
    "records": kept,
    "dropped_unparseable_codes": dropped_unparseable,
    "owner_title_normalized_failed": owner_title_normalized_failed,
}
```

- `aggregate_emit` は per-row dict をそのまま `data.results[]` に載せるため、`_common.py` への変更は不要
- 既存の `dropped_unparseable_codes` と同一の位置・型で追加し、per-ticker メタの既存パターンに追随する
- スキーマ安定性のため、全 provider で `0` 固定出力に統一（SEC / intrinio / tmx でもキーを省略せず `0` を載せる）。下流分析コードが provider 分岐なしで単一キーを参照できる

### 3. `scripts/insider.py:398-433` `_render_markdown` に件数表示

records ありセクションでも empty セクションでも、`owner_title_normalized_failed > 0` なら 1 行追加:

```markdown
_owner_title_normalized_failed: 2_
```

挿入位置は `## {symbol}` 見出しの直下、テーブル（または `_no records in window_`）の前。

### 4. テスト追加 (`tests/unit/test_insider_normalize.py`)

`_strip_role_prefix` セクションに以下を追加:

```python
def test_strip_role_prefix_returns_none_for_nan_float() -> None:
    assert _strip_role_prefix(float("nan")) is None

def test_strip_role_prefix_returns_none_for_numeric() -> None:
    assert _strip_role_prefix(1.0) is None
    assert _strip_role_prefix(0) is None

def test_strip_role_prefix_returns_none_for_dict() -> None:
    assert _strip_role_prefix({}) is None
    assert _strip_role_prefix({"k": "v"}) is None

def test_strip_role_prefix_returns_none_for_list() -> None:
    assert _strip_role_prefix([]) is None
    assert _strip_role_prefix(["a"]) is None
```

既存テストの更新: なし（`""` は従来どおり `""` を返す、`"CFO"` pass-through 等の既存挙動を全て維持）

`_normalize_fmp_record` セクションに 1 件追加:

```python
def test_normalize_fmp_record_nan_owner_title_yields_null_reporter_title() -> None:
    rec = _fmp_sample_record()
    rec["owner_title"] = float("nan")
    out = _normalize_fmp_record(rec)
    assert out["reporter_title"] is None
```

## 受け入れ基準との対応

| 基準 | 対応 |
|---|---|
| 1. 再現コマンドが AttributeError を出さず JSON/md が返る | §1 の isinstance ガードで達成 |
| 2. SEC `Insider 1` (FERNANDEZ RAUL J 2026-02-02) が FMP 経路でも取得可能 | research.md で FMP 500 行中に該当行の存在を確認済。§1 修正で流路が開通する |
| 3. owner_title=null レコードが正常出力され aggregate メタに件数記録 | §1 で null 出力、§2 で `owner_title_normalized_failed` をレコード単位に追加、§3 で markdown 可視化 |
| 4. 単体テストで float/None/dict/str 全形態を AttributeError なしで処理 | §4 のテスト追加 |
| 5. SEC 経路に regression なし | `_normalize_sec_record` は `owner_title` を無変換でパススルーのため影響なし。既存 SEC テストをそのまま維持 |

## 非目標

- FMP upstream の空 `transaction_type` 行（例: DXC の Gonzalez Anthony 2023-01-05）のスキップは本タスクのスコープ外。これらは `_extract_fmp_code("") → None` を経て `_apply_code_filter` で既に `dropped_unparseable_codes` にカウントされるため、既存経路で適切に扱われている
- NaN sanitization の一般化（他フィールドの NaN 対策）は本タスクのスコープ外。`_common.py:sanitize_for_json` が emit 直前に NaN → None を実施しているため、`owner_title` 以外の NaN は現状でも JSON 出力で破綻しない
