/* Shell: top bar, page nav, right rail, command bar and the refresh loop. */
"use strict";

const PAGES = {
  HOME: { label: "HOME", sub: "overview", render: pageHome },
  PORT: { label: "PORT", sub: "book+risk", render: pagePort },
  MKTS: { label: "MKTS", sub: "markets", render: pageMkts },
  WATCH: { label: "WATCH", sub: "pipeline", render: pageWatch },
  SCRN: { label: "SCRN", sub: "screener", render: pageScrn },
  JRNL: { label: "JRNL", sub: "journal", render: pageJournal },
  CAL: { label: "CAL", sub: "catalysts", render: pageCal },
  EVT: { label: "EVT", sub: "event feed", render: pageEvt },
  MAP: { label: "MAP", sub: "heat map", render: pageMap },
  SEC: { label: "DES", sub: "security", render: pageSec },
};
const ALIASES = { HEAT: "MAP", HEATMAP: "MAP", TREEMAP: "MAP", EVENTS: "EVT", SPINOFFS: "EVT", MERGERS: "EVT", ACTIVISTS: "EVT", CALENDAR: "CAL", EVTS: "CAL", CATALYSTS: "CAL", ERN: "CAL", SCREEN: "SCRN", SCREENER: "SCRN", EQS: "SCRN", JOURNAL: "JRNL", PORTFOLIO: "PORT", BOOK: "PORT", RISK: "PORT", MARKETS: "MKTS", WEI: "MKTS", FX: "MKTS", RATES: "MKTS", WL: "WATCH", WATCHLIST: "WATCH", H: "HOME" };

let page = "HOME";
function currentPage() {
  const h = location.hash.replace("#", "").toUpperCase();
  if (h === "SEC" || h.startsWith("SEC/")) return "SEC";
  return PAGES[h] && !PAGES[h].hidden ? h : "HOME";
}

// ---------- top bar ----------
function renderTop() {
  const a = S.account, b = S.benchmark || {}, rel = (a.day_pct ?? 0) - (b.change_pct ?? 0);
  $("kpis").innerHTML = [
    ["NAV", money(a.nav), "", "nav"],
    ["DAY P&L", `${smoney(a.day_pnl)} (${spct(a.day_pct)})`, cls(a.day_pnl), "nav"],
    ["vs SPY", spct(rel), cls(rel), "bench"],
    ["CASH", `${money(a.cash)} · ${pct(a.cash / a.nav)}`, "", "cash"],
    ["UNREAL", smoney(a.unreal_pnl), cls(a.unreal_pnl), "unreal"],
  ].map(([l, v, c, k]) => `<div class="kpi" data-drill="kpi:${k}"><span>${l}</span><b class="${c}">${v}</b></div>`).join("");
  const live = S.source === "robinhood";
  const stale = live && S.broker?.last_ok && Date.now() - new Date(S.broker.last_ok) > 15 * 60e3;
  $("tags").innerHTML = `<span class="tag" data-drill="broker:" style="color:${live && !stale ? "var(--pos)" : "var(--amber)"}">${live ? "LIVE · RH" + (stale ? " · STALE" : "") : "FALLBACK"}</span>
    <span class="tag dim">${esc((S.session || "").toUpperCase())}</span>
    <span class="tag dim" data-drill="regime:" title="${esc(S.regime?.label || "")}">${esc((S.regime?.short || "").slice(0, 36))}</span>
    <span class="tag dim" title="Desk data as of">${esc(S.as_of ? new Date(S.as_of).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false }) : "--")}</span>`;
}
setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) + " ET";
}, 1000);

function renderNav() {
  // DES is always pinned: it reopens the ticker on screen, else the last one you looked at.
  const last = (page === "SEC" && secTicker()) || lsGet("des:last") || "";
  $("nav").innerHTML = Object.entries(PAGES)
    .map(([k, p]) => `<a href="${k === "SEC" ? "#SEC/" + encodeURIComponent(last) : "#" + k}" class="${k === page ? "on" : ""}">${p.label}<span>${k === "SEC" && last ? esc(last) : p.sub}</span></a>`).join("");
}

