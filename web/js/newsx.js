/* News over a range you choose, and "why did it move?" on price charts.
   CN       LATEST (Yahoo + your digest) or a look-back of 1W to 2Y, or any number of days (Google News by month)
   N / wire the market wire's look-back, 1 day to 1 month (shared by the N reader and the MKTS wire)
   GP       double-click (or right-click) a point on a price chart: the move that day vs SPY, volume, the
            headlines a day either side and SEC filings that day, in a WHY window. Dots mark the biggest moves. */
"use strict";

// ---------- CN look-back ----------
const CN_RANGES = [["LATEST", 0], ["1W", 7], ["1M", 30], ["3M", 90], ["6M", 180], ["1Y", 365], ["2Y", 730]];
const CNR = {};  // "T|days" -> {t, d, busy}
const cnDays = () => Math.max(0, Math.min(730, +(lsGet("cn:days") || 0) || 0));
function cnSetDays(d) {
  lsSet("cn:days", String(Math.max(0, Math.min(730, Math.round(+d) || 0))));
  if (page === "SEC") desPaint("cn");
  secWinPaint(undefined, "cn");
}
async function loadCnRange(t, days) {
  const k = t + "|" + days;
  if (CNR[k]?.busy) return;
  CNR[k] = { ...(CNR[k] || {}), busy: true };
  let d;
  try { const r = await fetch(`/api/cnrange/${encodeURIComponent(t)}?days=${days}`); d = r.ok ? await r.json() : { ok: false, error: `Server said ${r.status}` }; }
  catch (e) { d = { ok: false, error: e.message }; }
  CNR[k] = { t: Date.now(), d };
  if (page === "SEC" && secTicker() === t) desPaint("cn");
  secWinPaint(t, "cn");
}
/** The CN data for the chosen look-back; starts a load when needed (null while loading). */
function cnData(t) {
  const days = cnDays();
  if (!days) return eqGet(t, "cn");
  const hit = CNR[t + "|" + days];
  const stale = hit && !hit.busy && Date.now() - hit.t > (hit.d?.ok ? 20 * 60e3 : 30e3);
  if (!hit || stale) setTimeout(() => loadCnRange(t, days), 0);
  return hit?.d || null;
}
function cnRangeBar() {
  const cur = cnDays(), named = CN_RANGES.some(([, d]) => d === cur);
  return `<div class="rbar" style="margin-bottom:4px;flex-wrap:wrap">${CN_RANGES.map(([lab, d]) => `<button class="${d === cur ? "on" : ""}" onclick="cnSetDays(${d})" title="${d ? `News from the last ${lab}` : "Latest stories from Yahoo plus your digest"}">${lab}</button>`).join("")}
    <input class="cndays" type="number" min="1" max="730" placeholder="days" value="${!named ? cur : ""}" title="Any look-back in days (up to 730), then Enter"
      onkeydown="if(event.key==='Enter'){cnSetDays(this.value)}" style="width:64px;margin-left:6px"></div>`;
}

// ---------- market wire look-back ----------
const NW_DAYS = [["1D", 1], ["2D", 2], ["1W", 7], ["2W", 14], ["1M", 30]];
const nwDays = () => Math.max(1, Math.min(30, +(lsGet("news:days") || 2) || 2));
function setNewsDays(d) {
  lsSet("news:days", String(d));
  NW = null; nwAt = 0;
  if (WINS.NEWS) newsPaint();
  const el = document.querySelector("[data-newsbody]");
  if (el) el.innerHTML = newsBody();
  const go = () => (nwBusy ? setTimeout(go, 400) : loadNews().then(() => { if (WINS.NEWS) newsPaint(); }));
  go();
}
const nwDaysBar = () => NW_DAYS.map(([lab, d]) => `<button class="${d === nwDays() ? "on" : ""}" onclick="setNewsDays(${d})" title="Stories from the last ${lab}">${lab}</button>`).join("");
/** Clock time for today's stories, the date for older ones. */
function newsWhen(iso) {
  if (!iso) return "--";
  const d = new Date(iso), et = { timeZone: "America/New_York" };
  const today = new Date().toLocaleDateString("en-CA", et) === d.toLocaleDateString("en-CA", et);
  return today ? d.toLocaleTimeString("en-US", { ...et, hour: "2-digit", minute: "2-digit", hour12: false })
    : d.toLocaleDateString("en-US", { ...et, month: "short", day: "numeric" });
}

