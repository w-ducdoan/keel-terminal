/* The PORT window: your book as a "security" with its own function strip, the same design as a security window.
   PERF NAV RISK FACT SCEN CORR EXPO ATTR TRIP SI THEME. Clicking a number on any Launchpad page (a sector, a scenario,
   a correlation cell, a factor, the NAV) opens this window on that function with that item selected; BACK steps back. */
"use strict";

const BOOKFN = {
  PERF: "Performance (time-weighted vs SPY)", NAV: "NAV, cash, P&L", RISK: "Risk measures", FACT: "Factor loadings",
  SCEN: "Stress tests and replay", CORR: "Correlation", EXPO: "Exposure: sector, bucket, sleeve",
  ATTR: "Performance attribution", TRIP: "Thesis tripwires", SI: "Short interest, crowding, liquidity", THEME: "Exposure themes",
};
const BOOK_KEY = "BOOK";

function openBook(fn = null, opt = {}) {
  closeDrill?.();
  const w = WINS[BOOK_KEY] || Object.assign(makeWin(BOOK_KEY, `<b class="amb">PORT</b> <span class="seccls dim">Your book</span>`, 980, 640,
    `<button data-back title="Back">&lt; BACK</button>`), { hist: [] });
  if (!w.wired) { w.el.querySelector("[data-back]").onclick = () => { if (w.hist.length) { [w.fn, w.opt] = w.hist.pop(); bookPaint(); } }; w.wired = true; }
  fn = (fn || w.fn || "PERF").toUpperCase();
  if (w.fn && (w.fn !== fn || JSON.stringify(w.opt) !== JSON.stringify(opt))) w.hist.push([w.fn, w.opt]);
  w.fn = fn; w.opt = opt || {};
  winFront(BOOK_KEY);
  bookPaint();
}

const chips = (items, cur, mk) => `<div class="rbar" style="margin-bottom:6px;flex-wrap:wrap">${items.map(([k, l]) => `<button class="${String(k) === String(cur) ? "on" : ""}" onclick="${mk(k)}">${esc(l)}</button>`).join("")}</div>`;
const viewBody = (v) => (v ? v.body : '<div class="dim">Nothing to show yet: the data may still be loading.</div>');

