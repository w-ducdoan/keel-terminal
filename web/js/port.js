/* PORT: portfolio management. Performance (TWR vs SPY), live composition, positions and risk. */
"use strict";

// ---------- performance data (fetched separately; the rebuild takes a few seconds) ----------
let PERF = null, perfAt = 0, perfBusy = false;
async function loadPerf(force = false) {
  if (perfBusy || (!force && PERF && Date.now() - perfAt < 60e3)) return PERF;
  perfBusy = true;
  try {
    const r = await fetch("/api/performance" + (force ? "?fresh=true" : ""));
    PERF = await r.json();
    perfAt = Date.now();
  } catch (e) { PERF = { ok: false, error: e.message }; }
  perfBusy = false;
  document.querySelectorAll("[data-perf]").forEach((el) => { el.innerHTML = PERF_PANELS[el.dataset.perf](); hydratePerf(el); });
  return PERF;
}
const perfRow = (k) => (PERF?.periods || []).find((p) => p.period === k);

const PERF_RANGES = ["1M", "3M", "6M", "YTD", "1Y", "MAX"];
function perfChartHTML() {
  const r = lsGet("rng:perf") || "YTD";
  return `<div class="perfchart" data-rng="${r}"><div class="rbar prbar">${PERF_RANGES.map((x) => `<button data-pr="${x}" class="${x === r ? "on" : ""}">${x}</button>`).join("")}
    <span class="chg"></span></div><div class="cplot" style="height:210px"></div>
    <div class="cnote"><span style="color:var(--amber)">&#9632;</span> Book TWR &nbsp; <span style="color:#9aa">&#9632;</span> SPY (price return) &nbsp; rebased to the start of the range. Monthly points from performance-log.md, daily after ${esc(PERF?.anchor_date || "")}.</div></div>`;
}
function drawPerf(el) {
  const pts = PERF?.points || [];
  if (pts.length < 2) return;
  const rng = el.dataset.rng, today = new Date(PERF.today + "T12:00:00");
  const start = { "1M": new Date(today - 31 * 864e5), "3M": new Date(today - 92 * 864e5), "6M": new Date(today - 183 * 864e5), YTD: new Date(today.getFullYear(), 0, 1), "1Y": new Date(today - 365 * 864e5), MAX: new Date(0) }[rng];
  const iso = start.toISOString().slice(0, 10);
  const before = pts.filter((p) => p.date < iso), inRange = pts.filter((p) => p.date >= iso);
  const ser = (before.length ? [before.at(-1)] : []).concat(inRange);
  if (ser.length < 2) { el.querySelector(".cplot").innerHTML = '<span class="dim">Not enough points in this range.</span>'; return; }
  const b0 = 1 + ser[0].twr, s0 = 1 + ser[0].spy;
  const book = ser.map((p) => (1 + p.twr) / b0 - 1), spy = ser.map((p) => (1 + p.spy) / s0 - 1);
  const w = 700, h = 210, padR = 52, lo = Math.min(...book, ...spy, 0), hi = Math.max(...book, ...spy, 0), rr = hi - lo || 1;
  const t0 = new Date(ser[0].date).getTime(), t1 = new Date(ser.at(-1).date).getTime();
  const X = (p) => ((new Date(p.date).getTime() - t0) / (t1 - t0 || 1)) * (w - padR), Y = (v) => h - 16 - ((v - lo) / rr) * (h - 26);
  const line = (vals, c, wd) => `<polyline points="${ser.map((p, i) => `${X(p).toFixed(1)},${Y(vals[i]).toFixed(1)}`).join(" ")}" fill="none" stroke="${c}" stroke-width="${wd}" vector-effect="non-scaling-stroke"/>`;
  const grid = [lo, 0, hi].map((v) => `<line x1="0" x2="${w - padR}" y1="${Y(v)}" y2="${Y(v)}" stroke="${v === 0 ? "#444" : "#1f1f1f"}"/><text x="${w - padR + 4}" y="${Y(v) + 4}" fill="#8a8a8a" font-size="10">${spct(v, 1)}</text>`).join("");
  const ticks = [0, 0.5, 1].map((f) => { const p = ser[Math.round(f * (ser.length - 1))]; return `<text x="${X(p)}" y="${h - 2}" fill="#6a6a6a" font-size="9" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${p.date}</text>`; }).join("");
  const dots = ser.map((p, i) => p.source === "log" ? `<circle cx="${X(p)}" cy="${Y(book[i])}" r="2.2" fill="var(--amber)"/>` : "").join("");
  const plot = el.querySelector(".cplot");
  plot.innerHTML = `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${grid}${line(spy, "#9aa", 1.2)}${line(book, "var(--amber)", 1.6)}${dots}${ticks}
    <line class="xh" x1="0" x2="0" y1="0" y2="${h - 16}" stroke="#777" visibility="hidden"/>
    <rect class="mband" x="0" y="0" width="0" height="${h - 16}" fill="rgba(255,160,40,.10)" visibility="hidden"/></svg><div class="hov" hidden></div><div class="meas" hidden></div>`;
  const ex = book.at(-1) - spy.at(-1), chg = el.querySelector(".chg");
  chg.className = "chg " + cls(ex);
  chg.textContent = `${rng}  book ${spct(book.at(-1))}  SPY ${spct(spy.at(-1))}  excess ${ex > 0 ? "+" : ""}${(ex * 100).toFixed(2)} pts`;
  const svg = plot.querySelector("svg"), hov = plot.querySelector(".hov"), xh = svg.querySelector(".xh");
  const band = svg.querySelector(".mband"), meas = plot.querySelector(".meas");
  let A = null, B = null;
  const near = (fx) => { let i = 0, best = 1e9; ser.forEach((p, k) => { const d = Math.abs(X(p) - fx); if (d < best) { best = d; i = k; } }); return i; };
  const showM = (i, j) => {
    const [a, b] = i <= j ? [i, j] : [j, i];
    band.setAttribute("x", X(ser[a])); band.setAttribute("width", Math.max(1, X(ser[b]) - X(ser[a]))); band.setAttribute("visibility", "visible");
    const bk = (1 + ser[j].twr) / (1 + ser[i].twr) - 1, sp = (1 + ser[j].spy) / (1 + ser[i].spy) - 1;
    meas.hidden = false;
    meas.innerHTML = `<span class="dim">${ser[i].date} → ${ser[j].date}</span> book <b class="${cls(bk)}">${spct(bk)}</b> · SPY <b class="${cls(sp)}">${spct(sp)}</b> · excess <b class="${cls(bk - sp)}">${((bk - sp) * 100).toFixed(2)} pts</b> <span class="dim">${B == null ? "· click to pin" : "· click to restart, Esc to clear"}</span>`;
  };
  el._clearMeasure = () => { A = B = null; band.setAttribute("visibility", "hidden"); meas.hidden = true; };
  svg.style.cursor = "crosshair";
  svg.onclick = (e) => {
    const bb = svg.getBoundingClientRect(), i = near(((e.clientX - bb.left) / bb.width) * w);
    if (A == null || B != null) { A = i; B = null; showM(A, A); } else { B = i; showM(A, B); }
  };
  svg.onmousemove = (e) => {
    const bb = svg.getBoundingClientRect(), fx = ((e.clientX - bb.left) / bb.width) * w;
    const i = near(fx);
    if (A != null && B == null) showM(A, i);
    xh.setAttribute("x1", X(ser[i])); xh.setAttribute("x2", X(ser[i])); xh.setAttribute("visibility", "visible");
    hov.hidden = false;
    hov.innerHTML = `${ser[i].date}${ser[i].label ? " (" + esc(ser[i].label) + ")" : ""}  book <b class="${cls(book[i])}">${spct(book[i])}</b>  SPY <b>${spct(spy[i])}</b>  NAV ${money(ser[i].nav ?? ser[i].value)}`;
    hov.style.left = Math.min(bb.width - 330, (X(ser[i]) / w) * bb.width + 8) + "px";
  };
  svg.onmouseleave = () => { hov.hidden = true; xh.setAttribute("visibility", "hidden"); };
}
function hydratePerf(root) { root.querySelectorAll(".perfchart").forEach(drawPerf); }
document.addEventListener("click", (e) => {
  const b = e.target.closest(".prbar button");
  if (!b) return;
  e.stopPropagation();
  const box = b.closest(".perfchart");
  box.dataset.rng = b.dataset.pr;
  lsSet("rng:perf", b.dataset.pr);
  box.querySelectorAll(".prbar button").forEach((x) => x.classList.toggle("on", x === b));
  drawPerf(box);
}, true);

