/* Range-switchable SVG charts fed by /api/series, plus small sparklines. */
"use strict";

const RANGES = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"];
const SC = {}; // "SYM|RANGE" -> {t, d}

async function getSeries(sym, rng) {
  const k = sym + "|" + rng, hit = SC[k];
  if (hit && Date.now() - hit.t < (rng === "1D" || rng === "5D" ? 60e3 : 600e3)) return hit.d;
  const r = await fetch(`/api/series?symbol=${encodeURIComponent(sym)}&range=${rng}`);
  if (!r.ok) throw new Error("series " + r.status);
  const d = await r.json();
  SC[k] = { t: Date.now(), d };
  return d;
}

// Compare: other tickers drawn on the same chart, all rebased to 0% at the start of the range.
const CMP_COLORS = ["var(--blue)", "#c9a0ff", "#4fd1c5"];
const cmpList = (key) => (lsGet("cmp:" + key) || "").split(",").filter(Boolean);
const canCompare = (sym) => !/^(FRED|SI:|SIP:|DTC:|ALT:)/.test(sym);  // levels and metrics, not prices: nothing to rebase against
function cmpBar(sym, key) {
  if (!canCompare(sym)) return "";
  const list = cmpList(key);
  return `<span class="cmpbox">${list.map((c, i) => `<span class="cmpchip" style="border-color:${CMP_COLORS[i]};color:${CMP_COLORS[i]}">${esc(c)}<b data-cmpdel="${esc(c)}" title="Remove">×</b></span>`).join("")}
    ${list.length < 3 ? `<input class="cmpin" data-suggest="cmp" placeholder="vs ticker or name" title="Compare with another ticker: type a ticker or a company name">${list.includes("SPY") || sym === "SPY" ? "" : '<button class="cmpq" data-cmpadd="SPY" title="Compare with the S&P 500">vs SPY</button>'}` : ""}</span>`;
}

/** Chart placeholder. sym = ticker, BOOK or FRED:<label>. key = where the chosen range is remembered. */
function chartSlot(sym, key, h = 200, def = "1Y") {
  const r = lsGet("rng:" + key) || def;
  return `<div class="chart" data-sym="${esc(sym)}" data-key="${esc(key)}" data-h="${h}" data-rng="${r}">
    <div class="rbar">${RANGES.map((x) => `<button data-r="${x}" class="${x === r ? "on" : ""}">${x}</button>`).join("")}${cmpBar(sym, key)}<span class="chg"></span></div>
    <div class="cplot" style="height:${h}px"><span class="dim">loading...</span></div><div class="cnote"></div></div>`;
}

function fmtT(iso, rng) {
  const d = new Date(iso);
  if (rng === "1D") return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (rng === "5D" || rng === "1M") return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const long = rng === "5Y" || rng === "MAX" || rng === "1Y";
  return d.toLocaleDateString([], { year: long ? "2-digit" : undefined, month: "short", day: rng === "MAX" ? undefined : "numeric" });
}

/** Line up a compare series on the main series' times: the latest compare point at or before each main point
    (by date for daily ranges, since two markets' daily bars carry different clock times). */
function alignTo(mainPts, cmpPts, rng) {
  const intraday = rng === "1D" || rng === "5D", key = (p) => (intraday ? new Date(p[0]).getTime() : String(p[0]).slice(0, 10));
  const out = [];
  let j = 0, last = null;
  for (const p of mainPts) {
    const k = key(p);
    while (j < cmpPts.length && (intraday ? key(cmpPts[j]) <= k + 60e3 : key(cmpPts[j]) <= k)) { last = cmpPts[j][1]; j++; }
    out.push(last);
  }
  return out;
}

