// Interactive pieces of the report. Data is embedded in <script id="data">; nothing is fetched.
// Text from the data is inserted with textContent / createTextNode only.
(() => {
  "use strict";
  const D = JSON.parse(document.getElementById("data").textContent);
  const M = Object.fromEntries(D.methods.map((m) => [m.key, m]));
  const SVGNS = "http://www.w3.org/2000/svg";
  const MAIN = ["regex", "presidio", "llm"].filter((k) => M[k]);
  const state = { mode: "overlap", note: D.defaultNote, tab: "llm", run: 0 };

  // ---------------------------------------------------------------- helpers
  const $ = (s) => document.querySelector(s);
  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "style") n.style.cssText = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const c of kids) if (c != null && c !== false) n.append(c);
    return n;
  }
  function svg(tag, attrs = {}) {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  function stext(x, y, s, cls, anchor = "start") {
    const t = svg("text", { x, y, class: cls, "text-anchor": anchor, "dominant-baseline": "middle" });
    t.textContent = s;
    return t;
  }
  const color = (k) => `var(--c-${k})`;
  const pct = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : `${(100 * v).toFixed(d)}%`);
  const isDark = () => getComputedStyle(document.documentElement).colorScheme.includes("dark");

  function seg(options, current, onPick, label) {
    return el("div", { class: "seg", role: "group", "aria-label": label },
      ...options.map(([v, t]) => el("button", { type: "button", "aria-pressed": String(v === current), onclick: () => onPick(v) }, t)));
  }
  function legend(keys) {
    return el("div", { class: "legend" }, ...keys.map((k) => el("span", {}, el("i", { style: `background:${color(k)}` }), M[k].label)));
  }

  // ---------------------------------------------------------------- tooltip
  const tip = $("#tip");
  function showTip(src, title, value, label, key, sub) {
    tip.replaceChildren();
    if (title) tip.append(el("div", { class: "t" }, title));
    tip.append(el("div", { class: "row" }, key ? el("span", { class: "key", style: `background:${color(key)}` }) : null,
      el("span", { class: "v" }, value), el("span", {}, label || "")));
    if (sub) tip.append(el("div", { class: "sub" }, sub));
    tip.style.display = "block";
    let x, y;
    if (src instanceof Event && "clientX" in src) { x = src.clientX; y = src.clientY; }
    else { const b = src.getBoundingClientRect(); x = b.left + b.width / 2; y = b.top; }
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + 14, top = y - h - 10;
    if (left + w > innerWidth - 8) left = x - w - 14;
    if (top < 8) top = y + 18;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${top}px`;
  }
  const hideTip = () => { tip.style.display = "none"; };
  function hoverable(node, show) {
    node.addEventListener("pointermove", (e) => show(e));
    node.addEventListener("pointerleave", hideTip);
    node.addEventListener("focus", () => show(node));
    node.addEventListener("blur", hideTip);
  }

  // ---------------------------------------------------------------- horizontal bars
  function barPath(x, y, w, h) {
    if (w <= 0) return "";
    const r = Math.min(4, w, h / 2);
    return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
  }
  /** groups: [{label, bars:[{key, value, text, tip:{title,value,label,sub}}]}] */
  function hbars(host, groups, { max = 1, ticks = [0, 0.25, 0.5, 0.75, 1], fmtTick = (t) => `${t * 100}%`, barH = 16, gap = 3, groupGap = 18, labelW } = {}) {
    host.replaceChildren();
    const W = Math.max(300, host.clientWidth);
    const lw = labelW || Math.min(140, Math.max(84, W * 0.18));
    const valW = 48, plotW = W - lw - valW, axisH = 24;
    let y = 2;
    const rows = groups.map((g) => { const h = g.bars.length * (barH + gap) - gap; const r = { g, y, h }; y += h + groupGap; return r; });
    const H = y - groupGap + axisH;
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
    const xs = (v) => lw + (Math.max(0, v) / max) * plotW;
    for (const t of ticks) {
      s.append(svg("line", { x1: xs(t), x2: xs(t), y1: 0, y2: H - axisH + 2, class: t === 0 ? "baseline" : "gridline" }));
      s.append(stext(xs(t), H - axisH / 2 + 4, fmtTick(t), "axis", t === 0 ? "start" : t === max ? "end" : "middle"));
    }
    for (const { g, y: gy, h } of rows) {
      s.append(stext(lw - 12, gy + h / 2, g.label, "cat", "end"));
      g.bars.forEach((b, i) => {
        const by = gy + i * (barH + gap);
        const w = xs(b.value) - lw;
        const p = svg("path", { d: barPath(lw, by, w, barH), class: "bar", tabindex: "0", style: `fill:${color(b.key)}`, "aria-label": `${b.tip.label} ${b.tip.title}: ${b.tip.value}` });
        const hit = svg("rect", { x: lw, y: by - gap / 2, width: plotW, height: barH + gap, class: "hit" });
        const show = (src) => { p.classList.add("hot"); showTip(src, b.tip.title, b.tip.value, b.tip.label, b.key, b.tip.sub); };
        for (const n of [hit, p]) { hoverable(n, show); n.addEventListener("pointerleave", () => p.classList.remove("hot")); }
        p.addEventListener("blur", () => p.classList.remove("hot"));
        s.append(hit, p, stext(lw + w + 7, by + barH / 2, b.text, "val"));
      });
    }
    host.append(s);
  }

  // ---------------------------------------------------------------- 2. accuracy
  function renderAccuracy() {
    const host = $("#acc-chart");
    const ctl = $("#acc-controls");
    ctl.replaceChildren(seg([["overlap", "Any overlap"], ["exact", "Exact boundaries"]], state.mode, (v) => { state.mode = v; renderAccuracy(); }, "Matching"));
    $("#acc-legend").replaceChildren(legend(MAIN));
    const metrics = [["recall", "Recall", "share of identifiers removed"], ["precision", "Precision", "share of redactions that were identifiers"], ["f1", "F1", "balance of the two"]];
    hbars(host, metrics.map(([k, label, what]) => ({
      label,
      bars: MAIN.map((m) => {
        const o = D.overall[m][state.mode];
        const sd = k === "f1" && D.runs[m] > 1 ? `± ${(100 * o.f1_sd).toFixed(1)} across ${D.runs[m]} runs` : what;
        return { key: m, value: o[k], text: pct(o[k]), tip: { title: label, value: pct(o[k], 1), label: M[m].label, sub: sd } };
      }),
    })));
  }

  // ---------------------------------------------------------------- 3. heatmap
  const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
  const BINS = [0.2, 0.4, 0.6, 0.75, 0.88, 0.97];
  function heatColor(v) {
    let i = BINS.findIndex((b) => v < b);
    if (i === -1) i = RAMP.length - 1;
    // Dark mode: more = lighter (more contrast against the dark surface).
    const idx = isDark() ? RAMP.length - 1 - i : i;
    const bg = RAMP[idx];
    const fg = idx >= 3 ? "#ffffff" : "#0b0b0b";
    return { bg, fg };
  }
  function renderHeatmap() {
    const host = $("#heatmap");
    host.replaceChildren();
    const cols = [...MAIN, ...(D.byCat.overlap.llm_rewrite ? ["llm_rewrite"] : [])];
    const head = el("tr", {}, el("th", {}), ...cols.map((k) => el("th", { scope: "col" }, el("span", { class: "sw", style: `background:${color(k)}` }), M[k].label + (k === "llm_rewrite" ? "*" : ""))));
    const body = D.categories.map((cat) => el("tr", {}, el("th", { class: "row", scope: "row" }, D.catLabels[cat] || cat),
      ...cols.map((k) => {
        const v = (D.byCat.overlap[k][cat] || {}).recall;
        const n = D.nGold[cat];
        if (k === "regex" && !D.regexCats.includes(cat)) {
          const td = el("td", { class: "na", tabindex: "0" }, "not tried");
          hoverable(td, (src) => showTip(src, `${D.catLabels[cat] || cat}`, "–", "Regex does not attempt this category", "regex"));
          return td;
        }
        const { bg, fg } = heatColor(v);
        const td = el("td", { tabindex: "0", style: `background:${bg};color:${fg}`, "aria-label": `${M[k].label}, ${cat}: ${pct(v)} recall` }, pct(v));
        hoverable(td, (src) => showTip(src, `${D.catLabels[cat] || cat} · recall`, pct(v, 1), M[k].label, k,
          k === "llm_rewrite" ? "approximate (text no longer present)" : `${Math.round(v * n)} of ${n} found`));
        return td;
      })));
    host.append(el("div", { class: "scroll-x" }, el("table", { class: "heat" }, el("thead", {}, head), el("tbody", {}, ...body))));
    const ramp = el("span", { class: "ramp" }, ...RAMP.map((_, i) => el("i", { style: `background:${RAMP[isDark() ? RAMP.length - 1 - i : i]}` })));
    host.append(el("div", { class: "scale" }, "0%", ramp, "100% recall",
      el("span", { style: "margin-left:auto" }, cols.includes("llm_rewrite") ? "* approximate" : "")));
  }

  // ---------------------------------------------------------------- simple bar charts
  function renderSimple(id, keys, valueOf, title, sub) {
    const host = $(id);
    if (!host) return;
    hbars(host, keys.map((k) => ({
      label: M[k].label,
      bars: [{ key: k, value: valueOf(k), text: pct(valueOf(k)), tip: { title, value: pct(valueOf(k), 1), label: M[k].label, sub: sub && sub(k) } }],
    })), { barH: 20, groupGap: 10, labelW: 120 });
  }

  // ---------------------------------------------------------------- 4. consistency
  function renderConsistency() {
    const host = $("#cons-bar");
    if (!host || !D.consistency) return;
    const c = D.consistency, n = c.runs;
    const total = Object.values(c.found).reduce((a, b) => a + b, 0);
    const order = [...Object.keys(c.found)].sort((a, b) => b - a); // 3,2,1,0
    const shades = ["#0d366b", "#3987e5", "#86b6ef", "#cde2fb"];
    const ink = ["#fff", "#fff", "#0b0b0b", "#0b0b0b"];
    host.replaceChildren();
    const bar = el("div", { class: "segbar", role: "img", "aria-label": "How many runs found each identifier" });
    const leg = el("div", { class: "legend" });
    order.forEach((k, i) => {
      const v = c.found[k], share = v / total;
      const label = k === String(n) ? `Found every run` : k === "0" ? "Never found" : `Found in ${k} of ${n}`;
      const part = el("div", { tabindex: "0", style: `flex:${v} 0 0;background:${shades[i]};color:${ink[i]}` }, share > 0.06 ? pct(share) : "");
      hoverable(part, (src) => showTip(src, label, v.toLocaleString(), `identifiers (${pct(share, 1)})`));
      bar.append(part);
      leg.append(el("span", {}, el("i", { style: `background:${shades[i]}` }), label));
    });
    host.append(bar, leg);
  }

  // ---------------------------------------------------------------- 6. examples
  const overlaps = (a, b) => a[0] < b[1] && b[0] < a[1];
  const mk = (tag, cls, text, title) => { const n = el(tag, { class: cls }, text); if (title) n.title = title; return n; };

  function renderSpans(pre, text, gold, preds) {
    let pos = 0;
    const plain = (a, b) => {
      let i = a;
      while (i < b) {
        const g = gold.find((g) => g[0] <= i && i < g[1]);
        if (g) { const j = Math.min(g[1], b); pre.append(mk("mark", "miss", text.slice(i, j), `Left in: ${g[2]}`)); i = j; }
        else { let nx = b; for (const h of gold) if (h[0] > i && h[0] < nx) nx = h[0]; pre.append(text.slice(i, nx)); i = nx; }
      }
    };
    for (const p of [...preds].sort((a, b) => a[0] - b[0])) {
      plain(pos, p[0]);
      const hit = gold.some((g) => overlaps(g, p));
      pre.append(mk("span", hit ? "tag" : "tag fp", `[${p[2]}]`, `${hit ? "Redacted" : "Not an identifier"}: ${text.slice(p[0], p[1])}`));
      pos = p[1];
    }
    plain(pos, text.length);
  }
  function findAll(hay, needle) {
    const out = [], w = (ch) => /[A-Za-z0-9]/.test(ch || "");
    for (const ci of [false, true]) {
      const h = ci ? hay.toLowerCase() : hay, nd = ci ? needle.toLowerCase() : needle;
      for (let i = h.indexOf(nd); i !== -1 && nd; i = h.indexOf(nd, i + 1)) {
        if (!(w(hay[i - 1]) && w(hay[i])) && !(w(hay[i + nd.length]) && w(hay[i + nd.length - 1]))) out.push([i, i + nd.length]);
      }
      if (out.length) break;
    }
    return out;
  }
  function renderRewrite(pre, out, note) {
    const marks = [], seen = new Set();
    for (const g of note.gold) {
      const t = note.text.slice(g[0], g[1]);
      if (seen.has(t)) continue;
      seen.add(t);
      for (const [s, e] of findAll(out, t)) marks.push([s, e, g[2]]);
    }
    marks.sort((a, b) => a[0] - b[0]);
    const tags = (s) => { const re = /\[[A-Z_0-9]+\]/g; let last = 0, m; while ((m = re.exec(s))) { pre.append(s.slice(last, m.index), mk("span", "tag", m[0])); last = m.index + m[0].length; } pre.append(s.slice(last)); };
    let pos = 0, leaked = 0;
    for (const [s, e, c] of marks) { if (s < pos) continue; tags(out.slice(pos, s)); pre.append(mk("mark", "miss", out.slice(s, e), `Left in: ${c}`)); leaked++; pos = e; }
    tags(out.slice(pos));
    return leaked;
  }
  function noteStats(note, preds) {
    let missed = 0;
    for (const g of note.gold) if (!preds.some((p) => overlaps(g, p))) missed++;
    const fp = preds.filter((p) => !note.gold.some((g) => overlaps(g, p))).length;
    return { missed, fp };
  }

  function renderExamples() {
    const host = $("#ex-viewer");
    host.replaceChildren();
    const note = D.notes[state.note];
    const select = el("select", { "aria-label": "Choose a note", onchange: (e) => { state.note = Number(e.target.value); renderExamples(); } });
    const groups = {};
    D.notes.forEach((n, i) => (groups[n.type] ||= []).push([n, i]));
    for (const [type, list] of Object.entries(groups)) {
      const og = el("optgroup", { label: type.replaceAll("_", " ") });
      for (const [n, i] of list) { const o = el("option", { value: String(i) }, `${n.id} (${n.type.replaceAll("_", " ")})`); if (i === state.note) o.selected = true; og.append(o); }
      select.append(og);
    }
    const go = (d) => { state.note = (state.note + d + D.notes.length) % D.notes.length; renderExamples(); };
    host.append(el("div", { class: "ex-bar" },
      el("button", { class: "navbtn", type: "button", onclick: () => go(-1), "aria-label": "Previous note" }, "←"),
      select,
      el("button", { class: "navbtn", type: "button", onclick: () => go(1), "aria-label": "Next note" }, "→")));

    const tabKeys = D.methods.filter((m) => m.key !== "llm_t0").map((m) => m.key);
    host.append(el("div", { class: "tabs", role: "tablist" }, ...tabKeys.map((k) => el("button", {
      role: "tab", type: "button", "aria-selected": String(k === state.tab), onclick: () => { state.tab = k; renderExamples(); },
    }, el("i", { style: `background:${color(k)}` }), M[k].label))));

    // Left: original with identifiers highlighted.
    const orig = el("pre");
    const marks = [...note.gold.map((g) => [g[0], g[1], "gold", g[2]]), ...note.traps.map((t) => [t[0], t[1], "trap", "Medical term, not an identifier"])].sort((a, b) => a[0] - b[0]);
    let pos = 0;
    for (const [s, e, cls, title] of marks) { orig.append(note.text.slice(pos, s), mk("span", cls, note.text.slice(s, e), title)); pos = e; }
    orig.append(note.text.slice(pos));
    const left = el("div", { class: "pane" }, el("div", { class: "pane-title" }, "Original"),
      el("div", { class: "pane-stats" }, el("span", { class: "pill" }, `${note.gold.length} identifiers`), note.traps.length ? el("span", { class: "pill" }, `${note.traps.length} medical-term trap${note.traps.length > 1 ? "s" : ""}`) : null), orig);

    // Right: the selected method.
    const m = M[state.tab];
    const pre = el("pre");
    const pills = el("div", { class: "pane-stats" });
    const titleRow = el("div", { class: "pane-title", style: "display:flex;justify-content:space-between;align-items:center;gap:8px" }, `Redacted by ${m.label}`);
    if (m.spans) {
      const runs = note.pred[m.key];
      if (runs.length > 1) titleRow.append(seg(runs.map((_, i) => [i, `Run ${i + 1}`]), Math.min(state.run, runs.length - 1), (v) => { state.run = v; renderExamples(); }, "LLM run"));
      const preds = runs[Math.min(state.run, runs.length - 1)];
      renderSpans(pre, note.text, note.gold, preds);
      const s = noteStats(note, preds);
      pills.append(el("span", { class: s.missed ? "pill bad" : "pill" }, `${s.missed} left in`), el("span", { class: s.fp ? "pill warn" : "pill" }, `${s.fp} over-redacted`));
    } else if (note.rewrite != null) {
      const leaked = renderRewrite(pre, note.rewrite, note);
      pills.append(el("span", { class: leaked ? "pill bad" : "pill" }, `${leaked} identifier text${leaked === 1 ? "" : "s"} left in`), el("span", { class: "pill" }, "the model rewrote the whole note"));
    }
    const right = el("div", { class: "pane" }, titleRow, pills, pre);
    host.append(el("div", { class: "panes" }, left, right));
    host.append(el("div", { class: "key-legend" },
      el("span", {}, el("span", { class: "gold" }, "highlighted"), " identifier"),
      el("span", {}, el("span", { class: "trap" }, "dotted"), " medical term (not an identifier)"),
      el("span", {}, el("span", { class: "tag" }, "[TAG]"), " redacted"),
      el("span", {}, el("mark", { class: "miss" }, "red"), " identifier left in"),
      el("span", {}, el("span", { class: "tag fp" }, "[TAG]"), " over-redacted (hover to see what was removed)")));
  }

  // ---------------------------------------------------------------- nav
  function wireNav() {
    const links = [...document.querySelectorAll("nav.toc a")];
    const byId = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const obs = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { links.forEach((a) => a.classList.remove("active")); byId.get(e.target.id)?.classList.add("active"); }
    }, { rootMargin: "-45% 0px -50% 0px" });
    document.querySelectorAll("section[id]").forEach((s) => obs.observe(s));
  }

  // ---------------------------------------------------------------- go
  function renderCharts() {
    renderAccuracy();
    renderHeatmap();
    renderSimple("#trap-chart", MAIN, (k) => D.traps[k].hit_rate, "Medical terms redacted", (k) => `of ${D.traps[k].n_terms} terms`);
    renderConsistency();
    const integKeys = D.methods.map((m) => m.key).filter((k) => D.integrity[k]);
    renderSimple("#integ-chart", integKeys, (k) => D.integrity[k].substantive_changed, "Notes with non-identifier text changed", (k) => `${D.integrity[k].n} notes`);
  }
  renderCharts();
  renderExamples();
  wireNav();
  let t;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(renderCharts, 120); });
  addEventListener("scroll", hideTip, { passive: true });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderHeatmap);
})();
