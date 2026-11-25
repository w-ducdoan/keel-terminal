/* DES Launchpad: the equity page for any ticker. Route: #SEC/<TICKER>.
   Ten panels (DES, ANR, CACS, RV, FA, EE, HDS, INS, CN, GP) plus Position / Research. Each panel
   loads its own /api/equity/<T>/<section> call, so a slow or failing source never blocks the page:
   the page draws at once and every panel fills in when its data arrives. */
"use strict";

const EQ = {};                       // "T|section" -> {t, d}
const EQ_TTL = 10 * 60e3;
const EQ_BUSY = new Set();
const DES_SECTIONS = ["cockpit", "flags", "des", "anr", "cacs", "rv", "fa", "ee", "hds", "ins", "cn", "rh", "splc", "ern"];
const CHART_MARKS = {};              // chart key -> [{date, kind, label}] (read by charts.js)

const secTicker = () => decodeURIComponent((location.hash.split("/")[1] || "")).toUpperCase();
const eqGet = (t, s) => EQ[t + "|" + s]?.d;
const dTab = (k, def) => lsGet("des:" + k) || def;

async function loadEq(t, s, force = false) {
  const k = t + "|" + s, hit = EQ[k];
  if (EQ_BUSY.has(k) || (!force && hit && Date.now() - hit.t < EQ_TTL)) return hit?.d;
  EQ_BUSY.add(k);
  try {
    const r = await fetch(`/api/equity/${encodeURIComponent(t)}/${s}`);
    EQ[k] = { t: Date.now(), d: r.ok ? await r.json() : { ok: false, error: `Server said ${r.status}` } };
  } catch (e) { EQ[k] = { t: Date.now(), d: { ok: false, error: e.message } }; }
  EQ_BUSY.delete(k);
  if (page === "SEC" && secTicker() === t) desPaint(s);
  if (typeof secWinPaint === "function") secWinPaint(t, s);
  return EQ[k].d;
}
/** Refresh every section of the open ticker (the REFRESH button). */
function loadSec(t, force = true) { CARDS[t] = null; return Promise.allSettled(DES_SECTIONS.map((s) => loadEq(t, s, force))); }

// ---------- formatting ----------
const dBig = (x) => { if (x == null || isNaN(+x)) return "--"; const v = +x, a = Math.abs(v), s = v < 0 ? "-" : ""; return a >= 1e12 ? s + "$" + n(a / 1e12, 2) + "T" : a >= 1e9 ? s + "$" + n(a / 1e9, 2) + "B" : a >= 1e6 ? s + "$" + n(a / 1e6, 1) + "M" : s + money(a, 0); };
const dCnt = (x) => { if (x == null || isNaN(+x)) return "--"; const v = Math.abs(+x), s = +x < 0 ? "-" : ""; return s + (v >= 1e9 ? n(v / 1e9, 2) + "B" : v >= 1e6 ? n(v / 1e6, 2) + "M" : v >= 1e3 ? n(v / 1e3, 1) + "K" : n(v, 0)); };
const dPct = (x, d = 1) => (x == null ? "--" : pct(x, d));
const dNum = (x, d = 2) => (x == null ? "--" : n(x, d));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const tabBar = (key, opts, cur, extra = "") => `<div class="rbar dtabs">${opts.map((o) => `<button class="${o === cur ? "on" : ""}" onclick="desTab('${key}','${esc(o)}')">${esc(o.toUpperCase())}</button>`).join("")}${extra}</div>`;
function desTab(key, v) { lsSet("des:" + key, v); desPaint(key.split(":")[0]); if (typeof secWinPaint === "function") secWinPaint(null, key.split(":")[0]); }
const miss = (d, what) => (!d ? `<div class="loading">Loading ${what}...</div>` : `<div class="dim" style="padding:6px">${esc(d.error || "No data.")}</div>`);
const fmtDate = (iso) => (iso ? String(iso).slice(0, 10) : "--");

// Stats keys: fractions shown as %, money as $B, ratios as plain numbers.
function statFmt(k, v) {
  if (v == null) return "--";
  if (/margin|growth|ROE|ROA|Payout|52w change|Short %/i.test(k)) return pct(v, 1);
  if (/Dividend yield %/.test(k)) return n(v, 2) + "%";
  if (/Debt\/Equity/.test(k)) return n(v, 0) + "%";
  if (/Market cap|Enterprise value|Revenue \(TTM\)|Cash|^Debt$|Free cash flow/.test(k)) return dBig(v);
  if (/Shares out|Float|Avg volume/.test(k)) return dCnt(v);
  return n(v, 2);
}

