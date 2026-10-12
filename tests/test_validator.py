import copy
from decimal import Decimal

import pytest

from services.validator import BadNumber, parse_date, parse_month, parse_number, validate_bill


def codes(report, severity=None):
    return {f["code"] for f in report["findings"] if severity in (None, f["severity"])}


def set_value(payload, key, value, confirmed=True):
    payload["fields"][key]["value"] = value
    payload["fields"][key]["confirmed"] = confirmed


# ---- parsing
@pytest.mark.parametrize("raw,expected", [("1,234.50", "1234.50"), ("Rs. 5,108", "5108"), ("(120)", "-120"),
                                          ("250", "250"), ("5108/-", "5108"), ("-3", "-3"), (None, None)])
def test_parse_number(raw, expected):
    assert parse_number(raw) == (None if expected is None else Decimal(expected))


@pytest.mark.parametrize("raw", ["abc", "12..3", "1e5", "", "Rs"])
def test_parse_number_rejects_garbage(raw):
    with pytest.raises(BadNumber):
        parse_number(raw)


def test_parse_dates_and_months():
    assert str(parse_date("05 OCT 25")) == "2025-10-05"
    assert str(parse_date("15/10/2025")) == "2025-10-15"
    assert parse_month("SEP-25") == (2025, 9)
    assert parse_month("Sep 2025") == (2025, 9)
    assert parse_month("09/2025") == (2025, 9)
    with pytest.raises(BadNumber):
        parse_date("32 OCT 25")
    with pytest.raises(BadNumber):
        parse_month("hello")


# ---- whole-bill behaviour
def test_consistent_synthetic_bill(review_payload):
    r = validate_bill(review_payload)
    assert r["status"] == "consistent"
    assert {"total_matches", "readings_match_units", "within_matches", "lps_matches"} <= codes(r, "ok")
    assert not codes(r, "error")
    assert r["all_confirmed"] is True


def test_derived_values_computed_in_python(review_payload):
    r = validate_bill(review_payload)
    assert r["derived"]["all_in_cost_per_unit"]["value"] == "20.43"  # 5108 / 250
    shares = {b["label"]: b["share_pct"] for b in r["breakdown"]}
    assert shares["Total electricity charges"] == "78.3"  # 4000 / 5108
    assert r["trend"]["prior_months"] == 5
    assert r["trend"]["avg_prior_units"] == "234.0"
    assert r["trend"]["change_pct"] == "6.8"


def test_total_mismatch_is_flagged_with_amount(review_payload):
    set_value(review_payload, "gst", "923")  # +200
    r = validate_bill(review_payload)
    f = next(f for f in r["findings"] if f["code"] == "total_mismatch")
    assert f["severity"] == "error" and f["details"]["gap"] == "-200.00"
    assert "gst" in f["fields"] and "current_bill_total" in f["fields"]
    assert r["status"] == "discrepancies"


def test_rounding_within_tolerance_is_ok(review_payload):
    set_value(review_payload, "gst", "724")
    assert "total_matches" in codes(validate_bill(review_payload), "ok")


def test_mismatch_with_unconfirmed_unknown_line_is_warning_not_error(review_payload):
    set_value(review_payload, "tv_fee", None, confirmed=False)  # model couldn't read it
    r = validate_bill(review_payload)
    assert "total_mismatch_pending" in codes(r, "warn")
    assert "total_mismatch" not in codes(r)
    assert "tv_fee" in next(f for f in r["findings"] if f["code"] == "total_mismatch_pending")["fields"]
    assert "tv_fee" in r["unconfirmed_fields"]


def test_confirmed_unknown_line_is_not_invented_as_zero(review_payload):
    set_value(review_payload, "tv_fee", None, confirmed=True)  # user says not on bill
    r = validate_bill(review_payload)
    assert "total_mismatch" in codes(r, "error")  # 35 short -> real discrepancy, not hidden


