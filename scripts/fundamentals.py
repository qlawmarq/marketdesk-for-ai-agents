"""Unified wrapper for three-statement financials, ratios, and key metrics.

Supported types:
  --type overview   Company profile
  --type income     Income statement
  --type balance    Balance sheet
  --type cash       Cash flow
  --type ratios     Financial ratios
  --type metrics    Key metrics (market cap, EV, FCF, etc.)

Usage:
    uv run scripts/fundamentals.py AAPL --type ratios --limit 5
    uv run scripts/fundamentals.py 7203.T --type metrics --provider yfinance
    uv run scripts/fundamentals.py AAPL --type income --period annual --limit 5

About providers:
  - Every retained sub-mode defaults to yfinance (free). fmp remains
    available via `--provider fmp` when a richer field set is needed.
  - Note that yfinance returns a subset of the fields fmp would expose.
  - `--type ratios` tags numeric ratio / decimal fields as
    `{"value": ..., "unit": ...}` with `unit ∈ {decimal, ratio}` so
    downstream agents never have to consult `--help` to know whether
    ROE arrives as a decimal fraction or a plain ratio. Per-share
    currency amounts, identifiers, and dates pass through untagged
    (see `_schema.PER_SHARE_FIELDS` / `RATIO_PASSTHROUGH_FIELDS`).
    No numeric rescaling: FMP's percent-measured fields are already
    stored in decimal form.
  - `--type metrics` (yfinance) tags every field registered in
    `_schema.METRIC_UNIT_MAP` as `{"value": ..., "unit": ...}` with
    `unit ∈ {decimal, percent, ratio, currency}`. yfinance mixes
    conventions in a single record (e.g. `gross_margin` is decimal,
    `debt_to_equity` / `dividend_yield` are percent, `market_cap` is a
    currency total), so the tag is what lets downstream agents compare
    magnitudes safely. Per-share amounts, identifiers, dates, and any
    field not in the map pass through as bare numbers (fail-closed —
    unknown fields are never coerced to a ratio).
  - ratios / metrics 共通で decimal タグ付き異常値は `status: "suspicious"` で flag される。
"""

from __future__ import annotations

import argparse
import math
from typing import Any

from _common import aggregate_emit, safe_call
from _env import apply_to_openbb
from _schema import (
    DECIMAL_SANITY_MAX,
    PER_SHARE_FIELDS as _PER_SHARE_FIELDS,
    RATIO_PASSTHROUGH_FIELDS as _RATIO_PASSTHROUGH_FIELDS,
    classify_metric_unit,
    classify_ratio_unit,
    is_suspicious_decimal,
)
from openbb import obb

apply_to_openbb()


TYPE_TO_CALLABLE = {
    "overview": lambda **kw: obb.equity.profile(**kw),
    "income": lambda **kw: obb.equity.fundamental.income(**kw),
    "balance": lambda **kw: obb.equity.fundamental.balance(**kw),
    "cash": lambda **kw: obb.equity.fundamental.cash(**kw),
    "ratios": lambda **kw: obb.equity.fundamental.ratios(**kw),
    "metrics": lambda **kw: obb.equity.fundamental.metrics(**kw),
}

DEFAULT_PROVIDERS = {
    "overview": "yfinance",
    "income": "yfinance",
    "balance": "yfinance",
    "cash": "yfinance",
    # ratios: OpenBB's obb.equity.fundamental.ratios only accepts fmp/intrinio,
    # so the sub-mode stays on fmp and remains FMP_API_KEY-gated in the
    # integration suite. The unit-normalization pipeline below still runs.
    "ratios": "fmp",
    "metrics": "yfinance",
}


