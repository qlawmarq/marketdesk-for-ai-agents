"""Helpers shared across every wrapper script.

- Uniform JSON output (`emit` / `emit_error`)
- Safe wrapping of OpenBB calls (exceptions become error records)
- Sequential processing of multiple symbols

This module has no side effects. Each script must call `apply_to_openbb()` itself.
"""

from __future__ import annotations

import contextlib
import enum
import io
import json
import math
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable


@contextlib.contextmanager
def silence_stdout():
    """Absorb warnings that OpenBB / yfinance providers dump to stdout.

    Prevents noise like the following from being interleaved with the JSON
    that AI agents parse:
      - yfinance: "HTTP Error 404" / "possibly delisted" / "X Failed download"
      - finviz:   "No ticker found."
      - openbb:   miscellaneous debug prints
    stderr is NOT suppressed (real errors stay visible to the agent).
    """
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = saved


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def to_records(result: Any) -> list[dict[str, Any]]:
    """Normalize an OBBject / DataFrame / list into a list of dicts."""
    if result is None:
        return []
    # OBBject
    if hasattr(result, "to_df"):
        df = result.to_df()
        if df is None or df.empty:
            return []
        # A named / non-default index (e.g. DatetimeIndex for historical)
        # carries information that must surface as a column. A default
        # RangeIndex is just row numbering — dropping it avoids leaking
        # an `index: 0,1,2,...` noise column into AI-facing JSON.
        try:
            is_default_range = (
                df.index.name is None
                and type(df.index).__name__ == "RangeIndex"
            )
            df = df.reset_index(drop=is_default_range)
        except Exception:  # noqa: BLE001
            pass
        return df.to_dict(orient="records")
    # Already a list of dicts
    if isinstance(result, list):
        return [r if isinstance(r, dict) else getattr(r, "__dict__", {"value": r}) for r in result]
    return [result if isinstance(result, dict) else {"value": result}]


class ErrorCategory(str, enum.Enum):
    """Stable category token attached to every `safe_call` error row.

    Agents pattern-match on this string rather than on provider-specific
    exception names or message fragments.

    `credential` and `plan_insufficient` are intentionally separate:
    "key missing/invalid" (401/403) recovers by rotating credentials,
    while "plan does not cover endpoint" (402 Restricted) recovers by
    changing subscription. FMP raises the same `UnauthorizedError`
    class for both, so only the message distinguishes them.
    """

    CREDENTIAL = "credential"
    PLAN_INSUFFICIENT = "plan_insufficient"
    TRANSIENT = "transient"
    VALIDATION = "validation"
    OTHER = "other"


CREDENTIAL_PREFIX = "CredentialError:"
PLAN_PREFIX = "PlanError:"

_CREDENTIAL_EXC_NAMES = {"UnauthorizedError"}
# 401 / 403 stay here; 402 and plan-tier wording moved to _PLAN_MESSAGE_RE.
_CREDENTIAL_MESSAGE_RE = re.compile(
    r"\b40[13]\b"
    r"|Unauthorized FMP request"
    r"|Missing credential"
)
_PLAN_MESSAGE_RE = re.compile(
    r"\b402\b"
    r"|Restricted Endpoint"
    r"|upgrade your plan"
    r"|subscription required"
)

_TRANSIENT_EXC_NAMES = {"TimeoutError", "ConnectionError", "ReadTimeout"}
_TRANSIENT_MESSAGE_RE = re.compile(r"\b5\d{2}\b")

_VALIDATION_EXC_NAMES = {"ValidationError", "ValueError", "KeyError"}