def test_too_few_components_cannot_verify(review_payload):
    for k in ("energy_charge", "fuel_price_adjustment", "electricity_duty", "gst"):
        set_value(review_payload, k, None, confirmed=True)
    assert "total_unverifiable" in codes(validate_bill(review_payload), "info")


def test_unknown_total_does_not_crash_or_invent(review_payload):
    set_value(review_payload, "current_bill_total", None, confirmed=False)
    r = validate_bill(review_payload)
    assert "total_unverifiable" in codes(r)
    assert "all_in_cost_per_unit" not in r["derived"]


def test_readings_mismatch_with_mf(review_payload):
    set_value(review_payload, "multiplying_factor", "2")
    assert "readings_vs_units" in codes(validate_bill(review_payload), "error")


def test_readings_mismatch_without_mf_is_warning(review_payload):
    set_value(review_payload, "multiplying_factor", None)
    set_value(review_payload, "units_consumed", "300")
    assert "readings_vs_units" in codes(validate_bill(review_payload), "warn")


def test_mf_applied(review_payload):
    set_value(review_payload, "multiplying_factor", "2")
    set_value(review_payload, "units_consumed", "500")
    assert "readings_match_units" in codes(validate_bill(review_payload), "ok")


def test_reading_decreased(review_payload):
    set_value(review_payload, "present_reading", "9000")
    assert "reading_decreased" in codes(validate_bill(review_payload), "warn")


def test_payable_chain_mismatch(review_payload):
    set_value(review_payload, "payable_within_due_date", "7000")
    assert "within_mismatch" in codes(validate_bill(review_payload), "error")


def test_after_due_lower_than_within(review_payload):
    set_value(review_payload, "payable_after_due_date", "100")
    assert "after_less_than_within" in codes(validate_bill(review_payload), "error")


def test_due_before_issue(review_payload):
    set_value(review_payload, "due_date", "01 OCT 25")
    assert "due_before_issue" in codes(validate_bill(review_payload), "error")


def test_bad_number_reported_not_swallowed(review_payload):
    set_value(review_payload, "units_consumed", "two fifty")
    r = validate_bill(review_payload)
    assert "bad_format" in codes(r, "error")


def test_history_conflicts(review_payload):
    h = copy.deepcopy(review_payload)
    h["history"][0]["units"] = "999"
    assert "history_vs_current" in codes(validate_bill(h), "warn")
    h = copy.deepcopy(review_payload)
    h["history"].append({"month": "AUG-25", "units": "100", "bill_amount": None})
    assert "history_duplicate" in codes(validate_bill(h), "warn")


def test_no_trend_without_enough_history(review_payload):
    review_payload["history"] = review_payload["history"][:2]
    assert validate_bill(review_payload)["trend"] is None


def test_other_charges_are_included_in_sum(review_payload):
    set_value(review_payload, "gst", "673")  # 50 less ...
    review_payload["other_charges"] = [{"label": "Adjustment", "amount": "50", "confirmed": True, "extracted_value": "50", "source_text": None}]
    assert "total_matches" in codes(validate_bill(review_payload), "ok")


def test_negative_adjustment_allowed_in_sum(review_payload):
    set_value(review_payload, "gst", "773")
    review_payload["other_charges"] = [{"label": "Credit", "amount": "(50)", "confirmed": True, "extracted_value": None, "source_text": None}]
    assert "total_matches" in codes(validate_bill(review_payload), "ok")


def test_protected_status_never_decided(review_payload):
    set_value(review_payload, "units_consumed", "150")
    r = validate_bill(review_payload)
    text = " ".join(f["message"].lower() for f in r["findings"]) 
    assert "protected" not in text
    assert any("not determined" in n for n in r["notes"])


def test_field_status_tracks_user_correction(review_payload):
    set_value(review_payload, "units_consumed", "260")
    st = validate_bill(review_payload)["field_status"]
    assert st["units_consumed"] == "user_corrected"
    assert st["gst"] == "confirmed_extracted"
    assert st["tv_fee"] == "confirmed_extracted"
    assert st["fixed_charges"] == "confirmed_unknown"
