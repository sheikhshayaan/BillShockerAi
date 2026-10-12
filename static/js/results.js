(function () {
  const { h, store, findingItem, postJSON, fmt, KEY_RESULT } = BS;
  const data = store.get(KEY_RESULT);
  if (!data) { window.location.href = "/"; return; }
  const { payload, validation: v } = data;
  const F = payload.fields, body = document.getElementById("body");
  if (data.synthetic) document.getElementById("synthetic-banner").classList.remove("hidden");
  const num = (x) => parseFloat(x);
  const cm = v.comparisons || {};
  const panel = (title, sub, ...kids) => h("section", { class: "panel" }, h("h2", { text: title }), sub ? h("p", { class: "sub", text: sub }) : null, ...kids);
  const pct = (s) => `${Math.abs(num(s))}% ${num(s) >= 0 ? "more" : "less"}`;

  // ---------------------------------------------------------------- unconfirmed / discrepancy notices
  if (!v.all_confirmed) body.append(h("div", { class: "callout", text: `${v.unconfirmed_fields.length} field(s) were not confirmed. Go back to edit to confirm them.` }));
  if (v.status === "discrepancies") body.append(h("div", { class: "callout bad" }, h("b", { text: "These numbers don't agree. " }), "Please re-check the highlighted items below against your paper bill. Anything built on them may be wrong."));
  else if (v.status === "review") body.append(h("div", { class: "callout" }, h("b", { text: "A few checks need a second look. " }), "See “Checks” at the bottom."));

  // ---------------------------------------------------------------- hero: what to pay and when
  const due = v.ladder[0] ? v.ladder[0].amount : (F.current_bill_total.value || null);
  const hero = h("section", { class: "hero" }, h("div", { class: "glow", "aria-hidden": "true" }),
    h("div", { class: "k", text: F.billing_month.value ? `Amount due for ${F.billing_month.value}` : "Amount due" }),
    h("div", { class: "amt" }, h("small", { text: "Rs" }), due == null ? "Unknown" : fmt(due)));
  if (v.due) {
    const d = v.due.days_left, cls = d < 0 ? "late" : d <= 3 ? "soon" : "";
    hero.append(h("div", { class: "due " + cls, text: d < 0 ? `Overdue by ${-d} day${d === -1 ? "" : "s"}` : d === 0 ? "Due today" : `Due in ${d} day${d === 1 ? "" : "s"}, ${v.due.date}` }));
  }
  if (v.ladder.length > 1) hero.append(h("div", { class: "ladder", role: "list", "aria-label": "What you pay if you pay late" }, v.ladder.map((s) =>
    h("div", { class: "step", role: "listitem" }, h("span", { class: "pip", "aria-hidden": "true" }),
      h("span", {}, s.label, s.date ? h("div", { class: "tiny", style: "color:var(--hero-2)", text: String(s.date).replace(/-/g, " ") }) : null),
      h("span", { style: "text-align:right" }, h("div", { class: "v", text: "Rs " + fmt(s.amount) }), s.extra ? h("div", { class: "e", text: "+ Rs " + fmt(s.extra) + " surcharge" }) : null)))));
  body.append(hero);

  // ---------------------------------------------------------------- is this a high month?
  const tm = cm.typical_month, prev = cm.vs_previous_month, yoy = cm.vs_last_year;
  if (tm || prev || yoy) {
    const kids = [];
    if (tm) {
      const x = num(tm.times_median), S = Math.max(2, Math.ceil(x * 1.25 * 2) / 2), posMid = (1 / S) * 100, posYou = Math.min(97, (x / S) * 100);
      kids.push(h("div", { class: "big", text: `${x}× your typical month`, style: "" }),
        h("div", { class: "meter", role: "img", "aria-label": `This month is ${x} times your typical month` },
          h("div", { class: "fill", style: `width:${posYou}%` }), h("div", { class: "mid", style: `left:${posMid}%` }, h("span", { text: `typical ${tm.median_units}` })),
          h("div", { class: "you", style: `left:${posYou}%` }, h("span", { text: `${fmt(F.units_consumed.value)} units` }))));
    }
    const lines = [];
    if (prev) lines.push(["Last month", `${fmt(prev.units)} units`, pct(prev.change_pct)]);
    if (yoy) lines.push(["Same month last year", `${fmt(yoy.units)} units`, pct(yoy.change_pct)]);
    if (lines.length) kids.push(h("div", { class: tm ? "twocol" : "", style: "margin-top:1rem;display:grid;gap:.5rem" }, lines.map(([k, a, b]) =>
      h("div", { class: "tint" }, h("div", { class: "small muted", text: k }), h("div", { class: "strong", text: a }), h("div", { class: "small", text: `this month is ${b}` })))));
    body.append(panel(tm && num(tm.times_median) >= 1.5 ? "This is a high-usage month for you" : "How this month compares", "Measured units from your bill's history, compared with this month.", ...kids));
  }

  // ---------------------------------------------------------------- KPI row
  const kp = [];
  kp.push(["Units used", fmt(F.units_consumed.value), "kWh", prev ? `${num(prev.change_pct) >= 0 ? "▲" : "▼"} ${Math.abs(num(prev.change_pct))}% vs last month` : null, prev && num(prev.change_pct) >= 0 ? "var(--bad-ink)" : "var(--ink-2)"]);
  if (v.derived.all_in_cost_per_unit) kp.push(["All-in cost per unit", "Rs " + fmt(v.derived.all_in_cost_per_unit.value, 1), "", "Bill ÷ units. Not a tariff.", "var(--muted)"]);
  if (v.derived.non_energy) kp.push(["Taxes and add-ons", fmt(v.derived.non_energy.share_pct, 1), "%", "of this month's charges", "var(--muted)"]);
  body.append(h("div", { class: "kpis" }, kp.map(([l, n, u, d, c]) => h("div", { class: "kpi" }, h("div", { class: "n" }, n, u ? h("small", { text: u }) : null), h("div", { class: "l", text: l }), d ? h("div", { class: "delta", style: `color:${c}`, text: d }) : null))));

  // ---------------------------------------------------------------- units per month
  if (v.series.length >= 3) {
    const host = h("div");
    body.append(panel("Units, month by month", tm ? "The dashed line is your typical month." : "Measured units from your bill history.", host));
    BSCharts.columns(host, { unit: "units", aria: "Units used each month", rows: v.series.map((s) => ({ label: s.month, value: num(s.units), hi: s.is_current, bill: s.bill == null ? null : num(s.bill) })), ref: tm ? { value: num(tm.median_units), label: "typical" } : null });
  }

  // ---------------------------------------------------------------- units vs bill
  const withBill = v.series.filter((s) => s.bill != null);
  if (withBill.length >= 4) {
    const host = h("div"), rp = withBill.filter((s) => s.rs_per_unit != null);
    const lo = rp.reduce((a, b) => (num(b.rs_per_unit) < num(a.rs_per_unit) ? b : a)), hi = rp.reduce((a, b) => (num(b.rs_per_unit) > num(a.rs_per_unit) ? b : a));
    body.append(panel("Units against bill", "Each dot is a month from your bill. The large dot is this month.", host,
      h("p", { class: "small", style: "margin-top:.6rem", text: `Cheapest month per unit: ${lo.month} at Rs ${lo.rs_per_unit}. Most expensive: ${hi.month} at Rs ${hi.rs_per_unit}. (Bill ÷ units, from the printed figures.)` })));
    BSCharts.scatter(host, { xLabel: "Units used", aria: "Units against bill for each month", points: withBill.map((s) => ({ x: num(s.units), y: num(s.bill), label: s.month, hi: s.is_current, rpu: s.rs_per_unit })) });
  }

  // ---------------------------------------------------------------- where the money goes
  if (v.composition.length) {
    const colour = (l) => /fuel|fpa/i.test(l) ? "var(--s3)" : /arrears|installment|adjust/i.test(l) ? (/arrears/i.test(l) ? "var(--s4)" : "var(--ctx)") : /fuel|fpa/i.test(l) ? "var(--s3)" : /tax|gst|duty|tv|ptv/i.test(l) ? "var(--s2)" : /electric|energy|net/i.test(l) ? "var(--s1)" : "var(--ctx)";
    body.append(panel("Where the money goes", "What makes up the amount you pay, from the figures on your bill.",
      h("div", { class: "stackbar", role: "img", "aria-label": v.composition.map((c) => `${c.label} ${c.share_pct}%`).join(", ") }, v.composition.map((c) => h("div", { style: `flex:${num(c.share_pct)};background:${colour(c.label)}`, title: `${c.label}: ${c.share_pct}%` }))),
      h("ul", { class: "stacklist" }, v.composition.map((c) => h("li", {}, h("i", { style: `background:${colour(c.label)}`, "aria-hidden": "true" }), h("span", { text: c.label }), h("span", { class: "strong", text: "Rs " + fmt(c.amount) }), h("span", { class: "pct", text: c.share_pct + "%" }))))));
  }

  // ---------------------------------------------------------------- explanation (EN / UR)
  (function explainCard() {
    const out = h("div", { class: "explain", style: "margin-top:1rem", "aria-live": "polite" });
    const btns = [];
    const mk = (lang, label) => { const b = h("button", { type: "button", class: "btn btn-ghost", style: "flex:1", text: label, onclick: () => run(lang) }); btns.push(b); return b; };
    async function run(lang) {
      btns.forEach((b) => (b.disabled = true));
      out.replaceChildren(h("p", { class: "small muted", text: lang === "ur" ? "Writing in Urdu…" : "Writing…" }));
      try {
        const j = await postJSON("/api/explain", { payload, lang }), e = j.explanation, rtl = j.lang === "ur";
        out.replaceChildren(h("div", { class: rtl ? "urdu rtl" : "", dir: rtl ? "rtl" : "ltr", lang: j.lang },
          h("h3", { text: e.headline }), e.summary ? h("p", { text: e.summary }) : null,
          h("ul", {}, e.key_points.map((t) => h("li", { text: t }))),
          (e.things_to_check || []).length ? h("div", { class: "callout", style: "margin-top:.7rem" }, e.things_to_check.map((t) => h("p", { text: t }))) : null),
          h("p", { class: "tiny muted", style: "margin-top:.7rem", text: (j.source === "ai" ? "Worded by AI. " : "Written from your figures. ") + j.note }));
      } catch (err) { out.replaceChildren(h("p", { class: "small", style: "color:var(--bad-ink)", text: err.message })); }
      btns.forEach((b) => (b.disabled = false));
    }
    body.append(panel("Explain it in words", "A plain-language summary of your confirmed figures. It describes what the numbers show, not why they changed.",
      h("div", { class: "row", style: "margin-top:.9rem" }, mk("en", "In English"), mk("ur", "اردو میں")), out));
  })();

  // ---------------------------------------------------------------- questions + factors
  (function questionsCard() {
    const QUESTIONS = JSON.parse(document.getElementById("questions").textContent), ANS = "bs_answers";
    const answers = store.get(ANS) || {}, form = h("div", { class: "stack" }), result = h("div", { class: "stack", style: "margin-top:1.1rem", "aria-live": "polite" });
    const LEVEL = { supported: "Your bill shows this", possible: "Possible, not proven", unlikely: "Unlikely", info: "Note" };
    const KIND = { measured: "Printed on bill", bill_printed: "Printed on bill", derived: "Calculated", self_reported: "You told us", context: "General context" };
    function renderQs() {
      form.replaceChildren(...QUESTIONS.filter((q) => !q.show_if || Object.entries(q.show_if).every(([k, vals]) => vals.includes(answers[k]))).map((q) =>
        h("fieldset", { style: "border:0;padding:0;min-width:0" }, h("legend", { class: "strong small", style: "margin-bottom:.6rem;padding:0", text: q.text }),
          h("div", { class: "btnrow" }, q.options.map((o) => {
            const on = q.type === "single" ? answers[q.id] === o.id : (answers[q.id] || []).includes(o.id);
            return h("button", { type: "button", class: "pill", "aria-pressed": on ? "true" : "false", text: o.label, onclick: () => {
              if (q.type === "single") { answers[q.id] = on ? undefined : o.id; if (q.id === "ac_count") delete answers.ac_hours; }
              else { const set = new Set(answers[q.id] || []); on ? set.delete(o.id) : set.add(o.id); answers[q.id] = [...set]; }
              store.set(ANS, answers); renderQs();
            } });
          })))));
    }
    renderQs();
    async function show() {
      result.replaceChildren(h("p", { class: "small muted", text: "Checking…" }));
      try {
        const j = await postJSON("/api/factors", { payload, answers });
        result.replaceChildren(...j.factors.map((f) => h("div", { class: "factor " + f.level }, h("div", { class: "bar", "aria-hidden": "true" }),
          h("div", {}, h("div", { class: "row between", style: "align-items:flex-start" }, h("h3", { text: f.title }), h("span", { class: "chip plain", style: "background:var(--surface);", text: LEVEL[f.level] })),
            h("p", { class: "small", style: "margin-top:.3rem", text: f.text }),
            f.evidence.length ? h("div", { class: "ev" }, f.evidence.map((e) => h("span", { title: e.text, text: KIND[e.kind] }))) : null))),
          h("p", { class: "tiny muted", text: "Appliance answers are what you told us, not measurements. Only items marked “Your bill shows this” come from the bill's own figures." }));
      } catch (err) { result.replaceChildren(h("p", { class: "small", style: "color:var(--bad-ink)", text: err.message })); }
    }
    body.append(panel("What might be behind it?", "Four quick questions. Skip any you like.", h("div", { style: "margin-top:1rem" }, form), h("button", { type: "button", class: "btn btn-primary btn-block", style: "margin-top:1.1rem", text: "Show what the evidence says", onclick: show }), result));
  })();

  // ---------------------------------------------------------------- checks
  body.append(h("details", { class: "panel" }, h("summary", { class: "row between" }, h("h2", { text: "Checks" }), h("span", { class: "chip plain " + (v.counts.error + v.counts.warn ? "pink" : "ok"), text: v.counts.error + v.counts.warn ? `${v.counts.error + v.counts.warn} to review` : `${v.counts.ok} passed` })),
    h("ul", { class: "findings" }, v.findings.map(findingItem)),
    h("ul", { class: "small muted", style: "margin:.8rem 0 0;padding-left:1.1rem" }, v.notes.map((n) => h("li", { text: n })))));

  document.getElementById("clear").addEventListener("click", () => { store.clear(); window.location.href = "/"; });
})();
