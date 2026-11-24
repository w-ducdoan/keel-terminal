/* MKTS: a Launchpad-style grid of market panels. Drag a corner to resize, drag a title to move,
   toggle panels from the toolbar. The layout is saved in this browser. */
"use strict";

let MK = null, mkAt = 0, mkBusy = false;
async function loadMkts(force = false) {
  if (mkBusy || (!force && MK && Date.now() - mkAt < 60e3)) return MK;
  mkBusy = true;
  try { MK = await (await fetch("/api/markets" + (force ? "?fresh=true" : ""))).json(); mkAt = Date.now(); }
  catch (e) { MK = MK || { error: e.message }; }
  mkBusy = false;
  if (page === "MKTS" && !window.mkDragging) render();
  return MK;
}
setInterval(() => { if (page === "MKTS") loadMkts(); }, 60e3);

const q = (s) => MK?.quotes?.rows?.[s] || {};
const lab = (s) => MK?.labels?.[s] || s;
const bp = (x, d = 0) => (x == null ? "--" : (x > 0 ? "+" : "") + (x * 100).toFixed(d));
const daysTo = (iso) => (iso ? Math.round((new Date(iso + "T00:00:00") - new Date(new Date().toDateString())) / 864e5) : null);

function rangeBar(lo, hi, v, w = 54) {
  if (lo == null || hi == null || v == null || hi === lo) return "";
  const x = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  return `<span class="rng" style="width:${w}px" title="52w ${n(lo)} - ${n(hi)}"><i style="left:${(x * 100).toFixed(0)}%"></i></span>`;
}
function quoteRow(s, isYield = false) {
  const r = q(s);
  if (r.error || r.last == null) return `<tr><td>${esc(lab(s))}</td><td colspan="6" class="dim">no data</td></tr>`;
  const dp = Math.abs(r.last) >= 1000 ? 2 : Math.abs(r.last) < 10 ? 4 : 2;
  const sign = r.chg > 0 ? "+" : "";
  return `<tr data-drill="mkt:${esc(s)}"><td>${esc(lab(s))}</td><td style="width:70px" class="spk">${sparkline(r.spark, 70, 14)}</td>
    <td class="num">${n(r.last, isYield ? 3 : dp)}${isYield ? "%" : ""}</td><td class="num ${cls(r.chg)}">${r.chg == null ? "--" : isYield ? sign + n(r.chg * 100, 1) + "bp" : sign + n(r.chg, dp)}</td>
    <td class="num ${cls(r.chg_pct)}">${isYield ? "" : spct(r.chg_pct)}</td><td class="num ${cls(r.ytd)}">${spct(r.ytd, 1)}</td>
    <td>${rangeBar(r.lo52, r.hi52, r.last)}</td><td class="num dim">${esc(mkTime(r.time))}</td></tr>`;
}
function mkTime(iso) {
  if (!iso) return "";
  const d = new Date(iso), today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }) : d.toLocaleDateString([], { month: "short", day: "numeric" });
}
const QHEAD = `<tr><th>NAME</th><th>2 DAY</th><th class="num">LAST</th><th class="num">CHG</th><th class="num">%CHG</th><th class="num">YTD</th><th>52W</th><th class="num">TIME</th></tr>`;
const group = (id) => (MK?.groups || []).find((g) => g.id === id);
function groupRows(id, title) {
  const g = group(id);
  if (!g) return "";
  return `${title ? `<tr class="sec"><td colspan="8">${esc(title)}</td></tr>` : ""}${g.rows.map((s) => quoteRow(s, g.yield)).join("")}`;
}

