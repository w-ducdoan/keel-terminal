/* Expanded FA: full statements, ratios and valuation history from SEC filings (/api/sec/<T>).
   Opens over the page (double-click the FA panel title, the EXPAND button, or Shift+F on DES).
   Drag the title bar to move, the corner to resize, ⛶ for full window, A−/A+ or Ctrl −/+ to zoom, Esc to close. */
"use strict";

const FA = { t: null, d: null, tab: lsGet("fa:x:tab") || "OVERVIEW", per: lsGet("fa:x:per") || "A", common: false, yoy: false,
  zoom: +(lsGet("fa:x:zoom") || 1), full: false, sel: null, q: "", unit: lsGet("fa:x:unit") || "M" };
const FA_TABS = ["OVERVIEW", "INCOME", "BALANCE", "CASH FLOW", "RATIOS", "VALUATION", "ALL LINES"];

async function openFA(t) {
  FA.t = t; FA.d = null; FA.sel = null;
  let w = $("fawin");
  if (!w) {
    w = document.createElement("div");
    w.id = "fawin";
    w.className = "fawin";
    document.body.appendChild(w);
  }
  w.hidden = false;
  drawFA();
  try {
    FA.d = await (await fetch("/api/sec/" + encodeURIComponent(t))).json();
  } catch (e) { FA.d = { ok: false, error: e.message }; }
  if (FA.t === t) drawFA();
}
function closeFA() { const w = $("fawin"); if (w) w.hidden = true; }
const faOpen = () => $("fawin") && !$("fawin").hidden;

// ---------- formatting ----------
const FA_STMT = { INCOME: "income", BALANCE: "balance", "CASH FLOW": "cashflow" };
function faMoney(v, tag) {
  if (v == null) return "";
  if (/EarningsPerShare|PerShare/.test(tag || "")) return v < 0 ? `(${n(-v, 2)})` : n(v, 2);
  const div = FA.unit === "B" ? 1e9 : 1e6, x = v / div;
  const s = n(Math.abs(x), FA.unit === "B" ? 2 : Math.abs(x) >= 100 ? 0 : 1);
  return v < 0 ? `(${s})` : s;
}
function faFmt(v, fmt) {
  if (v == null) return "";
  if (fmt === "pct") return v < 0 ? `(${(-v * 100).toFixed(1)}%)` : (v * 100).toFixed(1) + "%";
  if (fmt === "x") return n(v, 2) + "x";
  if (fmt === "d") return n(v, 0) + "d";
  if (fmt === "big") return faMoney(v);
  return n(v, 2);
}
const faLab = (e) => (FA.per === "A" ? "FY" + e.slice(2, 4) + " " + ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+e.slice(5, 7)]
  : e.slice(0, 7));

