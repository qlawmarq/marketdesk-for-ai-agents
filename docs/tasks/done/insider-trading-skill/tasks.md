# Implementation Plan

## Tasks

- [x] 1. Build argparse-side input validators in the insider wrapper
- [x] 1.1 Add the positive-integer validator used by `--days`
  - Reject non-integer, zero, and negative values before any OpenBB call so bad invocations exit 2 with `error_category: validation`
  - Return the parsed integer for downstream client-side day-window use
  - Verified behaviors: rejection of `"0"`, `"-1"`, `"abc"`, and acceptance of `"90"` and `"1"`
  - _Requirements: 1.4_
- [x] 1.2 Add the comma-separated transaction-code parser used by `--transaction-codes`
  - Split on commas, strip per-element whitespace, validate every element matches a single ASCII letter, and uppercase each before returning the list
  - Reject empty elements, multi-character tokens, and non-alphabetic characters before any OpenBB call so bad invocations exit 2 with `error_category: validation`
  - Verified behaviors: `"P"` → `["P"]`; `"p,s"` → `["P", "S"]`; `"PP"`, `""`, `","`, and `"P,1"` raise validation errors

  - _Requirements: 4.5_

- [x] 2. Establish the per-provider transaction-code lookup constants and pure helpers
- [x] 2.1 Encode the SEC long-English-to-code dictionary and the lookup helper
  - Materialize the 10-row mapping from the long-English transaction-type descriptions to single-letter codes (P/S/A/F/M/C/G/D/J/W) as a frozen module-level constant
  - Build the lookup helper that returns the code on exact match and `null` on miss or empty input, and emits a one-time-per-session stderr line when an unmapped string is observed so future corpus drift is triageable without polluting stdout
  - Verified behaviors: each of the 10 documented strings returns the expected letter; an unmapped string returns `null`; the empty string returns `null`; the unmapped-stderr log fires only once per unique miss within the session
  - _Requirements: 6.3_
- [x] 2.2 Encode the FMP leading-letter extractor and the shared code-to-label table
  - Build the regex-based helper that returns the leading letter when the upstream value matches the `^[A-Z]-` shape and `null` otherwise
  - Encode the closed code-to-label dictionary used to populate `transaction_code_label` whenever `transaction_code` is non-null
  - Verified behaviors: each of the observed FMP shapes (`S-Sale`, `M-Exempt`, `A-Award`, `F-InKind`, `G-Gift`) returns the expected letter; a malformed shape (`"sale"`) returns `null`; the empty string returns `null`; every code in the SEC dispatch table has a matching label entry
  - _Requirements: 6.3_
- [x] 2.3 Add the role-prefix stripper and the total-value computer
  - Build the role-prefix stripper that removes `"officer: "`, `"director: "`, and `"ten_percent_owner: "` prefixes (and the analogous infix forms) from FMP `owner_title` so the field is the bare title across providers; bare `"officer"` / `"director"` / `"ten_percent_owner"` map to their human-readable forms
  - Build the total-value computer that returns `shares * price` only when both are non-null and `price > 0`, else `null`
  - Verified behaviors: bare `"officer: Chief Executive Officer"` returns `"Chief Executive Officer"`; bare `"director"` returns `"Director"`; compound `"director, officer: Chief Executive Officer"` returns `"Chief Executive Officer"`; `(100, 150.5)` returns `15050.0`; `(0, 150.5)` returns `0.0`; `(100, 0)` and `(100, None)` return `null`
  - _Requirements: 6.2_

- [x] 3. Implement the per-provider record normalizers that produce the canonical 19-field schema
- [x] 3.1 Implement the SEC record normalizer
  - Map upstream SEC keys (`securities_transacted`, `transaction_price`, `securities_owned`, `form`, `filing_url`, `owner_name`, `owner_title`) to the canonical schema names; preserve `transaction_type` verbatim under `transaction_type_raw`
  - Collapse SEC's full-word `"Acquisition"` / `"Disposition"` to single letters; pass `"Direct"` / `"Indirect"` ownership through verbatim
  - Compute `transaction_code` via the SEC long-English lookup and populate `transaction_code_label` from the shared code-to-label table; compute `total_value` via the shared computer
  - Emit every canonical key on every output record (missing upstream values render as `null`, never absent) so the schema-shape invariant holds across rows
  - Verified behaviors: live SEC samples produce records whose key set is exactly the 19-key canonical surface; unmapped transaction-type strings yield `transaction_code: null` while `transaction_type_raw` retains the original verbatim
  - _Requirements: 6.2, 6.3_