function drawSeries(el, d, comps = []) {
  const plot = el.querySelector(".cplot"), h = +el.dataset.h, rng = el.dataset.rng;
  let pts = d.points || [];
  el.querySelector(".cnote").textContent = d.note || "";
  const chg = el.querySelector(".chg");
  if (pts.length < 2) { plot.innerHTML = `<span class="dim">${esc(d.note || "No data")}</span>`; chg.textContent = ""; return; }
  comps = comps.filter((c) => (c.d?.points || []).length > 1);
  if (comps.length) return drawCompare(el, pts, comps);
  // Rates and spreads change in points or basis points; a % change of a yield means nothing.
  const sym = el.dataset.sym, isYield = /^(FREDID:DGS|FREDID:DFII|FREDID:T10Y|FREDID:SOFR|FREDID:DFF|\^(TNX|TYX|FVX|IRX)$)/.test(sym);
  const isLevel = sym.startsWith("FRED") || sym.startsWith("ALT:");
  el.dataset.unit = isYield ? "bp" : isLevel ? "pts" : "pct";
  chg.className = "chg " + cls(d.change);
  const move = isYield ? `${d.change > 0 ? "+" : ""}${n(d.change * 100, 0)} bp` : isLevel ? `${d.change > 0 ? "+" : ""}${n(d.change)}` : `${d.change > 0 ? "+" : ""}${n(d.change)}  (${spct(d.change_pct)})`;
  chg.textContent = `${rng}  ${move}  H ${n(d.high)}  L ${n(d.low)}`;
  const vals = pts.map((p) => p[1]), base = d.base, w = 700, padR = 52;
  const lo = Math.min(...vals, base ?? Infinity), hi = Math.max(...vals, base ?? -Infinity), r = hi - lo || 1;
  // 1D: x is clock time across the whole session, so a live day stops where "now" is (like a broker app).
  // Equities: 9:30-16:00 ET. Round-the-clock instruments (FX, futures, crypto): 24 hours from the first print.
  const ts = pts.map((p) => new Date(p[0]).getTime());
  let t0 = ts[0], t1 = ts.at(-1), session = false;
  if (rng === "1D") {
    const hm = new Date(ts[0]).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false, hour: "2-digit", minute: "2-digit" });
    session = hm >= "09:25" && hm <= "09:45";
    t1 = Math.max(ts.at(-1), t0 + (session ? 390 : 1440) * 60e3);
  }
  const xs = rng === "1D" ? ts.map((t) => ((t - t0) / (t1 - t0 || 1)) * (w - padR)) : vals.map((_, i) => (i / (vals.length - 1)) * (w - padR));
  const X = (i) => xs[i], Y = (v) => h - 16 - ((v - lo) / r) * (h - 26);
  const c = vals.at(-1) >= (base ?? vals[0]) ? "var(--pos)" : "var(--neg)";
  const line = vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const grid = [lo, lo + r / 2, hi].map((v) => `<line x1="0" x2="${w - padR}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1f1f1f"/><text x="${w - padR + 4}" y="${Y(v) + 4}" fill="#8a8a8a" font-size="10">${n(v)}</text>`).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const x = rng === "1D" ? f * (w - padR) : X(Math.round(f * (vals.length - 1)));
    const label = rng === "1D" ? fmtT(new Date(t0 + f * (t1 - t0)).toISOString(), rng) : fmtT(pts[Math.round(f * (vals.length - 1))][0], rng);
    return `<text x="${x}" y="${h - 2}" fill="#6a6a6a" font-size="9" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${esc(label)}</text>`;
  }).join("");
  // A faint marker where the session is now, when the day is still running.
  const nowX = rng === "1D" && Date.now() < t1 && Date.now() > t0 ? ((Date.now() - t0) / (t1 - t0)) * (w - padR) : null;
  const baseLine = base != null ? `<line x1="0" x2="${w - padR}" y1="${Y(base)}" y2="${Y(base)}" stroke="#555" stroke-dasharray="3 3"/>` : "";
  plot.innerHTML = `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${grid}${baseLine}
    <polygon points="0,${h - 16} ${line} ${X(vals.length - 1)},${h - 16}" fill="${c}" opacity=".08"/>
    <polyline points="${line}" fill="none" stroke="${c}" stroke-width="1.4" vector-effect="non-scaling-stroke"/>${ticks}
    ${nowX != null ? `<line x1="${nowX}" x2="${nowX}" y1="0" y2="${h - 16}" stroke="#333" stroke-dasharray="1 3"/>` : ""}
    <line class="xh" x1="0" x2="0" y1="0" y2="${h - 16}" stroke="#777" visibility="hidden"/>
    <rect class="mband" x="0" y="0" width="0" height="${h - 16}" fill="rgba(255,160,40,.10)" visibility="hidden"/>
    <line class="ma" y1="0" y2="${h - 16}" stroke="var(--amber)" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"/>
    <line class="mb" y1="0" y2="${h - 16}" stroke="var(--amber)" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"/></svg>
    <div class="hov" hidden></div><div class="meas" hidden></div>`;
  const svg = plot.querySelector("svg"), hov = plot.querySelector(".hov"), xh = svg.querySelector(".xh");
  drawMarks(svg, el.dataset.key, rng, ts, xs, (i) => Y(vals[i]), h);
  const band = svg.querySelector(".mband"), la = svg.querySelector(".ma"), lb = svg.querySelector(".mb"), meas = plot.querySelector(".meas");
  // Point-to-point measure: click A, move for a live A->cursor readout, click B to pin, click again to restart, Esc clears.
  let A = null, B = null;
  const nearest = (fx) => { let i = 0, best = Infinity; xs.forEach((x, k) => { const dd = Math.abs(x - fx); if (dd < best) { best = dd; i = k; } }); return i; };
  const span = (i, j) => {
    const ms = Math.abs(ts[j] - ts[i]), d = ms / 864e5;
    return d < 1 ? `${Math.round(ms / 6e4)} min` : d < 60 ? `${Math.round(d)} days` : d < 730 ? `${(d / 30.44).toFixed(1)} months` : `${(d / 365.25).toFixed(1)} years`;
  };
  const showMeasure = (i, j) => {
    const [a, b] = i <= j ? [i, j] : [j, i];
    band.setAttribute("x", xs[a]); band.setAttribute("width", Math.max(1, xs[b] - xs[a])); band.setAttribute("visibility", "visible");
    la.setAttribute("x1", xs[i]); la.setAttribute("x2", xs[i]); la.setAttribute("visibility", "visible");
    lb.setAttribute("x1", xs[j]); lb.setAttribute("x2", xs[j]); lb.setAttribute("visibility", "visible");
    const dv = vals[j] - vals[i], u = el.dataset.unit;
    const move = u === "bp" ? `${dv > 0 ? "+" : ""}${n(dv * 100, 0)} bp` : u === "pts" ? `${dv > 0 ? "+" : ""}${n(dv)}` : `${dv > 0 ? "+" : ""}${n(dv)} (${spct(vals[j] / vals[i] - 1)})`;
    meas.hidden = false;
    meas.innerHTML = `<span class="dim">${esc(fmtT(pts[i][0], rng))}</span> ${n(vals[i])} → <span class="dim">${esc(fmtT(pts[j][0], rng))}</span> ${n(vals[j])}
      <b class="${cls(dv)}">${move}</b> <span class="dim">over ${span(i, j)}${B == null ? " · click to pin" : " · click to restart, Esc to clear"}</span>`;
  };
  const clearMeasure = () => { A = B = null; [band, la, lb].forEach((x) => x.setAttribute("visibility", "hidden")); meas.hidden = true; };
  el._clearMeasure = clearMeasure;
  if (typeof whyHook === "function") whyHook(el, svg, pts, vals, xs, Y, nearest, w, clearMeasure);
  svg.style.cursor = "crosshair";
  svg.onclick = (e) => {
    const bb = svg.getBoundingClientRect(), i = nearest(((e.clientX - bb.left) / bb.width) * w);
    if (A == null || B != null) { A = i; B = null; showMeasure(A, A); }
    else { B = i; showMeasure(A, B); }
  };
  svg.onmousemove = (e) => {
    const b = svg.getBoundingClientRect(), fx = ((e.clientX - b.left) / b.width) * w;
    const i = nearest(fx);
    if (A != null && B == null) showMeasure(A, i);
    xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
    const vs = base ?? vals[0];
    hov.hidden = false;
    const dv = vals[i] - vs, u = el.dataset.unit;
    const rel = u === "bp" ? `${dv > 0 ? "+" : ""}${n(dv * 100, 0)} bp` : u === "pts" ? `${dv > 0 ? "+" : ""}${n(dv)}` : spct(vals[i] / vs - 1);
    hov.innerHTML = `${esc(fmtT(pts[i][0], rng))}  <b>${n(vals[i])}</b>  <span class="${cls(dv)}">${rel}</span>`;
    hov.style.left = Math.min(b.width - 150, (X(i) / w) * b.width + 8) + "px";
  };
  svg.onmouseleave = () => { hov.hidden = true; xh.setAttribute("visibility", "hidden"); };
}