def classify_exception(exc: BaseException) -> ErrorCategory:
    """Dispatch an exception to its `ErrorCategory`.

    Dispatch order is message-regex-first, exception-class-fallback:
    FMP raises `UnauthorizedError` for both 401 (missing credential)
    and 402 (plan insufficient); the message is the only specific
    signal. Evaluating `_PLAN_MESSAGE_RE` before the credential
    exception-class fallback keeps the two recovery paths separable.

    Order:
      1. `_PLAN_MESSAGE_RE` → PLAN_INSUFFICIENT
      2. `_CREDENTIAL_MESSAGE_RE` → CREDENTIAL
      3. `_TRANSIENT_MESSAGE_RE` → TRANSIENT
      4. exception name in `_CREDENTIAL_EXC_NAMES` → CREDENTIAL
         (fallback for empty/unusual UnauthorizedError messages)
      5. exception name in `_TRANSIENT_EXC_NAMES` → TRANSIENT
      6. exception name in `_VALIDATION_EXC_NAMES` → VALIDATION
      7. OTHER
    """
    exc_names = {cls.__name__ for cls in type(exc).__mro__}
    message = str(exc)

    if _PLAN_MESSAGE_RE.search(message):
        return ErrorCategory.PLAN_INSUFFICIENT
    if _CREDENTIAL_MESSAGE_RE.search(message):
        return ErrorCategory.CREDENTIAL
    if _TRANSIENT_MESSAGE_RE.search(message):
        return ErrorCategory.TRANSIENT
    if exc_names & _CREDENTIAL_EXC_NAMES:
        return ErrorCategory.CREDENTIAL
    if exc_names & _TRANSIENT_EXC_NAMES:
        return ErrorCategory.TRANSIENT
    if exc_names & _VALIDATION_EXC_NAMES:
        return ErrorCategory.VALIDATION
    return ErrorCategory.OTHER


_CATEGORY_PREFIX: dict[ErrorCategory, str] = {
    ErrorCategory.CREDENTIAL: CREDENTIAL_PREFIX,
    ErrorCategory.PLAN_INSUFFICIENT: PLAN_PREFIX,
}


def _prefix_for_category(category: ErrorCategory) -> str | None:
    """Return the fatal-error message prefix for a category, or None."""

    return _CATEGORY_PREFIX.get(category)


def safe_call(fn: Callable[..., Any], /, **kwargs: Any) -> dict[str, Any]:
    """Execute an OpenBB API call safely; exceptions become an error record.

    Stdout warnings are absorbed as well, so the JSON output remains parseable.
    On exception, the row carries a stable `error_category` field
    (`credential`/`plan_insufficient`/`transient`/`validation`/`other`).
    Credential errors get a `CredentialError:` prefix; plan-insufficient
    errors get a parallel `PlanError:` prefix so agents can match on a
    single token regardless of provider wording.
    """
    try:
        with silence_stdout():
            result = fn(**kwargs)
        return {"ok": True, "records": to_records(result)}
    except Exception as exc:  # noqa: BLE001
        category = classify_exception(exc)
        message = str(exc)
        prefix = _prefix_for_category(category)
        if prefix is not None and not message.lstrip().startswith(prefix):
            message = f"{prefix} {message}"
        return {
            "ok": False,
            "error": message,
            "error_type": type(exc).__name__,
            "error_category": category.value,
        }


def sanitize_for_json(value: Any) -> Any:
    """Return a copy of ``value`` with NaN / +Inf / -Inf floats replaced by None.

    RFC 8259 has no representation for these IEEE-754 states; Python's
    ``json`` emits them as bare ``NaN`` / ``Infinity`` tokens when
    ``allow_nan=True``, which Node.js / jq / Go parsers reject. The
    sanitizer walks ``dict`` / ``list`` / ``tuple`` containers and
    substitutes those floats with ``None``. Dict keys, ints, bools,
    strings, finite floats, and non-container objects pass through
    untouched.

    Used exclusively by ``emit`` as the single chokepoint for strict
    JSON output (Req 1.1 / 1.2 / 1.4).
    """

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(v) for v in value]
    return value


def emit(payload: Any) -> None:
    """Write JSON to stdout followed by a newline.

    Uses ``allow_nan=False`` so a residual NaN / Inf that the
    sanitizer missed raises ``ValueError`` instead of silently emitting
    a non-standard ``NaN`` / ``Infinity`` token (Req 1.1 / 1.3).
    """
    sanitized = sanitize_for_json(payload)
    json.dump(
        sanitized,
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        default=str,
        allow_nan=False,
    )
    sys.stdout.write("\n")


