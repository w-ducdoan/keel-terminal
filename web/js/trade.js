/* Pre-trade check (whatif:side|TICKER|shares|dollars) and the decision journal page (#JRNL).
   Both are paper only: nothing here places, changes or cancels an order. */
"use strict";

// ---------- pre-trade check ----------
const LVL = { pass: ["PASS", "var(--pos)"], warn: ["WARN", "var(--amber)"], fail: ["FAIL", "var(--neg)"] };

DRILL.whatif = (spec) => {
  const [side, t] = spec.split("|");
  setTimeout(() => runWhatif(spec), 0);
  return { title: `PRE-TRADE CHECK · ${esc(side.toUpperCase())} ${esc(t)}`, body: `<div id="wi" data-spec="${esc(spec)}"><div class="loading">Checking the trade against your book and mandate...</div></div>` };
};

async function runWhatif(spec) {
  const [side, t, sh, dl] = spec.split("|");
  const qs = new URLSearchParams({ ticker: t, side });
  if (sh) qs.set("shares", sh);
  if (dl) qs.set("dollars", dl);
  let d;
  try { d = await (await fetch("/api/whatif?" + qs)).json(); } catch (e) { d = { ok: false, error: e.message }; }
  const el = document.getElementById("wi");
  if (!el || el.dataset.spec !== spec) return;
  el.innerHTML = whatifHTML(d, side, t);
}

function whatifHTML(d, side, t) {
  if (!d.ok) return `<div class="note neg">${esc(d.error || "Check failed.")}</div>`;
  const [vtag, vcol] = LVL[d.level];
  const r = d.risk || {};
  const pair = (a, f) => (a && a[0] != null ? `${f(a[0])} → <b>${f(a[1])}</b>` : "--");
  return `<div class="dsec">
      <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
        <span class="dtag" style="color:${vcol};border-color:${vcol};width:auto;padding:0 6px">${vtag}</span>
        <b style="font-size:14px">${esc(d.verdict)}</b>
        <span class="dim">${esc(d.side.toUpperCase())} ${n(d.shares, 4)} sh ${esc(d.ticker)} at ${n(d.price)} = ${money(d.dollars)}</span>
      </div>
      <div class="rbar" style="margin-top:6px;gap:6px">
        <button class="${side === "buy" ? "on" : ""}" onclick="wiRerun('buy')">BUY</button><button class="${side === "sell" ? "on" : ""}" onclick="wiRerun('sell')">SELL</button>
        <span class="dim small" style="margin-left:6px">shares</span><input id="wi-sh" class="wiin" value="${n(d.shares, 4).replace(/,/g, "")}" onkeydown="if(event.key==='Enter')wiRerun()">
        <span class="dim small">or $</span><input id="wi-dl" class="wiin" placeholder="dollars" onkeydown="if(event.key==='Enter')wiRerun()">
        <button onclick="wiRerun()">RECHECK</button>
      </div></div>
    <div class="dsec"><table>${d.checks.map((c) => `<tr><td style="width:46px"><span class="dtag" style="color:${LVL[c.level][1]};border-color:${LVL[c.level][1]}">${LVL[c.level][0]}</span></td>
      <td class="amb" style="width:110px">${esc(c.name)}</td><td style="white-space:normal">${esc(c.text)}</td></tr>`).join("")}</table></div>
    <div class="dsec">${kv([
      ["Weight", `${pct(d.weight_before)} → <b>${pct(d.weight_after)}</b>`], ["Cash after", money(d.cash_after)],
      ["Book beta", pair(r.beta, (v) => n(v))], ["Book vol", pair(r.vol, (v) => pct(v))],
      ["1-day VaR 95", pair(r.var95, (v) => money(v, 0))], [`${esc(d.ticker)} share of risk`, r.risk_share != null ? pct(r.risk_share) : "--"],
      ["Sector", esc(d.sector || "--")], ["Coverage", esc(d.coverage || "--")]])}</div>
    <div class="dsec"><div class="small dim">Log this plan in the journal (one line: why, and what would prove you wrong)</div>
      <div style="display:flex;gap:6px;margin-top:4px"><input id="wi-why" class="wiin" style="flex:1;width:auto" placeholder="e.g. at Buy-below after the Q3 beat; wrong if bookings fall below $37M">
      <button class="mini" onclick="wiLog()">Log plan</button></div><div id="wi-logged" class="small"></div>
      <div style="margin-top:8px;display:flex;gap:6px"><a class="mini" href="https://robinhood.com/stocks/${encodeURIComponent(d.ticker)}" target="_blank" rel="noopener">Place it in Robinhood</a>
      <a class="mini" href="#SEC/${encodeURIComponent(d.ticker)}" onclick="closeDrill()">DES</a></div>
      <div class="cnote">${esc(d.note)}</div></div>`;
}