/** Compare mode: every line as % change from the first point of the range, so the gap is relative performance. */
function drawCompare(el, pts, comps) {
  const plot = el.querySelector(".cplot"), h = +el.dataset.h, rng = el.dataset.rng, w = 700, padR = 52, sym = el.dataset.sym;
  const v0 = pts[0][1], main = pts.map((p) => p[1] / v0 - 1);
  const lines = comps.map((c, i) => {
    const raw = alignTo(pts, c.d.points, rng), first = raw.find((v) => v != null);
    return { sym: c.sym, color: CMP_COLORS[i], vals: raw.map((v) => (v == null || !first ? null : v / first - 1)) };
  });
  const all = [...main, ...lines.flatMap((l) => l.vals)].filter((v) => v != null);
  const lo = Math.min(...all, 0), hi = Math.max(...all, 0), r = hi - lo || 1;
  const X = (i) => (i / (pts.length - 1)) * (w - padR), Y = (v) => h - 16 - ((v - lo) / r) * (h - 26);
  const path = (vals) => vals.map((v, i) => (v == null ? null : `${X(i).toFixed(1)},${Y(v).toFixed(1)}`)).filter(Boolean).join(" ");
  const grid = [lo, (lo + hi) / 2, hi].map((v) => `<line x1="0" x2="${w - padR}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1f1f1f"/><text x="${w - padR + 4}" y="${Y(v) + 4}" fill="#8a8a8a" font-size="10">${spct(v, 0)}</text>`).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => { const i = Math.round(f * (pts.length - 1));
    return `<text x="${X(i)}" y="${h - 2}" fill="#6a6a6a" font-size="9" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${esc(fmtT(pts[i][0], rng))}</text>`; }).join("");
  const lastOf = (vals) => [...vals].reverse().find((v) => v != null);
  const mEnd = main.at(-1);
  const chg = el.querySelector(".chg");
  chg.className = "chg";
  chg.innerHTML = `${rng} &nbsp;<b style="color:var(--amber)">${esc(sym)} ${spct(mEnd, 1)}</b>` + lines.map((l) => { const e = lastOf(l.vals);
    return ` · <b style="color:${l.color}">${esc(l.sym)} ${spct(e, 1)}</b> <span class="${cls(mEnd - e)}">(${mEnd - e > 0 ? "+" : ""}${n((mEnd - e) * 100, 1)} pts)</span>`; }).join("");
  plot.innerHTML = `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${grid}
    <line x1="0" x2="${w - padR}" y1="${Y(0)}" y2="${Y(0)}" stroke="#555" stroke-dasharray="3 3"/>
    ${lines.map((l) => `<polyline points="${path(l.vals)}" fill="none" stroke="${l.color}" stroke-width="1.2" vector-effect="non-scaling-stroke"/>`).join("")}
    <polyline points="${path(main)}" fill="none" stroke="var(--amber)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>${ticks}
    <line class="xh" x1="0" x2="0" y1="0" y2="${h - 16}" stroke="#777" visibility="hidden"/></svg><div class="hov" hidden></div>`;
  const svg = plot.querySelector("svg"), hov = plot.querySelector(".hov"), xh = svg.querySelector(".xh");
  svg.onmousemove = (e) => {
    const b = svg.getBoundingClientRect(), i = Math.max(0, Math.min(pts.length - 1, Math.round((((e.clientX - b.left) / b.width) * w) / (w - padR) * (pts.length - 1))));
    xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
    hov.hidden = false;
    hov.innerHTML = `${esc(fmtT(pts[i][0], rng))} <b style="color:var(--amber)">${esc(sym)} ${spct(main[i], 1)}</b>` + lines.map((l) => ` <b style="color:${l.color}">${esc(l.sym)} ${spct(l.vals[i], 1)}</b>`).join("");
    hov.style.left = Math.min(b.width - 260, (X(i) / w) * b.width + 8) + "px";
  };
  svg.onmouseleave = () => { hov.hidden = true; xh.setAttribute("visibility", "hidden"); };
  el.querySelector(".cnote").textContent = "Compare: each line is % change from the first point of the range. Gap in points = relative performance.";
}

