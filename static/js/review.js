(function () {
  const { h, store, statusChip, findingItem, postJSON, blankExtraction, KEY_EXTRACTION, KEY_RESULT } = BS;
  const specs = JSON.parse(document.getElementById("specs").textContent);
  const saved = store.get(KEY_EXTRACTION);
  if (!saved) { window.location.href = "/"; return; }
  const ex = saved.extraction || blankExtraction(specs);
  const state = { fields: ex.fields, other_charges: ex.other_charges || [], history: ex.history || [] };
  const items = [];      // every confirmable item, for the progress bar
  let validation = null, timer = null;

  if (saved.synthetic) document.getElementById("synthetic-banner").classList.remove("hidden");
  const notes = document.getElementById("notes");
  (saved.warnings || []).forEach((w) => notes.append(h("div", { class: "callout", text: w })));
  if (saved.manual) notes.append(h("div", { class: "tint small", text: "Manual entry: fill in what you can see on your bill and mark the rest “Not on bill”." }));
  if (saved.covered_areas) notes.append(h("div", { class: "tint small", text: `${saved.covered_areas} area${saved.covered_areas > 1 ? "s were" : " was"} covered before upload.` }));
  if (ex.utility_seen && !/fesco/i.test(ex.utility_seen) && !saved.synthetic)
    notes.append(h("div", { class: "callout", text: `This bill looks like it is from “${ex.utility_seen}”, not FESCO. Layout and charges may differ, so check carefully.` }));

  const statusOf = (item, key) => {
    if (!item.confirmed) return item[key] == null ? "unconfirmed_unknown" : "unconfirmed";
    if (item[key] == null) return "confirmed_unknown";
    return item[key] === item.extracted_value ? "confirmed_extracted" : "user_corrected";
  };

  function buildRow(id, label, hint, item, key) {
    const input = h("input", { type: "text", inputmode: "decimal", id: "f-" + id, autocomplete: "off", value: item[key] ?? "", placeholder: "not read" });
    const chipSlot = h("span");
    const box = h("input", { type: "checkbox", id: "c-" + id }); box.checked = !!item.confirmed;
    const wrap = h("div", { class: "field", id: "row-" + id });
    const refresh = () => { item.confirmed = box.checked; chipSlot.replaceChildren(statusChip(statusOf(item, key))); progress(); };
    input.addEventListener("input", () => { const v = input.value.trim(); item[key] = v === "" ? null : v; box.checked = true; refresh(); schedule(); });
    box.addEventListener("change", () => { refresh(); schedule(); });
    wrap.append(
      h("div", { class: "row between" }, h("label", { for: "f-" + id, class: "lab", text: label }), chipSlot),
      input,
      item.source_text ? h("div", { class: "src" }, "Printed as ", h("code", { text: item.source_text })) : null,
      hint ? h("div", { class: "src", text: hint }) : null,
      h("div", { class: "acts" }, h("label", {}, box, "Confirmed"),
        h("button", { type: "button", class: "link", text: "Not on bill", onclick: () => { item[key] = null; input.value = ""; box.checked = true; refresh(); schedule(); } })));
    items.push({ item, key });
    refresh();
    return wrap;
  }

  function progress() {
    const total = items.length || 1, done = items.filter(({ item }) => item.confirmed).length;
    document.getElementById("progress-bar").style.width = (done / total * 100) + "%";
    document.getElementById("progress-text").textContent = `${done} of ${items.length} confirmed`;
  }

  const form = document.getElementById("form");
  for (const sec of [...new Set(specs.map((s) => s.section))]) {
    const secSpecs = specs.filter((s) => s.section === sec), optional = secSpecs.every((s) => s.optional);
    const rows = secSpecs.map((s) => buildRow(s.key, s.label, s.hint, state.fields[s.key], "value"));
    if (sec === "Charges") state.other_charges.forEach((oc, i) => rows.push(buildRow(`other_charges[${i}]`, oc.label, "Extra line found on the bill", oc, "amount")));
    form.append(optional
      ? h("details", { class: "sec", id: "sec-optional", open: secSpecs.some((s) => state.fields[s.key].value != null) }, h("summary", { text: sec }), rows)
      : h("section", { class: "sec" }, h("h2", { text: sec }), rows));
  }
  if (state.history.length) {
    form.append(h("section", { class: "panel" }, h("h2", { style: "font-size:1.1rem", text: "Past months on your bill" }),
      h("p", { class: "sub", text: "Read from the bill's history table. Used for the charts." }),
      h("table", { class: "datatable" }, h("tbody", {}, state.history.map((r) => h("tr", {}, h("td", { text: r.month || "?" }), h("td", { text: (r.units ?? "?") + " units" }), h("td", { text: r.bill_amount ? "Rs " + r.bill_amount : "" })))))));
  }

  const payload = () => ({ fields: state.fields, other_charges: state.other_charges, history: state.history });

  function renderFindings() {
    const ul = document.getElementById("findings"); ul.replaceChildren();
    document.querySelectorAll(".field").forEach((r) => r.classList.remove("flag-error", "flag-warn"));
    const pill = document.getElementById("checks-pill");
    if (!validation) return;
    const c = validation.counts, issues = c.error + c.warn;
    pill.className = "chip plain " + (issues ? "pink" : "ok"); pill.textContent = issues ? `${issues} to review` : `${c.ok} passed`;
    if (!validation.findings.length) ul.append(h("li", { class: "small muted", text: "Fill in more fields and checks will appear here." }));
    validation.findings.forEach((f) => {
      ul.append(findingItem(f));
      const flag = f.severity === "error" ? "flag-error" : f.severity === "warn" ? "flag-warn" : null;
      if (flag) f.fields.forEach((k) => { const row = document.getElementById("row-" + k); if (row) { row.classList.add(flag); const d = row.closest("details"); if (d) d.open = true; } });
    });
  }
  async function runChecks() {
    try { validation = (await postJSON("/api/validate", payload())).validation; renderFindings(); }
    catch (e) { document.getElementById("findings").replaceChildren(h("li", { class: "small", style: "color:var(--bad-ink)", text: e.message })); }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(runChecks, 350); }

  document.getElementById("confirm-all").addEventListener("click", () => {
    document.querySelectorAll("input[type=checkbox][id^=c-]").forEach((cb) => { if (!cb.checked) { cb.checked = true; cb.dispatchEvent(new Event("change")); } });
  });
  document.getElementById("continue").addEventListener("click", async () => {
    const msg = document.getElementById("continue-msg"); msg.classList.add("hidden");
    await runChecks();
    if (!validation) return;
    if (!validation.all_confirmed) {
      const first = validation.unconfirmed_fields[0];
      msg.textContent = `${validation.unconfirmed_fields.length} field(s) still need your confirmation. Tick “Confirmed”, or press “Confirm all” if everything matches your bill.`;
      msg.classList.remove("hidden");
      const row = document.getElementById("row-" + first);
      if (row) { const d = row.closest("details"); if (d) d.open = true; row.scrollIntoView({ block: "center" }); row.classList.add("flash"); }
      return;
    }
    store.set(KEY_RESULT, { payload: payload(), validation, synthetic: !!saved.synthetic });
    window.location.href = "/results";
  });
  runChecks();
})();
