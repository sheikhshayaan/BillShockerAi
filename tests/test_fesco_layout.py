"""Aggregated-charges layout: net + one taxes line, FPA added after the current bill, two surcharge tiers."""
import copy

from services.factors import evaluate
from services.schema import build_tool_schema
from services.validator import validate_bill


def codes(r, sev=None):
    return {f["code"] for f in r["findings"] if sev in (None, f["severity"])}


def setv(p, k, v):
    p["fields"][k]["value"], p["fields"][k]["confirmed"] = v, True


def test_aggregate_layout_is_consistent(aggregate_payload):
    r = validate_bill(aggregate_payload)
    assert r["status"] == "consistent", [f["message"] for f in r["findings"] if f["severity"] != "ok"]
    assert {"net_matches", "total_matches", "within_matches", "lps_matches", "readings_match_units"} <= codes(r, "ok")
    msgs = " ".join(f["message"] for f in r["findings"])
    assert "adds FPA after the current bill" in msgs and "second-tier" in msgs and "first-tier" in msgs
    assert r["all_confirmed"]


def test_all_in_cost_includes_fpa_when_added_after_current_bill(aggregate_payload):
    r = validate_bill(aggregate_payload)
    assert r["derived"]["all_in_cost_per_unit"]["value"] == "48.37"  # (14300 + 210.50) / 300
    assert r["derived"]["non_energy"] == {"amount": "2510.50", "share_pct": "17.3", "note": r["derived"]["non_energy"]["note"]}
    assert "arrears_share" not in r["derived"]  # arrears are 0


def test_comparisons_use_last_month_last_year_and_median(aggregate_payload):
    c = validate_bill(aggregate_payload)["comparisons"]
    assert c["vs_previous_month"] == {"month": "Jul 26", "units": "360", "change_pct": "-16.7"}
    assert c["vs_last_year"] == {"month": "Aug 25", "units": "280", "change_pct": "7.1"}
    assert c["typical_month"] == {"median_units": "165.0", "months": 12, "times_median": "1.82"}


def test_taxes_mismatch_is_flagged(aggregate_payload):
    setv(aggregate_payload, "taxes_total", "2000")
    r = validate_bill(aggregate_payload)
    f = next(f for f in r["findings"] if f["code"] == "total_mismatch")
    assert "net charges + taxes" in f["message"] and f["details"]["gap"] == "300.00"


def test_net_vs_subsidy_mismatch(aggregate_payload):
    setv(aggregate_payload, "subsidies", "1000")
    assert "net_mismatch" in codes(validate_bill(aggregate_payload), "error")


def test_payable_mismatch_even_with_fpa(aggregate_payload):
    setv(aggregate_payload, "payable_within_due_date", "15000")
    assert "within_mismatch" in codes(validate_bill(aggregate_payload), "error")


def test_second_tier_checked_separately(aggregate_payload):
    setv(aggregate_payload, "payable_after_due_date_2", "16000")
    r = validate_bill(aggregate_payload)
    assert any(f["code"] == "lps_mismatch" and "second-tier" in f["message"] for f in r["findings"])
    assert any(f["code"] == "lps_matches" and "first-tier" in f["message"] for f in r["findings"])


def test_installment_and_adjustments_flow_into_payable(aggregate_payload):
    setv(aggregate_payload, "installment", "500")
    setv(aggregate_payload, "payable_within_due_date", "15011")  # 14300 + 500 + 210.50
    assert "within_matches" in codes(validate_bill(aggregate_payload), "ok")


def test_unconfirmed_optional_lines_only_block_confirmation_not_checks(aggregate_payload):
    aggregate_payload["fields"]["gst"]["confirmed"] = False
    r = validate_bill(aggregate_payload)
    assert r["status"] == "consistent" and r["unconfirmed_fields"] == ["gst"]


def test_protected_wording_recorded_but_not_judged(aggregate_payload):
    r = validate_bill(aggregate_payload)
    assert aggregate_payload["fields"]["protected_indicator_text"]["value"] == "UNPROTECTED"
    assert "protected" not in " ".join(f["message"].lower() for f in r["findings"])


# ---- factors on this layout
def run(p, a):
    return {f["id"]: f for f in evaluate(p, validate_bill(p), a)["factors"]}