/** Event markers (set by des.js in CHART_MARKS[key]): letters on the bottom row, triangles on the line.
    E earnings, D dividend, S split, UP/DN rating change, IB/IS insider buy/sale. Hover shows the detail. */
const MARK_STYLE = { E: ["E", "var(--amber)"], D: ["D", "#9a9a9a"], S: ["S", "var(--blue)"], UP: ["▲", "var(--pos)"], DN: ["▼", "var(--neg)"], IB: ["▲", "var(--pos)"], IS: ["▼", "var(--neg)"] };
function drawMarks(svg, key, rng, ts, xs, yAt, h) {
  const marks = typeof CHART_MARKS !== "undefined" ? CHART_MARKS[key] : null;
  if (!marks || !marks.length || rng === "1D" || ts.length < 2) return;
  const lo = ts[0], hi = ts.at(-1) + 864e5, used = {};
  let out = "";
  for (const m of marks) {
    const t = Date.parse(String(m.date).slice(0, 10) + "T16:00:00");
    if (!(t >= lo && t <= hi)) continue;
    let i = 0, best = Infinity;
    for (let k = 0; k < ts.length; k++) { const dd = Math.abs(ts[k] - t); if (dd < best) { best = dd; i = k; } }
    const [glyph, color] = MARK_STYLE[m.kind] || ["•", "#888"], x = xs[i];
    const onLine = m.kind === "IB" || m.kind === "IS";
    const slot = (used[(onLine ? "L" : "B") + Math.round(x / 6)] = (used[(onLine ? "L" : "B") + Math.round(x / 6)] || 0) + 1) - 1;
    const y = onLine ? yAt(i) + (m.kind === "IB" ? 11 : -4) + slot * (m.kind === "IB" ? 8 : -8) : h - 20 - slot * 10;
    out += `<text class="mk" x="${x}" y="${y}" fill="${color}" font-size="10" text-anchor="middle"><title>${esc(String(m.date).slice(0, 10) + " · " + m.label)}</title>${glyph}</text>`;
  }
  svg.insertAdjacentHTML("beforeend", out);
}

