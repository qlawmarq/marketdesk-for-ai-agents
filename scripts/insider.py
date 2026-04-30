"""Fetch Form 4 insider-trading records per ticker.

Surfaces officer/director/10%-owner transactions so the analyst can
read capital-structure and disclosure signals without a Python REPL.

Usage:
    uv run scripts/insider.py AAPL MSFT
    uv run scripts/insider.py AAPL --days 30 --provider sec
    uv run scripts/insider.py AAPL --provider fmp --limit 100

Providers (key-free first):
  - sec (default): no key beyond SEC_USER_AGENT
  - fmp: requires FMP_API_KEY
  - intrinio: requires intrinio key
  - tmx: Canadian listings

Day-window semantics: the upstream endpoint accepts a row `limit`, not a
date window, so `--days N` is applied client-side on each record's
`transaction_date` (or `filing_date` when transaction_date is missing).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Mapping

from _common import aggregate_emit, is_fatal_aggregate, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


PROVIDER_CHOICES = ["sec", "fmp", "intrinio", "tmx"]
DEFAULT_PROVIDER = "sec"


_CODE_TOKEN_RE = re.compile(r"^[A-Za-z]$")


_SEC_TYPE_TO_CODE: Mapping[str, str] = MappingProxyType(
    {
        "Open market or private purchase of non-derivative or derivative security": "P",
        "Open market or private sale of non-derivative or derivative security": "S",
        "Grant, award or other acquisition pursuant to Rule 16b-3(d)": "A",
        (
            "Payment of exercise price or tax liability by delivering or "
            "withholding securities incident to the receipt, exercise or "
            "vesting of a security issued in accordance with Rule 16b-3"
        ): "F",
        "Exercise or conversion of derivative security exempted pursuant to Rule 16b-3": "M",
        "Conversion of derivative security": "C",
        "Bona fide gift": "G",
        "Disposition to the issuer of issuer equity securities pursuant to Rule 16b-3(e)": "D",
        "Other acquisition or disposition (describe transaction)": "J",
        "Acquisition or disposition by will or the laws of descent and distribution": "W",
    }
)


_CODE_TO_LABEL: Mapping[str, str] = MappingProxyType(
    {
        "P": "Open Market Purchase",
        "S": "Open Market Sale",
        "A": "Grant or Award",
        "F": "Tax Withholding Payment",
        "M": "Exempt Exercise or Conversion",
        "C": "Derivative Conversion",
        "G": "Bona Fide Gift",
        "D": "Disposition to Issuer",
        "J": "Other (see footnote)",
        "W": "Will or Inheritance",
    }
)


_FMP_CODE_RE = re.compile(r"^([A-Z])-")


_OWNERSHIP_TYPE_EXPAND: Mapping[str, str] = MappingProxyType(
    {"D": "Direct", "I": "Indirect"}
)


_CANONICAL_KEYS: tuple[str, ...] = (
    "filing_date",
    "transaction_date",
    "reporter_name",
    "reporter_title",
    "transaction_code",
    "transaction_code_label",
    "transaction_type_raw",
    "acquisition_or_disposition",
    "shares",
    "price",
    "total_value",
    "shares_after",
    "form_type",
    "url",
    "ownership_type",
    "security_type",
    "company_cik",
    "owner_cik",
    "footnote",
)


_INFIX_MARKERS = (", officer: ", ", director: ", ", ten_percent_owner: ")
_PREFIX_MARKERS = ("officer: ", "director: ", "ten_percent_owner: ")
_BARE_ROLES: Mapping[str, str] = MappingProxyType(
    {
        "officer": "Officer",
        "director": "Director",
        "ten_percent_owner": "Ten Percent Owner",
    }
)


_SEEN_UNMAPPED_SEC_STRINGS: set[str] = set()


def _lookup_sec_code(value: str | None) -> str | None:
    """Map an SEC long-English transaction-type to its single-letter code.

    Returns the code on exact match, ``None`` on miss or empty input.
    Emits a one-time-per-session stderr line on the first miss for any
    given input string so corpus drift is triageable without polluting
    stdout.
    """

    if not value:
        return None
    code = _SEC_TYPE_TO_CODE.get(value)
    if code is not None:
        return code
    if value not in _SEEN_UNMAPPED_SEC_STRINGS:
        _SEEN_UNMAPPED_SEC_STRINGS.add(value)
        print(
            f"insider: unmapped SEC transaction_type: {value!r}",
            file=sys.stderr,
        )
    return None


def _extract_fmp_code(value: str | None) -> str | None:
    """Return the leading letter of an FMP ``X-Description`` shape, else ``None``."""

    if not value:
        return None
    match = _FMP_CODE_RE.match(value)
    return match.group(1) if match else None


def _strip_role_prefix(value: str | None) -> str | None:
    """Remove FMP role prefixes / infixes from a reporter title."""

    if not value:
        return value
    for marker in _INFIX_MARKERS:
        idx = value.rfind(marker)
        if idx != -1:
            tail = value[idx + len(marker) :].strip()
            return tail or None
    for prefix in _PREFIX_MARKERS:
        if value.startswith(prefix):
            tail = value[len(prefix) :].strip()
            return tail or None
    if value in _BARE_ROLES:
        return _BARE_ROLES[value]
    return value


def _compute_total_value(
    shares: float | int | None, price: float | int | None
) -> float | None:
    """Return ``shares * price`` only when both are non-null and ``price > 0``."""

    if shares is None or price is None:
        return None
    if price <= 0:
        return None
    return float(shares) * float(price)


def _normalize_sec_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project an SEC upstream record into the canonical 19-field schema."""

    raw_type = record.get("transaction_type")
    code = _lookup_sec_code(raw_type)
    shares = record.get("securities_transacted")
    price = record.get("transaction_price")
    acq = record.get("acquisition_or_disposition")
    if acq == "Acquisition":
        acq_norm: str | None = "A"
    elif acq == "Disposition":
        acq_norm = "D"
    else:
        acq_norm = None
    return {
        "filing_date": record.get("filing_date"),
        "transaction_date": record.get("transaction_date"),
        "reporter_name": record.get("owner_name"),
        "reporter_title": record.get("owner_title"),
        "transaction_code": code,
        "transaction_code_label": _CODE_TO_LABEL.get(code) if code else None,
        "transaction_type_raw": raw_type,
        "acquisition_or_disposition": acq_norm,
        "shares": shares,
        "price": price,
        "total_value": _compute_total_value(shares, price),
        "shares_after": record.get("securities_owned"),
        "form_type": record.get("form"),
        "url": record.get("filing_url"),
        "ownership_type": record.get("ownership_type"),
        "security_type": record.get("security_type"),
        "company_cik": record.get("company_cik"),
        "owner_cik": record.get("owner_cik"),
        "footnote": record.get("footnote"),
    }