const PERF_PANELS = {
  table() {
    if (!PERF) return '<span class="dim">Rebuilding returns from the performance log and Robinhood fills...</span>';
    if (!PERF.ok) return `<span class="neg">${esc(PERF.error || "Performance unavailable")}</span>`;
    const c = PERF.check || {};
    const flag = c.unexplained_flow ? `<div class="neg" data-drill="perfcheck:" style="margin-top:4px">Unexplained cash change ${smoney(c.unexplained_flow)} since ${esc(c.anchor_date)}: kept out of today's return. Log the deposit or withdrawal.</div>` : "";
    return `<table><tr><th>PERIOD</th><th class="num">BOOK TWR</th><th class="num">SPY</th><th class="num">EXCESS</th><th class="num dim">FROM</th></tr>
      ${PERF.periods.map((p) => `<tr data-drill="perf:${p.period}"><td class="amb">${p.period}</td><td class="num ${cls(p.book)}"><b>${spct(p.book)}</b></td><td class="num ${cls(p.spy)}">${spct(p.spy)}</td><td class="num ${cls(p.excess)}">${p.excess > 0 ? "+" : ""}${(p.excess * 100).toFixed(2)} pts</td><td class="num dim">${p.base_date}</td></tr>`).join("")}
      ${perfRow("ITD")?.book_ann != null ? `<tr data-drill="perf:ITD"><td class="dim">ITD ann.</td><td class="num ${cls(perfRow("ITD").book_ann)}">${spct(perfRow("ITD").book_ann)}</td><td class="num">${spct(perfRow("ITD").spy_ann)}</td><td class="num ${cls(perfRow("ITD").book_ann - perfRow("ITD").spy_ann)}">${((perfRow("ITD").book_ann - perfRow("ITD").spy_ann) * 100).toFixed(2)} pts</td><td></td></tr>` : ""}
      </table>
      <div class="cnote" data-drill="perfcheck:">Check: rebuilt ${esc(c.anchor_date || "")} NAV ${money(c.anchor_value_rebuilt)} vs log ${money(c.anchor_value_log)} · cash residual ${smoney(c.cash_residual)} · ${c.fills ?? 0} fills since${PERF.warning ? ` · <span class="neg">${esc(PERF.warning)}</span>` : ""}</div>${flag}`;
  },
  chart() {
    if (!PERF?.ok) return '<span class="dim">Waiting for returns...</span>';
    return perfChartHTML();
  },
};

// ---------- page ----------
let portSort = JSON.parse(lsGet("port:sort") || '{"k":"value","d":-1}');
let compTab = lsGet("port:comp") || "sector";
const PCOLS = [
  ["ticker", "TKR"], ["shares", "SHARES", 1], ["avg_cost", "AVG", 1], ["price", "LAST", 1], ["day_pct", "DAY %", 1], ["day_pnl", "DAY P&L", 1],
  ["value", "VALUE", 1], ["weight", "WT", 1], ["capuse", "CAP USE", 1], ["unreal_pnl", "UNREAL", 1], ["unreal_pct", "UNREAL %", 1],
  ["beta", "BETA", 1], ["vol", "VOL", 1], ["risk_share", "RISK %", 1], ["spark", "", 0],
];
function sortPort(k) {
  if (k === "spark") return;
  portSort = { k, d: portSort.k === k ? -portSort.d : (k === "ticker" ? 1 : -1) };
  lsSet("port:sort", JSON.stringify(portSort));
  render();
}

