"""Defence-in-depth scrubbing of text that comes back from the model.

The prompt tells the model not to transcribe identifiers, but we never rely on that alone.
"""
import re

# 10+ digits, optionally separated by single spaces/hyphens -> CNIC, consumer/reference IDs, meter numbers, phones.
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\d[\s-]?){10,}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
REDACTED = "[redacted]"


def scrub_text(text, max_len=160):
    if text is None:
        return None
    text = str(text)
    text = _EMAIL.sub(REDACTED, text)
    text = _LONG_DIGITS.sub(REDACTED, text)
    text = " ".join(text.split())
    return text[:max_len] or None
