---
name: _envelope
description: >-
  Read the shared JSON envelope every wrapper emits on stdout. Use when
  parsing any `uv run scripts/<name>.py` output to locate results,
  per-row failures, partial-failure warnings, and exit-code semantics.
---

## When to Use

- Parsing stdout from any wrapper under `scripts/`.
- Deciding whether to read `data.results[]` or surface a fatal `error`.
- Distinguishing "all inputs failed for one fatal reason" (exit 2) from "some rows failed but others succeeded" (exit 0 + `warnings`).

Not for: per-wrapper sub-mode arguments (see `<wrapper>/SKILL.md`), error-category meanings (see `../_errors/SKILL.md`).

## Inputs

None. This is a contract description, not a wrapper.

## Command

None. Wrappers emit the envelope; consumers read it.

## Output

Root keys (always present on success):

- `source` — constant `"marketdesk-for-ai-agents"`.
- `collected_at` — ISO-8601 with offset, e.g. `"2026-04-26T01:59:24+09:00"`.
- `tool` — wrapper basename (e.g. `"quote"`, `"fundamentals"`).
- `data` — wrapper-specific payload. Always traverse `data.results[]` (single_emit and aggregate_emit both place rows there); per-query meta (`provider`, `series`, `universe`, …) sits as siblings of `results` under `data`.

Optional root keys:

- `warnings[]` — appended on partial failure or when a wrapper surfaces a non-error data-quality signal. Row-failure entries: `{symbol, error, error_category}` (`symbol` is `null` for single-query wrappers). Non-error signals use `{symbol, warning_type, …wrapper-specific keys}` and omit `error` / `error_category`; consumers should branch on the presence of `warning_type` vs `error_category` to distinguish the two. Example non-error `warning_type` values: `"partial_filing_window"` (`scripts/institutional.py`, per-record 13F filing-window marker).
- `error`, `error_category`, `details` — present **only on fatal exit** (code 2). When `error` is set, `data` is omitted.

`aggregate_emit` (multi-symbol wrappers): each `data.results[i]` is `{symbol, provider, ok, records|error, error_type?, error_category?}`. Rows with `ok: false` carry `error_category`; the same failure is mirrored into top-level `warnings[]`. `single_emit` (single-query wrappers): `data.results` is a flat list of records on success and `[]` on non-fatal failure (with the failure surfaced in `warnings[]`).

Exit-code contract:

- `0` — full success, or partial success (some rows failed, others succeeded). Always emits `data`.
- `2` — every input failed with the same fatal category (`credential` or `plan_insufficient`), or argparse / input-validation rejection. Emits top-level `error` + `error_category`, omits `data`.

Live example (from `uv run scripts/quote.py AAPL`, truncated):

```json
{
  "collected_at": "2026-04-26T01:59:24+09:00",
  "source": "marketdesk-for-ai-agents",
  "tool": "quote",
  "data": {
    "results": [
      {"symbol": "AAPL", "provider": "yfinance", "ok": true,
       "records": [{"symbol": "AAPL", "last_price": 271.06, "...": "..."}]}
    ],
    "provider": "yfinance"
  }
}
```

## Failure Handling

Fatal envelope (exit 2) carries `error_category` at the root; map it via `../_errors/SKILL.md`. Non-fatal partial failures appear only in `warnings[]` — each entry's `error_category` drives the per-row response.

## References

- `scripts/_common.py` — `wrap`, `emit`, `aggregate_emit`, `single_emit`.
- `docs/steering/tech.md` § JSON output contract.
- `../_errors/SKILL.md`, `../_providers/SKILL.md`.
