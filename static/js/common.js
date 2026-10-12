// Shared helpers. Data never goes through innerHTML: everything is built with textContent.
window.BS = (function () {
  const KEY_EXTRACTION = "bs_extraction"; // {extraction, synthetic, ...}
  const KEY_RESULT = "bs_result";         // {payload, validation, synthetic}

  const SVGNS = "http://www.w3.org/2000/svg";
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) el.append(c);
    return el;
  }
  function svg(tag, attrs, ...children) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v);
    for (const c of children.flat()) if (c != null) el.append(c);
    return el;
  }
  const store = {
    get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } },
    clear() { try { sessionStorage.removeItem(KEY_EXTRACTION); sessionStorage.removeItem(KEY_RESULT); sessionStorage.removeItem("bs_answers"); } catch (e) {} },
  };
  const STATUS = {
    confirmed_extracted: ["Confirmed", "ok"], user_corrected: ["Corrected", "edit"], confirmed_unknown: ["Not on bill", "none"],
    unconfirmed: ["Check this", "todo"], unconfirmed_unknown: ["Not read", "todo"],
  };
  function statusChip(status) {
    const [label, cls] = STATUS[status] || STATUS.unconfirmed;
    return h("span", { class: "chip " + cls, text: label });
  }
  function findingItem(f) {
    return h("li", { class: "finding " + f.severity }, h("span", { class: "dot", "aria-hidden": "true" }), h("span", { text: f.message }));
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => null);
    if (!r.ok || !j || !j.ok) throw new Error((j && j.error && j.error.message) || "Request failed. Please try again.");
    return j;
  }
  function blankExtraction(specs) {
    const fields = {};
    for (const s of specs) fields[s.key] = { value: null, source_text: null, confirmed: false, extracted_value: null };
    return { is_electricity_bill: true, legibility: "good", utility_seen: null, fields, other_charges: [], history: [], notes: null };
  }
  const fmt = (x, d = 0) => Number(x).toLocaleString("en-PK", { minimumFractionDigits: d, maximumFractionDigits: d });
  return { h, svg, store, statusChip, findingItem, postJSON, blankExtraction, fmt, KEY_EXTRACTION, KEY_RESULT };
})();
