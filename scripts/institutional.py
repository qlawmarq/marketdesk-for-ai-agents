"""Fetch 13F institutional holders per ticker.

Surfaces aggregate institutional ownership statistics so the analyst can
read concentration / drift signals from the most-recent 13F vintage.

Usage:
    uv run scripts/institutional.py AAPL MSFT
    uv run scripts/institutional.py AAPL --provider fmp

Providers:
  - fmp (default, only choice): requires FMP_API_KEY (free tier 250/day
    is sufficient — endpoint returns one row per ticker per quarter).

The upstream endpoint does not accept a row limit; the wrapper passes
through whatever rows the provider returns.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Any

from _common import aggregate_emit, is_fatal_aggregate, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


PROVIDER_CHOICES = ["fmp"]
DEFAULT_PROVIDER = "fmp"

_FILING_WINDOW_DAYS = 45  # SEC 17 CFR §240.13f-1


def _is_partial_filing_window(record_date: Any, today: date) -> bool:
    # `datetime` is a subclass of `date`, so branch on it first to strip
    # time / tz; `pandas.Timestamp` is a `datetime` subclass and is
    # absorbed by the same branch.
    if isinstance(record_date, datetime):
        record_date = record_date.date()
    elif isinstance(record_date, str):
        try:
            record_date = date.fromisoformat(record_date)
        except ValueError:
            return False
    elif not isinstance(record_date, date):
        return False
    return record_date + timedelta(days=_FILING_WINDOW_DAYS) > today


def _coerce_record_date(value: Any) -> date | None:
    """Share `_is_partial_filing_window`'s date-parse rules; return None on miss."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _build_partial_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return `{date, filing_deadline}` entries for every partial record.

    Mirrors `_is_partial_filing_window`'s fail-open date coercion: rows whose
    date cannot be parsed are silently skipped. Non-partial rows are ignored.
    The input list is not mutated.
    """

    summary: list[dict[str, Any]] = []
    for rec in records:
        if not rec.get("partial_filing_window"):
            continue
        parsed = _coerce_record_date(rec.get("date"))
        if parsed is None:
            continue
        deadline = parsed + timedelta(days=_FILING_WINDOW_DAYS)
        summary.append(
            {
                "date": parsed.isoformat(),
                "filing_deadline": deadline.isoformat(),
            }
        )
    return summary


_MASKED_FIELDS: frozenset[str] = frozenset(
    {
        "investors_holding",
        "investors_holding_change",
        "ownership_percent",
        "ownership_percent_change",
        "number_of_13f_shares",
        "number_of_13f_shares_change",
        "total_invested",
        "total_invested_change",
        "new_positions",
        "new_positions_change",
        "increased_positions",
        "increased_positions_change",
        "closed_positions",
        "closed_positions_change",
        "reduced_positions",
        "reduced_positions_change",
        "total_calls",
        "total_calls_change",
        "total_puts",
        "total_puts_change",
        "put_call_ratio",
        "put_call_ratio_change",
    }
)


def _apply_hide_partial(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Null out current-period numeric fields on partial=true records.

    Non-partial records pass through unchanged. Meta keys (`symbol`, `cik`,
    `date`, `partial_filing_window`) and all `last_*` prior-period values
    are preserved. The input list and its dicts are not mutated; a new
    list of shallow-copied dicts is returned.
    """

    out: list[dict[str, Any]] = []
    for rec in records:
        if not rec.get("partial_filing_window"):
            out.append(rec)
            continue
        masked = dict(rec)
        for key in _MASKED_FIELDS:
            if key in masked:
                masked[key] = None
        out.append(masked)
    return out


def _escape_md_cell(value: Any) -> str:
    """Render a single cell value safe for a markdown table.

    `None` becomes the empty string. Pipes are escaped, and any newline /
    carriage-return is collapsed so multi-paragraph cell content cannot break
    the table layout.
    """

    if value is None:
        return ""
    return (
        str(value)
        .replace("\r", "")
        .replace("|", "\\|")
        .replace("\n", " ")
    )


_MD_COLUMNS: tuple[str, ...] = (
    "date",
    "investors_holding",
    "ownership_percent",
    "number_of_13f_shares",
    "total_invested",
    "put_call_ratio",
)


