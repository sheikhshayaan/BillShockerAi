"""Single source of truth for bill fields.

Every field is {value, source_text, confirmed, extracted_value}:
  value           what the user/Python will use; None = unknown (never invented)
  source_text     text as printed on the bill (kept for audit)
  confirmed       True once the user has accepted it (or accepted that it is unknown)
  extracted_value what the model originally read, so we can tell user corrections apart
The model only ever returns strings; Python parses them (see validator.py).
"""
from dataclasses import dataclass

from .pii import scrub_text


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str  # text | date | month | int | decimal | money
    section: str
    hint: str = ""
    optional: bool = False


FIELD_SPECS = [
    # --- Bill details
    FieldSpec("billing_month", "Billing month", "month", "Bill details", "e.g. SEP-25"),
    FieldSpec("issue_date", "Issue date", "date", "Bill details"),
    FieldSpec("due_date", "Due date", "date", "Bill details"),
    FieldSpec("reading_date", "Meter reading date", "date", "Bill details"),
    FieldSpec("tariff_category", "Tariff category (as printed)", "text", "Bill details", "e.g. A-1(a)"),
    FieldSpec("sanctioned_load_kw", "Sanctioned load (kW)", "decimal", "Bill details"),
    FieldSpec("protected_indicator_text", "Protected / unprotected wording (as printed)", "text", "Bill details",
              "Only recorded. This app does not decide eligibility."),
    # --- Meter
    FieldSpec("previous_reading", "Previous meter reading", "decimal", "Meter"),
    FieldSpec("present_reading", "Present meter reading", "decimal", "Meter"),
    FieldSpec("multiplying_factor", "Multiplying factor (MF)", "decimal", "Meter"),
    FieldSpec("units_consumed", "Units consumed (kWh)", "decimal", "Meter"),
    # --- Charges, in the order a FESCO bill prints them
    FieldSpec("energy_charge", "Total electricity charges", "money", "Charges",
              "Cost of electricity before subsidies and taxes"),
    FieldSpec("subsidies", "Subsidies", "money", "Charges", "Often 0"),
    FieldSpec("net_electric_charges", "Net electric charges", "money", "Charges"),
    FieldSpec("taxes_total", "Taxes (single line)", "money", "Charges", "Use this if your bill shows one combined taxes line"),
    FieldSpec("current_bill_total", "Current bill (as printed)", "money", "Charges", "Does not include arrears"),
    FieldSpec("fuel_price_adjustment", "Fuel price adjustment (FPA) amount", "money", "Charges",
              "On some bills this is added after the current bill"),
    # --- Optional itemised lines (other layouts print these separately)
    FieldSpec("fixed_charges", "Fixed charges", "money", "Itemised lines (only if your bill lists them)", optional=True),
    FieldSpec("quarterly_adjustment", "Quarterly tariff adjustment", "money", "Itemised lines (only if your bill lists them)", optional=True),
    FieldSpec("electricity_duty", "Electricity duty", "money", "Itemised lines (only if your bill lists them)", optional=True),
    FieldSpec("gst", "GST / sales tax", "money", "Itemised lines (only if your bill lists them)", optional=True),
    FieldSpec("tv_fee", "PTV / TV fee", "money", "Itemised lines (only if your bill lists them)", optional=True),
    FieldSpec("further_tax", "Further / extra tax", "money", "Itemised lines (only if your bill lists them)", optional=True),
    # --- Amounts payable
    FieldSpec("arrears", "Arrears / previous balance", "money", "Amount payable"),
    FieldSpec("installment", "Installment", "money", "Amount payable"),
    FieldSpec("adjustments", "Adjustments", "money", "Amount payable"),
    FieldSpec("payable_within_due_date", "Payable within due date (Grand total)", "money", "Amount payable"),
    FieldSpec("surcharge_date", "Date the first late-surcharge tier ends", "date", "Amount payable", "e.g. “Till 07-SEP-26”"),
    FieldSpec("late_payment_surcharge", "Late payment surcharge, first tier", "money", "Amount payable"),
    FieldSpec("payable_after_due_date", "Payable after due date, first tier", "money", "Amount payable"),
    FieldSpec("late_payment_surcharge_2", "Late payment surcharge, second tier", "money", "Amount payable", "Only if your bill has a second, higher tier"),
    FieldSpec("payable_after_due_date_2", "Payable, second tier", "money", "Amount payable"),
]

FIELD_KEYS = [s.key for s in FIELD_SPECS]
SPEC_BY_KEY = {s.key: s for s in FIELD_SPECS}
CHARGE_COMPONENT_KEYS = [
    "energy_charge", "fixed_charges", "fuel_price_adjustment", "quarterly_adjustment",
    "electricity_duty", "gst", "tv_fee", "further_tax",
]  # itemised layout; aggregate layouts use net_electric_charges + taxes_total (see validator)

MAX_VALUE_LEN = 60
MAX_OTHER_CHARGES = 12
MAX_HISTORY = 24
LEGIBILITY = ("good", "poor", "unreadable")