function sparkline(vals, w = 80, h = 16) {
  vals = (vals || []).filter((v) => v != null);
  if (vals.length < 2) return "";
  const lo = Math.min(...vals), hi = Math.max(...vals), r = hi - lo || 1;
  const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / r) * (h - 4)).toFixed(1)}`).join(" ");
  const c = vals.at(-1) >= vals[0] ? "var(--pos)" : "var(--neg)";
  return `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.3" vector-effect="non-scaling-stroke"/></svg>`;
}
const sparkRange = () => lsGet("rng:spark") || "1D";
const sparkBar = () => `<div class="pb sparkbar rbar" style="padding-bottom:0">${RANGES.map((x) => `<button data-r="${x}" class="${x === sparkRange() ? "on" : ""}">${x}</button>`).join("")}</div>`;

/** Fill every chart and sparkline under root. A late reply for a range the user already left is dropped. */
async function hydrate(root = document) {
  const jobs = [...root.querySelectorAll(".chart")].map(async (el) => {
    const sym = el.dataset.sym, rng = el.dataset.rng, cmps = canCompare(sym) ? cmpList(el.dataset.key) : [];
    try {
      const [d, ...cd] = await Promise.all([getSeries(sym, rng), ...cmps.map((c) => getSeries(c, rng).catch(() => null))]);
      if (el.dataset.rng === rng) drawSeries(el, d, cmps.map((c, i) => ({ sym: c, d: cd[i] })));
    }
    catch (e) { if (el.dataset.rng === rng) el.querySelector(".cplot").innerHTML = `<span class="neg">Chart failed: ${esc(e.message)}</span>`; }
  });
  for (const el of root.querySelectorAll(".spark[data-sym]")) {
    getSeries(el.dataset.sym, sparkRange()).then((d) => { el.innerHTML = sparkline((d.points || []).map((p) => p[1])); }).catch(() => {});
  }
  await Promise.all(jobs);
}

