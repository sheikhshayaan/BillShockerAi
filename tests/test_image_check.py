import io

import pytest
from PIL import Image

from services.image_check import ImageRejected, prepare_image
from tests.conftest import make_image

MB = 1024 * 1024


def reject_code(data, max_bytes=8 * MB):
    with pytest.raises(ImageRejected) as e:
        prepare_image(data, max_bytes)
    return e.value.code


def test_valid_jpeg_png_webp(jpeg_bytes):
    for fmt in ("JPEG", "PNG", "WEBP"):
        p = prepare_image(make_image(fmt), 8 * MB)
        assert p.media_type == "image/jpeg" and p.data.startswith(b"\xff\xd8")


def test_empty_and_too_large(jpeg_bytes):
    assert reject_code(b"") == "empty_file"
    assert reject_code(jpeg_bytes, max_bytes=100) == "too_large"


def test_non_image_and_fake_extension_rejected():
    assert reject_code(b"%PDF-1.4 hello") == "unsupported_type"
    assert reject_code(b"<html><script>alert(1)</script></html>") == "unsupported_type"


def test_heic_gets_helpful_message():
    with pytest.raises(ImageRejected) as e:
        prepare_image(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100, MB)
    assert "HEIC" in e.value.message


def test_truncated_image_rejected(jpeg_bytes):
    assert reject_code(jpeg_bytes[:300] + b"garbage") == "corrupt_image"


def test_tiny_and_blank_rejected():
    assert reject_code(make_image(size=(100, 100))) == "too_small"
    assert reject_code(make_image(textured=False)) == "blank_image"


def test_large_image_downscaled():
    p = prepare_image(make_image(size=(4000, 3000)), 20 * MB)
    assert max(p.width, p.height) == 1568


def test_exif_is_stripped():
    exif = Image.Exif()
    exif[0x010F] = "SecretPhoneMaker"
    textured = Image.open(io.BytesIO(make_image(size=(800, 800))))
    buf2 = io.BytesIO()
    textured.save(buf2, format="JPEG", exif=exif.tobytes())
    assert b"SecretPhoneMaker" in buf2.getvalue()
    p = prepare_image(buf2.getvalue(), 8 * MB)
    assert b"SecretPhoneMaker" not in p.data
