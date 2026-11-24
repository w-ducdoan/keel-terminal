/* EVT: market-wide event-driven idea feed (spin-offs, tender offers, merger votes, going-private deals, new
   activist stakes) from SEC filings, with your held / watch / tracked names tagged. */
"use strict";

let EVT = null, evtBusy = false;
const EVT_TYPES = { spinoff: ["SPIN", "#c9a0ff"], tender: ["TENDER", "var(--pos)"], buyback_tender: ["SELF-TENDER", "#4fd1c5"],
  merger: ["MERGER", "var(--amber)"], going_private: ["GO-PRIVATE", "var(--blue)"], activist: ["ACTIVIST", "var(--neg)"] };
const evtF = () => lsGet("evt:type") || "ALL", evtSize = () => +(lsGet("evt:size") || 3e8);

async function loadEvt(force = false) {
  if (evtBusy || (!force && EVT && Date.now() - EVT.t < 30 * 60e3)) return;
  evtBusy = true;
  try { EVT = { t: Date.now(), d: await (await fetch(`/api/eventfeed${force ? "?fresh=true" : ""}`)).json() }; }
  catch (e) { EVT = { t: Date.now(), d: { ok: false, error: e.message } }; }
  evtBusy = false;
  if (page === "EVT") render();
}
function evtSet(k, v) { lsSet("evt:" + k, v); render(); }

function pageEvt() {
  if (!EVT) { setTimeout(() => loadEvt(), 0); return '<div class="loading">Reading the last 30 days of SEC filings (spin-offs, tenders, mergers, go-private, activists)...</div>'; }
  const d = EVT.d;
  if (!d.ok) return `<div class="loading neg">${esc(d.error || "Feed failed")}</div>`;
  const f = evtF(), size = evtSize();
  const shown = d.events.filter((e) => (f === "ALL" || e.type === f) && (e.type === "spinoff" || !size || (e.mcap || 0) >= size || e.own));
  const bar = `<div class="rbar" style="margin-bottom:6px"><button class="${f === "ALL" ? "on" : ""}" onclick="evtSet('type','ALL')">ALL</button>
      ${Object.entries(EVT_TYPES).map(([k, [l]]) => `<button class="${f === k ? "on" : ""}" onclick="evtSet('type','${k}')">${l} <span class="dim">${d.counts[k] || 0}</span></button>`).join("")}
      <span style="margin-left:auto" class="dim small">size</span>${[[0, "ANY"], [3e8, ">$300M"], [2e9, ">$2B"], [1e10, ">$10B"]].map(([v, l]) => `<button class="${size === v ? "on" : ""}" onclick="evtSet('size','${v}')">${l}</button>`).join("")}
      <button onclick="loadEvt(true)">REFRESH</button></div>`;
  const rows = shown.map((e) => { const [l, c] = EVT_TYPES[e.type] || [e.type, "#888"];
    return `<tr ${e.ticker ? `data-drill="tkr:${esc(e.ticker)}"` : ""}><td class="dim" style="width:84px">${esc(e.date || "")}</td>
      <td style="width:96px"><span class="dtag" style="color:${c};border-color:${c}">${l}</span></td>
      <td><b class="${e.ticker ? "amb" : ""}">${esc(e.ticker || "")}</b> ${esc(e.company)}${e.own ? ` <span class="dtag" style="color:var(--pos);border-color:var(--pos)">${esc(e.own.toUpperCase())}</span>` : ""}
        ${e.counterparty ? `<div class="small dim">${e.type === "activist" ? "Filed by" : e.type === "tender" ? "Bidder" : "With"}: ${esc(e.counterparty)}</div>` : ""}</td>
      <td class="num">${e.mcap ? bigUsd(e.mcap) : '<span class="dim">--</span>'}</td><td class="dim small">${esc(e.sector || "")}</td>
      <td><a class="mini" href="${esc(e.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(e.form)}</a></td></tr>`; }).join("");
  return `<div class="grid"><div class="p span2"><h3>EVENT FEED <i>${esc(d.from)} to ${esc(d.to)} · ${shown.length} shown of ${d.events.length}</i></h3><div class="pb">${bar}
    <table><tr><th>FILED</th><th>TYPE</th><th>COMPANY</th><th class="num">MKT CAP</th><th>SECTOR</th><th>FILING</th></tr>${rows || '<tr><td class="dim" colspan="6">Nothing in this filter.</td></tr>'}</table>
    ${d.failed?.length ? `<div class="small neg">SEC search failed for: ${esc(d.failed.join(", "))} (refresh to retry)</div>` : ""}
    <div class="cnote">From SEC filings: Form 10-12B (spin-offs), SC TO-T / TO-I (tender offers, self-tenders), DEFM14A / PREM14A (merger votes), SC 13E3 (going private), first SCHEDULE 13D (activist stakes). Private funds, trusts and blank-check companies are left out. Spin-offs show at any size. A starting list for the event pod's own checks (the research folder/event-pod/pod-process.md), not a recommendation.</div></div></div></div>`;
}