function wiRerun(side) {
  const el = document.getElementById("wi");
  if (!el) return;
  const [s0, t] = el.dataset.spec.split("|");
  const dl = (document.getElementById("wi-dl")?.value || "").replace(/[$,]/g, "").trim();
  const sh = dl ? "" : (document.getElementById("wi-sh")?.value || "").replace(/,/g, "").trim();
  const spec = `${side || s0}|${t}|${sh}|${dl}`;
  el.dataset.spec = spec;
  drillStack[drillStack.length - 1] = "whatif:" + spec;
  el.innerHTML = '<div class="loading">Rechecking...</div>';
  runWhatif(spec);
}

async function wiLog() {
  const el = document.getElementById("wi"), why = document.getElementById("wi-why")?.value.trim();
  if (!el) return;
  if (!why) { document.getElementById("wi-logged").innerHTML = '<span class="amb">Write the one-line reason first.</span>'; return; }
  const [side, t] = el.dataset.spec.split("|");
  const sh = +(document.getElementById("wi-sh")?.value || 0) || 0;
  const r = await fetch("/api/journal", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker: t, side, shares: sh, reason: why, price: null }) });
  document.getElementById("wi-logged").innerHTML = r.ok ? '<span class="pos">Logged in the journal (JRNL).</span>' : '<span class="neg">Could not log it.</span>';
}

// ---------- decision journal ----------
let JR = null, jrAt = 0, jrFilter = lsGet("jr:filter") || "open";
async function loadJournal(force = false) {
  if (!force && JR && Date.now() - jrAt < 60e3) return JR;
  try { JR = await (await fetch("/api/journal")).json(); jrAt = Date.now(); } catch (e) { JR = JR || { ok: false, error: e.message }; }
  if (page === "JRNL") render();
  return JR;
}

const KIND = { new: ["NEW", "var(--pos)"], add: ["ADD", "var(--blue)"], buy: ["BUY", "var(--pos)"], trim: ["TRIM", "var(--amber)"],
  exit: ["EXIT", "var(--neg)"], sell: ["SELL", "var(--amber)"], plan_buy: ["PLAN BUY", "#9a9a9a"], plan_sell: ["PLAN SELL", "#9a9a9a"] };