// Range buttons (capture phase, so a click on a range never opens a drill-down underneath it).
document.addEventListener("click", (e) => {
  const b = e.target.closest(".rbar button[data-r]");
  const box = b && b.closest(".chart,.sparkbar");
  if (!box) return; // other button rows (tabs, the TWR chart) handle their own clicks
  e.stopPropagation();
  const r = b.dataset.r;
  box.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  if (box.classList.contains("sparkbar")) { lsSet("rng:spark", r); hydrate(box.closest(".p") || document); return; }
  box.dataset.rng = r;
  lsSet("rng:" + box.dataset.key, r);
  box.querySelector(".cplot").innerHTML = '<span class="dim">loading...</span>';
  hydrate(box.parentElement);
}, true);

SUGGEST_ACT.cmp = (inp, sym) => {
  const box = inp.closest(".chart");
  if (box && sym !== box.dataset.sym) cmpSet(box, [...new Set([...cmpList(box.dataset.key), sym])]);
};
// Compare controls: Enter in the box or "vs SPY" adds, × removes. Capture phase so no drill-down opens underneath.
function cmpSet(box, list) {
  lsSet("cmp:" + box.dataset.key, list.slice(0, 3).join(","));
  const bar = box.querySelector(".cmpbox");
  if (bar) bar.outerHTML = cmpBar(box.dataset.sym, box.dataset.key);
  box.querySelector(".cplot").innerHTML = '<span class="dim">loading...</span>';
  hydrate(box.parentElement);
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-cmpadd],[data-cmpdel]"), box = a && a.closest(".chart");
  if (!box) { if (e.target.closest(".cmpin")) e.stopPropagation(); return; }
  e.stopPropagation();
  const list = cmpList(box.dataset.key);
  cmpSet(box, a.dataset.cmpadd ? [...new Set([...list, a.dataset.cmpadd])] : list.filter((x) => x !== a.dataset.cmpdel));
}, true);
document.addEventListener("keydown", (e) => {
  const inp = e.target.closest && e.target.closest(".cmpin");
  if (!inp) return;
  e.stopPropagation();  // typing here must not trigger page shortcuts
  if (e.key !== "Enter") return;
  const box = inp.closest(".chart"), v = inp.value.trim().toUpperCase();
  if (!box || !/^[A-Z0-9.^=\-]{1,12}$/.test(v) || v === box.dataset.sym) return;
  cmpSet(box, [...new Set([...cmpList(box.dataset.key), v])]);
}, true);

// Esc clears an active measure first (capture phase, so it does not also close the pop-up); the next Esc closes it.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const active = [...document.querySelectorAll(".chart,.perfchart")].filter((el) => el.querySelector(".meas:not([hidden])"));
  if (!active.length) return;
  active.forEach((el) => el._clearMeasure && el._clearMeasure());
  e.stopImmediatePropagation();
  e.preventDefault();
}, true);
