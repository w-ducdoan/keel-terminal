/* Three more things that now open as windows, in the same design as security and PORT windows:
   N       the news reader: headlines on the left, the story on the right; filters for your names, WSJ, topics
   cards   a DO TODAY card opens the window that answers it (ERN, THESIS, GP, CN, DES, or PORT / EXPO), with the
           card and its buttons as a banner on top
   panels  double-click any Launchpad panel title to pop it out as a live window (on DES, the stock's window) */
"use strict";

// ---------- N: news reader ----------
const NEWS_KEY = "NEWS";
function openNews(opt = {}) {
  closeDrill?.();
  const w = WINS[NEWS_KEY] || makeWin(NEWS_KEY, `<b class="amb">N</b> <span>News</span>`, 1040, 640);
  w.opt = { ...(w.opt || {}), ...opt };
  winFront(NEWS_KEY);
  newsPaint();
  if (!NW || Date.now() - nwAt > 120e3) loadNews().then(() => { if (WINS[NEWS_KEY]) newsPaint(); });
}
function newsPaint() {
  const w = WINS[NEWS_KEY];
  if (!w) return;
  const body = w.el.querySelector(".fnbody"), o = w.opt || {}, f = o.f || "ALL", q = (o.q || "").toLowerCase();
  if (!NW) { body.innerHTML = '<div class="loading">Loading the wire...</div>'; return; }
  if (!NW.ok) { body.innerHTML = `<div class="neg">${esc(NW.error || "News unavailable")}</div>`; return; }
  const items = (NW.items || []).map((s, i) => ({ s, i }));
  const isWsj = (s) => s.sources.some((x) => /^WSJ\b|Wall Street Journal/i.test(x));
  const shown = items.filter(({ s }) => (f === "ALL" || (f === "BOOK" ? s.book.length : f === "WSJ" ? isWsj(s) : s.topics.includes(f)))
    && (!q || (s.title + " " + (s.summary || "") + " " + s.book.join(" ")).toLowerCase().includes(q)));
  const sel = o.i != null ? +o.i : shown[0]?.i;
  w.el.querySelector(".fnsub").textContent = `${shown.length} stories`;
  const filt = ["ALL", "BOOK", "WSJ", ...(NW.topics || [])];
  const list = shown.map(({ s, i }) => `<div class="nwrow ${i === sel ? "on" : ""}" onclick="openNews({i:${i}})">
      <span class="dim small">${esc(newsWhen(s.time))}</span>
      ${s.book.length ? `<b class="nbook">${esc(s.book.join(" "))}</b> ` : ""}${esc(s.title)} <span class="dim small">${esc(s.sources.slice(0, 2).join(", "))}</span></div>`).join("")
    || '<div class="dim">Nothing matches.</div>';
  const story = sel != null && DRILL.wire(sel) ? DRILL.wire(sel).body : '<div class="dim">Pick a story.</div>';
  body.innerHTML = `<div class="rbar" style="flex-wrap:wrap;margin-bottom:6px">${filt.map((x) => `<button class="${x === f ? "on" : ""}" onclick="openNews({f:'${esc(x)}', i:null})">${esc(x === "BOOK" ? "YOUR NAMES" : x)}</button>`).join("")}
      <span style="margin-left:auto"></span>${nwDaysBar()}
      <input class="nwq" placeholder="search headlines" value="${esc(o.q || "")}" onkeydown="if(event.key==='Enter'){openNews({q:this.value, i:null})}" style="margin-left:8px;width:180px">
      <button onclick="loadNews(true).then(newsPaint)">REFRESH</button></div>
    <div class="nwsplit"><div class="nwlist">${list}</div><div class="nwstory">${story}</div></div>`;
  hydrate(body);
}

// ---------- DO TODAY cards -> the window that answers them ----------
const ACT_FN = { earnings: "ERN", trip_review: "THESIS", trip_trim: "THESIS", trip_add: "THESIS", drawdown: "GP", drawdown_high: "GP",
  day_move: "GP", five_day_drop: "GP", buy_below: "DES", cap_name: "DES", beta: "DES", event_entry: "DES", recon: "DES", digest: "CN" };
