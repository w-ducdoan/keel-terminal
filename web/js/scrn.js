/* SCRN: mandate-aware screener. Criteria are editable rows; mandate rows load by default and are labeled
   when a screen loosens them. Results are tagged by coverage, cross-checked against Robinhood, and can be
   verified against SEC filings, tracked, or queued for Gate 0. Nothing here trades. */
"use strict";

const SCR = { meta: null, name: lsGet("scr:name") || "Quality under a cloud", spec: null, res: null, busy: false, showAll: false };
const OPS = { gte: "≥", lte: "≤", btwn: "between" };

async function scrMeta() {
  if (SCR.meta) return SCR.meta;
  SCR.meta = await (await fetch("/api/screens")).json();
  if (!SCR.spec) scrLoad(SCR.name, false);
  return SCR.meta;
}
function scrLoad(name, rerender = true) {
  const p = SCR.meta?.presets?.[name] || SCR.meta?.presets?.["Mandate base"] || { criteria: [] };
  SCR.name = name; lsSet("scr:name", name);
  SCR.spec = JSON.parse(JSON.stringify({ criteria: p.criteria || [], sort: p.sort || "mcap", asc: !!p.asc, mandate_off: p.mandate_off || [] }));
  SCR.res = null;
  if (rerender) render();
}

// ---------- value units: $ fields typed in $M, percents as whole numbers ----------
const unitOf = (f) => SCR.meta?.fields?.[f]?.unit || "";
const toInput = (f, v) => (unitOf(f) === "usd" ? +(v / 1e6).toFixed(1) : v);
const fromInput = (f, v) => (unitOf(f) === "usd" ? +v * 1e6 : +v);
const unitHint = (f) => ({ usd: "$M", pct: "%", x: "x", n: "" }[unitOf(f)] || "");

