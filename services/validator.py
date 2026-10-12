"""Plain-Python validation and arithmetic. No AI involved.

Input: the sanitised review payload (see schema.sanitize_review_payload).
Output: findings (ok/info/warn/error), derived figures computed from PRINTED values only, and
        a field-state summary. Nothing here invents a missing value.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .schema import CHARGE_COMPONENT_KEYS, FIELD_KEYS, SPEC_BY_KEY, field_status

TOLERANCE = Decimal("3")  # Rs; printed bills round each line, so small gaps are normal
HIGH_UNITS_NOTE = Decimal("2000")
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_DATE_FORMATS = ["%d %b %y", "%d %b %Y", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%d/%m/%y",
                 "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"]


class BadNumber(ValueError):
    pass


def parse_number(s):
    """'Rs. 1,234.50' -> Decimal('1234.50'); '(120)' -> -120. None -> None. Garbage -> BadNumber."""
    if s is None:
        return None
    t = str(s).strip().upper().replace("RS.", "").replace("RS", "").replace("PKR", "").replace("/-", "")
    t = t.replace(",", "").replace(" ", "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.endswith("-") and not t.startswith("-"):
        neg, t = True, t[:-1]
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise BadNumber(str(s))
    d = Decimal(t)
    return -d if neg else d


def parse_date(s):
    if s is None:
        return None
    t = " ".join(str(s).strip().upper().split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    raise BadNumber(str(s))


def parse_month(s):
    """'SEP-25', 'Sep 2025', '09/2025' -> (2025, 9). Unparseable -> BadNumber."""
    if s is None:
        return None
    t = str(s).strip().upper().replace("/", "-").replace(" ", "-").replace(".", "-")
    m = re.fullmatch(r"([A-Z]{3})[A-Z]*-?(\d{2}|\d{4})", t)
    if m and m.group(1) in _MONTHS:
        y = int(m.group(2))
        return (2000 + y if y < 100 else y, _MONTHS[m.group(1)])
    m = re.fullmatch(r"(\d{1,2})-(\d{2}|\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        y = int(m.group(2))
        return (2000 + y if y < 100 else y, int(m.group(1)))
    raise BadNumber(str(s))


def _f(d, places=2):
    return str(d.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


def _money(d):
    return _f(d, 2)


class _Report:
    def __init__(self):
        self.findings = []

    def add(self, code, severity, message, fields=(), **details):
        self.findings.append({"code": code, "severity": severity, "message": message,
                              "fields": list(fields), "details": details})


def validate_bill(payload, today=None):
    """payload = sanitised {fields, other_charges, history}."""
    fields = payload["fields"]
    rep = _Report()
    parsed = {}

    # 1. Parse every field once; unparseable values are errors, not silently dropped.
    for key in FIELD_KEYS:
        spec, raw = SPEC_BY_KEY[key], fields[key]["value"]
        if raw is None or spec.kind == "text":
            parsed[key] = None if spec.kind != "text" else raw
            continue
        try:
            if spec.kind == "date":
                parsed[key] = parse_date(raw)
            elif spec.kind == "month":
                parsed[key] = parse_month(raw)
            else:
                parsed[key] = parse_number(raw)
        except BadNumber:
            parsed[key] = None
            rep.add("bad_format", "error", f"“{raw}” in “{spec.label}” isn't a valid {'date' if spec.kind == 'date' else 'month' if spec.kind == 'month' else 'number'}. Please correct it.", [key])

    def known(k):
        return parsed.get(k) is not None

    # 2. Values that cannot be negative
    for k in ("units_consumed", "previous_reading", "present_reading", "sanctioned_load_kw", "current_bill_total"):
        if known(k) and parsed[k] < 0:
            rep.add("negative_value", "error", f"“{SPEC_BY_KEY[k].label}” is negative, which isn't expected.", [k])

    # 3. Meter readings vs units
    prev, pres, units, mf = (parsed.get(k) for k in ("previous_reading", "present_reading", "units_consumed", "multiplying_factor"))
    if prev is not None and pres is not None:
        if pres < prev:
            rep.add("reading_decreased", "warn", "Present reading is lower than previous reading. This can happen after a meter change or rollover; please check both.", ["previous_reading", "present_reading"])
        elif units is not None:
            diff = pres - prev
            expected = diff * (mf if mf is not None else 1)
            if abs(expected - units) <= Decimal("1"):
                rep.add("readings_match_units", "ok", f"Meter readings ({_f(diff, 0 if diff == diff.to_integral() else 2)} difference{' × MF ' + _f(mf, 2).rstrip('0').rstrip('.') if mf is not None else ''}) match the units consumed.", ["previous_reading", "present_reading", "units_consumed"], difference=str(diff))
            elif mf is None:
                rep.add("readings_vs_units", "warn", f"Present − previous reading is {diff}, but units consumed is {units}. A multiplying factor, or a misread number, could explain it. Please review.", ["previous_reading", "present_reading", "units_consumed", "multiplying_factor"], difference=str(diff), units=str(units))
            else:
                rep.add("readings_vs_units", "error", f"({pres} − {prev}) × {mf} = {expected}, but units consumed is {units}. Please review these fields.", ["previous_reading", "present_reading", "multiplying_factor", "units_consumed"], expected=str(expected), units=str(units))
    elif units is not None:
        rep.add("readings_missing", "info", "Meter readings aren't both known, so units consumed can't be cross-checked.", ["previous_reading", "present_reading"])
    if units is not None and units > HIGH_UNITS_NOTE:
        rep.add("very_high_units", "info", f"{units} units is unusually high for a household. Please confirm it was read correctly.", ["units_consumed"])

    # 4. Net charges, then charges -> printed current bill. Layouts differ, so try each way a bill can add up.
    elec, subs, net, taxes = (parsed.get(k) for k in ("energy_charge", "subsidies", "net_electric_charges", "taxes_total"))
    if elec is not None and subs is not None and net is not None:
        if abs(elec - subs - net) <= TOLERANCE:
            rep.add("net_matches", "ok", f"Total electricity charges (Rs {_money(elec)}) minus subsidies (Rs {_money(subs)}) match the net charges (Rs {_money(net)}).", ["net_electric_charges"])
        else:
            rep.add("net_mismatch", "error", f"Total electricity charges (Rs {_money(elec)}) minus subsidies (Rs {_money(subs)}) is Rs {_money(elec - subs)}, but net charges say Rs {_money(net)}. Please review.", ["energy_charge", "subsidies", "net_electric_charges"])
    components, pending = [], []
    for k in CHARGE_COMPONENT_KEYS:
        if known(k):
            components.append((SPEC_BY_KEY[k].label, parsed[k], [k]))
        elif not fields[k]["confirmed"]:
            pending.append(k)
    for i, oc in enumerate(payload.get("other_charges", [])):
        try:
            amt = parse_number(oc["amount"])
        except BadNumber:
            rep.add("bad_format", "error", f"“{oc['amount']}” for “{oc['label']}” isn't a valid number.", [f"other_charges[{i}]"])
            continue
        if amt is not None:
            components.append((oc["label"], amt, [f"other_charges[{i}]"]))
        elif not oc["confirmed"]:
            pending.append(f"other_charges[{i}]")
    total, fpa = parsed.get("current_bill_total"), parsed.get("fuel_price_adjustment")
    cands = []  # (kind, label, sum, field keys, breakdown components)
    if net is not None and taxes is not None:
        cands.append(("aggregate", "net charges + taxes", net + taxes, ["net_electric_charges", "taxes_total"],
                      [("Net electric charges", net, []), ("Taxes", taxes, [])]))
    if len(components) >= 2:
        cands.append(("itemised", "itemised charges", sum(c[1] for c in components), [f for c in components for f in c[2]], components))
        if fpa is not None and len(components) >= 3:
            rest = [c for c in components if c[2] != ["fuel_price_adjustment"]]
            cands.append(("itemised", "itemised charges without FPA", sum(c[1] for c in rest), [f for c in rest for f in c[2]], rest))
    breakdown, month_total, chosen = [], total, None
    if total is None or not cands:
        rep.add("total_unverifiable", "info", "Not enough charge lines are known to compare against the printed current bill.", ["current_bill_total"])
    else:
        hit = next((c for c in cands if abs(total - c[2]) <= TOLERANCE), None)
        if hit:
            extra = f" ({len(pending)} itemised line(s) still unknown)." if pending and hit[0] == "itemised" else "."
            rep.add("total_matches", "ok", f"The {hit[1]} add up to Rs {_money(hit[2])}, matching the printed total of Rs {_money(total)}{extra}", ["current_bill_total"], sum=_money(hit[2]), total=_money(total))
            chosen = hit
        else:
            chosen = cands[0]
            gap = total - chosen[2]
            direction = "more" if gap > 0 else "less"
            pend_labels = [SPEC_BY_KEY[k].label if k in SPEC_BY_KEY else k for k in pending]
            if pending and chosen[0] == "itemised":
                rep.add("total_mismatch_pending", "warn", f"The printed total (Rs {_money(total)}) is Rs {_money(abs(gap))} {direction} than the charges entered (Rs {_money(chosen[2])}). Unfilled lines ({', '.join(pend_labels)}) might account for it; please fill or confirm them.", ["current_bill_total"] + pending, sum=_money(chosen[2]), total=_money(total), gap=_money(gap))
            else:
                rep.add("total_mismatch", "error", f"Discrepancy: the printed total (Rs {_money(total)}) is Rs {_money(abs(gap))} {direction} than the sum of the {chosen[1]} (Rs {_money(chosen[2])}). Please check the total and each charge against your bill.", ["current_bill_total"] + chosen[3], sum=_money(chosen[2]), total=_money(total), gap=_money(gap))
        positive_sum = sum(c[1] for c in chosen[4] if c[1] > 0)
        if positive_sum > 0:
            breakdown = [{"label": c[0], "amount": _money(c[1]), "share_pct": _f(c[1] / positive_sum * 100, 1)} for c in chosen[4] if c[1] > 0]

    # 5. Current bill -> amount payable. Some bills add FPA after the current bill, some include it already.
    arrears, within = parsed.get("arrears"), parsed.get("payable_within_due_date")
    inst, adj = parsed.get("installment"), parsed.get("adjustments")
    if total is not None and arrears is not None and within is not None:
        base = total + arrears + (inst or 0) + (adj or 0)
        with_fpa = base + fpa if fpa is not None else None
        if abs(base - within) <= TOLERANCE:
            rep.add("within_matches", "ok", f"Current bill + arrears{' + installment + adjustments' if inst or adj else ''} = Rs {_money(base)}, matching “Payable within due date”.", ["payable_within_due_date"])
        elif with_fpa is not None and abs(with_fpa - within) <= TOLERANCE:
            month_total = total + fpa
            rep.add("within_matches", "ok", f"Current bill + FPA (Rs {_money(fpa)}) + arrears{' + installment + adjustments' if inst or adj else ''} = Rs {_money(with_fpa)}, matching “Payable within due date”. This bill adds FPA after the current bill.", ["payable_within_due_date", "fuel_price_adjustment"])
        else:
            want = with_fpa if (with_fpa is not None and taxes is not None) else base
            rep.add("within_mismatch", "error", f"Current bill (Rs {_money(total)}) + arrears (Rs {_money(arrears)}){' + FPA' if want is with_fpa and with_fpa is not None else ''} = Rs {_money(want)}, but “Payable within due date” is Rs {_money(within)}. Please review. Some bills include other adjustments.", ["current_bill_total", "arrears", "installment", "adjustments", "fuel_price_adjustment", "payable_within_due_date"])
    elif within is not None and total is not None and arrears is None and not fields["arrears"]["confirmed"]:
        rep.add("arrears_unknown", "info", "Arrears are unknown, so “Payable within due date” can't be cross-checked.", ["arrears"])
    for tier, lps_k, after_k in (("first", "late_payment_surcharge", "payable_after_due_date"), ("second", "late_payment_surcharge_2", "payable_after_due_date_2")):
        lps, after = parsed.get(lps_k), parsed.get(after_k)
        if within is None or after is None:
            continue
        if after < within:
            rep.add("after_less_than_within", "error", f"The {tier}-tier payable amount is lower than “Payable within due date”. Please review.", ["payable_within_due_date", after_k])
        elif lps is not None:
            if abs(within + lps - after) <= TOLERANCE:
                rep.add("lps_matches", "ok", f"Payable within due date + {tier}-tier surcharge matches the {tier}-tier payable amount.", [lps_k, after_k])
            else:
                rep.add("lps_mismatch", "warn", f"Within-due amount (Rs {_money(within)}) + {tier}-tier surcharge (Rs {_money(lps)}) is not the printed amount (Rs {_money(after)}). Please review.", ["payable_within_due_date", lps_k, after_k])

    # 6. Dates
    issue, due, rdate = parsed.get("issue_date"), parsed.get("due_date"), parsed.get("reading_date")
    if issue and due and due < issue:
        rep.add("due_before_issue", "error", "Due date is earlier than the issue date. Please review.", ["issue_date", "due_date"])
    if rdate and issue and rdate > issue:
        rep.add("reading_after_issue", "warn", "Meter reading date is after the issue date. Please review.", ["reading_date", "issue_date"])

    # 7. History
    hist_rows, seen = [], {}
    for i, h in enumerate(payload.get("history", [])):
        try:
            ym = parse_month(h["month"])
            u = parse_number(h["units"])
            b = parse_number(h.get("bill_amount"))
        except BadNumber:
            rep.add("history_unreadable", "warn", f"History row {i + 1} (“{h.get('month')}”) could not be parsed and was left out of trends.", [f"history[{i}]"])
            continue
        if ym is None or u is None:
            continue
        if ym in seen:
            rep.add("history_duplicate", "warn", f"Month {h['month']} appears twice in the printed history.", [f"history[{i}]"])
            continue
        seen[ym] = u
        hist_rows.append({"ym": ym, "month": h["month"], "units": u, "bill": b})
    cur_ym = parsed.get("billing_month")
    trend = None
    if cur_ym and units is not None:
        if cur_ym in seen and seen[cur_ym] != units:
            rep.add("history_vs_current", "warn", f"The history table lists {seen[cur_ym]} units for {fields['billing_month']['value']}, but units consumed is {units}. Please review.", ["units_consumed", "billing_month"])
        prior = [r["units"] for r in hist_rows if r["ym"] != cur_ym]
        if len(prior) >= 2:
            avg = sum(prior) / len(prior)
            if avg > 0:
                trend = {"basis": "printed history", "prior_months": len(prior), "avg_prior_units": _f(avg, 1),
                         "current_units": str(units), "change_pct": _f((units - avg) / avg * 100, 1)}

    comparisons = None
    if cur_ym and units is not None and seen:
        def cmp(ym):
            if ym in seen and seen[ym] > 0:
                return {"month": next(r["month"] for r in hist_rows if r["ym"] == ym), "units": str(seen[ym]),
                        "change_pct": _f((units - seen[ym]) / seen[ym] * 100, 1)}
        prev_ym = (cur_ym[0], cur_ym[1] - 1) if cur_ym[1] > 1 else (cur_ym[0] - 1, 12)
        others = sorted(u for ym, u in seen.items() if ym != cur_ym)
        comparisons = {"vs_previous_month": cmp(prev_ym), "vs_last_year": cmp((cur_ym[0] - 1, cur_ym[1]))}
        if len(others) >= 6:
            mid = len(others) // 2
            median = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2
            if median > 0:
                comparisons["typical_month"] = {"median_units": _f(median, 1), "months": len(others), "times_median": _f(units / median, 2)}
        comparisons = {k: v for k, v in comparisons.items() if v} or None

    derived = {}
    if month_total is not None and units is not None and units > 0:
        derived["all_in_cost_per_unit"] = {"value": _f(month_total / units, 2),
                                           "note": "This month's printed charges (arrears excluded) ÷ units. Includes taxes and fixed charges; not a tariff rate."}

    energy = parsed.get("energy_charge")
    if month_total is not None and energy is not None and month_total > 0:
        other = month_total - energy
        derived["non_energy"] = {"amount": _money(other), "share_pct": _f(other / month_total * 100, 1),
                                 "note": "Printed current-bill total minus the energy cost line (taxes, adjustments, fixed charges, etc.)."}
    if arrears is not None and within is not None and arrears > 0 and within > 0:
        derived["arrears_share"] = {"amount": _money(arrears), "share_pct": _f(arrears / within * 100, 1)}

    # What the amount payable is made of (Python arithmetic; shares are of the positive parts).
    parts = [(c[0], c[1]) for c in (chosen[4] if chosen else [])]
    if fpa is not None and month_total != total and not any(c[2] == ["fuel_price_adjustment"] for c in (chosen[4] if chosen else [])):
        parts.append(("Fuel price adjustment (FPA)", fpa))
    for lab, v in (("Arrears", arrears), ("Installment", inst), ("Adjustments", adj)):
        if v:
            parts.append((lab, v))
    pos = sum(v for _, v in parts if v > 0)
    composition = [{"label": lab, "amount": _money(v), "share_pct": _f(v / pos * 100, 1)} for lab, v in parts if v > 0] if pos > 0 else []

    # Chart-ready series: printed history plus this month, each with bill ÷ units (Python arithmetic).
    series = []
    for r in sorted(hist_rows, key=lambda r: r["ym"]):
        series.append({"month": r["month"], "ym": "%04d-%02d" % r["ym"], "units": str(r["units"]),
                       "bill": None if r["bill"] is None else str(r["bill"]), "is_current": r["ym"] == cur_ym, "from_history": True})
    if cur_ym and units is not None and not any(x["is_current"] for x in series):
        series.append({"month": fields["billing_month"]["value"], "ym": "%04d-%02d" % cur_ym, "units": str(units),
                       "bill": None if month_total is None else _f(month_total, 2), "is_current": True, "from_history": False})
        series.sort(key=lambda x: x["ym"])
    for x in series:
        u, b = Decimal(x["units"]), None if x["bill"] is None else Decimal(x["bill"])
        x["rs_per_unit"] = _f(b / u, 1) if (b is not None and u > 0) else None

    today = today or date.today()
    due_info = None
    if parsed.get("due_date"):
        dd = parsed["due_date"]
        due_info = {"date": fields["due_date"]["value"], "days_left": (dd - today).days}
    ladder = []
    if within is not None:
        ladder.append({"label": "Pay by the due date", "date": fields["due_date"]["value"], "amount": _money(within), "extra": None})
        for lps_k, after_k in (("late_payment_surcharge", "payable_after_due_date"), ("late_payment_surcharge_2", "payable_after_due_date_2")):
            if parsed.get(after_k) is not None:
                ladder.append({"label": "After the due date" if not ladder[1:] else "Later still", "date": fields["surcharge_date"]["value"] if not ladder[1:] else None,
                               "amount": _money(parsed[after_k]), "extra": None if parsed.get(lps_k) is None else _money(parsed[lps_k])})

    has_error = any(f["severity"] == "error" for f in rep.findings)
    has_warn = any(f["severity"] == "warn" for f in rep.findings)
    statuses = {k: field_status(fields[k]) for k in FIELD_KEYS}
    unconfirmed = [k for k, s in statuses.items() if s in ("unconfirmed", "unconfirmed_unknown")]
    order = {"error": 0, "warn": 1, "info": 2, "ok": 3}
    rep.findings.sort(key=lambda f: order[f["severity"]])
    return {
        "findings": rep.findings,
        "status": "discrepancies" if has_error else "review" if has_warn else "consistent",
        "counts": {s: sum(1 for f in rep.findings if f["severity"] == s) for s in order},
        "breakdown": breakdown,
        "derived": derived,
        "trend": trend,
        "comparisons": comparisons,
        "composition": composition,
        "series": series,
        "due": due_info,
        "ladder": ladder,
        "history": [{"month": r["month"], "units": str(r["units"]), "bill": None if r["bill"] is None else str(r["bill"]),
                     "is_current": r["ym"] == cur_ym} for r in sorted(hist_rows, key=lambda r: r["ym"])],
        "field_status": statuses,
        "unconfirmed_fields": unconfirmed,
        "all_confirmed": not unconfirmed,
        "notes": ["Protected-consumer status is not determined by this app. It depends on your consumption history and other criteria, not on one month's units."],
    }