// ---------- panels ----------
const MPANELS = {
  wei: { title: "WEI · WORLD EQUITY INDICES", span: 6, h: 470, render: () => `<table class="mq">${QHEAD}${groupRows("americas", "AMERICAS")}${groupRows("emea", "EMEA")}${groupRows("asia", "ASIA-PACIFIC")}</table>` },
  fx: { title: "FX · CURRENCIES", span: 6, h: 330, render: () => `<table class="mq">${QHEAD}${groupRows("fx")}</table><div class="cnote">USD/xxx rows rise when the dollar strengthens.</div>` },
  rates: { title: "RATES · TREASURY CURVE & MONEY MARKETS", span: 6, h: 520, render: panelRates },
  credit: { title: "CRDT · CREDIT SPREADS", span: 6, h: 330, render: panelCredit },
  cmdty: { title: "CMDTY · COMMODITIES", span: 6, h: 380, render: () => `<table class="mq">${QHEAD}${groupRows("energy", "ENERGY")}${groupRows("metals", "METALS")}${groupRows("ags", "AGRICULTURE")}</table>` },
  mov: { title: "MOV · MOVERS", span: 6, h: 380, render: panelMovers },
  econ: { title: "ECO · ECONOMIC DATA", span: 6, h: 360, render: panelEcon },
  news: { title: "NEWS · MARKET WIRE", span: 6, h: 470, render: panelNews },
};
const DEFAULT_ORDER = ["wei", "rates", "fx", "credit", "cmdty", "mov", "econ", "news"];

function getLayout() {
  let lay;
  try { lay = JSON.parse(lsGet("mkts:layout") || "null"); } catch { lay = null; }
  if (!Array.isArray(lay)) lay = [];
  const known = new Set(lay.map((x) => x.id));
  for (const id of DEFAULT_ORDER) if (!known.has(id)) lay.push({ id, span: MPANELS[id].span, h: MPANELS[id].h, hidden: false });
  return lay.filter((x) => MPANELS[x.id]);
}
const saveLayout = (lay) => lsSet("mkts:layout", JSON.stringify(lay));
function resetLayout() { lsSet("mkts:layout", ""); render(); }
function togglePanel(id) { const lay = getLayout(); const p = lay.find((x) => x.id === id); p.hidden = !p.hidden; saveLayout(lay); render(); }

function pageMkts() {
  setTimeout(() => loadMkts(), 0);
  const lay = getLayout();
  const tb = `<div class="mtool"><span class="dim">PANELS</span>${lay.map((x) => `<button class="${x.hidden ? "" : "on"}" onclick="togglePanel('${x.id}')">${x.id.toUpperCase()}</button>`).join("")}
    <button onclick="resetLayout()">RESET LAYOUT</button><button onclick="loadMkts(true)">REFRESH</button>
    <span class="dim" style="margin-left:auto">drag a title to move · drag the corner to resize${MK?.as_of?.quotes ? " · quotes " + esc(ago(MK.as_of.quotes)) : ""}</span></div>`;
  if (!MK) return tb + `<div class="loading">Loading world markets (first load takes about 10 seconds)...</div>`;
  if (MK.error) return tb + `<div class="loading neg">Markets unavailable: ${esc(MK.error)}</div>`;
  const panels = lay.filter((x) => !x.hidden).map((x) => {
    let body;
    try { body = MPANELS[x.id].render(); } catch (e) { body = `<span class="neg">${esc(e.message)}</span>`; }
    return `<div class="p mp" data-pid="${x.id}" style="grid-column:span ${x.span};height:${x.h}px">
      <h3 draggable="true" class="mdrag">${MPANELS[x.id].title}<i>${esc(x.id.toUpperCase())}</i></h3><div class="pb mbody">${body}</div>${EDGES_HTML}</div>`;
  }).join("");
  return tb + `<div class="mgrid" id="mgrid">${panels}</div>`;
}