function pageScrn() {
  if (!SCR.meta) { setTimeout(() => scrMeta().then(render), 0); return '<div class="loading">Loading the screener...</div>'; }
  const M = SCR.meta, sp = SCR.spec;
  const fieldOpts = (sel) => Object.entries(M.fields).reduce((acc, [k, f]) => {
    (acc[f.group] = acc[f.group] || []).push(`<option value="${k}" ${k === sel ? "selected" : ""}>${esc(f.label)}</option>`); return acc;
  }, {});
  const fieldSelect = (sel, i) => `<select class="wiin" style="width:180px" onchange="scrSet(${i},'field',this.value)">${Object.entries(fieldOpts(sel)).map(([g, o]) => `<optgroup label="${esc(g)}">${o.join("")}</optgroup>`).join("")}</select>`;
  const own = sp.criteria;
  const ownFields = new Set(own.map((c) => c.field).filter(Boolean));
  const mandRows = M.mandate.map((m) => {
    const off = sp.mandate_off.includes(m.field), replaced = ownFields.has(m.field);
    return `<tr class="${off || replaced ? "dim" : ""}"><td><input type="checkbox" ${off ? "" : "checked"} ${replaced ? "disabled" : ""} onchange="scrMand('${m.field}',this.checked)" title="Switch this mandate check on or off"></td>
      <td>${esc(M.fields[m.field].label)}</td><td>${OPS[m.op]}</td><td class="num">${esc(String(toInput(m.field, m.value)))} ${unitHint(m.field)}</td>
      <td class="small">${replaced ? "set by this screen below" : off ? '<span class="amb">off: looser than the mandate</span>' : m.soft ? `<span class="dim" title="${esc(m.soft)}">soft</span>` : ""}</td></tr>`;
  }).join("");
  const ownRows = own.map((c, i) => c.hist ? scrHistRow(c, i) : c.sector
    ? `<tr><td></td><td colspan="3"><span class="dim">Sector</span> ${M.sectors.map((s) => `<label class="small" style="margin-right:6px"><input type="checkbox" ${c.sector.includes(s) ? "checked" : ""} onchange="scrSector(${i},'${esc(s)}',this.checked)">${esc(s)}</label>`).join("")}</td><td><button class="mini" onclick="scrDel(${i})">×</button></td></tr>`
    : `<tr><td></td><td>${fieldSelect(c.field, i)}</td>
      <td><select class="wiin" style="width:auto" onchange="scrSet(${i},'op',this.value)">${Object.entries(OPS).map(([k, l]) => `<option value="${k}" ${k === c.op ? "selected" : ""}>${l}</option>`).join("")}</select></td>
      <td>${c.op === "btwn"
        ? `<input class="wiin" style="width:64px" value="${esc(String(toInput(c.field, c.value[0])))}" onchange="scrVal(${i},0,this.value)"> – <input class="wiin" style="width:64px" value="${esc(String(toInput(c.field, c.value[1])))}" onchange="scrVal(${i},1,this.value)">`
        : `<input class="wiin" style="width:80px" value="${esc(String(toInput(c.field, c.value)))}" onchange="scrVal(${i},null,this.value)">`} <span class="dim small">${unitHint(c.field)}</span></td>
      <td><button class="mini" onclick="scrDel(${i})">×</button></td></tr>`).join("");
  const sortSel = `<select class="wiin" style="width:auto" onchange="SCR.spec.sort=this.value">${own.filter((c) => c.hist).map((c, j) => `<option value="hist:${j}" ${sp.sort === "hist:" + j ? "selected" : ""}>${esc(scrHistLabel(c))}</option>`).join("")}${Object.entries(M.fields).map(([k, f]) => `<option value="${k}" ${k === sp.sort ? "selected" : ""}>${esc(f.label)}</option>`).join("")}</select>
    <select class="wiin" style="width:auto" onchange="SCR.spec.asc=this.value==='asc'"><option value="desc" ${sp.asc ? "" : "selected"}>high first</option><option value="asc" ${sp.asc ? "selected" : ""}>low first</option></select>`;
  const isBuiltin = M.builtin.includes(SCR.name);
  const left = `<div class="p" data-pid="CRITERIA" data-span="4"><h3>CRITERIA <i>${esc(SCR.name)}${isBuiltin ? " · built-in" : " · saved"}</i></h3><div class="pb">
    <div class="rbar" style="gap:4px;margin-bottom:6px"><select class="wiin" style="width:auto;flex:1" onchange="scrLoad(this.value)">${Object.keys(M.presets).map((n2) => `<option ${n2 === SCR.name ? "selected" : ""}>${esc(n2)}</option>`).join("")}</select>
      ${isBuiltin ? "" : '<button onclick="scrSave(false)">SAVE</button>'}<button onclick="scrSave(true)">SAVE AS</button>${isBuiltin ? "" : '<button onclick="scrDelete()">DELETE</button>'}</div>
    <div class="sec2">MANDATE BASE <span class="dim">from MANDATE.md · untick to switch off</span></div><table>${mandRows}</table>
    <div class="sec2">THIS SCREEN</div><table>${ownRows || '<tr><td class="dim">No extra criteria: the mandate base only.</td></tr>'}</table>
    <div style="display:flex;gap:6px;margin-top:6px"><button class="mini" onclick="scrAdd()">+ criterion</button><button class="mini" onclick="scrAddHist()" title="CAGR, years of growth, worst year, average or lowest margin over any 1-15 years">+ multi-year</button><button class="mini" onclick="scrAddSector()">+ sector filter</button></div>
    <div class="sec2">SORT</div>${sortSel}
    <div style="margin-top:10px"><button class="mini" style="color:var(--amber);border-color:var(--amber)" onclick="scrRun()">RUN SCREEN ⏎</button></div>
    <div class="cnote">Values: $ in millions, percents as whole numbers (15 = 15%). US-listed stocks only (OTC duplicates excluded). + multi-year runs on SEC annual reports, any 1-15 years. Data: Yahoo screener; cross-checks from Robinhood; verify against SEC filings per row.</div></div></div>`;
  return `<div class="grid">${left}<div class="p" data-pid="RESULTS" data-span="8"><h3>RESULTS <i>new names first · click a row for its card</i></h3><div class="pb" data-scr>${scrResults()}</div></div></div>`;
}

