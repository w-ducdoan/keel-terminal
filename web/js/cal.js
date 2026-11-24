/* CAL: catalyst calendar. Earnings, ex-dividend dates and IPO lockups for held, watchlist and tracked names,
   market dates (options expiry, index rebalances, holidays) and macro releases, grouped by week. */
"use strict";

let CAL = null, calAt = 0, calBusy = false;
let calFilter = lsGet("cal:filter") || "ALL";
const CAL_DAYS = [30, 60, 90];
const CAL_KIND = { earnings: ["EARN", "var(--amber)"], dividend: ["DIV", "#9a9a9a"], lockup: ["LOCKUP", "#c9a0ff"], macro: ["MACRO", "var(--blue)"],
  market: ["MKT", "#4fd1c5"], holiday: ["CLOSED", "var(--neg)"] };
const CAL_OWN = { held: ["HELD", "var(--pos)"], watch: ["WATCH", "var(--amber)"], tracked: ["TRACKED", "#c9a0ff"] };

async function loadCal(force = false) {
  const days = +(lsGet("cal:days") || 60);
  if (calBusy || (!force && CAL && CAL.days === days && Date.now() - calAt < 10 * 60e3)) return CAL;
  calBusy = true;
  try { CAL = { ...(await (await fetch(`/api/calendar?days=${days}${force ? "&fresh=true" : ""}`)).json()), days }; calAt = Date.now(); }
  catch (e) { CAL = { ok: false, error: e.message, days }; }
  calBusy = false;
  if (page === "CAL") render();
  return CAL;
}
function calSet(k, v) { lsSet("cal:" + k, v); if (k === "filter") calFilter = v; if (k === "days") { CAL = null; } render(); }

function calShown(ev) {
  return ev.filter((e) => calFilter === "ALL" || (calFilter === "BOOK" ? e.own === "held" : calFilter === "PIPELINE" ? e.own === "watch" || e.own === "tracked"
    : calFilter === "MACRO" ? e.kind === "macro" : calFilter === "MARKET" ? e.kind === "market" || e.kind === "holiday" : true));
}

function calRow(e) {
  const [k, kc] = CAL_KIND[e.kind] || [e.kind.toUpperCase(), "#888"], own = CAL_OWN[e.own];
  const imp = e.importance === "high" ? "font-weight:bold" : "";
  return `<tr ${e.ticker ? `data-drill="tkr:${esc(e.ticker)}"` : ""}><td style="width:62px"><span class="dtag" style="color:${kc};border-color:${kc}">${k}</span></td>
    <td style="width:70px">${own ? `<span class="dtag" style="color:${own[1]};border-color:${own[1]}">${own[0]}</span>` : ""}</td>
    <td style="white-space:normal;${imp}">${esc(e.title)}${e.estimated ? ' <span class="dim small">(est.)</span>' : ""}${e.detail ? `<div class="small dim">${esc(e.detail)}</div>` : ""}</td></tr>`;
}

function pageCal() {
  if (!CAL || CAL.days !== +(lsGet("cal:days") || 60)) { setTimeout(() => loadCal(), 0); return '<div class="loading">Building the calendar (earnings, dividends, filings, market dates, FRED releases)...</div>'; }
  if (!CAL.ok) return `<div class="loading neg">${esc(CAL.error || "Calendar failed")}</div>`;
  const ev = calShown(CAL.events), today = new Date().toISOString().slice(0, 10);
  const counts = { ALL: CAL.events.length, BOOK: CAL.events.filter((e) => e.own === "held").length,
    PIPELINE: CAL.events.filter((e) => e.own === "watch" || e.own === "tracked").length, MACRO: CAL.events.filter((e) => e.kind === "macro").length,
    MARKET: CAL.events.filter((e) => e.kind === "market" || e.kind === "holiday").length };
  const days = +(lsGet("cal:days") || 60);
  const bar = `<div class="rbar" style="margin-bottom:6px">${Object.entries({ ALL: "ALL", BOOK: "YOUR BOOK", PIPELINE: "WATCH + TRACKED", MACRO: "MACRO", MARKET: "MARKET" })
      .map(([k, l]) => `<button class="${calFilter === k ? "on" : ""}" onclick="calSet('filter','${k}')">${l} <span class="dim">${counts[k]}</span></button>`).join("")}
    <span style="margin-left:auto"></span>${CAL_DAYS.map((d) => `<button class="${d === days ? "on" : ""}" onclick="calSet('days','${d}')">${d}D</button>`).join("")}
    <button onclick="loadCal(true)" title="Rebuild now (otherwise every 6 hours)">REFRESH</button></div>`;
  // Group by week (Monday start), then by day
  const wk = (iso) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.toISOString().slice(0, 10); };
  const weeks = {};
  for (const e of ev) (weeks[wk(e.date)] ||= {})[e.date] ? weeks[wk(e.date)][e.date].push(e) : (weeks[wk(e.date)][e.date] = [e]);
  const dayLab = (iso) => { const d = new Date(iso + "T12:00:00"), diff = Math.round((d - new Date(today + "T12:00:00")) / 864e5);
    return `${d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}${diff === 0 ? ' <b class="amb">TODAY</b>' : diff === 1 ? ' <span class="amb">TOMORROW</span>' : ""}`; };
  const body = Object.entries(weeks).map(([w, ds]) => `<div class="sec2">WEEK OF ${esc(new Date(w + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }).toUpperCase())}</div>
    ${Object.entries(ds).map(([d, es]) => `<div class="calday"><div class="calhd">${dayLab(d)}</div><table>${es.map(calRow).join("")}</table></div>`).join("")}`).join("")
    || '<div class="dim">Nothing in this filter for the window.</div>';
  const nextHeld = CAL.events.filter((e) => e.own === "held" && e.kind === "earnings").slice(0, 6);
  const summary = window.KEEL_PUBLIC ? "" : `<div class="p"><h3>NEXT ON YOUR BOOK <i>held-name earnings</i></h3><div class="pb">
    ${nextHeld.length ? `<table>${nextHeld.map((e) => `<tr data-drill="tkr:${esc(e.ticker)}"><td class="amb">${esc(e.ticker)}</td><td>${esc(e.date)}</td><td class="small dim">${esc(e.detail || "")}</td></tr>`).join("")}</table>` : '<span class="dim">No held name reports in the window.</span>'}
    <div class="cnote">Click a name for its card. ERN on the security page has the options-implied move.</div></div></div>`;
  return `<div class="grid"><div class="p span2" data-pid="CALENDAR" data-span="8"><h3>CATALYST CALENDAR <i>${esc(CAL.from)} to ${esc(CAL.to)}</i></h3><div class="pb">${bar}${body}
    <div class="cnote">Earnings from Robinhood (Yahoo for tracked names); "tentative" means the company has not confirmed. Ex-dividend dates from Yahoo, estimated one quarter on when Yahoo only has the last one. Lockups are the typical 180 days after a recent IPO. Market dates follow exchange rules; macro dates are FRED's release schedule${CAL.fred ? "" : " (no FRED key: macro dates missing)"}. Built ${esc(CAL.as_of)}.</div></div></div>${summary}</div>`;
}
