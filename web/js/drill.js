/* Drill-downs: any element with data-drill="kind:id" opens a detail window.
   Views live in the DRILL registry; clicks inside a window push onto a back stack. */
"use strict";

const DRILL = {};
let drillStack = [];

const kv = (rows) => `<div class="dgrid">${rows.map(([k, v, c]) => `<div><span>${k}</span><b class="${c || ""}">${v}</b></div>`).join("")}</div>`;
const sec = (title, body) => `<div class="dsec"><b>${title}</b>${body}</div>`;

function openDrill(spec, push = false) {
  // A security opens in its terminal window (functions, BACK, LAUNCHPAD); other pop-ups stay pop-ups
  if (typeof openSec === "function" && /^(tkr|mkt):/.test(spec)) { openSec(spec.slice(4)); return; }
  if (typeof openSec === "function" && spec.startsWith("fredx:")) {   // a FRED series from MKTS: its own window
    const body = spec.slice(6), [head, label = ""] = body.split("|"), id = head.slice(head.indexOf(":") + 1), sid = id.split(/[~*]/)[0];
    openSec("FRED:" + sid, "DES", { label, fx: body });
    return;
  }
  if (typeof bookRoute === "function") { const r = bookRoute(spec); if (r) { openBook(r[0], r[1]); return; } }
  if (typeof openNews === "function" && spec.startsWith("wire:")) { openNews({ i: +spec.slice(5) }); return; }
  if (typeof actRoute === "function" && spec.startsWith("act:") && actRoute(spec.slice(4))) return;
  const view = DRILL[spec.split(":")[0]];
  if (!view) return;
  const id = spec.slice(spec.indexOf(":") + 1);
  let out;
  try { out = view(id); } catch (e) { out = { title: "Error", body: `<div class="note neg">${esc(e.message)}</div>` }; }
  if (!out) return;
  drillStack = push ? [...drillStack, spec] : [spec];
  $("dbox").innerHTML = `<h2>${drillStack.length > 1 ? '<button data-back title="Back (Backspace)">&lt; BACK</button>' : ""}<span class="t">${out.title}</span><button data-close title="Close (Esc)">ESC</button></h2>${out.body}`;
  $("drill").hidden = false;
  if (document.activeElement === $("cmd")) $("cmd").blur();  // so D / Enter / Esc reach the window, not the command bar
  $("dbox").scrollTop = 0;
  hydrate($("dbox"));
  fillCards($("dbox"));
}
function closeDrill() { $("drill").hidden = true; drillStack = []; }
function backDrill() {
  if (drillStack.length < 2) return closeDrill();
  const prev = drillStack.slice(0, -1);
  drillStack = prev.slice(0, -1);
  openDrill(prev.at(-1), drillStack.length > 0);
}
/** Re-render the open window with fresh state (keeps charts and the back stack). */
function refreshDrill() {
  if ($("drill").hidden || !drillStack.length) return;
  const top = drillStack.at(-1), view = DRILL[top.split(":")[0]];
  if (!view) return;
  const out = view(top.slice(top.indexOf(":") + 1));
  // Only refresh the fact grid, not charts, so an open chart does not reload.
  const fresh = document.createElement("div");
  fresh.innerHTML = out.body;
  const oldGrid = $("dbox").querySelector(".dgrid"), newGrid = fresh.querySelector(".dgrid");
  if (oldGrid && newGrid) oldGrid.replaceWith(newGrid);
}

document.addEventListener("click", (e) => {
  if (e.target.id === "drill") return closeDrill();
  if (e.target.closest("[data-close]")) return closeDrill();
  if (e.target.closest("[data-back]")) return backDrill();
  const el = e.target.closest("[data-drill]");
  if (!el || e.target.closest("a,button:not([data-drill])")) return;
  openDrill(el.dataset.drill, !!el.closest("#dbox"));
});
document.addEventListener("keydown", (e) => {
  if ($("drill").hidden) return;
  if (e.key === "Escape") closeDrill();
  if (e.key === "Backspace" && document.activeElement?.tagName !== "INPUT") { e.preventDefault(); backDrill(); }
  // D or Enter in a ticker window opens that ticker's DES page.
  const top = drillStack.at(-1) || "";
  if (top.startsWith("tkr:") && !e.ctrlKey && !e.metaKey && !e.altKey && document.activeElement?.tagName !== "INPUT"
      && (e.key === "d" || e.key === "D" || e.key === "Enter")) {
    e.preventDefault();
    const t = top.slice(4);
    closeDrill();
    location.hash = "SEC/" + encodeURIComponent(t);
  }
});

