"""Bill Shocker - Flask app. Uploads are processed in memory and never written to disk."""
import logging
import os

from flask import Flask, jsonify, render_template, request

from config import load_config
from services.llm import make_backend
from services.explainer import Explainer, NotReady, build_facts, explain
from services.factors import QUESTIONS, evaluate
from services.extractor import BillExtractor, ExtractionError, SyntheticExtractor
from services.image_check import ImageRejected, prepare_image
from services.schema import FIELD_SPECS, sanitize_review_payload
from services.tariffs import load_tariffs, tariff_status
from services.validator import validate_bill

log = logging.getLogger("billshocker")


def create_app(overrides=None, extractor=None):
    app = Flask(__name__)
    app.config.update(load_config())
    if overrides:
        app.config.update(overrides)
    app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_UPLOAD_MB"] * 1024 * 1024 + 4096

    backend = make_backend(app.config)
    app.extensions["backend"] = backend
    if extractor is None:
        extractor = SyntheticExtractor(app.config["SYNTHETIC_BILL_FILE"]) if app.config["SYNTHETIC_MODE"] else BillExtractor(backend=backend)
    app.extensions["extractor"] = extractor
    if "explainer" in (overrides or {}):
        explainer = overrides["explainer"]
    elif not app.config["SYNTHETIC_MODE"] and backend.configured:
        explainer = Explainer(backend=backend)
    else:
        explainer = None
    app.extensions["explainer"] = explainer

    def ai_ready():
        if app.config["SYNTHETIC_MODE"] or not isinstance(extractor, BillExtractor):
            return True
        return extractor.backend.configured

    @app.context_processor
    def provider_info():
        return {"provider_name": backend.label, "provider_id": backend.name}

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def err(code, message, status):
        return jsonify({"ok": False, "error": {"code": code, "message": message}}), status

    @app.errorhandler(413)
    def too_big(_):
        return err("too_large", f"The photo is larger than {app.config['MAX_UPLOAD_MB']} MB. Please use a smaller photo.", 413)

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def upload_page():
        return render_template("upload.html", ai_ready=ai_ready(), synthetic=app.config["SYNTHETIC_MODE"],
                               max_mb=app.config["MAX_UPLOAD_MB"])

    @app.get("/review")
    def review_page():
        specs = [{"key": s.key, "label": s.label, "kind": s.kind, "section": s.section, "hint": s.hint, "optional": s.optional} for s in FIELD_SPECS]
        return render_template("review.html", specs=specs)

    @app.get("/results")
    def results_page():
        return render_template("results.html", tariff=tariff_status(load_tariffs(app.config["TARIFF_FILE"])), questions=QUESTIONS)

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "ai_configured": ai_ready(), "provider": backend.name, "synthetic_mode": app.config["SYNTHETIC_MODE"]})

    # ---------------------------------------------------------------- API
    @app.post("/api/extract")
    def api_extract():
        f = request.files.get("bill")
        if f is None:
            return err("no_file", "No photo was received. Please choose a photo of your bill.", 400)
        try:
            prepared = prepare_image(f.read(), app.config["MAX_UPLOAD_MB"] * 1024 * 1024)
        except ImageRejected as e:
            return err(e.code, e.message, e.status)
        try:
            extraction = app.extensions["extractor"].extract(prepared.data, prepared.media_type)
        except ExtractionError as e:
            log.warning("extraction failed: %s", e.code)
            return err(e.code, e.message, e.status)
        except Exception:  # never leak internals or the image
            log.exception("unexpected extraction failure")
            return err("internal", "Something went wrong while reading the bill. Please try again.", 500)
        return jsonify({"ok": True, "extraction": extraction, "image": {"width": prepared.width, "height": prepared.height,
                        "warnings": prepared.warnings}, "synthetic": app.config["SYNTHETIC_MODE"]})

    @app.post("/api/validate")
    def api_validate():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return err("bad_request", "Expected a JSON body.", 400)
        return jsonify({"ok": True, "validation": validate_bill(sanitize_review_payload(payload))})

    def _checked(payload_json):
        if not isinstance(payload_json, dict):
            return None, None, err("bad_request", "Expected a JSON body.", 400)
        payload = sanitize_review_payload(payload_json.get("payload", payload_json))
        validation = validate_bill(payload)
        if not validation["all_confirmed"]:
            return None, None, err("not_confirmed", "Please confirm every field on the review screen first.", 409)
        return payload, validation, None

    @app.post("/api/explain")
    def api_explain():
        body = request.get_json(silent=True)
        payload, validation, bad = _checked(body)
        if bad:
            return bad
        lang = body.get("lang") if body.get("lang") in ("en", "ur") else "en"
        try:
            facts = build_facts(payload, validation)
        except NotReady as e:
            return err("not_confirmed", str(e), 409)
        return jsonify({"ok": True, **explain(facts, lang, app.extensions["explainer"])})

    @app.post("/api/factors")
    def api_factors():
        body = request.get_json(silent=True)
        payload, validation, bad = _checked(body)
        if bad:
            return bad
        return jsonify({"ok": True, **evaluate(payload, validation, body.get("answers"))})

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
                    debug=os.environ.get("FLASK_DEBUG") == "1")


# Module-level instance: Vercel (and `flask --app app run`) look for a variable named `app`.
app = create_app()