function panelRates() {
  const r = MK.rates || {};
  if (r.error) return `<span class="neg">${esc(r.error)}</span>`;
  const cv = (r.curve || []).filter((c) => c.now != null);
  let chart = "";
  if (cv.length > 2) {
    const w = 640, h = 170, padL = 30, padR = 10, all = cv.flatMap((c) => [c.now, c.w1, c.m1, c.y1]).filter((v) => v != null);
    const lo = Math.min(...all) - 0.05, hi = Math.max(...all) + 0.05, X = (i) => padL + (i / (cv.length - 1)) * (w - padL - padR), Y = (v) => h - 18 - ((v - lo) / (hi - lo)) * (h - 28);
    const ln = (k, c, wd, dash = "") => `<polyline points="${cv.map((x, i) => (x[k] == null ? "" : `${X(i)},${Y(x[k])}`)).join(" ")}" fill="none" stroke="${c}" stroke-width="${wd}" ${dash ? `stroke-dasharray="${dash}"` : ""}/>`;
    chart = `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="curve">
      ${[lo, (lo + hi) / 2, hi].map((v) => `<line x1="${padL}" x2="${w - padR}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1f1f1f"/><text x="0" y="${Y(v) + 3}" fill="#8a8a8a" font-size="9">${v.toFixed(2)}</text>`).join("")}
      ${ln("y1", "#555", 1, "2 3")}${ln("m1", "#4da3ff", 1, "4 3")}${ln("w1", "#9aa", 1)}${ln("now", "var(--amber)", 2)}
      ${cv.map((c, i) => `<circle data-drill="fredx:DGS:${esc(c.series)}|${esc(c.tenor)} Treasury yield" cx="${X(i)}" cy="${Y(c.now)}" r="4" fill="var(--amber)" style="cursor:pointer"><title>${c.tenor} ${c.now}%</title></circle><text x="${X(i)}" y="${h - 4}" fill="#aaa" font-size="9" text-anchor="middle">${c.tenor}</text>`).join("")}</svg>
      <div class="cnote"><span class="amb">&#9632;</span> now (${esc(cv[0].date || "")}) &nbsp;<span style="color:#9aa">&#9632;</span> 1W ago &nbsp;<span style="color:#4da3ff">&#9632;</span> 1M ago &nbsp;<span style="color:#777">&#9632;</span> 1Y ago · click a point for its chart</div>`;
  }
  const tbl = `<table><tr><th>TENOR</th><th class="num">YIELD</th><th class="num">1W bp</th><th class="num">1M bp</th><th class="num">1Y bp</th></tr>
    ${cv.map((c) => `<tr data-drill="fredx:DGS:${esc(c.series)}|${esc(c.tenor)} Treasury yield"><td class="amb">${c.tenor}</td><td class="num">${n(c.now, 2)}</td><td class="num ${cls(c.now - c.w1)}">${c.w1 == null ? "--" : bp(c.now - c.w1)}</td><td class="num ${cls(c.now - c.m1)}">${c.m1 == null ? "--" : bp(c.now - c.m1)}</td><td class="num ${cls(c.now - c.y1)}">${c.y1 == null ? "--" : bp(c.now - c.y1)}</td></tr>`).join("")}</table>`;
  const money = `<table><tr class="sec"><td colspan="6">MONEY MARKETS & REAL RATES (FRED, daily)</td></tr><tr><th>SERIES</th><th class="num">LAST</th><th class="num">1D bp</th><th class="num">1M bp</th><th>1Y RANGE</th><th class="num">AS OF</th></tr>
    ${(r.money || []).map((m) => m.error ? "" : `<tr data-drill="fredx:LVL:${esc(m.series)}|${esc(m.label)}"><td>${esc(m.label)}</td><td class="num">${n(m.last, 2)}</td><td class="num ${cls(m.d1)}">${bp(m.d1)}</td><td class="num ${cls(m.m1)}">${bp(m.m1)}</td><td>${rangeBar(m.lo1y, m.hi1y, m.last)}</td><td class="num dim">${esc((m.date || "").slice(5))}</td></tr>`).join("")}
    <tr class="sec"><td colspan="6">LIVE (Yahoo)</td></tr></table><table class="mq">${groupRows("rates_live")}</table>`;
  return chart + `<div class="two">${tbl}<div>${money}</div></div>`;
}