def emit_error(message: str, **extra: Any) -> int:
    """Write an error message to stdout as JSON and return a non-zero exit code."""
    payload: dict[str, Any] = {"error": message, "collected_at": now_iso()}
    payload.update(extra)
    emit(payload)
    return 2


def wrap(data: Any, **meta: Any) -> dict[str, Any]:
    """Wrap data with common metadata in a shape ready for artifact placement."""
    base: dict[str, Any] = {
        "collected_at": now_iso(),
        "source": "marketdesk-for-ai-agents",
    }
    base.update(meta)
    base["data"] = data
    return base


_FATAL_CATEGORIES: tuple[ErrorCategory, ...] = (
    ErrorCategory.CREDENTIAL,
    ErrorCategory.PLAN_INSUFFICIENT,
)


def _all_rows_in_category(
    rows: list[dict[str, Any]], category: ErrorCategory
) -> bool:
    """True iff `rows` is non-empty and every row failed with `category`.

    A row "failed with `category`" iff `ok` is falsy and `error_category`
    equals `category.value`. Used to decide the fatal-exit branch for
    credential / plan-insufficient gates.
    """

    if not rows:
        return False
    return all(
        not r.get("ok") and r.get("error_category") == category.value for r in rows
    )


def _decide_exit_and_warnings(
    rows: list[dict[str, Any]],
) -> tuple[ErrorCategory | None, list[dict[str, Any]]]:
    """Classify a row batch into (fatal-category-or-None, per-failure-warnings).

    Returns the `ErrorCategory` whose prefix/error should be emitted at
    the envelope top level when every row failed with that single
    category (currently `CREDENTIAL` or `PLAN_INSUFFICIENT`), else None.
    Mixed fatal categories (e.g., some credential + some plan) return
    None so both survive via the warnings channel.

    Warning entries use `error_category` (matching the failed-row field
    name) so records and warnings can be parsed with a single key.
    """

    failed = [r for r in rows if not r.get("ok")]
    fatal: ErrorCategory | None = None
    for candidate in _FATAL_CATEGORIES:
        if _all_rows_in_category(rows, candidate):
            fatal = candidate
            break
    warnings = [
        {
            "symbol": r.get("symbol"),
            "error": r.get("error"),
            "error_category": r.get("error_category"),
        }
        for r in failed
    ]
    return fatal, warnings


def is_fatal_aggregate(rows: list[dict[str, Any]]) -> ErrorCategory | None:
    """Peek the fatal-exit gate without emitting.

    Returns the `ErrorCategory` that `aggregate_emit` would treat as a
    top-level fatal error (currently `CREDENTIAL` or `PLAN_INSUFFICIENT`)
    when every row failed with that single category, else `None`. Mixed
    fatal categories collapse to `None`, matching the existing internal
    contract used by `aggregate_emit`.

    Used by wrappers with a non-JSON success path (e.g. `--format md`)
    that need to fall back to `aggregate_emit`'s JSON envelope on a
    fatal exit so the recovery hint still reaches the agent.
    """

    fatal, _ = _decide_exit_and_warnings(rows)
    return fatal


def _fatal_error_message(category: ErrorCategory) -> str:
    """Top-level error string for an all-rows-in-category fatal emit."""

    prefix = _prefix_for_category(category) or ""
    return f"{prefix} all inputs rejected by the provider for {category.value} reasons"


_RESERVED_QUERY_META_KEYS: frozenset[str] = frozenset(
    {"source", "collected_at", "tool", "results"}
)


def _normalize_query_meta(query_meta: dict[str, Any] | None) -> dict[str, Any]:
    """Copy `query_meta` and reject keys that would clobber envelope/data slots.

    `source` / `collected_at` / `tool` sit at the envelope root and are
    owned by `wrap()`; `results` is the sibling of the per-query meta
    under `data/`. Allowing any of them through would silently overwrite
    an envelope invariant, so we fail fast instead.
    """

    if not query_meta:
        return {}
    collisions = _RESERVED_QUERY_META_KEYS & query_meta.keys()
    if collisions:
        raise ValueError(
            "query_meta keys collide with reserved envelope/data slots: "
            f"{sorted(collisions)}"
        )
    return dict(query_meta)