- [x] 3.2 Implement the FMP record normalizer
  - Map upstream FMP keys (`form_type`, `url`, `securities_transacted`, `transaction_price`, `securities_owned`, `owner_name`, `owner_title`) to the canonical schema names; preserve `transaction_type` verbatim under `transaction_type_raw`
  - Extract `transaction_code` via the leading-letter regex; expand the single-letter `ownership_type` values (`"D"` / `"I"`) to `"Direct"` / `"Indirect"` and emit `null` for unknown letters
  - Pass `acquisition_or_disposition` through verbatim (FMP already emits the single letter); strip role prefixes from `owner_title` via the shared stripper
  - Populate `transaction_code_label` and `total_value` identically to the SEC path so the schema is provider-invariant
  - Verified behaviors: live FMP samples produce records whose key set matches the SEC normalizer's key set exactly; the `"officer: "` prefix is gone from `reporter_title`; ownership letters are expanded
  - _Requirements: 2.6, 6.2, 6.3_
- [x] 3.3 Implement the intrinio / tmx record normalizer
  - Emit the closed canonical key set with `transaction_code = null` and no role-prefix stripping; preserve `transaction_type_raw` verbatim where present
  - Use lookup-style access for any field whose upstream key happens to coincide with a canonical name; do not apply the FMP-specific field renames or the role-prefix stripper, so unsupported providers yield mostly-`null` records that are honest about the lack of mapping
  - Verified behaviors: an arbitrary record dict produces an output whose key set matches the canonical 19-field surface and whose `transaction_code` is `null`
  - _Requirements: 2.6, 6.2, 6.3_

- [x] 4. Implement the transaction-code filter and integrate it with the per-symbol orchestration
- [x] 4.1 Implement the transaction-code filter helper
  - When the supplied filter is empty (`null`), pass the input list through unchanged and report a dropped-unparseable count of zero
  - When a filter is supplied, walk records once, keep records whose normalized `transaction_code` (already uppercase) is in the filter set, and drop records whose `transaction_code` is `null`; record the number of dropped null-code rows so the wrapper can echo it under per-row `dropped_unparseable_codes`
  - Records rejected because their non-null code did not match the filter must not contribute to the dropped-unparseable counter
  - Verified behaviors: empty filter passes through with count zero; an all-kept filter returns every record with count zero; an all-dropped filter (mix of mismatched and null codes) returns an empty list with count equal to the null-code subset size
  - _Requirements: 4.2, 4.3, 4.4_
- [x] 4.2 Refactor the per-symbol orchestration to apply normalize → day-window → code-filter → row builder in order
  - Reuse the existing safe-call envelope so every OpenBB call still produces structured `{ok, error, error_type, error_category}` failures and stdout warnings remain absorbed
  - On success, dispatch every upstream record through the matching per-provider normalizer via a single three-way branch (sec / fmp / other), then apply the existing client-side day-window filter, then the new transaction-code filter, then build the per-symbol row including the `dropped_unparseable_codes` count
  - Apply the day-window filter before the transaction-code filter so the dropped-unparseable count reflects only in-window rows that the code filter rejected as null-coded
  - On failure, build the per-symbol failure row with `ok: false` and omit `records` and `dropped_unparseable_codes` so the per-row shape stays clean
  - Treat empty upstream responses as `ok: true` with `records: []` so consumers can distinguish "no insider activity" from "fetch failed"
  - Forward `--limit` verbatim when supplied and skip the keyword entirely when omitted so the upstream provider default applies; pass `.T` and other suffixes unchanged to every OpenBB call
  - _Requirements: 1.5, 1.6, 2.6, 3.5, 4.4, 6.1, 6.5, 7.1, 7.4_

- [x] 5. (P) Add the public fatal-aggregate-gate helper to the shared envelope module
  - Expose a pure free function that returns the fatal `ErrorCategory` value when every row failed with the same `credential` or `plan_insufficient` category, and `null` otherwise (mixed fatal categories collapse to `null`, matching the existing internal contract)
  - Implement by delegating to the existing private fatal-decision helper so no new policy is introduced; place it next to the other public exports in the shared envelope module
  - Verified behaviors: empty rows return `null`; all-success rows return `null`; mixed success-and-credential rows return `null`; all-credential rows return the credential category; all-plan-insufficient rows return the plan-insufficient category
  - _Requirements: 5.8_

- [x] 6. Implement the markdown emission path
- [x] 6.1 Implement the cell-escape helper that protects markdown-table structure
  - Replace pipe characters in cell values with their escaped form and collapse newlines to single spaces so cells with multi-paragraph SEC footnotes or English transaction descriptions cannot break the table
  - Render `null` cell values as the empty string rather than the literal `"None"` so empty cells read cleanly
  - Verified behaviors: pipe-in-cell renders as the escaped form; newline-in-cell renders as a single space; numeric cells render via default string conversion
  - _Requirements: 5.7_
- [x] 6.2 Implement the markdown document renderer
  - Pin the column order to the closed eleven-column reading set (`filing_date | transaction_date | reporter_name | reporter_title | transaction_code | transaction_code_label | shares | price | total_value | shares_after | url`) so consumers can paste tables across reports without column-order drift
  - Emit a level-2 heading per input ticker; for failure rows emit a single inline `_error_category_:` line with the upstream message; for ok-but-empty sections emit a `_no records in window_` line, optionally suffixed with the dropped-unparseable count when the code filter is active and the count is positive; for non-empty sections emit the markdown table built from the normalized records via the cell escape helper
  - Append a single trailing newline at end-of-output so downstream pagers and pipe consumers behave predictably
  - Verified behaviors: heading appears once per input ticker; non-empty sections render the canonical column header verbatim; failure rows render the inline error-category line and no table; empty sections render the no-records line; pipe and newline characters in cell content cannot break the table
  - _Requirements: 5.3, 5.4, 5.5, 5.6, 5.9_

