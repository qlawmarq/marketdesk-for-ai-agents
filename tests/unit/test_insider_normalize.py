"""Unit tests for `scripts/insider.py` pure helpers.

Covers Task 1 (argparse validators) from
`docs/tasks/todo/insider-trading-skill/tasks.md`. The pre-collection guard
in `tests/unit/conftest.py` strips `_CREDENTIAL_MAP` env vars and installs
a fake `openbb` module, so the wrapper's top-level `apply_to_openbb()`
call is a no-op and the module is safe to import offline.
"""

from __future__ import annotations

import argparse

import pytest

from insider import (  # type: ignore[import-not-found]
    _CANONICAL_KEYS,
    _CODE_TO_LABEL,
    _MD_COLUMNS,
    _SEC_TYPE_TO_CODE,
    _apply_code_filter,
    _compute_total_value,
    _escape_md_cell,
    _extract_fmp_code,
    _lookup_sec_code,
    _normalize_fmp_record,
    _normalize_other_record,
    _normalize_sec_record,
    _parse_codes_csv,
    _positive_int,
    _render_markdown,
    _strip_role_prefix,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Task 1.1 — _positive_int (Req 1.4)
# ---------------------------------------------------------------------------


def test_positive_int_accepts_typical_window() -> None:
    assert _positive_int("90") == 90


def test_positive_int_accepts_minimum_one() -> None:
    assert _positive_int("1") == 1


def test_positive_int_rejects_zero() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("0")


def test_positive_int_rejects_negative() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("-1")


def test_positive_int_rejects_non_integer() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("abc")


# ---------------------------------------------------------------------------
# Task 1.2 — _parse_codes_csv (Req 4.5)
# ---------------------------------------------------------------------------


def test_parse_codes_csv_single_letter_returns_singleton() -> None:
    assert _parse_codes_csv("P") == ["P"]


def test_parse_codes_csv_uppercases_each_element() -> None:
    assert _parse_codes_csv("p,s") == ["P", "S"]


def test_parse_codes_csv_strips_per_element_whitespace() -> None:
    assert _parse_codes_csv(" p , s ") == ["P", "S"]


def test_parse_codes_csv_rejects_multi_character_token() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_codes_csv("PP")


def test_parse_codes_csv_rejects_empty_string() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_codes_csv("")


def test_parse_codes_csv_rejects_lone_comma() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_codes_csv(",")


def test_parse_codes_csv_rejects_non_alphabetic_element() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_codes_csv("P,1")


# ---------------------------------------------------------------------------
# Task 2.1 — _SEC_TYPE_TO_CODE / _lookup_sec_code (Req 6.3)
# ---------------------------------------------------------------------------


_SEC_DOCUMENTED_PAIRS = [
    (
        "Open market or private purchase of non-derivative or derivative security",
        "P",
    ),
    (
        "Open market or private sale of non-derivative or derivative security",
        "S",
    ),
    ("Grant, award or other acquisition pursuant to Rule 16b-3(d)", "A"),
    (
        "Payment of exercise price or tax liability by delivering or withholding "
        "securities incident to the receipt, exercise or vesting of a security "
        "issued in accordance with Rule 16b-3",
        "F",
    ),
    ("Exercise or conversion of derivative security exempted pursuant to Rule 16b-3", "M"),
    ("Conversion of derivative security", "C"),
    ("Bona fide gift", "G"),
    (
        "Disposition to the issuer of issuer equity securities pursuant to "
        "Rule 16b-3(e)",
        "D",
    ),
    ("Other acquisition or disposition (describe transaction)", "J"),
    ("Acquisition or disposition by will or the laws of descent and distribution", "W"),
]


@pytest.mark.parametrize("long_english,code", _SEC_DOCUMENTED_PAIRS)
def test_lookup_sec_code_returns_documented_letter(long_english: str, code: str) -> None:
    assert _lookup_sec_code(long_english) == code


def test_sec_type_to_code_has_ten_documented_entries() -> None:
    assert len(_SEC_TYPE_TO_CODE) == 10


def test_lookup_sec_code_returns_null_for_unmapped_string(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _lookup_sec_code("Made-up unmapped transaction-type string") is None


def test_lookup_sec_code_returns_null_for_empty_string() -> None:
    assert _lookup_sec_code("") is None


def test_lookup_sec_code_returns_null_for_none() -> None:
    assert _lookup_sec_code(None) is None


def test_lookup_sec_code_logs_once_per_unique_miss(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import insider as _insider  # type: ignore[import-not-found]

    _insider._SEEN_UNMAPPED_SEC_STRINGS.clear()
    capsys.readouterr()  # drain any leftover stderr
    _lookup_sec_code("Some unique unmapped string A")
    _lookup_sec_code("Some unique unmapped string A")
    captured = capsys.readouterr()
    assert captured.err.count("Some unique unmapped string A") == 1


# ---------------------------------------------------------------------------
# Task 2.2 — _extract_fmp_code / _CODE_TO_LABEL (Req 6.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("S-Sale", "S"),
        ("M-Exempt", "M"),
        ("A-Award", "A"),
        ("F-InKind", "F"),
        ("G-Gift", "G"),
    ],
)
def test_extract_fmp_code_returns_leading_letter(raw: str, expected: str) -> None:
    assert _extract_fmp_code(raw) == expected


def test_extract_fmp_code_returns_null_for_lowercase_word() -> None:
    assert _extract_fmp_code("sale") is None


def test_extract_fmp_code_returns_null_for_empty_string() -> None:
    assert _extract_fmp_code("") is None


def test_extract_fmp_code_returns_null_for_none() -> None:
    assert _extract_fmp_code(None) is None


def test_code_to_label_covers_every_sec_dispatched_code() -> None:
    sec_codes = set(_SEC_TYPE_TO_CODE.values())
    label_codes = set(_CODE_TO_LABEL.keys())
    assert sec_codes <= label_codes


# ---------------------------------------------------------------------------
# Task 2.3 — _strip_role_prefix / _compute_total_value (Req 6.2)
# ---------------------------------------------------------------------------


def test_strip_role_prefix_removes_officer_prefix() -> None:
    assert (
        _strip_role_prefix("officer: Chief Executive Officer")
        == "Chief Executive Officer"
    )


def test_strip_role_prefix_removes_director_prefix() -> None:
    assert _strip_role_prefix("director: Lead Director") == "Lead Director"


def test_strip_role_prefix_removes_ten_percent_owner_prefix() -> None:
    assert (
        _strip_role_prefix("ten_percent_owner: Major Holder LLC")
        == "Major Holder LLC"
    )


def test_strip_role_prefix_expands_bare_director() -> None:
    assert _strip_role_prefix("director") == "Director"


def test_strip_role_prefix_expands_bare_officer() -> None:
    assert _strip_role_prefix("officer") == "Officer"


def test_strip_role_prefix_expands_bare_ten_percent_owner() -> None:
    assert _strip_role_prefix("ten_percent_owner") == "Ten Percent Owner"


def test_strip_role_prefix_handles_compound_director_officer() -> None:
    assert (
        _strip_role_prefix("director, officer: Chief Executive Officer")
        == "Chief Executive Officer"
    )


def test_strip_role_prefix_returns_none_for_none() -> None:
    assert _strip_role_prefix(None) is None


def test_strip_role_prefix_returns_empty_for_empty() -> None:
    assert _strip_role_prefix("") == ""


def test_strip_role_prefix_passes_through_unmarked_text() -> None:
    assert _strip_role_prefix("Chairman of the Board") == "Chairman of the Board"


def test_compute_total_value_multiplies_when_both_positive() -> None:
    assert _compute_total_value(100, 150.5) == 15050.0


def test_compute_total_value_zero_shares_yields_zero() -> None:
    assert _compute_total_value(0, 150.5) == 0.0


def test_compute_total_value_returns_null_when_price_is_zero() -> None:
    assert _compute_total_value(100, 0) is None


def test_compute_total_value_returns_null_when_price_is_negative() -> None:
    assert _compute_total_value(100, -1.0) is None


def test_compute_total_value_returns_null_when_price_is_none() -> None:
    assert _compute_total_value(100, None) is None


def test_compute_total_value_returns_null_when_shares_is_none() -> None:
    assert _compute_total_value(None, 150.5) is None


# ---------------------------------------------------------------------------
# Task 3.1 — _normalize_sec_record (Req 6.2, 6.3)
# ---------------------------------------------------------------------------


_CANONICAL_KEY_SET = {
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
}


def test_canonical_keys_constant_is_the_documented_19_field_set() -> None:
    assert set(_CANONICAL_KEYS) == _CANONICAL_KEY_SET
    assert len(_CANONICAL_KEYS) == 19


def _sec_sample_record() -> dict:
    return {
        "filing_date": "2026-04-15",
        "transaction_date": "2026-04-12",
        "owner_name": "Tim D Cook",
        "owner_title": "Chief Executive Officer",
        "transaction_type": (
            "Open market or private sale of non-derivative or derivative security"
        ),
        "acquisition_or_disposition": "Disposition",
        "securities_transacted": 100,
        "transaction_price": 150.5,
        "securities_owned": 5000,
        "form": "4",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/0000320193/x.html",
        "ownership_type": "Direct",
        "security_type": "Common Stock",
        "company_cik": "0000320193",
        "owner_cik": "0001214156",
        "footnote": "Sale pursuant to Rule 10b5-1 plan.",
    }


def test_normalize_sec_record_returns_canonical_19_key_set() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert set(out.keys()) == _CANONICAL_KEY_SET


def test_normalize_sec_record_renames_securities_transacted_to_shares() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["shares"] == 100


def test_normalize_sec_record_renames_transaction_price_to_price() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["price"] == 150.5


def test_normalize_sec_record_renames_securities_owned_to_shares_after() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["shares_after"] == 5000


def test_normalize_sec_record_renames_form_to_form_type() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["form_type"] == "4"


def test_normalize_sec_record_renames_filing_url_to_url() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["url"].startswith("https://www.sec.gov/")


def test_normalize_sec_record_renames_owner_name_to_reporter_name() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["reporter_name"] == "Tim D Cook"


def test_normalize_sec_record_passes_owner_title_through_as_reporter_title() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["reporter_title"] == "Chief Executive Officer"


def test_normalize_sec_record_preserves_transaction_type_raw_verbatim() -> None:
    rec = _sec_sample_record()
    out = _normalize_sec_record(rec)
    assert out["transaction_type_raw"] == rec["transaction_type"]


def test_normalize_sec_record_collapses_acquisition_to_letter() -> None:
    rec = _sec_sample_record()
    rec["acquisition_or_disposition"] = "Acquisition"
    out = _normalize_sec_record(rec)
    assert out["acquisition_or_disposition"] == "A"


def test_normalize_sec_record_collapses_disposition_to_letter() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["acquisition_or_disposition"] == "D"


def test_normalize_sec_record_passes_ownership_type_verbatim() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["ownership_type"] == "Direct"


def test_normalize_sec_record_populates_transaction_code_from_lookup() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["transaction_code"] == "S"


def test_normalize_sec_record_populates_transaction_code_label() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["transaction_code_label"] == "Open Market Sale"


def test_normalize_sec_record_computes_total_value() -> None:
    out = _normalize_sec_record(_sec_sample_record())
    assert out["total_value"] == 15050.0


def test_normalize_sec_record_unmapped_transaction_type_yields_null_code() -> None:
    rec = _sec_sample_record()
    rec["transaction_type"] = "Brand-new SEC string the corpus has not seen"
    out = _normalize_sec_record(rec)
    assert out["transaction_code"] is None
    assert out["transaction_code_label"] is None
    assert out["transaction_type_raw"] == rec["transaction_type"]


def test_normalize_sec_record_missing_keys_emit_null_not_absent() -> None:
    out = _normalize_sec_record({})
    assert set(out.keys()) == _CANONICAL_KEY_SET
    assert out["filing_date"] is None
    assert out["transaction_date"] is None
    assert out["reporter_name"] is None
    assert out["reporter_title"] is None
    assert out["transaction_code"] is None
    assert out["transaction_code_label"] is None
    assert out["transaction_type_raw"] is None
    assert out["acquisition_or_disposition"] is None
    assert out["shares"] is None
    assert out["price"] is None
    assert out["total_value"] is None
    assert out["shares_after"] is None
    assert out["form_type"] is None
    assert out["url"] is None
    assert out["ownership_type"] is None
    assert out["security_type"] is None
    assert out["company_cik"] is None
    assert out["owner_cik"] is None
    assert out["footnote"] is None


def test_normalize_sec_record_unknown_acquisition_disposition_value_is_null() -> None:
    rec = _sec_sample_record()
    rec["acquisition_or_disposition"] = "Other"
    out = _normalize_sec_record(rec)
    assert out["acquisition_or_disposition"] is None


# ---------------------------------------------------------------------------
# Task 3.2 — _normalize_fmp_record (Req 2.6, 6.2, 6.3)
# ---------------------------------------------------------------------------


def _fmp_sample_record() -> dict:
    return {
        "filing_date": "2026-04-20",
        "transaction_date": "2026-04-18",
        "owner_name": "COOK TIMOTHY D",
        "owner_title": "officer: Chief Executive Officer",
        "transaction_type": "S-Sale",
        "acquisition_or_disposition": "D",
        "securities_transacted": 50,
        "transaction_price": 200.0,
        "securities_owned": 1000,
        "form_type": "4",
        "url": "https://www.sec.gov/Archives/edgar/data/0000320193/y.html",
        "ownership_type": "D",
        "security_type": "Common Stock",
        "company_cik": "0000320193",
        "owner_cik": "0001214156",
    }


def test_normalize_fmp_record_returns_canonical_19_key_set() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert set(out.keys()) == _CANONICAL_KEY_SET


def test_normalize_fmp_record_key_set_matches_sec_normalizer_key_set() -> None:
    sec_out = _normalize_sec_record(_sec_sample_record())
    fmp_out = _normalize_fmp_record(_fmp_sample_record())
    assert set(sec_out.keys()) == set(fmp_out.keys())


def test_normalize_fmp_record_strips_officer_prefix_from_reporter_title() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["reporter_title"] == "Chief Executive Officer"


def test_normalize_fmp_record_strips_compound_director_officer_title() -> None:
    rec = _fmp_sample_record()
    rec["owner_title"] = "director, officer: Chief Financial Officer"
    out = _normalize_fmp_record(rec)
    assert out["reporter_title"] == "Chief Financial Officer"


def test_normalize_fmp_record_expands_ownership_type_d_to_direct() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["ownership_type"] == "Direct"


def test_normalize_fmp_record_expands_ownership_type_i_to_indirect() -> None:
    rec = _fmp_sample_record()
    rec["ownership_type"] = "I"
    out = _normalize_fmp_record(rec)
    assert out["ownership_type"] == "Indirect"


def test_normalize_fmp_record_unknown_ownership_type_letter_is_null() -> None:
    rec = _fmp_sample_record()
    rec["ownership_type"] = "X"
    out = _normalize_fmp_record(rec)
    assert out["ownership_type"] is None


def test_normalize_fmp_record_extracts_transaction_code_via_regex() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["transaction_code"] == "S"


def test_normalize_fmp_record_populates_transaction_code_label() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["transaction_code_label"] == "Open Market Sale"


def test_normalize_fmp_record_unparseable_transaction_type_yields_null_code() -> None:
    rec = _fmp_sample_record()
    rec["transaction_type"] = ""
    out = _normalize_fmp_record(rec)
    assert out["transaction_code"] is None
    assert out["transaction_code_label"] is None


def test_normalize_fmp_record_preserves_transaction_type_raw_verbatim() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["transaction_type_raw"] == "S-Sale"


def test_normalize_fmp_record_passes_acquisition_or_disposition_verbatim() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["acquisition_or_disposition"] == "D"


def test_normalize_fmp_record_renames_securities_transacted_to_shares() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["shares"] == 50


def test_normalize_fmp_record_uses_form_type_identity() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["form_type"] == "4"


def test_normalize_fmp_record_uses_url_identity() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["url"].startswith("https://www.sec.gov/")


def test_normalize_fmp_record_computes_total_value() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["total_value"] == 10000.0


def test_normalize_fmp_record_footnote_is_null_when_missing() -> None:
    out = _normalize_fmp_record(_fmp_sample_record())
    assert out["footnote"] is None


def test_normalize_fmp_record_missing_keys_emit_null_not_absent() -> None:
    out = _normalize_fmp_record({})
    assert set(out.keys()) == _CANONICAL_KEY_SET
    assert out["shares"] is None
    assert out["price"] is None
    assert out["transaction_code"] is None


# ---------------------------------------------------------------------------
# Task 3.3 — _normalize_other_record (Req 2.6, 6.2, 6.3)
# ---------------------------------------------------------------------------


def test_normalize_other_record_returns_canonical_19_key_set() -> None:
    out = _normalize_other_record({"any": "value"})
    assert set(out.keys()) == _CANONICAL_KEY_SET


def test_normalize_other_record_transaction_code_is_always_null() -> None:
    out = _normalize_other_record({"transaction_type": "P-Purchase"})
    assert out["transaction_code"] is None
    assert out["transaction_code_label"] is None


def test_normalize_other_record_does_not_rename_fmp_specific_fields() -> None:
    out = _normalize_other_record(
        {
            "securities_transacted": 999,
            "transaction_price": 1.0,
            "owner_name": "Should Not Map",
        }
    )
    assert out["shares"] is None
    assert out["price"] is None
    assert out["reporter_name"] is None


def test_normalize_other_record_passes_canonical_named_keys_through() -> None:
    out = _normalize_other_record(
        {"filing_date": "2026-04-30", "form_type": "4", "url": "https://x"}
    )
    assert out["filing_date"] == "2026-04-30"
    assert out["form_type"] == "4"
    assert out["url"] == "https://x"


def test_normalize_other_record_does_not_strip_role_prefix() -> None:
    out = _normalize_other_record({"reporter_title": "officer: Director"})
    assert out["reporter_title"] == "officer: Director"


def test_normalize_other_record_preserves_transaction_type_raw() -> None:
    out = _normalize_other_record({"transaction_type": "anything"})
    assert out["transaction_type_raw"] == "anything"


# ---------------------------------------------------------------------------
# Task 4.1 — _apply_code_filter (Req 4.2, 4.3, 4.4)
# ---------------------------------------------------------------------------


def _coded_record(code: str | None) -> dict:
    return {"transaction_code": code}


def test_apply_code_filter_none_passes_through_with_zero_count() -> None:
    records = [_coded_record("P"), _coded_record(None), _coded_record("S")]
    kept, dropped = _apply_code_filter(records, None)
    assert kept is records
    assert dropped == 0


def test_apply_code_filter_none_with_empty_records_returns_empty_zero() -> None:
    kept, dropped = _apply_code_filter([], None)
    assert kept == []
    assert dropped == 0


def test_apply_code_filter_all_kept_returns_every_record_with_zero_count() -> None:
    records = [_coded_record("P"), _coded_record("S"), _coded_record("P")]
    kept, dropped = _apply_code_filter(records, ["P", "S"])
    assert kept == records
    assert dropped == 0


def test_apply_code_filter_keeps_only_matching_codes() -> None:
    records = [_coded_record("P"), _coded_record("S"), _coded_record("A")]
    kept, dropped = _apply_code_filter(records, ["P"])
    assert kept == [_coded_record("P")]
    assert dropped == 0


def test_apply_code_filter_drops_null_code_rows_and_counts_them() -> None:
    records = [
        _coded_record("P"),
        _coded_record(None),
        _coded_record("S"),
        _coded_record(None),
    ]
    kept, dropped = _apply_code_filter(records, ["P"])
    assert kept == [_coded_record("P")]
    assert dropped == 2


def test_apply_code_filter_all_dropped_with_mixed_mismatch_and_null() -> None:
    records = [
        _coded_record("S"),
        _coded_record(None),
        _coded_record("A"),
        _coded_record(None),
        _coded_record(None),
    ]
    kept, dropped = _apply_code_filter(records, ["P"])
    assert kept == []
    assert dropped == 3


def test_apply_code_filter_mismatched_non_null_codes_do_not_increment_count() -> None:
    records = [_coded_record("S"), _coded_record("A"), _coded_record("M")]
    kept, dropped = _apply_code_filter(records, ["P"])
    assert kept == []
    assert dropped == 0


def test_apply_code_filter_empty_records_with_filter_returns_empty_zero() -> None:
    kept, dropped = _apply_code_filter([], ["P"])
    assert kept == []
    assert dropped == 0


# ---------------------------------------------------------------------------
# Task 6.1 — _escape_md_cell (Req 5.7)
# ---------------------------------------------------------------------------


def test_escape_md_cell_escapes_pipe_character() -> None:
    assert _escape_md_cell("a|b") == "a\\|b"


def test_escape_md_cell_collapses_newline_to_single_space() -> None:
    assert _escape_md_cell("line1\nline2") == "line1 line2"


def test_escape_md_cell_handles_carriage_return() -> None:
    assert _escape_md_cell("line1\r\nline2") == "line1 line2"


def test_escape_md_cell_renders_none_as_empty_string() -> None:
    assert _escape_md_cell(None) == ""


def test_escape_md_cell_renders_int_via_str() -> None:
    assert _escape_md_cell(100) == "100"


def test_escape_md_cell_renders_float_via_str() -> None:
    assert _escape_md_cell(150.5) == "150.5"


def test_escape_md_cell_passes_plain_string_through() -> None:
    assert _escape_md_cell("hello") == "hello"


def test_escape_md_cell_handles_pipe_and_newline_together() -> None:
    assert _escape_md_cell("a|b\nc") == "a\\|b c"


# ---------------------------------------------------------------------------
# Task 6.2 — _render_markdown (Req 5.3, 5.4, 5.5, 5.6, 5.9)
# ---------------------------------------------------------------------------


def _ok_row(symbol: str, records: list[dict], dropped: int = 0) -> dict:
    return {
        "symbol": symbol,
        "provider": "sec",
        "ok": True,
        "records": records,
        "dropped_unparseable_codes": dropped,
    }


def _failure_row(symbol: str, category: str, error: str) -> dict:
    return {
        "symbol": symbol,
        "provider": "sec",
        "ok": False,
        "error": error,
        "error_type": "RuntimeError",
        "error_category": category,
    }


def _md_record(**overrides) -> dict:
    base = {key: None for key in _CANONICAL_KEYS}
    base.update(overrides)
    return base


def test_md_columns_pins_eleven_column_reading_order() -> None:
    assert list(_MD_COLUMNS) == [
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
    ]


def test_render_markdown_emits_one_heading_per_ticker() -> None:
    rows = [_ok_row("AAPL", []), _ok_row("MSFT", [])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert out.count("## AAPL") == 1
    assert out.count("## MSFT") == 1


def test_render_markdown_empty_section_emits_no_records_line() -> None:
    rows = [_ok_row("AAPL", [])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "_no records in window_" in out


def test_render_markdown_empty_section_no_dropped_suffix_when_filter_inactive() -> None:
    rows = [_ok_row("AAPL", [], dropped=0)]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "dropped" not in out


def test_render_markdown_empty_section_appends_dropped_suffix_when_filter_active() -> None:
    rows = [_ok_row("AAPL", [], dropped=3)]
    out = _render_markdown(rows, {"transaction_codes": ["P"]})
    assert "_no records in window_ (dropped 3 unparseable codes)" in out


def test_render_markdown_empty_section_no_suffix_when_dropped_zero_with_filter() -> None:
    rows = [_ok_row("AAPL", [], dropped=0)]
    out = _render_markdown(rows, {"transaction_codes": ["P"]})
    assert "dropped" not in out


def test_render_markdown_failure_row_emits_error_category_line() -> None:
    rows = [_failure_row("AAPL", "credential", "missing key")]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "_error_category_: credential" in out
    assert "missing key" in out


def test_render_markdown_failure_row_does_not_render_table() -> None:
    rows = [_failure_row("AAPL", "credential", "missing key")]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "filing_date | transaction_date" not in out


def test_render_markdown_non_empty_section_renders_canonical_header_verbatim() -> None:
    rows = [_ok_row("AAPL", [_md_record(filing_date="2026-04-15")])]
    out = _render_markdown(rows, {"transaction_codes": None})
    expected_header = (
        "filing_date | transaction_date | reporter_name | reporter_title | "
        "transaction_code | transaction_code_label | shares | price | "
        "total_value | shares_after | url"
    )
    assert expected_header in out


def test_render_markdown_table_includes_separator_row() -> None:
    rows = [_ok_row("AAPL", [_md_record(filing_date="2026-04-15")])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "---" in out


def test_render_markdown_renders_record_values_in_pinned_order() -> None:
    rec = _md_record(
        filing_date="2026-04-15",
        transaction_date="2026-04-12",
        reporter_name="Tim Cook",
        reporter_title="CEO",
        transaction_code="S",
        transaction_code_label="Open Market Sale",
        shares=100,
        price=150.5,
        total_value=15050.0,
        shares_after=5000,
        url="https://x",
    )
    rows = [_ok_row("AAPL", [rec])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert (
        "2026-04-15 | 2026-04-12 | Tim Cook | CEO | S | Open Market Sale | "
        "100 | 150.5 | 15050.0 | 5000 | https://x"
    ) in out


def test_render_markdown_null_record_value_renders_as_empty_cell() -> None:
    rec = _md_record(filing_date="2026-04-15", reporter_name=None)
    rows = [_ok_row("AAPL", [rec])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "None" not in out


def test_render_markdown_pipe_in_cell_does_not_break_table() -> None:
    rec = _md_record(reporter_title="Director|Officer")
    rows = [_ok_row("AAPL", [rec])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "Director\\|Officer" in out


def test_render_markdown_newline_in_cell_does_not_break_table() -> None:
    rec = _md_record(reporter_title="Line1\nLine2")
    rows = [_ok_row("AAPL", [rec])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert "Line1 Line2" in out
    table_section = out.split("## AAPL", 1)[1]
    table_lines = [line for line in table_section.splitlines() if line.strip()]
    # header + separator + 1 data row = 3 non-blank lines in the table
    assert len(table_lines) == 3


def test_render_markdown_does_not_end_with_trailing_newline() -> None:
    rows = [_ok_row("AAPL", [])]
    out = _render_markdown(rows, {"transaction_codes": None})
    assert not out.endswith("\n")
