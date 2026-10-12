"""Appliance questions and the 'possible factors' engine.

Rules:
- Appliance answers are SELF-REPORTED. They can make a factor "possible", never "supported".
- "supported" means the printed bill figures themselves show it.
- If units were not higher than earlier months, appliance factors are "unlikely" for this month's rise.
- No kWh estimates here (those arrive with the what-if simulator, clearly labelled as estimates).
"""
from .validator import BadNumber, parse_month

UP_PCT, DOWN_PCT = 10.0, -10.0
SUMMER_MONTHS = {5, 6, 7, 8, 9}

QUESTIONS = [
    {"id": "ac_count", "type": "single", "text": "How many air conditioners do you run?",
     "options": [{"id": "none", "label": "None"}, {"id": "one", "label": "One"}, {"id": "two_plus", "label": "Two or more"}]},
    {"id": "ac_hours", "type": "single", "text": "On a typical day, how long does the AC run?", "show_if": {"ac_count": ["one", "two_plus"]},
     "options": [{"id": "lt4", "label": "Under 4 hours"}, {"id": "4to8", "label": "4 to 8 hours"}, {"id": "gt8", "label": "More than 8 hours"}]},
    {"id": "heavy", "type": "multi", "text": "Which of these do you use regularly?",
     "options": [{"id": "geyser", "label": "Electric geyser / water heater"}, {"id": "pump", "label": "Water pump / motor"},
                 {"id": "heater", "label": "Electric room heater"}, {"id": "freezer", "label": "Deep freezer"}]},
    {"id": "changes", "type": "multi", "text": "Did anything change this month?",
     "options": [{"id": "more_people", "label": "More people or guests at home"}, {"id": "new_appliance", "label": "A new appliance"},
                 {"id": "less_home", "label": "The house was empty some days"}]},
]
_BY_ID = {q["id"]: q for q in QUESTIONS}
LABELS = {o["id"]: o["label"] for q in QUESTIONS for o in q["options"]}