function actRoute(id) {
  const x = (S.actions || []).find((y) => y.id === id);
  if (!x) return false;
  if (x.ticker) {
    const fn = x.type.startsWith("sec_") ? "CN" : x.type.startsWith("alt_") ? "ALT" : ACT_FN[x.type] || "DES";
    openSec(x.ticker, fn, { act: id });
    return true;
  }
  const sector = (/^cap_sector_(.+)$/.exec(x.type) || [])[1];
  if (sector) { openBook("EXPO", { kind: "sector", k: sector, act: id }); return true; }
  if (x.type === "cap_cd") { openBook("EXPO", { kind: "sector", k: "Consumer Discretionary", act: id }); return true; }
  if (x.type === "hedge_sleeve") { openBook("EXPO", { kind: "sleeve", k: "hedge", act: id }); return true; }
  if (x.type === "event_pod_cap") { openBook("EXPO", { kind: "sleeve", k: "event_pod", act: id }); return true; }
  return false;  // anything else keeps its card pop-up
}
/** The card on top of the window it opened: title, detail and its Done / Snooze buttons. */
function actBanner(id) {
  const x = (S.actions || []).find((y) => y.id === id);
  if (!x) return "";
  const col = { act: "var(--neg)", review: "var(--amber)", watch: "var(--blue)" }[x.severity] || "#888";
  return `<div class="actbanner" style="border-color:${col}"><span class="dtag" style="color:${col};border-color:${col}">${esc(x.severity.toUpperCase())}</span>
    <b>${esc(x.title)}</b>${x.detail ? `<div class="small">${esc(x.detail)}</div>` : ""}<div style="display:flex;gap:6px;margin-top:4px">${actButtons(x)}</div></div>`;
}

// ---------- pop a Launchpad panel out into a live window ----------
const DES_SEC_FN = { cockpit: "THESIS", des: "DES", anr: "ANR", cacs: "CACS", rv: "RV", hds: "HDS", cn: "CN", ee: "EE", ins: "INS", ern: "ERN", gp: "GP", pos: "DES" };
const panelTitle = (p) => { const h = p.querySelector(":scope > h3"); return (h?.childNodes[0]?.textContent || h?.textContent || "").trim(); };
function popOut(p) {
  if (page === "SEC") {
    const code = DES_SEC_FN[p.dataset.sec];
    if (code) openSec(secTicker(), code);
    return;  // FA and SPLC open their own full windows on double-click
  }
  const title = panelTitle(p);
  if (!title) return;
  const key = `PANEL:${page}:${title}`;
  const w = WINS[key] || makeWin(key, `<b class="amb">${esc(PAGES[page]?.label || page)}</b> <span>${esc(title)}</span>`, 900, 560);
  w.page = page; w.title = title;
  winFront(key);
  panelPaint(key);
}
/** Re-render the page off screen and lift that panel's body: the window stays live with every refresh. */
function panelPaint(key) {
  const w = WINS[key];
  if (!w || !S?.ready) return;
  const body = w.el.querySelector(".fnbody"), top = body.scrollTop, tmp = document.createElement("div");
  try { tmp.innerHTML = PAGES[w.page].render(); } catch (e) { body.innerHTML = `<div class="neg">${esc(e.message)}</div>`; return; }
  const p = [...tmp.querySelectorAll(".p")].find((x) => panelTitle(x) === w.title);
  body.innerHTML = p ? (p.querySelector(".pb, .mbody")?.innerHTML ?? p.innerHTML) : '<div class="dim">This panel is not on the page right now.</div>';
  body.scrollTop = top;
  hydrate(body);
  fillCards(body);
}
document.addEventListener("dblclick", (e) => {
  const h = e.target.closest(".lp > h3, .mp > h3");
  if (!h || e.target.closest("button, .pclose, input")) return;
  popOut(h.parentElement);
});