def normalize_ratio_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag every ratio field with ``{"value": ..., "unit": ...}``.

    Identifier / date / per-share currency fields pass through
    unchanged, as do ``None``, NaN, and boolean values. No provider
    rescaling: FMP's decimal-measured fields are already in decimal
    form, and yfinance does not expose the ratios endpoint at all —
    so the unit tag is purely informational, never a transformation.
    """

    tagged: list[dict[str, Any]] = []
    for record in records:
        out: dict[str, Any] = {}
        for key, value in record.items():
            if key in _RATIO_PASSTHROUGH_FIELDS or key in _PER_SHARE_FIELDS:
                out[key] = value
                continue
            if value is None or isinstance(value, bool):
                out[key] = value
                continue
            if isinstance(value, float) and math.isnan(value):
                out[key] = value
                continue
            if isinstance(value, (int, float)):
                unit = classify_ratio_unit(key)
                out[key] = {"value": float(value), "unit": unit}
                continue
            out[key] = value
        tagged.append(out)
    return tagged


def normalize_metric_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag yfinance metrics fields with ``{"value": ..., "unit": ...}``.

    Unknown fields pass through untagged (fail-closed) so per-share USD
    amounts like ``book_value`` and discrete governance scores are never
    silently coerced to a ratio. Intentionally independent of
    ``normalize_ratio_records``: the classifier, unit vocabulary, and
    fallback contract differ by provider.
    """

    tagged: list[dict[str, Any]] = []
    for record in records:
        out: dict[str, Any] = {}
        for key, value in record.items():
            if key in _RATIO_PASSTHROUGH_FIELDS or key in _PER_SHARE_FIELDS:
                out[key] = value
                continue
            if value is None or isinstance(value, bool):
                out[key] = value
                continue
            if isinstance(value, float) and math.isnan(value):
                out[key] = value
                continue
            if isinstance(value, (int, float)):
                unit = classify_metric_unit(key)
                if unit is None:
                    out[key] = value
                else:
                    out[key] = {"value": float(value), "unit": unit}
                continue
            out[key] = value
        tagged.append(out)
    return tagged


def flag_suspicious_decimals(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Annotate decimal-tagged cells whose magnitude exceeds the sanity bound.

    Mutates each flagged cell with ``status="suspicious"`` and appends a
    ``{field, symbol, value, reason}`` entry to the returned warnings list
    so the aggregator can expose the hit at the top level. Returns the same
    ``records`` list for ergonomic chaining.
    """

    warnings: list[dict[str, Any]] = []
    for record in records:
        symbol = record.get("symbol")
        for key, cell in record.items():
            if not is_suspicious_decimal(cell):
                continue
            cell["status"] = "suspicious"
            warnings.append(
                {
                    "field": key,
                    "symbol": symbol,
                    "value": cell.get("value"),
                    "reason": (
                        f"|value| exceeds DECIMAL_SANITY_MAX "
                        f"({DECIMAL_SANITY_MAX}); possible unit-tag mismatch"
                    ),
                }
            )
    return records, warnings


def fetch(symbol: str, fund_type: str, provider: str, period: str, limit: int) -> dict[str, Any]:
    fn = TYPE_TO_CALLABLE[fund_type]
    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    # overview / metrics do not accept period / limit
    if fund_type in {"income", "balance", "cash", "ratios"}:
        kwargs["period"] = period
        kwargs["limit"] = limit
    result = safe_call(fn, **kwargs)
    if fund_type == "ratios" and result.get("ok") and isinstance(result.get("records"), list):
        result["records"] = normalize_ratio_records(result["records"])
    elif fund_type == "metrics" and result.get("ok") and isinstance(result.get("records"), list):
        result["records"] = normalize_metric_records(result["records"])
    return {"symbol": symbol, "type": fund_type, "provider": provider, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument(
        "--type",
        required=True,
        choices=list(TYPE_TO_CALLABLE.keys()),
        help="Data type to fetch",
    )
    parser.add_argument("--provider", default=None, help="Provider (defaults per type if omitted)")
    parser.add_argument("--period", default="annual", choices=["annual", "quarter"], help="Period (3-statement financials / ratios only)")
    parser.add_argument("--limit", type=int, default=5, help="Most recent N periods (3-statement financials / ratios only)")
    args = parser.parse_args()

    provider = args.provider or DEFAULT_PROVIDERS[args.type]
    results = [fetch(s, args.type, provider, args.period, args.limit) for s in args.symbols]

    extra_warnings: list[dict[str, Any]] = []
    if args.type in {"ratios", "metrics"}:
        for row in results:
            if row.get("ok") and isinstance(row.get("records"), list):
                row["records"], row_warnings = flag_suspicious_decimals(row["records"])
                extra_warnings.extend(row_warnings)

    return aggregate_emit(
        results,
        tool="fundamentals",
        query_meta={"provider": provider, "type": args.type},
        extra_warnings=extra_warnings or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