function pagePort() {
  const a = S.account, rp = S.risk?.portfolio || {}, caps = S.caps || {};
  setTimeout(() => loadPerf(), 0);

  const perf = `<div class="p"><h3>PERFORMANCE <i>time-weighted · ${esc(PERF?.today || "")}</i></h3><div class="pb" data-perf="table">${PERF_PANELS.table()}</div></div>`;
  const perfChart = `<div class="p"><h3>TWR vs SPY <i>cumulative, rebased</i></h3><div class="pb" data-perf="chart">${PERF_PANELS.chart()}</div></div>`;

  const rows = S.positions.map((p) => ({ ...p, capuse: p.weight / caps.single_name }));
  rows.sort((x, y) => { const k = portSort.k, A = x[k], B = y[k]; return (typeof A === "string" ? String(A).localeCompare(B) : (A ?? -1e9) - (B ?? -1e9)) * portSort.d; });
  const tot = { value: rows.reduce((s, p) => s + p.value, 0), day: rows.reduce((s, p) => s + (p.day_pnl || 0), 0), un: rows.reduce((s, p) => s + (p.unreal_pnl || 0), 0) };
  const positions = `<div class="p span2"><h3>POSITIONS <i>${rows.length} names · live · click a header to sort</i></h3>${sparkBar()}<div class="pb"><table class="ptable">
    <tr>${PCOLS.map(([k, l, num]) => `<th class="${num ? "num" : ""} sort" onclick="sortPort('${k}')">${l}${portSort.k === k ? (portSort.d < 0 ? " ▼" : " ▲") : ""}</th>`).join("")}</tr>
    ${rows.map((p) => {
      const cu = p.capuse, cuc = cu > 1 ? "var(--neg)" : cu >= (caps.warn_ratio || 0.9) ? "var(--amber)" : "var(--blue)";
      const betaFlag = p.beta > 1.8 ? ' style="color:var(--neg)"' : "";
      return `<tr data-drill="tkr:${esc(p.ticker)}"><td class="amb">${esc(p.ticker)}</td><td class="num">${n(p.shares, p.shares % 1 ? 4 : 0)}</td><td class="num">${n(p.avg_cost)}</td><td class="num">${n(p.price)}</td>
        <td class="num ${cls(p.day_pct)}">${spct(p.day_pct, 1)}</td><td class="num ${cls(p.day_pnl)}">${smoney(p.day_pnl)}</td><td class="num">${money(p.value)}</td><td class="num">${pct(p.weight)}</td>
        <td><div class="bar" title="${pct(cu, 0)} of the ${pct(caps.single_name, 0)} name cap"><div style="width:${Math.min(cu, 1) * 100}%;background:${cuc}"></div></div></td>
        <td class="num ${cls(p.unreal_pnl)}">${smoney(p.unreal_pnl)}</td><td class="num ${cls(p.unreal_pct)}">${spct(p.unreal_pct, 1)}</td>
        <td class="num"${betaFlag}>${n(p.beta)}</td><td class="num">${pct(p.vol, 0)}</td><td class="num">${pct(p.risk_share)}</td>
        <td style="width:90px" class="spark" data-sym="${esc(p.ticker)}"></td></tr>`;
    }).join("")}
    <tr class="tot"><td>TOTAL</td><td colspan="4"></td><td class="num ${cls(tot.day)}">${smoney(tot.day)}</td><td class="num">${money(tot.value)}</td><td class="num">${pct(tot.value / a.nav)}</td><td></td><td class="num ${cls(tot.un)}">${smoney(tot.un)}</td><td colspan="5" class="dim">+ cash ${money(a.cash)} (${pct(a.cash / a.nav)}) = NAV ${money(a.nav)}</td></tr>
  </table></div></div>`;

  // composition
  const agg = S.aggregates || {}, scale = 0.4;
  const compRows = {
    sector: Object.entries(agg.sector || {}).map(([k, v]) => [k, v, capFor(k), "sector"]),
    sleeve: Object.entries(agg.sleeve || {}).map(([k, v]) => [k, v, k === "hedge" ? caps.hedge_sleeve_cap : k === "event_pod" ? caps.event_pod : null, "sleeve"]),
    bucket: Object.entries(agg.bucket || {}).map(([k, v]) => [k, v, null, "bucket"]),
  }[compTab].sort((x, y) => y[1] - x[1]);
  const comp = `<div class="p"><h3>COMPOSITION <i>live weights · amber tick = cap</i></h3><div class="pb">
    <div class="rbar tabs2" style="margin-bottom:5px">${["sector", "sleeve", "bucket"].map((t) => `<button class="${t === compTab ? "on" : ""}" onclick="compTab='${t}';lsSet('port:comp','${t}');render()">${t.toUpperCase()}</button>`).join("")}</div>
    <table>${compRows.map(([k, v, c, kind]) => {
      const near = c && v >= c * (caps.warn_ratio || 0.9), over = c && v > c;
      const floor = kind === "sleeve" && k === "hedge" ? `<div class="cap" style="left:${(caps.hedge_sleeve_floor / scale) * 100}%;background:#666"></div>` : "";
      const under = kind === "sleeve" && k === "hedge" && v < caps.hedge_sleeve_floor;
      return `<tr data-drill="${kind}:${esc(k)}"><td style="width:38%">${esc(kind === "sleeve" ? sleeveName(k) : k)}</td><td style="width:42%"><div class="bar"><div style="width:${Math.min(v / scale, 1) * 100}%;background:${over ? "var(--neg)" : near || under ? "var(--amber)" : "var(--blue)"}"></div>${c ? `<div class="cap" style="left:${Math.min(c / scale, 1) * 100}%"></div>` : ""}${floor}</div></td><td class="num">${pct(v)}</td><td class="num dim">${c ? pct(c, 0) : "--"}</td></tr>`;
    }).join("")}
    <tr data-drill="kpi:cash"><td>Cash</td><td><div class="bar"><div style="width:${Math.min((agg.cash || 0) / scale, 1) * 100}%;background:#555"></div></div></td><td class="num">${pct(agg.cash)}</td><td></td></tr></table>
    ${compTab === "sleeve" ? `<div class="cnote">Hedge sleeve: floor ${pct(caps.hedge_sleeve_floor, 0)} (grey tick), target ${pct(caps.hedge_sleeve_target, 0)}, cap ${pct(caps.hedge_sleeve_cap, 0)}.</div>` : ""}</div></div>`;

  // limits
  const top = [...S.positions].sort((x, y) => y.weight - x.weight)[0] || {};
  const secMax = Object.entries(agg.sector || {}).filter(([k]) => capFor(k) && k !== "Hedge").sort((x, y) => y[1] / capFor(y[0]) - x[1] / capFor(x[0]))[0] || ["", 0];
  const pod = agg.sleeve?.event_pod || 0, hedge = agg.sleeve?.hedge || 0, hiBeta = S.positions.filter((p) => p.beta > 1.8);
  const lim = [
    ["Largest name", `${top.ticker} ${pct(top.weight)}`, top.weight / caps.single_name, pct(caps.single_name, 0), `tkr:${top.ticker}`],
    ["Tightest sector", `${secMax[0]} ${pct(secMax[1])}`, secMax[1] / (capFor(secMax[0]) || 1), pct(capFor(secMax[0]), 0), `sector:${secMax[0]}`],
    ["Consumer Disc.", pct(agg.sector?.["Consumer Discretionary"] || 0), (agg.sector?.["Consumer Discretionary"] || 0) / caps.consumer_discretionary, pct(caps.consumer_discretionary, 0), "sector:Consumer Discretionary"],
    ["Hedge sleeve", pct(hedge), hedge / caps.hedge_sleeve_cap, `${pct(caps.hedge_sleeve_floor, 0)}-${pct(caps.hedge_sleeve_cap, 0)}`, "sleeve:hedge"],
    ["Event pod", pct(pod), pod / caps.event_pod, pct(caps.event_pod, 0), "sleeve:event_pod"],
  ];
  const limits = `<div class="p"><h3>MANDATE LIMITS <i>usage vs cap</i></h3><div class="pb"><table>
    ${lim.map(([l, v, u, c, d]) => `<tr data-drill="${esc(d)}"><td style="width:34%">${l}</td><td class="num" style="width:26%">${esc(v)}</td><td style="width:28%"><div class="bar"><div style="width:${Math.min(u, 1) * 100}%;background:${u > 1 ? "var(--neg)" : u >= (caps.warn_ratio || 0.9) ? "var(--amber)" : "var(--blue)"}"></div></div></td><td class="num dim">${c}</td></tr>`).join("")}
    <tr data-drill="risk:beta"><td>Names beta &gt; 1.8</td><td class="num ${hiBeta.length ? "amb" : ""}">${hiBeta.map((p) => p.ticker).join(", ") || "none"}</td><td colspan="2" class="dim">${hiBeta.length ? "need written overrides" : ""}</td></tr>
  </table></div></div>`;

  // risk
  const stats = `<div class="p"><h3>RISK <i>1y daily at today's weights · beta 2y weekly</i></h3><div class="pb"><div class="stats">${[
    ["Vol (ann.)", pct(rp.vol), "vol"], ["Beta", n(rp.beta), "beta"], ["VaR 95 1d", money(rp.var95_hist), "var"], ["ES 95 1d", money(rp.es95_hist), "es"],
    ["Max DD 1y", pct(rp.max_drawdown_1y), "dd"], ["Eff. names", n(rp.effective_n, 1), "effn"], ["Top 5 weight", pct(rp.top5_weight), "top5"], ["Systematic", pct(S.risk?.factors?.systematic_share), "sys"],
  ].map(([l, v, k]) => `<div class="stat" data-drill="risk:${k}"><span>${l}</span><b>${v}</b></div>`).join("")}</div>
  <div class="cnote">VaR as % of NAV: ${pct((rp.var95_hist || 0) / a.nav)} · parametric VaR ${money(rp.var95_param)}</div></div></div>`;

  const L = S.risk?.factors?.loadings || {}, lmax = Math.max(...Object.values(L).map(Math.abs), 1);
  const factors = `<div class="p"><h3>FACTOR LOADINGS <i>book, daily regression on ETF spreads</i></h3><div class="pb"><table>
    ${Object.entries(L).map(([k, v]) => `<tr data-drill="factor:${esc(k)}"><td style="width:24%">${esc(k)}</td><td><div style="position:relative;height:10px"><div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#444"></div><div style="position:absolute;top:0;height:10px;${v >= 0 ? "left:50%" : "right:50%"};width:${(Math.abs(v) / lmax) * 50}%;background:${v >= 0 ? "var(--blue)" : "var(--amber)"}"></div></div></td><td class="num" style="width:16%">${n(v)}</td></tr>`).join("")}
  </table><div class="cnote">Market = beta to SPY; the rest are tilts (Size = IWM-SPY, Value = IWD-IWF, Momentum = MTUM-SPY, Quality = QUAL-SPY).</div></div></div>`;

  const contrib = [...S.positions].sort((x, y) => (y.risk_share || 0) - (x.risk_share || 0));
  const cmax = Math.max(...contrib.map((p) => Math.max(p.risk_share || 0, p.weight || 0)), 0.01);
  const riskc = `<div class="p"><h3>RISK CONTRIBUTION <i>share of book variance vs weight</i></h3><div class="pb"><table>
    ${contrib.slice(0, 12).map((p) => `<tr data-drill="tkr:${esc(p.ticker)}"><td class="amb" style="width:14%">${esc(p.ticker)}</td><td><div class="bar" style="height:6px"><div style="width:${((p.risk_share || 0) / cmax) * 100}%;background:var(--amber)"></div></div><div class="bar" style="height:4px;margin-top:1px;background:none"><div style="width:${((p.weight || 0) / cmax) * 100}%;background:#4a5a6a"></div></div></td><td class="num" style="width:14%">${pct(p.risk_share)}</td><td class="num dim" style="width:14%">${pct(p.weight)}</td><td class="num ${(p.risk_share || 0) > 1.5 * (p.weight || 0) ? "neg" : "dim"}" style="width:12%">${p.weight ? n((p.risk_share || 0) / p.weight, 1) + "x" : "--"}</td></tr>`).join("")}
  </table><div class="cnote"><span class="amb">&#9632;</span> risk share &nbsp; <span style="color:#4a5a6a">&#9632;</span> weight · red ratio = risk more than 1.5x weight</div></div></div>`;

  const sc = S.risk?.scenarios || [], rep = S.risk?.replay || {};
  const stress = `<div class="p"><h3>STRESS TESTS <i>names regressed on scenario drivers, 2y weekly</i></h3><div class="pb"><table>
    <tr><th>SCENARIO</th><th class="num">P&amp;L</th><th class="num">% NAV</th><th>WORST</th></tr>
    ${sc.map((s, i) => `<tr data-drill="scen:${i}"><td>${esc(s.name)}</td><td class="num ${cls(s.pnl)}">${smoney(s.pnl, 0)}</td><td class="num ${cls(s.pct)}">${spct(s.pct, 1)}</td><td class="dim">${(s.worst || []).slice(0, 2).map((w) => w.ticker).join(", ")}</td></tr>`).join("")}
    ${rep.start ? `<tr data-drill="replay:"><td>Replay: SPY's worst 20d (${esc(rep.start)} to ${esc(rep.end)})</td><td class="num ${cls(rep.pnl)}">${smoney(rep.pnl, 0)}</td><td class="num ${cls(rep.pct)}">${spct(rep.pct, 1)}</td><td class="dim">SPY ${spct(rep.benchmark_move, 1)}</td></tr>` : ""}
  </table></div></div>`;

  const cr = S.risk?.corr || {}, tk = cr.tickers || [], M = cr.matrix || [];
  const heat = (v) => { if (v == null) return "#111"; const t = Math.max(-1, Math.min(1, v)); return t >= 0 ? `rgba(255,77,77,${(0.12 + 0.88 * t).toFixed(2)})` : `rgba(77,163,255,${(0.12 + 0.88 * -t).toFixed(2)})`; };
  const corr = `<div class="p span2"><h3>CORRELATION <i>1y daily · red = moves together, blue = offsets · click a cell</i></h3><div class="pb" style="overflow:auto"><table class="heat">
    <tr><th></th>${tk.map((t) => `<th class="vt">${esc(t)}</th>`).join("")}</tr>
    ${tk.map((t, i) => `<tr><th class="amb">${esc(t)}</th>${tk.map((u, j) => `<td data-drill="corr:${i},${j}" style="background:${i === j ? "#222" : heat(M[i]?.[j])}" title="${esc(t)} / ${esc(u)}: ${n(M[i]?.[j])}">${i === j ? "" : n(M[i]?.[j], 1).replace(/^0/, "").replace(/^-0/, "-")}</td>`).join("")}</tr>`).join("")}
  </table></div></div>`;

  // Deeper tools open as their own windows (type the code in the command bar, or click here)
  const hits = (S.tripwires || []).filter((r) => (r.status || []).some((s) => s.state === "hit")).length;
  const fnP = `<div class="p span2" data-pid="FUNCTIONS"><h3>FUNCTIONS <i>type the code in the command bar, or click</i></h3><div class="pb fnlaunch">
    ${["PERF", "RISK", "EXPO", "ATTR", "TRIP", "SI", "THEME"].map((c) => `<button onclick="openBook('${c}')"><b>${c}</b><span>${esc(BOOKFN[c])}${c === "TRIP" && hits ? ` <i class="amb">${hits} hit</i>` : ""}</span></button>`).join("")}
    <button onclick="openFn('FLNG')"><b>FLNG</b><span>${esc(FN.FLNG.name)}</span></button></div></div>`;
  return `<div class="grid">${perf}${perfChart}${positions}${comp}${fnP}${limits}${stats}${factors}${riskc}${stress}${corr}</div>`;
}
// ---------- ATTRIBUTION ----------
const ATTR = {};
let attrBusy = false;
const attrPer = () => lsGet("attr:per") || "MTD";
async function loadAttr(force = false) {
  const p = attrPer(), hit = ATTR[p];
  if (attrBusy || (!force && hit && Date.now() - hit.t < 10 * 60e3)) return;
  attrBusy = true;
  try { ATTR[p] = { t: Date.now(), d: await (await fetch(`/api/attribution?period=${p}${force ? "&fresh=true" : ""}`)).json() }; }
  catch (e) { ATTR[p] = { t: Date.now(), d: { ok: false, error: e.message } }; }
  attrBusy = false;
  document.querySelectorAll("[data-attr]").forEach((el) => { el.innerHTML = attrBody(); hydrate(el); });
}
function attrSet(p) { lsSet("attr:per", p); document.querySelectorAll("[data-attr]").forEach((el) => { el.innerHTML = attrBody(); }); loadAttr(); }
const pts = (x, d = 2) => (x == null ? "--" : `${x > 0 ? "+" : ""}${n(x * 100, d)}`);
function attrBody() {
  const p = attrPer(), d = ATTR[p]?.d;
  const bar = `<div class="rbar" style="margin-bottom:6px">${["1M", "MTD", "QTD", "3M", "YTD"].map((x) => `<button class="${x === p ? "on" : ""}" onclick="attrSet('${x}')">${x}</button>`).join("")}
    <span style="margin-left:auto"></span><button onclick="loadAttr(true)" title="Rebuild now">REFRESH</button></div>`;
  if (!d) return bar + '<div class="loading">Rebuilding daily holdings from your fills...</div>';
  if (!d.ok) return bar + `<div class="dim">${esc(d.error || "")}</div>`;
  const e = d.effects, mx = Math.max(...Object.values(e).map(Math.abs), Math.abs(d.excess), 1e-6);
  const eff = [["Sector bets (allocation)", e.allocation, "Owning more or less of sectors that beat or lagged SPY"], ["Stock picking (selection)", e.selection, "Your names vs their sector funds"],
    ["Cash", e.cash, `Average ${pct(d.cash_weight, 0)} in cash while SPY moved ${spct(d.spy, 1)}`], ["Other", e.other, "Dividends, cash income, compounding and weights moving inside the period"]];
  const effHtml = `<table>${eff.map(([l, v, why]) => `<tr title="${esc(why)}"><td style="width:40%">${l}</td><td><div style="position:relative;height:10px"><div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#444"></div>
      <div style="position:absolute;top:1px;height:8px;${v >= 0 ? "left:50%" : "right:50%"};width:${(Math.abs(v) / mx) * 50}%;background:${v >= 0 ? "var(--pos)" : "var(--neg)"}"></div></div></td>
      <td class="num ${cls(v)}" style="width:70px">${pts(v)} pts</td></tr>`).join("")}</table>`;
  const nm = (x) => `<tr data-drill="tkr:${esc(x.ticker)}"><td class="amb">${esc(x.ticker)}${x.held_now ? "" : ' <span class="dim small">SOLD</span>'}</td><td class="num ${cls(x.contribution)}">${pts(x.contribution)}</td>
      <td class="num ${cls(x.pnl)}">${smoney(x.pnl, 0)}</td><td class="num dim">${pct(x.avg_weight, 1)}</td></tr>`;
  const top = d.names.filter((x) => x.contribution > 0).slice(0, 5), bot = d.names.filter((x) => x.contribution < 0).slice(-5).reverse();
  const th = '<tr><th>NAME</th><th class="num">CONTRIB pts</th><th class="num">P&amp;L</th><th class="num">AVG WT</th></tr>';
  const grp = (rows) => `<table><tr><th>GROUP</th><th class="num">CONTRIB pts</th><th class="num">RETURN</th><th class="num">AVG WT</th></tr>${rows.map((r) => `<tr><td>${esc(r.group)}</td><td class="num ${cls(r.contribution)}">${pts(r.contribution)}</td><td class="num ${cls(r.ret)}">${r.ret == null ? "--" : spct(r.ret, 1)}</td><td class="num dim">${pct(r.avg_weight, 1)}</td></tr>`).join("")}</table>`;
  const secs = d.sectors.filter((r) => r.w_book > 0.001 || r.w_spy > 0.001).sort((a, b) => (b.allocation + b.selection) - (a.allocation + a.selection));
  const secHtml = `<table><tr><th>SECTOR</th><th class="num">YOU</th><th class="num">SPY</th><th class="num">YOUR RET</th><th class="num">SECTOR RET</th><th class="num">ALLOC</th><th class="num">SELECT</th></tr>
    ${secs.map((r) => `<tr><td>${esc(r.group)}</td><td class="num">${pct(r.w_book, 1)}</td><td class="num dim">${pct(r.w_spy, 1)}</td><td class="num ${cls(r.r_book)}">${r.r_book == null ? "--" : spct(r.r_book, 1)}</td>
      <td class="num ${cls(r.r_bench)}">${spct(r.r_bench, 1)}</td><td class="num ${cls(r.allocation)}">${pts(r.allocation)}</td><td class="num ${cls(r.selection)}">${pts(r.selection)}</td></tr>`).join("")}</table>`;
  return bar + `<div style="margin-bottom:6px">${esc(p)} <span class="dim">(${esc(d.from)} to ${esc(d.to)})</span>: book <b class="${cls(d.book)}">${spct(d.book, 2)}</b> vs SPY <b class="${cls(d.spy)}">${spct(d.spy, 2)}</b>
      = <b class="${cls(d.excess)}">${pts(d.excess)} pts</b></div>${effHtml}
    <div class="attrcols"><div><div class="sec2">TOP CONTRIBUTORS</div><table>${th}${top.map(nm).join("") || '<tr><td class="dim">None.</td></tr>'}</table></div>
      <div><div class="sec2">TOP DETRACTORS</div><table>${th}${bot.map(nm).join("") || '<tr><td class="dim">None.</td></tr>'}</table></div></div>
    <div class="attrcols"><div><div class="sec2">BY BUCKET</div>${grp(d.buckets)}</div><div><div class="sec2">BY SECTOR vs SPY</div>${secHtml}</div></div>
    <div class="cnote">Contribution = each day's P&amp;L on the name / the book's value before that day, summed (points of return). Built from ${d.fills} fills and official closes. ${esc(d.note)}</div>`;
}

// ---------- TRIPWIRES ----------
const TRIP_LAB = { review_at: "REVIEW AT", trim_above: "TRIM ABOVE", add_below: "ADD BELOW" };
function tripCell(r, k) {
  const v = r[k], d = r.distance?.[k], st = (r.status || []).find((s) => s.level === k);
  const col = st ? (st.state === "hit" ? (k === "review_at" ? "var(--neg)" : k === "trim_above" ? "var(--amber)" : "var(--pos)") : "var(--amber)") : "";
  const src = r.source?.[k];
  return `<td class="num" style="cursor:pointer;${col ? "color:" + col : ""}" title="${esc(src ? "From: " + src : "Not set")} · click to edit" onclick="event.stopPropagation();tripEdit('${esc(r.ticker)}','${k}',${v ?? "null"})">
    ${v == null ? '<span class="dim">set</span>' : money(v, 2)}${d != null ? ` <span class="small ${st ? "" : "dim"}">${spct(d, 0)}</span>` : ""}${src === "set by you" ? ' <span class="small amb">*</span>' : ""}</td>`;
}
async function tripEdit(t, k, cur) {
  const v = prompt(`${t} ${TRIP_LAB[k]} price (empty = use the thesis default):`, cur ?? "");
  if (v === null) return;
  const r = await fetch(`/api/tripwires/${encodeURIComponent(t)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [k]: v.trim() || null }) });
  if (!r.ok) return alert((await r.json().catch(() => ({}))).detail || `Server said ${r.status}`);
  await refresh();
  if (typeof WINS !== "undefined" && WINS.TRIP) winPaint("TRIP");
}
function tripBody() {
  const rows = S.tripwires || [];
  if (!rows.length) return '<div class="dim">No holdings with thesis values yet.</div>';
  const hits = rows.filter((r) => (r.status || []).some((s) => s.state === "hit")).length;
  const sorted = [...rows].sort((a, b) => ((b.status || []).length - (a.status || []).length) || ((a.distance?.review_at ?? 9) - (b.distance?.review_at ?? 9)));
  return `<div style="margin-bottom:4px">${hits ? `<b class="amb">${hits}</b> name${hits > 1 ? "s" : ""} at a tripwire (in DO TODAY).` : "No tripwire hit."} <span class="dim">Distance = price vs the level.</span></div>
    <table><tr><th>NAME</th><th class="num">PRICE</th><th class="num">REVIEW AT</th><th class="num">TRIM ABOVE</th><th class="num">ADD BELOW</th><th>STATUS</th></tr>
    ${sorted.map((r) => `<tr data-drill="tkr:${esc(r.ticker)}"><td class="amb">${esc(r.ticker)}${r.has_thesis ? "" : ' <span class="dim small">no thesis</span>'}</td><td class="num">${money(r.price, 2)}</td>
      ${tripCell(r, "review_at")}${tripCell(r, "trim_above")}${tripCell(r, "add_below")}
      <td class="small">${(r.status || []).map((s) => `<span style="color:${s.state === "hit" ? "var(--neg)" : "var(--amber)"}">${TRIP_LAB[s.level].toLowerCase()} ${s.state}</span>`).join(", ")}</td></tr>`).join("")}</table>
    <div class="cnote">Defaults from each thesis: review at the bear value, trim above the probability-weighted value (base value if none). * = set by you (stored in Keel only). A hit lands in DO TODAY; "near" = within 5%.</div>`;
}

