import copy

import pytest

from services.factors import QUESTIONS, clean_answers, evaluate
from services.validator import validate_bill


def run(payload, answers):
    return evaluate(payload, validate_bill(payload), answers)


def by_id(res):
    return {f["id"]: f for f in res["factors"]}


def make_up(payload):  # current 250 vs earlier avg ~234 -> make earlier months low so units are clearly up
    p = copy.deepcopy(payload)
    for h, u in zip(p["history"][1:], ["150", "140", "130", "120", "110"]):
        h["units"] = u
    return p


def test_four_questions_only():
    assert len(QUESTIONS) == 4


def test_clean_answers_drops_unknowns():
    out = clean_answers({"ac_count": "none", "ac_hours": "gt8", "heavy": ["geyser", "bogus"], "x": 1, "changes": "str"})
    assert out == {"ac_count": "none", "heavy": ["geyser"]}
    assert clean_answers(None) == {}


def test_bill_supported_factors_present(review_payload):
    f = by_id(run(review_payload, {}))
    assert f["non_energy"]["level"] == "supported" and "21.7%" in f["non_energy"]["text"]  # (5108-4000)/5108
    assert f["arrears"]["level"] == "supported" and "19.0%" in f["arrears"]["text"]  # 1200/6308
    assert f["units_not_up"]["level"] == "supported"  # +6.8% -> not 'up'


def test_ac_possible_only_when_units_up_and_never_supported(review_payload):
    res = run(make_up(review_payload), {"ac_count": "one", "ac_hours": "gt8"})
    f = by_id(res)
    assert res["units_state"] == "up" and f["units_up"]["level"] == "supported"
    assert f["cooling"]["level"] == "possible"
    assert any(e["kind"] == "self_reported" for e in f["cooling"]["evidence"])
    assert all(x["level"] != "supported" for x in res["factors"] if x["id"] in ("cooling", "heavy", "household_change"))
    assert "cannot confirm" in f["cooling"]["text"]


def test_ac_unlikely_when_units_not_up(review_payload):
    f = by_id(run(review_payload, {"ac_count": "two_plus", "ac_hours": "gt8"}))
    assert f["cooling"]["level"] == "unlikely"


def test_no_history_never_claims_appliance_cause(review_payload):
    p = copy.deepcopy(review_payload)
    p["history"] = []
    res = run(p, {"ac_count": "one", "ac_hours": "gt8"})
    f = by_id(res)
    assert res["units_state"] == "unknown" and f["units_unknown"]["level"] == "info"
    assert f["cooling"]["level"] == "possible" and any(e["kind"] == "context" for e in f["cooling"]["evidence"])


def test_no_ac_no_cooling_factor(review_payload):
    assert "cooling" not in by_id(run(review_payload, {"ac_count": "none"}))
    assert "cooling" not in by_id(run(review_payload, {}))


def test_units_up_without_explanation_says_unknown(review_payload):
    f = by_id(run(make_up(review_payload), {"ac_count": "none"}))
    assert f["cause_unknown"]["level"] == "info"


def test_household_change_and_empty_home(review_payload):
    f = by_id(run(make_up(review_payload), {"changes": ["more_people", "less_home"]}))
    assert f["household_change"]["level"] == "possible" and f["empty_home"]["level"] == "possible"


def test_ordering_supported_first(review_payload):
    levels = [x["level"] for x in run(make_up(review_payload), {"ac_count": "one", "ac_hours": "gt8"})["factors"]]
    assert levels == sorted(levels, key=["supported", "possible", "unlikely", "info"].index)
