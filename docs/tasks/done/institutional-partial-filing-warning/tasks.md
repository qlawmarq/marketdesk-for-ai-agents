# Implementation Plan

## Tasks

- [x] 1. `scripts/institutional.py` に partial レコード整形用の純関数ヘルパー群を追加
- [x] 1.1 per-ticker サマリ集約関数を追加
  - レコード配列を走査し、`partial_filing_window == True` のレコードから `{date, filing_deadline}` の辞書を生成して返す純関数
  - `filing_deadline` は `date + 45日` の ISO 文字列（`_is_partial_filing_window` と同じパース規則・ハードコード定数 `45` に同期）
  - partial が 0 件なら空リストを返す（空リストでも常に付与するスキーマ不変量を満たすため）
  - 日付パース失敗行は fail-open で無視（`_is_partial_filing_window` と同挙動）
  - 入力レコード配列を破壊しない
  - _Requirements: 1.2, 2.2_
- [x] 1.2 `--hide-partial` 用のマスキング関数と対象フィールド定数を追加
  - マスク対象となる現在期由来の数値フィールド 22 個（11 本体 + 11 `*_change`）を frozenset 定数として module-level に凍結（`investors_holding`, `ownership_percent`, `number_of_13f_shares`, `total_invested`, `new_positions`, `increased_positions`, `closed_positions`, `reduced_positions`, `total_calls`, `total_puts`, `put_call_ratio` と各 `_change` 派生）
  - `partial_filing_window == True` のレコードでは対象フィールドのみ `None` に置換、それ以外のキー（`symbol`, `cik`, `date`, `partial_filing_window`, `last_*` 全て）は保持
  - `partial_filing_window == False` のレコードは値を一切変更しない（identity pass-through）
  - 入力リストを破壊せず、dict 単位で浅くコピーした新リストを返す
  - _Requirements: 1.4, 2.4_
- [x] 1.3 markdown セル用のエスケープ関数を追加
  - `scripts/insider.py::_escape_md_cell` と同シグネチャ・同挙動で `institutional.py` にコピー（`|` エスケープ、改行→空白、`\r` 除去、`None` → `""`、整数・浮動小数点はデフォルト変換）
  - Decision 3 に従い横断共通化は本タスクでは見送り（`_common.py` への昇格は 3 個目の md wrapper 出現時の別タスク）
  - _Requirements: 1.3, 2.3_

- [x] 2. `scripts/institutional.py` に markdown 描画関数を追加
  - per-ticker の level-2 見出しと markdown 表を返す純関数
  - 列順は固定: `date | investors_holding | ownership_percent | number_of_13f_shares | total_invested | put_call_ratio | notes`
  - 数値は JSON と同じ**生値**で表示（パーセント変換・k/M/B サフィックスは入れない。Decision 3 に従い md と JSON の値を一致させて verification 摩擦を排除）
  - `partial_filing_window == True` の行は行頭に `⚠ ` プレフィックスを付け、`notes` 列に `filing window: deadline YYYY-MM-DD` を出す
  - `partial_filing_window == False` の行は行頭プレフィックスなし・`notes` 空文字列
  - エラー行（`ok: False`）は `_error_category_: <cat> — <error>` の inline 行を出し、表は描画しない
  - `records` が空の行は `_no records in quarter_` を出す
  - 全セル描画は 1.3 のエスケープ関数経由（`--hide-partial` 併用時の `None` は空セルに落ちる。意図通り「比較不可能を明示」）
  - 末尾に改行 1 個を付ける
  - _Requirements: 1.3, 2.3_

- [x] 3. stderr 警告と fetch 層の拡張
- [x] 3.1 stderr 警告エミッタを追加
  - 全 ticker の `partial_filing_window_records[]` を走査し、各 partial レコード 1 件につき要件 §望ましい挙動 1 の 3 行ブロックを stderr に出力する副作用関数
  - フォーマット厳密: `⚠ institutional: <TICKER> <YYYY-MM-DD> is in 13F filing window` / `  (deadline ≈ <YYYY-MM-DD>); ownership_percent / investors_holding may be` / `  materially understated. Treat as preliminary; refresh after deadline.`
  - 複数 ticker / 同一 ticker 内の複数 partial レコードがあれば各々に対して 1 ブロックずつ出す（Decision 6）
  - `ok: False` 行は `partial_filing_window_records` が無いため自動 skip
  - `--no-stderr-warn` 指定時は呼び出し元（main）から本関数を呼ばない設計のため、関数自体に抑制分岐は不要
  - _Requirements: 1.1, 2.1_
- [x] 3.2 `fetch()` に per-ticker サマリ付与を 1 行追加
  - 既存の per-record `partial_filing_window` 付与ループの直後で、`call_result` に `partial_filing_window_records` キーを 1.1 の関数の返り値で埋める
  - `ok: True` の行のみに付与する（`ok: False` 行は触らない＝後方互換 §受け入れ基準 5）
  - 空リストでも必ず付与（スキーマ不変量）
  - 既存 37 フィールド・per-record `partial_filing_window` フラグ・`_is_partial_filing_window` 判定ロジックは一切変更しない
  - _Requirements: 1.2, 2.2, 2.5_

