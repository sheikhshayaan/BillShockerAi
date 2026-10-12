import types

import pytest

from services.explainer import (NotReady, Explainer, build_facts, explain, numbers_in, template_explanation,
                                unsupported_numbers)
from services.validator import validate_bill


@pytest.fixture
def facts(review_payload):
    return build_facts(review_payload, validate_bill(review_payload))


class StubClient:
    def __init__(self, payload=None, exc=None):
        self.messages, self.payload, self.exc, self.calls = self, payload, exc, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", name="write_explanation", input=self.payload)])


def ai(payload=None, exc=None):
    c = StubClient(payload, exc)
    return Explainer("k", "m", client=c), c


GOOD = {"headline": "Your SEP-25 bill is Rs 5108 for 250 units", "summary": "Here is what the figures show.",
        "key_points": ["Energy cost is Rs 4000, which is 78.3% of the bill.", "Units are 6.8% above your earlier months."],
        "things_to_check": []}


def test_facts_need_confirmation(review_payload):
    review_payload["fields"]["gst"]["confirmed"] = False
    with pytest.raises(NotReady):
        build_facts(review_payload, validate_bill(review_payload))


def test_facts_contain_no_identifiers(facts):
    blob = str(facts).lower()
    for w in ("cnic", "address", "consumer", "meter_no", "name"):
        assert w not in blob.replace("charges", "").replace("name'", "")  # 'name' key of charge dicts allowed
    assert facts["units_consumed_kwh"] == "250" and facts["all_in_cost_per_unit_rs"] == "20.43"


def test_number_normalisation():
    assert numbers_in("Rs 5,108.00 and ۲۵۰ units, 78.30%") == {"5108", "250", "78.3"}


def test_good_ai_output_accepted(facts):
    ex, client = ai(GOOD)
    out = explain(facts, "en", ex)
    assert out["source"] == "ai" and out["explanation"]["headline"].startswith("Your SEP-25")
    assert "<facts>" in client.calls[0]["messages"][0]["content"]
    assert "Do not say why consumption changed" in client.calls[0]["system"]


def test_invented_number_is_rejected(facts):
    bad = dict(GOOD, key_points=["You could save Rs 1750 by using the AC less."])
    out = explain(facts, "en", ai(bad)[0])
    assert out["source"] == "template" and "not on your bill" in out["note"]
    assert unsupported_numbers({"x": "Rs 1750"}, facts) == ["1750"]


def test_urdu_digits_checked_too(facts):
    bad = dict(GOOD, key_points=["آپ کا بل ۹۹۹۹ روپے ہے"])
    assert explain(facts, "ur", ai(bad)[0])["source"] == "template"
    ok = dict(GOOD, headline="آپ کا بل Rs 5108 ہے", key_points=["یونٹس 250 ہیں"], summary="خلاصہ")
    out = explain(facts, "ur", ai(ok)[0])
    assert out["source"] == "ai" and out["lang"] == "ur"


def test_urdu_prompt_requested(facts):
    ex, c = ai(GOOD)
    explain(facts, "ur", ex)
    assert "Urdu script" in c.calls[0]["system"]


def test_api_failure_and_no_ai_fall_back(facts):
    out = explain(facts, "en", ai(exc=RuntimeError("down"))[0])
    assert out["source"] == "template" and out["explanation"]["key_points"]
    out = explain(facts, "ur", None)
    assert out["source"] == "template" and out["lang"] == "en" and "Urdu" in out["note"]


def test_empty_ai_answer_falls_back(facts):
    assert explain(facts, "en", ai({"headline": "", "key_points": []})[0])["source"] == "template"


def test_template_numbers_all_come_from_facts(facts):
    t = template_explanation(facts)
    assert unsupported_numbers(t, facts) == []
    assert "Rs 5108" in t["headline"]