// ---------- views ----------

const RISK_TXT = {
  beta: "2-year weekly regression of each holding against SPY, weighted by today's weights. Mandate: names above 1.8 need a written override.",
  vol: "Annualised standard deviation of the book's daily returns over the last year, at today's weights.",
  var: "Historical 95% one-day VaR: the 5th-worst daily loss over the last year at today's weights, in dollars.",
  es: "Expected shortfall: the average loss on the worst 5% of days.",
  dd: "Largest peak-to-trough fall of today's book replayed over the last year.",
  effn: "1 / sum of squared weights: how many equal-sized names the book behaves like.",
  top5: "Weight of the five largest positions. A high number means a few names drive the book.",
  sys: "Share of book variance explained by the five factors (market, size, value, momentum, quality). The rest is stock-specific.",
  twr: "Time-weighted return is not built yet. It comes with the Portfolio page: daily NAV snapshots plus a backfill from Robinhood orders and deposits, cross-checked against performance-log.md.",
};

function riskDrivers() {
  const top = [...S.positions].sort((x, y) => (y.risk_share || 0) - (x.risk_share || 0)).slice(0, 8);
  return sec("Who drives book risk", `<table>${top.map((p) => `<tr data-drill="tkr:${p.ticker}"><td class="amb">${p.ticker}</td><td style="width:40%"><div class="bar"><div style="width:${Math.min(100, (p.risk_share || 0) * 250)}%;background:var(--amber)"></div></div></td><td class="num">${pct(p.risk_share)}</td><td class="num dim">wt ${pct(p.weight)}</td><td class="num dim">beta ${n(p.beta)}</td></tr>`).join("")}</table>`);
}

DRILL.tkr = (t) => {
  const p = posBy(t), w = watchBy(t);
  const chart = `<div class="dsec" data-card="${esc(t)}"></div><div class="dsec">${chartSlot(t, "drill-tkr", 220, "1Y")}</div>`;
  const links = `<div class="dsec" style="display:flex;gap:6px">
    <a class="mini" href="#SEC/${encodeURIComponent(t)}" onclick="closeDrill()" title="Shortcut: D or Enter">Security page (DES) <span class="dim">[D]</span></a>
    <button class="mini" data-drill="whatif:buy|${esc(t)}||" title="Pre-trade check: the book after a starter buy">What-if buy</button>${p || w ? "" : `<button class="mini" data-drill="track:${esc(t)}" title="Add to Keel's tracking list (Shift+T on DES)">Track</button>`}${p ? `<button class="mini" data-drill="whatif:sell|${esc(t)}||">What-if sell</button>` : ""}
    <a class="mini" href="https://robinhood.com/stocks/${encodeURIComponent(t)}" target="_blank" rel="noopener">Robinhood</a>
    ${(p?.thesis_path || w?.thesis_path) ? `<button class="mini" onclick="openPath(${esc(JSON.stringify(p?.thesis_path || w?.thesis_path))})">Open thesis</button>` : ""}</div>`;
  if (p) {
    return { title: `${t} US Equity · ${esc(p.company || "")}`, body: chart + kv([
      ["Last", n(p.price)], ["Day", `${smoney(p.price - p.prev_close)} (${spct(p.day_pct)})`, cls(p.day_pct)],
      ["Shares", n(p.shares, 4)], ["Avg cost", n(p.avg_cost)],
      ["Market value", money(p.value)], ["Weight", `${pct(p.weight)} of NAV (cap ${pct(S.caps.single_name, 0)})`],
      ["Day P&L", smoney(p.day_pnl), cls(p.day_pnl)], ["Unrealized", `${smoney(p.unreal_pnl)} (${spct(p.unreal_pct, 1)})`, cls(p.unreal_pnl)],
      ["Beta (2y wk)", n(p.beta)], ["Vol (1y)", pct(p.vol)],
      ["Share of book risk", pct(p.risk_share)], ["Sector / sleeve", `${esc(p.sector)} / ${esc(p.sleeve)}`],
      ["Bucket", esc(p.bucket)], ["Earnings", esc(p.earnings?.date || "--")],
      ["Quote", `${esc(p.quote_source || "")} ${esc((p.quote_time || "").slice(11, 16))}`], ["Thesis status", esc(p.thesis_status || "--")],
    ]) + sec("Factor loadings", `<div>${Object.entries(p.factors || {}).map(([k, v]) => `${k} <b class="${cls(v)}">${n(v)}</b>`).join(" · ") || "--"}</div>`)
      + (p.action_flag ? sec("Latest note", `<div class="dim" style="white-space:normal">${esc(p.action_flag.slice(0, 600))}${p.action_flag.length > 600 ? "..." : ""}</div>`) : "") + links };
  }
  if (w) {
    return { title: `${t} US Equity · watchlist`, body: chart + kv([
      ["Last", n(w.price)], ["Day", spct(w.day_pct), cls(w.day_pct)], ["Buy-below", n(w.buy_below)], ["Distance", spct(w.dist_pct, 1), cls(-w.dist_pct)],
      ["Fair value", esc(w.fair_value || "--")], ["Upside to base", spct(w.upside_base, 1)], ["Starter size", esc(w.starter_pct || "--")],
      ["Next catalyst", esc(w.next_catalyst || "--")], ["Earnings", esc(w.earnings?.date || "--")], ["Sector", esc(w.sector || "--")],
    ]) + (w.entry_criteria ? sec("Entry criteria", `<div class="dim" style="white-space:normal">${esc(w.entry_criteria)}</div>`) : "") + links };
  }
  return { title: `${esc(t)} · not in coverage`, body: chart + `<div class="note">Not held and not on the watchlist. New names start at Gate 0 (the research folder underwriting process). Fundamentals, ratings and filings arrive with the security page.</div>` + links };
};