function panelCredit() {
  const sp = MK.rates?.spreads || [];
  const t = `<table><tr><th>SPREAD (bp)</th><th class="num">LAST</th><th class="num">1D</th><th class="num">1W</th><th class="num">1M</th><th>1Y RANGE</th><th class="num">1Y PCTL</th></tr>
    ${sp.map((s) => s.error ? "" : `<tr data-drill="fredx:BP:${esc(s.series)}|${esc(s.label)}"><td>${esc(s.label)}</td><td class="num"><b>${n(s.last, 0)}</b></td><td class="num ${cls(-s.d1)}">${s.d1 > 0 ? "+" : ""}${n(s.d1, 0)}</td><td class="num ${cls(-s.w1)}">${s.w1 > 0 ? "+" : ""}${n(s.w1, 0)}</td><td class="num ${cls(-s.m1)}">${s.m1 > 0 ? "+" : ""}${n(s.m1, 0)}</td><td>${rangeBar(s.lo1y, s.hi1y, s.last, 70)}</td><td class="num ${s.rank1y >= 0.9 ? "neg" : s.rank1y <= 0.1 ? "pos" : ""}">${pct(s.rank1y, 0)}</td></tr>`).join("")}</table>
    <div class="cnote">Wider spreads (red) mean credit stress. 1Y percentile: 100% = widest in a year. ICE BofA OAS via FRED, daily, about a day behind.</div>`;
  return t + `<table class="mq" style="margin-top:6px">${QHEAD}${groupRows("credit_etf", "CREDIT ETFs (live)")}</table>`;
}

function panelMovers() {
  const ps = [...(S.positions || [])].filter((p) => p.day_pct != null).sort((x, y) => y.day_pct - x.day_pct);
  const sec = [...(group("sectors")?.rows || [])].map((s) => ({ s, ...q(s) })).filter((x) => x.chg_pct != null).sort((x, y) => y.chg_pct - x.chg_pct);
  const mx = Math.max(...sec.map((x) => Math.abs(x.chg_pct)), 0.001);
  const bookRow = (p) => `<tr data-drill="tkr:${esc(p.ticker)}"><td class="amb">${esc(p.ticker)}</td><td class="num ${cls(p.day_pct)}">${spct(p.day_pct, 1)}</td><td class="num ${cls(p.day_pnl)}">${smoney(p.day_pnl)}</td></tr>`;
  return `<div class="two"><div><table><tr class="sec"><td colspan="3">BOOK LEADERS</td></tr>${ps.slice(0, 6).map(bookRow).join("")}<tr class="sec"><td colspan="3">BOOK LAGGARDS</td></tr>${ps.slice(-6).reverse().map(bookRow).join("")}</table></div>
    <div><table><tr class="sec"><td colspan="3">S&amp;P SECTORS TODAY (SPDR ETFs)</td></tr>${sec.map((x) => `<tr data-drill="mkt:${esc(x.s)}"><td>${esc(lab(x.s))}</td><td style="width:40%"><div style="position:relative;height:9px"><div style="position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:#444"></div><div style="position:absolute;top:0;height:9px;${x.chg_pct >= 0 ? "left:50%" : "right:50%"};width:${(Math.abs(x.chg_pct) / mx) * 50}%;background:${x.chg_pct >= 0 ? "var(--pos)" : "var(--neg)"}"></div></div></td><td class="num ${cls(x.chg_pct)}">${spct(x.chg_pct)}</td></tr>`).join("")}</table></div></div>`;
}

function panelEcon() {
  const rows = MK.economy?.rows || [];
  if (MK.economy?.error) return `<span class="neg">${esc(MK.economy.error)}</span>`;
  const fmt = (v, u) => (v == null ? "--" : n(v, u === "k" ? 0 : u === "" ? 1 : 2) + (u === "%" ? "%" : u === "k" ? "k" : ""));
  return `<table><tr><th>INDICATOR</th><th class="num">LATEST</th><th class="num">PRIOR</th><th class="num">YR AGO</th><th>TREND</th><th class="num">PERIOD</th><th class="num">NEXT</th></tr>
    ${[...rows].sort((a, b) => (a.next || "9") < (b.next || "9") ? -1 : 1).map((e) => {
      if (e.error) return `<tr><td>${esc(e.label)}</td><td colspan="6" class="dim">no data</td></tr>`;
      const dd = daysTo(e.next), soon = dd != null && dd <= 7;
      return `<tr data-drill="fredx:ECO:${esc(e.series)}~${esc(e.transform || "level")}*${e.scale || 1}|${esc(e.label)}"><td>${esc(e.label)}</td><td class="num"><b>${fmt(e.last, e.unit)}</b></td><td class="num dim">${fmt(e.prior, e.unit)}</td><td class="num dim">${fmt(e.yr_ago, e.unit)}</td>
        <td style="width:64px">${sparkline(e.spark, 64, 14)}</td><td class="num dim">${esc((e.period || "").slice(0, 7))}</td>
        <td class="num ${soon ? "amb" : "dim"}" title="${esc(e.release || "")}">${e.next ? esc(e.next.slice(5)) + (dd != null ? ` (${dd}d)` : "") : "--"}</td></tr>`;
    }).join("")}</table><div class="cnote">Sorted by next release. Amber = out within a week. FRED shows actuals only, so there is no consensus column.</div>`;
}