def _normalize_fmp_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project an FMP upstream record into the canonical 19-field schema."""

    raw_type = record.get("transaction_type")
    code = _extract_fmp_code(raw_type)
    shares = record.get("securities_transacted")
    price = record.get("transaction_price")
    return {
        "filing_date": record.get("filing_date"),
        "transaction_date": record.get("transaction_date"),
        "reporter_name": record.get("owner_name"),
        "reporter_title": _strip_role_prefix(record.get("owner_title")),
        "transaction_code": code,
        "transaction_code_label": _CODE_TO_LABEL.get(code) if code else None,
        "transaction_type_raw": raw_type,
        "acquisition_or_disposition": record.get("acquisition_or_disposition"),
        "shares": shares,
        "price": price,
        "total_value": _compute_total_value(shares, price),
        "shares_after": record.get("securities_owned"),
        "form_type": record.get("form_type"),
        "url": record.get("url"),
        "ownership_type": _OWNERSHIP_TYPE_EXPAND.get(
            record.get("ownership_type") or ""
        ),
        "security_type": record.get("security_type"),
        "company_cik": record.get("company_cik"),
        "owner_cik": record.get("owner_cik"),
        "footnote": record.get("footnote"),
    }


def _normalize_other_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project an intrinio / tmx record into the canonical 19-field schema.

    No FMP-specific renames and no role-prefix stripping; ``transaction_code``
    is always ``null``. Canonical keys whose name happens to coincide with the
    upstream key pass through via ``dict.get``; everything else is ``null``.
    """

    return {
        "filing_date": record.get("filing_date"),
        "transaction_date": record.get("transaction_date"),
        "reporter_name": record.get("reporter_name"),
        "reporter_title": record.get("reporter_title"),
        "transaction_code": None,
        "transaction_code_label": None,
        "transaction_type_raw": record.get("transaction_type"),
        "acquisition_or_disposition": record.get("acquisition_or_disposition"),
        "shares": record.get("shares"),
        "price": record.get("price"),
        "total_value": record.get("total_value"),
        "shares_after": record.get("shares_after"),
        "form_type": record.get("form_type"),
        "url": record.get("url"),
        "ownership_type": record.get("ownership_type"),
        "security_type": record.get("security_type"),
        "company_cik": record.get("company_cik"),
        "owner_cik": record.get("owner_cik"),
        "footnote": record.get("footnote"),
    }


