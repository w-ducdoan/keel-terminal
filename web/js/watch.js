/* WATCH: the pipeline. Watchlist names against their Buy-below, fair value and sizing, the event pod,
   and every dated catalyst. Read-only: entries still go through Gate 0 / the trigger-hit refresh. */
"use strict";

const GICS = ["Financials", "Industrials", "Information Technology", "Health Care", "Consumer Discretionary", "Consumer Staples", "Utilities", "Energy", "Materials", "Real Estate", "Communication Services"];
const DEFAULT_STARTER = 2.0; // % of NAV when a memo gives no starter size
const APPROACH = () => S.caps?.watch_approach ?? 0.05;

// Fields parsed from the watchlist's free text
function wInfo(w) {
  const fv = w.fair_value || "", ec = w.entry_criteria || "";
  const num = (re) => { const m = fv.match(re); return m ? parseFloat(m[1].replace(/,/g, "")) : null; };
  const bear = num(/bear \$([\d,.]+)/i), ud = num(/up\/down ([\d.]+)x/i);
  const tier = (ec.match(/\bT([1-3])\b/) || [])[1];
  const review = (ec.match(/Review (\d{4}-\d{2}-\d{2})/) || [])[1] || null;
  const starterPct = w.starter_pct ?? null;
  const nav = S.account?.nav || 0, px = w.price || 0;
  const sp = starterPct ?? DEFAULT_STARTER;
  const dollars = nav * sp / 100, shares = px ? dollars / px : null;
  let gics = (w.sector || "").split("/")[0].trim();
  if (!GICS.includes(gics) && posBy(w.ticker)?.sector) gics = posBy(w.ticker).sector; // held: use the book's sector
  const cap = GICS.includes(gics) ? capFor(gics) : null;   // funds and diversifiers have no sector cap
  const secNow = cap ? (S.aggregates?.sector?.[gics] || 0) : null;
  const secAfter = secNow != null && cap ? secNow + sp / 100 : null;
  const bearDown = bear && px ? bear / px - 1 : null;
  const dEarn = w.earnings?.date ? daysTo(w.earnings.date) : null;
  return { bear, ud, tier, review, starterPct, sp, dollars, shares, gics, secNow, cap, secAfter, bearDown, dEarn, dReview: review ? daysTo(review) : null };
}
function distState(d) {
  if (d == null) return "none";
  if (d <= 0) return "hit";
  if (d <= APPROACH()) return "near";
  if (d <= 0.15) return "mid";
  return "far";
}
const DCOL = { hit: "var(--pos)", near: "var(--amber)", mid: "var(--blue)", far: "#444", none: "#333" };

let wSort = JSON.parse(lsGet("watch:sort") || '{"k":"dist_pct","d":1}');
function sortWatch(k) { wSort = { k, d: wSort.k === k ? -wSort.d : 1 }; lsSet("watch:sort", JSON.stringify(wSort)); render(); }
const WCOLS = [["ticker", "TKR"], ["price", "LAST", 1], ["day_pct", "DAY", 1], ["buy_below", "BUY <", 1], ["dist_pct", "DIST", 1], ["", "", 0],
  ["base_value", "BASE", 1], ["upside_base", "UPSIDE", 1], ["bearDown", "BEAR", 1], ["ud", "UP/DN", 1], ["tier", "TIER"], ["dollars", "STARTER", 1],
  ["secAfter", "SECTOR AFTER", 1], ["dEarn", "EARN", 1], ["dReview", "REVIEW", 1], ["next_catalyst", "NEXT CATALYST"], ["spark", "", 0]];