def _render_institutional_markdown(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
) -> str:
    """Render the per-ticker institutional markdown document.

    Partial rows carry a leading `⚠ ` on the row and a `filing window:
    deadline YYYY-MM-DD` note in the trailing `notes` column. Numeric cells
    keep JSON raw values (no percent / k-M-B conversion) so md and JSON
    compare byte-for-byte on shared fields.
    """

    del meta  # reserved for future echo (e.g. hide_partial) — unused today
    sections: list[str] = []
    header_line = " | ".join(_MD_COLUMNS) + " | notes"
    separator_line = " | ".join(["---"] * (len(_MD_COLUMNS) + 1))
    for row in rows:
        symbol = row.get("symbol", "")
        lines: list[str] = [f"## {symbol}"]
        if not row.get("ok"):
            category = row.get("error_category", "")
            error = row.get("error", "")
            lines.append(f"_error_category_: {category} — {error}")
            sections.append("\n".join(lines))
            continue
        records = row.get("records", [])
        if not records:
            lines.append("_no records in quarter_")
            sections.append("\n".join(lines))
            continue
        lines.append(header_line)
        lines.append(separator_line)
        for record in records:
            cells = [_escape_md_cell(record.get(col)) for col in _MD_COLUMNS]
            is_partial = bool(record.get("partial_filing_window"))
            if is_partial:
                parsed = _coerce_record_date(record.get("date"))
                note = (
                    f"filing window: deadline "
                    f"{(parsed + timedelta(days=_FILING_WINDOW_DAYS)).isoformat()}"
                    if parsed is not None
                    else "filing window"
                )
                row_line = "⚠ " + " | ".join(cells) + f" | {note}"
            else:
                row_line = " | ".join(cells) + " | "
            lines.append(row_line)
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def _build_partial_warnings(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten per-ticker `partial_filing_window_records[]` into envelope
    top-level warning entries.

    Each entry is shaped so a JSON consumer can treat the envelope's
    `warnings[]` as a single surface to scan before reading
    `data.results[]` numerics. `warning_type` distinguishes these
    non-error signals from `aggregate_emit`'s row-failure warnings
    (which carry `error_category`).
    """

    warnings: list[dict[str, Any]] = []
    for row in results:
        if not row.get("ok"):
            continue
        symbol = row.get("symbol")
        for partial in row.get("partial_filing_window_records", []):
            warnings.append(
                {
                    "symbol": symbol,
                    "warning_type": "partial_filing_window",
                    "date": partial.get("date"),
                    "filing_deadline": partial.get("filing_deadline"),
                }
            )
    return warnings


def _emit_stderr_warning(results: list[dict[str, Any]]) -> None:
    """Print a three-line ⚠ block to stderr for each partial record.

    Iterates `partial_filing_window_records[]` per ticker and prints one
    block per partial record. Rows without the key (`ok: False` or no
    partials) are skipped. Callers suppress emission by not invoking
    this function (e.g. when `--no-stderr-warn` is set).
    """

    for row in results:
        symbol = row.get("symbol", "")
        for partial in row.get("partial_filing_window_records", []):
            record_date = partial.get("date", "")
            deadline = partial.get("filing_deadline", "")
            print(
                f"⚠ institutional: {symbol} {record_date} is in 13F filing window\n"
                f"  (deadline ≈ {deadline}); ownership_percent / investors_holding may be\n"
                f"  materially understated. Treat as preliminary; refresh after deadline.",
                file=sys.stderr,
            )


def fetch(
    symbol: str,
    provider: str,
    year: int | None = None,
    quarter: int | None = None,
) -> dict[str, Any]:
    opt = {k: v for k, v in (("year", year), ("quarter", quarter)) if v is not None}
    call_result = safe_call(
        obb.equity.ownership.institutional, symbol=symbol, provider=provider, **opt
    )
    if call_result.get("ok"):
        today = date.today()
        for rec in call_result["records"]:
            rec["partial_filing_window"] = _is_partial_filing_window(
                rec.get("date"), today
            )
        call_result["partial_filing_window_records"] = _build_partial_summary(
            call_result["records"]
        )
    return {"symbol": symbol, "provider": provider, **call_result}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Upstream returns in-flight quarters (quarter end + 45 days) as "
            "partial aggregates. With --year/--quarter omitted the current "
            "calendar quarter is selected; records still inside the filing "
            "window carry partial_filing_window: true. For a stable snapshot "
            "pass --year/--quarter pointing to a quarter older than 1 year. "
            "--quarter accepts 1-4 (validated upstream)."
        ),
    )
    parser.add_argument("symbols", nargs="+", help="One or more tickers")
    parser.add_argument(
        "--provider", default=DEFAULT_PROVIDER, choices=PROVIDER_CHOICES
    )
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--quarter", type=int, default=None)
    parser.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--hide-partial",
        action="store_true",
        default=False,
        help="Null out current-period numeric fields on partial_filing_window=true records",
    )
    parser.add_argument(
        "--no-stderr-warn",
        action="store_true",
        default=False,
        help="Suppress the stderr ⚠ warning block (for batch / CI use)",
    )
    args = parser.parse_args()

    results = [fetch(s, args.provider, year=args.year, quarter=args.quarter) for s in args.symbols]

    if args.hide_partial:
        for row in results:
            if row.get("ok"):
                row["records"] = _apply_hide_partial(row["records"])

    if not args.no_stderr_warn:
        _emit_stderr_warning(results)

    query_meta: dict[str, Any] = {
        "provider": args.provider,
        "hide_partial": args.hide_partial,
    }
    for k, v in (("year", args.year), ("quarter", args.quarter)):
        if v is not None:
            query_meta[k] = v

    if args.format == "md":
        if is_fatal_aggregate(results) is None:
            sys.stdout.write(_render_institutional_markdown(results, query_meta))
            return 0
    return aggregate_emit(
        results,
        tool="institutional",
        query_meta=query_meta,
        extra_warnings=_build_partial_warnings(results),
    )


if __name__ == "__main__":
    raise SystemExit(main())