// ---------- window ----------
function drawFA() {
  const w = $("fawin");
  if (!w) return;
  w.classList.toggle("full", FA.full);
  w.style.setProperty("--faz", FA.zoom);
  const d = FA.d;
  const head = `<div class="fahead" id="fahead"><b class="amb">FA · ${esc(FA.t)}</b> <span>${esc(d?.entity || "")}</span>${d?.standard === "IFRS" ? ` <span class="dtag">IFRS · ${esc(d.currency)}</span>` : ""}
      <span class="dim small">${d?.ok ? `SEC ${d.standard === "IFRS" ? "20-F" : "10-K / 10-Q"} XBRL · CIK ${esc(d.cik)}` : ""}</span>
      <span style="margin-left:auto;display:flex;gap:4px;align-items:center" class="rbar">
        <button class="${FA.per === "A" ? "on" : ""}" onclick="faSet('per','A')">ANNUAL</button><button class="${FA.per === "Q" ? "on" : ""}" onclick="faSet('per','Q')">QUARTERLY</button>
        <button class="${FA.common ? "on" : ""}" onclick="faSet('common',!FA.common)" title="Each line as % of revenue (income, cash flow) or % of total assets (balance sheet)">% COMMON</button>
        <button class="${FA.yoy ? "on" : ""}" onclick="faSet('yoy',!FA.yoy)" title="Year-over-year change of each line">YoY</button>
        <button onclick="faSet('unit', FA.unit === 'M' ? 'B' : 'M')" title="Units">${esc(d?.currency && d.currency !== "USD" ? d.currency + " " : "$")}${FA.unit}</button>
        <button onclick="faZoom(-0.1)" title="Zoom out (Ctrl -)">A−</button><button onclick="faZoom(0.1)" title="Zoom in (Ctrl +)">A+</button>
        <button onclick="faCSV()" title="Download this table as CSV">CSV</button>
        <button onclick="faSet('full',!FA.full)" title="Full window">⛶</button><button onclick="closeFA()" title="Close (Esc)">ESC</button></span></div>
    <div class="rbar fatabs">${FA_TABS.map((x) => `<button class="${x === FA.tab ? "on" : ""}" onclick="faSet('tab','${x}')">${x}</button>`).join("")}
      ${FA.tab === "ALL LINES" ? `<input class="wiin" style="width:220px;margin-left:8px" placeholder="filter lines (e.g. lease, tax)" value="${esc(FA.q)}" oninput="FA.q=this.value;faBody()">` : ""}</div>`;
  w.innerHTML = head + `<div class="fabody" id="fabody"></div>`;
  faBody();
  faDrag();
}
function faSet(k, v) { FA[k] = v; if (["tab", "per", "unit"].includes(k)) lsSet("fa:x:" + k, v); FA.sel = k === "tab" ? null : FA.sel; drawFA(); }
function faZoom(dz) { FA.zoom = Math.max(0.6, Math.min(2, +(FA.zoom + dz).toFixed(2))); lsSet("fa:x:zoom", FA.zoom); $("fawin")?.style.setProperty("--faz", FA.zoom); }