function pageWatch() {
  if (!MK) setTimeout(() => loadMkts().then(() => { if (page === "WATCH") render(); }), 0); // macro release dates for the calendar
  const wl = (S.watchlist || []).map((w) => ({ ...w, ...wInfo(w) }));
  const pod = S.event_pod || [];
  const cnt = (f) => wl.filter(f).length;

  const strip = [
    ["At / through buy-below", cnt((w) => distState(w.dist_pct) === "hit"), "hit"],
    [`Within ${pct(APPROACH(), 0)}`, cnt((w) => distState(w.dist_pct) === "near"), "near"],
    ["5-15% away", cnt((w) => distState(w.dist_pct) === "mid"), "mid"],
    ["More than 15%", cnt((w) => distState(w.dist_pct) === "far"), "far"],
    ["No buy-below", cnt((w) => w.dist_pct == null), "none"],
    ["Event pod armed", pod.filter((e) => e.status === "armed").length + " / " + pod.length, "pod"],
    ["Earnings in 14d", cnt((w) => w.dEarn != null && w.dEarn >= 0 && w.dEarn <= 14), "earn"],
    ["Reviews due 30d", cnt((w) => w.dReview != null && w.dReview <= 30), "rev"],
  ];
  const pipe = `<div class="p span2"><h3>PIPELINE <i>${wl.length} watchlist names · ${pod.length} event-pod trades · click a box to filter</i></h3><div class="pb"><div class="stats" style="grid-template-columns:repeat(8,1fr)">
    ${strip.map(([l, v, k]) => `<div class="stat ${wFilter === k ? "sel" : ""}" onclick="setWatchFilter('${k}')" style="cursor:pointer;border-left:3px solid ${DCOL[k] || "#555"}"><span>${l}</span><b>${v}</b></div>`).join("")}</div></div></div>`;

  const filt = { hit: (w) => distState(w.dist_pct) === "hit", near: (w) => distState(w.dist_pct) === "near", mid: (w) => distState(w.dist_pct) === "mid", far: (w) => distState(w.dist_pct) === "far",
    none: (w) => w.dist_pct == null, earn: (w) => w.dEarn != null && w.dEarn >= 0 && w.dEarn <= 14, rev: (w) => w.dReview != null && w.dReview <= 30, pod: () => true }[wFilter] || (() => true);
  const rows = wl.filter(filt).sort((x, y) => {
    const A = x[wSort.k], B = y[wSort.k];
    if (A == null && B == null) return 0; if (A == null) return 1; if (B == null) return -1;
    return (typeof A === "string" ? A.localeCompare(B) : A - B) * wSort.d;
  });
  const table = `<div class="p span2"><h3>WATCHLIST <i>${rows.length}${wFilter ? " (filtered: " + esc(wFilter) + ")" : ""} · sizing at ${money(S.account?.nav, 0)} NAV · ${DEFAULT_STARTER}% starter where the memo gives none</i></h3>${sparkBar()}<div class="pb" style="overflow:auto"><table class="ptable wtable">
    <tr>${WCOLS.map(([k, l, num]) => `<th class="${num ? "num" : ""} ${k && k !== "spark" ? "sort" : ""}" ${k && k !== "spark" ? `onclick="sortWatch('${k}')"` : ""}>${l}${wSort.k === k ? (wSort.d < 0 ? " ▼" : " ▲") : ""}</th>`).join("")}</tr>
    ${rows.map((w) => {
      const st = distState(w.dist_pct), over = w.secAfter != null && w.cap && w.secAfter > w.cap;
      return `<tr data-drill="tkr:${esc(w.ticker)}" class="w-${st}"><td><b class="amb">${esc(w.ticker)}</b>${w.held ? ' <span class="tagh">HELD</span>' : ""}<div class="dim small">${esc((w.company || "").slice(0, 26))}</div></td>
        <td class="num">${n(w.price)}</td><td class="num ${cls(w.day_pct)}">${spct(w.day_pct, 1)}</td><td class="num">${w.buy_below ? n(w.buy_below) : "--"}</td>
        <td class="num" style="color:${DCOL[st]}"><b>${w.dist_pct == null ? "--" : spct(w.dist_pct, 1)}</b></td><td style="width:70px">${distBar(w.dist_pct)}</td>
        <td class="num">${w.base_value ? n(w.base_value) : "--"}</td><td class="num ${cls(w.upside_base)}">${spct(w.upside_base, 0)}</td>
        <td class="num neg">${w.bearDown == null ? "--" : spct(w.bearDown, 0)}</td><td class="num ${w.ud == null ? "" : w.ud >= 2 ? "pos" : w.ud < 1 ? "neg" : ""}">${w.ud == null ? "--" : n(w.ud, 2) + "x"}</td>
        <td>${w.tier ? "T" + w.tier : "--"}</td><td class="num">${w.held ? '<span class="dim">add</span> ' : ""}${money(w.dollars, 0)}${w.starterPct == null ? "*" : ""}<div class="dim small">${w.shares == null ? "" : n(w.shares, w.shares < 1 ? 3 : 2) + " sh"}</div></td>
        <td class="num ${over ? "neg" : ""}" title="${esc(w.gics)}: now ${pct(w.secNow)}, cap ${pct(w.cap, 0)}">${w.secAfter == null ? '<span class="dim">--</span>' : pct(w.secAfter) + (over ? " &gt; cap" : "")}</td>
        <td class="num ${w.dEarn != null && w.dEarn <= 14 ? "amb" : "dim"}">${w.earnings?.date ? esc(w.earnings.date.slice(5)) + ` (${w.dEarn}d)` : "--"}</td>
        <td class="num ${w.dReview != null && w.dReview <= 30 ? "amb" : "dim"}">${w.review ? esc(w.review.slice(2)) : "--"}</td>
        <td class="small" style="white-space:normal;min-width:160px;max-width:260px">${esc((w.next_catalyst || "").slice(0, 90))}</td>
        <td style="width:80px" class="spark" data-sym="${esc(w.ticker)}"></td></tr>`;
    }).join("") || '<tr><td colspan="17" class="dim">Nothing in this filter.</td></tr>'}
  </table><div class="cnote">DIST = price vs Buy-below (green at or through, amber within ${pct(APPROACH(), 0)}). BEAR = downside to the memo's bear value. SECTOR AFTER = GICS sector weight after a starter buy (red if it breaks the cap). * = default starter size. Any Buy still needs the trigger-hit refresh.</div></div></div>`;

  // distance ladder
  const lad = wl.filter((w) => w.dist_pct != null).sort((x, y) => x.dist_pct - y.dist_pct);
  const lmax = Math.max(0.3, ...lad.map((w) => Math.abs(w.dist_pct)));
  const ladder = `<div class="p"><h3>DISTANCE LADDER <i>% above Buy-below · left of the line = through</i></h3><div class="pb"><table>
    ${lad.map((w) => { const d = w.dist_pct, x = Math.max(-1, Math.min(1, d / lmax)), st = distState(d);
      return `<tr data-drill="tkr:${esc(w.ticker)}"><td class="amb" style="width:52px">${esc(w.ticker)}</td><td><div style="position:relative;height:11px">
        <div style="position:absolute;left:25%;top:-2px;bottom:-2px;width:1px;background:#777"></div>
        <div style="position:absolute;left:25%;width:${(APPROACH() / lmax) * 75}%;top:0;bottom:0;background:rgba(255,160,40,.08)"></div>
        <div style="position:absolute;top:1px;height:9px;${x >= 0 ? `left:25%;width:${x * 75}%` : `right:75%;width:${-x * 25}%`};background:${DCOL[st]}"></div></div></td>
        <td class="num" style="width:58px;color:${DCOL[st]}">${spct(d, 1)}</td></tr>`; }).join("")}
  </table><div class="cnote">Shaded band = within ${pct(APPROACH(), 0)} of the trigger.</div></div></div>`;

  // event pod
  const podW = S.aggregates?.sleeve?.event_pod || 0;
  const podP = `<div class="p"><h3>EVENT POD <i>${pct(podW)} of NAV · cap ${pct(S.caps?.event_pod, 0)}</i></h3><div class="pb"><table>
    <tr><th>TKR</th><th>STATUS</th><th class="num">LAST</th><th class="num">ENTRY &lt;</th><th class="num">DIST</th><th>ENTRY RULE</th></tr>
    ${pod.map((e) => `<tr data-drill="pod:${esc(e.ticker)}"><td class="amb">${esc(e.ticker)}</td><td class="${e.status === "armed" ? "amb" : "dim"}">${esc((e.status || "").toUpperCase())}</td><td class="num">${n(e.price)}</td><td class="num">${e.entry_below ? n(e.entry_below) : "--"}</td>
      <td class="num" style="color:${DCOL[distState(e.dist_pct)]}">${spct(e.dist_pct, 1)}</td><td class="small dim" style="white-space:normal">${esc(e.entry_text || "")}</td></tr>`).join("") || '<tr><td colspan="6" class="dim">No event-pod trades.</td></tr>'}
  </table><div class="cnote">Pod entries follow event-pod/pod-process.md. Click a row for the trade file and chart.</div></div></div>`;

  // catalyst calendar: earnings (held + watch), reviews, upcoming econ releases
  const ev = [];
  for (const w of wl) {
    if (w.earnings?.date) ev.push({ d: w.earnings.date, t: w.ticker, what: "Earnings" + (w.earnings.timing ? " (" + w.earnings.timing + ")" : ""), kind: "earn", drill: "tkr:" + w.ticker });
    if (w.review) ev.push({ d: w.review, t: w.ticker, what: "Watchlist review", kind: "rev", drill: "tkr:" + w.ticker });
  }
  for (const p of S.positions || []) if (p.earnings?.date) ev.push({ d: p.earnings.date, t: p.ticker, what: "Earnings (held)", kind: "held", drill: "tkr:" + p.ticker });
  for (const e of S.earnings_upcoming || []) if (!ev.some((x) => x.t === e.ticker && x.d === e.date)) ev.push({ d: e.date, t: e.ticker, what: "Earnings" + (e.held ? " (held)" : ""), kind: e.held ? "held" : "earn", drill: "tkr:" + e.ticker });
  for (const e of MK?.economy?.rows || []) if (e.next) ev.push({ d: e.next, t: "MACRO", what: e.label, kind: "eco", drill: `fredx:ECO:${e.series}~${e.transform || "level"}*${e.scale || 1}|${e.label}` });
  const today = new Date().toISOString().slice(0, 10);
  const cal = ev.filter((e) => e.d >= today).sort((a, b) => a.d.localeCompare(b.d)).slice(0, 40);
  const KCOL = { earn: "var(--amber)", held: "var(--pos)", rev: "var(--blue)", eco: "#9aa" };
  const calP = `<div class="p"><h3>CATALYST CALENDAR <i>earnings, reviews, macro releases</i></h3><div class="pb"><table>
    ${cal.map((e) => `<tr data-drill="${esc(e.drill)}"><td class="num dim" style="width:80px">${esc(e.d.slice(5))} <span class="small">(${daysTo(e.d)}d)</span></td><td style="width:60px;color:${KCOL[e.kind]}">${esc(e.t)}</td><td>${esc(e.what)}</td></tr>`).join("") || '<tr><td class="dim">Nothing dated.</td></tr>'}
  </table><div class="cnote"><span style="color:var(--pos)">&#9632;</span> held earnings &nbsp;<span class="amb">&#9632;</span> watchlist earnings &nbsp;<span style="color:var(--blue)">&#9632;</span> review dates &nbsp;<span style="color:#9aa">&#9632;</span> macro releases (FRED)</div></div></div>`;

  // flags and notes
  const fl = wl.flatMap((w) => [...(w.flags || []).map((f) => ({ t: w.ticker, d: f.date, txt: f.text })), ...(w.notes || []).map((x) => ({ t: w.ticker, d: (x.match(/\d{4}-\d{2}-\d{2}/) || [""])[0], txt: x }))])
    .sort((a, b) => (b.d || "").localeCompare(a.d || "")).slice(0, 20);
  const flags = `<div class="p"><h3>FLAGS &amp; NOTES <i>digest flags and watchlist notes, newest first</i></h3><div class="pb">
    ${fl.map((f, i) => `<div class="head" style="white-space:normal" data-drill="wnote:${esc(f.t)}:${i}"><span class="amb">${esc(f.t)}</span> <span class="dim">${esc(f.d || "")}</span> ${esc(f.txt.replace(/^[->\s]+/, "").slice(0, 200))}</div>`).join("") || '<span class="dim">No flags.</span>'}</div></div>`;
  window._wflags = fl;

  return `<div class="grid">${pipe}${table}${trackPanel()}${ladder}${podP}${calP}${flags}</div>`;
}
let wFilter = lsGet("watch:filter") || "";
function setWatchFilter(k) { wFilter = wFilter === k ? "" : k; lsSet("watch:filter", wFilter); render(); }
function distBar(d) {
  if (d == null) return "";
  const x = Math.max(-0.2, Math.min(0.4, d)), st = distState(d);
  return `<div style="position:relative;height:8px;background:#161616"><div style="position:absolute;left:33.3%;top:-2px;bottom:-2px;width:1px;background:#888"></div>
    <div style="position:absolute;top:0;height:8px;${x >= 0 ? `left:33.3%;width:${(x / 0.4) * 66.7}%` : `right:66.7%;width:${(-x / 0.2) * 33.3}%`};background:${DCOL[st]}"></div></div>`;
}