// ---------- multi-year rows: metric, statistic, number of years ----------
const scrStatsFor = (m) => Object.entries(SCR.meta.hist_stats).filter(([, s]) => s.kinds.includes(SCR.meta.hist_metrics[m].kind));
const scrHistLabel = (c) => `${SCR.meta.hist_metrics[c.hist].label} ${c.years}y ${SCR.meta.hist_stats[c.stat].label}`;
function scrHistRow(c, i) {
  const M = SCR.meta, u = M.hist_stats[c.stat].unit;
  return `<tr><td></td><td colspan="3" style="white-space:normal">
      <select class="wiin" style="width:128px" onchange="scrHist(${i},'hist',this.value)">${Object.entries(M.hist_metrics).map(([k, m]) => `<option value="${k}" ${k === c.hist ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select>
      <select class="wiin" style="width:128px" onchange="scrHist(${i},'stat',this.value)">${scrStatsFor(c.hist).map(([k, s]) => `<option value="${k}" ${k === c.stat ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
      <span class="dim small">over</span> <input class="wiin" type="number" min="1" max="${M.hist_max_years}" style="width:44px" value="${c.years}" onchange="scrHist(${i},'years',this.value)"> <span class="dim small">yrs</span>
      <select class="wiin" style="width:auto" onchange="scrSet(${i},'op',this.value)">${Object.entries(OPS).map(([k, l]) => `<option value="${k}" ${k === c.op ? "selected" : ""}>${l}</option>`).join("")}</select>
      ${c.op === "btwn"
        ? `<input class="wiin" style="width:48px" value="${esc(String(c.value[0]))}" onchange="scrVal(${i},0,this.value)"> – <input class="wiin" style="width:48px" value="${esc(String(c.value[1]))}" onchange="scrVal(${i},1,this.value)">`
        : `<input class="wiin" style="width:52px" value="${esc(String(c.value))}" onchange="scrVal(${i},null,this.value)">`} <span class="dim small">${u === "yrs" ? "of " + c.years + " yrs" : esc(u)}</span></td>
    <td><button class="mini" onclick="scrDel(${i})">×</button></td></tr>`;
}
function scrAddHist() { SCR.spec.criteria.push({ hist: "rev", stat: "cagr", years: 5, op: "gte", value: 8 }); render(); }
function scrHist(i, k, v) {
  const c = SCR.spec.criteria[i];
  if (k === "years") c.years = Math.max(1, Math.min(SCR.meta.hist_max_years, Math.round(+v) || 5));
  else c[k] = v;
  const ok = scrStatsFor(c.hist).map(([s]) => s);
  if (!ok.includes(c.stat)) c.stat = ok[0];
  if (k === "stat" && SCR.meta.hist_stats[c.stat].unit === "yrs" && c.op !== "btwn") { c.op = "gte"; c.value = c.years; }
  render();
}
function scrHistCell(v, h) {
  if (!v) return '<td class="num">--</td>';
  const fx = v.cur && v.cur !== "USD" ? v.cur + " " : "$";
  const raw = (x) => (h.kind === "ratio" ? n(x, 1) + "%" : ["eps", "fcfps"].includes(h.hist) ? fx + n(x, 2) : h.hist === "shares" ? n(x / 1e6, 1) + "M sh" : (x < 0 ? "-" : "") + bigN(Math.abs(x)).replace("$", fx));
  const shown = h.unit === "yrs" ? `${v.v}/${h.years}` : (h.stat === "chg" && v.v > 0 ? "+" : "") + n(v.v, 1) + (h.unit === "pts" ? "" : "%");
  return `<td class="num ${h.stat === "cagr" || h.stat === "chg" ? cls(h.hist === "shares" ? -v.v : v.v) : ""}" title="FY${v.from} ${esc(raw(v.a))} to FY${v.to} ${esc(raw(v.b))} (year ended ${esc(v.end || "")}), SEC filings${v.ifrs ? ", IFRS" : ""}${v.late ? ". The SEC data feed has no newer year for this company" : ""}">${shown}${v.late ? ` <span class="amb small">FY${String(v.to).slice(2)}</span>` : ""}${fx !== "$" && h.kind !== "ratio" && h.hist !== "shares" ? ` <span class="dim small">${esc(v.cur)}</span>` : ""}</td>`;
}

function scrResults() {
  if (SCR.busy) return '<div class="loading">Screening (Yahoo), then checking the rows against Robinhood and your coverage... about 5-10 seconds.</div>';
  const r = SCR.res;
  if (!r) return '<div class="dim">Pick or edit a screen, then RUN SCREEN (or press Enter in any value box).</div>';
  if (!r.ok) return `<div class="neg">${esc(r.error)}</div>`;
  const COVT = { none: ["NEW", "var(--amber)"], tracked: ["TRACKED", "#c9a0ff"], held: ["HELD", "var(--pos)"], holdings: ["HELD", "var(--pos)"],
    watchlist: ["WATCH", "var(--blue)"], passed: ["PASSED", "#888"], "closed-positions": ["CLOSED", "#888"] };
  const c = r.counts || {};
  const head = `<div class="small" style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:4px"><b>${r.total} match</b>
      <span>${(c.none || 0)} new · ${(c.tracked || 0)} tracked · ${(c.held || 0) + (c.holdings || 0)} held · ${c.watchlist || 0} watchlist · ${c.passed || 0} passed</span>
      ${r.entered.length || r.left.length ? `<span class="amb">since last run: +${r.entered.length} in${r.entered.length ? " (" + esc(r.entered.slice(0, 6).join(", ")) + ")" : ""}, -${r.left.length} out</span>` : ""}
      <span style="margin-left:auto" class="rbar"><button class="${SCR.showAll ? "" : "on"}" onclick="SCR.showAll=false;scrRun()">NEW + TRACKED</button><button class="${SCR.showAll ? "on" : ""}" onclick="SCR.showAll=true;scrRun()">ALL</button></span></div>
    ${r.looser.length ? `<div class="small amb">Looser than the mandate: ${esc(r.looser.join("; "))}. Results may not pass MANDATE.md.</div>` : ""}
    ${r.hist_info ? `<div class="small dim">Multi-year rows checked on ${r.hist_info.checked} Yahoo matches: ${r.total} passed, ${r.hist_info.failed} failed, ${r.hist_info.no_data} left out for lack of usable SEC history. ${esc(r.hist_info.sort_note || "")}</div>` : ""}
    ${r.robinhood_checked ? "" : `<div class="small dim">Robinhood cross-check unavailable: ${esc(r.robinhood_error || "")}</div>`}`;
  const rows = r.shown.map((x) => {
    const [tag, col] = COVT[x.coverage] || [x.coverage, "#888"];
    return `<tr data-drill="tkr:${esc(x.ticker)}" title="${esc(x.summary || "")}">
      <td class="amb">${esc(x.ticker)}${x.conflict ? ` <span class="neg" title="${esc(x.conflict)}">⚠</span>` : ""}</td><td class="small" style="max-width:170px;overflow:hidden;text-overflow:ellipsis">${esc(x.name || "")}</td>
      <td class="small dim">${esc(x.sector || "")}</td><td class="num">${bigN(x.mcap)}</td>${(r.hist || []).map((h, j) => scrHistCell(x.hist?.[j], h)).join("")}<td class="num">${x.pe ? n(x.pe, 1) : "--"}</td><td class="num">${x.fwd_pe ? n(x.fwd_pe, 1) : "--"}</td>
      <td class="num">${x.ev_ebitda ? n(x.ev_ebitda, 1) : "--"}</td><td class="num">${x.roe != null ? n(x.roe, 0) + "%" : "--"}</td>
      <td class="num">${x.nd_ebitda != null ? n(x.nd_ebitda, 1) + "x" : "--"}</td><td class="num">${x.cur ? n(x.cur, 2) : "--"}</td><td class="num">${x.beta ? n(x.beta, 2) : "--"}</td>
      <td class="num ${cls(x.chg52)}">${spct(x.chg52, 0)}</td><td class="num ${cls(x.from_high)}">${spct(x.from_high, 0)}</td>
      <td><span class="tag" style="color:${col};border-color:${col}">${tag}</span></td>
      <td style="white-space:nowrap" onclick="event.stopPropagation()"><a class="mini" href="#SEC/${encodeURIComponent(x.ticker)}">DES</a>
        <button class="mini" onclick="scrVerify('${esc(x.ticker)}')" title="Check Yahoo's numbers against the SEC filing">SEC</button>
        ${["none", "tracked"].includes(x.coverage) ? `<button class="mini" onclick="openDrill('track:${esc(x.ticker)}')" title="Track">T</button><button class="mini" onclick="scrGate0('${esc(x.ticker)}')" title="Queue for Gate 0">G0</button>` : ""}</td></tr>`;
  }).join("");
  return head + `<table><tr><th>TKR</th><th>NAME</th><th>SECTOR</th><th class="num">MCAP</th>${(r.hist || []).map((h) => `<th class="num amb" style="white-space:normal;min-width:58px">${esc(h.label)}</th>`).join("")}<th class="num">P/E</th><th class="num">FWD</th><th class="num">EV/EBITDA</th><th class="num">ROE</th><th class="num">ND/EBITDA</th><th class="num">CUR</th><th class="num">BETA</th><th class="num">52W</th><th class="num">FROM HIGH</th><th>COV</th><th></th></tr>${rows || '<tr><td class="dim" colspan="${15 + (r.hist || []).length}">Nothing new: every match is already in your coverage. Switch to ALL to see them.</td></tr>'}</table>
    <div class="cnote">⚠ = Yahoo and Robinhood disagree by more than 10% (hover for both). Ratios per row come from each company's profile; the screen itself runs on Yahoo's screener fields. Amber columns are multi-year figures from SEC annual reports (fiscal years; hover for the start and end values). Foreign companies are IFRS in their own currency; an amber FYxx tag means the SEC feed stops at that year. Acquisitions count as growth. Every name still goes through Gate 0.</div>`;
}

// ---------- editing ----------
function scrSet(i, k, v) { const c = SCR.spec.criteria[i]; c[k] = v; if (k === "op") c.value = v === "btwn" ? [Array.isArray(c.value) ? c.value[0] : c.value, Array.isArray(c.value) ? c.value[1] : c.value] : (Array.isArray(c.value) ? c.value[0] : c.value); render(); }
function scrVal(i, j, v) { const c = SCR.spec.criteria[i]; if (v === "" || isNaN(+v)) return; if (c.hist) { if (j == null) c.value = +v; else c.value[j] = +v; return; } if (j == null) c.value = fromInput(c.field, v); else c.value[j] = fromInput(c.field, v); }
function scrDel(i) { SCR.spec.criteria.splice(i, 1); if (String(SCR.spec.sort).startsWith("hist:")) SCR.spec.sort = SCR.spec.criteria.some((c) => c.hist) ? "hist:0" : "mcap"; render(); }
function scrAdd() { SCR.spec.criteria.push({ field: "roe", op: "gte", value: 15 }); render(); }
function scrAddSector() { if (!SCR.spec.criteria.some((c) => c.sector)) SCR.spec.criteria.push({ sector: ["Industrials"] }); render(); }
function scrSector(i, s, on) { const c = SCR.spec.criteria[i]; c.sector = on ? [...new Set([...c.sector, s])] : c.sector.filter((x) => x !== s); if (!c.sector.length) SCR.spec.criteria.splice(i, 1); render(); }
function scrMand(f, on) { const o = SCR.spec.mandate_off; SCR.spec.mandate_off = on ? o.filter((x) => x !== f) : [...new Set([...o, f])]; render(); }

async function scrRun() {
  SCR.busy = true;
  const el = document.querySelector("[data-scr]"); if (el) el.innerHTML = scrResults();
  try {
    SCR.res = await (await fetch("/api/screen/run", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...SCR.spec, name: SCR.name, show_all: SCR.showAll }) })).json();
  } catch (e) { SCR.res = { ok: false, error: e.message }; }
  SCR.busy = false;
  const el2 = document.querySelector("[data-scr]"); if (el2 && page === "SCRN") el2.innerHTML = scrResults();
}
async function scrSave(asNew) {
  const name = asNew ? (prompt("Save this screen as:", SCR.name + " (mine)") || "").trim() : SCR.name;
  if (!name) return;
  const r = await fetch("/api/screens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, ...SCR.spec }) });
  if (!r.ok) { alert((await r.json().catch(() => ({}))).detail || "Could not save."); return; }
  SCR.meta = null; SCR.name = name; lsSet("scr:name", name);
  await scrMeta(); render();
}
async function scrDelete() {
  if (!confirm(`Delete the saved screen "${SCR.name}"?`)) return;
  await fetch("/api/screens/" + encodeURIComponent(SCR.name), { method: "DELETE" });
  SCR.meta = null; SCR.spec = null; SCR.name = "Mandate base";
  await scrMeta(); render();
}
async function scrGate0(t) {
  await fetch("/api/tracking", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: t, theme: "Screen: " + SCR.name, note: "Queued from SCRN" }) });
  await post(`/api/tracking/${encodeURIComponent(t)}/status?status=gate0`);
  CARDS[t] = null;
  if (typeof loadTracking === "function") loadTracking(true);
  const row = SCR.res?.shown?.find((x) => x.ticker === t); if (row) row.coverage = "tracked";
  const el = document.querySelector("[data-scr]"); if (el) el.innerHTML = scrResults();
}