// ---------- drill-downs for MKTS ----------
const _mktDrill = DRILL.mkt;
DRILL.mkt = (sym) => {
  const r = q(sym);
  if (!r.last && _mktDrill) return _mktDrill(sym);
  const g = (MK?.groups || []).find((x) => x.rows.includes(sym)), isY = !!g?.yield;
  return { title: `${esc(lab(sym))} · ${esc(sym)}`, body: `<div class="dsec">${chartSlot(sym, "drill-mkt", 220, "1M")}</div>` + kv([
    ["Last", n(r.last, 4)], ["Change", isY ? `${n(r.chg * 100, 1)} bp` : `${n(r.chg, 4)} (${spct(r.chg_pct)})`, cls(r.chg)], ["Prior close", n(r.prev, 4)], ["YTD", spct(r.ytd, 1), cls(r.ytd)],
    ["52w high", n(r.hi52, 4)], ["52w low", n(r.lo52, 4)], ["From 52w high", spct(r.last / r.hi52 - 1, 1), "neg"], ["As of", esc(mkTime(r.time))]]) + `<div class="note">Source: Yahoo (delayed for some exchanges). Group: ${esc(g?.label || "")}.</div>` };
};
// fredx:<kind>:<id>[~transform][*scale]|<label>   kind = DGS (yield), LVL, BP (spread, x100), ECO
DRILL.fredx = (spec) => {
  const [head, label] = spec.split("|"), [kind, id] = [head.slice(0, head.indexOf(":")), head.slice(head.indexOf(":") + 1)];
  const sym = "FREDID:" + id + (kind === "BP" ? "*100" : "");
  const sid = id.split(/[~*]/)[0];
  const r = [...(MK?.rates?.money || []), ...(MK?.rates?.spreads || [])].find((x) => x.series === sid);
  const c = (MK?.rates?.curve || []).find((x) => x.series === sid), e = (MK?.economy?.rows || []).find((x) => x.series === sid);
  const facts = r ? kv([["Last", n(r.last, 2)], ["As of", esc(r.date)], ["1D", n(r.d1, 2)], ["1W", n(r.w1, 2)], ["1M", n(r.m1, 2)], ["1Y range", `${n(r.lo1y, 2)} - ${n(r.hi1y, 2)}`], ["1Y percentile", pct(r.rank1y, 0)], ["Percentile since " + esc(r.history_from || ""), pct(r.rank_all, 0)]])
    : c ? kv([["Now", n(c.now, 2) + "%"], ["As of", esc(c.date)], ["1W ago", n(c.w1, 2)], ["1M ago", n(c.m1, 2)], ["1Y ago", n(c.y1, 2)], ["1Y change", bp(c.now - c.y1) + " bp"]])
    : e ? kv([["Latest", n(e.last, 2) + " " + esc(e.unit || "")], ["Period", esc(e.period)], ["Prior", n(e.prior, 2)], ["Year ago", n(e.yr_ago, 2)], ["Release", esc(e.release || "")], ["Next release", esc(e.next || "--")]]) : "";
  const tf = (id.split("~")[1] || "level").split("*")[0], base = id.split(/[~*]/)[0];
  const tfBar = kind === "ECO" || kind === "LVL" ? `<div class="rbar" style="margin-bottom:4px">${[["level", "LEVEL"], ["yoy", "% Y/Y"], ["mom_pct", "% M/M"], ["mom_diff", "CHANGE"]]
    .map(([k, l]) => `<button class="${tf === k ? "on" : ""}" onclick="openDrill('fredx:${kind}:${base}${k === "level" ? "" : "~" + k}|${esc(String(label || "").replace(/'/g, ""))}')">${l}</button>`).join("")}</div>` : "";
  return { title: `${esc(label || sid)} · FRED ${esc(sid)}`, body: `${tfBar}<div class="dsec">${chartSlot(sym, "drill-fred-" + kind, 220, kind === "ECO" ? "5Y" : "1Y")}</div>${facts}<div class="note">Source: FRED (St. Louis Fed). <a class="amb" href="https://fred.stlouisfed.org/series/${encodeURIComponent(sid)}" target="_blank" rel="noopener">Open on FRED</a></div>` };
};