// ---------- drill-downs ----------
const _tkrDrill = DRILL.tkr;
DRILL.tkr = (t) => {
  const out = _tkrDrill(t), w = watchBy(t);
  if (!w || !out) return out;
  const i = wInfo(w), over = i.secAfter != null && i.cap && i.secAfter > i.cap;
  const sizing = sec("Sizing a starter (read-only)", kv([
    ["Starter size", `${n(i.sp, 1)}% of NAV${i.starterPct == null ? " (default)" : ""}`], ["Dollars", money(i.dollars)],
    ["Shares at last", i.shares == null ? "--" : n(i.shares, 4)], ["Limit at Buy-below", w.buy_below ? money(w.buy_below) : "--"],
    ["Sector (GICS)", esc(i.gics || "--")], ["Sector now / after", i.secAfter == null ? "--" : `${pct(i.secNow)} / ${pct(i.secAfter)}`, over ? "neg" : ""],
    ["Sector cap", i.cap ? pct(i.cap, 0) : "--"], ["Single-name cap", pct(S.caps?.single_name, 0)],
    ["Base value", w.base_value ? money(w.base_value) : "--"], ["Bear value", i.bear ? money(i.bear) : "--"],
    ["Upside to base", spct(w.upside_base, 1)], ["Up/down ratio", i.ud ? n(i.ud, 2) + "x" : "--"],
    ["Tier", i.tier ? "T" + i.tier : "--"], ["Review", esc(i.review || "--")],
  ]) + `<div class="note">${over ? "<b class=\"neg\">A starter would push " + esc(i.gics) + " over its cap. </b>" : ""}At or through Buy-below, run the trigger-hit refresh (the research folder underwriting process) before any Buy. The desk never places orders.${w.fair_value ? "\n\nFair value: " + esc(w.fair_value) : ""}</div>`);
  return { ...out, body: out.body + sizing };
};
DRILL.pod = (t) => {
  const e = (S.event_pod || []).find((x) => x.ticker === t);
  if (!e) return null;
  return { title: `EVENT POD · ${esc(t)} <span class="dim">${esc(e.status)}</span>`, body: `<div class="dsec">${chartSlot(t, "drill-tkr", 220, "3M")}</div>` + kv([
    ["Status", esc(e.status)], ["Last", n(e.price)], ["Entry below", e.entry_below ? n(e.entry_below) : "--"], ["Distance", spct(e.dist_pct, 1), cls(-e.dist_pct)],
    ["Pod weight now", pct(S.aggregates?.sleeve?.event_pod || 0)], ["Pod cap", pct(S.caps?.event_pod, 0)]])
    + `<div class="note">${esc(e.entry_text || "")}</div><div class="dsec" style="display:flex;gap:6px">${e.path ? `<button class="mini" onclick='openPath(${esc(JSON.stringify(e.path))})'>Open trade file</button>` : ""}<a class="mini" href="https://robinhood.com/stocks/${encodeURIComponent(t)}" target="_blank" rel="noopener">Robinhood</a></div>` };
};
DRILL.wnote = (spec) => {
  const i = +spec.split(":").pop(), f = (window._wflags || [])[i];
  if (!f) return null;
  return { title: `${esc(f.t)} · ${esc(f.d || "note")}`, body: `<div class="note" style="color:var(--txt)">${esc(f.txt)}</div><div class="dsec"><button class="mini" data-drill="tkr:${esc(f.t)}">Open ${esc(f.t)}</button></div>` };
};
