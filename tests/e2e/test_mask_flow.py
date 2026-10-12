"""Browser test of the in-browser blackout tool. Needs: pip install playwright (+ a Chromium). Skipped otherwise.

It proves what matters for privacy: the bytes the browser uploads contain BLACK where the user drew, no EXIF, and nothing is
sent when the photo can't be decoded.
"""
import io
import re
import threading

import pytest

pytest.importorskip("playwright.sync_api")
from PIL import Image, ImageDraw  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402


def make_photo(tmp_path, name="bill.jpg", with_exif=True):
    img = Image.new("RGB", (1000, 1400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 499, 1399], fill=(200, 30, 30))          # left half red
    for y in range(40, 1380, 60):
        d.rectangle([520, y, 900 - (y * 3) % 200, y + 14], fill=(20, 20, 20))
    exif = Image.Exif()
    exif[0x010F] = "SecretPhoneMaker"
    p = tmp_path / name
    img.save(p, format="JPEG", quality=95, **({"exif": exif.tobytes()} if with_exif else {}))
    assert b"SecretPhoneMaker" in p.read_bytes() or not with_exif
    return p


@pytest.fixture
def site():
    app = create_app({"TESTING": True, "SYNTHETIC_MODE": True, "explainer": None})
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def page(site):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 800, "height": 1800}, reduced_motion="reduce")  # tall enough that the whole canvas is on screen
        pg.route("**/cdn.tailwindcss.com/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body="window.tailwind={};"))
        pg.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        pg.uploads = []
        pg.on("request", lambda r: pg.uploads.append(r.post_data_buffer) if r.url.endswith("/api/extract") else None)
        pg.goto(site + "/")
        yield pg
        browser.close()


def stable_box(pg):
    """Canvas box once its size has stopped changing (image decode / layout can resize it)."""
    loc, last = pg.locator("#mask-canvas"), None
    for _ in range(40):
        box = loc.bounding_box()
        if box == last and box["width"] > 50:
            return box
        last = box
        pg.wait_for_timeout(75)
    return last


def drag(pg, x0, y0, x1, y1):
    box = stable_box(pg)
    pg.mouse.move(box["x"] + box["width"] * x0, box["y"] + box["height"] * y0)
    pg.mouse.down()
    pg.mouse.move(box["x"] + box["width"] * (x0 + x1) / 2, box["y"] + box["height"] * (y0 + y1) / 2)
    pg.mouse.move(box["x"] + box["width"] * x1, box["y"] + box["height"] * y1)
    pg.mouse.up()


def jpeg_from_multipart(buf):
    start = buf.index(b"\xff\xd8\xff")
    end = buf.rindex(b"\r\n--")
    return buf[start:end]


def test_gating_requires_cover_or_confirmation(page, tmp_path):
    assert page.is_disabled("#go") and page.is_hidden("#mask-panel")
    page.set_input_files("#bill", str(make_photo(tmp_path)))
    page.wait_for_selector("#mask-panel:not(.hidden)")
    assert page.is_disabled("#go")
    page.check("#no-pii"); assert page.is_enabled("#go")
    page.uncheck("#no-pii"); assert page.is_disabled("#go")
    drag(page, 0.1, 0.1, 0.4, 0.3)
    assert "1 area covered" in page.inner_text("#mask-count")
    assert page.is_enabled("#go") and page.is_disabled("#no-pii") and not page.is_checked("#no-pii")
    page.click("#mask-undo")
    assert page.is_disabled("#go") and "Nothing covered" in page.inner_text("#mask-count")


def test_uploaded_bytes_are_masked_and_metadata_free(page, tmp_path):
    page.set_input_files("#bill", str(make_photo(tmp_path)))
    page.wait_for_selector("#mask-panel:not(.hidden)")
    drag(page, 0.1, 0.1, 0.4, 0.3)       # inside the red half
    drag(page, 0.6, 0.5, 0.9, 0.6)       # over the text lines on the right
    page.click("#go")
    page.wait_for_url("**/review")
    assert len(page.uploads) == 1
    raw = page.uploads[0]
    assert b"SecretPhoneMaker" not in raw and b"Exif" not in raw
    img = Image.open(io.BytesIO(jpeg_from_multipart(raw))).convert("RGB")
    W, H = img.size
    assert max(W, H) <= 2000
    mean = lambda box: [sum(c) / len(c) for c in zip(*[img.getpixel((x, y)) for x in range(box[0], box[2], 7) for y in range(box[1], box[3], 7)])]
    inside1 = mean((int(W * .16), int(H * .15), int(W * .34), int(H * .25)))
    inside2 = mean((int(W * .66), int(H * .53), int(W * .84), int(H * .57)))
    outside_red = mean((int(W * .12), int(H * .5), int(W * .38), int(H * .7)))
    outside_white = mean((int(W * .92), int(H * .05), int(W * .98), int(H * .2)))
    assert max(inside1) < 12 and max(inside2) < 12, (inside1, inside2)          # truly black
    assert outside_red[0] > 150 and outside_red[1] < 90                         # untouched
    assert min(outside_white) > 230                                             # untouched


def test_accidental_tap_does_not_add_box(page, tmp_path):
    page.set_input_files("#bill", str(make_photo(tmp_path)))
    page.wait_for_selector("#mask-panel:not(.hidden)")
    box = page.locator("#mask-canvas").bounding_box()
    page.mouse.click(box["x"] + 50, box["y"] + 50)
    assert "Nothing covered" in page.inner_text("#mask-count") and page.is_disabled("#go")


def test_undecodable_photo_fails_closed(page, tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff" + b"not really a jpeg" * 20)
    page.set_input_files("#bill", str(bad))
    page.wait_for_selector("#error:not(.hidden)")
    assert "can't be covered safely" in page.inner_text("#error")
    assert page.is_hidden("#mask-panel") and page.is_disabled("#go") and page.uploads == []


def test_clear_all_and_new_photo_resets(page, tmp_path):
    page.set_input_files("#bill", str(make_photo(tmp_path)))
    page.wait_for_selector("#mask-panel:not(.hidden)")
    drag(page, 0.1, 0.1, 0.3, 0.2); drag(page, 0.5, 0.5, 0.7, 0.6)
    assert "2 areas covered" in page.inner_text("#mask-count")
    page.click("#mask-clear")
    assert "Nothing covered" in page.inner_text("#mask-count")
    drag(page, 0.1, 0.1, 0.3, 0.2)
    page.set_input_files("#bill", str(make_photo(tmp_path, "second.jpg")))
    page.wait_for_function("document.getElementById('mask-count').textContent.includes('Nothing covered')")
    assert page.is_disabled("#go")


def test_server_never_sees_original_when_user_covers(page, tmp_path):
    """The full flow lands on the review screen using the covered image only."""
    page.set_input_files("#bill", str(make_photo(tmp_path)))
    page.wait_for_selector("#mask-panel:not(.hidden)")
    drag(page, 0.02, 0.02, 0.98, 0.5)       # cover the whole top half
    page.click("#go"); page.wait_for_url("**/review")
    page.wait_for_selector("#findings li")
    assert re.search(r"covered_areas", page.evaluate("sessionStorage.getItem('bs_extraction')"))
