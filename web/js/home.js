/* HOME: the one-glance page. Every number opens a drill-down. */
"use strict";

// ---------- TODAY: live brief (/api/brief, rebuilt every 30 s while HOME is open) ----------
let BRIEF = null, briefAt = 0, briefBusy = false;
async function loadBrief(force = false) {
  if (briefBusy || (!force && BRIEF && Date.now() - briefAt < 25e3)) return BRIEF;
  briefBusy = true;
  try { BRIEF = await (await fetch("/api/brief" + (force ? "?fresh=true" : ""))).json(); briefAt = Date.now(); }
  catch (e) { BRIEF = BRIEF || { ok: false, error: e.message }; }
  briefBusy = false;
  const el = document.querySelector('[data-brief]');
  if (el && page === "HOME") el.innerHTML = briefBody();
  return BRIEF;
}
setInterval(() => { if (page === "HOME" && !getLay().hidden.includes("TODAY-LIVE-BRIEF")) loadBrief(); }, 30e3);

function pathSpark(path, prev, w = 90, h = 18) {
  if (!path || path.length < 2) return "";
  const vals = prev ? [...path, prev] : path, lo = Math.min(...vals), hi = Math.max(...vals), r = hi - lo || 1;
  const Y = (v) => (h - 2 - ((v - lo) / r) * (h - 4)).toFixed(1);
  // The session runs 9:30-16:00; draw only the part of the day that has happened.
  const now = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const frac = Math.max(0.05, Math.min(1, ((now.getHours() * 60 + now.getMinutes()) - 570) / 390));
  const span = (BRIEF?.session === "market") ? frac * w : w;
  const pts = path.map((v, i) => `${((i / (path.length - 1)) * span).toFixed(1)},${Y(v)}`).join(" ");
  const c = path.at(-1) >= (prev ?? path[0]) ? "var(--pos)" : "var(--neg)";
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${prev ? `<line x1="0" x2="${w}" y1="${Y(prev)}" y2="${Y(prev)}" stroke="#555" stroke-dasharray="2 2"/>` : ""}<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.3"/></svg>`;
}

function briefBody() {
  const b = BRIEF;
  if (!b) return '<span class="dim">Reading the tape (about 8 seconds the first time)...</span>';
  if (!b.ok) return `<span class="neg">${esc(b.error || "Brief unavailable")}</span>`;
  const t = new Date(b.as_of).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false });
  const li = (arr) => (arr || []).map((x) => `<li>${esc(x)}</li>`).join("");
  const rs = (b.rs || []).map((r) => `<tr data-drill="tkr:${esc(r.ticker)}"><td class="amb">${esc(r.ticker)}${r.held ? ' <span class="tagh">H</span>' : ""}</td>
      <td class="num ${cls(r.chg)}">${spct(r.chg, 1)}</td><td class="num ${cls(r.week)}">${spct(r.week, 1)}</td>
      <td class="num ${cls(r.rs_today)}">${r.rs_today == null ? "--" : (r.rs_today * 100).toFixed(1)}</td><td class="small ${/leading/.test(r.tag) ? "pos" : /lagging both/.test(r.tag) ? "neg" : "dim"}">${esc(r.tag)}</td></tr>`).join("");
  const DG = { act: ["ACT", "var(--neg)"], dont: ["DON'T", "var(--amber)"], wait: ["WAIT", "var(--blue)"], watch: ["WATCH", "#9a9a9a"], note: ["NOTE", "#666"] };
  const dg = b.digest;
  const digestHTML = dg ? `<div class="dig"><div class="dghead">1-MINUTE DIGEST <span class="dim">what matters now, and what it means for the book</span></div>
      ${dg.verdict ? `<div class="dverd">${esc(dg.verdict)}</div>` : ""}
      ${(dg.items || []).map((x) => `<div class="dline"${x.drill ? ` data-drill="${esc(x.drill)}"` : ""}><span class="dtag" style="color:${DG[x.kind][1]};border-color:${DG[x.kind][1]}">${DG[x.kind][0]}</span><span>${esc(x.text)}</span></div>`).join("")}
      <div class="dnet">${esc(dg.net)}</div></div>` : "";
  return digestHTML + `<div class="bhead">${esc(b.headline)} <span class="dim small">as of ${esc(t)} ET${b.mins_to_close != null ? ` · ${b.mins_to_close} min to close` : ""}</span></div>
    <div class="bgrid">
      <div><div class="sec2">THE TAPE</div><table>${(b.tape || []).map((x) => `<tr data-drill="tkr:${esc(x.sym)}"><td class="amb" style="width:118px">${esc(x.label)}</td><td style="width:96px">${pathSpark(x.path, x.prev)}</td><td class="small">${esc(x.text)}</td></tr>`).join("")}</table>
        <div class="sec2">UNDER THE HOOD</div><ul class="bl">${li(b.breadth)}</ul>
        <div class="gchips">${(b.gauges || []).map((g) => `<span class="${cls(g.chg)}">${esc(g.label)} <b>${esc(g.text)}</b></span>`).join("")}</div></div>
      <div><div class="sec2">INTO THE CLOSE</div><ul class="bl">${li(b.close)}</ul>
        <div class="sec2">YOUR BOOK</div><ul class="bl">${li(b.book)}</ul>
        ${(b.next || []).length ? `<div class="sec2">NEXT</div><ul class="bl">${li(b.next)}</ul>` : ""}</div>
      <div><div class="sec2">RELATIVE STRENGTH · your names (move vs beta x S&P)</div><table><tr><th>TKR</th><th class="num">DAY</th><th class="num">WK BEFORE</th><th class="num">RS pts</th><th>READ</th></tr>${rs}</table></div>
    </div>`;
}