// ---------- 13F ----------
const F13 = {};
let f13Busy = false;
const f13Scope = () => lsGet("f13:scope") || "book", f13Tab = () => lsGet("f13:tab") || "NAMES";
async function loadF13(force = false) {
  const s = f13Scope(), hit = F13[s];
  if (f13Busy || (!force && hit && Date.now() - hit.t < 60 * 60e3)) return;
  f13Busy = true;
  try { F13[s] = { t: Date.now(), d: await (await fetch(`/api/13f?scope=${s}${force ? "&fresh=true" : ""}`)).json() }; }
  catch (e) { F13[s] = { t: Date.now(), d: { ok: false, error: e.message } }; }
  f13Busy = false;
  document.querySelectorAll("[data-f13]").forEach((el) => { el.innerHTML = f13Body(); hydrate(el); });
}

async function f13Post(body) {
  const r = await fetch("/api/13f/funds", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) return alert((await r.json().catch(() => ({}))).detail || `Server said ${r.status}`);
  for (const k of Object.keys(F13)) delete F13[k];
  document.querySelectorAll("[data-f13]").forEach((el) => { el.innerHTML = f13Body(); });
  loadF13(true);
}
const f13Remove = (cik) => f13Post({ remove: cik });
const f13Reset = () => { if (confirm("Go back to the fund list in settings.yaml?")) f13Post({ reset: true }); };
SUGGEST_ACT.fund = (inp, sym, x) => {
  if (!x?.files13f) return alert(`${x?.name || "That entity"} does not file 13Fs. Try another entity of the same firm.`);
  inp.value = "";
  f13Post({ name: x.name, cik: x.cik });
};
function f13Set(k, v) { lsSet("f13:" + k, v); document.querySelectorAll("[data-f13]").forEach((el) => { el.innerHTML = f13Body(); }); if (k === "scope") loadF13(); }
const F13_ACT = { new: ["NEW", "var(--pos)"], added: ["ADD", "var(--pos)"], trimmed: ["TRIM", "var(--amber)"], exited: ["EXIT", "var(--neg)"], held: ["HOLD", "#9a9a9a"] };
const actTag = (a, c) => { const [l, col] = F13_ACT[a] || [a || "", "#888"]; return `<span class="dtag" style="color:${col};border-color:${col}">${l}${c != null && (a === "added" || a === "trimmed") ? " " + spct(c, 0) : ""}</span>`; };
function f13Body() {
  const d = F13[f13Scope()]?.d, tab = f13Tab();
  const bar = `<div class="rbar" style="margin-bottom:6px">${[["NAMES", "YOUR NAMES"], ["IDEAS", "THEIR NEW BUYS"], ["FUNDS", "FUNDS"]].map(([k, l]) => `<button class="${tab === k ? "on" : ""}" onclick="f13Set('tab','${k}')">${l}</button>`).join("")}
    <span style="margin-left:auto"></span><button class="${f13Scope() === "book" ? "on" : ""}" onclick="f13Set('scope','book')">HELD</button><button class="${f13Scope() === "all" ? "on" : ""}" onclick="f13Set('scope','all')">+ WATCH &amp; TRACKED</button>
    <button onclick="loadF13(true)">REFRESH</button></div>`;
  if (!d) return bar + '<div class="loading">Reading the funds\' latest 13F filings from SEC (first run takes about a minute)...</div>';
  if (!d.ok) return bar + `<div class="dim">${esc(d.error || "")}</div>`;
  let body;
  if (tab === "IDEAS") {
    body = `<table><tr><th>FUND</th><th>COMPANY</th><th></th><th class="num">% OF FUND</th><th class="num">VALUE</th><th>QUARTER</th></tr>
      ${d.ideas.map((i) => `<tr><td>${esc(i.fund)}</td><td>${esc(i.issuer)}</td><td>${actTag(i.action, i.share_chg)}</td><td class="num">${pct(i.pct_of_fund, 1)}</td><td class="num">${bigUsd(i.value)}</td><td class="dim">${esc(i.quarter)}</td></tr>`).join("") || '<tr><td class="dim">None.</td></tr>'}</table>
      <div class="cnote">New positions of at least 1% of the fund, and adds of 50%+ to positions of at least 2%, outside your names. A starting point for Gate 0, not a reason to buy: 13Fs are 45+ days old.</div>`;
  } else if (tab === "FUNDS") {
    body = `<div style="display:flex;gap:6px;align-items:center;margin-bottom:6px"><input id="f13-add" data-suggest="fund" data-suggest-src="funds" placeholder="+ add a fund by name (e.g. Scion, Greenlight)" style="width:300px">
        <button class="mini" onclick="f13Reset()" title="Back to the list in settings.yaml">Reset list</button></div>
      <table><tr><th>FUND</th><th>QUARTER</th><th>FILED</th><th class="num">POSITIONS</th><th class="num">13F VALUE</th><th></th></tr>
      ${d.funds.map((f) => `<tr style="cursor:pointer" onclick="openFn('FLNG', {cik: ${f.cik}, name: '${esc(f.name).replace(/'/g, "")}'})"><td class="amb">${esc(f.name)}</td><td>${esc(f.quarter)}</td><td class="dim">${esc(f.filed)}</td><td class="num">${f.positions}</td><td class="num">${bigUsd(f.total)}</td>
        <td><button class="mini" title="Stop tracking" onclick="event.stopPropagation();f13Remove('${f.cik}')">×</button></td></tr>`).join("")}</table>
      ${d.unresolved?.length ? `<div class="small neg">Not found on SEC: ${esc(d.unresolved.join(", "))}</div>` : ""}${d.stale?.length ? `<div class="small dim">Left out, no recent 13F: ${esc(d.stale.join(", "))}</div>` : ""}
      <div class="cnote">Click a fund for its full 13F. Type a name to add a fund (only entities that file 13Fs can be tracked). Or open any fund from the command bar: 13F &lt;name&gt;. Your list is saved in Keel.</div>`;
  } else {
    const rows = d.rows.filter((r) => r.holders || r.exited);
    body = rows.length ? `<table><tr><th>NAME</th><th class="num">FUNDS</th><th>LAST QUARTER</th><th>WHO</th></tr>
      ${rows.map((r) => `<tr data-drill="tkr:${esc(r.ticker)}"><td class="amb">${esc(r.ticker)}</td><td class="num">${r.holders} / ${d.funds.length}</td>
        <td class="small">${[["new", r.new, "pos"], ["added", r.added, "pos"], ["trimmed", r.trimmed, "amb"], ["exited", r.exited, "neg"]].filter((x) => x[1]).map(([l, c, k]) => `<span class="${k}">${c} ${l}</span>`).join(" · ") || '<span class="dim">no change</span>'}</td>
        <td style="white-space:normal" class="small">${r.funds.map((h) => `${esc(h.fund.replace(/,? (LLC|L\.?P\.?|LTD|Ltd|LLP|Inc)\.?$/i, ""))} ${actTag(h.action, h.share_chg)}${h.pct_of_fund ? ` <span class="dim">${pct(h.pct_of_fund, 1)}</span>` : ""}`).join(" &nbsp; ")}</td></tr>`).join("")}</table>`
      : '<div class="dim">None of the tracked funds owns your names.</div>';
    body += `<div class="cnote">Count = tracked funds holding it at quarter end; % = share of that fund's 13F book. Many funds in one name is crowding: if they sell together, it falls together. ${esc(d.note)}</div>`;
  }
  return bar + body;
}

