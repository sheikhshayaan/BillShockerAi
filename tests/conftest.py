import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import create_app
from services.schema import normalise_extraction

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "demo" / "synthetic_bill.json"


@pytest.fixture
def synthetic_raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def aggregate_payload():
    """Fully-confirmed payload in the aggregated FESCO-style layout (net + taxes line, FPA after current bill)."""
    raw = json.loads((ROOT / "data" / "demo" / "synthetic_bill_aggregate.json").read_text(encoding="utf-8"))
    ex = normalise_extraction(raw)
    for f in ex["fields"].values():
        f["confirmed"] = True
    return {"fields": ex["fields"], "other_charges": ex["other_charges"], "history": ex["history"]}


@pytest.fixture
def review_payload(synthetic_raw):
    """A fully-confirmed review payload built from the synthetic bill."""
    ex = normalise_extraction(synthetic_raw)
    for f in ex["fields"].values():
        f["confirmed"] = True
    return {"fields": ex["fields"], "other_charges": ex["other_charges"], "history": ex["history"]}


def make_image(fmt="JPEG", size=(900, 1200), textured=True):
    img = Image.new("RGB", size, (250, 250, 250))
    if textured:
        d = ImageDraw.Draw(img)
        for y in range(40, max(size[1] - 40, 41), 38):
            x1 = max(size[0] - 40 - (y * 7) % 300, 41)
            d.rectangle([40, y, x1, y + 14], fill=(30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def jpeg_bytes():
    return make_image()


class FakeExtractor:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    def extract(self, image_bytes, media_type="image/jpeg"):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def make_app():
    def _make(extractor=None, explainer=None, **overrides):
        return create_app({"TESTING": True, "LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test", "SYNTHETIC_MODE": False, "explainer": explainer, **overrides}, extractor=extractor)
    return _make
