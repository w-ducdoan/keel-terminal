/* ALT: alternative data found for each company and tested against its own revenue.
   In a security window: type ALT (or TMDX ALT). Signals are grouped by status: ACTIVE (you promoted), PROPOSED
   (passed the test, waiting for you), CONTEXT (positioning data, not tested), WEAK, NEEDS A FREE KEY, UNTESTED,
   REJECTED. Promote or dismiss with one click; RUN DISCOVERY searches again now (otherwise nightly).
   In the PORT window: ALT shows every name in the book, watchlist and tracking list. */
"use strict";

SECFN.ALT = ["Alternative data (found and tested)", null, false];
CLASS_FNS.Equity.push("ALT");
CLASS_FNS.Fund.push("ALT");
BOOKFN.ALT = "Alternative data across your names";
BOOK_CODES.ALT = "Alternative data across your names (PORT window)";

const ALTD = {};   // ticker -> {t, d}
let ALTB = null, altbAt = 0;
const ALT_GROUPS = [["active", "ACTIVE · promoted by you", "var(--pos)"], ["proposed", "PROPOSED · passed the test, your call", "var(--amber)"],
  ["context", "CONTEXT · positioning, shown not tested", "var(--blue)"], ["weak", "WEAK · some link, not enough to rely on", "#9a9a9a"],
  ["needs_key", "NEEDS A FREE KEY", "#c9a0ff"], ["untested", "UNTESTED", "#888"], ["no_data", "NO DATA", "#666"],
  ["rejected", "REJECTED · no useful link", "#666"], ["dismissed", "DISMISSED by you", "#555"]];

async function loadAlt(t, force = false) {
  const hit = ALTD[t];
  if (!force && hit && (hit.busy || Date.now() - hit.t < 60e3)) return;
  ALTD[t] = { ...(hit || {}), busy: true };
  let d;
  try { d = await (await fetch(`/api/alt/${encodeURIComponent(t)}`)).json(); } catch (e) { d = { ok: false, error: e.message }; }
  ALTD[t] = { t: Date.now(), d };
  if (WINS[secKey(t)]?.fn === "ALT") secPaint(secKey(t));
  if (d.running) setTimeout(() => loadAlt(t, true), 6000);  // discovery still going: keep the window current
}
async function altRun(t) {
  await fetch(`/api/alt/${encodeURIComponent(t)}/discover`, { method: "POST" });
  ALTD[t] = { t: 0, d: { ...(ALTD[t]?.d || {}), running: true } };
  if (WINS[secKey(t)]?.fn === "ALT") secPaint(secKey(t));
  setTimeout(() => loadAlt(t, true), 3000);
}
async function altSet(t, sid, status) {
  await fetch(`/api/alt/${encodeURIComponent(t)}/signal`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ series_id: sid, status }) });
  ALTB = null;
  loadAlt(t, true);
}
function altPick(t, sid) {
  const w = WINS[secKey(t)];
  if (!w) return;
  w.opt = { ...(w.opt || {}), alt: w.opt?.alt === sid ? null : sid };
  secPaint(secKey(t));
}

function altRow(t, s, open) {
  const st = s.shown_status, mine = !!s.user_status;
  const btn = [];
  if (!["active", "needs_key", "no_data"].includes(st)) btn.push(`<button class="mini" onclick="event.stopPropagation();altSet('${esc(t)}','${esc(s.series_id)}','active')" title="Track it and alert on big moves">Promote</button>`);
  if (st !== "dismissed") btn.push(`<button class="mini" onclick="event.stopPropagation();altSet('${esc(t)}','${esc(s.series_id)}','dismissed')">Dismiss</button>`);
  if (mine) btn.push(`<button class="mini" onclick="event.stopPropagation();altSet('${esc(t)}','${esc(s.series_id)}',null)" title="Back to Keel's own verdict">Undo</button>`);
  const stats = s.fit != null ? `<span class="small">fit <b>${n(s.fit, 2)}</b> · leads by ${s.lead}q · ${s.n}q tested · recent ${esc(s.holdout || "")}</span>` : "";
  const z = s.z != null ? ` <span class="${Math.abs(s.z) >= 2 ? "amb" : "dim"}" title="How unusual the latest change is against its own history">z ${s.z > 0 ? "+" : ""}${n(s.z, 1)}</span>` : "";
  return `<div class="altrow ${open ? "on" : ""}" onclick="altPick('${esc(t)}','${esc(s.series_id)}')">
    <div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap"><b>${esc(s.title || s.series_id)}</b><span class="dim small">${esc(s.source || "")}${s.last_date ? " · to " + esc(s.last_date) : ""}</span>
      <span style="margin-left:auto;display:flex;gap:4px">${btn.join("")}</span></div>
    <div style="display:grid;grid-template-columns:160px 1fr;gap:10px;align-items:center;margin-top:2px">
      <div>${sparkline(s.spark || [], 160, 26)}</div>
      <div><span>${s.last != null ? n(s.last, Math.abs(s.last) < 10 ? 2 : 0) + " " + esc(s.unit || "") : ""}</span> <span class="dim">${esc(s.label || "")}</span>${z} ${stats}
        <div class="small dim">${esc(s.note || "")}</div><div class="small">${esc(s.reason || "")}${s.needs ? ` <span class="amb">Waits for a free ${esc(s.needs)}.</span>` : ""}</div></div></div>
    ${open && s.spark?.length ? chartSlot("ALT:" + s.series_id, "sw-alt", 220, "5Y") : ""}</div>`;
}