def aggregate_emit(
    rows: list[dict[str, Any]],
    *,
    tool: str,
    query_meta: dict[str, Any] | None = None,
    extra_warnings: list[dict[str, Any]] | None = None,
) -> int:
    """Decide the exit code from per-row categories and emit the envelope.

    - When every row failed with the same fatal category (`credential`
      or `plan_insufficient`), emit a top-level error via `emit_error`
      with the corresponding prefix (`CredentialError:` / `PlanError:`)
      and return exit code 2 so the recovery path (rotate credentials
      vs. upgrade plan) surfaces without the agent reading the body.
    - Otherwise emit one envelope top-level `warnings` entry per failed
      row (`{symbol, error, error_category}`), then append
      `extra_warnings` verbatim. Only set `warnings` when the combined
      list is non-empty. Return exit code 0.

    `query_meta` keys are placed under `data/` as siblings of `results`
    (matching `single_emit`); the envelope root is restricted at the
    signature level to `{source, collected_at, tool, data}` plus the
    optional `{warnings, error}` slots.
    """

    meta = _normalize_query_meta(query_meta)
    extras = list(extra_warnings) if extra_warnings else []
    fatal, row_warnings = _decide_exit_and_warnings(rows)

    if fatal is not None:
        return emit_error(
            _fatal_error_message(fatal),
            details=[r.get("error") for r in rows],
            tool=tool,
            error_category=fatal.value,
        )

    warnings = row_warnings + extras
    envelope = wrap({"results": rows, **meta}, tool=tool)
    if warnings:
        envelope["warnings"] = warnings
    emit(envelope)
    return 0


def single_emit(
    call_result: dict[str, Any],
    *,
    tool: str,
    query_meta: dict[str, Any] | None = None,
    extra_warnings: list[dict[str, Any]] | None = None,
) -> int:
    """Emit a single-query wrapper's envelope with the shared exit-code gate.

    - Success: stdout `{..., data:{results:[records...], **query_meta}}`,
      return 0.
    - Credential failure: `emit_error` with the `CredentialError:`-prefixed
      message and exit code 2.
    - Other failure: stdout `{..., data:{results:[], **query_meta},
      warnings:[{symbol:None, error, error_category}, *extras]}`, return 0.

    Invariant: `payload["data"]["results"]` is always a list (empty when the
    call failed for non-credential reasons), so agents can dereference it
    without branching on success/failure. The envelope root is restricted
    at the signature level to `{source, collected_at, tool, data}` plus the
    optional `{warnings, error}` slots.
    """

    meta = _normalize_query_meta(query_meta)
    extras = list(extra_warnings) if extra_warnings else []

    if call_result.get("ok"):
        data: dict[str, Any] = {"results": call_result.get("records", []), **meta}
        envelope = wrap(data, tool=tool)
        if extras:
            envelope["warnings"] = extras
        emit(envelope)
        return 0

    error_message = call_result.get("error") or ""
    error_category = call_result.get("error_category")

    fatal_prefix: str | None = None
    for category in _FATAL_CATEGORIES:
        if error_category == category.value:
            fatal_prefix = _prefix_for_category(category)
            break
    if fatal_prefix is not None:
        if not error_message.lstrip().startswith(fatal_prefix):
            error_message = f"{fatal_prefix} {error_message}".strip()
        return emit_error(error_message, tool=tool, error_category=error_category)

    data = {"results": [], **meta}
    envelope = wrap(data, tool=tool)
    failure_warning = {
        "symbol": None,
        "error": call_result.get("error"),
        "error_category": error_category,
    }
    envelope["warnings"] = [failure_warning] + extras
    emit(envelope)
    return 0