// Full 13F for one fund: every position, weight, change vs last quarter, exits
const F13FUND = {};
DRILL.f13fund = (spec) => {
  const [cik, name] = spec.split("|");
  const d = F13FUND[cik];
  if (!d) {
    fetch(`/api/13f/fund/${cik}${name ? "?name=" + encodeURIComponent(name) : ""}`).then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }))
      .then((x) => { F13FUND[cik] = x; if (!$("drill").hidden && drillStack.at(-1) === "f13fund:" + spec) openDrill("f13fund:" + spec); });
    return { title: `13F · ${esc(name || "CIK " + cik)}`, body: '<div class="loading">Reading the latest two 13F filings from SEC...</div>' };
  }
  if (!d.ok) return { title: `13F · ${esc(name || cik)}`, body: `<div class="neg">${esc(d.error || "")}</div>` };
  return { title: `13F · ${esc(d.name)} <span class="dim">${esc(d.quarter)} (filed ${esc(d.filed)})</span>`, body: fundViewHTML(d) };
};
function fundViewHTML(d) {
  const cik = d.cik;
  const mine = new Set([...(S.positions || []).map((p) => p.ticker), ...(S.watchlist || []).map((w) => w.ticker)]);
  const tracked = (F13[f13Scope()]?.d?.funds || []).some((f) => String(f.cik) === String(cik));
  const c = d.counts;
  return `<div style="margin-bottom:4px"><b class="amb">${esc(d.name)}</b> <span class="dim">13F for ${esc(d.quarter)}, filed ${esc(d.filed)} · CIK ${cik}</span></div>${kv([["13F value", bigUsd(d.total)], ["Positions", d.positions], ["Top 10", pct(d.top10, 0)],
                 ["Vs " + esc(d.prev_quarter || "prior"), `<span class="pos">${c.new} new · ${c.added} added</span> · <span class="amb">${c.trimmed} trimmed</span> · <span class="neg">${c.exited} exited</span>`]])}
      <div style="margin:6px 0">${tracked ? '<span class="dim small">Tracked in your 13F panel.</span>' : `<button class="mini" onclick="f13Post({name: ${esc(JSON.stringify(d.name))}, cik: ${d.cik}}).then(() => WINS.FLNG ? winPaint('FLNG') : openDrill('f13fund:${d.cik}|'))">+ Track this fund</button>`}
        <a class="mini" href="${esc(d.url)}" target="_blank" rel="noopener">Filings on SEC</a></div>
      <table class="sortable" data-sortkey="f13fund"><tr><th>#</th><th>TICKER</th><th>COMPANY</th><th class="num">% OF 13F</th><th class="num">VALUE</th><th>CHANGE</th></tr>
      ${d.rows.map((r, i) => `<tr ${r.ticker ? `data-drill="tkr:${esc(r.ticker)}"` : ""}><td class="dim">${i + 1}</td><td class="amb">${esc(r.ticker || "")}${r.ticker && mine.has(r.ticker) ? ' <span class="dtag" style="color:var(--pos);border-color:var(--pos)">YOURS</span>' : ""}</td>
        <td>${esc(r.issuer)}</td><td class="num" data-v="${r.pct ?? ""}">${pct(r.pct, 1)}</td><td class="num" data-v="${r.value ?? ""}">${bigUsd(r.value)}</td><td data-v="${r.action === "new" ? 1e6 : r.action === "exited" ? -1e6 : r.share_chg ?? 0}">${actTag(r.action, r.share_chg)}</td></tr>`).join("")}</table>
      ${d.exits.length ? `<div class="sec2">EXITED SINCE ${esc(d.prev_quarter || "")}</div><div class="small">${d.exits.map((e) => `${e.ticker ? `<b class="amb">${esc(e.ticker)}</b> ` : ""}${esc(e.issuer)} <span class="dim">(${bigUsd(e.prev_value)})</span>`).join(" · ")}</div>` : ""}
      <div class="cnote">Long US positions at quarter end only (13Fs leave out shorts, cash and non-US holdings), filed up to 45 days later. Tickers are matched by company name; options lines are left out.</div>`;
}