- [x] 7. Wire the new flags, query metadata, and emit dispatch into main
  - Accept one or more positional ticker symbols; reject zero-positional invocations via argparse default behavior; default `--days` to 90 and route through the positive-integer validator; forward `--limit` verbatim when present
  - Expose `--provider` as a closed choice over the supported providers and default to the keyless SEC path; never promote any paid provider to the default
  - Expose `--transaction-codes` routed through the comma-separated parser and `--format` as a closed `{json, md}` choice defaulting to `json`
  - Echo per-query metadata (`provider`, `days`, `transaction_codes`, and `limit` only when supplied) under the envelope's `data` block so consumers can replay the exact request from the envelope alone; the active filter list is uppercased or `null` when omitted, and per-row dropped-unparseable counts are zero whenever the filter is inactive
  - On the JSON path, emit through the existing aggregate-emit helper with the insider tool name so the cross-wrapper envelope contract is preserved unchanged
  - On the markdown path, peek the fatal-aggregate gate first; when the gate trips, fall back to the JSON aggregate emitter so exit-code-2 fatal exits still carry machine-readable error fields, otherwise write the rendered markdown document to stdout
  - Send any traceback to stderr, keep stdout a single document per format, and emit a single trailing newline; do not introduce a `--side` flag, do not parse `portfolio.yaml`, do not emit notifications, do not write any file or cache, and do not split normalization into a second module file
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.5, 3.1, 3.2, 3.3, 3.4, 4.1, 4.6, 4.7, 5.1, 5.2, 5.8, 5.9, 6.4, 6.6, 6.7, 6.8, 7.2, 7.3, 7.5, 7.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

- [x] 8. Add the per-wrapper integration suite for the new behavior
- [x] 8.1 Cover SEC happy-paths and validation rejections
  - Multi-symbol fetch with `--days` returns a populated envelope that passes the cross-wrapper JSON-contract invariants
  - A `--transaction-codes` filter against a ticker known to have insider activity in the window returns a non-empty subset on at least one symbol; the filter is exercised on the keyless default provider so the case stays unconditional
  - Invalid `--days 0` and invalid `--transaction-codes "PP"` invocations exit 2 with the validation category before any OpenBB call
  - Echo invariants: when the filter is supplied, `data.transaction_codes` is the uppercased list; when the filter is omitted, the value is `null` and per-row dropped-unparseable counts are zero
  - Per-row failure shape: a credential-failure row carries no `dropped_unparseable_codes` field
  - _Requirements: 9.1, 9.2, 9.6_
- [x] 8.2 Cover FMP happy-paths under the existing skip-gate convention
  - Single-symbol fetch under the FMP provider returns a normalized envelope; the test is skip-gated on the FMP key so the default integration run stays green when the key is absent
  - A `--format md` invocation under FMP returns a non-empty markdown document that is not valid JSON
  - _Requirements: 9.3_
- [x] 8.3 Cover cross-provider schema-consistency
  - The same single-symbol query under the SEC and FMP providers (e.g. `AAPL`) produces records whose key set is exactly the canonical 19-field surface on both sides, and whose `transaction_code`, when non-null, is a single uppercase letter on both
  - The FMP half is skip-gated on the key per the existing convention
  - _Requirements: 9.4_
- [x] 8.4 Cover markdown invariants and the fatal fall-back
  - Under `--format md`, stdout is not valid JSON, contains a `## <SYMBOL>` heading per input ticker, and contains either a markdown table, the inline error-category line, or the no-records line for each ticker
  - Under `--format md` with the FMP provider and a deliberately unset key on a single-symbol input (an all-rows-fatal scenario), stdout is the JSON envelope with exit 2 — markdown is not emitted on fatal exits
  - _Requirements: 9.5_

- [x] 9. Rewrite the agent-facing skill manual from a live-run sample
  - Run the wrapper end-to-end against a small basket (recommended invocation: `uv run scripts/insider.py AAPL --days 90 --transaction-codes P,S --format md`) and capture one short markdown sample for the manual; do not embed test-fixture data
  - Document `--transaction-codes` and `--format`, the markdown column set, and the typical analyst usage patterns (insider-buying conviction filter and discretionary-trade filter); replace the existing per-provider record-key paragraph with a single normalized-schema section listing the canonical field set once
  - Include the closed transaction-code label table (P / S / A / F / M / C / G / D / J / W) so callers know which letters the filter accepts and what each means
  - Stay English-only and within the 30–80-line skill budget; do not add tests under the skill folder; update the skills index row only if the existing row needs new flag mentions, since the skill folder itself is already registered
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