// ---------- right rail ----------
function actButtons(x) {  // used on cards, pop-ups and window banners
  const done = x.state !== "open";
  return `${x.link ? `<a class="mini" href="${esc(x.link)}" target="_blank" rel="noopener">Robinhood</a>` : ""}
    ${x.source ? `<button class="mini" onclick='openPath(${esc(JSON.stringify(x.source))})'>Source</button>` : ""}
    ${done ? `<button class="mini" onclick="ackAction('${esc(x.id)}','open',0)">Reopen</button>`
      : `<button class="mini" onclick="ackAction('${esc(x.id)}','ack',20)">Done today</button><button class="mini" onclick="ackAction('${esc(x.id)}','snoozed',72)">Snooze 3d</button>`}`;
}
async function ackAction(id, state, hours) {
  await post(`/api/actions/${encodeURIComponent(id)}/ack?state=${state}&hours=${hours}`);
  await refresh();
  if (!$("drill").hidden) closeDrill();
  // a card shown as a window banner: redraw that window so its buttons show the new state
  if (typeof WINS !== "undefined") for (const k of Object.keys(WINS)) if (WINS[k].opt?.act === id) winPaint(k);
}

// ---------- RECONCILE: what Robinhood shows that the research folder doesn't yet (/api/reconcile) ----------
let RECON = null, reconAt = 0;
async function loadRecon(force = false) {
  if (!force && RECON && Date.now() - reconAt < 60e3) return RECON;
  try { RECON = await (await fetch("/api/reconcile")).json(); reconAt = Date.now(); } catch { /* keep the last copy */ }
  const el = document.querySelector("[data-recon]");
  if (el) el.innerHTML = reconBody();
  return RECON;
}
function reconBody() {
  const r = RECON;
  if (!r) return '<span class="dim">Checking...</span>';
  if (!r.live) return `<span class="dim">${esc(r.note || "Robinhood not connected.")}</span>`;
  if (!r.items.length) return '<span class="dim">In sync: portfolio.md and the return log match Robinhood.</span>';
  return r.items.map((x, i) => `<div class="act" style="border-color:${x.judgment ? "var(--amber)" : "var(--blue)"}">
      <div class="t"><span class="sev" style="background:${x.judgment ? "var(--amber)" : "var(--blue)"}">${x.judgment ? "YOU" : "AUTO"}</span><b class="amb">${esc(x.ticker || "")}</b></div>
      <div>${esc(x.title)}</div><div class="dim small" style="white-space:normal">${esc(x.detail)}</div>
      ${x.judgment ? `<div class="small amb" style="white-space:normal">${esc(x.judgment)}</div>` : ""}
      <div style="display:flex;gap:4px;margin-top:3px"><button class="mini" onclick="reconCopy(${i})">Copy draft</button><button class="mini" onclick="reconDone('${esc(x.id)}')">Done</button></div></div>`).join("")
    + '<div class="cnote">AUTO items are written to the research folder by the reconcile task (Mon and Thu, 9:00 AM). YOU items need your call.</div>';
}
function reconCopy(i) { const x = RECON?.items?.[i]; if (x) navigator.clipboard?.writeText(x.draft).catch(() => prompt("Copy:", x.draft)); }
async function reconDone(id) { await post(`/api/reconcile/${encodeURIComponent(id)}/done`); await loadRecon(true); }
setInterval(() => loadRecon(), 60e3);

// ---------- POSITION CHANGES: what moved in the account since the last check ----------
const CHG_TAG = { new: ["NEW", "var(--pos)"], add: ["ADD", "var(--blue)"], trim: ["TRIM", "var(--amber)"], exit: ["EXIT", "var(--neg)"] };
const fmtSh = (x) => (x == null ? "--" : (+x).toFixed(4).replace(/\.?0+$/, ""));
function changeLine(c) {
  const [tag, col] = CHG_TAG[c.kind] || [c.kind.toUpperCase(), "#888"];
  return `<div class="act" style="border-color:${col}" data-drill="tkr:${esc(c.ticker)}">
    <div class="t"><span class="sev" style="background:${col}">${tag}</span><b class="amb">${esc(c.ticker)}</b>
      <span class="dim small" style="margin-left:auto">${esc(c.date)}${c.seen ? "" : ' <b class="amb">NEW</b>'}</span></div>
    <div>${fmtSh(c.old)} → ${fmtSh(c.new)} sh <span class="${c.delta > 0 ? "pos" : "neg"}">(${c.delta > 0 ? "+" : ""}${fmtSh(c.delta)})</span>${c.price ? ` @ ${n(c.price)}` : ""}</div>
    ${c.matched ? `<div class="dim small">matches ${c.fills} Robinhood fill${c.fills === 1 ? "" : "s"}</div>` : `<div class="small amb" style="white-space:normal">${esc(c.note || "")}</div>`}</div>`;
}
const EVSEV = { high: ["HIGH", "var(--neg)"], medium: ["MED", "var(--amber)"], low: ["LOW", "#666"] };
function filingLine(e) {
  const [tag, col] = EVSEV[e.severity] || EVSEV.low;
  const own = posBy(e.ticker) ? "held" : watchBy(e.ticker) ? "watch" : "tracked";
  return `<div class="act" style="border-color:${col}">
    <div class="t"><span class="sev" style="background:${col}">${tag}</span><b class="amb" data-drill="tkr:${esc(e.ticker)}">${esc(e.ticker)}</b>
      <span class="dim small">${esc(e.form)} · ${esc(e.date)} · ${own}${e.seen ? "" : ' <b class="amb">NEW</b>'}</span></div>
    <div style="white-space:normal"><a href="${esc(e.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(e.title)}</a></div></div>`;
}
async function eventsSeen() { await post("/api/events/seen"); await refresh(); }
async function eventsCheck() {
  try { const r = await post("/api/events/check"); await refresh(); alert(`Checked SEC filings: ${r.new} new.`); }
  catch (e) { alert(e.message); }
}
async function changesSeen() { await post("/api/changes/seen"); await refresh(); }