// Short interest over time (FINRA, twice a month): sihist:<T>|<pct|si|dtc>
DRILL.sihist = (spec) => {
  const [t, what = "pct"] = spec.split("|");
  const r = (Object.values(CROWD).map((x) => x?.d?.rows || []).flat()).find((x) => x.ticker === t) || {};
  if (!r.ticker && !CROWD[crowdWatch()]) {
    const again = () => { if (!$("drill").hidden && drillStack.at(-1) === "sihist:" + spec) openDrill("sihist:" + spec); };
    if (crowdBusy) setTimeout(again, 1500); else loadCrowd().then(() => { if (CROWD[crowdWatch()]) again(); });
  }
  const sym = { pct: "SIP:", si: "SI:", dtc: "DTC:" }[what] + t;
  const tabs = [["pct", "SHORT % FLOAT"], ["si", "SHARES SHORT"], ["dtc", "DAYS TO COVER"]]
    .map(([k, l]) => `<button class="${k === what ? "on" : ""}" onclick="openDrill('sihist:${esc(t)}|${k}')">${l}</button>`).join("");
  return { title: `SHORT INTEREST · ${esc(t)}`, body: `<div class="rbar" style="margin-bottom:4px">${tabs}</div>
    <div class="dsec">${chartSlot(sym, "drill-si", 220, "1Y")}</div>
    ${kv([["Short % of float", dPct(r.short_pct_float)], ["Change vs prior report", r.short_change == null ? "--" : spct(r.short_change, 0)], ["Days to cover", r.days_to_cover == null ? "--" : n(r.days_to_cover, 1)], ["As of", esc(r.short_as_of || "--")]])}
    <div class="note">FINRA consolidated short interest, reported for mid-month and month-end settlement dates (about two weeks old when published). Short % of float divides by today's float.</div>` };
};
const siCell = (t, k, html, cls2 = "") => `<td class="num ${cls2}" style="cursor:pointer" title="Click for the history" onclick="event.stopPropagation();openDrill('sihist:${esc(t)}|${k}')">${html}</td>`;

