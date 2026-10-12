import json

import httpx
import pytest

from config import load_config
from services.extractor import BillExtractor
from services.explainer import Explainer
from services.llm import AnthropicBackend, GeminiBackend, LLMError, make_backend, to_gemini_schema
from services.schema import build_tool_schema
from tests.conftest import FakeExtractor, make_image

KEY = "AIza-FAKE-TEST-KEY-123"


def gemini(handler, key=KEY, model="gemini-test"):
    return GeminiBackend(key, model, http=httpx.Client(transport=httpx.MockTransport(handler)))


def ok_response(payload, **extra):
    cand = {"content": {"parts": [{"text": json.dumps(payload)}]}, "finishReason": "STOP", **extra}
    return httpx.Response(200, json={"candidates": [cand]})


def err(status, message="x"):
    return httpx.Response(status, json={"error": {"code": status, "message": message}})


def test_request_shape_and_key_handling(synthetic_raw):
    seen = {}

    def handler(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return ok_response(synthetic_raw)

    out = BillExtractor(backend=gemini(handler)).extract(b"IMG")
    assert out["fields"]["units_consumed"]["value"] == "250"
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert seen["key"] == KEY and KEY not in seen["url"] and KEY not in json.dumps(seen["body"])
    b = seen["body"]
    assert b["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/jpeg"
    assert b["generationConfig"]["responseMimeType"] == "application/json"
    assert b["generationConfig"]["temperature"] == 0
    assert "CNIC" in b["systemInstruction"]["parts"][0]["text"]


def test_schema_conversion_is_gemini_compatible():
    g = to_gemini_schema(build_tool_schema()["input_schema"])
    blob = json.dumps(g)
    assert g["type"] == "OBJECT" and "maxLength" not in blob and "additionalProperties" not in blob
    unit = g["properties"]["fields"]["properties"]["units_consumed"]["properties"]["value"]
    assert unit == {"type": "STRING", "nullable": True, "description": unit["description"]}
    assert g["properties"]["legibility"]["enum"] == ["good", "poor", "unreadable"]
    assert '"null"' not in blob and "['" not in blob


def test_schema_rejection_retries_with_schema_in_prompt(synthetic_raw):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return err(400, "Invalid JSON payload: unknown field in response_schema") if len(bodies) == 1 else ok_response(synthetic_raw)

    out = BillExtractor(backend=gemini(handler)).extract(b"IMG")
    assert out["is_electricity_bill"] and len(bodies) == 2
    assert "responseSchema" in bodies[0]["generationConfig"] and "responseSchema" not in bodies[1]["generationConfig"]
    assert "JSON Schema" in bodies[1]["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize("resp,code,status", [
    (err(400, "API key not valid. Please pass a valid API key."), "ai_auth", 503),
    (err(403, "denied"), "ai_auth", 503),
    (err(404, "model not found"), "ai_model", 503),
    (err(429, "quota"), "ai_busy", 429),
    (err(503, "overloaded"), "ai_error", 502),
])
def test_http_errors_mapped(resp, code, status):
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(lambda r: resp)).extract(b"IMG")
    assert e.value.code == code and e.value.status == status and KEY not in e.value.message


def test_bad_key_is_not_retried():
    calls = []
    def handler(req):
        calls.append(1)
        return err(400, "API key not valid")
    with pytest.raises(LLMError):
        BillExtractor(backend=gemini(handler)).extract(b"IMG")
    assert len(calls) == 1


def test_network_failures_mapped():
    def boom(req):
        raise httpx.ConnectTimeout("t")
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(boom)).extract(b"IMG")
    assert e.value.code == "ai_unreachable"
    def down(req):
        raise httpx.ConnectError("c")
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(down)).extract(b"IMG")
    assert e.value.code == "ai_unreachable"


def test_missing_key():
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=GeminiBackend("", "m")).extract(b"IMG")
    assert e.value.code == "not_configured" and e.value.status == 503