// ---------- panel bodies ----------
function bodyDes(t) {
  const d = eqGet(t, "des");
  if (!d?.ok) return miss(d, "the company profile");
  const tab = dTab("des:tab", "Profile");
  let body = "";
  if (tab === "Profile") {
    const fy = d.fiscal_year_end ? new Date(d.fiscal_year_end * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : null;
    const sm = d.summary || "No description.", long = sm.length > 420 && dTab("des:more", "0") !== "1";
    body = `<div class="cblurb" style="white-space:normal;max-height:none">${esc(long ? sm.slice(0, 420).replace(/\s+\S*$/, "") + "..." : sm)}
      ${sm.length > 420 ? `<a href="#" class="amb" onclick="desTab('des:more','${long ? "1" : "0"}');return false">${long ? "more" : "less"}</a>` : ""}</div>${chartSlot(t, "des-mini", 90, "1Y")}${kv([
      ["Sector", esc(d.sector || "--")], ["Industry", esc(d.industry || "--")],
      ["HQ", esc([d.city, d.state, d.country].filter(Boolean).join(", ") || "--")], ["Employees", d.employees ? n(d.employees, 0) : "--"],
      ["Fiscal year end", esc(fy || "--")], ["Website", safeUrl(d.website) ? `<a class="amb" href="${esc(d.website)}" target="_blank" rel="noopener">${esc(d.website.replace(/^https?:\/\/(www\.)?/, ""))}</a>` : "--"]])}`;
  } else if (tab === "Stats") {
    body = kv(Object.entries(d.stats || {}).map(([k, v]) => [esc(k), statFmt(k, v)]));
  } else {
    body = `<table><tr><th>NAME</th><th>TITLE</th><th class="num">PAY</th></tr>${(d.officers || []).map((o) => `<tr><td>${esc(o.name)}</td><td class="small" style="white-space:normal">${esc(o.title)}</td><td class="num">${o.pay ? dBig(o.pay) : "--"}</td></tr>`).join("") || '<tr><td class="dim">No officers listed.</td></tr>'}</table>`;
  }
  return tabBar("des:tab", ["Profile", "Stats", "Officers"], tab) + body;
}

function bodyAnr(t) {
  const d = eqGet(t, "anr");
  if (!d?.ok) return miss(d, "analyst ratings");
  const tab = dTab("anr:tab", "Summary");
  let body = "";
  if (tab === "Summary") {
    const last = d.last, lo = Math.min(d.target_low ?? last, last), hi = Math.max(d.target_high ?? last, last), X = (v) => ((v - lo) / (hi - lo || 1)) * 100;
    const tr = d.trend || [];
    body = `${kv([["Consensus", `<b class="${/buy/.test(d.consensus) ? "pos" : /sell/.test(d.consensus) ? "neg" : ""}">${esc((d.consensus || "--").replace("_", " ").toUpperCase())}</b>`],
      ["Score (1 buy - 5 sell)", dNum(d.score, 2)], ["Analysts", d.analysts ?? "--"], ["Last", dNum(last)],
      ["12M target (mean)", dNum(d.target_mean)], ["Median target", dNum(d.target_median)],
      ["Return potential", spct(d.upside, 1), cls(d.upside)], ["Low / high", `${dNum(d.target_low)} / ${dNum(d.target_high)}`]])}
      ${d.target_low != null && d.target_high != null && last ? `<div style="position:relative;height:30px;margin:6px 14px">
        <div style="position:absolute;top:12px;left:${X(d.target_low)}%;width:${X(d.target_high) - X(d.target_low)}%;height:6px;background:#2a3a2a"></div>
        <div title="mean target" style="position:absolute;top:8px;left:${X(d.target_mean)}%;width:2px;height:14px;background:var(--pos)"></div>
        <div title="last" style="position:absolute;top:6px;left:${X(last)}%;width:3px;height:18px;background:var(--amber)"></div>
        <span class="small dim" style="position:absolute;top:21px;left:${X(d.target_low)}%">${n(d.target_low, 0)}</span><span class="small dim" style="position:absolute;top:21px;left:${X(d.target_high)}%;transform:translateX(-100%)">${n(d.target_high, 0)}</span></div>
        <div class="cnote"><span class="amb">&#9632;</span> last &nbsp;<span class="pos">&#9632;</span> mean target &nbsp; bar = low to high target</div>` : ""}
      ${tr.length ? `<div class="sec2">RECOMMENDATION TREND</div><table><tr><th>MONTH</th><th class="num">STRONG BUY</th><th class="num">BUY</th><th class="num">HOLD</th><th class="num">SELL</th><th class="num">STRONG SELL</th></tr>
        ${tr.map((r) => `<tr><td>${esc(r.period === "0m" ? "This month" : r.period.replace("-", "") + " ago")}</td><td class="num">${r.strong_buy}</td><td class="num">${r.buy}</td><td class="num">${r.hold}</td><td class="num">${r.sell}</td><td class="num">${r.strong_sell}</td></tr>`).join("")}</table>` : ""}`;
  } else if (tab === "History") {
    body = anrHistory(d.history || [], d.last);
  } else {
    body = `<table><tr><th>DATE</th><th>FIRM</th><th>RATING</th><th>ACTION</th><th class="num">TARGET</th><th class="num">PRIOR</th></tr>
      ${(d.firms || []).map((f) => `<tr><td class="dim">${esc(fmtDate(f.date))}</td><td>${esc(f.firm)}</td><td class="${f.class === "buy" ? "pos" : f.class === "sell" ? "neg" : ""}">${esc(f.grade || "--")}${f.from && f.from !== f.grade ? ` <span class="dim small">from ${esc(f.from)}</span>` : ""}</td>
        <td class="small dim">${esc({ up: "upgrade", down: "downgrade", main: "maintain", init: "initiate", reit: "reiterate" }[f.action] || f.action || "")}</td>
        <td class="num">${dNum(f.target)}</td><td class="num dim">${dNum(f.prior_target)}</td></tr>`).join("") || '<tr><td class="dim">No broker actions.</td></tr>'}</table>
      <div class="cnote">Latest action per firm, from Yahoo's upgrades and downgrades feed.</div>`;
  }
  return tabBar("anr:tab", ["Summary", "History", "Firms"], tab) + body;
}

/** ANR history: monthly buy/hold/sell share (stacked bars) above the average target (its own chart). */
function anrHistory(h, last) {
  if (!h.length) return '<div class="dim">No rating history.</div>';
  const w = 420, bh = 90, lh = 70, bw = w / h.length;
  const bars = h.map((m, i) => {
    const tot = (m.buy || 0) + (m.hold || 0) + (m.sell || 0) || 1;
    let y = 0;
    return [["buy", "var(--pos)"], ["hold", "#666"], ["sell", "var(--neg)"]].map(([k, c]) => {
      const hh = ((m[k] || 0) / tot) * bh, r = `<rect x="${i * bw + 1}" y="${bh - y - hh}" width="${Math.max(bw - 2, 1)}" height="${hh}" fill="${c}"><title>${esc(m.month.slice(0, 7))}: ${m.buy} buy, ${m.hold} hold, ${m.sell} sell</title></rect>`;
      y += hh; return r;
    }).join("");
  }).join("");
  const tg = h.map((m) => m.target).filter((v) => v != null);
  const lo = Math.min(...tg, last ?? Infinity), hi = Math.max(...tg, last ?? -Infinity), r = hi - lo || 1;
  const Y = (v) => lh - 6 - ((v - lo) / r) * (lh - 14);
  const pts = h.map((m, i) => (m.target == null ? null : `${(i * bw + bw / 2).toFixed(1)},${Y(m.target).toFixed(1)}`)).filter(Boolean).join(" ");
  return `<div class="legend2"><span><i style="background:var(--pos)"></i>Buy</span><span><i style="background:#666"></i>Hold</span><span><i style="background:var(--neg)"></i>Sell</span></div>
    <svg width="100%" height="${bh}" viewBox="0 0 ${w} ${bh}" preserveAspectRatio="none">${bars}</svg>
    <div class="sec2">AVERAGE 12M TARGET ${last ? `<span class="dim">· last ${n(last)} dashed</span>` : ""}</div>
    <svg width="100%" height="${lh}" viewBox="0 0 ${w + 40} ${lh}" preserveAspectRatio="none">
      ${last ? `<line rvX="0" x2="${w}" y1="${Y(last)}" y2="${Y(last)}" stroke="var(--amber)" stroke-dasharray="3 3"/>` : ""}
      <polyline points="${pts}" fill="none" stroke="var(--blue)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
      <text x="${w + 4}" y="${Y(hi) + 4}" fill="#8a8a8a" font-size="10">${n(hi, 0)}</text><text x="${w + 4}" y="${Y(lo)}" fill="#8a8a8a" font-size="10">${n(lo, 0)}</text></svg>
    <div class="cnote">${esc(h[0].month.slice(0, 7))} to ${esc(h.at(-1).month.slice(0, 7))}. Rebuilt from individual broker actions, so it approximates the consensus at each month end.</div>`;
}

function bodyCacs(t) {
  const d = eqGet(t, "cacs");
  if (!d?.ok) return miss(d, "corporate actions");
  const types = ["All", ...(d.types || [])], cur = types.includes(dTab("cacs:tab", "All")) ? dTab("cacs:tab", "All") : "All";
  const ev = (d.events || []).filter((e) => cur === "All" || e.type === cur);
  return tabBar("cacs:tab", types, cur) + `<table><tr><th>DATE</th><th>TYPE</th><th>EVENT</th></tr>
    ${ev.map((e) => `<tr class="${e.future ? "amb" : ""}"><td class="${e.future ? "" : "dim"}">${esc(e.date)}${e.future ? " ▸" : ""}</td><td>${esc(e.type)}</td>
      <td class="small" style="white-space:normal">${safeUrl(e.url) ? `<a href="${esc(e.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(e.text)}</a>` : esc(e.text)}</td></tr>`).join("") || '<tr><td class="dim">None.</td></tr>'}</table>
    <div class="cnote">▸ upcoming. Filings come from Robinhood when it is connected; dividends, splits and earnings from Yahoo.</div>`;
}

// Every column the RV panel can show: [key, header, formatter, what it is]. Pick any set with COLUMNS.
const rvX = (v) => (v == null ? "--" : n(v, 1) + "x");
const RV_ALL = [
  ["mcap", "MKT CAP", dBig, "Market cap"], ["ev", "EV", dBig, "Enterprise value"],
  ["ev_ebitda", "EV/EBITDA", rvX, "Enterprise value / EBITDA"], ["ev_rev", "EV/REV", rvX, "Enterprise value / revenue"],
  ["ev_fcf", "EV/FCF", rvX, "Enterprise value / free cash flow"], ["pe", "P/E", rvX, "Trailing P/E"], ["fwd_pe", "FWD P/E", rvX, "Forward P/E"],
  ["peg", "PEG", (v) => dNum(v, 2), "P/E / earnings growth"], ["ps", "P/S", rvX, "Price / sales"], ["pb", "P/B", rvX, "Price / book"],
  ["fcf_yield", "FCF YLD", (v) => dPct(v), "Free cash flow / market cap"], ["div_yield", "DIV %", (v) => (v == null ? "--" : n(v, 2)), "Dividend yield"],
  ["rev_growth", "REV GR", (v) => dPct(v), "Revenue growth (latest quarter y/y)"], ["eps_growth", "EPS GR", (v) => dPct(v), "Earnings growth (latest quarter y/y)"],
  ["gross_margin", "GROSS MGN", (v) => dPct(v), "Gross margin"], ["op_margin", "OP MGN", (v) => dPct(v), "Operating margin"],
  ["net_margin", "NET MGN", (v) => dPct(v), "Net margin"], ["roe", "ROE", (v) => dPct(v), "Return on equity"], ["roa", "ROA", (v) => dPct(v), "Return on assets"],
  ["nd_ebitda", "ND/EBITDA", rvX, "Net debt / EBITDA"], ["beta", "BETA", (v) => dNum(v, 2), "Beta"]];
const RV_DEFAULT = ["mcap", "ev_ebitda", "pe", "fwd_pe", "ps", "fcf_yield", "rev_growth", "op_margin", "roe", "nd_ebitda"];
const rvCols = () => { const k = (lsGet("rv:cols") || RV_DEFAULT.join(",")).split(",").filter(Boolean); return RV_ALL.filter((c) => k.includes(c[0])); };
let RV_PICK = false;

function rvSort(k) {
  const [cur, dir] = (lsGet("rv:sort") || "mcap:desc").split(":");
  lsSet("rv:sort", `${k}:${cur === k && dir === "asc" ? "desc" : "asc"}`);  // first click: smallest first
  desPaint("rv");
}
function rvToggleCol(k) {
  const cur = rvCols().map((c) => c[0]), next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
  lsSet("rv:cols", RV_ALL.map((c) => c[0]).filter((x) => next.includes(x)).join(",") || "mcap");
  desPaint("rv");
}
async function rvPeers(t, peers, reset = false) {
  const r = await fetch(`/api/rv/${encodeURIComponent(t)}/peers`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(reset ? { reset: true } : { peers }) });
  if (!r.ok) return alert((await r.json().catch(() => ({}))).detail || `Server said ${r.status}`);
  const body = document.querySelector('[data-eq="rv"]');
  if (body) body.insertAdjacentHTML("afterbegin", '<div class="loading">Repricing peers...</div>');
  await loadEq(t, "rv", true);
}
SUGGEST_ACT.rv = (inp, sym) => rvAdd(secTicker());
function rvAdd(t) {
  const el = document.getElementById("rv-add"), d = eqGet(t, "rv");
  const add = (el?.value || "").toUpperCase().split(/[\s,]+/).filter(Boolean);
  if (add.length) rvPeers(t, [...(d.peers || []), ...add]);
}