// ---------- CROWDING & LIQUIDITY ----------
const CROWD = {};
let crowdBusy = false;
const crowdWatch = () => lsGet("crowd:watch") === "1";
async function loadCrowd(force = false) {
  const w = crowdWatch(), hit = CROWD[w];
  if (crowdBusy || (!force && hit && Date.now() - hit.t < 30 * 60e3)) return;
  crowdBusy = true;
  try { CROWD[w] = { t: Date.now(), d: await (await fetch(`/api/crowding?watch=${w}${force ? "&fresh=true" : ""}`)).json() }; }
  catch (e) { CROWD[w] = { t: Date.now(), d: { ok: false, error: e.message } }; }
  crowdBusy = false;
  document.querySelectorAll("[data-crowd]").forEach((el) => { el.innerHTML = crowdBody(); hydrate(el); });
}
function crowdToggle() { lsSet("crowd:watch", crowdWatch() ? "0" : "1"); document.querySelectorAll("[data-crowd]").forEach((el) => { el.innerHTML = crowdBody(); }); loadCrowd(); }
const bigUsd = (v) => (v == null ? "--" : v >= 1e9 ? "$" + n(v / 1e9, 1) + "B" : v >= 1e6 ? "$" + n(v / 1e6, 0) + "M" : "$" + n(v / 1e3, 0) + "K");
const dayFmt = (d) => (d == null ? "--" : d < 0.01 ? "<0.01" : n(d, d < 1 ? 2 : 1));
function crowdBody() {
  const d = CROWD[crowdWatch()]?.d;
  const bar = `<div class="rbar" style="margin-bottom:6px"><button class="${crowdWatch() ? "" : "on"}" onclick="crowdToggle()">YOUR BOOK</button><button class="${crowdWatch() ? "on" : ""}" onclick="crowdToggle()">+ WATCHLIST</button>
    <span style="margin-left:auto"></span><button onclick="loadCrowd(true)">REFRESH</button></div>`;
  if (!d) return bar + '<div class="loading">Reading short interest and volume...</div>';
  if (!d.ok) return bar + `<div class="dim">${esc(d.error || "")}</div>`;
  const sevc = { high: "var(--neg)", medium: "var(--amber)", low: "#9a9a9a" };
  const rows = d.rows.map((r) => `<tr data-drill="tkr:${esc(r.ticker)}"><td class="amb">${esc(r.ticker)}${r.own === "watch" ? ' <span class="dim small">W</span>' : ""}</td>
      ${r.fund ? '<td class="num"><span class="dim">fund</span></td>' : siCell(r.ticker, "pct", `<u>${dPct(r.short_pct_float)}</u>`)}${r.fund ? '<td class="num">--</td>' : siCell(r.ticker, "si", r.short_change == null ? "--" : spct(r.short_change, 0), r.short_change > 0.2 ? "neg" : r.short_change < -0.2 ? "pos" : "")}
      ${r.fund ? '<td class="num">--</td>' : siCell(r.ticker, "dtc", r.days_to_cover == null ? "--" : `<u>${n(r.days_to_cover, 1)}</u>`)}<td class="num">${r.inst_pct == null ? "--" : pct(Math.min(r.inst_pct, 9.99), 0)}</td>
      <td class="num">${bigUsd(r.adv_usd)}</td><td class="num">${r.own === "held" ? dayFmt(r.days_at?.["100000000"]) : "--"}</td>
      <td class="num">${r.own === "held" ? bigUsd(r.one_day_fund_size) : "--"}</td>
      <td style="white-space:normal">${(r.flags || []).map((f) => `<span class="small" style="color:${sevc[f.severity]}">${esc(f.flag)}</span>`).join("<br>")}</td></tr>`).join("");
  const asof = d.rows.find((r) => r.short_as_of)?.short_as_of;
  return bar + `<div style="margin-bottom:4px">${d.flagged ? `<b class="amb">${d.flagged}</b> held name${d.flagged > 1 ? "s" : ""} flagged.` : "No crowding flags on held names."}
      ${d.book_one_day_size ? ` Your current weights stay sellable within a day up to a fund of about <b>${bigUsd(d.book_one_day_size)}</b> (limited by <b class="amb">${esc(d.binding_name)}</b>).` : ""}</div>
    <table><tr><th>NAME</th><th class="num" title="Short interest as % of the shares that trade">SHORT % FLOAT</th><th class="num" title="Change in shares short vs the prior report">CHG</th>
      <th class="num" title="Shares short / average daily volume">DAYS TO COVER</th><th class="num">INST %</th><th class="num" title="3-month average daily dollar volume">ADV</th>
      <th class="num" title="Days to sell this weight in a $100M fund at ${pct(d.participation, 0)} of daily volume">DAYS AT $100M</th>
      <th class="num" title="Fund size at which this weight takes one full day to sell">1-DAY SIZE</th><th>FLAGS</th></tr>${rows}</table>
    <div class="cnote">Short data as of ${esc(asof || "--")} (exchange reports, twice a month). Liquidity assumes you trade ${pct(d.participation, 0)} of average daily volume. ${esc(d.note)}</div>`;
}

