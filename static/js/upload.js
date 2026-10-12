// Upload page: pick a photo -> black out personal details in the browser -> send ONLY the masked JPEG.
// Fail-closed: if the photo can't be decoded for masking, nothing is sent.
(function () {
  const { store, KEY_EXTRACTION } = BS;
  const $ = (id) => document.getElementById(id);
  const input = $("bill"), form = $("upload-form"), go = $("go"), goLabel = $("go-label");
  const errBox = $("error"), warnBox = $("warnings"), dropLabel = $("drop-label");
  const panel = $("mask-panel"), canvas = $("mask-canvas"), ctx = canvas.getContext("2d");
  const undoBtn = $("mask-undo"), clearBtn = $("mask-clear"), countEl = $("mask-count"), noPii = $("no-pii");
  const MAX_BYTES = parseInt($("limits").dataset.maxMb || "8", 10) * 1024 * 1024;
  const MAX_EDGE = 2000;           // working resolution; the server downsizes further
  const MIN_BOX = 0.01;            // ignore accidental taps smaller than 1% of the image
  const OK_TYPES = ["image/jpeg", "image/png", "image/webp"];
  let base = null, boxes = [], drag = null, busy = false;

  const showError = (m) => { errBox.textContent = m; errBox.classList.toggle("hidden", !m); };

  async function decode(file) {
    if (typeof createImageBitmap === "function") {
      try { return await createImageBitmap(file, { imageOrientation: "from-image" }); } catch (e) { /* fall through */ }
    }
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file), img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode")); };
      img.src = url;
    });
  }

  function redraw(withPreview) {
    ctx.drawImage(base, 0, 0);
    ctx.fillStyle = "#000";
    boxes.forEach((b) => ctx.fillRect(b.x, b.y, b.w, b.h));
    if (withPreview && drag) {
      const b = norm(drag);
      ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(b.x, b.y, b.w, b.h);
      ctx.lineWidth = Math.max(2, canvas.width / 400); ctx.strokeStyle = "#FCA311"; ctx.strokeRect(b.x, b.y, b.w, b.h);
    }
  }
  const norm = (d) => ({ x: Math.min(d.x0, d.x1), y: Math.min(d.y0, d.y1), w: Math.abs(d.x1 - d.x0), h: Math.abs(d.y1 - d.y0) });
  function point(e) {
    const r = canvas.getBoundingClientRect();
    const x = (e.clientX - r.left) * canvas.width / r.width, y = (e.clientY - r.top) * canvas.height / r.height;
    return [Math.max(0, Math.min(canvas.width, x)), Math.max(0, Math.min(canvas.height, y))];
  }

  function updateState() {
    const n = boxes.length;
    countEl.textContent = n ? `${n} area${n > 1 ? "s" : ""} covered` : "Nothing covered yet";
    undoBtn.disabled = clearBtn.disabled = n === 0 || busy;
    noPii.disabled = n > 0;
    if (n > 0) noPii.checked = false;
    const ready = !!base && !busy && (n > 0 || noPii.checked);
    go.disabled = !ready;
    goLabel.textContent = busy ? "Reading your bill… this can take 10–20 seconds"
      : !base ? "Choose a photo first" : ready ? "Read my bill" : "Cover personal details, or tick the box above";
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (!base || busy) return;
    e.preventDefault(); try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* synthetic or unsupported pointer */ }
    const [x, y] = point(e); drag = { x0: x, y0: y, x1: x, y1: y };
  });
  canvas.addEventListener("pointermove", (e) => { if (!drag) return; [drag.x1, drag.y1] = point(e); redraw(true); });
  const finish = (e, commit) => {
    if (!drag) return;
    if (commit) {
      [drag.x1, drag.y1] = point(e);
      const b = norm(drag);
      if (b.w >= canvas.width * MIN_BOX && b.h >= canvas.height * MIN_BOX) boxes.push(b);
    }
    drag = null; redraw(false); updateState();
  };
  canvas.addEventListener("pointerup", (e) => finish(e, true));
  canvas.addEventListener("pointercancel", (e) => finish(e, false));
  undoBtn.addEventListener("click", () => { boxes.pop(); redraw(false); updateState(); });
  clearBtn.addEventListener("click", () => { boxes = []; redraw(false); updateState(); });
  noPii.addEventListener("change", updateState);

  input.addEventListener("change", async () => {
    showError(""); warnBox.classList.add("hidden");
    base = null; boxes = []; drag = null; noPii.checked = false; panel.classList.add("hidden"); updateState();
    const f = input.files[0];
    if (!f) return;
    if (f.type && !OK_TYPES.includes(f.type)) {
      showError(/heic|heif/i.test(f.type) ? "HEIC photos aren't supported yet. Please use JPEG, PNG or WebP." : "Please choose a JPEG, PNG or WebP photo."); return;
    }
    let src;
    try { src = await decode(f); }
    catch (e) { showError("Your browser couldn't open this photo, so it can't be covered safely. Try another photo, or enter the values by hand."); return; }
    const w = src.width || src.naturalWidth, h = src.height || src.naturalHeight;
    if (!w || !h) { showError("That photo looks empty. Please choose another."); return; }
    const s = Math.min(1, MAX_EDGE / Math.max(w, h));
    canvas.width = Math.round(w * s); canvas.height = Math.round(h * s);
    base = document.createElement("canvas"); base.width = canvas.width; base.height = canvas.height;
    base.getContext("2d").drawImage(src, 0, 0, canvas.width, canvas.height);
    if (src.close) src.close();
    redraw(false); panel.classList.remove("hidden"); dropLabel.textContent = "Choose a different photo";
    updateState(); panel.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  });

  function toJpeg() {
    return new Promise((resolve) => {
      redraw(false);
      const tryQ = (q) => canvas.toBlob((b) => (b && b.size > MAX_BYTES && q > 0.5 ? tryQ(q - 0.2) : resolve(b)), "image/jpeg", q);
      tryQ(0.9);
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (go.disabled) return;
    busy = true; showError(""); updateState();
    try {
      const blob = await toJpeg();
      if (!blob) throw new Error("Could not prepare the covered photo. Please try again.");
      const fd = new FormData(); fd.append("bill", blob, "bill.jpg");
      const r = await fetch("/api/extract", { method: "POST", body: fd });
      const j = await r.json().catch(() => null);
      if (!j || !j.ok) throw new Error((j && j.error && j.error.message) || "Something went wrong. Please try again.");
      if (!store.set(KEY_EXTRACTION, { extraction: j.extraction, synthetic: !!j.synthetic, warnings: j.image.warnings, covered_areas: boxes.length }))
        throw new Error("Your browser blocked temporary storage. Please allow site data and retry.");
      window.location.href = "/review";
    } catch (err) {
      showError(err.message || "Network problem. Please check your connection and try again.");
      busy = false; updateState();
    }
  });

  $("manual").addEventListener("click", () => {
    store.set(KEY_EXTRACTION, { extraction: null, manual: true });
    window.location.href = "/review";
  });
  updateState();
})();