function bookPaint(keepScroll = false) {
  const w = WINS[BOOK_KEY];
  if (!w || !S?.ready) return;
  const body = w.el.querySelector(".fnbody"), top = body.scrollTop, fn = w.fn, o = w.opt || {};
  w.el.querySelector(".fnsub").textContent = `${fn} · ${BOOKFN[fn] || ""}`;
  w.el.querySelector("[data-back]").disabled = !w.hist.length;
  const strip = `<div class="secstrip rbar">${Object.keys(BOOKFN).map((c) => `<button class="${c === fn ? "on" : ""}" title="${esc(BOOKFN[c])}" onclick="openBook('${c}')">${c}</button>`).join("")}</div>`;
  let html = "";
  if (fn === "PERF") {
    if (!PERF) {
      const again = () => { if (WINS[BOOK_KEY]?.fn === "PERF") bookPaint(); };
      if (perfBusy) setTimeout(again, 1500); else loadPerf().then(() => { if (PERF) again(); });
      html = '<div class="loading">Loading returns...</div>';
    }
    else {
      const k = o.k || "YTD", periods = (PERF.periods || []).map((p) => [p.period, p.period]);
      html = chips([...periods, ["check", "REBUILD CHECK"]], k, (x) => `openBook('PERF',{k:'${x}'})`) + viewBody(k === "check" ? DRILL.perfcheck() : DRILL.perf(k));
    }
  } else if (fn === "NAV") {
    const k = o.k || "nav";
    html = chips([["nav", "NAV"], ["bench", "VS SPY TODAY"], ["cash", "CASH"], ["unreal", "UNREALIZED"]], k, (x) => `openBook('NAV',{k:'${x}'})`) + viewBody(DRILL.kpi(k));
  } else if (fn === "RISK") {
    const k = o.k || "beta";
    html = chips([["beta", "BETA"], ["vol", "VOL"], ["var", "VAR 95"], ["es", "ES 95"], ["dd", "MAX DD"], ["effn", "EFF. NAMES"], ["top5", "TOP 5"], ["sys", "SYSTEMATIC"]], k, (x) => `openBook('RISK',{k:'${x}'})`)
      + viewBody(DRILL.risk(k));
  } else if (fn === "FACT") {
    const keys = Object.keys(S.risk?.factors?.loadings || {}), k = o.k || keys[0];
    html = keys.length ? chips(keys.map((x) => [x, x.toUpperCase()]), k, (x) => `openBook('FACT',{k:'${x}'})`) + viewBody(DRILL.factor(k)) : '<div class="dim">No factor loadings yet.</div>';
  } else if (fn === "SCEN") {
    const sc = S.risk?.scenarios || [], k = o.i ?? 0;
    html = chips([...sc.map((s, i) => [i, s.name]), ["replay", "WORST 20D REPLAY"]], k, (x) => `openBook('SCEN',{i:'${x}'})`) + viewBody(k === "replay" ? DRILL.replay() : DRILL.scen(k));
  } else if (fn === "CORR") {
    const cr = S.risk?.corr || {}, tk = cr.tickers || [];
    let ij = o.ij;
    if (ij && tk.length > 1) {  // a diagonal cell (a name against itself): show it against its closest partner
      const [a, b] = ij.split(",").map(Number);
      if (a === b) { let best = [-2, (a + 1) % tk.length]; tk.forEach((_, j) => { if (j !== a && (cr.matrix?.[a]?.[j] ?? -2) > best[0]) best = [cr.matrix[a][j], j]; }); ij = `${a},${best[1]}`; }
    }
    if (!ij && tk.length > 1) {  // default: the most correlated pair in the book
      let best = [-2, 0, 1];
      for (let i = 0; i < tk.length; i++) for (let j = i + 1; j < tk.length; j++) if ((cr.matrix?.[i]?.[j] ?? -2) > best[0]) best = [cr.matrix[i][j], i, j];
      ij = `${best[1]},${best[2]}`;
    }
    const [i] = (ij || "0,1").split(",").map(Number);
    html = tk.length ? chips(tk.map((t, x) => [x, t]), i, (x) => `openBook('CORR',{ij:'${x},${x === i ? (i + 1) % tk.length : i}'})`) + viewBody(DRILL.corr(ij))
      + '<div class="cnote">Pick a name to see it against the rest of the book; click a name in the list for its window.</div>' : '<div class="dim">No correlation data yet.</div>';
  } else if (fn === "EXPO") {
    const kind = o.kind || "sector", groups = Object.entries((S.aggregates || {})[kind] || {}).filter(([g]) => g !== "Cash").sort((a, b) => b[1] - a[1]);
    const k = o.k || groups[0]?.[0];
    html = chips([["sector", "SECTOR"], ["bucket", "BUCKET"], ["sleeve", "SLEEVE"]], kind, (x) => `openBook('EXPO',{kind:'${x}'})`)
      + chips(groups.map(([g, v]) => [g, `${kind === "sleeve" ? sleeveName(g) : g} ${pct(v, 0)}`]), k, (x) => `openBook('EXPO',{kind:'${kind}',k:${esc(JSON.stringify(x))}})`)
      + viewBody(kind === "sector" ? DRILL.sector(k) : kind === "bucket" ? DRILL.bucket(k) : DRILL.sleeve(k));
  } else if (fn === "ATTR") { html = `<div data-attr>${attrBody()}</div>`; loadAttr(); }
  else if (fn === "TRIP") { html = `<div data-trip>${tripBody()}</div>`; }
  else if (fn === "SI") { html = `<div data-crowd>${crowdBody()}</div>`; loadCrowd(); }
  else if (fn === "THEME") {
    if (o.i != null && THM?.ok) html = `<button class="mini" onclick="openBook('THEME')" style="margin-bottom:6px">All themes</button>` + viewBody(DRILL.theme(o.i));
    else { html = `<div data-themes>${themesBody()}</div>`; loadThemes(); }
  }
  body.innerHTML = strip + (o.act && typeof actBanner === "function" ? actBanner(o.act) : "") + `<div class="secbody">${html}</div>`;
  body.scrollTop = keepScroll ? top : 0;
  hydrate(body);
  fillCards(body);
}

/** The pop-up specs that were book drill-downs, mapped onto the PORT window. */
function bookRoute(spec) {
  const [kind, ...rest] = spec.split(":"), arg = rest.join(":");
  const m = {
    kpi: () => ["NAV", { k: arg }], perf: () => ["PERF", { k: arg }], perfcheck: () => ["PERF", { k: "check" }], risk: () => ["RISK", { k: arg }],
    factor: () => ["FACT", { k: arg }], scen: () => ["SCEN", { i: arg }], replay: () => ["SCEN", { i: "replay" }], corr: () => ["CORR", { ij: arg }],
    sector: () => ["EXPO", { kind: "sector", k: arg }], bucket: () => ["EXPO", { kind: "bucket", k: arg }], sleeve: () => ["EXPO", { kind: "sleeve", k: arg }],
    theme: () => ["THEME", { i: arg }],
  }[kind];
  return m ? m() : null;
}