function altWinBody(t) {
  const hit = ALTD[t];
  if (!hit || (!hit.busy && Date.now() - hit.t > 60e3)) setTimeout(() => loadAlt(t), 0);
  const d = hit?.d;
  const head = `<div class="rbar" style="margin-bottom:6px"><button onclick="altRun('${esc(t)}')" ${d?.running ? "disabled" : ""}>${d?.running ? "SEARCHING..." : "RUN DISCOVERY"}</button>
    <span class="dim small" style="margin-left:8px">Sources: openFDA, ClinicalTrials.gov, EIA-860M, EIA grid, CFTC, FAA registry + OpenSky. Click a row for its chart.</span></div>`;
  if (!d) return head + '<div class="loading">Reading ALT signals...</div>';
  if (!d.ok) return head + `<div class="neg">${esc(d.error || "ALT unavailable")}</div>`;
  const sig = d.signals || [];
  if (!sig.length) return head + `<div class="dim">${d.running ? "Searching every source for this company (a minute or two the first time)..." : "No signals yet. RUN DISCOVERY to search every source for this company."}</div>`;
  const open = WINS[secKey(t)]?.opt?.alt;
  return head + ALT_GROUPS.map(([k, label, col]) => {
    const rows = sig.filter((s) => s.shown_status === k);
    if (!rows.length) return "";
    const fold = ["rejected", "dismissed", "no_data"].includes(k);
    const inner = rows.map((s) => altRow(t, s, open === s.series_id)).join("");
    return fold ? `<details class="altgrp"><summary class="sec2" style="color:${col}">${label} (${rows.length})</summary>${inner}</details>`
      : `<div class="sec2" style="color:${col}">${label}</div>${inner}`;
  }).join("") + `<div class="cnote">A signal is proposed only if its year-on-year change moved with the company's revenue growth over at least 8 quarters
    (correlation 0.5+) and then called at least 3 of the next 4 quarters it never saw. Proxies, not reported numbers. Promoted signals alert in DO TODAY on unusual moves.</div>`;
}

async function loadAltBook(force = false) {
  if (!force && ALTB && Date.now() - altbAt < 120e3) return;
  try { ALTB = await (await fetch("/api/alt")).json(); altbAt = Date.now(); } catch (e) { ALTB = { ok: false, error: e.message }; }
  if (WINS.BOOK?.fn === "ALT") bookPaint(true);
}
function altBookBody() {
  if (!ALTB) { setTimeout(() => loadAltBook(), 0); return '<div class="loading">Reading ALT signals for your names...</div>'; }
  if (!ALTB.ok) return `<div class="neg">${esc(ALTB.error || "")}</div>`;
  const rows = ALTB.rows.filter((r) => Object.values(r.counts).some((x) => x));
  const none = ALTB.rows.filter((r) => !Object.values(r.counts).some((x) => x)).map((r) => r.ticker);
  return `<table class="sortable" data-sortkey="altbook"><tr><th>NAME</th><th class="num">ACTIVE</th><th class="num">PROPOSED</th><th class="num">CONTEXT</th><th class="num">NEEDS KEY</th><th>TOP SIGNALS</th></tr>
    ${rows.map((r) => `<tr class="clk" onclick="openSec('${esc(r.ticker)}','ALT')"><td class="amb">${esc(r.ticker)}</td><td class="num pos">${r.counts.active || ""}</td>
      <td class="num amb">${r.counts.proposed || ""}</td><td class="num">${r.counts.context || ""}</td><td class="num dim">${r.counts.needs_key || ""}</td>
      <td style="white-space:normal" class="small">${r.top.map((s) => `${esc(s.title)} <span class="dim">${esc(s.label || "")}</span>`).join("<br>")}</td></tr>`).join("")}</table>
    ${none.length ? `<div class="small dim" style="margin-top:6px">No signals found yet: ${esc(none.join(", "))}. Discovery runs nightly; open a name and RUN DISCOVERY to search now.</div>` : ""}`;
}