def test_blocked_and_unreadable_outputs():
    blocked = httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(lambda r: blocked)).extract(b"IMG")
    assert e.value.code == "blocked"
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(lambda r: ok_response({}, finishReason="SAFETY"))).extract(b"IMG")
    assert e.value.code == "blocked"
    garbage = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not json {"}]}, "finishReason": "MAX_TOKENS"}]})
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(lambda r: garbage)).extract(b"IMG")
    assert e.value.code == "bad_response"
    arr = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[1,2]"}]}}]})
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(lambda r: arr)).extract(b"IMG")
    assert e.value.code == "bad_response"


def test_code_fences_and_thought_parts_tolerated(synthetic_raw):
    text = "```json\n" + json.dumps(synthetic_raw) + "\n```"
    r = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": text}]}}]})
    assert BillExtractor(backend=gemini(lambda req: r)).extract(b"IMG")["fields"]["units_consumed"]["value"] == "250"


def test_not_a_bill_and_hostile_output(synthetic_raw):
    synthetic_raw["is_electricity_bill"] = False
    with pytest.raises(LLMError) as e:
        BillExtractor(backend=gemini(lambda r: ok_response(synthetic_raw))).extract(b"IMG")
    assert e.value.code == "not_a_bill"
    hostile = {"is_electricity_bill": True, "legibility": "good", "fields": {"units_consumed": {"value": "250", "source_text": "CNIC 35202-1234567-1"}}}
    out = BillExtractor(backend=gemini(lambda r: ok_response(hostile))).extract(b"IMG")
    assert "35202" not in out["fields"]["units_consumed"]["source_text"]


def test_explainer_over_gemini_text_only(review_payload):
    from services.explainer import build_facts, explain
    from services.validator import validate_bill
    facts = build_facts(review_payload, validate_bill(review_payload))
    seen = {}
    good = {"headline": "Your SEP-25 bill is Rs 5108", "summary": "ok", "key_points": ["Units are 250."]}
    def handler(req):
        seen["body"] = json.loads(req.content)
        return ok_response(good)
    out = explain(facts, "ur", Explainer(backend=gemini(handler)))
    assert out["source"] == "ai" and out["lang"] == "ur"
    parts = seen["body"]["contents"][0]["parts"]
    assert len(parts) == 1 and "<facts>" in parts[0]["text"] and "inlineData" not in json.dumps(seen["body"])
    assert "Urdu script" in seen["body"]["systemInstruction"]["parts"][0]["text"]
    bad = explain(facts, "en", Explainer(backend=gemini(lambda r: err(429))))
    assert bad["source"] == "template"


# ---- provider selection / app wiring
def test_provider_selection(monkeypatch):
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert isinstance(make_backend(load_config()), GeminiBackend)
    monkeypatch.delenv("GEMINI_API_KEY"); monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert isinstance(make_backend(load_config()), AnthropicBackend)
    monkeypatch.setenv("GEMINI_API_KEY", "g"); monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(make_backend(load_config()), AnthropicBackend)
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    assert isinstance(make_backend(load_config()), GeminiBackend)
    monkeypatch.delenv("GEMINI_API_KEY"); monkeypatch.delenv("ANTHROPIC_API_KEY"); monkeypatch.delenv("LLM_PROVIDER")
    assert make_backend(load_config()).configured is False


def test_app_end_to_end_with_gemini(make_app, synthetic_raw, jpeg_bytes):
    import io
    app = make_app(None, LLM_PROVIDER="gemini", GEMINI_API_KEY=KEY, GEMINI_MODEL="gemini-test")
    app.extensions["backend"]._http = httpx.Client(transport=httpx.MockTransport(lambda r: ok_response(synthetic_raw)))
    c = app.test_client()
    j = c.get("/healthz").get_json()
    assert j["provider"] == "gemini" and j["ai_configured"] is True
    html = c.get("/").get_data(as_text=True)
    assert "free Gemini key" in html and "sent to Google Gemini" in html
    r = c.post("/api/extract", data={"bill": (io.BytesIO(jpeg_bytes), "b.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["extraction"]["fields"]["units_consumed"]["value"] == "250"


def test_app_without_gemini_key_is_graceful(make_app, jpeg_bytes):
    import io
    app = make_app(None, LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    c = app.test_client()
    assert "set up" in c.get("/").get_data(as_text=True).lower()
    r = c.post("/api/extract", data={"bill": (io.BytesIO(jpeg_bytes), "b.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "not_configured"
