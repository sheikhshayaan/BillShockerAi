"""Environment-driven configuration. Read at app-creation time so tests can override."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def load_config():
    gem, ant = os.environ.get("GEMINI_API_KEY", "").strip(), os.environ.get("ANTHROPIC_API_KEY", "").strip()
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower() or ("anthropic" if ant and not gem else "gemini")
    return {
        "LLM_PROVIDER": provider if provider in ("gemini", "anthropic") else "gemini",
        "GEMINI_API_KEY": gem,
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip(),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip(),
        "ANTHROPIC_TIMEOUT_S": _int("ANTHROPIC_TIMEOUT_S", 60),
        "MAX_UPLOAD_MB": _int("MAX_UPLOAD_MB", 8),
        "SYNTHETIC_MODE": os.environ.get("BILLSHOCKER_SYNTHETIC", "0") == "1",
        "TARIFF_FILE": BASE_DIR / "data" / "tariffs" / "fesco_tariffs.json",
        "SYNTHETIC_BILL_FILE": BASE_DIR / "data" / "demo" / "synthetic_bill.json",
    }