function faBody() {
  const el = $("fabody"), d = FA.d;
  if (!el) return;
  if (!d) { el.innerHTML = '<div class="loading">Pulling every 10-K and 10-Q number from SEC.gov (first time about 3-5 seconds)...</div>'; return; }
  if (!d.ok) {
    el.innerHTML = `<div class="note neg" style="white-space:normal">${esc(d.error)}</div>` + (d.needs_contact ? `<div class="note" style="white-space:normal">SEC.gov requires a contact email in every automated request. Set yours once in PowerShell, then reopen this window:<pre class="cmdline">Set-Content -Path "<your Keel folder>\\.secrets\\sec_user_agent" -Value "Keel personal research tool YOUR_EMAIL"</pre></div>` : "");
    return;
  }
  const ends = d.periods[FA.per] || [];
  let rows = [];
  if (FA_STMT[FA.tab]) rows = d.statements[FA_STMT[FA.tab]].map((r) => ({ ...r, kind: "money" }));
  else if (FA.tab === "RATIOS") rows = Object.entries(d.ratios).flatMap(([g, rs]) => [{ group: g }, ...rs.map((r) => ({ ...r, kind: "ratio" }))]);
  else if (FA.tab === "VALUATION") rows = (d.valuation || []).map((r) => ({ ...r, kind: "ratio" }));
  else if (FA.tab === "ALL LINES") {
    const q = FA.q.toLowerCase();
    rows = d.all_tags.filter((r) => !q || r.label.toLowerCase().includes(q) || r.tag.toLowerCase().includes(q)).map((r) => ({ ...r, kind: "money", indent: 0 }));
  } else rows = faOverview(d);
  FA.rows = rows; FA.ends = ends;
  const base = FA.tab === "BALANCE" ? d.statements.balance.find((r) => r.key === "assets") : d.statements.income.find((r) => r.key === "rev");
  const cell = (r, e, i) => {
    let v = r[FA.per]?.[e];
    if (v == null) return '<td class="num dim">--</td>';
    if (r.kind === "money" && FA.yoy) {
      const p = ends[i - (FA.per === "A" ? 1 : 4)], pv = p ? r[FA.per]?.[p] : null;
      return `<td class="num ${cls(pv ? v / pv - 1 : null)}">${pv && pv > 0 ? spct(v / pv - 1, 1) : "--"}</td>`;
    }
    if (r.kind === "money" && FA.common && base && !/PerShare|shares/i.test(r.tag || r.key || "")) {
      const b = base[FA.per]?.[e];
      return `<td class="num">${b ? faFmt(v / b, "pct") : "--"}</td>`;
    }
    const txt = r.kind === "money" ? faMoney(v, r.tag) : faFmt(v, r.fmt);
    return `<td class="num ${v < 0 ? "neg" : ""}">${txt}</td>`;
  };
  const spark = (r) => sparkline(ends.map((e) => r[FA.per]?.[e]).filter((x) => x != null), 70, 14);
  el.innerHTML = `<table class="fatab"><tr><th class="fa-l">${FA_STMT[FA.tab] ? `${esc(FA.d?.currency || "USD")} ${FA.unit === "B" ? "billions" : "millions"}` : ""}</th>${ends.map((e) => `<th class="num">${esc(faLab(e))}</th>`).join("")}<th></th></tr>
    ${rows.map((r, ri) => r.group ? `<tr class="sec"><td colspan="${ends.length + 2}">${esc(r.group.toUpperCase())}</td></tr>`
      : `<tr class="${r.bold ? "fab" : ""} ${FA.sel === ri ? "on" : ""}" onclick="FA.sel=${ri};faChart()" title="${esc(r.tag || "")}">
          <td class="fa-l" style="padding-left:${6 + (r.indent || 0) * 14}px">${esc(r.label)}</td>${ends.map((e, i) => cell(r, e, i)).join("")}<td style="width:76px">${spark(r)}</td></tr>`).join("")}</table>
    <div id="fachart" class="fachart"></div>
    <div class="cnote">${FA_STMT[FA.tab] ? "From the company's own XBRL tags; hover a line for its tag. Lines marked (derived) are computed from other lines. " : ""}${FA.per === "Q" ? "Fourth quarters and quarterly cash flows are derived from full-year and year-to-date filings. " : ""}${[d.note, d.valuation_note, d.shares_note].filter(Boolean).map(esc).join(" ")} Negative numbers in parentheses. <a class="amb" href="${esc(d.filings_url)}" target="_blank" rel="noopener">Filings on SEC.gov</a></div>`;
  if (FA.sel != null) faChart();
}

function faOverview(d) {
  const S = (st, key) => d.statements[st].find((r) => r.key === key);
  const R = (g, lab) => (d.ratios[g] || []).find((r) => r.label.startsWith(lab));
  const V = (lab) => (d.valuation || []).find((r) => r.label === lab);
  const m = (r) => (r ? { ...r, kind: "money" } : null), q = (r) => (r ? { ...r, kind: "ratio" } : null);
  return [{ group: "Scale" }, m(S("income", "rev")), m(S("income", "ebitda")), m(S("income", "ni")), m(S("cashflow", "fcf")),
    { group: "Profitability and returns" }, q(R("Profitability", "Gross margin")), q(R("Profitability", "Operating margin")), q(R("Profitability", "FCF margin")),
    q(R("Returns", "Return on equity")), q(R("Returns", "ROIC")),
    { group: "Growth" }, q(R("Growth", "Revenue")), q(R("Growth", "Diluted EPS")), q(R("Growth", "Free cash flow")),
    { group: "Balance sheet" }, q(R("Leverage", "Net debt / EBITDA")), q(R("Leverage", "Interest coverage")), q(R("Liquidity", "Current ratio")),
    { group: "Valuation (at period-end price)" }, q(V("P/E")), q(V("EV / EBITDA")), q(V("FCF yield"))].filter(Boolean);
}