def test_high_level_and_year_on_year_supported_by_bill(aggregate_payload):
    f = run(aggregate_payload, {})
    assert f["high_level"]["level"] == "supported" and "1.82 times" in f["high_level"]["text"]
    assert f["year_on_year"]["level"] == "supported" and "7.1%" in f["year_on_year"]["text"]
    assert f["units_not_up"]["level"] == "supported" and "last month" in f["units_not_up"]["text"]


def test_cooling_possible_for_high_level_never_supported(aggregate_payload):
    f = run(aggregate_payload, {"ac_count": "one", "ac_hours": "gt8"})
    assert f["cooling"]["level"] == "possible" and "cannot confirm" in f["cooling"]["text"]


def test_cooling_unlikely_when_level_is_not_high(aggregate_payload):
    p = copy.deepcopy(aggregate_payload)
    for h in p["history"]:
        h["units"] = "290"
    f = run(p, {"ac_count": "one", "ac_hours": "gt8"})
    assert "high_level" not in f and f["cooling"]["level"] == "unlikely"


def test_tool_schema_describes_aggregate_fields():
    props = build_tool_schema()["input_schema"]["properties"]["fields"]["properties"]
    assert "combined taxes" in props["taxes_total"]["description"]
    assert "Grand total" in props["payable_within_due_date"]["description"]
    assert {"subsidies", "net_electric_charges", "installment", "adjustments", "late_payment_surcharge_2"} <= set(props)


def test_review_page_marks_optional_sections(make_app):
    html = make_app(None).test_client().get("/review").get_data(as_text=True)
    assert '"optional": true' in html and '"optional": false' in html


# ---- chart-ready data
from datetime import date


def test_series_includes_current_month_and_cost_per_unit(aggregate_payload):
    r = validate_bill(aggregate_payload, today=date(2026, 8, 25))
    s = r["series"]
    assert len(s) == 13 and s[-1]["is_current"] and s[-1]["month"] == "AUG 26" and not s[-1]["from_history"]
    assert [x["ym"] for x in s] == sorted(x["ym"] for x in s)
    assert s[-1]["units"] == "300" and s[-1]["bill"] == "14510.50" and s[-1]["rs_per_unit"] == "48.4"
    aug25 = next(x for x in s if x["month"] == "Aug 25")
    assert aug25["rs_per_unit"] == "32.1"  # 9000 / 280


def test_series_uses_history_row_for_current_month_when_present(review_payload):
    s = validate_bill(review_payload)["series"]
    assert sum(x["is_current"] for x in s) == 1 and next(x for x in s if x["is_current"])["from_history"]


def test_due_countdown_and_payment_ladder(aggregate_payload):
    r = validate_bill(aggregate_payload, today=date(2026, 8, 30))
    assert r["due"] == {"date": "03 SEP 26", "days_left": 4}
    assert validate_bill(aggregate_payload, today=date(2026, 9, 5))["due"]["days_left"] == -2
    assert [(x["amount"], x["extra"]) for x in r["ladder"]] == [("14511.00", None), ("15111.00", "600.00"), ("15711.00", "1200.00")]
    assert r["ladder"][1]["date"] == "10-SEP-26"


def test_no_due_date_no_countdown(aggregate_payload):
    setv(aggregate_payload, "due_date", None)
    assert validate_bill(aggregate_payload)["due"] is None


def test_composition_of_amount_payable(aggregate_payload):
    c = validate_bill(aggregate_payload)["composition"]
    assert [(x["label"], x["amount"], x["share_pct"]) for x in c] == [
        ("Net electric charges", "12000.00", "82.7"), ("Taxes", "2300.00", "15.9"), ("Fuel price adjustment (FPA)", "210.50", "1.5")]


def test_composition_includes_arrears_and_skips_zero_lines(aggregate_payload):
    setv(aggregate_payload, "arrears", "1000")
    setv(aggregate_payload, "payable_within_due_date", "15511")  # 14300 + 1000 + 210.50
    c = validate_bill(aggregate_payload)["composition"]
    assert [x["label"] for x in c] == ["Net electric charges", "Taxes", "Fuel price adjustment (FPA)", "Arrears"]
    assert c[-1]["share_pct"] == "6.4"  # 1000 / 15510.5 = 6.447%


def test_composition_itemised_layout_does_not_double_count_fpa(review_payload):
    labels = [x["label"] for x in validate_bill(review_payload)["composition"]]
    assert sum("Fuel price" in l for l in labels) == 1 and "Arrears" in labels
