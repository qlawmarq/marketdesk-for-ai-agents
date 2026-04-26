"""Fetch the options chain or a derived implied-volatility view for a ticker.

Surfaces options market data so the analyst can read positioning and
volatility signals without dropping into a Python REPL. ``--type chain``
returns the raw per-contract chain; ``--type iv`` aggregates the chain into
a derived per-expiration view (ATM implied volatility and an
open-interest-weighted put/call ratio).

Usage:
    uv run scripts/options.py AAPL
    uv run scripts/options.py AAPL --type iv
    uv run scripts/options.py AAPL --type chain --expiration 2026-05-15
    uv run scripts/options.py AAPL --provider cboe

Providers (key-free first):
  - yfinance (default): no key, fastest
  - cboe: no key, US listings only

Single-symbol only. Multi-symbol input is intentionally unsupported because
each chain returns 2-3k rows and per-symbol fanout would blow up the
envelope.

The paid-only ``--type unusual`` sub-mode (intrinio) is intentionally
omitted; see README §1-2 footnote for the deferral rationale.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from typing import Any

from _common import safe_call, single_emit
from _env import apply_to_openbb
from openbb import obb

apply_to_openbb()


# Live provider literal probe 2026-04-25:
#   obb.derivatives.options.chains -> ['cboe', 'deribit', 'intrinio', 'tmx', 'tradier', 'yfinance']
# Narrowed to the key-free US-equity providers per design §options.py.
PROVIDER_CHOICES = ["yfinance", "cboe"]
DEFAULT_PROVIDER = "yfinance"

TYPE_CHOICES = ["chain", "iv"]
DEFAULT_TYPE = "chain"

# Fields that derive_iv_view depends on; presence is reported per expiration
# so callers can disambiguate "no data" from "provider lacks the field".
_REQUIRED_IV_FIELDS = ("implied_volatility", "open_interest", "underlying_price")


def _parse_iso_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _coerce_expiration(value: Any) -> date | None:
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


def _filter_by_expiration(
    records: list[dict[str, Any]], expiration: date
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for record in records:
        record_exp = _coerce_expiration(record.get("expiration"))
        if record_exp == expiration:
            kept.append(record)
    return kept


def _is_present(value: Any) -> bool:
    """True iff ``value`` is a usable numeric (not None / not NaN)."""
    if value is None:
        return False
    try:
        return value == value  # NaN != NaN
    except Exception:  # noqa: BLE001
        return True


def _atm_iv_for_side(
    contracts: list[dict[str, Any]], underlying: float
) -> float | None:
    """Return the IV of the strike nearest ``underlying`` for one side, or None."""
    candidates = [
        c for c in contracts
        if _is_present(c.get("strike")) and _is_present(c.get("implied_volatility"))
    ]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda c: abs(float(c["strike"]) - underlying))
    return float(nearest["implied_volatility"])


def derive_iv_view(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate raw chain rows into a per-expiration ATM IV / put-call view.

    Returns ``{"records": [...], "missing_fields": [...]}`` where each output
    row is ``{expiration, atm_iv, put_call_oi_ratio}``. ``atm_iv`` averages
    put / call ATM IVs when both sides have data, falls back to whichever
    side is present, or ``None`` when neither side has IV. ``put_call_oi_ratio``
    is ``sum(put OI) / sum(call OI)`` per expiration, or ``None`` when calls
    have no open interest. ``missing_fields`` lists ``_REQUIRED_IV_FIELDS``
    members that are absent on every record.
    """

    if not records:
        return {"records": [], "missing_fields": []}

    missing = [
        f for f in _REQUIRED_IV_FIELDS
        if not any(_is_present(r.get(f)) for r in records)
    ]

    # group by expiration preserving insertion order
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        exp = _coerce_expiration(record.get("expiration"))
        if exp is None:
            continue
        grouped.setdefault(exp, []).append(record)

    out: list[dict[str, Any]] = []
    for exp in sorted(grouped.keys()):
        side_records = grouped[exp]
        underlying_values = [
            float(r["underlying_price"])
            for r in side_records
            if _is_present(r.get("underlying_price"))
        ]
        underlying = underlying_values[0] if underlying_values else None

        calls = [r for r in side_records if r.get("option_type") == "call"]
        puts = [r for r in side_records if r.get("option_type") == "put"]

        if underlying is None:
            atm_iv: float | None = None
        else:
            atm_call = _atm_iv_for_side(calls, underlying)
            atm_put = _atm_iv_for_side(puts, underlying)
            sides = [v for v in (atm_call, atm_put) if v is not None]
            atm_iv = sum(sides) / len(sides) if sides else None

        call_oi = sum(
            float(c["open_interest"]) for c in calls if _is_present(c.get("open_interest"))
        )
        put_oi = sum(
            float(p["open_interest"]) for p in puts if _is_present(p.get("open_interest"))
        )
        put_call_oi_ratio: float | None = put_oi / call_oi if call_oi > 0 else None

        out.append(
            {
                "expiration": exp.isoformat(),
                "atm_iv": atm_iv,
                "put_call_oi_ratio": put_call_oi_ratio,
            }
        )

    return {"records": out, "missing_fields": missing}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbol", help="Single ticker (multi-symbol intentionally unsupported)")
    parser.add_argument("--type", default=DEFAULT_TYPE, choices=TYPE_CHOICES)
    parser.add_argument(
        "--expiration",
        default=None,
        help="YYYY-MM-DD post-filter applied to chain rows",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=PROVIDER_CHOICES,
    )
    args = parser.parse_args()

    expiration_filter: date | None = None
    if args.expiration:
        try:
            expiration_filter = _parse_iso_date(args.expiration)
        except ValueError:
            parser.error(f"--expiration must be YYYY-MM-DD (got {args.expiration!r})")

    call_result = safe_call(
        obb.derivatives.options.chains,
        symbol=args.symbol,
        provider=args.provider,
    )

    query_meta: dict[str, Any] = {
        "type": args.type,
        "provider": args.provider,
        "symbol": args.symbol,
    }
    if expiration_filter is not None:
        query_meta["expiration_filter"] = expiration_filter.isoformat()

    if call_result.get("ok"):
        records = call_result["records"]
        if expiration_filter is not None:
            records = _filter_by_expiration(records, expiration_filter)
        if args.type == "iv":
            view = derive_iv_view(records)
            records = view["records"]
            query_meta["missing_fields"] = view["missing_fields"]
        call_result = {"ok": True, "records": records}

    return single_emit(call_result, tool="options", query_meta=query_meta)


if __name__ == "__main__":
    raise SystemExit(main())