function pageJournal() {
  if (!JR) { setTimeout(() => loadJournal(), 0); return '<div class="loading">Loading the journal...</div>'; }
  if (!JR.ok) return `<div class="loading neg">${esc(JR.error)}</div>`;
  const sc = JR.score, E = JR.entries;
  const shown = E.filter((e) => jrFilter === "all" || (jrFilter === "open" ? (e.needs_reason || e.due) : jrFilter === "reviewed" ? e.verdict && e.verdict !== "early" : true));
  const stat = (l, v, c = "") => `<div class="stat"><span>${l}</span><b class="${c}">${v}</b></div>`;
  const head = `<div class="p span2"><h3>JOURNAL · SCORECARD <i>process vs outcome</i></h3><div class="pb">
    <div class="stats">${stat("Decisions", sc.entries)}${stat("Need a reason", sc.need_reason, sc.need_reason ? "amb" : "")}${stat("Due for review", sc.due, sc.due ? "amb" : "")}
      ${stat("Right for the reason", sc.process_rate == null ? "--" : pct(sc.process_rate, 0) + ` (${sc.right}/${sc.reviewed})`)}
      ${stat("Lucky / wrong", `${sc.lucky} / ${sc.wrong}`)}${stat("Beat SPY", sc.beat_spy_rate == null ? "--" : pct(sc.beat_spy_rate, 0) + ` of ${sc.scored}`)}
      ${stat("Avg vs SPY", sc.avg_excess == null ? "--" : spct(sc.avg_excess, 1), cls(sc.avg_excess))}</div>
    <div class="cnote">"Right for the reason" counts only reviewed decisions where the stated reason is what happened. A buy that went up for a different reason is "lucky", not right. Trims and exits score the move you avoided.</div></div></div>`;
  const tabs = `<div class="p span2"><h3>DECISIONS <i>${shown.length} shown</i></h3><div class="pb">
    <div class="rbar" style="margin-bottom:6px">${[["open", "NEEDS YOU"], ["reviewed", "REVIEWED"], ["all", "ALL"]].map(([k, l]) => `<button class="${jrFilter === k ? "on" : ""}" onclick="jrFilter='${k}';lsSet('jr:filter','${k}');render()">${l}</button>`).join("")}
      <span style="margin-left:auto"></span><button onclick="jrBackfill()" title="Create entries for Robinhood fills in the last 30 days">BACKFILL 30 DAYS</button></div>
    ${shown.length ? shown.map(jrCard).join("") : `<div class="dim">${jrFilter === "open" ? "Nothing needs you: every decision has a reason and nothing is due for review." : "No entries yet."}</div>`}</div></div>`;
  return `<div class="grid">${head}${tabs}</div>`;
}

function jrCard(e) {
  const [tag, col] = KIND[e.kind] || [e.kind.toUpperCase(), "#888"];
  const res = e.result == null ? "" : `<span class="${cls(e.result)}">${spct(e.result, 1)}</span>${e.excess != null ? ` <span class="dim">(${spct(e.excess, 1)} vs SPY)</span>` : ""}`;
  const reason = e.needs_reason
    ? `<div style="display:flex;gap:6px;margin-top:4px"><input class="wiin" style="flex:1;width:auto" id="jr-r-${e.id}" placeholder="Why? One line, plus what would prove you wrong" onkeydown="if(event.key==='Enter')jrSave(${e.id})">
       <select class="wiin" id="jr-h-${e.id}" style="width:auto" title="When to review">${[30, 90, 180, 365].map((d) => `<option value="${d}" ${d === (e.horizon || 90) ? "selected" : ""}>review in ${d}d</option>`).join("")}</select>
       <button class="mini" onclick="jrSave(${e.id})">Save</button></div>`
    : `<div class="small" style="white-space:normal;margin-top:2px">${esc(e.reason)}</div>`;
  const review = e.due && !e.needs_reason
    ? `<div style="display:flex;gap:6px;margin-top:5px;flex-wrap:wrap;align-items:center"><span class="amb small">Review: did it play out for the reason you gave?</span>
       ${Object.entries(JR.verdicts).map(([k, l]) => `<button class="mini" title="${esc(l)}" onclick="jrVerdict(${e.id},'${k}')">${k.toUpperCase()}</button>`).join("")}</div>`
    : e.verdict ? `<div class="small dim" style="margin-top:2px">Verdict: <b>${esc(JR.verdicts[e.verdict] || e.verdict)}</b>${e.review_note ? " · " + esc(e.review_note) : ""}</div>` : "";
  return `<div class="act" style="border-color:${col}">
    <div class="t"><span class="sev" style="background:${col}">${tag}</span><b class="amb" data-drill="tkr:${esc(e.ticker)}">${esc(e.ticker)}</b>
      <span class="dim small">${esc(e.date)} · ${e.shares ? n(e.shares, 4) + " sh" : ""}${e.price ? " @ " + n(e.price) : ""} · ${esc(e.source)}</span>
      <span style="margin-left:auto" class="small">${res} <span class="dim">${e.days}d · review ${esc(e.review_date)}</span></span></div>
    ${reason}${review}</div>`;
}

