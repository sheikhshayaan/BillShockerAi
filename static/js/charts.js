// Small dependency-free SVG charts. Redrawn to the container's real width so text stays a readable size.
// Every chart: hover/focus tooltip, keyboard-focusable marks, and a "View as table" alternative.
window.BSCharts = (function () {
  const { h, svg, fmt } = BS;
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  const short = (n) => (n >= 1000 ? fmt(n / 1000, n % 1000 ? 1 : 0) + "k" : fmt(n));
  function monthShort(label) { const m = String(label || "").trim().slice(0, 3); return m ? m[0].toUpperCase() + m.slice(1).toLowerCase() : "?"; }
  function yearShort(label) { const m = String(label || "").match(/(\d{2,4})\s*$/); return m ? "’" + m[1].slice(-2) : ""; }
  function roundedTop(x, y, w, hgt, r) {
    r = Math.min(r, w / 2, hgt);
    return `M${x},${y + hgt} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + hgt} Z`;
  }

  // shared tooltip + frame (chart area / table toggle)
  function frame(host, draw, tableBuilder) {
    host.replaceChildren();
    const box = h("div", { class: "chartbox" });
    const tip = h("div", { class: "tip", role: "status" });
    const tableWrap = h("div", { class: "hidden" }, tableBuilder());
    const toggle = h("button", { type: "button", class: "link", text: "View as table", "aria-expanded": "false" });
    toggle.addEventListener("click", () => {
      const show = tableWrap.classList.contains("hidden");
      tableWrap.classList.toggle("hidden", !show); box.classList.toggle("hidden", show);
      toggle.textContent = show ? "View as chart" : "View as table"; toggle.setAttribute("aria-expanded", String(show));
    });
    host.append(box, tableWrap, toggle);
    const api = {
      show(html, x, y) { tip.replaceChildren(...html); tip.style.left = x + "px"; tip.style.top = y + "px"; tip.classList.add("on"); },
      hide() { tip.classList.remove("on"); },
    };
    const render = () => { const w = Math.max(240, box.clientWidth || host.clientWidth || 320); box.replaceChildren(draw(w, api), tip); };
    new ResizeObserver(() => { const w = box.clientWidth; if (w && Math.abs(w - (render.last || 0)) > 4) { render.last = w; render(); } }).observe(host);
    render.last = box.clientWidth; render();
  }
  const tipLines = (title, lines) => [h("b", { text: title }), ...lines.map((l) => h("span", { text: l, style: "display:block" }))];

  // ---------------------------------------------------------------- columns (units per month)
  function columns(host, o) {
    const rows = o.rows, H = 230, M = { l: 36, r: 6, t: 26, b: 34 };
    const maxV = Math.max(...rows.map((r) => r.value), o.ref ? o.ref.value : 0);
    const top = niceMax(maxV * 1.08);
    const table = () => h("table", { class: "datatable" }, h("thead", {}, h("tr", {}, h("th", { text: "Month" }), h("th", { text: o.unit }), h("th", { text: "Bill (Rs)" }))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { text: r.label }), h("td", { text: fmt(r.value) }), h("td", { text: r.bill != null ? fmt(r.bill) : "–" })))));
    frame(host, (W, tip) => {
      const iw = W - M.l - M.r, ih = H - M.t - M.b, slot = iw / rows.length, bw = Math.max(6, Math.min(26, slot * 0.64));
      const y = (v) => M.t + ih - (v / top) * ih;
      const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": o.aria });
      [0, top / 2, top].forEach((t) => {
        s.append(svg("line", { class: "grid", x1: M.l, x2: W - M.r, y1: y(t), y2: y(t) }));
        s.append(svg("text", { x: M.l - 6, y: y(t) + 4, "text-anchor": "end" }, document.createTextNode(short(t))));
      });
      const every = slot < 24 ? 2 : 1;
      rows.forEach((r, i) => {
        const cx = M.l + slot * i + slot / 2, x = cx - bw / 2, bh = Math.max(2, (r.value / top) * ih);
        const bar = svg("path", { d: roundedTop(x, y(r.value), bw, bh, 4), fill: r.hi ? "var(--accent)" : "var(--ctx)", class: "mark" });
        s.append(bar);
        if (i % every === 0 || r.hi) {
          s.append(svg("text", { x: cx, y: H - 16, "text-anchor": "middle", style: r.hi ? "fill:var(--ink);font-weight:700" : "" }, document.createTextNode(monthShort(r.label))));
          const first = i === 0 || monthShort(r.label) === "Jan";
          if (first || r.hi) s.append(svg("text", { x: cx, y: H - 3, "text-anchor": "middle", style: "font-size:10px" }, document.createTextNode(yearShort(r.label))));
        }
        if (r.hi) s.append(svg("text", { class: "val", x: cx, y: y(r.value) - 7, "text-anchor": "middle", style: "fill:var(--ink)" }, document.createTextNode(fmt(r.value))));
        const hit = svg("rect", { x: cx - slot / 2, y: M.t, width: slot, height: ih + 6, fill: "transparent", tabindex: "0", "aria-label": `${r.label}: ${fmt(r.value)} ${o.unit}` });
        const on = () => { bar.setAttribute("fill", "var(--ash)"); tip.show(tipLines(r.label, [`${fmt(r.value)} ${o.unit}`].concat(r.bill != null ? [`Bill Rs ${fmt(r.bill)}`] : [])), cx, y(r.value)); };
        const off = () => { bar.setAttribute("fill", r.hi ? "var(--accent)" : "var(--ctx)"); tip.hide(); };
        ["mouseenter", "focus"].forEach((e) => hit.addEventListener(e, on)); ["mouseleave", "blur"].forEach((e) => hit.addEventListener(e, off));
        s.append(hit);
      });
      if (o.ref) {
        s.append(svg("line", { class: "ref", x1: M.l, x2: W - M.r, y1: y(o.ref.value), y2: y(o.ref.value) }));
        s.append(svg("text", { x: M.l + 4, y: y(o.ref.value) - 6, "text-anchor": "start", style: "fill:var(--ink-2);font-weight:700;stroke:var(--surface);stroke-width:4px;paint-order:stroke" }, document.createTextNode(`${o.ref.label} ${fmt(o.ref.value)}`)));
      }
      return s;
    }, table);
  }

  // ---------------------------------------------------------------- scatter (units vs bill)
  function scatter(host, o) {
    const pts = o.points, H = 250, M = { l: 44, r: 12, t: 16, b: 38 };
    const xt = niceMax(Math.max(...pts.map((p) => p.x)) * 1.05), yt = niceMax(Math.max(...pts.map((p) => p.y)) * 1.08);
    const table = () => h("table", { class: "datatable" }, h("thead", {}, h("tr", {}, h("th", { text: "Month" }), h("th", { text: "Units" }), h("th", { text: "Bill (Rs)" }), h("th", { text: "Rs per unit" }))),
      h("tbody", {}, pts.map((p) => h("tr", {}, h("td", { text: p.label }), h("td", { text: fmt(p.x) }), h("td", { text: fmt(p.y) }), h("td", { text: p.rpu != null ? p.rpu : "–" })))));
    frame(host, (W, tip) => {
      const iw = W - M.l - M.r, ih = H - M.t - M.b;
      const X = (v) => M.l + (v / xt) * iw, Y = (v) => M.t + ih - (v / yt) * ih;
      const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": o.aria });
      [0, yt / 2, yt].forEach((t) => { s.append(svg("line", { class: "grid", x1: M.l, x2: W - M.r, y1: Y(t), y2: Y(t) })); s.append(svg("text", { x: M.l - 6, y: Y(t) + 4, "text-anchor": "end" }, document.createTextNode(short(t)))); });
      [0, xt / 2, xt].forEach((t) => s.append(svg("text", { x: X(t), y: H - 20, "text-anchor": t === 0 ? "start" : t === xt ? "end" : "middle" }, document.createTextNode(fmt(t)))));
      s.append(svg("text", { x: M.l + iw / 2, y: H - 4, "text-anchor": "middle" }, document.createTextNode(o.xLabel)));
      const ordered = pts.slice().sort((a, b) => a.hi - b.hi); // current month drawn last, on top
      ordered.forEach((p) => {
        const c = svg("circle", { cx: X(p.x), cy: Y(p.y), r: p.hi ? 8 : 6, fill: p.hi ? "var(--accent)" : "var(--bark)", "fill-opacity": p.hi ? 1 : .75, stroke: "var(--surface)", "stroke-width": 2, class: "mark", tabindex: "0", "aria-label": `${p.label}: ${fmt(p.x)} units, Rs ${fmt(p.y)}` });
        const on = () => tip.show(tipLines(p.label, [`${fmt(p.x)} units`, `Rs ${fmt(p.y)}`].concat(p.rpu != null ? [`Rs ${p.rpu} per unit`] : [])), X(p.x), Y(p.y) - 8);
        ["mouseenter", "focus"].forEach((e) => c.addEventListener(e, on)); ["mouseleave", "blur"].forEach((e) => c.addEventListener(e, () => tip.hide()));
        s.append(c);
        if (p.hi) s.append(svg("text", { class: "val", x: X(p.x) - 12, y: Y(p.y) + 4, "text-anchor": "end", style: "fill:var(--ink)" }, document.createTextNode("This month")));
      });
      return s;
    }, table);
  }
  return { columns, scatter, niceMax };
})();