// ---------- WHY: what moved the price at a chart point ----------
const canWhy = (sym) => sym && sym !== "BOOK" && !/^(FRED|SI:|SIP:|DTC:)/.test(sym);
const etDay = (iso) => new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
const addDays = (ymd, k) => { const d = new Date(ymd + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + k); return d.toISOString().slice(0, 10); };
/** The bar's own window: previous point's day + 1 -> this point's day (a weekly bar covers its week). */
function whyWindow(pts, i) {
  const end = etDay(pts[i][0]);
  if (i === 0) return [end, end];
  const prev = etDay(pts[i - 1][0]);
  return prev >= end ? [end, end] : [addDays(prev, 1), end];
}
const WHY = {};  // "T|start|end" -> {busy, d}
function openWhy(sym, start, end, key) {
  const wk = "WHY:" + sym;
  const w = WINS[wk] || makeWin(wk, `<b class="amb">WHY</b> <span>${esc(sym)} · what moved it</span>`, 760, 600);
  w.why = { sym, start, end, key, base: w.why?.sym === sym && w.why?.start === start && w.why?.end === end ? w.why.base : [start, end] };
  winFront(wk);
  whyPaint(wk);
}
async function loadWhy(sym, start, end) {
  const k = `${sym}|${start}|${end}`;
  if (WHY[k]) return;
  WHY[k] = { busy: true };
  let d;
  try { const r = await fetch(`/api/why/${encodeURIComponent(sym)}?start=${start}&end=${end}`); d = r.ok ? await r.json() : { ok: false, error: `Server said ${r.status}` }; }
  catch (e) { d = { ok: false, error: e.message }; }
  WHY[k] = { d, t: Date.now() };
  if (d.errors?.length) setTimeout(() => { if (WHY[k]?.d === d) delete WHY[k]; }, 60e3);  // partial: ask again later
  const wk = "WHY:" + sym;
  if (WINS[wk]?.why && `${sym}|${WINS[wk].why.start}|${WINS[wk].why.end}` === k) whyPaint(wk);
}
function whyWiden(wk, days) {
  const w = WINS[wk];
  if (!w) return;
  const [s, e] = w.why.base;
  w.why.start = addDays(s, -days); w.why.end = days ? addDays(e, days) : e;
  if (days && w.why.end > etDay(new Date().toISOString())) w.why.end = etDay(new Date().toISOString());
  whyPaint(wk);
}
const fmtDay = (ymd) => new Date(ymd + "T12:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric", year: "numeric" });
function whyPaint(wk) {
  const w = WINS[wk];
  if (!w?.why) return;
  const { sym, start, end, key, base } = w.why, k = `${sym}|${start}|${end}`;
  const body = w.el.querySelector(".fnbody");
  w.el.querySelector(".fnsub").textContent = start === end ? fmtDay(end) : `${fmtDay(start)} to ${fmtDay(end)}`;
  const widen = `<div class="rbar" style="margin-bottom:6px">${[["THIS BAR", 0], ["±3 DAYS", 3], ["±1 WEEK", 7]].map(([lab, d]) => {
    const on = (d === 0 && start === base[0] && end === base[1]) || (d && start === addDays(base[0], -d));
    return `<button class="${on ? "on" : ""}" onclick="whyWiden('${esc(wk)}', ${d})">${lab}</button>`; }).join("")}
    <span class="dim small" style="margin-left:auto">Double-click any point on a price chart to look at that day</span></div>`;
  const hit = WHY[k];
  if (!hit) setTimeout(() => loadWhy(sym, start, end), 0);
  if (!hit?.d) { body.innerHTML = widen + '<div class="loading">Reading the news and SEC filings for those days...</div>'; return; }
  const d = hit.d;
  if (!d.ok) { body.innerHTML = widen + `<div class="neg">${esc(d.error || "Could not load")}</div>`; return; }
  const m = d.move || {};
  const mv = m.ret != null ? `<div class="whymove"><b class="${cls(m.ret)}" style="font-size:18px">${spct(m.ret, 1)}</b>
      <span class="dim">close ${n(m.p0)} (${esc(m.from)}) → ${n(m.p1)} (${esc(m.to)})</span>
      ${m.spy != null ? `<span>SPY <b class="${cls(m.spy)}">${spct(m.spy, 1)}</b></span><span>vs market <b class="${cls(m.excess)}">${m.excess > 0 ? "+" : ""}${n(m.excess * 100, 1)} pts</b></span>` : ""}
      ${m.vol_x != null ? `<span title="Highest volume in the window vs the 20 days before">volume <b class="${m.vol_x >= 2 ? "amb" : ""}">${n(m.vol_x, 1)}x</b> avg</span>` : ""}</div>` : '<div class="dim small">No price data for those days.</div>';
  const need = ["cacs", "anr"].filter((s) => !EQ[sym + "|" + s]);
  if (need.length && !w.why.marksAsked) { w.why.marksAsked = true; Promise.all(need.map((s) => loadEq(sym, s))).then(() => whyPaint(wk)); }
  const marks = marksFor(sym).gp.filter((x) => { const dd = String(x.date).slice(0, 10); return dd >= addDays(start, -1) && dd <= end; });
  const markHTML = marks.length ? `<div class="sec2">ON THE CHART</div>${marks.map((x) => `<div class="small">${esc(String(x.date).slice(0, 10))} · ${esc(x.label)}</div>`).join("")}` : "";
  const sevc = { high: "var(--neg)", medium: "var(--amber)", low: "#9a9a9a" };
  const fil = (d.filings || []).length ? `<div class="sec2">SEC FILINGS</div><table>${d.filings.map((f) => `<tr><td class="dim small" style="width:80px">${esc(f.date)}</td>
      <td style="width:56px"><span class="dtag" style="color:${sevc[f.severity] || "#888"};border-color:${sevc[f.severity] || "#888"}">${esc(f.form)}</span></td>
      <td style="white-space:normal"><a href="${esc(safeUrl(f.url) || "#")}" target="_blank" rel="noopener" style="color:inherit">${esc(f.title)}</a></td></tr>`).join("")}</table>` : "";
  const inWin = (s) => { const dd = (s.time || "").slice(0, 10); return dd >= start && dd <= end; };
  const heads = (d.news || []).length ? `<div class="sec2">HEADLINES <span class="dim">(${esc(d.window[0])} to ${esc(d.window[1])})</span></div><table class="cntbl">${d.news.map((s) => {
      const u = safeUrl(s.url);
      return `<tr class="${inWin(s) ? "" : "dimrow"}"><td class="dim small" style="width:62px">${esc(s.time ? new Date(s.time).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "")}</td>
        <td class="dim small" style="width:110px;white-space:normal">${esc(s.source || "")}</td>
        <td style="white-space:normal">${u ? `<a href="${esc(u)}" target="_blank" rel="noopener" style="color:inherit">${esc(s.title)}</a>` : esc(s.title)}</td></tr>`; }).join("")}</table>`
    : '<div class="dim" style="margin-top:8px">No headlines naming the company in those days. Try ±3 DAYS.</div>';
  body.innerHTML = widen + mv + markHTML + fil + heads +
    `<div class="cnote">Headlines from Google News naming the company or ticker, dated a day either side; filings from SEC EDGAR (insider Form 4s left out). Headlines dated outside the window are dimmed.${d.errors?.length ? " Some sources failed: " + esc(d.errors.join("; ")) : ""}</div>`;
}

