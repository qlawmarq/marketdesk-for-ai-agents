# Implementation Plan

## Tasks

- [x] 1. `_strip_role_prefix` に非 str 入力の型ガードを追加し、NaN / 数値 / dict / list / None を一律欠損として吸収する
  - 関数冒頭で `isinstance(value, str)` を満たさない入力を `None` として返し、後続の `rfind` / `startswith` ロジックに到達させない
  - 既存の空文字挙動 (`""` → `""`) と str 入力時のロジック（marker 剥離・bare role 展開）は変更しない
  - シグネチャを「任意型を受け取り str or None を返す」形に更新し、型ガードが関数の責務であることを明示する
  - _Requirements: 1.1, 1.5_

- [x] 2. FMP の owner_title 正規化失敗件数をレコード単位のメタ情報として出力する
  - insider fetch の per-symbol 戻り値に `owner_title_normalized_failed` を追加し、FMP 経路で `owner_title` が非 None かつ非 str の行を数える
  - 下流コードが provider 分岐なしで参照できるよう、SEC / intrinio / tmx を含め全 provider で当該キーを `0` として出力しスキーマを統一する
  - 既存の `dropped_unparseable_codes` と同じ位置・型でフィールドを追加し、aggregate emit 側の変更を不要にする
  - _Requirements: 1.3_

- [x] 3. markdown レンダラに正規化失敗件数の可視化を追加する
  - records ありセクション・空セクションのいずれでも、`owner_title_normalized_failed > 0` のとき `_owner_title_normalized_failed: N_` を 1 行出力する
  - 挿入位置は `## {symbol}` 見出し直下、テーブル（または `_no records in window_`）の直前に固定する
  - _Requirements: 1.3_

- [x] 4. (P) `_strip_role_prefix` の型ガードに対する単体テストを追加する
  - `float('nan')`, `1.0`, `0`, `None`, `{}`, `{"k": "v"}`, `[]`, `["a"]` を渡しても AttributeError が出ず `None` が返ることを保証する
  - `""` → `""`、`"CFO"` → `"CFO"`、`"Officer - CFO"` → `"CFO"`（infix 剥離）など既存挙動が維持されることを既存テストとともに確認する
  - `_normalize_fmp_record` に `owner_title=float('nan')` を渡したとき、`reporter_title` が None になることを 1 件追加する
  - _Requirements: 1.4_

- [x] 5. FMP 再現コマンドと SEC 経路の回帰を最終検証する
  - `uv run scripts/insider.py DXC --provider fmp --days 365 --format md` を実行し、AttributeError なしで JSON / md が返ることを確認する
  - 同実行の出力に FERNANDEZ RAUL J（2026-02-02, P-Purchase, 16,446 株, $15.2442）が含まれることを確認する
  - 同銘柄で SEC 経路を実行し、既存 `Insider 1` の出力が変化していないこと（regression なし）を確認する
  - owner_title が float 由来で正規化失敗したレコードが `owner_title=null` で出力され、aggregate メタに失敗件数が反映されていることを確認する
  - _Requirements: 1.1, 1.2, 1.3, 1.5_