def clean_answers(raw):
    """Keep only known question/option ids. Unanswered stays absent (unknown), never defaulted."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for q in QUESTIONS:
        v, ok = raw.get(q["id"]), {o["id"] for o in q["options"]}
        if q["type"] == "single" and isinstance(v, str) and v in ok:
            out[q["id"]] = v
        elif q["type"] == "multi" and isinstance(v, list):
            out[q["id"]] = sorted({x for x in v if isinstance(x, str) and x in ok})
    if out.get("ac_count") in (None, "none"):
        out.pop("ac_hours", None)
    return out


HIGH_TIMES_MEDIAN = 1.5


def _units_state(trend, comps):
    """Direction of change. Prefers last month (printed) over the all-months average, which is distorted by seasons."""
    prev = (comps or {}).get("vs_previous_month")
    if prev:
        c, ref = float(prev["change_pct"]), f"last month's {prev['units']} units ({prev['month']})"
    elif trend:
        c, ref = float(trend["change_pct"]), f"the average of {trend['prior_months']} earlier months ({trend['avg_prior_units']} units)"
    else:
        return "unknown", None, None
    return ("up" if c >= UP_PCT else "down" if c <= DOWN_PCT else "similar"), c, ref


def _factor(fid, title, level, text, evidence):
    return {"id": fid, "title": title, "level": level, "text": text, "evidence": evidence}


def evaluate(payload, validation, answers):
    answers = clean_answers(answers)
    trend, derived = validation["trend"], validation["derived"]
    F = payload["fields"]
    comps = validation.get("comparisons")
    state, change, ref = _units_state(trend, comps)
    typical = (comps or {}).get("typical_month")
    high_level = bool(typical and float(typical["times_median"]) >= HIGH_TIMES_MEDIAN)
    cur_units = F["units_consumed"]["value"]
    F = payload["fields"]
    try:
        ym = parse_month(F["billing_month"]["value"])
    except BadNumber:
        ym = None
    summer = bool(ym and ym[1] in SUMMER_MONTHS)
    out = []

    # ---- supported by the printed bill
    if state == "up":
        out.append(_factor("units_up", "Your units are higher than usual", "supported",
            f"This month's {cur_units} units is {abs(change)}% above {ref}.",
            [{"kind": "measured", "text": "Units and history are printed on your bill."}, {"kind": "derived", "text": "Percentage calculated by Python."}]))
    elif state in ("similar", "down"):
        out.append(_factor("units_not_up", "Usage did not jump compared with before", "supported",
            f"This month's {cur_units} units is {abs(change)}% {'below' if change < 0 else 'above'} {ref}, so usage did not jump compared with then. If the bill feels high, see the points below.",
            [{"kind": "measured", "text": "Units and history are printed on your bill."}, {"kind": "derived", "text": "Percentage calculated by Python."}]))
    else:
        out.append(_factor("units_unknown", "Not enough history to compare units", "info",
            "At least two earlier months of units are needed on the bill to tell whether this month is unusual.", []))
    yoy = (comps or {}).get("vs_last_year")
    if yoy:
        c = float(yoy["change_pct"])
        out.append(_factor("year_on_year", "Compared with the same month last year", "supported",
            f"Last year ({yoy['month']}) you used {yoy['units']} units; this month {cur_units} units ({abs(c)}% {'more' if c >= 0 else 'less'}).",
            [{"kind": "measured", "text": "Both months are printed in your bill history."}, {"kind": "derived", "text": "Percentage calculated by Python."}]))
    if high_level:
        out.append(_factor("high_level", "This is a high-usage month for you", "supported",
            f"This month's {cur_units} units is {typical['times_median']} times your typical month ({typical['median_units']} units, the middle value of {typical['months']} months on your bill). A big part of the bill is simply the amount of electricity used.",
            [{"kind": "measured", "text": "History printed on your bill."}, {"kind": "derived", "text": "Median and ratio calculated by Python."}]))
    ne = derived.get("non_energy")
    if ne:
        out.append(_factor("non_energy", "Much of the bill is not the energy cost line", "supported",
            f"Rs {ne['amount']} ({ne['share_pct']}%) of the current bill is taxes, adjustments or other lines apart from the energy cost.",
            [{"kind": "bill_printed", "text": "Charge lines as printed."}, {"kind": "derived", "text": "Share calculated by Python."}]))
    ar = derived.get("arrears_share")
    if ar:
        out.append(_factor("arrears", "Your payable amount includes arrears", "supported",
            f"Arrears of Rs {ar['amount']} make up {ar['share_pct']}% of the amount payable within the due date.",
            [{"kind": "bill_printed", "text": "Arrears and payable amount as printed."}, {"kind": "derived", "text": "Share calculated by Python."}]))

    # ---- appliance factors: self-reported, never 'supported'
    selfrep = lambda t: {"kind": "self_reported", "text": t}
    ac = answers.get("ac_count")
    if ac in ("one", "two_plus"):
        hours = {"lt4": "under 4 hours", "4to8": "4 to 8 hours", "gt8": "more than 8 hours"}.get(answers.get("ac_hours"), "an unknown number of hours")
        who = "an air conditioner" if ac == "one" else "two or more air conditioners"
        ev = [selfrep(f"You said you run {who} for {hours} a day. Not measured.")]
        if state == "up":
            out.append(_factor("cooling", "Cooling may be part of the rise", "possible",
                "Your units rose, and you run air conditioning. It could be a contributor, but this app has no measurement of the AC, so it cannot confirm it.", ev))
        elif high_level:
            out.append(_factor("cooling", "Cooling may be part of the high usage", "possible",
                "Your usage is well above your typical month and you run air conditioning. It could be a contributor, but this app has no measurement of the AC, so it cannot confirm it.", ev))
        elif state in ("similar", "down"):
            out.append(_factor("cooling", "Cooling does not explain a higher bill this month", "unlikely",
                "Your units are not higher than earlier months, so a change in AC use is unlikely to be behind this month's bill.", ev))
        elif summer and answers.get("ac_hours") in ("4to8", "gt8"):
            out.append(_factor("cooling", "Cooling could matter in hot months", "possible",
                "This is a hot-season month and you run the AC for several hours. Without earlier months on the bill we cannot see whether your units actually rose.",
                ev + [{"kind": "context", "text": "General seasonal context, not specific to your home."}]))
    heavy = answers.get("heavy") or []
    if heavy:
        names = ", ".join(LABELS[h].lower() for h in heavy)
        ev = [selfrep(f"You said you use: {names}. Not measured.")]
        if state == "up" or high_level:
            out.append(_factor("heavy", "Other heavy appliances could contribute", "possible",
                "Your usage is high or rising and you use appliances that draw a lot of power. They may be a contributor; this app cannot tell.", ev))
        elif state in ("similar", "down"):
            out.append(_factor("heavy", "Heavy appliances do not explain a rise this month", "unlikely",
                "Your units are not higher than earlier months.", ev))
    ch = answers.get("changes") or []
    if state == "up" and ("more_people" in ch or "new_appliance" in ch):
        what = " and ".join(LABELS[c].lower() for c in ch if c in ("more_people", "new_appliance"))
        out.append(_factor("household_change", "A change at home may explain higher use", "possible",
            "Your units rose and you reported: " + what + ". That is a plausible reason, but it is not proven by the bill.", [selfrep("You reported this change.")]))
    if state == "up" and "less_home" in ch:
        out.append(_factor("empty_home", "Units rose although the house was empty some days", "possible",
            "That is unusual. It is worth checking for appliances left running and re-checking the meter readings on your bill. This app cannot tell which.", [selfrep("You reported empty days.")]))
    if state == "up" and not (ac in ("one", "two_plus") or heavy or "more_people" in ch or "new_appliance" in ch):
        out.append(_factor("cause_unknown", "The bill does not show why units rose", "info",
            "None of your answers point to a clear reason. A bill alone cannot identify the cause.", []))
    order = {"supported": 0, "possible": 1, "unlikely": 2, "info": 3}
    out.sort(key=lambda f: order[f["level"]])
    return {"factors": out, "answers": answers, "units_state": state}