- [x] 4. `main()` の argparse と分岐ロジックを拡張
  - 新フラグ 3 本を追加: `--format {json,md}` デフォルト `json`、`--hide-partial` action=store_true デフォルト False、`--no-stderr-warn` action=store_true デフォルト False
  - `--hide-partial` 既定は OFF（Decision 2: stderr + md + サマリの三重警告で既定 OFF でも誤解を防げる。既定 ON は既存スキーマ互換を壊す）
  - 実行順は「全 ticker fetch → `--hide-partial` 指定時のみ各 `ok: True` 行の `records` をマスキング → `--no-stderr-warn` でなければ stderr 警告 → `--format md` かつ `is_fatal_aggregate` が `None` なら markdown を stdout に書いて exit 0、それ以外は `aggregate_emit` に委譲」
  - `--hide-partial` 適用は stderr 警告より**前**に行う（マスキング後も `partial_filing_window` フラグが残るので警告は正しく出る）
  - `query_meta` に `hide_partial` をエコー追加（agent が呼び出し文脈を再現可能に）。既存の `provider`, `year`, `quarter` エコーは不変
  - `--format md` でも fatal（全行 credential / plan_insufficient 一致）時は `aggregate_emit` に fall-back（`insider.py` と同規約）
  - `_common.silence_stdout` / `safe_call` / `aggregate_emit` / `is_fatal_aggregate` の挙動と exit code 契約はそのまま再利用。新たな `ErrorCategory` 値は追加しない
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 5. (P) `_is_partial_filing_window` の境界値単体テストを追加
  - 新テストファイル（`scripts/institutional.py` と別ファイル、既存 `tests/unit/` 配下の命名規則に揃える）に境界値ケースを網羅
  - `record_date = today - 45日` → `False`（deadline ちょうど、`+45 > today` が成立しない）
  - `record_date = today - 44日` → `True`（deadline 手前）
  - `record_date = today - 46日` → `False`（deadline 超過）
  - `record_date = today` → `True`
  - 非 str / 非 date 入力 → `False`（fail-open）
  - 不正な ISO 文字列 → `False`
  - 判定関数本体は 1〜4 で変更しないので、テスト追加は実装と非干渉。並列実行可
  - _Requirements: 2.6_

- [x] 6. (P) 統合テストスイートを新設して要件の受け入れ基準 1-5 をエンドツーエンド検証
  - 新テストファイルで live FMP API を叩く。`FMP_API_KEY` の有無で skip-gate
  - DXC 現行クエリで stderr に `⚠ institutional:` 行が含まれることを `subprocess.run(..., capture_output=True)` で assert（付随: `--no-stderr-warn` 指定時は stderr に当該警告が出ない）
  - JSON 出力で `data.results[0].partial_filing_window_records` が list で、partial 発生時は要素の `date` / `filing_deadline` が ISO 文字列
  - `--format md` 実行で stdout が JSON パース不可（markdown）、行頭 `⚠ ` を含む行が 1 つ以上、`filing window: deadline 2026-` を含む
  - `--hide-partial` 実行で partial=true レコードの `ownership_percent is None` かつ `last_ownership_percent is not None` かつ `partial_filing_window is True`
  - 既存 `tests/integration/test_json_contract.py` の institutional happy argv が無変更でパスすること（37 フィールド存在確認含む後方互換）を別ケースでスポットチェック
  - `FMP_API_KEY` 未設定 + `--format md` の組合せで fatal fall-back 経路を踏み、stdout が JSON envelope、exit 2 であることを確認
  - README §1-1 の "Test evidence (sample)" ブロックには institutional 固有テストを明示追記しない（設計 §技術スタックに基づき README 改変不要）
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 7. (P) `skills/institutional/SKILL.md` の Inputs / Output / Failure Handling セクションを追記更新
  - Inputs セクションに `--format`, `--hide-partial`, `--no-stderr-warn` の 3 フラグを追記（既定値と用途を明記。`--hide-partial` 既定 OFF と三重警告設計の意図を 1 行で触れる）
  - Output セクションに per-ticker `partial_filing_window_records[]` フィールドとスキーマ（`{date, filing_deadline}`）を追記し、既存フィールドとの後方互換（追加のみ）を明示
  - Output セクションに markdown フォーマット時の列順と `⚠` 行頭マーカー + `notes` 列の意味を 1 段落で記述
  - Failure Handling セクションに stderr 警告の発生条件・フォーマット 3 行ブロック・`--no-stderr-warn` 抑止方法を追記
  - Failure Handling セクションに「CI / 自動バッチでは `--hide-partial` を明示するか stderr を監視する運用を推奨」の注記を追加（Decision 2 補強）
  - 既存の JSON exit code / error_category 記述は改変しない
  - _Requirements: 1.1, 1.2, 1.3, 1.4_