// ---------- THEMES ----------
let THM = null, thmAt = 0, thmBusy = false;
async function loadThemes(force = false) {
  if (thmBusy || (!force && THM && Date.now() - thmAt < 10 * 60e3)) return;
  thmBusy = true;
  try { THM = await (await fetch("/api/themes")).json(); thmAt = Date.now(); } catch (e) { THM = { ok: false, error: e.message }; }
  thmBusy = false;
  document.querySelectorAll("[data-themes]").forEach((el) => { el.innerHTML = themesBody(); hydrate(el); });
}
const THM_READ = { tailwind: ["TAILWIND", "var(--pos)"], headwind: ["HEADWIND", "var(--neg)"], mixed: ["MIXED", "var(--amber)"], "no drivers set": ["--", "#666"] };
const thmSig = (s) => (s > 0 ? '<span class="pos">▲</span>' : s < 0 ? '<span class="neg">▼</span>' : '<span class="dim">·</span>');
function thmVal(x) {
  if (x.value == null) return `<span class="dim">${esc(x.note || "n/a")}</span>`;
  const v = x.unit === "3M return" ? spct(x.value, 1) : n(x.value, Math.abs(x.value) >= 100 ? 0 : 2);
  return `${v}${x.stale ? ' <span class="neg small">STALE</span>' : ""}`;
}
function themesBody() {
  if (!THM) return '<div class="loading">Reading FRED, the alt-data feed and sector ETFs...</div>';
  if (!THM.ok) return `<div class="dim">${esc(THM.error || "")}</div>`;
  return `<table><tr><th>BUCKET</th><th class="num">WEIGHT</th><th>NAMES</th><th>READ</th><th>DRIVERS</th></tr>
    ${THM.rows.map((r, i) => { const [lab, col] = THM_READ[r.read] || THM_READ["no drivers set"];
      return `<tr data-drill="theme:${i}"><td>${esc(r.bucket)}</td><td class="num">${pct(r.weight)}</td><td class="amb small">${esc(r.names.join(" "))}</td>
        <td>${r.drivers.length ? `<span class="dtag" style="color:${col};border-color:${col}">${lab}</span>` : '<span class="dim small">' + (r.bucket === "Unassigned" ? "bucket these in portfolio.md" : "no drivers") + "</span>"}</td>
        <td class="small">${r.drivers.map((x) => `${thmSig(x.signal)} ${esc(x.label)}`).join(" &nbsp; ")}</td></tr>`; }).join("")}</table>
    <div class="cnote">Rule-based: each driver votes ▲ tailwind, ▼ headwind or · no read; stale feeds (over 10 days) show but never vote. Drivers live in config/settings.yaml (themes:).${THM.fred ? "" : ' <span class="neg">No FRED key: FRED drivers are blank.</span>'}</div>`;
}
DRILL.theme = (i) => {
  const r = THM?.rows?.[+i];
  if (!r) return { title: "THEMES", body: "Not loaded." };
  return { title: `THEME · ${esc(r.bucket)} <span class="dim">${pct(r.weight)} of the book</span>`,
    body: `${r.why ? `<div class="note" style="color:var(--txt)">${esc(r.why)}</div>` : ""}
      <table><tr><th></th><th>DRIVER</th><th class="num">LATEST</th><th>CHANGE</th><th>AS OF</th><th>SOURCE</th></tr>
      ${r.drivers.map((x) => { const fid = (/^FRED (\S+)/.exec(x.source || "") || [])[1], etf = (/^(\S+) vs SPY$/.exec(x.source || "") || [])[1];
        const go = fid ? `openDrill('fredx:ECO:${fid}|${esc(x.label).replace(/'/g, "")}', true)` : etf ? `openDrill('tkr:${etf}', true)` : "";
        return `<tr ${go ? `style="cursor:pointer" title="Click for the chart" onclick="${go}"` : ""}><td>${thmSig(x.signal)}</td><td>${go ? "<u>" + esc(x.label) + "</u>" : esc(x.label)}</td><td class="num">${thmVal(x)}</td><td class="small">${esc(x.change_txt || "")}</td><td class="dim">${esc(x.asof || "")}</td><td class="dim small">${esc(x.source || "")}</td></tr>`; }).join("")}</table>
      <div class="sec2">NAMES</div><div>${r.names.map((t) => `<a class="amb" href="#SEC/${encodeURIComponent(t)}" onclick="closeDrill()">${esc(t)}</a>`).join(" · ") || "--"}</div>` };
};

function sleeveName(k) { return { core: "Core equity", hedge: "Hedge sleeve", etf: "Index ETFs", income: "Income (BDC)", event_pod: "Event pod" }[k] || k; }

// ---------- PORT drill-downs ----------
DRILL.perf = (k) => {
  const p = perfRow(k);
  if (!p) return { title: "Performance", body: '<div class="note">Returns are still loading.</div>' };
  const pts = (PERF.points || []).filter((x) => x.date >= p.base_date);
  return { title: `PERFORMANCE · ${k} <span class="dim">from ${p.base_date}</span>`, body: kv([
    ["Book TWR", spct(p.book), cls(p.book)], ["SPY (price)", spct(p.spy), cls(p.spy)], ["Excess", `${(p.excess * 100).toFixed(2)} pts`, cls(p.excess)],
    ["Base date", p.base_date], ...(p.book_ann != null ? [["Book annualized", spct(p.book_ann)], ["SPY annualized", spct(p.spy_ann)]] : []),
  ]) + sec("Valuation points in this window", `<table><tr><th>DATE</th><th class="num">NAV</th><th class="num">SUB-PERIOD</th><th class="num">SPY</th><th class="num">FLOW</th><th>SOURCE</th></tr>
    ${pts.map((x, i) => { const prev = i ? pts[i - 1] : null; const r = prev ? (1 + x.twr) / (1 + prev.twr) - 1 : null, rs = prev ? (1 + x.spy) / (1 + prev.spy) - 1 : null;
      return `<tr><td>${x.date}${x.label ? ` <span class="dim">${esc(x.label)}</span>` : ""}</td><td class="num">${money(x.nav ?? x.value)}</td><td class="num ${cls(r)}">${r == null ? "--" : spct(r)}</td><td class="num ${cls(rs)}">${rs == null ? "--" : spct(rs)}</td><td class="num">${x.flow ? smoney(x.flow) : ""}</td><td class="dim">${esc(x.source)}</td></tr>`; }).reverse().join("")}</table>`)
    + `<div class="note">TWR strips out deposits and withdrawals, so it judges decisions, not timing. SPY is price return (no dividends), which understates the index by about 1.2% a year. Log rows are monthly or flow-cut marks; rebuilt rows are daily closes.</div>` };
};
DRILL.perfcheck = () => {
  const c = PERF?.check || {};
  return { title: "TWR REBUILD CHECK", body: kv([
    ["Anchor row", esc(c.anchor_date)], ["Log NAV", money(c.anchor_value_log)], ["Rebuilt NAV", money(c.anchor_value_rebuilt)], ["Gap", smoney(c.anchor_gap)],
    ["Fills replayed", c.fills], ["Recurring buys (flows)", money(c.recurring_flows)], ["Cash residual", smoney(c.cash_residual)], ["Treated as", c.unexplained_flow ? "deposit/withdrawal" : "income (return)"],
  ]) + `<div class="note">The desk replays every Robinhood fill since the anchor row of performance-log.md. Bank-funded recurring buys count as deposits. Cash left over after the replay up to ${money(c.income_tolerance, 0)} is dividends and lending income; more than that is a transfer the desk cannot date, so it is kept out of today's return.${(c.missing_prices || []).length ? "\nMissing prices: " + c.missing_prices.join(", ") : ""}\n\nThe anchor gap is holdings the order history cannot date (for example a stock reward). The daily chain starts from the rebuilt value, so the gap does not show up as return.</div>` };
};
DRILL.factor = (k) => {
  const rows = [...S.positions].filter((p) => p.factors?.[k] != null).sort((x, y) => y.factors[k] - x.factors[k]);
  return { title: `FACTOR · ${esc(k)} <span class="dim">book ${n(S.risk?.factors?.loadings?.[k])}</span>`, body: sec("Loading by position (weight x loading = contribution)", `<table><tr><th>TKR</th><th class="num">LOADING</th><th class="num">WT</th><th class="num">CONTRIB</th><th class="num">R²</th></tr>
    ${rows.map((p) => `<tr data-drill="tkr:${esc(p.ticker)}"><td class="amb">${esc(p.ticker)}</td><td class="num ${cls(p.factors[k])}">${n(p.factors[k])}</td><td class="num">${pct(p.weight)}</td><td class="num ${cls(p.factors[k])}">${n(p.factors[k] * p.weight, 3)}</td><td class="num dim">${n(p.r2)}</td></tr>`).join("")}</table>`) };
};
DRILL.scen = (i) => {
  const s = (S.risk?.scenarios || [])[+i];
  if (!s) return null;
  return { title: `STRESS · ${esc(s.name)}`, body: kv([["Book P&L", smoney(s.pnl), cls(s.pnl)], ["% of NAV", spct(s.pct), cls(s.pct)], ...Object.entries(s.shocks || {}).map(([k, v]) => [`Shock ${esc(k)}`, spct(v, 0)])])
    + sec("Hardest hit", `<table><tr><th>TKR</th><th class="num">MOVE</th><th class="num">P&amp;L</th></tr>${(s.worst || []).map((w) => `<tr data-drill="tkr:${esc(w.ticker)}"><td class="amb">${esc(w.ticker)}</td><td class="num ${cls(w.move)}">${spct(w.move, 1)}</td><td class="num ${cls(w.pnl)}">${smoney(w.pnl)}</td></tr>`).join("")}</table>`)
    + `<div class="note">Each holding is regressed on the scenario's driver ETFs (2y weekly), then the shocks are applied. Linear, so it misses gap risk and correlation breaks.</div>` };
};
DRILL.replay = () => {
  const r = S.risk?.replay || {};
  return { title: "HISTORICAL REPLAY", body: kv([["Window", `${esc(r.start)} to ${esc(r.end)}`], ["SPY", spct(r.benchmark_move, 1), cls(r.benchmark_move)], ["Book (today's weights)", spct(r.pct, 1), cls(r.pct)], ["Book P&L", smoney(r.pnl), cls(r.pnl)]])
    + `<div class="note">SPY's worst 20 trading days in the price history, with today's holdings run through it.</div>` };
};
DRILL.corr = (ij) => {
  const [i, j] = ij.split(",").map(Number), cr = S.risk?.corr || {}, a = cr.tickers?.[i], b = cr.tickers?.[j];
  if (!a || !b) return null;
  const others = (cr.tickers || []).map((t, k) => [t, cr.matrix[i][k]]).filter(([t]) => t !== a).sort((x, y) => y[1] - x[1]);
  return { title: `CORRELATION · ${esc(a)} / ${esc(b)}  <span class="dim">${n(cr.matrix[i][j])}</span>`, body: kv([["Pair", `${esc(a)} / ${esc(b)}`], ["Correlation (1y daily)", n(cr.matrix[i][j])], [`${esc(a)} weight`, pct(posBy(a)?.weight)], [`${esc(b)} weight`, pct(posBy(b)?.weight)]])
    + sec(`${esc(a)} vs the rest of the book`, `<table>${others.map(([t, v]) => `<tr data-drill="tkr:${esc(t)}"><td class="amb">${esc(t)}</td><td style="width:50%"><div class="bar"><div style="width:${Math.abs(v) * 100}%;background:${v >= 0 ? "var(--neg)" : "var(--blue)"}"></div></div></td><td class="num">${n(v)}</td></tr>`).join("")}</table>`)
    + `<div class="dsec" style="display:flex;gap:6px"><button class="mini" data-drill="tkr:${esc(a)}">Open ${esc(a)}</button><button class="mini" data-drill="tkr:${esc(b)}">Open ${esc(b)}</button></div>` };
};
function groupDrill(kind, key, label, cap) {
  const ps = S.positions.filter((p) => p[kind] === key).sort((x, y) => y.weight - x.weight), w = ps.reduce((s, p) => s + p.weight, 0);
  return { title: `${kind.toUpperCase()} · ${esc(label)}`, body: kv([["Weight", pct(w)], ["Cap", cap ? pct(cap, 0) : "--"], ["Names", ps.length], ["Day P&L", smoney(ps.reduce((s, p) => s + (p.day_pnl || 0), 0))]])
    + sec("Holdings", `<table><tr><th>TKR</th><th class="num">WT</th><th class="num">DAY</th><th class="num">UNREAL</th><th class="num">BETA</th></tr>${ps.map((p) => `<tr data-drill="tkr:${esc(p.ticker)}"><td class="amb">${esc(p.ticker)}</td><td class="num">${pct(p.weight)}</td><td class="num ${cls(p.day_pct)}">${spct(p.day_pct, 1)}</td><td class="num ${cls(p.unreal_pnl)}">${smoney(p.unreal_pnl)}</td><td class="num">${n(p.beta)}</td></tr>`).join("")}</table>`) };
}
DRILL.sleeve = (k) => groupDrill("sleeve", k, sleeveName(k), k === "hedge" ? S.caps.hedge_sleeve_cap : k === "event_pod" ? S.caps.event_pod : null);
DRILL.bucket = (k) => groupDrill("bucket", k, k, null);