function faChart() {
  const el = $("fachart"), r = FA.rows?.[FA.sel], ends = FA.ends || [];
  document.querySelectorAll(".fatab tr").forEach((tr) => tr.classList.remove("on"));
  if (!el || !r || r.group) return;
  const pts = ends.map((e) => [e, r[FA.per]?.[e]]).filter((p) => p[1] != null);
  if (pts.length < 2) { el.innerHTML = ""; return; }
  const vals = pts.map((p) => p[1]), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = hi - lo || 1;
  const W = 900, H = 160, bw = W / pts.length, Y = (v) => 10 + (1 - (v - lo) / span) * (H - 30);
  const fmt = (v) => (r.kind === "money" ? faMoney(v, r.tag) : faFmt(v, r.fmt));
  el.innerHTML = `<div class="sec2">${esc(r.label)} <span class="dim">${FA.per === "A" ? "annual" : "quarterly"} · click another row to switch</span></div>
    <svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="#444"/>
      ${pts.map(([e, v], i) => `<rect x="${i * bw + bw * 0.15}" y="${Math.min(Y(v), Y(0))}" width="${bw * 0.7}" height="${Math.max(1, Math.abs(Y(v) - Y(0)))}" fill="${v < 0 ? "var(--neg)" : "var(--blue)"}"><title>${esc(faLab(e))}: ${esc(fmt(v))}</title></rect>
        <text x="${i * bw + bw / 2}" y="${H - 4}" fill="#777" font-size="10" text-anchor="middle">${esc(faLab(e))}</text>`).join("")}</svg>`;
}

function faCSV() {
  const rows = FA.rows || [], ends = FA.ends || [];
  const lines = [["Line", "Tag", ...ends].join(",")];
  for (const r of rows) {
    if (r.group) { lines.push(`"${r.group}"`); continue; }
    lines.push([`"${(r.label || "").replace(/"/g, '""')}"`, r.tag || "", ...ends.map((e) => r[FA.per]?.[e] ?? "")].join(","));
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  a.download = `${FA.t}_${FA.tab.replace(/\s/g, "_")}_${FA.per === "A" ? "annual" : "quarterly"}.csv`;
  a.click();
}

// Drag the window by its title bar
function faDrag() {
  const h = $("fahead"), w = $("fawin");
  if (!h || !w) return;
  h.onmousedown = (e) => {
    if (FA.full || e.target.closest("button,input")) return;
    const r = w.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    const move = (ev) => { w.style.left = Math.max(0, ev.clientX - dx) + "px"; w.style.top = Math.max(0, ev.clientY - dy) + "px"; w.style.transform = "none"; };
    const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };
}

// Keys: F on DES opens; Esc closes; Ctrl -/+ zooms while open
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (faOpen()) {
    if (e.key === "Escape") { e.stopImmediatePropagation(); closeFA(); return; }
    if ((e.ctrlKey || e.metaKey) && (e.key === "=" || e.key === "+" || e.key === "-")) { e.preventDefault(); faZoom(e.key === "-" ? -0.1 : 0.1); }
    return;
  }
  if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.shiftKey && (e.key === "F" || e.key === "f") && page === "SEC" && $("drill").hidden) {
    e.preventDefault();
    e.stopImmediatePropagation();
    openFA(secTicker());
  }
}, true);
// Shift+T on DES: add the name to the tracking list
document.addEventListener("keydown", (e) => {
  if (faOpen() || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey || (e.key !== "T" && e.key !== "t")) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName) || page !== "SEC" || !$("drill").hidden) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  openDrill("track:" + secTicker());
}, true);
// Double-click the FA panel title on DES
document.addEventListener("dblclick", (e) => {
  const h = e.target.closest('.p[data-sec="fa"] > h3');
  if (h && page === "SEC") openFA(secTicker());
});