def _clean_value(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    v = scrub_text(str(v), MAX_VALUE_LEN)
    return v if v not in (None, "", "-", "N/A", "n/a", "null") else None


def blank_field():
    return {"value": None, "source_text": None, "confirmed": False, "extracted_value": None}


def field_status(f):
    """Human-meaningful state of one field."""
    if not f.get("confirmed"):
        return "unconfirmed_unknown" if f.get("value") is None else "unconfirmed"
    if f.get("value") is None:
        return "confirmed_unknown"
    if f.get("value") == f.get("extracted_value"):
        return "confirmed_extracted"
    return "user_corrected"


# ---------------------------------------------------------------- model tool schema
def build_tool_schema():
    str_or_null = {"type": ["string", "null"]}
    field_obj = {
        "type": "object",
        "properties": {
            "value": {**str_or_null, "description": "Value exactly as printed, or null if not visible/legible."},
            "source_text": {**str_or_null, "description": "Short printed text this value came from (label + value only)."},
        },
        "required": ["value", "source_text"],
    }
    return {
        "name": "record_bill",
        "description": "Record what is printed on the electricity bill image. Never guess; use null when unsure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "is_electricity_bill": {"type": "boolean"},
                "legibility": {"type": "string", "enum": list(LEGIBILITY)},
                "utility_seen": {**str_or_null, "description": "Utility name printed in the header, e.g. FESCO, or null."},
                "fields": {
                    "type": "object",
                    "properties": {s.key: {**field_obj, "description": s.label + (". " + s.hint if s.hint else "")} for s in FIELD_SPECS},
                    "required": FIELD_KEYS,
                },
                "other_charges": {
                    "type": "array",
                    "description": "Any additional charge/adjustment lines in the current bill not covered by `fields`.",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}, "amount": str_or_null, "source_text": str_or_null},
                        "required": ["label", "amount"],
                    },
                },
                "history": {
                    "type": "array",
                    "description": "Past-months consumption table if printed (month, units, bill amount).",
                    "items": {
                        "type": "object",
                        "properties": {"month": str_or_null, "units": str_or_null, "bill_amount": str_or_null},
                        "required": ["month", "units"],
                    },
                },
                "notes": {"type": ["string", "null"], "description": "Anything unusual about legibility/layout. No personal data."},
            },
            "required": ["is_electricity_bill", "legibility", "fields"],
        },
    }


# ---------------------------------------------------------------- sanitising
def normalise_extraction(raw):
    """Turn untrusted model output into our canonical shape. Unknown keys are dropped."""
    raw = raw if isinstance(raw, dict) else {}
    raw_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    fields = {}
    for key in FIELD_KEYS:
        item = raw_fields.get(key) if isinstance(raw_fields.get(key), dict) else {}
        value = _clean_value(item.get("value"))
        fields[key] = {
            "value": value,
            "source_text": scrub_text(item.get("source_text")),
            "confirmed": False,
            "extracted_value": value,
        }
    other = []
    for item in (raw.get("other_charges") or [])[:MAX_OTHER_CHARGES]:
        if not isinstance(item, dict):
            continue
        amount = _clean_value(item.get("amount"))
        other.append({
            "label": scrub_text(item.get("label"), 60) or "Other charge",
            "amount": amount, "extracted_value": amount,
            "source_text": scrub_text(item.get("source_text")), "confirmed": False,
        })
    history = []
    for item in (raw.get("history") or [])[:MAX_HISTORY]:
        if not isinstance(item, dict):
            continue
        history.append({
            "month": _clean_value(item.get("month")),
            "units": _clean_value(item.get("units")),
            "bill_amount": _clean_value(item.get("bill_amount")),
        })
    legibility = raw.get("legibility") if raw.get("legibility") in LEGIBILITY else "poor"
    return {
        "is_electricity_bill": raw.get("is_electricity_bill") is True,
        "legibility": legibility,
        "utility_seen": scrub_text(raw.get("utility_seen"), 40),
        "fields": fields,
        "other_charges": other,
        "history": history,
        "notes": scrub_text(raw.get("notes"), 300),
    }


def sanitize_review_payload(payload):
    """Validate the JSON the review screen posts back. Never trust the client."""
    payload = payload if isinstance(payload, dict) else {}
    in_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    fields = {}
    for key in FIELD_KEYS:
        item = in_fields.get(key) if isinstance(in_fields.get(key), dict) else {}
        fields[key] = {
            "value": _clean_value(item.get("value")),
            "source_text": scrub_text(item.get("source_text")),
            "confirmed": item.get("confirmed") is True,
            "extracted_value": _clean_value(item.get("extracted_value")),
        }
    other = []
    for item in (payload.get("other_charges") or [])[:MAX_OTHER_CHARGES]:
        if isinstance(item, dict):
            other.append({
                "label": scrub_text(item.get("label"), 60) or "Other charge",
                "amount": _clean_value(item.get("amount")),
                "extracted_value": _clean_value(item.get("extracted_value")),
                "source_text": scrub_text(item.get("source_text")),
                "confirmed": item.get("confirmed") is True,
            })
    history = []
    for item in (payload.get("history") or [])[:MAX_HISTORY]:
        if isinstance(item, dict):
            history.append({k: _clean_value(item.get(k)) for k in ("month", "units", "bill_amount")})
    return {"fields": fields, "other_charges": other, "history": history}