def _positive_int(value: str) -> int:
    """argparse ``type=`` validator for ``--days``: accept positive integers only."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"expected a positive integer; got {value!r}"
        ) from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer; got {value!r}"
        )
    return parsed


def _parse_codes_csv(value: str) -> list[str]:
    """argparse ``type=`` validator for ``--transaction-codes``.

    Splits on commas, strips per-element whitespace, validates each element
    matches a single ASCII letter, and uppercases each before returning.
    """

    tokens = [chunk.strip() for chunk in value.split(",")]
    codes: list[str] = []
    for token in tokens:
        if not _CODE_TOKEN_RE.match(token):
            raise argparse.ArgumentTypeError(
                "each --transaction-codes entry must be a single ASCII letter; "
                f"got {token!r}"
            )
        codes.append(token.upper())
    return codes


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def _apply_code_filter(
    records: list[dict[str, Any]],
    codes: list[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    """Filter normalized records by ``transaction_code``.

    When ``codes`` is ``None``, returns ``(records, 0)`` unchanged. Otherwise
    walks the input once, keeps records whose ``transaction_code`` (already
    uppercase) is in the filter set, drops records whose ``transaction_code``
    is ``None`` and counts those drops, and silently rejects records whose
    non-null code does not match the filter without counting them.
    """

    if codes is None:
        return records, 0
    keep_set = set(codes)
    kept: list[dict[str, Any]] = []
    dropped_unparseable = 0
    for record in records:
        code = record.get("transaction_code")
        if code is None:
            dropped_unparseable += 1
            continue
        if code in keep_set:
            kept.append(record)
    return kept, dropped_unparseable


_MD_COLUMNS: tuple[str, ...] = (
    "filing_date",
    "transaction_date",
    "reporter_name",
    "reporter_title",
    "transaction_code",
    "transaction_code_label",
    "shares",
    "price",
    "total_value",
    "shares_after",
    "url",
)


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


def _render_markdown(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
) -> str:
    """Render the per-symbol markdown document from aggregate-shaped rows."""

    codes_filter = meta.get("transaction_codes")
    sections: list[str] = []
    header_line = " | ".join(_MD_COLUMNS)
    separator_line = " | ".join(["---"] * len(_MD_COLUMNS))
    for row in rows:
        symbol = row.get("symbol", "")
        lines: list[str] = [f"## {symbol}"]
        if not row.get("ok"):
            category = row.get("error_category", "")
            error = row.get("error", "")
            lines.append(f"_error_category_: {category} — {error}")
        else:
            records = row.get("records", [])
            if not records:
                empty_line = "_no records in window_"
                dropped = row.get("dropped_unparseable_codes", 0)
                if codes_filter is not None and dropped > 0:
                    empty_line += f" (dropped {dropped} unparseable codes)"
                lines.append(empty_line)
            else:
                lines.append(header_line)
                lines.append(separator_line)
                for record in records:
                    lines.append(
                        " | ".join(
                            _escape_md_cell(record.get(col)) for col in _MD_COLUMNS
                        )
                    )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _filter_by_days(records: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=days)
    kept: list[dict[str, Any]] = []
    for record in records:
        record_date = _coerce_date(record.get("transaction_date")) or _coerce_date(
            record.get("filing_date")
        )
        if record_date is None or record_date >= cutoff:
            kept.append(record)
    return kept


def _normalize_record(record: dict[str, Any], provider: str) -> dict[str, Any]:
    """Dispatch a raw upstream record to the matching per-provider normalizer."""

    if provider == "sec":
        return _normalize_sec_record(record)
    if provider == "fmp":
        return _normalize_fmp_record(record)
    return _normalize_other_record(record)


def fetch(
    symbol: str,
    provider: str,
    days: int,
    limit: int | None,
    codes_filter: list[str] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    if limit is not None:
        kwargs["limit"] = limit
    call_result = safe_call(obb.equity.ownership.insider_trading, **kwargs)
    if not call_result.get("ok"):
        return {"symbol": symbol, "provider": provider, **call_result}
    normalized = [_normalize_record(r, provider) for r in call_result["records"]]
    in_window = _filter_by_days(normalized, days)
    kept, dropped_unparseable = _apply_code_filter(in_window, codes_filter)
    return {
        "symbol": symbol,
        "provider": provider,
        "ok": True,
        "records": kept,
        "dropped_unparseable_codes": dropped_unparseable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbols", nargs="+", help="One or more tickers")
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=PROVIDER_CHOICES,
    )
    parser.add_argument(
        "--days",
        type=_positive_int,
        default=90,
        help="Client-side window applied to transaction_date (default: 90)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Upstream row limit passed through to the provider",
    )
    parser.add_argument(
        "--transaction-codes",
        type=_parse_codes_csv,
        default=None,
        help=(
            "Comma-separated single-letter Form 4 codes (e.g. P,S). "
            "Each entry is uppercased; rows whose normalized transaction_code "
            "is null are dropped and counted under dropped_unparseable_codes."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args()

    codes_filter: list[str] | None = args.transaction_codes
    results = [
        fetch(symbol, args.provider, args.days, args.limit, codes_filter)
        for symbol in args.symbols
    ]
    query_meta: dict[str, Any] = {
        "provider": args.provider,
        "days": args.days,
        "transaction_codes": codes_filter,
    }
    if args.limit is not None:
        query_meta["limit"] = args.limit

    if args.format == "md":
        if is_fatal_aggregate(results) is None:
            sys.stdout.write(_render_markdown(results, query_meta) + "\n")
            return 0
    return aggregate_emit(results, tool="insider", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
