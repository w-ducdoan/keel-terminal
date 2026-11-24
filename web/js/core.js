/* Keel core: state, formatting and API helpers shared by every page. Plain JS, no build step. */
"use strict";

let S = null;                       // latest /api/state
const $ = (id) => document.getElementById(id);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cls = (x) => (x == null ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "");
const n = (x, d = 2) => (x == null || Number.isNaN(+x) ? "--" : Number(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (x, d = 2) => (x == null ? "--" : "$" + n(x, d));
const smoney = (x, d = 2) => (x == null ? "--" : (x > 0 ? "+" : x < 0 ? "-" : "") + money(Math.abs(x), d));
const pct = (x, d = 1) => (x == null ? "--" : (x * 100).toFixed(d) + "%");
const spct = (x, d = 2) => (x == null ? "--" : (x > 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
const ago = (iso) => {
  if (!iso) return "--";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 90 ? Math.max(0, Math.round(s)) + "s ago" : s < 5400 ? Math.round(s / 60) + "m ago" : Math.round(s / 3600) + "h ago";
};
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };

async function fetchState() {
  const r = await fetch("/api/state", { cache: "no-store" });
  if (!r.ok) throw new Error("state " + r.status);
  return r.json();
}
async function post(url) {
  const r = await fetch(url, { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
/** Edge resize for grid panels, splitter style.
    Side edge (e / w): if a panel sits right against that edge in the same row, the two trade width so the row
    keeps its total and never wraps; with no neighbor there, the panel alone grows or shrinks.
    Bottom edge (s): every panel in the row takes the new height, so the row stays flush. se = both.
    edgeStart(e, panel, grid, minH, onDone([{panel, span, h}])) begins a drag; move and release listeners are shared. */
const EDGES_HTML = '<div class="rz rz-w" data-dir="w" title="Drag to resize"></div><div class="rz rz-e" data-dir="e" title="Drag to resize"></div><div class="rz rz-s" data-dir="s" title="Drag to resize"></div><div class="rz rz-se" data-dir="se" title="Drag to resize"></div>';
const EDGE_CURSOR = { e: "ew-resize", w: "ew-resize", s: "ns-resize", se: "nwse-resize" };
const MIN_SPAN = 3;
let edgeDrag = null;
const spanOf = (el, col) => +(/span\s+(\d+)/.exec(el.style.gridColumn || "") || [])[1] || Math.max(1, Math.round((el.getBoundingClientRect().width + 6) / (col + 6)));
function rowMates(panel, grid) {
  const top = panel.getBoundingClientRect().top;
  return [...grid.children].filter((x) => x !== panel && x.classList.contains("p") && x.offsetParent !== null
    && Math.abs(x.getBoundingClientRect().top - top) < 6);
}
function edgeStart(e, panel, grid, minH, onDone) {
  const dir = e.target.dataset.dir || "se", gw = grid.getBoundingClientRect().width, r = panel.getBoundingClientRect();
  const col = (gw - 11 * 6) / 12, mates = rowMates(panel, grid);
  // The panel touching the dragged side, if any (gap between grid cells is 6px)
  const nb = dir === "e" || dir === "se" ? mates.find((x) => Math.abs(x.getBoundingClientRect().left - r.right) < 16)
    : dir === "w" ? mates.find((x) => Math.abs(x.getBoundingClientRect().right - r.left) < 16) : null;
  edgeDrag = { dir, panel, grid, x: e.clientX, y: e.clientY, w: r.width, h: r.height, col, minH, onDone, mates,
               s0: spanOf(panel, col), nb, n0: nb ? spanOf(nb, col) : 0, span: null, nspan: null, hh: null };
  window.mkDragging = true;
  document.body.style.cursor = EDGE_CURSOR[dir];
  document.body.classList.add("resizing");
  e.preventDefault();
}
document.addEventListener("mousemove", (e) => {
  const d = edgeDrag;
  if (!d) return;
  if (d.dir !== "s") {
    const dx = (e.clientX - d.x) * (d.dir === "w" ? -1 : 1);
    let span = Math.round((d.w + dx + 6) / (d.col + 6));
    if (d.nb) {  // splitter: the neighbor gives up exactly what this panel takes
      const total = d.s0 + d.n0;
      span = Math.max(MIN_SPAN, Math.min(total - MIN_SPAN, span));
      d.nspan = total - span;
      d.nb.style.gridColumn = `span ${d.nspan}`;
    } else span = Math.max(MIN_SPAN, Math.min(12, span));
    d.span = span;
    d.panel.style.gridColumn = `span ${span}`;
  }
  if (d.dir === "s" || d.dir === "se") {
    d.hh = Math.max(d.minH, Math.min(2000, d.h + (e.clientY - d.y)));
    for (const x of [d.panel, ...d.mates]) { x.style.height = d.hh + "px"; x.classList.add("fixedh"); }
  }
});
document.addEventListener("mouseup", () => {
  const d = edgeDrag;
  if (!d) return;
  edgeDrag = null; window.mkDragging = false; document.body.style.cursor = ""; document.body.classList.remove("resizing");
  const hh = d.hh != null ? Math.round(d.hh) : null, out = new Map();
  const put = (panel, span, h) => { const o = out.get(panel) || { panel, span: null, h: null }; if (span != null) o.span = span; if (h != null) o.h = h; out.set(panel, o); };
  if (d.span != null) put(d.panel, d.span, null);
  if (d.nb && d.nspan != null) put(d.nb, d.nspan, null);
  if (hh != null) for (const x of [d.panel, ...d.mates]) put(x, null, hh);
  if (out.size) d.onDone([...out.values()]);
});

/** Ticker suggestions for small inputs (chart compare, RV peers, SPLC links), the same search as the command bar.
    Mark an input with data-suggest="<act>"; SUGGEST_ACT[act](input, symbol) runs on a pick. Enter picks the
    highlighted row, or the top match when what was typed is a name rather than a ticker. */
const SUGGEST_ACT = {};
let sugBox = null, sugFor = null, sugRows = [], sugIdx = -1, sugTimer = null, sugSeq = 0;
function sugClose() { if (sugBox) sugBox.hidden = true; sugFor = null; sugRows = []; sugIdx = -1; }
function sugDraw() {
  if (!sugFor || !sugRows.length) { if (sugBox) sugBox.hidden = true; return; }
  if (!sugBox) { sugBox = document.createElement("div"); sugBox.className = "sugbox"; document.body.appendChild(sugBox); }
  const r = sugFor.getBoundingClientRect();
  Object.assign(sugBox.style, { left: `${r.left + window.scrollX}px`, top: `${r.bottom + window.scrollY + 2}px`, minWidth: `${Math.max(260, r.width)}px` });
  sugBox.innerHTML = sugRows.map((x, i) => `<div class="sugrow ${i === sugIdx ? "on" : ""}" data-i="${i}"><b>${esc(x.symbol)}</b> <span>${esc(x.name || "")}</span><i>${esc(x.exchange || "")}${x.type && x.type !== "EQUITY" ? " · " + esc(x.type) : ""}</i></div>`).join("");
  sugBox.hidden = false;
}
function sugPick(i) {
  const inp = sugFor, x = sugRows[i];
  if (x?._ph) return;  // "Searching..." is not a choice
  sugClose();
  if (!inp || !x) return;
  inp.value = inp.dataset.suggestSrc === "funds" ? x.name : x.symbol;
  (SUGGEST_ACT[inp.dataset.suggest] || (() => {}))(inp, x.symbol, x);
}
document.addEventListener("input", (e) => {
  const inp = e.target.closest && e.target.closest("input[data-suggest]");
  if (!inp) return;
  const q = inp.value.trim(), seq = ++sugSeq;
  clearTimeout(sugTimer);
  if (q.length < 1) { sugClose(); return; }
  if (inp.dataset.suggestSrc === "funds" && q.length >= 2) {  // SEC fund search can take a few seconds: say so at once
    sugFor = inp; sugIdx = -1; sugRows = [{ symbol: "...", name: "Searching SEC for funds that file 13Fs", exchange: "", _ph: true }]; sugDraw();
  }
  sugTimer = setTimeout(async () => {
    try {
      const src = inp.dataset.suggestSrc === "funds" ? "/api/13f/funds/search?q=" : "/api/lookup?q=";
      const r = await (await fetch(src + encodeURIComponent(q))).json();
      if (seq !== sugSeq) return;  // a newer keystroke already asked
      sugFor = inp; sugIdx = -1;
      sugRows = (r.results || []).slice(0, 8).map((x) => (inp.dataset.suggestSrc === "funds"
        ? { ...x, symbol: (x.tickers || [])[0] || "13F", exchange: [x.where, "CIK " + x.cik, x.quarter ? "13F " + x.quarter : ""].filter(Boolean).join(" · ") } : x));
      sugDraw();
    } catch { /* suggestions are a convenience */ }
  }, 180);
});
document.addEventListener("keydown", (e) => {
  const inp = e.target.closest && e.target.closest("input[data-suggest]");
  if (!inp || !sugBox || sugBox.hidden || sugFor !== inp) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); e.stopImmediatePropagation();
    sugIdx = (sugIdx + (e.key === "ArrowDown" ? 1 : -1) + sugRows.length) % sugRows.length; sugDraw();
  } else if (e.key === "Enter") {
    const typed = inp.dataset.suggestSrc === "funds" ? "" : inp.value.trim().toUpperCase();
    // A typed ticker that is in the list goes through as typed; a name picks the top match
    const i = sugIdx >= 0 ? sugIdx : sugRows.findIndex((x) => x.symbol === typed) >= 0 ? sugRows.findIndex((x) => x.symbol === typed) : 0;
    e.preventDefault(); e.stopImmediatePropagation();
    sugPick(i);
  } else if (e.key === "Escape") { e.stopImmediatePropagation(); sugClose(); }
}, true);
document.addEventListener("mousedown", (e) => {
  const row = e.target.closest && e.target.closest(".sugrow");
  if (row) { e.preventDefault(); e.stopPropagation(); sugPick(+row.dataset.i); return; }
  if (sugBox && !sugBox.hidden && !(e.target.closest && e.target.closest("input[data-suggest]"))) sugClose();
}, true);

const openPath = (p) => (p ? post("/api/open?path=" + encodeURIComponent(p)).catch((e) => alert(e.message)) : null);

// Lookups used by several pages
const posBy = (t) => (S?.positions || []).find((p) => p.ticker === t);
const watchBy = (t) => (S?.watchlist || []).find((w) => w.ticker === t);
const capFor = (sector) => {
  const c = S?.caps || {};
  if (sector === "Consumer Discretionary") return c.consumer_discretionary;
  if (sector === "Hedge") return c.hedge_sleeve_cap;
  if (sector === "ETF") return null;
  return c.sector;
};
const SEV = { act: "var(--neg)", review: "var(--amber)", watch: "var(--blue)" };