function renderRail() {
  if (!RECON) setTimeout(() => loadRecon(), 0);
  const nRecon = RECON?.items?.length || 0;
  const acts = (S.actions || []).filter((x) => x.state === "open");
  const order = { act: 0, review: 1, watch: 2 };
  acts.sort((x, y) => (order[x.severity] ?? 3) - (order[y.severity] ?? 3));
  const movers = [...S.positions].sort((x, y) => Math.abs(y.day_pct || 0) - Math.abs(x.day_pct || 0)).slice(0, 6);
  const earn = S.earnings_upcoming || [];
  const panels = [["DO TODAY", `<div class="p"><h3>DO TODAY <i>${acts.length} open</i>${railX("DO TODAY")}</h3><div class="pb">
      ${acts.map((x) => `<div class="act" style="border-color:${SEV[x.severity] || "#555"}" data-drill="act:${esc(x.id)}">
        <div class="t"><span class="sev" style="background:${SEV[x.severity] || "#555"}">${esc(x.severity.toUpperCase())}</span><b class="amb">${esc(x.ticker || "")}</b></div>
        <div>${esc(x.title)}</div></div>`).join("") || '<span class="dim">Nothing needs you right now.</span>'}
    </div></div>`], ["FILINGS", (() => { const ev = (S.sec_events || []).filter((e) => e.severity !== "low" || !e.seen).slice(0, 14), unseen = (S.sec_events || []).filter((e) => !e.seen && e.severity !== "low").length;
      return `<div class="p"><h3>FILINGS <i>${unseen ? `${unseen} new · ` : ""}SEC, your names, 14 days</i>${railX("FILINGS")}</h3><div class="pb">
      ${ev.length ? ev.map(filingLine).join("") : '<span class="dim">No material filings on your held, watchlist or tracked names in 14 days.</span>'}
      <div style="display:flex;gap:6px;margin-top:4px">${unseen ? '<button class="mini" onclick="eventsSeen()">Mark all seen</button>' : ""}<button class="mini" onclick="eventsCheck()" title="Check SEC now (otherwise every 30 minutes)">Check now</button></div>
      <div class="cnote">8-K items, insider buys and sales (Form 4), late-filing notices, offerings and activist stakes. HIGH on a held name also lands in DO TODAY.</div></div></div>`; })()], ["POSITION CHANGES", (() => { const ch = S.changes || [], unseen = ch.filter((c) => !c.seen).length;
      return `<div class="p"><h3>POSITION CHANGES <i>${unseen ? `${unseen} new · ` : ""}last 14 days</i>${railX("POSITION CHANGES")}</h3><div class="pb">
      ${ch.length ? ch.slice(0, 12).map(changeLine).join("") : '<span class="dim">No buys, adds, trims or exits in the last 14 days.</span>'}
      ${unseen ? '<button class="mini" onclick="changesSeen()">Mark all seen</button>' : ""}
      <div class="cnote">Checked against Robinhood on every refresh; each change is matched to its fill.</div></div></div>`; })()], ["RECONCILE", `<div class="p"><h3>RECONCILE <i>${nRecon ? `${nRecon} to sync${RECON.needs_you ? ` · ${RECON.needs_you} need you` : ""}` : "Robinhood vs your files"}</i>${railX("RECONCILE")}</h3><div class="pb" data-recon>${reconBody()}</div></div>`], ["BIGGEST MOVES", `<div class="p"><h3>BIGGEST MOVES <i>held</i>${railX("BIGGEST MOVES")}</h3>${sparkBar()}<div class="pb"><table>
      ${movers.map((p) => `<tr data-drill="tkr:${p.ticker}"><td class="amb">${p.ticker}</td><td style="width:45%" class="spark" data-sym="${p.ticker}"></td><td class="num ${cls(p.day_pct)}">${spct(p.day_pct, 1)}</td></tr>`).join("")}
    </table></div></div>`], ["EARNINGS", `<div class="p"><h3>EARNINGS <i>next 14d</i>${railX("EARNINGS")}</h3><div class="pb"><table>
      ${earn.length ? earn.map((e) => `<tr data-drill="tkr:${esc(e.ticker)}"><td class="amb">${esc(e.ticker)}</td><td>${esc(e.date)}</td><td class="dim">${esc(e.timing || "")}</td><td class="dim">${e.held ? "held" : "watch"}</td></tr>`).join("") : '<tr><td class="dim">none</td></tr>'}
    </table></div></div>`], ["REGIME", `<div class="p"><h3>REGIME <i>${esc(S.regime?.as_of || "")}</i>${railX("REGIME")}</h3><div class="pb"><table>
      ${(S.regime?.axes || []).map((x) => `<tr data-drill="regime:"><td class="amb" style="width:70px">${esc(x.axis)}</td><td style="white-space:normal">${esc(x.classification.split("—")[0])}</td></tr>`).join("")}
    </table></div></div>`]];
  const hid = railHidden();
  $("rail").innerHTML = panels.filter(([k]) => !hid.includes(k)).map(([, h]) => h).join("")
    + (hid.length ? `<div class="mtool"><span class="dim">CLOSED</span>${hid.map((k) => `<button onclick="railShow('${k}')">+ ${k}</button>`).join("")}</div>` : "");
}
const railHidden = () => { try { return JSON.parse(lsGet("rail:hidden") || "[]"); } catch { return []; } };
const railX = (k) => `<span class="pclose" title="Close" onclick="event.stopPropagation();railHide('${k}')">×</span>`;
function railHide(k) { const h = railHidden(); h.push(k); lsSet("rail:hidden", JSON.stringify(h)); renderRail(); hydrate($("rail")); }
function railShow(k) { lsSet("rail:hidden", JSON.stringify(railHidden().filter((x) => x !== k))); renderRail(); hydrate($("rail")); }
function toggleRail() { lsSet("rail:off", lsGet("rail:off") === "1" ? "0" : "1"); applyRail(); }
function applyRail() {
  const off = lsGet("rail:off") === "1";
  document.querySelector(".shell").classList.toggle("norail", off);
  $("railbtn").classList.toggle("on", !off);
}

