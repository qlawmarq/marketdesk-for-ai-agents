"""Fetch SEC filings index per ticker, optionally filtered by form type.

Surfaces the disclosure stream (10-K, 10-Q, 8-K, 4, 13F-HR, ...) so the
analyst can locate the underlying EDGAR documents from the same uniform
JSON envelope as every other wrapper.

Usage:
    uv run scripts/filings.py AAPL
    uv run scripts/filings.py AAPL --form 10-K,10-Q --limit 20
    uv run scripts/filings.py AAPL --form 8-K --provider fmp

Providers (key-free first):
  - sec (default): no key beyond SEC_USER_AGENT; accepts a CSV form_type
    natively, so the form filter is dispatched server-side
  - fmp: requires FMP_API_KEY; the wrapper post-filters on `report_type`
  - intrinio: passes form_type natively (single form)
  - nasdaq / tmx: post-filtered client-side

The requested form filter is echoed inside each per-symbol entry as
`form_filter`, so callers can distinguish "no filings of that form" from
"unknown form name" returns.
"""

from __future__ import annotations

import argparse
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


PROVIDER_CHOICES = ["sec", "fmp", "intrinio", "nasdaq", "tmx"]
DEFAULT_PROVIDER = "sec"

# Providers that accept the `form_type` kwarg directly (verified live
# 2026-04-25). For other providers the wrapper post-filters the
# `report_type` field client-side.
_NATIVE_FORM_FILTER_PROVIDERS = frozenset({"sec", "intrinio"})


def _parse_forms(form_arg: str | None) -> list[str] | None:
    if not form_arg:
        return None
    parts = [p.strip() for p in form_arg.split(",") if p.strip()]
    seen: dict[str, None] = {}
    for part in parts:
        seen.setdefault(part, None)
    return list(seen.keys()) or None


def _post_filter(records: list[dict[str, Any]], forms: list[str]) -> list[dict[str, Any]]:
    wanted = {f.casefold() for f in forms}
    kept: list[dict[str, Any]] = []
    for record in records:
        report_type = record.get("report_type") or record.get("form_type")
        if isinstance(report_type, str) and report_type.casefold() in wanted:
            kept.append(record)
    return kept


def fetch(
    symbol: str,
    provider: str,
    forms: list[str] | None,
    limit: int | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    if limit is not None:
        kwargs["limit"] = limit
    if forms and provider in _NATIVE_FORM_FILTER_PROVIDERS:
        kwargs["form_type"] = ",".join(forms)
    call_result = safe_call(obb.equity.fundamental.filings, **kwargs)
    entry: dict[str, Any] = {
        "symbol": symbol,
        "provider": provider,
        "form_filter": forms,
        **call_result,
    }
    if call_result.get("ok") and forms and provider not in _NATIVE_FORM_FILTER_PROVIDERS:
        entry["records"] = _post_filter(call_result["records"], forms)
    return entry


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
        "--form",
        default=None,
        help="Comma-separated SEC form types (e.g. '10-K,10-Q,8-K,4')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Upstream row limit passed through to the provider",
    )
    args = parser.parse_args()

    forms = _parse_forms(args.form)
    results = [fetch(symbol, args.provider, forms, args.limit) for symbol in args.symbols]
    query_meta: dict[str, Any] = {"provider": args.provider}
    if forms is not None:
        query_meta["form_filter"] = forms
    if args.limit is not None:
        query_meta["limit"] = args.limit
    return aggregate_emit(results, tool="filings", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