DRILL.verify = (t) => {
  setTimeout(async () => {
    let d; try { d = await (await fetch("/api/screen/verify/" + encodeURIComponent(t))).json(); } catch (e) { d = { ok: false, error: e.message }; }
    const el = document.getElementById("vf"); if (!el) return;
    el.innerHTML = !d.ok ? `<div class="note neg" style="white-space:normal">${esc(d.error)}</div>`
      : `<div class="small" style="margin-bottom:4px">Verdict: <b class="${d.verdict === "match" ? "pos" : "amb"}">${esc(d.verdict)}</b> (within 5% counts as a match)</div><table><tr><th>LINE</th><th class="num">SEC FILING</th><th class="num">YAHOO</th><th class="num">DIFF</th><th>AS OF</th></tr>
        ${d.checks.map((c) => `<tr><td>${esc(c.label)}</td><td class="num">${bigN(c.sec)}</td><td class="num">${bigN(c.yahoo)}</td><td class="num ${c.ok === false ? "neg" : c.ok ? "pos" : "dim"}">${c.diff == null ? "--" : spct(c.diff, 1)}</td><td class="dim">${esc(c.as_of || "")}</td></tr>`).join("")}</table>
        <div class="sec2">RED FLAGS</div><div id="vf-flags" class="dim small">Scanning the latest 10-K and 10-Q...</div>`;
    if (d.ok) {
      try { const f = await (await fetch("/api/equity/" + encodeURIComponent(t) + "/flags")).json(); EQ[t + "|flags"] = { t: Date.now(), d: f }; const ff = document.getElementById("vf-flags"); if (ff) ff.innerHTML = flagsHTML(t); }
      catch { /* flags are a bonus */ }
    }
  }, 0);
  return { title: `SEC CHECK · ${esc(t)}`, body: `<div class="dsec" id="vf"><div class="loading">Reading ${esc(t)}'s latest 10-K and 10-Q from SEC.gov...</div></div>` };
};
function scrVerify(t) { openDrill("verify:" + t); }

// Enter in a value box runs the screen
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && page === "SCRN" && e.target.closest?.(".p[data-pid='CRITERIA'] input")) { e.target.dispatchEvent(new Event("change")); scrRun(); }
});