/** Big-move dots: daily-or-longer bars whose move is far above the range's typical move (up to 6). */
function bigMoves(vals) {
  const r = vals.map((v, i) => (i && vals[i - 1] ? v / vals[i - 1] - 1 : 0));
  const abs = r.slice(1).map(Math.abs).sort((a, b) => a - b);
  if (abs.length < 15) return [];
  const med = abs[Math.floor(abs.length / 2)] || 0, cut = Math.max(3 * med, 0.03);
  return r.map((x, i) => [i, x]).filter(([i, x]) => i && Math.abs(x) >= cut).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6);
}
/** Hooked from drawSeries: double-click / right-click opens WHY; dots on the biggest moves. */
function whyHook(el, svg, pts, vals, xs, Y, nearest, w, clear) {
  const sym = el.dataset.sym, rng = el.dataset.rng;
  if (!canWhy(sym)) return;
  const open = (e) => {
    e.preventDefault();
    clear?.();
    const bb = svg.getBoundingClientRect(), i = nearest(((e.clientX - bb.left) / bb.width) * w);
    const [s, en] = whyWindow(pts, i);
    openWhy(sym, s, en, el.dataset.key);
  };
  svg.ondblclick = open;
  svg.oncontextmenu = open;
  if (rng === "1D" || rng === "5D") return;
  svg.insertAdjacentHTML("beforeend", bigMoves(vals).map(([i, x]) =>
    `<circle cx="${xs[i]}" cy="${Y(vals[i])}" r="3" fill="none" stroke="${x > 0 ? "var(--pos)" : "var(--neg)"}" stroke-width="1.2" vector-effect="non-scaling-stroke"><title>${esc(etDay(pts[i][0]))} ${spct(x, 1)} · double-click for why</title></circle>`).join(""));
}