DRILL.mkt = (sym) => {
  const c = (S.cross_asset || []).find((x) => x.symbol === sym) || { label: sym, symbol: sym };
  return { title: `${esc(c.label)} · ${esc(c.symbol)}`, body: `<div class="dsec">${chartSlot(c.symbol, "drill-mkt", 220, "1M")}</div>` + kv([
    ["Last", n(c.last)], ["Change", n(c.change), cls(c.change)], ["% change", spct(c.change_pct), cls(c.change_pct)],
    ["Prior close", c.last != null && c.change != null ? n(c.last - c.change) : "--"], ["As of", esc((c.time || "").slice(11, 16))], ["Source", "Yahoo"]]) };
};

DRILL.fred = (label) => {
  const f = (S.fred || []).find((x) => x.label === label);
  if (!f) return null;
  const sc = f.scale || 1;
  return { title: esc(f.label), body: `<div class="dsec">${chartSlot("FRED:" + f.label, "drill-fred", 220, "1Y")}</div>` + kv([
    ["Latest", n(f.last * sc)], ["Prior", n(f.prev * sc)], ["Change", n((f.last - f.prev) * sc), cls(f.last - f.prev)], ["As of", esc(f.date)], ["Source", "FRED, daily"]]) };
};

DRILL.risk = (k) => {
  const rp = S.risk?.portfolio || {};
  const vals = { beta: n(rp.beta), vol: pct(rp.vol), var: money(rp.var95_hist), es: money(rp.es95_hist), dd: pct(rp.max_drawdown_1y), effn: n(rp.effective_n, 1), top5: pct(rp.top5_weight), sys: pct(S.risk?.factors?.systematic_share), twr: "--" };
  const names = { beta: "Beta (2y weekly)", vol: "Volatility (1y)", var: "VaR 95% 1-day", es: "Expected shortfall 95%", dd: "Max drawdown 1y", effn: "Effective names", top5: "Top 5 weight", sys: "Systematic share", twr: "Time-weighted return" };
  return { title: `RISK · ${names[k] || k}  <span class="dim">${vals[k] || ""}</span>`, body: `<div class="note">${RISK_TXT[k] || ""}</div>` + (k === "twr" ? "" : riskDrivers()) };
};

DRILL.sector = (k) => {
  const ps = S.positions.filter((p) => p.sector === k).sort((x, y) => y.weight - x.weight), c = capFor(k);
  return { title: `SECTOR · ${esc(k)}`, body: kv([["Weight", pct(S.aggregates.sector[k])], ["Cap", c ? pct(c, 0) : "--"], ["Room to cap", c ? money((c - S.aggregates.sector[k]) * S.account.nav) : "--"], ["Names", ps.length]])
    + sec("Holdings", `<table><tr><th>TKR</th><th class="num">WT</th><th class="num">DAY</th><th class="num">UNREAL</th></tr>${ps.map((p) => `<tr data-drill="tkr:${p.ticker}"><td class="amb">${p.ticker}</td><td class="num">${pct(p.weight)}</td><td class="num ${cls(p.day_pct)}">${spct(p.day_pct, 1)}</td><td class="num ${cls(p.unreal_pnl)}">${smoney(p.unreal_pnl)}</td></tr>`).join("")}</table>`) };
};

