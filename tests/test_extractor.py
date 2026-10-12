import types

import anthropic
import httpx
import pytest

from services.extractor import BillExtractor, ExtractionError
from services.pii import scrub_text
from services.schema import FIELD_KEYS, normalise_extraction, build_tool_schema


def tool_response(payload):
    block = types.SimpleNamespace(type="tool_use", name="record_bill", input=payload)
    return types.SimpleNamespace(content=[block])


class StubClient:
    def __init__(self, response=None, exc=None):
        self.calls = []
        self.messages = self
        self._response, self._exc = response, exc

    def create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return self._response


def extractor(client, key="k"):
    return BillExtractor(key, "test-model", client=client)


def test_happy_path_normalises(synthetic_raw):
    c = StubClient(tool_response(synthetic_raw))
    out = extractor(c).extract(b"img")
    assert out["fields"]["units_consumed"]["value"] == "250"
    assert out["fields"]["units_consumed"]["confirmed"] is False
    assert out["fields"]["fixed_charges"]["value"] is None  # unknown stays unknown
    assert set(out["fields"]) == set(FIELD_KEYS)
    kw = c.calls[0]
    assert kw["tool_choice"] == {"type": "tool", "name": "record_bill"}
    content = kw["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"


def test_prompt_and_schema_ask_for_no_personal_data(synthetic_raw):
    c = StubClient(tool_response(synthetic_raw))
    extractor(c).extract(b"img")
    system = c.calls[0]["system"].lower()
    for word in ("cnic", "address", "meter number", "qr"):
        assert word in system
    props = build_tool_schema()["input_schema"]["properties"]["fields"]["properties"]
    assert not any(w in k for k in props for w in ("name", "address", "cnic", "consumer", "meter_no", "qr"))


def test_missing_key_is_graceful():
    with pytest.raises(ExtractionError) as e:
        BillExtractor("", "m").extract(b"x")
    assert e.value.code == "not_configured" and e.value.status == 503


def test_not_a_bill(synthetic_raw):
    synthetic_raw["is_electricity_bill"] = False
    with pytest.raises(ExtractionError) as e:
        extractor(StubClient(tool_response(synthetic_raw))).extract(b"x")
    assert e.value.code == "not_a_bill"


def test_unreadable(synthetic_raw):
    synthetic_raw["legibility"] = "unreadable"
    with pytest.raises(ExtractionError) as e:
        extractor(StubClient(tool_response(synthetic_raw))).extract(b"x")
    assert e.value.code == "unreadable"


def test_no_tool_call_is_bad_response():
    resp = types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="hi")])
    with pytest.raises(ExtractionError) as e:
        extractor(StubClient(resp)).extract(b"x")
    assert e.value.code == "bad_response"


REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("exc,code", [
    (anthropic.APIConnectionError(request=REQ), "ai_unreachable"),
    (anthropic.APITimeoutError(request=REQ), "ai_unreachable"),
    (anthropic.RateLimitError("x", response=httpx.Response(429, request=REQ), body=None), "ai_busy"),
    (anthropic.AuthenticationError("x", response=httpx.Response(401, request=REQ), body=None), "ai_auth"),
    (anthropic.InternalServerError("x", response=httpx.Response(500, request=REQ), body=None), "ai_error"),
])
def test_api_failures_mapped(exc, code):
    with pytest.raises(ExtractionError) as e:
        extractor(StubClient(exc=exc)).extract(b"x")
    assert e.value.code == code


def test_hostile_model_output_is_sanitised():
    raw = {"is_electricity_bill": True, "legibility": "good",
           "fields": {"units_consumed": {"value": 250, "source_text": "Units 250, CNIC 35202-1234567-1"},
                      "gst": {"value": "x" * 500, "source_text": "<script>"},
                      "evil_key": {"value": "1"}},
           "other_charges": "not a list", "history": [None, 5, {"month": "AUG-25", "units": "10"}]}
    out = normalise_extraction(raw)
    assert "evil_key" not in out["fields"]
    assert "35202" not in out["fields"]["units_consumed"]["source_text"]
    assert len(out["fields"]["gst"]["value"]) <= 60
    assert out["fields"]["units_consumed"]["value"] == "250"
    assert out["history"] == [{"month": "AUG-25", "units": "10", "bill_amount": None}]
    assert out["other_charges"] == []


def test_pii_scrub():
    assert "[redacted]" in scrub_text("CNIC 35202-1234567-1")
    assert "[redacted]" in scrub_text("Ref 05 12345 1234567")
    assert "[redacted]" in scrub_text("mail me a@b.com")
    assert scrub_text("Units Consumed 250") == "Units Consumed 250"
    assert scrub_text("Payable 1,234,567") == "Payable 1,234,567"
