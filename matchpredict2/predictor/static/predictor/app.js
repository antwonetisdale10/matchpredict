/* Matchday UI behaviour. Springs (damping/response, Apple-style), interruptible sheet with velocity hand-off,
   sliding segmented thumbs, local-time formatting, prediction form. No dependencies. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------- spring: response (s) + damping ratio; starts from the CURRENT value + velocity ---------- */
  function spring({ from, to, velocity = 0, response = 0.4, damping = 1, onUpdate, onDone }) {
    if (reduce.matches) { onUpdate(to); onDone && onDone(); return { cancel() {}, get value() { return to; } }; }
    const w = (2 * Math.PI) / response, k = w * w, c = 2 * damping * w;
    let x = from, v = velocity, last = performance.now(), raf, dead = false;
    const step = (now) => {
      let dt = Math.min((now - last) / 1000, 1 / 30); last = now;
      const n = Math.max(1, Math.ceil(dt / 0.004)), h = dt / n;
      for (let i = 0; i < n; i++) { const a = -k * (x - to) - c * v; v += a * h; x += v * h; }
      onUpdate(x);
      if (Math.abs(x - to) < 0.15 && Math.abs(v) < 0.5) { onUpdate(to); onDone && onDone(); return; }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return { cancel() { dead = true; cancelAnimationFrame(raf); }, get value() { return x; }, get velocity() { return v; } };
  }
  const project = (v, d = 0.998) => (v / 1000) * d / (1 - d); // Apple's momentum projection (px/s -> px)
  const rubber = (o, dim, c = 0.55) => (o * dim * c) / (dim + c * Math.abs(o));

  /* ---------- toast ---------- */
  let toastEl, toastT;
  function toast(msg, err) {
    toastEl = toastEl || Object.assign(document.body.appendChild(document.createElement("div")), { className: "toast", role: "status" });
    toastEl.textContent = msg; toastEl.classList.toggle("err", !!err); toastEl.classList.add("on");
    clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove("on"), 2600);
  }
  window.mdToast = toast;

  /* ---------- local kick-off times ---------- */
  function localTimes(root = document) {
    $$("time[data-fmt]", root).forEach((t) => {
      const d = new Date(t.dateTime); if (isNaN(d)) return;
      const f = t.dataset.fmt;
      const o = f === "date" ? { weekday: "short", day: "numeric", month: "short" }
        : f === "long" ? { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }
        : { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
      t.textContent = new Intl.DateTimeFormat(undefined, o).format(d);
    });
  }

  /* ---------- segmented controls with a sliding spring thumb ---------- */
  function initSeg(seg) {
    if (seg._init) { seg._place(false); return; }
    seg._init = true;
    const thumb = Object.assign(document.createElement("span"), { className: "thumb" });
    seg.prepend(thumb);
    let x = 0, w = 0, sx, sw;
    const sel = () => seg.querySelector('[aria-pressed="true"], [aria-selected="true"], .on');
    seg._place = (animate = true) => {
      const el = sel();
      if (!el) { thumb.style.opacity = 0; return; }
      thumb.style.opacity = 1;
      const tx = el.offsetLeft, tw = el.offsetWidth;
      sx && sx.cancel(); sw && sw.cancel();
      if (!animate || !w) { x = tx; w = tw; draw(); return; }
      sx = spring({ from: x, to: tx, response: 0.38, damping: 0.9, onUpdate: (v) => { x = v; draw(); } });
      sw = spring({ from: w, to: tw, response: 0.38, damping: 1, onUpdate: (v) => { w = v; draw(); } });
    };
    const draw = () => { thumb.style.transform = `translateX(${x}px)`; thumb.style.width = w + "px"; };
    seg._place(false);
    addEventListener("resize", () => seg._place(false));
    document.fonts && document.fonts.ready.then(() => seg._place(false));
  }
  function centerWeek() {
    const sc = $(".weeks"); if (!sc) return;
    const on = sc.querySelector(".on"); if (on) sc.scrollLeft = on.offsetLeft - sc.clientWidth / 2 + on.offsetWidth / 2;
  }

  /* ---------- animate bars in (probabilities, goal-time columns) ---------- */
  function animateBars(root = document) {
    const run = (el) => {
      if (el.dataset.w) el.style.width = el.dataset.w + "%";
      if (el.dataset.h) el.style.height = el.dataset.h + "%";
    };
    const els = $$("[data-w], [data-h]", root);
    if (reduce.matches) return els.forEach(run);
    requestAnimationFrame(() => requestAnimationFrame(() => els.forEach(run))); // let the 0-width state paint, then transition
  }

  /* ---------- bottom sheet: 1:1 drag, rubber-band, momentum projection, velocity hand-off ---------- */
  const Sheet = (() => {
    let scrim, sheet, body, anim, y = 0, open = false, onClose;
    const desktop = () => matchMedia("(min-width: 700px)").matches;
    const rest = () => (desktop() ? sheet.offsetHeight / 2 : 0);
    const hidden = () => innerHeight + (desktop() ? sheet.offsetHeight : 0);
    const draw = (v) => { y = v; sheet.style.transform = `translate(-50%, ${v}px)`; };
    function build() {
      scrim = Object.assign(document.createElement("div"), { className: "scrim" });
      sheet = Object.assign(document.createElement("div"), { className: "sheet", role: "dialog", ariaModal: "true" });
      sheet.innerHTML = '<div class="grab" aria-label="Drag down to close"><i></i></div><div class="body"></div>';
      body = $(".body", sheet);
      document.body.append(scrim, sheet);
      draw(innerHeight * 1.2);
      scrim.addEventListener("click", close);
      addEventListener("keydown", (e) => { if (e.key === "Escape" && open) close(); });
      // drag on the grabber / anywhere that isn't scrolled content
      const grab = $(".grab", sheet);
      let start, grabOffset, hist = [], dragging = false;
      const down = (e) => {
        if (e.target.closest("button, a, input, select, textarea, .seg") ) return;
        if (e.currentTarget === body && body.scrollTop > 0) return;
        dragging = true; anim && anim.cancel(); // interruptible: grab it mid-flight
        e.currentTarget.setPointerCapture(e.pointerId);
        start = e.clientY; grabOffset = y; hist = [{ t: e.timeStamp, y: e.clientY }];
      };
      const move = (e) => {
        if (!dragging) return;
        hist.push({ t: e.timeStamp, y: e.clientY }); if (hist.length > 6) hist.shift();
        const dy = e.clientY - start, r = rest();
        draw(dy < 0 ? r + rubber(dy, 300) : r + dy); // resists upward, follows 1:1 downward
      };
      const up = (e) => {
        if (!dragging) return; dragging = false;
        const a = hist[0], b = hist[hist.length - 1];
        const vel = b.t > a.t ? ((b.y - a.y) / (b.t - a.t)) * 1000 : 0; // px/s
        const projected = y + project(vel);
        const dismiss = projected - rest() > sheet.offsetHeight * 0.45 || vel > 900; // decide by projection + velocity sign
        if (dismiss) close(vel); else settle(vel);
      };
      [grab, body].forEach((el) => { el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up); });
    }
    function settle(v = 0) { anim && anim.cancel(); anim = spring({ from: y, to: rest(), velocity: v, response: 0.34, damping: 0.82, onUpdate: draw }); }
    function show(html, cb) {
      if (!sheet) build();
      body.innerHTML = html; body.scrollTop = 0; onClose = cb; open = true;
      scrim.classList.add("on"); document.body.style.overflow = "hidden";
      if (!y || y < innerHeight * 0.5) draw(hidden());
      anim && anim.cancel();
      anim = spring({ from: y, to: rest(), response: 0.42, damping: 0.86, onUpdate: draw });
      return body;
    }
    function close(v = 0) {
      if (!open) return; open = false; scrim.classList.remove("on"); document.body.style.overflow = "";
      anim && anim.cancel();
      anim = spring({ from: y, to: hidden(), velocity: v, response: 0.32, damping: 1, onUpdate: draw, onDone: () => { onClose && onClose(); } });
    }
    return { show, close, get body() { return body; } };
  })();

  /* ---------- prediction form (inside the sheet or inline on the match page) ---------- */
  function csrf() { const m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : ""; }
  function initPickForm(root) {
    $$(".seg[data-input]", root).forEach((seg) => {
      const input = $(`input[name="${seg.dataset.input}"]`, root);
      const sync = () => $$("button", seg).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.value === input.value)));
      seg.addEventListener("click", (e) => {
        const b = e.target.closest("button"); if (!b) return;
        e.preventDefault();
        input.value = (seg.dataset.clear && input.value === b.dataset.value) ? "" : b.dataset.value;
        sync(); initSeg(seg); seg._place(true);
      });
      sync(); requestAnimationFrame(() => initSeg(seg));
    });
    $$(".stepper", root).forEach((st) => {
      const out = $("output", st), inp = $("input", st);
      st.addEventListener("click", (e) => {
        const b = e.target.closest("button"); if (!b) return;
        e.preventDefault();
        const v = Math.max(0, Math.min(15, (+inp.value || 0) + (b.dataset.d === "+" ? 1 : -1)));
        inp.value = v; out.textContent = v;
        out.animate && !reduce.matches && out.animate([{ transform: "scale(1.18)" }, { transform: "scale(1)" }], { duration: 260, easing: "cubic-bezier(.2,1.4,.4,1)" });
      });
    });
    const useModel = $("[data-usemodel]", root);
    useModel && useModel.addEventListener("click", (e) => {
      e.preventDefault();
      const [h, a] = useModel.dataset.usemodel.split("-");
      [["home_goals", h], ["away_goals", a]].forEach(([n, v]) => { const i = $(`input[name="${n}"]`, root); i.value = v; i.parentElement.querySelector("output").textContent = v; });
    });
    const form = $("form", root);
    form && form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $('button[type="submit"]', form); btn.disabled = true;
      try {
        const r = await fetch(form.action, { method: "POST", body: new FormData(form), headers: { "X-CSRFToken": csrf() }, credentials: "same-origin" });
        const j = await r.json();
        if (r.status === 401) { location.href = `/login/?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
        if (!j.ok) { toast(j.error || "Couldn't save", true); btn.disabled = false; return; }
        navigator.vibrate && navigator.vibrate(10);
        toast(`${j.message} ${j.pick}`);
        const id = form.dataset.match;
        $$(`[data-pick-tag="${id}"]`).forEach((t) => { t.textContent = `Your pick ${j.pick}`; t.hidden = false; });
        btn.disabled = false;
        if (Sheet.body && Sheet.body.contains(form)) setTimeout(() => Sheet.close(), 350);
      } catch (err) { toast("Network error", true); btn.disabled = false; }
    });
  }
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-pick]"); if (!t) return;
    e.preventDefault(); e.stopPropagation();
    try {
      const r = await fetch(`/match/${t.dataset.pick}/pick/`, { credentials: "same-origin" });
      const body = Sheet.show(await r.text());
      initPickForm(body);
    } catch (err) { toast("Couldn't open the form", true); }
  });

  /* ---------- misc ---------- */
  $$("[data-nav]").forEach((s) => s.addEventListener("change", () => { location.href = s.value; }));
  const job = $("#job[data-id]");
  if (job) {
    const bar = $("i", job), txt = $("span", job);
    const poll = async () => {
      try {
        const j = await (await fetch(`/studio/job/${job.dataset.id}/`)).json();
        if (j.state === "running") { bar.style.width = (100 * j.done / (j.total || 1)) + "%"; txt.textContent = `Predicting match ${j.done} of ${j.total}…`; setTimeout(poll, 700); }
        else if (j.state === "done") { bar.style.width = "100%"; txt.textContent = "Done — " + (j.msg || ""); sessionStorage.removeItem("job"); setTimeout(() => location.reload(), 900); }
        else { txt.textContent = j.state === "error" ? "Failed: " + j.msg : ""; }
      } catch (e) { setTimeout(poll, 1500); }
    };
    poll();
  }

  const boot = () => {
    $$(".ev").forEach((e, i) => e.style.setProperty("--d", (i * 0.09) + "s")); localTimes(); $$(".seg[data-thumb]").forEach(initSeg); centerWeek(); animateBars();
    $$("[data-pickform]").forEach(initPickForm);
  };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", boot) : boot();
})();