async function jrSave(id) {
  const reason = document.getElementById("jr-r-" + id)?.value.trim();
  if (!reason) return;
  const horizon = +(document.getElementById("jr-h-" + id)?.value || 90);
  await fetch("/api/journal/" + id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason, horizon }) });
  await loadJournal(true);
}
async function jrVerdict(id, verdict) {
  const note = verdict === "early" ? "" : (prompt("One line on what actually happened (optional):") || "");
  await fetch("/api/journal/" + id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verdict, review_note: note }) });
  await loadJournal(true);
}
async function jrBackfill() {
  const r = await (await fetch("/api/journal/backfill?days=30", { method: "POST" })).json().catch(() => ({}));
  alert(r.ok ? `Added ${r.added} decision(s) from Robinhood fills since ${r.since}.` : "Backfill needs the Robinhood feed.");
  await loadJournal(true);
}

// ---------- tracking list (Keel only, separate from the research folder's watchlist.md) ----------
let TRK = null, trkAt = 0;
async function loadTracking(force = false) {
  if (!force && TRK && Date.now() - trkAt < 120e3) return TRK;
  try { TRK = await (await fetch("/api/tracking")).json(); trkAt = Date.now(); } catch (e) { TRK = TRK || { ok: false, error: e.message, items: [] }; }
  const el = document.querySelector("[data-trk]");
  if (el && page === "WATCH") el.innerHTML = trackBody();
  return TRK;
}

DRILL.track = (t) => {
  setTimeout(async () => {
    await loadTracking();
    const sel = document.getElementById("trk-theme");
    if (sel) sel.innerHTML = (TRK?.themes || []).map((x) => `<option>${esc(x)}</option>`).join("") + '<option value="__new">+ new theme...</option>';
    const cur = (TRK?.items || []).find((x) => x.ticker === t);
    if (cur) { if (sel) sel.value = cur.theme; const nt = document.getElementById("trk-note"); if (nt) nt.value = cur.note || ""; }
  }, 0);
  return { title: `TRACK · ${esc(t)}`, body: `<div class="dsec" data-card="${esc(t)}"></div>
    <div class="dsec"><div class="small dim">Adds ${esc(t)} to Keel's tracking list: separate from your the research folder watchlist, for names you find interesting but haven't underwritten. It never touches watchlist.md.</div>
      <div style="display:grid;grid-template-columns:90px 1fr;gap:6px;margin-top:8px;align-items:center">
        <span class="dim">Theme</span><span><select id="trk-theme" class="wiin" style="width:auto" onchange="trackNewTheme(this)"><option>Loading...</option></select></span>
        <span class="dim">Why</span><input id="trk-note" class="wiin" style="width:auto" placeholder="One line: what caught your eye" onkeydown="if(event.key==='Enter')trackSave('${esc(t)}')">
      </div>
      <div style="margin-top:8px;display:flex;gap:6px"><button class="mini" onclick="trackSave('${esc(t)}')">Save to tracking</button><span id="trk-msg" class="small"></span></div></div>` };
};
function trackNewTheme(sel) {
  if (sel.value !== "__new") return;
  const v = (prompt("New theme name:") || "").trim();
  if (!v) { sel.selectedIndex = 0; return; }
  const o = document.createElement("option");
  o.textContent = v;
  sel.prepend(o);
  sel.value = v;
}
async function trackSave(t) {
  const theme = document.getElementById("trk-theme")?.value, note = document.getElementById("trk-note")?.value.trim() || "";
  const r = await fetch("/api/tracking", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: t, theme, note }) });
  const m = document.getElementById("trk-msg");
  if (m) m.innerHTML = r.ok ? '<span class="pos">Tracking. It shows under WATCH, TRACKING.</span>' : `<span class="neg">${esc((await r.json().catch(() => ({}))).detail || "Could not save.")}</span>`;
  if (r.ok) { CARDS[t] = null; await loadTracking(true); }
}
async function trackStatus(t, status) {
  if (status === "removed" && !confirm(`Stop tracking ${t}?`)) return;
  await post(`/api/tracking/${encodeURIComponent(t)}/status?status=${status}`);
  CARDS[t] = null;
  await loadTracking(true);
}