// ---------- resize and move ----------
document.addEventListener("mousedown", (e) => {
  const h = e.target.closest(".mp > .rz");
  if (!h) return;
  const panel = h.closest(".mp");
  edgeStart(e, panel, $("mgrid"), 140, (changes) => {
    const lay = getLayout();
    for (const c of changes) {
      const p = lay.find((x) => x.id === c.panel.dataset.pid);
      if (!p) continue;
      if (c.span != null) p.span = c.span;
      if (c.h != null) p.h = c.h;
    }
    saveLayout(lay);
  });
});
document.addEventListener("dragstart", (e) => {
  const t = e.target.closest?.(".mp .mdrag");
  if (!t) return;
  e.dataTransfer.setData("text/plain", t.closest(".mp").dataset.pid);
  e.dataTransfer.effectAllowed = "move";
  window.mkDragging = true;
});
document.addEventListener("dragover", (e) => { const p = e.target.closest?.(".mp"); if (p) { e.preventDefault(); p.classList.add("dropto"); } });
document.addEventListener("dragleave", (e) => { const p = e.target.closest?.(".mp"); if (p) p.classList.remove("dropto"); });
document.addEventListener("drop", (e) => {
  const target = e.target.closest?.(".mp");
  window.mkDragging = false;
  if (!target) return;
  e.preventDefault();
  const from = e.dataTransfer.getData("text/plain"), to = target.dataset.pid;
  if (!from || from === to) { render(); return; }
  const lay = getLayout(), fi = lay.findIndex((x) => x.id === from), item = lay.splice(fi, 1)[0];
  lay.splice(lay.findIndex((x) => x.id === to), 0, item);
  saveLayout(lay);
  render();
});
document.addEventListener("dragend", () => { window.mkDragging = false; document.querySelectorAll(".dropto").forEach((x) => x.classList.remove("dropto")); });

// ---------- NEWS: market-wide wire (/api/news) ----------
let NW = null, nwAt = 0, nwBusy = false;
async function loadNews(force = false) {
  if (nwBusy || (!force && NW && Date.now() - nwAt < 120e3)) return NW;
  nwBusy = true;
  try { NW = await (await fetch(`/api/news?days=${nwDays()}` + (force ? "&fresh=true" : ""))).json(); nwAt = Date.now(); }
  catch (e) { NW = NW || { ok: false, error: e.message }; }
  nwBusy = false;
  const el = document.querySelector("[data-newsbody]");
  if (el && page === "MKTS") el.innerHTML = newsBody();
  return NW;
}
setInterval(() => { if (page === "MKTS") loadNews(); }, 150e3);
let newsFilter = lsGet("news:filter") || "ALL";
function setNewsFilter(f) { newsFilter = f; lsSet("news:filter", f); const el = document.querySelector("[data-newsbody]"); if (el) el.innerHTML = newsBody(); }
const TCOL = { "Fed & rates": "#4da3ff", "US policy": "#c792ea", Geopolitics: "#ff4d4d", Energy: "#ffa028", Metals: "#e6c35c", Credit: "#ff7a59",
  Equities: "#2fd67b", "AI & tech": "#59d0ff", "FX & EM": "#9aa", Crypto: "#b388ff" };
