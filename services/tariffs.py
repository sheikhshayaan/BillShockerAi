"""Loads the versioned tariff JSON. Only versions with status == 'verified' may be used for estimates."""
import json


def load_tariffs(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verified_version(data, on_date=None):
    """Return the newest verified version (optionally effective on `on_date`, ISO string), else None."""
    best = None
    for v in data.get("versions", []):
        if v.get("status") != "verified" or not v.get("effective_from") or not v.get("categories"):
            continue
        if on_date and v["effective_from"] > on_date:
            continue
        if best is None or v["effective_from"] > best["effective_from"]:
            best = v
    return best


def tariff_status(data):
    v = verified_version(data)
    if v is None:
        return {"verified": False, "message": "No verified FESCO tariff rates are loaded, so this app shows bill-printed amounts only and makes no tariff-based estimates."}
    return {"verified": True, "version": v["id"], "effective_from": v["effective_from"], "source": v.get("source")}