function trackPanel() {
  if (!TRK) setTimeout(() => loadTracking(), 0);
  return `<div class="p span2" data-pid="TRACKING"><h3>TRACKING <i>Keel list · interesting, not yet underwritten · separate from the the research folder watchlist</i></h3><div class="pb" data-trk>${trackBody()}</div></div>`;
}
function trackBody() {
  if (!TRK) return '<span class="dim">Loading...</span>';
  const items = TRK.items || [];
  if (!items.length) return '<span class="dim">Nothing tracked yet. On any DES page press Shift+T, or use Track in a ticker window.</span>';
  const groups = {};
  for (const x of items) (groups[x.theme || "Unsorted"] = groups[x.theme || "Unsorted"] || []).push(x);
  const rowHTML = (x) => {
    const pos = x.hi52 && x.lo52 && x.last ? (x.last - x.lo52) / (x.hi52 - x.lo52) : null;
    const gate = x.status === "gate0" ? ' <span class="tag amb">GATE 0 QUEUE</span>' : "";
    const gateBtn = x.status === "gate0" ? "" : `<button class="mini" onclick="trackStatus('${esc(x.ticker)}','gate0')" title="Queue for Gate 0 in chat">Gate 0</button>`;
    return `<tr><td class="amb" data-drill="tkr:${esc(x.ticker)}">${esc(x.ticker)}${gate}</td>
      <td class="small">${esc((x.company || "").slice(0, 26))}</td><td class="small dim">${esc(x.sector || "")}</td>
      <td class="num">${n(x.last)}</td><td class="num ${cls(x.chg_pct)}">${spct(x.chg_pct, 1)}</td>
      <td class="num ${cls(x.since)}">${spct(x.since, 1)} <span class="dim small">${esc(x.added)}</span></td><td class="num ${cls(x.vs_spy)}">${spct(x.vs_spy, 1)}</td>
      <td>${pos != null ? `<span class="rng" style="width:60px"><i style="left:${(pos * 100).toFixed(0)}%"></i></span>` : ""}</td>
      <td class="num">${bigN(x.market_cap)}</td><td class="num">${x.fwd_pe ? n(x.fwd_pe, 1) : "--"}</td><td class="small">${esc(x.earnings || "")}</td>
      <td class="small" style="white-space:normal;max-width:260px">${esc(x.note || "")}</td>
      <td style="white-space:nowrap"><a class="mini" href="#SEC/${encodeURIComponent(x.ticker)}">DES</a> ${gateBtn}
        <button class="mini" onclick="trackStatus('${esc(x.ticker)}','removed')" title="Stop tracking">x</button></td></tr>`;
  };
  return `<table><tr><th>TICKER</th><th>NAME</th><th>SECTOR</th><th class="num">LAST</th><th class="num">DAY</th><th class="num">SINCE ADDED</th><th class="num">vs SPY</th><th>52W</th><th class="num">MKT CAP</th><th class="num">FWD P/E</th><th>EARNINGS</th><th>WHY</th><th></th></tr>
    ${Object.entries(groups).map(([g, xs]) => `<tr class="sec"><td colspan="13">${esc(g.toUpperCase())} · ${xs.length}</td></tr>` + xs.map(rowHTML).join("")).join("")}</table>
    <div class="cnote">Mirrored to the research folder/portfolio/tracking.md by the Mon/Thu reconcile task. A name joins watchlist.md only after Gate 0 passes in chat.</div>`;
}