// ---------- render + refresh ----------
function render() {
  if (window.mkDragging) return; // never redraw under a panel being resized or moved
  page = currentPage();
  renderTop();
  renderNav();
  const main = $("main"), scroll = main.scrollTop;
  main.innerHTML = PAGES[page].render();
  if (page !== "MKTS") applyLayout();
  main.scrollTop = scroll;
  renderRail();
  hydrate(document.querySelector(".shell"));
  hydratePerf(main);
  refreshDrill();
  if (typeof winRefresh === "function") winRefresh();
}

async function refresh() {
  try {
    S = await fetchState();
    if (!S.ready) {
      $("main").innerHTML = `<div class="loading">${esc(S.error || `Starting up: loading prices${window.KEEL_PUBLIC ? "" : " and the book"}...`)}</div>`;
      setTimeout(refresh, 3000);
      return;
    }
    render();
  } catch (e) {
    $("tags").innerHTML = `<span class="tag neg">KEEL OFFLINE: ${esc(e.message)}</span>`;
  }
}
window.addEventListener("hashchange", () => { if (S?.ready) { $("main").scrollTop = 0; render(); } });

// ---------- manual refresh and auto cadence ----------
let refreshing = false;
/** Pull everything now: Robinhood + Yahoo on the server (about 6 s), then the current page's own data. */
function refreshToast(r) {
  const ch = r?.changes || [];
  const msg = r?.source !== "robinhood" ? "Refreshed (Robinhood not connected: no position check)."
    : ch.length ? `Refresh found ${ch.length} change${ch.length === 1 ? "" : "s"}: ` + ch.map((c) => `${c.ticker} ${({ new: "new", add: "added", trim: "trimmed", exit: "exited" })[c.kind]}`).join(", ")
    : "Refreshed: positions match the last check, no new buys, trims or exits.";
  let t = $("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.className = "toast" + (ch.length ? " hit" : "");
  clearTimeout(t._h); t._h = setTimeout(() => { t.className = "toast gone"; }, ch.length ? 12000 : 5000);
  if (ch.length) loadRecon(true);
  return r;
}

async function refreshAll() {
  if (refreshing) return;
  refreshing = true;
  const b = $("rfbtn");
  b.classList.add("busy");
  b.textContent = "REFRESHING";
  try {
    for (const k of Object.keys(SC)) if (/\|(1D|5D)$/.test(k)) delete SC[k];
    const jobs = [post("/api/refresh?wait=true").then(refreshToast)];
    if (page === "MKTS") jobs.push(loadMkts(true), loadNews(true));
    if (page === "HOME") jobs.push(loadBrief(true));
    if (page === "PORT") jobs.push(loadPerf(true));
    if (page === "SEC") jobs.push(loadSec(secTicker(), true));
    await Promise.allSettled(jobs);
    await refresh();
  } finally {
    refreshing = false;
    b.classList.remove("busy");
    b.textContent = "⟳ REFRESH";
  }
}
let autoTimer = null;
function setAuto(sec) {
  lsSet("auto", String(sec));
  clearInterval(autoTimer);
  // AUTO OFF still reloads the desk's own snapshot every 30 s (the server refreshes on its own schedule).
  autoTimer = +sec > 0 ? setInterval(refreshAll, +sec * 1000) : setInterval(refresh, 30000);
}

// ---------- command bar ----------
// Local matches (pages, holdings, watchlist, markets) appear at once; Yahoo name search fills in after a
// short pause, so "arrow" finds ARW. The highlighted company is previewed on the right.
// Enter = quick view, Shift+Enter = security page (DES).
let suggIdx = 0, suggList = [], suggRemote = [], suggQ = "", searchTimer = null, previewTimer = null;
const isCompany = (s) => s && ["held", "watch", "ticker", "search", "des"].includes(s.kind);

/** "BUY 2 HUBB", "BUY HUBB 2", "SELL $50 NXT", "BUY HUBB" (starter size), "B 2 HUBB" -> {side, ticker, shares|dollars} */
function parseTrade(q) {
  const m = q.match(/^(BUY|SELL|B|S)\s+(.+)$/);
  if (!m) return null;
  const side = m[1].startsWith("B") ? "buy" : "sell";
  let ticker = null, shares = null, dollars = null;
  for (const tok of m[2].trim().split(/\s+/)) {
    if (/^\$[\d.,]+$/.test(tok)) dollars = +tok.slice(1).replace(/,/g, "");
    else if (/^[\d.]+$/.test(tok)) shares = +tok;
    else if (/^[A-Z][A-Z0-9.\-]{0,9}$/.test(tok) && !ticker) ticker = tok;
    else return null;
  }
  return ticker ? { side, ticker, shares, dollars } : null;
}

function buildSuggestions(q) {
  q = q.trim().toUpperCase();
  if (!q) return [];
  const out = [];
  // N / NEWS / TOP: the news reader window
  if (["N", "NEWS", "TOP", "NI"].includes(q)) return [{ key: "N", label: "News reader: the wire, your names, WSJ, topics", kind: "news" }];
  // "TOST SI", "EURUSD=X GP": a security, then a function code -> that function in the security's window
  const sf = q.match(/^([A-Z0-9^][A-Z0-9.\-=^]{0,11})\s+([A-Z]{2,6})$/);
  if (sf && typeof SECFN !== "undefined" && SECFN[sf[2]]) return [{ key: sf[2], label: `${SECFN[sf[2]][0]} · ${sf[1]}`, kind: "secfn", t: sf[1] }];
  // Function codes (FLNG, ATTR, TRIP, SI, THEME, IMAP, EVTS...): "FLNG" opens it, "FLNG tiger" opens it searching
  const fm = q.match(/^([A-Z]{2,6})(?:\s+(.*))?$/);
  if (fm && typeof FN !== "undefined") {
    // With more words after it, only an exact code is a function ("FLNG tiger"); "the trade desk", "fund ..." or
    // "short ..." are ordinary searches, not THEME / FLNG / SI.
    if (fm[2] && FN[fm[1]]) return [{ key: fm[1], label: `${FN[fm[1]].name} · search "${fm[2].toLowerCase()}"`, kind: "function", arg: { q: fm[2].toLowerCase() } }];
    const fnOut = [];
    if (!fm[2]) {
      for (const [c, f] of Object.entries(FN)) if (c.startsWith(fm[1]) || (fm[1].length >= 3 && f.name.toUpperCase().includes(fm[1])))
        fnOut.push({ key: c, label: f.name, kind: "function", arg: null });
      for (const [c, [, l]] of Object.entries(FN_LINKS)) if (c.startsWith(fm[1])) fnOut.push({ key: c, label: l, kind: "function" });
      for (const [c, l] of Object.entries(BOOK_CODES)) if (c.startsWith(fm[1])) fnOut.push({ key: c, label: l, kind: "book" });
      // an exact code first, then codes that start with what was typed, then name matches ("EXPO" is EXPO, not "Exposure themes")
      const rank = (o) => (o.key === fm[1] ? 0 : o.key.startsWith(fm[1]) ? 1 : 2);
      fnOut.sort((a, b) => rank(a) - rank(b));
    }
    suggFnExtra = fnOut;  // a bare code: functions first, then tickers with the same letters (TRIP is also a ticker)
    // ...and a security function runs on the loaded security first (SI with TOST loaded = TOST's short interest)
    if (!fm[2] && typeof SECFN !== "undefined" && LOADED && secFits(fm[1]))
      suggFnExtra = [{ key: fm[1], label: `${SECFN[fm[1]][0]} · ${isFred(LOADED) ? LOADED.slice(5) : LOADED} (loaded)`, kind: "secfn", t: LOADED }, ...suggFnExtra];
  } else suggFnExtra = [];
  if (!fm && typeof SECFN !== "undefined" && LOADED && secFits(q)) suggFnExtra = [{ key: q, label: `${SECFN[q][0]} · ${LOADED} (loaded)`, kind: "secfn", t: LOADED }];
  const f13 = q.match(/^13F\s*(.*)$/);
  if (f13) {
    const rest = f13[1].trim(), listed = (typeof F13 !== "undefined" ? Object.values(F13).flatMap((x) => x?.d?.funds || []) : []);
    const seen = new Set(), out13 = [];
    for (const f of listed) if (!seen.has(f.cik) && (!rest || f.name.toUpperCase().includes(rest))) { seen.add(f.cik); out13.push({ key: "13F", label: f.name, kind: "13f", cik: f.cik }); }
    if (suggFundQ === rest) for (const f of suggFunds) if (f.files13f && !seen.has(f.cik)) { seen.add(f.cik); out13.push({ key: "13F", label: f.name, kind: "13f", cik: f.cik }); }
    if (!out13.length) out13.push({ key: "13F", label: rest.length >= 2 ? "searching SEC for funds..." : "type a fund name, e.g. 13F berkshire", kind: "13f" });
    return out13.slice(0, 12);
  }
  const tr = parseTrade(q);
  if (tr) return [{ key: tr.ticker, label: `pre-trade check: ${tr.side} ${tr.shares != null ? tr.shares + " sh" : tr.dollars != null ? "$" + tr.dollars : "starter size"}`, kind: "whatif", spec: tr }];
  const des = q.match(/^(?:DES\s+)?([A-Z][A-Z0-9.\-]{0,9})(?:\s+DES)?$/);
  if (des && q.split(/\s+/).includes("DES") && des[1] !== "DES") return [{ key: des[1], label: "security page", kind: "des" }];
  for (const [k, p] of Object.entries(PAGES)) if (!p.hidden && (k.startsWith(q) || p.sub.toUpperCase().includes(q))) out.push({ key: k, label: p.sub, kind: "page" });
  for (const [a, k] of Object.entries(ALIASES)) if (a.startsWith(q) && !out.some((o) => o.key === k)) out.push({ key: k, label: PAGES[k].sub + " (" + a + ")", kind: "page" });
  for (const p of S?.positions || []) if (p.ticker.startsWith(q) || (p.company || "").toUpperCase().includes(q)) out.push({ key: p.ticker, label: p.company || "", kind: "held" });
  for (const w of S?.watchlist || []) if ((w.ticker.startsWith(q) || (w.company || "").toUpperCase().includes(q)) && !out.some((o) => o.key === w.ticker)) out.push({ key: w.ticker, label: w.company || "", kind: "watch" });
  for (const c of S?.cross_asset || []) if (c.symbol.toUpperCase().startsWith(q) || c.label.toUpperCase().includes(q)) out.push({ key: c.symbol, label: c.label, kind: "market" });
  // Indexes you can only see through funds (Bloomberg / Barclays Aggregate, MSCI ...)
  const more = suggMore.q === q ? suggMore : { eco: [], proxies: [] };
  for (const p of more.proxies.slice(0, 3)) if (!out.some((o) => o.key === p.symbol)) out.push({ key: p.symbol, label: `${p.index}${p.proxy ? " · fund that tracks it" : ""}`, kind: "index" });
  // Yahoo search results for this query (any company, covered or not); leave room for economic data below
  if (suggQ === q) for (const r of suggRemote.slice(0, more.eco.length ? 6 : 10)) if (!out.some((o) => o.key === r.symbol)) out.push({ key: r.symbol, label: `${r.name || ""} · ${r.exchange || ""}${r.type && r.type !== "EQUITY" ? " · " + r.type : ""}`, kind: "search", type: r.type });
  // FRED economic series after the securities
  for (const e of more.eco.slice(0, 4)) out.push({ key: e.id.length > 10 ? e.id.slice(0, 9) + "…" : e.id, label: `${e.title} · ${[e.freq, e.sa, e.id.length > 10 ? e.id : ""].filter(Boolean).join(" · ")}`, kind: "economic", eco: e });
  if (/^[A-Z][A-Z.\-]{0,6}$/.test(q) && !out.some((o) => o.key === q)) out.push({ key: q, label: "look up any ticker", kind: "ticker" });
  const exact = out.findIndex((o) => o.key === q && o.kind !== "page" && o.kind !== "market");
  if (exact >= 0) out.splice(exact + 1, 0, { key: q, label: "security page (DES)", kind: "des" });
  return [...suggFnExtra, ...out].slice(0, 12);
}

function drawSuggestions(keepIdx = false) {
  const box = $("sugg");
  suggList = buildSuggestions($("cmd").value);
  if (!keepIdx) suggIdx = 0;
  suggIdx = Math.min(suggIdx, Math.max(0, suggList.length - 1));
  box.hidden = !suggList.length;
  box.innerHTML = `<div class="slist">${suggList.map((s, i) => `<div class="${i === suggIdx ? "on" : ""}" data-i="${i}"><b>${esc(s.key)}</b><span>${esc(s.label)}</span><i>${s.kind}</i></div>`).join("")}
    <div class="shint">Enter: open in a window · Shift+Enter: DES Launchpad page · then type a function (DES GP FA SI RV ERN ...) · ↑↓ to choose</div></div><div class="sprev" id="sprev"></div>`;
  schedulePreview();
}
function schedulePreview() {
  clearTimeout(previewTimer);
  const s = suggList[suggIdx], el = $("sprev");
  if (!el) return;
  if (!isCompany(s)) { el.innerHTML = '<span class="dim small">Pick a company to preview it.</span>'; return; }
  const t = s.key;
  el.innerHTML = CARDS[t] ? cardHTML(CARDS[t].d) : '<span class="dim small">Loading ' + esc(t) + "...</span>";
  previewTimer = setTimeout(async () => {
    const c = await getCard(t).catch(() => null);
    if ($("sprev") && suggList[suggIdx]?.key === t) $("sprev").innerHTML = cardHTML(c);
  }, 200);
}
let suggFunds = [], suggFundQ = null, suggFnExtra = [], suggMore = { q: null, eco: [], proxies: [] };
function showSuggestions() {
  drawSuggestions();
  clearTimeout(searchTimer);
  const raw = $("cmd").value.trim(), q = raw.toUpperCase();
  const f13 = q.match(/^13F\s+(.{2,})$/);
  if (f13) {  // fund search on SEC instead of the ticker search
    const rest = f13[1].trim();
    searchTimer = setTimeout(async () => {
      try {
        const r = await (await fetch("/api/13f/funds/search?q=" + encodeURIComponent(rest))).json();
        if ($("cmd").value.trim().toUpperCase() !== q) return;
        suggFundQ = rest; suggFunds = r.results || [];
        drawSuggestions(true);
      } catch { /* optional */ }
    }, 300);
    return;
  }
  if (raw.length < 2 || /\bDES\b/.test(q)) return;
  searchTimer = setTimeout(async () => {
    try {
      const r = await (await fetch("/api/lookup?q=" + encodeURIComponent(raw))).json();
      if ($("cmd").value.trim().toUpperCase() !== q) return;
      suggQ = q; suggRemote = r.results || [];
      drawSuggestions(true);
    } catch { /* search is optional */ }
  }, 250);
  // Economic data (FRED) and indexes seen through the funds that track them, in parallel
  if (raw.length >= 3) setTimeout(async () => {
    try {
      const r = await (await fetch("/api/lookup/more?q=" + encodeURIComponent(raw))).json();
      if ($("cmd").value.trim().toUpperCase() !== q) return;
      suggMore = { q, eco: r.eco || [], proxies: r.proxies || [] };
      drawSuggestions(true);
    } catch { /* optional */ }
  }, 300);
}
function goSuggestion(s, toDes = false) {
  $("cmd").value = "";
  $("sugg").hidden = true;
  $("cmd").blur();
  if (!s) return;
  if (s.kind === "page") { location.hash = s.key; return; }
  if (s.kind === "13f") { if (s.cik) openFn("FLNG", { cik: s.cik, name: s.label }); return; }
  if (s.kind === "function") { openFn(s.key, s.arg || {}); return; }
  if (s.kind === "book") return openBook(s.key);
  if (s.kind === "news") return openNews();
  // DES typed as a command ("TOST DES", or DES with a stock loaded) is the Launchpad page for that stock
  if (s.kind === "secfn" && s.key === "DES" && !s.t.startsWith("FRED:")) {
    const cls = WINS["SEC:" + s.t]?.cls;
    if (!cls || cls === "Equity") { location.hash = "SEC/" + encodeURIComponent(s.t); setLoaded(s.t); return; }
  }
  if (s.kind === "secfn") return openSec(s.t, s.key);
  if (s.kind === "economic") return openSec("FRED:" + (s.eco?.id || s.key), "GP", { label: s.eco?.title || s.key });
  if (s.kind === "index") return openSec(s.key);
  if (s.kind === "whatif") return openDrill(`whatif:${s.spec.side}|${s.spec.ticker}|${s.spec.shares ?? ""}|${s.spec.dollars ?? ""}`);
  if (s.kind === "des" || (toDes && isCompany(s))) { closeDrill(); location.hash = "SEC/" + s.key; return; }
  // On the DES Launchpad page, a stock you search replaces the page's security (the Launchpad follows what you load);
  // anything that has no Launchpad (currency, index, future, crypto) opens in its window
  const stock = ["held", "watch", "ticker"].includes(s.kind) || (s.kind === "search" && (!s.type || s.type === "EQUITY"));
  if (page === "SEC" && stock) { location.hash = "SEC/" + encodeURIComponent(s.key); setLoaded(s.key); return; }
  openSec(s.key);
}
$("cmd").addEventListener("input", showSuggestions);
$("cmd").addEventListener("keydown", (e) => {
  const box = $("sugg");
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!suggList.length) return;
    suggIdx = (suggIdx + (e.key === "ArrowDown" ? 1 : -1) + suggList.length) % suggList.length;
    box.querySelectorAll(".slist > div[data-i]").forEach((d, i) => d.classList.toggle("on", i === suggIdx));
    schedulePreview();
  } else if (e.key === "Enter") {
    e.preventDefault();
    goSuggestion(suggList[suggIdx], e.shiftKey);
  } else if (e.key === "Escape") {
    $("cmd").value = ""; box.hidden = true; $("cmd").blur();
  }
});
$("sugg").addEventListener("mousedown", (e) => {
  const d = e.target.closest("[data-i]");
  if (d) { e.preventDefault(); goSuggestion(suggList[+d.dataset.i], e.shiftKey); }
  else e.preventDefault(); // clicks on the preview keep the bar open
});
$("sugg").addEventListener("mouseover", (e) => {
  const d = e.target.closest("[data-i]");
  if (d && +d.dataset.i !== suggIdx) {
    suggIdx = +d.dataset.i;
    $("sugg").querySelectorAll(".slist > div[data-i]").forEach((x, i) => x.classList.toggle("on", i === suggIdx));
    schedulePreview();
  }
});
$("cmd").addEventListener("blur", () => setTimeout(() => { $("sugg").hidden = true; }, 150));
// "/" or any letter typed outside an input jumps to the command bar, like a terminal.
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || document.activeElement?.tagName === "INPUT") return;
  if (e.key === "/" || (/^[a-zA-Z]$/.test(e.key) && $("drill").hidden)) {
    $("cmd").focus();
    if (e.key === "/") e.preventDefault();
  }
});

$("rfbtn").addEventListener("click", refreshAll);
$("railbtn").addEventListener("click", toggleRail);
$("autosel").value = lsGet("auto") || "0";
$("autosel").addEventListener("change", (e) => setAuto(e.target.value));
document.addEventListener("keydown", (e) => { if (e.key === "F5" && !e.ctrlKey) { e.preventDefault(); refreshAll(); } });
applyRail();
refresh();
setAuto(lsGet("auto") || "0");
