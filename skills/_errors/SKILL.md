---
name: _errors
description: >-
  Interpret the `error_category` token attached to every wrapper failure
  and apply the default agent response. Use when a wrapper returns a
  non-empty `error` (fatal) or `warnings[]` entry (partial).
---

## When to Use

- A wrapper exits 2 with top-level `error` + `error_category`.
- A wrapper exits 0 but `data.warnings[]` (or row-level `ok: false`) carries `error_category`.
- Choosing between retry, fallback, skip, and stop.

Not for: envelope shape (see `../_envelope/SKILL.md`), credential map (see `../_providers/SKILL.md`).

## Inputs

None. This skill describes the `ErrorCategory` enum and its agent response policy.

## Command

None. Consumers read `error_category` from the envelope.

## Output

Five `error_category` values (from `scripts/_common.py::ErrorCategory`):

| `error_category` | Message prefix | Default agent response |
|---|---|---|
| `credential` | `CredentialError:` | Notify operator (Discord) and stop the affected pipeline step. Do not retry. The env-var named in `../_providers/SKILL.md` is missing or invalid. |
| `plan_insufficient` | `PlanError:` | Skip the paid sub-mode and record `skipped: paid tier required` in the summary. Fall back to a free-tier sub-mode if one exists for the same data need. Do not retry. |
| `transient` | (none) | Retry **once** after a short backoff. On second failure, surface as a warning and proceed with remaining inputs. |
| `validation` | (none) | Treat as a fatal input bug. Re-derive arguments (ticker spelling, date range, sub-mode) and try again with the corrected request. Do not retry the original arguments. |
| `other` | (none) | Log a warning and exclude the affected row/result from downstream analysis. Do not retry. |

Prefix semantics: when every input failed with the same fatal category, the wrapper exits 2 and the top-level `error` string starts with `CredentialError:` or `PlanError:`. Agents pattern-match on `error_category` (stable token), not on the prefix string.

`credential` and `plan_insufficient` are deliberately separate even though FMP raises the same `UnauthorizedError` class for both: the recovery path differs (rotate credentials vs. upgrade plan vs. fall back to free sub-mode).

## Failure Handling

This skill **is** the failure-handling reference. Per-wrapper SKILLs link here and add only wrapper-specific paths (e.g., "`etf --type holdings` returns `plan_insufficient` on a free FMP key — skip sub-mode").

## References

- `scripts/_common.py` — `ErrorCategory`, `classify_exception`, `safe_call`, `aggregate_emit`, `single_emit`.
- `../_envelope/SKILL.md`, `../_providers/SKILL.md`.
