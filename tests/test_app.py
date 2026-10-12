import io
import json

from services.extractor import ExtractionError
from services.schema import normalise_extraction
from tests.conftest import FakeExtractor, make_image


def post_bill(client, data, name="bill.jpg"):
    return client.post("/api/extract", data={"bill": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def test_pages_render(make_app):
    c = make_app(FakeExtractor()).test_client()
    for url in ("/", "/review", "/results", "/healthz"):
        assert c.get(url).status_code == 200


def test_upload_page_warns_when_not_configured(make_app):
    app = make_app(None, ANTHROPIC_API_KEY="")
    html = app.test_client().get("/").get_data(as_text=True)
    assert "isn&#39;t set up" in html.lower() or "isn't set up" in html.lower()


def test_extract_success(make_app, synthetic_raw, jpeg_bytes):
    ex = FakeExtractor(normalise_extraction(synthetic_raw))
    r = post_bill(make_app(ex).test_client(), jpeg_bytes)
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and ex.calls == 1
    assert j["extraction"]["fields"]["units_consumed"]["value"] == "250"


def test_extract_no_file(make_app):
    r = make_app(FakeExtractor()).test_client().post("/api/extract", data={}, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "no_file"


def test_extract_rejects_non_image_before_calling_ai(make_app):
    ex = FakeExtractor()
    r = post_bill(make_app(ex).test_client(), b"%PDF-1.4 not an image", "bill.jpg")
    assert r.status_code == 415 and ex.calls == 0


def test_extract_too_large_returns_json(make_app):
    app = make_app(FakeExtractor(), MAX_UPLOAD_MB=1)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 + 4096
    r = post_bill(app.test_client(), b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024))
    assert r.status_code == 413 and r.get_json()["error"]["code"] == "too_large"


def test_extraction_errors_pass_friendly_messages(make_app, jpeg_bytes):
    for code, status in (("not_a_bill", 422), ("ai_busy", 429), ("not_configured", 503)):
        ex = FakeExtractor(exc=ExtractionError(code, "friendly", status))
        r = post_bill(make_app(ex).test_client(), jpeg_bytes)
        assert r.status_code == status and r.get_json()["error"] == {"code": code, "message": "friendly"}


def test_unexpected_exception_hides_internals(make_app, jpeg_bytes):
    r = post_bill(make_app(FakeExtractor(exc=RuntimeError("secret sk-ant-123"))).test_client(), jpeg_bytes)
    assert r.status_code == 500 and "sk-ant" not in r.get_data(as_text=True)


def test_missing_api_key_end_to_end(make_app, jpeg_bytes):
    r = post_bill(make_app(None, ANTHROPIC_API_KEY="").test_client(), jpeg_bytes)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "not_configured"


def test_synthetic_mode_is_labelled(make_app, jpeg_bytes):
    r = post_bill(make_app(None, SYNTHETIC_MODE=True, ANTHROPIC_API_KEY="").test_client(), jpeg_bytes)
    j = r.get_json()
    assert j["ok"] and j["synthetic"] is True and j["extraction"]["utility_seen"] == "SYNTHETIC-DEMO"


def test_uploads_not_written_to_disk(make_app, jpeg_bytes, synthetic_raw, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    post_bill(make_app(FakeExtractor(normalise_extraction(synthetic_raw))).test_client(), jpeg_bytes)
    assert list(tmp_path.iterdir()) == []


def test_validate_endpoint(make_app, review_payload):
    c = make_app(FakeExtractor()).test_client()
    r = c.post("/api/validate", json=review_payload)
    assert r.status_code == 200 and r.get_json()["validation"]["status"] == "consistent"
    review_payload["fields"]["gst"]["value"] = "999"
    assert c.post("/api/validate", json=review_payload).get_json()["validation"]["status"] == "discrepancies"


def test_validate_rejects_non_json_and_tolerates_junk(make_app):
    c = make_app(FakeExtractor()).test_client()
    assert c.post("/api/validate", data="nope").status_code == 400
    r = c.post("/api/validate", json={"fields": {"gst": "oops", "units_consumed": {"value": {"x": 1}}}, "history": "x"})
    assert r.status_code == 200


def test_explain_endpoint_template_without_ai(make_app, review_payload):
    c = make_app(FakeExtractor()).test_client()
    j = c.post("/api/explain", json={"payload": review_payload, "lang": "en"}).get_json()
    assert j["ok"] and j["source"] == "template" and j["explanation"]["key_points"]


def test_explain_endpoint_uses_ai_and_guards_numbers(make_app, review_payload):
    from tests.test_explainer import GOOD, ai
    ex, _ = ai(GOOD)
    j = make_app(FakeExtractor(), explainer=ex).test_client().post("/api/explain", json={"payload": review_payload, "lang": "ur"}).get_json()
    assert j["source"] == "ai" and j["lang"] == "ur"


def test_explain_requires_confirmed_fields(make_app, review_payload):
    review_payload["fields"]["units_consumed"]["confirmed"] = False
    c = make_app(FakeExtractor()).test_client()
    r = c.post("/api/explain", json={"payload": review_payload})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "not_confirmed"
    assert c.post("/api/factors", json={"payload": review_payload}).status_code == 409
    assert c.post("/api/explain", data="x").status_code == 400


def test_factors_endpoint(make_app, review_payload):
    c = make_app(FakeExtractor()).test_client()
    j = c.post("/api/factors", json={"payload": review_payload, "answers": {"ac_count": "one", "ac_hours": "lt4", "evil": 1}}).get_json()
    assert j["ok"] and j["answers"] == {"ac_count": "one", "ac_hours": "lt4"} and j["factors"]