function pageHome() {
  const a = S.account, rp = S.risk?.portfolio || {};
  if (!PERF) setTimeout(() => loadPerf().then(() => { if (page === "HOME") render(); }), 0);
  if (!BRIEF) setTimeout(() => loadBrief(), 0);
  const today = `<div class="p span2 brief"><h3>TODAY · LIVE BRIEF <i>tape, internals, the close, your names · refreshes every 30 s</i></h3><div class="pb" data-brief>${briefBody()}</div></div>`;

  const book = `<div class="p"><h3>BOOK <i>1D: today's holdings live · longer: actual, flow-adjusted (TWR)</i></h3><div class="pb">
    ${chartSlot("BOOK", "home-book", 190, "1D")}
    <div class="stats">${[
      ["TWR MTD", perfRow("MTD") ? spct(perfRow("MTD").book) : "--", "twr:MTD"], ["TWR YTD", perfRow("YTD") ? spct(perfRow("YTD").book) : "--", "twr:YTD"], ["Beta (2y wk)", n(rp.beta), "beta"], ["Vol (1y)", pct(rp.vol), "vol"],
      ["VaR 95 1d", money(rp.var95_hist), "var"], ["ES 95 1d", money(rp.es95_hist), "es"], ["Max DD 1y", pct(rp.max_drawdown_1y), "dd"], ["Eff. names", n(rp.effective_n, 1), "effn"],
    ].map(([l, v, k]) => `<div class="stat" data-drill="${k.startsWith("twr:") ? "perf:" + k.slice(4) : "risk:" + k}"><span>${l}</span><b class="${k.startsWith("twr:") && perfRow(k.slice(4)) ? cls(perfRow(k.slice(4)).book) : ""}">${v}</b></div>`).join("")}</div></div></div>`;

  const ca = S.cross_asset || [];
  const EQ = ["ES=F", "NQ=F", "^RUT", "^VIX", "SMH"];
  const row = (c) => `<tr data-drill="mkt:${esc(c.symbol)}"><td>${esc(c.label)}</td><td class="num">${n(c.last, Math.abs(c.last) >= 1000 ? 0 : 2)}</td><td class="num ${cls(c.change)}">${c.change > 0 ? "+" : ""}${n(c.change)}</td><td class="num ${cls(c.change_pct)}">${spct(c.change_pct)}</td></tr>`;
  const fred = (S.fred || []).map((f) => {
    const sc = f.scale || 1, dp = sc > 1 ? 0 : 2, d = f.prev != null ? f.last - f.prev : null;
    return `<tr data-drill="fred:${esc(f.label)}"><td>${esc(f.label)}</td><td class="num">${n(f.last * sc, dp)}</td><td class="num ${cls(d)}">${d == null ? "--" : (d > 0 ? "+" : "") + n(d * sc, dp)}</td><td class="num dim">${esc((f.date || "").slice(5))}</td></tr>`;
  }).join("");
  const mkts = `<div class="p"><h3>MARKETS <i>SPY ${spct(S.benchmark?.change_pct)} · full grid on MKTS</i></h3><div class="pb"><table>
    <tr><th>INSTRUMENT</th><th class="num">LAST</th><th class="num">CHG</th><th class="num">%CHG</th></tr>
    <tr class="sec"><td colspan="4">EQUITY</td></tr>${ca.filter((c) => EQ.includes(c.symbol)).map(row).join("")}
    <tr class="sec"><td colspan="4">RATES · FX · COMMODITIES</td></tr>${ca.filter((c) => !EQ.includes(c.symbol)).map(row).join("")}
    <tr class="sec"><td colspan="4">CREDIT · MACRO (FRED, daily)</td></tr>${fred}
  </table></div></div>`;

  const pos = [...S.positions].sort((x, y) => (y.day_pnl ?? 0) - (x.day_pnl ?? 0));
  const maxAbs = Math.max(...pos.map((p) => Math.abs(p.day_pnl || 0)), 1);
  const contrib = `<div class="p"><h3>P&amp;L ATTRIBUTION TODAY <i>${pos.length} names · ${smoney(a.day_pnl)}</i></h3><div class="pb"><table>
    <tr><th>TKR</th><th class="num">PX</th><th class="num">DAY</th><th class="num">P&amp;L</th><th style="width:36%"></th><th class="num">WT</th></tr>
    ${pos.map((p) => {
      const w = (Math.abs(p.day_pnl || 0) / maxAbs) * 50, up = (p.day_pnl || 0) >= 0;
      return `<tr data-drill="tkr:${p.ticker}"><td class="amb">${p.ticker}</td><td class="num">${n(p.price)}</td><td class="num ${cls(p.day_pct)}">${spct(p.day_pct, 1)}</td><td class="num ${cls(p.day_pnl)}">${smoney(p.day_pnl)}</td>
        <td><div style="position:relative;height:9px"><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#333"></div><div style="position:absolute;top:0;height:9px;${up ? "left:50%" : "right:50%"};width:${w}%;background:${up ? "var(--pos)" : "var(--neg)"}"></div></div></td>
        <td class="num dim">${pct(p.weight)}</td></tr>`;
    }).join("")}
  </table></div></div>`;

  const caps = S.caps || {}, scale = 0.4;
  const secs = Object.entries(S.aggregates?.sector || {}).sort((x, y) => y[1] - x[1]);
  const comp = `<div class="p"><h3>COMPOSITION vs CAPS <i>sector · amber tick = cap</i></h3><div class="pb"><table>
    ${secs.map(([k, v]) => {
      const c = capFor(k), near = c && v >= c * (caps.warn_ratio || 0.9), over = c && v > c;
      return `<tr data-drill="sector:${esc(k)}"><td style="width:36%">${esc(k)}</td><td style="width:45%"><div class="bar"><div style="width:${Math.min(v / scale, 1) * 100}%;background:${over ? "var(--neg)" : near ? "var(--amber)" : "var(--blue)"}"></div>${c ? `<div class="cap" style="left:${(c / scale) * 100}%"></div>` : ""}</div></td><td class="num">${pct(v)}</td><td class="num dim">${c ? pct(c, 0) : "--"}</td></tr>`;
    }).join("")}
    <tr data-drill="kpi:cash"><td>Cash</td><td><div class="bar"><div style="width:${Math.min((S.aggregates?.cash || 0) / scale, 1) * 100}%;background:#555"></div></div></td><td class="num">${pct(S.aggregates?.cash)}</td><td></td></tr>
  </table></div></div>`;

  const dg = (S.digest || [])[0] || {};
  const news = `<div class="p span2"><h3>HEADLINES <i>digest ${esc(dg.date || "")} · click to expand</i></h3><div class="pb">
    ${(dg.headlines || []).map((h, i) => { const m = h.match(/^([^:]{3,40}):\s*(.*)$/s); return `<div class="head" data-drill="news:${i}"><span class="src">${esc(m ? m[1] : "Digest")}</span>${esc(m ? m[2] : h)}</div>`; }).join("") || '<span class="dim">No digest yet.</span>'}
  </div></div>`;

  return `<div class="grid">${today}${book}${mkts}${contrib}${comp}${news}</div>`;
}