function bodyRv(t) {
  const d = eqGet(t, "rv");
  if (!d?.ok) return miss(d, "relative valuation");
  const ph = (d.pe_history || []).filter((p) => p[1] != null && p[1] > 0);
  const top = ph.length > 5 ? `<div class="sec2">TRAILING P/E vs OWN AVERAGE AND PEERS</div>${peChart(ph, d.median?.pe, 110)}` : "";
  const cols = rvCols(), [sk, sd] = (lsGet("rv:sort") || "mcap:desc").split(":");
  // Missing values always sort to the bottom, whichever way the column is sorted
  const rows = [...(d.rows || [])].sort((a, b) => sk === "ticker" ? a.ticker.localeCompare(b.ticker) * (sd === "asc" ? 1 : -1)
    : (a[sk] == null) - (b[sk] == null) || ((a[sk] ?? 0) - (b[sk] ?? 0)) * (sd === "asc" ? 1 : -1));
  const arrow = (k) => (k === sk ? (sd === "asc" ? " ▲" : " ▼") : "");
  const chips = (d.peers || []).map((p) => `<span class="rvchip">${esc(p)}<b onclick="event.stopPropagation();rvPeers('${esc(t)}', ${esc(JSON.stringify((d.peers || []).filter((x) => x !== p)))})" title="Remove ${esc(p)}">×</b></span>`).join("");
  const picker = RV_PICK ? `<div class="rvpick">${RV_ALL.map(([k, l, , what]) => `<label title="${esc(what)}"><input type="checkbox" ${cols.some((c) => c[0] === k) ? "checked" : ""} onchange="rvToggleCol('${k}')"> ${l}</label>`).join("")}</div>` : "";
  const body = `<div class="rvbar">${chips}<input id="rv-add" data-suggest="rv" placeholder="+ ticker or name" onkeydown="if(event.key==='Enter')rvAdd('${esc(t)}')">
      <button class="mini" onclick="rvAdd('${esc(t)}')">Add</button>
      <button class="mini ${RV_PICK ? "on" : ""}" onclick="RV_PICK=!RV_PICK;desPaint('rv')" title="Choose which multiples to compare">COLUMNS</button>
      ${d.peer_source === "your list" ? `<button class="mini" onclick="rvPeers('${esc(t)}', null, true)" title="Back to ${esc((d.default_peers || []).join(", ") || "the default list")}">Reset peers</button>` : ""}</div>${picker}
    <table><tr><th class="rvsort" onclick="rvSort('ticker')">TICKER</th>${cols.map(([k, l, , what]) => `<th class="num rvsort" onclick="rvSort('${k}')" title="${esc(what)}: click to sort">${l}${arrow(k)}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr data-drill="tkr:${esc(r.ticker)}" class="${r.self ? "rvself" : ""}" title="${esc(r.name || "")}"><td class="${r.self ? "amb" : ""}">${esc(r.ticker)}</td>${cols.map(([k, , f]) => `<td class="num">${f(r[k])}</td>`).join("")}</tr>`).join("")}
      <tr class="tot"><td>MEDIAN</td>${cols.map(([k, , f]) => `<td class="num">${f(d.median?.[k])}</td>`).join("")}</tr>
      <tr><td class="amb">${esc(t)} vs MED</td>${cols.map(([k]) => { const p = d.premium?.[k]; return `<td class="num ${p == null ? "dim" : p > 0 ? "neg" : "pos"}">${p == null ? "" : spct(p, 0)}</td>`; }).join("")}</tr></table>
    <div class="cnote">Peers: ${esc(d.peer_source)} (${esc(d.industry || "")}). Click a header to sort (smallest first, again for biggest). Premium in red, discount in green on valuation multiples, vs the median of peers excluding ${esc(t)}. Your peer list is saved per name.</div>`;
  return top + body;
}
function peChart(ph, peerMed, h = 150) {
  const w = 440, vals = ph.map((p) => p[1]), all = peerMed ? [...vals, peerMed] : vals;
  const lo = Math.min(...all) * 0.95, hi = Math.max(...all) * 1.05, r = hi - lo || 1;
  const X = (i) => (i / (ph.length - 1)) * (w - 40), Y = (v) => h - 14 - ((v - lo) / r) * (h - 22);
  const avg = vals.reduce((s, v) => s + v, 0) / vals.length;
  const line = ph.map((p, i) => `${X(i).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const hl = (v, c, lab) => `<line rvX="0" x2="${w - 40}" y1="${Y(v)}" y2="${Y(v)}" stroke="${c}" stroke-dasharray="3 3"/><text x="${w - 37}" y="${Y(v) + 4}" fill="${c}" font-size="10">${lab} ${n(v, 1)}</text>`;
  return `<div class="legend2"><span><i style="background:var(--blue)"></i>Trailing P/E</span><span><i style="background:#888"></i>Own average</span>${peerMed ? '<span><i style="background:var(--amber)"></i>Peer median today</span>' : ""}</div>
    <svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${hl(avg, "#888", "avg")}${peerMed ? hl(peerMed, "var(--amber)", "peer") : ""}
    <polyline points="${line}" fill="none" stroke="var(--blue)" stroke-width="1.4" vector-effect="non-scaling-stroke"/>
    <text x="0" y="${h - 2}" fill="#6a6a6a" font-size="9">${esc(ph[0][0].slice(0, 7))}</text><text x="${w - 40}" y="${h - 2}" fill="#6a6a6a" font-size="9" text-anchor="end">${esc(ph.at(-1)[0].slice(0, 7))}</text></svg>
    <div class="small">Now <b>${n(vals.at(-1), 1)}x</b> vs own average ${n(avg, 1)}x (${spct(vals.at(-1) / avg - 1, 0)})${peerMed ? `, peer median ${n(peerMed, 1)}x` : ""}.</div>`;
}

// Not "faFmt": fa.js defines a different faFmt(v, fmt) and loads later, which silently replaced this one.
function desFaFmt(label, v, cur) {
  if (v == null) return "--";
  if (cur && cur !== "USD" && !/%|\(x\)|EPS|\/ share/.test(label)) return dBig(v).replace("$", "");
  if (/%/.test(label)) return n(v, 1);
  if (/\(x\)/.test(label)) return n(v, 2) + "x";
  if (/EPS|\/ share/.test(label)) return n(v, 2);
  return dBig(v);
}
function bodyFa(t) {
  const d = eqGet(t, "fa");
  if (!d?.ok) return miss(d, "financial statements");
  const per = dTab("fa:per", "Annual"), blk = d[per.toLowerCase()] || d.annual;
  const groups = Object.keys(blk.groups || {}), tab = groups.includes(dTab("fa:tab", "Profitability")) ? dTab("fa:tab", "Profitability") : groups[0];
  const rows = blk.groups?.[tab] || [], sel = rows.find((r) => r.label === dTab("fa:row", "")) || rows[0];
  const lab = (p) => (per === "Annual" ? "FY" + p.slice(2, 4) : p.slice(2, 7));
  const vals = sel ? sel.values : [], present = vals.filter((v) => v != null);
  const mx = Math.max(...present.map(Math.abs), 1e-9);
  const chart = sel && present.length ? `<div class="sec2">${esc(sel.label)}</div><div class="fabars">${vals.map((v, i) => `<div title="${esc(blk.periods[i])}: ${esc(desFaFmt(sel.label, v, d.currency))}"><span class="${v < 0 ? "neg" : ""}">${v == null ? "" : desFaFmt(sel.label, v, d.currency)}</span><i style="height:${v == null ? 0 : (Math.abs(v) / mx) * 54}px;background:${v < 0 ? "var(--neg)" : "var(--blue)"}"></i><em>${esc(lab(blk.periods[i]))}</em></div>`).join("")}</div>` : "";
  return tabBar("fa:tab", groups, tab, `<span style="margin-left:auto"></span><button onclick="openFA('${esc(t)}')" title="Full statements, ratios and valuation history from SEC filings (Shift+F, or double-click the title)">EXPAND · SEC</button>${["Annual", "Quarterly"].map((p) => `<button class="${p === per ? "on" : ""}" onclick="desTab('fa:per','${p}')">${p.toUpperCase()}</button>`).join("")}`)
    + `<table class="fatbl"><tr><th>${esc(d.currency || "")}</th>${blk.periods.map((p) => `<th class="num">${esc(lab(p))}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr class="${r === sel ? "on" : ""}" onclick="desTab('fa:row', ${esc(JSON.stringify(r.label))})" style="cursor:pointer"><td>${esc(r.label)}</td>${r.values.map((v) => `<td class="num ${v < 0 ? "neg" : ""}">${desFaFmt(r.label, v, d.currency)}</td>`).join("")}</tr>`).join("")}</table>
    ${chart}<div class="cnote">Click a row to chart it. ${esc(d.note || "")}</div>`;
}

function bodyEe(t) {
  const d = eqGet(t, "ee");
  if (!d?.ok) return miss(d, "estimates");
  const tab = dTab("ee:tab", "Estimates");
  let body;
  if (tab === "Estimates") {
    // Columns from whichever periods exist (Yahoo sometimes has revenue estimates but no EPS ones).
    const byE = Object.fromEntries((d.eps || []).map((r) => [r.period, r])), rev = Object.fromEntries((d.revenue || []).map((r) => [r.period, r]));
    const eps = [...new Set([...(d.eps || []), ...(d.revenue || [])].map((r) => r.period))].map((p) => byE[p] || { period: p });
    const row = (label, f, c) => `<tr><td>${label}</td>${eps.map((e) => { const v = f(e, rev[e.period] || {}); return `<td class="num ${c ? cls(v) : ""}">${c === "pct" ? spct(v, 1) : c === "big" ? dBig(v) : c === "cnt" ? (v ?? "--") : dNum(v)}</td>`; }).join("")}</tr>`;
    body = `<table><tr><th></th>${eps.map((e) => `<th class="num">${esc(e.period.toUpperCase())}</th>`).join("")}</tr>
      <tr class="sec"><td colspan="${eps.length + 1}">EPS</td></tr>
      ${row("<b>Consensus</b>", (e) => e.avg)}${row("Low", (e) => e.low)}${row("High", (e) => e.high)}${row("Year ago", (e) => e.yearAgoEps)}${row("Growth", (e) => e.growth, "pct")}${row("Analysts", (e) => e.numberOfAnalysts, "cnt")}
      <tr class="sec"><td colspan="${eps.length + 1}">REVENUE</td></tr>
      ${row("<b>Consensus</b>", (e, r) => r.avg, "big")}${row("Low", (e, r) => r.low, "big")}${row("High", (e, r) => r.high, "big")}${row("Year ago", (e, r) => r.yearAgoRevenue, "big")}${row("Growth", (e, r) => r.growth, "pct")}${row("Analysts", (e, r) => r.numberOfAnalysts, "cnt")}</table>`;
  } else if (tab === "Revisions") {
    const rv = Object.fromEntries((d.revisions || []).map((r) => [r.period, r]));
    body = `<table><tr><th>EPS</th><th class="num">NOW</th><th class="num">7D AGO</th><th class="num">30D</th><th class="num">60D</th><th class="num">90D</th><th class="num">vs 90D</th><th class="num">UP/DN 30D</th></tr>
      ${(d.trend || []).map((r) => { const ch = r.current && r["90daysAgo"] ? r.current / r["90daysAgo"] - 1 : null, x = rv[r.period] || {};
        return `<tr><td>${esc(r.period)}</td><td class="num"><b>${dNum(r.current)}</b></td><td class="num dim">${dNum(r["7daysAgo"])}</td><td class="num dim">${dNum(r["30daysAgo"])}</td><td class="num dim">${dNum(r["60daysAgo"])}</td><td class="num dim">${dNum(r["90daysAgo"])}</td>
          <td class="num ${cls(ch)}">${spct(ch, 1)}</td><td class="num"><span class="pos">${x.upLast30days ?? 0}</span> / <span class="neg">${x.downLast30days ?? 0}</span></td></tr>`; }).join("")}</table>
      <div class="cnote">Consensus EPS now and 7 to 90 days ago; up/down = analysts who raised or cut in the last 30 days.</div>`;
  } else {
    const s = [...(d.surprises || [])].reverse(), mx = Math.max(...s.map((x) => Math.abs(x.surprise || 0)), 1);
    body = `<table><tr><th>REPORTED</th><th class="num">EST</th><th class="num">ACTUAL</th><th class="num">SURPRISE</th><th></th></tr>
      ${s.map((x) => `<tr><td class="dim">${esc(fmtDate(x.date))}</td><td class="num">${dNum(x.estimate)}</td><td class="num"><b>${dNum(x.actual)}</b></td><td class="num ${cls(x.surprise)}">${x.surprise == null ? "--" : (x.surprise > 0 ? "+" : "") + n(x.surprise, 1) + "%"}</td>
        <td style="width:80px"><div style="position:relative;height:8px"><div style="position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:#444"></div><div style="position:absolute;top:0;height:8px;${(x.surprise || 0) >= 0 ? "left:50%" : "right:50%"};width:${(Math.abs(x.surprise || 0) / mx) * 50}%;background:${(x.surprise || 0) >= 0 ? "var(--pos)" : "var(--neg)"}"></div></div></td></tr>`).join("")}</table>
      <div class="cnote">Beat rate ${s.length ? `${s.filter((x) => (x.surprise || 0) >= 0).length} of ${s.length}` : "--"}.</div>`;
  }
  return tabBar("ee:tab", ["Estimates", "Revisions", "Surprises"], tab) + body + (d.note ? `<div class="cnote">${esc(d.note)}</div>` : "");
}

function bodyHds(t) {
  const d = eqGet(t, "hds");
  if (!d?.ok) return miss(d, "holders");
  const tab = dTab("hds:tab", "Institutions"), list = tab === "Funds" ? d.funds : d.institutions, s = d.summary || {};
  const p = posBy(t);
  return tabBar("hds:tab", ["Institutions", "Funds"], tab) + `<div class="small" style="margin:3px 0">Institutions hold <b>${dPct(s.institutionsPercentHeld)}</b> (${s.institutionsCount ? n(s.institutionsCount, 0) + " holders" : "--"}) · insiders <b>${dPct(s.insidersPercentHeld, 2)}</b>${p ? ` · you hold <b class="amb">${n(p.shares, 4)} sh</b> (${d.shares_out ? (p.shares / d.shares_out * 1e6).toFixed(3) + " per million" : "--"})` : ""}</div>
    <table class="sortable" data-sortkey="hds"><tr><th>HOLDER</th><th class="num">SHARES</th><th class="num">% OUT</th><th class="num">VALUE</th><th class="num">CHG</th><th class="num">AS OF</th></tr>
    ${(list || []).map((h) => `<tr class="clk" data-holder="${esc(h.holder)}" data-hk="${tab === "Funds" ? "fund" : "inst"}" title="${tab === "Funds" ? "Open this fund" : "Open this holder's 13F"}"><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis" title="${esc(h.holder)}">${esc(h.holder)}</td><td class="num">${dCnt(h.shares)}</td><td class="num">${dPct(h.pct, 2)}</td><td class="num">${dBig(h.value)}</td><td class="num ${cls(h.chg)}" data-v="${h.chg ?? ""}">${spct(h.chg, 1)}</td><td class="num dim" data-v="${h.date ? Date.parse(h.date) : ""}">${esc(fmtDate(h.date))}</td></tr>`).join("") || '<tr><td class="dim">None.</td></tr>'}</table>
    <div class="cnote">Top 10 from the latest 13F and fund filings (Yahoo). CHG = change in shares last period. Click a column to sort; click a holder to open it (institutions: their 13F; funds: the fund).</div>`;
}

function bodyIns(t) {
  const d = eqGet(t, "ins");
  if (!d?.ok) return miss(d, "insider transactions");
  const tab = dTab("ins:tab", "Transactions"), y = d.year || {};
  const sum = `<div class="small" style="margin:3px 0">Last 12 months: <span class="pos">${y.buys ?? 0} buys ${y.buy_value ? dBig(y.buy_value) : ""}</span> · <span class="neg">${y.sells ?? 0} sells ${y.sell_value ? dBig(y.sell_value) : ""}</span></div>`;
  if (tab === "Roster") {
    return tabBar("ins:tab", ["Transactions", "Roster"], tab) + sum + `<table><tr><th>INSIDER</th><th>POSITION</th><th>LATEST</th><th class="num">DATE</th><th class="num">SHARES HELD</th></tr>
      ${(d.roster || []).map((r) => `<tr><td>${esc(r.name)}</td><td class="small">${esc(r.position || "")}</td><td class="small">${esc(r.latest || "")}</td><td class="num dim">${esc(fmtDate(r.date))}</td><td class="num">${dCnt(r.shares)}</td></tr>`).join("")}</table>`;
  }
  return tabBar("ins:tab", ["Transactions", "Roster"], tab) + sum + chartSlot(t, "des-ins", 120, "1Y")
    + `<div class="cnote"><span class="pos">▲</span> insider buy · <span class="neg">▼</span> insider sale (open-market; option exercises and gifts left off)</div><table><tr><th>DATE</th><th>INSIDER</th><th>TYPE</th><th class="num">SHARES</th><th class="num">VALUE</th></tr>
    ${(d.transactions || []).map((x) => `<tr title="${esc(x.text || "")}"><td class="dim">${esc(fmtDate(x.date))}</td><td style="max-width:150px;overflow:hidden;text-overflow:ellipsis">${esc(x.insider)} <span class="dim small">${esc(x.position || "")}</span></td>
      <td class="${x.kind === "buy" ? "pos" : x.kind === "sell" ? "neg" : "dim"}">${esc(x.kind)}</td><td class="num">${dCnt(x.shares)}</td><td class="num">${x.value ? dBig(x.value) : "--"}</td></tr>`).join("")}</table>`;
}

function bodyCn(t) {
  const d = cnData(t), days = cnDays(), bar = cnRangeBar();
  if (!d && days) return bar + `<div class="loading">Searching news from the last ${days} days...</div>`;
  if (!d?.ok) return bar + miss(d, "company news");
  return bar + (d.note ? `<div class="dim small" style="margin-bottom:4px">${esc(d.note)}</div>` : "") + `<table class="cntbl"><colgroup><col style="width:78px"><col style="width:84px"><col></colgroup>${(d.items || []).map((x) => {
    const when = x.time ? new Date(x.time) : null, u = safeUrl(x.url);
    return `<tr title="${esc(x.summary || "")}"><td class="dim small" style="white-space:normal">${when ? esc(when.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })) : esc(x.date || "")}</td>
      <td class="small ${x.kind === "digest" ? "amb" : "dim"}" style="white-space:normal">${esc(x.kind === "digest" ? "YOUR DIGEST" : x.source || "")}</td>
      <td style="white-space:normal">${u ? `<a href="${esc(u)}" target="_blank" rel="noopener" style="color:inherit">${esc(x.title)}</a>` : esc(x.title)}${x.summary ? `<div class="dim small">${esc(x.summary.slice(0, 200))}</div>` : ""}</td></tr>`;
  }).join("")}</table><div class="cnote">${days ? "Google News stories naming the company or ticker, searched month by month, plus Yahoo and your digest." : "Latest company news from Yahoo plus your market-digest mentions. Pick a range to go further back."} Market-wide news lives on MKTS and N.</div>`;
}

// ---------- THESIS cockpit (from the coverage folder's thesis, read-only) ----------
const KST = { ok: ["OK", "var(--pos)"], watch: ["WATCH", "var(--amber)"], hit: ["HIT", "var(--neg)"], unknown: ["--", "#666"] };
function flagsHTML(t) {
  const f = eqGet(t, "flags");
  if (!f) return '<div class="dim small">Scanning the filings...</div>';
  if (!f.ok) return `<div class="dim small">${esc(f.error || "")}</div>`;
  const all = (f.reports || []).flatMap((r) => r.flags.map((x) => ({ ...x, form: r.form, date: r.date, url: r.url })));
  if (!all.length) return `<div class="small pos">None found in ${f.reports.map((r) => `${esc(r.form)} (${esc(r.date)})`).join(" and ") || "the latest filings"}.</div>`;
  const col = { high: "var(--neg)", medium: "var(--amber)", low: "#9a9a9a" };
  return `<table>${all.map((x) => `<tr><td style="width:60px"><span class="dtag" style="color:${col[x.severity]};border-color:${col[x.severity]}">${x.severity.toUpperCase()}</span></td>
    <td style="white-space:normal"><b>${esc(x.flag)}</b> <span class="dim small">${esc(x.form)} ${esc(x.date)}</span><div class="small dim">${esc(x.snippets[0])}</div>
    <a class="small amb" href="${esc(x.url)}" target="_blank" rel="noopener">open filing</a></td></tr>`).join("")}</table>
    <div class="cnote">${esc(f.note)}</div>`;
}
function bodyCockpit(t) {
  const d = eqGet(t, "cockpit");
  if (!d) return '<div class="loading">Reading the thesis...</div>';
  if (!d.ok) return `<div class="${d.covered ? "dim" : "amb"}" style="padding:4px 0">${esc(d.error)}</div>${d.model ? `<div class="small">Model: <a href="#" class="amb" onclick='openPath(${esc(JSON.stringify(d.model.path))});return false'>${esc(d.model.name)}</a> · saved ${esc(d.model.modified)}</div>` : ""}`;
  const p = posBy(t), w = watchBy(t), last = p?.price ?? w?.price ?? CARDS[t]?.d?.last;
  const sc = d.scenarios || [];
  let band = '<div class="dim small">No Bear / Base / Bull values found in the thesis.</div>';
  if (sc.length && last) {
    const vals = [...sc.map((c) => c.value), last, d.weighted].filter((v) => v != null);
    const lo = Math.min(...vals) * 0.95, hi = Math.max(...vals) * 1.05, X = (v) => ((v - lo) / (hi - lo)) * 100;
    const bear = sc.find((c) => c.case === "bear"), bull = sc.find((c) => c.case === "bull"), base = sc.find((c) => c.case === "base");
    const up = (v) => (v && last ? spct(v / last - 1, 0) : "--");
    band = `<div style="position:relative;height:46px;margin:6px 18px 0">
        ${bear && bull ? `<div style="position:absolute;top:16px;left:${X(bear.value)}%;width:${X(bull.value) - X(bear.value)}%;height:8px;background:linear-gradient(90deg,#4a1f1f,#2a2a2a 45%,#1f3a26)"></div>` : ""}
        ${sc.map((c) => `<div title="${esc(c.text)}" style="position:absolute;top:10px;left:${X(c.value)}%;width:2px;height:20px;background:${c.case === "bear" ? "var(--neg)" : c.case === "bull" ? "var(--pos)" : "var(--blue)"}"></div>
          <span class="small" style="position:absolute;top:31px;left:${X(c.value)}%;transform:translateX(-50%);white-space:nowrap">${c.case.toUpperCase()} ${n(c.value, c.value < 100 ? 2 : 0)}${c.prob ? ` · ${pct(c.prob, 0)}` : ""}</span>`).join("")}
        ${d.weighted ? `<div title="probability-weighted" style="position:absolute;top:12px;left:${X(d.weighted)}%;width:6px;height:16px;margin-left:-3px;border:1px solid var(--blue)"></div>` : ""}
        <div title="last" style="position:absolute;top:4px;left:${X(last)}%;width:3px;height:30px;margin-left:-1px;background:var(--amber)"></div>
        <span class="small amb" style="position:absolute;top:-10px;left:${X(last)}%;transform:translateX(-50%);white-space:nowrap">LAST ${n(last)}</span></div>
      <div class="small" style="margin-top:12px">To base <b class="${cls(base?.value - last)}">${up(base?.value)}</b> · to weighted <b class="${cls(d.weighted - last)}">${up(d.weighted)}</b> · downside to bear <b class="${cls(bear?.value - last)}">${up(bear?.value)}</b>${bear && base && last < base.value && bear.value < last ? ` · up/down <b>${n((base.value - last) / (last - bear.value), 2)}x</b>` : ""}</div>`;
  }
  const killers = (d.killers || []).map((k) => `<tr><td style="width:52px"><span class="dtag" style="color:${KST[k.status][1]};border-color:${KST[k.status][1]}">${KST[k.status][0]}</span></td><td style="white-space:normal">${esc(k.text)}${k.detail ? `<div class="dim small">${esc(k.detail)}</div>` : ""}</td></tr>`).join("");
  const kp = d.kpis;
  const kpis = kp ? `<table><tr>${kp.header.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>${kp.rows.map((r) => `<tr>${r.cells.map((c, i) => `<td style="white-space:normal" class="${i === kp.status_col ? (r.status === "ok" ? "pos" : r.status === "hit" ? "neg" : r.status === "watch" ? "amb" : "") : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</table>` : '<div class="dim small">No KPI table found in the thesis.</div>';
  return `<div class="small" style="display:flex;gap:10px;flex-wrap:wrap;align-items:baseline"><b>${esc(d.title)}</b>
      <span class="dim">${esc(d.bucket)} · updated ${esc(d.updated)}</span>
      <a href="#" class="amb" onclick='openPath(${esc(JSON.stringify(d.path))});return false'>open thesis</a>
      ${d.model ? `<a href="#" class="amb" onclick='openPath(${esc(JSON.stringify(d.model.path))});return false'>open model</a> <span class="dim">model saved ${esc(d.model.modified)}${d.model.open_in_excel ? " · open in Excel now" : ""}</span>` : '<span class="dim">no Excel model</span>'}</div>
    <div class="cockpit"><div><div class="sec2">VALUE BAND</div>${band}</div>
      <div><div class="sec2">THESIS KILLERS <span class="dim">any one triggers a review</span></div>${killers ? `<table>${killers}</table>` : '<div class="dim small">None listed.</div>'}</div></div>
    <div class="sec2">RED FLAGS IN THE LATEST 10-K / 10-Q</div>${flagsHTML(t)}
    <div class="sec2">KPIs TO WATCH</div>${kpis}
    <div class="cnote">Read from the thesis file; status words (pass, watch, not triggered) come from your notes. "--" means the thesis gives no status yet.</div>`;
}

function bodyPos(t) {
  const p = posBy(t), w = watchBy(t), d = eqGet(t, "rh");
  const cov = d?.coverage;
  let out = "";
  if (p) {
    const lots = (d?.lots || []).map((l) => ({ ...l, q: +l.quantity, c: +l.cost_per_share })), today = new Date();
    out += `${kv([["Shares", n(p.shares, 4)], ["Avg cost", n(p.avg_cost)], ["Market value", money(p.value)], ["Weight", `${pct(p.weight)} (cap ${pct(S.caps.single_name, 0)})`],
      ["Unrealized", `${smoney(p.unreal_pnl)} (${spct(p.unreal_pct, 1)})`, cls(p.unreal_pnl)], ["Day P&L", smoney(p.day_pnl), cls(p.day_pnl)],
      ["Beta / vol", `${n(p.beta)} / ${pct(p.vol, 0)}`], ["Share of book risk", pct(p.risk_share)]])}
      <div class="sec2">TAX LOTS</div><table><tr><th>OPENED</th><th class="num">QTY</th><th class="num">COST</th><th class="num">UNREAL</th><th>TERM</th><th class="num">LONG-TERM ON</th></tr>
      ${lots.map((l) => { const un = (p.price - l.c) * l.q, lt = new Date(l.open_date + "T00:00:00"); lt.setFullYear(lt.getFullYear() + 1); lt.setDate(lt.getDate() + 1);
        return `<tr><td>${esc(l.open_date)}</td><td class="num">${n(l.q, 4)}</td><td class="num">${n(l.c)}</td><td class="num ${cls(un)}">${smoney(un)}</td><td class="${l.term === "lt" ? "pos" : "amb"}">${l.term === "lt" ? "long" : "short"}</td><td class="num dim">${l.term === "lt" ? "--" : `${lt.toISOString().slice(0, 10)} (${Math.round((lt - today) / 864e5)}d)`}</td></tr>`; }).join("")
        || `<tr><td class="dim" colspan="6">${d ? (d.ok === false ? esc(d.error) : "No lot detail.") : "Loading lots from Robinhood..."}</td></tr>`}</table>
      ${(d?.trades || []).length ? `<div class="sec2">REALIZED TRADES</div><table><tr><th>DATE</th><th>SIDE</th><th class="num">QTY</th><th class="num">PRICE</th><th class="num">REALIZED</th></tr>
        ${d.trades.map((x) => `<tr><td>${esc((x.timestamp || "").slice(0, 10))}</td><td>${esc(x.side)}</td><td class="num">${n(+x.quantity, 4)}</td><td class="num">${n(+x.price)}</td><td class="num ${cls(+x.realized_gain)}">${smoney(+x.realized_gain)}</td></tr>`).join("")}</table>` : ""}`;
  } else if (w) {
    const i = wInfo(w);
    out += `${kv([["Buy-below", w.buy_below ? n(w.buy_below) : "--"], ["Distance", spct(w.dist_pct, 1)], ["Base / bear", `${w.base_value ? n(w.base_value) : "--"} / ${i.bear ? n(i.bear) : "--"}`], ["Up/down", i.ud ? n(i.ud, 2) + "x" : "--"],
      ["Starter", `${n(i.sp, 1)}% = ${money(i.dollars)}`], ["Shares", i.shares == null ? "--" : n(i.shares, 4)], ["Sector after", i.secAfter == null ? "--" : `${pct(i.secAfter)} (cap ${pct(i.cap, 0)})`], ["Tier / review", `${i.tier ? "T" + i.tier : "--"} / ${esc(i.review || "--")}`]])}
      <div class="note" style="white-space:normal">${esc(w.entry_criteria || "")}</div>`;
  } else {
    out += `<div class="dim" style="padding:4px 0">Not held and not on the watchlist.${cov ? "" : " New names start at Gate 0 (the research folder/workflows/underwriting-process.md)."}</div>`;
  }
  if (cov) {
    out += `<div class="sec2">RESEARCH · INVESTING HQ <span class="dim">${esc(cov.bucket)} · newest first</span></div><table>
      ${cov.files.map((x) => `<tr><td><a href="#" class="amb" onclick='openPath(${esc(JSON.stringify(x.path))});return false'>${esc(x.rel)}</a></td></tr>`).join("")}</table>
      <div class="cnote">Opens in your default app (Obsidian or editor).</div>`;
  }
  return out;
}

// ---------- page ----------
const DES_PANELS = [
  ["cockpit", "THESIS", "cockpit · value band, killers, KPIs from your the research folder thesis", bodyCockpit, 12],
  ["des", "DES", "description · key stats", bodyDes, 4],
  ["anr", "ANR", "analyst recommendations", bodyAnr, 4],
  ["cacs", "CACS", "corporate actions", bodyCacs, 4],
  ["rv", "RV", "relative valuation", bodyRv, 4],
  ["fa", "FA", "financial analysis", bodyFa, 4],
  ["hds", "HDS", "holders", bodyHds, 4],
  ["cn", "CN", "company news", bodyCn, 4],
  ["ee", "EE", "earnings estimates", bodyEe, 4],
  ["ins", "INS", "insider transactions", bodyIns, 4],
  ["ern", "ERN", "next earnings · options-implied move vs past reactions and your thesis", bodyErn, 6],
  ["splc", "SPLC", "supply chain · named customers and suppliers from SEC filings · double-click for the full window", bodySplc, 6],
];

function pageSec() {
  const t = secTicker();
  if (!t) return `<div class="loading">Type a ticker in the command bar, then pick "security page" (or Shift+Enter).</div>`;
  lsSet("des:last", t);
  if (typeof setLoaded === "function") setLoaded(t);
  for (const s of DES_SECTIONS) { const hit = EQ[t + "|" + s]; if (!hit || Date.now() - hit.t > EQ_TTL) setTimeout(() => loadEq(t, s), 0); }
  setMarks(t);
  setTimeout(() => fillCards($("main")), 0);
  const strip = DES_PANELS.map(([s, code, lab]) => `<button onclick="desJump('${s}')" title="${esc(lab)}">${code}</button>`).join("") + `<button onclick="desJump('gp')">GP</button><button onclick="desJump('pos')">POS</button>`;
  const header = `<div class="p span2 deshead" data-pid="HEADER" data-span="12"><div class="pb">
      <div data-card="${esc(t)}" data-full="0">${cardHTML(CARDS[t]?.d || null)}</div>
      <div class="rbar fnstrip" style="margin-top:4px"><span class="dim" style="margin-right:6px">${esc(t)} &lt;Equity&gt;</span>${strip}
        <span style="margin-left:auto;display:flex;gap:6px">${posBy(t) || watchBy(t) ? "" : `<button class="mini" data-drill="track:${esc(t)}" title="Add to Keel's tracking list (Shift+T)">Track</button>`}<button class="mini" data-drill="whatif:buy|${esc(t)}||" title="Pre-trade check">What-if buy</button>${posBy(t) ? `<button class="mini" data-drill="whatif:sell|${esc(t)}||" title="Pre-trade check">What-if sell</button>` : ""}<button class="mini" data-drill="tkr:${esc(t)}">Quick view</button>
        <a class="mini" href="https://robinhood.com/stocks/${encodeURIComponent(t)}" target="_blank" rel="noopener">Robinhood</a>
        <button class="mini" onclick="loadSec('${esc(t)}', true)">Reload</button></span></div></div></div>`;
  const panels = DES_PANELS.map(([s, code, lab, , span]) =>
    `<div class="p des" data-pid="${code}" data-span="${span}" data-h="360" data-sec="${s}"><h3>${code} <i>${esc(lab)}</i></h3><div class="pb" data-eq="${s}">${desBody(s, t)}</div></div>`).join("");
  const gp = `<div class="p des" data-pid="GP" data-span="8" data-sec="gp"><h3>GP <i>price · <span class="amb">E</span> earnings <span class="pos">▲</span><span class="neg">▼</span> up/downgrades · D dividends · I insiders</i></h3><div class="pb">${chartSlot(t, "des-gp", 280, "1Y")}</div></div>`;
  const pos = `<div class="p des" data-pid="POSITION" data-span="4" data-sec="pos"><h3>POSITION · RESEARCH <i>${posBy(t) ? "held" : watchBy(t) ? "watchlist" : "not held"}</i></h3><div class="pb" data-eq="pos">${bodyPos(t)}</div></div>`;
  return `<div class="grid">${header}${panels}${gp}${pos}</div>`;
}

function desBody(s, t) {
  const p = DES_PANELS.find((x) => x[0] === s);
  try { return p ? p[3](t) : s === "pos" || s === "rh" ? bodyPos(t) : ""; }
  catch (e) { return `<div class="neg">Panel failed to draw: ${esc(e.message)}</div>`; }
}

/** Repaint one panel after its data arrives or a tab changes (no full-page render, so nothing jumps). */
function desPaint(s) {
  if (page !== "SEC") return;
  const t = secTicker();
  const targets = s === "rh" ? ["pos"] : s === "flags" ? ["cockpit"] : [s];
  for (const k of targets) {
    const el = document.querySelector(`[data-eq="${k}"]`);
    if (!el) continue;
    const top = el.scrollTop;
    el.innerHTML = desBody(k, t);
    el.scrollTop = top;
    hydrate(el);
  }
  if (["anr", "cacs", "ins"].includes(s)) {
    setMarks(t);
    document.querySelectorAll('.chart[data-key="des-gp"],.chart[data-key="des-ins"]').forEach((c) => hydrate(c.parentElement));
  }
}

function desJump(s) {
  const el = document.querySelector(`.p[data-sec="${s}"]`);
  if (!el) return;
  if (el.style.display === "none") { showPanel(el.dataset.pid); return desJump(s); }
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 900);
}

/** Event markers for the GP and INS charts, built from whatever sections have loaded. */
function setMarks(t) {
  const { gp, ins } = marksFor(t);
  CHART_MARKS["des-gp"] = gp;
  CHART_MARKS["des-ins"] = ins;
}
function marksFor(t) {
  const gp = [], ins = [];
  for (const e of eqGet(t, "cacs")?.events || []) {
    if (e.future) continue;
    if (e.type === "Earnings") gp.push({ date: e.date, kind: "E", label: e.text });
    else if (e.type === "Dividend") gp.push({ date: e.date, kind: "D", label: e.text });
    else if (e.type === "Split") gp.push({ date: e.date, kind: "S", label: e.text });
  }
  for (const f of eqGet(t, "anr")?.firms || []) {
    if (f.action === "up" || f.action === "down") gp.push({ date: f.date, kind: f.action === "up" ? "UP" : "DN", label: `${f.firm}: ${f.action === "up" ? "upgrade" : "downgrade"} to ${f.grade}${f.target ? ", target " + f.target : ""}` });
  }
  for (const x of eqGet(t, "ins")?.transactions || []) {
    if (x.kind !== "buy" && x.kind !== "sell") continue;
    const m = { date: x.date, kind: x.kind === "buy" ? "IB" : "IS", label: `${x.insider} ${x.kind} ${dCnt(x.shares)} sh${x.value ? " (" + dBig(x.value) + ")" : ""}` };
    ins.push(m);
    gp.push({ ...m, kind: x.kind === "buy" ? "IB" : "IS" });
  }
  return { gp, ins };
}
