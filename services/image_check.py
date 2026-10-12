"""Server-side image validation + sanitising. Everything stays in memory; nothing is written to disk."""
import io
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 50_000_000  # decompression-bomb guard
MAX_EDGE = 1568  # long edge sent to the model
MIN_SHORT_EDGE = 300


class ImageRejected(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class PreparedImage:
    data: bytes
    media_type: str
    width: int
    height: int
    warnings: list = field(default_factory=list)


def _sniff(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def prepare_image(data, max_bytes):
    if not data:
        raise ImageRejected("empty_file", "The file is empty. Please choose a photo of your bill.")
    if len(data) > max_bytes:
        raise ImageRejected("too_large", f"The photo is larger than {max_bytes // (1024 * 1024)} MB. Please use a smaller photo.", 413)
    kind = _sniff(data)
    if kind is None:
        heic = data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1")
        msg = ("HEIC photos are not supported yet. Please take the photo as JPEG or share it as a screenshot."
               if heic else "Unsupported file type. Please upload a JPEG, PNG or WebP photo.")
        raise ImageRejected("unsupported_type", msg, 415)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # honour phone rotation, then drop all metadata below
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError):
        raise ImageRejected("corrupt_image", "That image could not be opened. It may be damaged; please try another photo.", 422)

    if min(img.size) < MIN_SHORT_EDGE:
        raise ImageRejected("too_small", "The photo is too small to read. Please take a closer, higher-resolution photo.", 422)

    gray = img.convert("L")
    if ImageStat.Stat(gray).stddev[0] < 4:
        raise ImageRejected("blank_image", "The photo looks blank or almost one colour. Please retake it in good light.", 422)

    warnings = []
    small = gray.copy()
    small.thumbnail((800, 800))
    edge_var = ImageStat.Stat(small.filter(ImageFilter.FIND_EDGES)).var[0]
    if edge_var < 60:  # heuristic only; never blocks
        warnings.append("The photo may be blurry or low-contrast. If extraction looks wrong, retake it with the bill flat and well lit.")

    img.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)  # re-encoding strips EXIF/GPS
    return PreparedImage(out.getvalue(), "image/jpeg", img.width, img.height, warnings)