DRILL.act = (id) => {
  const x = (S.actions || []).find((y) => y.id === id);
  if (!x) return { title: "Action", body: `<div class="note">This card has cleared.</div>` };
  return { title: `${x.severity.toUpperCase()} · ${esc(x.ticker || "")} · ${esc(x.type)}`, body: `<div class="note" style="color:var(--txt)"><b class="amb">${esc(x.title)}</b>\n${esc(x.detail || "")}${x.suggestion ? "\n\nSuggested: " + esc(x.suggestion) : ""}</div>`
    + (x.ticker ? `<div class="dsec"><button class="mini" data-drill="tkr:${esc(x.ticker)}">Open ${esc(x.ticker)}</button></div>` : "")
    + `<div class="dsec" style="display:flex;gap:6px">${actButtons(x)}</div>` };
};

DRILL.news = (i) => {
  const h = ((S.digest || [])[0]?.headlines || [])[+i];
  return { title: "HEADLINE", body: `<div class="note" style="color:var(--txt)">${esc(h)}</div>` };
};

DRILL.regime = () => ({ title: `REGIME · ${esc(S.regime?.as_of || "")}`, body: `<div class="note" style="color:var(--txt)">${esc(S.regime?.label || "")}</div>` + kv((S.regime?.axes || []).map((x) => [esc(x.axis), esc(x.classification)]))
  + (S.regime?.path ? `<div class="dsec"><button class="mini" onclick="openPath(${esc(JSON.stringify(S.regime.path))})">Open macro-regime.md</button></div>` : "") });

DRILL.kpi = (k) => {
  const a = S.account, rp = S.risk?.portfolio || {}, b = S.benchmark || {};
  if (k === "nav") return { title: "NAV", body: `<div class="dsec">${chartSlot("BOOK", "drill-book", 220, "1D")}</div>` + kv([
    ["NAV", money(a.nav)], ["Invested", money(a.invested)], ["Cash", money(a.cash)], ["Day P&L", smoney(a.day_pnl), cls(a.day_pnl)],
    ["Day %", spct(a.day_pct), cls(a.day_pct)], ["Source", esc(S.source)]]) };
  if (k === "bench") {
    const exp = (rp.beta ?? 1) * (b.change_pct ?? 0);
    return { title: "BOOK vs SPY", body: kv([["Book", spct(a.day_pct), cls(a.day_pct)], ["SPY", spct(b.change_pct), cls(b.change_pct)],
      ["Beta-expected move", spct(exp)], ["Alpha today", spct(a.day_pct - exp), cls(a.day_pct - exp)]]) + `<div class="note">Alpha = book move minus beta x SPY move. Beta is the 2-year weekly book beta (${n(rp.beta)}).</div>` };
  }
  if (k === "cash") return { title: "CASH", body: kv([["Cash", money(a.cash)], ["% of NAV", pct(a.cash / a.nav)], ["Source", esc(S.source)]]) + (a.cash_note ? `<div class="note">${esc(a.cash_note)}</div>` : "") };
  if (k === "unreal") return { title: "UNREALIZED P&L", body: sec("By position", `<table>${[...S.positions].sort((x, y) => y.unreal_pnl - x.unreal_pnl).map((p) => `<tr data-drill="tkr:${p.ticker}"><td class="amb">${p.ticker}</td><td class="num ${cls(p.unreal_pnl)}">${smoney(p.unreal_pnl)}</td><td class="num ${cls(p.unreal_pct)}">${spct(p.unreal_pct, 1)}</td><td class="num dim">cost ${money(p.cost)}</td></tr>`).join("")}</table>`) };
  return null;
};

DRILL.broker = () => {
  const b = S.broker || {};
  return { title: "DATA SOURCE", body: kv([["Mode", S.source === "robinhood" ? "Live (Robinhood)" : "Fallback (portfolio.md + Yahoo)"], ["Broker state", esc(b.state)], ["Via", esc(b.via || "robin_stocks")], ["Last good read", esc(ago(b.last_ok))]])
    + (b.error ? `<div class="note neg">${esc(b.error)}</div>` : "")
    + (S.source !== "robinhood" ? `<div class="note">To go live, run in the pm-desk folder:\n  .venv\\Scripts\\python -m pmdesk.rh_login</div>` : "") };
};