function newsBody() {
  if (!NW) return '<span class="dim">Pulling the wire (a few seconds)...</span>';
  if (!NW.ok) return `<span class="neg">${esc(NW.error || "News unavailable")}</span>`;
  const items = NW.items || [];
  const isWsj = (s) => s.sources.some((x) => /^WSJ\b|Wall Street Journal/i.test(x));
  const shown = items.map((s, i) => ({ s, i })).filter(({ s }) => newsFilter === "ALL" || (newsFilter === "BOOK" ? s.book.length : newsFilter === "WSJ" ? isWsj(s) : s.topics.includes(newsFilter)));
  const counts = {}; items.forEach((s) => s.topics.forEach((t) => (counts[t] = (counts[t] || 0) + 1)));
  const live = Object.values(NW.sources || {}).filter((x) => x.ok).length, all = Object.keys(NW.sources || {}).length;
  const dg = (S.digest || [])[0] || {};
  return `<div class="nchips">${[["ALL", items.length], ["BOOK", items.filter((s) => s.book.length).length], ["WSJ", items.filter(isWsj).length], ...NW.topics.map((t) => [t, counts[t] || 0])]
      .map(([t, c]) => `<button class="${newsFilter === t ? "on" : ""}" onclick="setNewsFilter('${esc(t)}')" style="${TCOL[t] ? "border-left:3px solid " + TCOL[t] : ""}">${esc(t === "BOOK" ? "YOUR NAMES" : t)} <span class="dim">${c}</span></button>`).join("")}
      <span class="rbar" style="margin-left:auto">${nwDaysBar()}</span><span class="dim small" title="${esc(Object.entries(NW.sources || {}).filter(([, v]) => !v.ok).map(([k]) => k).join(", "))}">${live}/${all} sources live · ${esc(ago(NW.as_of))}</span></div>
    ${dg.headlines?.length && newsFilter === "ALL" ? `<details class="ndig"><summary class="sec2">YOUR DIGEST · ${esc(dg.date || "")}</summary>${dg.headlines.map((h, i) => `<div class="head" data-drill="news:${i}">${esc(h.slice(0, 220))}</div>`).join("")}</details>` : ""}
    ${shown.map(({ s, i }) => `<div class="nrow" data-drill="wire:${i}"><span class="nt">${esc(newsWhen(s.time))}</span>
      <span class="ntags">${s.topics.map((t) => `<i style="background:${TCOL[t] || "#555"}" title="${esc(t)}"></i>`).join("")}</span>
      <span class="nh">${s.book.length ? `<b class="nbook">${esc(s.book.join(" "))}</b> ` : ""}${isWsj(s) ? '<span class="dtag" style="color:#d8c8a0;border-color:#d8c8a0">WSJ</span> ' : ""}${esc(s.title)}</span><span class="ns">${esc(s.sources.slice(0, 2).join(", "))}</span></div>`).join("") || '<span class="dim">Nothing in this filter.</span>'}`;
}
function panelNews() {
  if (!NW) setTimeout(() => loadNews(), 0);
  return `<div data-newsbody>${newsBody()}</div>`;
}
DRILL.wire = (i) => {
  const s = (NW?.items || [])[+i];
  if (!s) return null;
  return { title: `NEWS · ${esc(s.sources.join(", "))}`, body: `<div class="note" style="color:var(--txt)"><b class="amb">${esc(s.title)}</b>\n${esc(s.time ? new Date(s.time).toLocaleString("en-US", { timeZone: "America/New_York" }) + " ET" : "")}  ${s.topics.map((t) => esc(t)).join(" · ")}\n\n${esc(s.summary || "")}</div>
    <div class="dsec" style="display:flex;gap:6px;flex-wrap:wrap">${s.url ? `<a class="mini" href="${esc(s.url)}" target="_blank" rel="noopener">Read the story</a>` : ""}${s.book.map((t) => `<button class="mini" data-drill="tkr:${esc(t)}">Open ${esc(t)}</button>`).join("")}</div>` };
};
