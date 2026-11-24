/* Function windows, Bloomberg style: type a function code in the command bar (FLNG, ATTR, TRIP, SI, THEME) and
   it opens in its own window. Windows can be dragged by the title bar, resized from the corner, made full screen
   and stacked; Esc closes the window on top. Each function's body reuses the same loaders as before, so data
   refreshes into the window wherever it is. */
"use strict";

const FN = {
  FLNG: { name: "13F fund holdings", desc: "Search any fund that files 13Fs; holdings, changes, funds in your names, their new buys", w: 980, h: 640 },
  ATTR: { name: "Performance attribution", desc: "What drove the return and the gap to SPY: names, buckets, sectors", w: 980, h: 620 },
  TRIP: { name: "Thesis tripwires", desc: "Review at the bear value, trim above the weighted value, add below your level", w: 860, h: 520 },
  SI: { name: "Short interest, crowding and liquidity", desc: "Short % of float, days to cover, ownership, days to exit", w: 1000, h: 560 },
  THEME: { name: "Exposure themes", desc: "Each exposure bucket against the data that drives it", w: 920, h: 560 },
};
// Codes that open a page or an existing window instead (shown in the command bar next to the windows)
// Book functions that live in the PORT window, listed in the command bar like any function
const BOOK_CODES = { PERF: "Performance vs SPY (PORT window)", RISK: "Risk measures (PORT window)", EXPO: "Exposure: sector, bucket, sleeve (PORT window)",
  FACT: "Factor loadings (PORT window)", SCEN: "Stress tests (PORT window)", CORR: "Correlation (PORT window)", NAV: "NAV, cash and P&L (PORT window)" };
const FN_LINKS = {
  IMAP: ["MAP", "Market heat map"], EVTS: ["CAL", "Catalyst calendar"], EVT: ["EVT", "Event-driven feed: spin-offs, tenders, mergers, activists"],
  PORT: ["PORT", "Portfolio: performance, positions, risk"], WEI: ["MKTS", "World markets"], EQS: ["SCRN", "Equity screener"],
};
const WINS = {};        // code -> {el, arg}
let winZ = 100, winN = 0;  // windows stack from 100 up; pop-ups (5000) and suggestion lists (6000+) stay above

/** One window design for everything: title bar (drag), optional extra buttons, full-screen, ESC, resizable body. */
function makeWin(key, headHTML, w, h, extraButtons = "") {
  const el = document.createElement("div");
  el.className = "fnwin";
  const off = (winN++ % 6) * 28;
  Object.assign(el.style, { left: `${Math.max(10, (window.innerWidth - w) / 2 + off)}px`, top: `${Math.max(46, 70 + off)}px`,
    width: `${Math.min(w, window.innerWidth - 20)}px`, height: `${Math.min(h, window.innerHeight - 60)}px` });
  el.innerHTML = `<div class="fnhead">${headHTML}<span class="dim small fnsub"></span>
    <span style="margin-left:auto;display:flex;gap:4px" class="rbar">${extraButtons}<button data-fnfull title="Full window">⛶</button><button data-fnclose title="Close (Esc)">ESC</button></span></div>
    <div class="fnbody"></div>`;
  document.body.appendChild(el);
  WINS[key] = { el, arg: null };
  winDrag(el, key);
  return WINS[key];
}

function openFn(code, arg = null) {
  code = code.toUpperCase();
  if (FN_LINKS[code]) { location.hash = FN_LINKS[code][0]; return; }
  if (["ATTR", "TRIP", "SI", "THEME"].includes(code) && typeof openBook === "function") return openBook(code, arg || {});
  const f = FN[code];
  if (!f) return;
  closeDrill?.();
  const w = WINS[code] || makeWin(code, `<b class="amb">${code}</b> <span>${esc(f.name)}</span>`, f.w, f.h);
  w.arg = arg ?? w.arg;
  winFront(code);
  winPaint(code);
}
function closeFn(code) { const w = WINS[code]; if (w) { w.el.remove(); delete WINS[code]; } if (typeof renderLoaded === "function") renderLoaded(); }
function winFront(code) { const w = WINS[code]; if (w) { w.el.style.zIndex = ++winZ; if (code.startsWith("SEC:") && typeof setLoaded === "function") setLoaded(code.slice(4)); } }
function winTop() { return Object.entries(WINS).sort((a, b) => +b[1].el.style.zIndex - +a[1].el.style.zIndex)[0]?.[0]; }

/** Draw a window's body. Functions whose data lives in the shared state (TRIP) repaint on every refresh. */
function winPaint(code) {
  const w = WINS[code];
  if (!w) return;
  if (code.startsWith("SEC:")) return secPaint(code);
  if (code === "BOOK") return bookPaint();
  if (code === "NEWS") return newsPaint();
  if (code.startsWith("WHY:")) return whyPaint(code);
  if (code.startsWith("PANEL:")) return panelPaint(code);
  const body = w.el.querySelector(".fnbody"), top = body.scrollTop;
  if (code === "ATTR") { body.innerHTML = `<div data-attr>${attrBody()}</div>`; loadAttr(); }
  else if (code === "SI") { body.innerHTML = `<div data-crowd>${crowdBody()}</div>`; loadCrowd(); }
  else if (code === "THEME") { body.innerHTML = `<div data-themes>${themesBody()}</div>`; loadThemes(); }
  else if (code === "TRIP") { body.innerHTML = `<div data-trip>${tripBody()}</div>`; }
  else if (code === "FLNG") { body.innerHTML = flngBody(w.arg); flngAfter(w.arg); }
  body.scrollTop = top;
  hydrate(body);
}
/** After a state refresh: repaint the windows that read it. */
function winRefresh() {
  if (WINS.TRIP) winPaint("TRIP");
  // the PORT window's book views read the shared state: redraw them in place after each refresh
  if (WINS.BOOK && ["NAV", "RISK", "FACT", "SCEN", "CORR", "EXPO", "TRIP"].includes(WINS.BOOK.fn)) bookPaint(true);
  for (const k of Object.keys(WINS)) if (k.startsWith("PANEL:")) panelPaint(k);  // popped-out panels stay live
}

function winDrag(el, code) {
  el.addEventListener("mousedown", () => { if (+el.style.zIndex !== winZ) winFront(code); });
  el.querySelector("[data-fnclose]").onclick = () => closeFn(code);
  el.querySelector("[data-fnfull]").onclick = () => el.classList.toggle("full");
  const h = el.querySelector(".fnhead");
  h.onmousedown = (e) => {
    if (el.classList.contains("full") || e.target.closest("button,input")) return;
    const r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    const move = (ev) => { el.style.left = Math.max(0, ev.clientX - dx) + "px"; el.style.top = Math.max(0, ev.clientY - dy) + "px"; };
    const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    e.preventDefault();
  };
}
// Esc closes the window on top (unless typing in a box, or a pop-up / the FA window is open above it)
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !winTop() || !$("drill").hidden || (typeof faOpen === "function" && faOpen())) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) && document.activeElement.closest(".fnwin")) { document.activeElement.blur(); return; }
  e.stopImmediatePropagation();
  closeFn(winTop());
}, true);

// ---------- FLNG: 13F fund search and holdings ----------
const flngSub = (t) => { const s = WINS.FLNG?.el.querySelector(".fnsub"); if (s) s.textContent = t; };
function flngBody(arg) {
  const search = `<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
      <input id="flng-q" data-suggest="flng" data-suggest-src="funds" placeholder="Search any fund that files 13Fs (name, e.g. Tiger, Scion, Baupost)" style="flex:1;max-width:520px" value="${esc(arg?.q || "")}">
      ${arg?.cik ? '<button class="mini" onclick="openFn(\'FLNG\', {})">OVERVIEW</button>' : ""}</div>`;
  if (!arg?.cik) {
    flngSub("funds you track, in your names and their new buys");
    return search + `<div data-f13>${f13Body()}</div>`;
  }
  const d = F13FUND[arg.cik];
  flngSub(arg.name || "");
  if (!d) return search + '<div class="loading">Reading the latest two 13F filings from SEC...</div>';
  if (!d.ok) return search + `<div class="neg">${esc(d.error || "")}</div>`;
  return search + fundViewHTML(d);
}
function flngAfter(arg) {
  // "FLNG tiger" from the command bar: run the fund search for what was typed
  if (arg?.q && !arg.cik) setTimeout(() => { const i = document.getElementById("flng-q"); if (i) { i.focus(); i.dispatchEvent(new Event("input", { bubbles: true })); } }, 0);
  if (!arg?.cik) { loadF13(); return; }
  if (!F13FUND[arg.cik]) {
    fetch(`/api/13f/fund/${arg.cik}${arg.name ? "?name=" + encodeURIComponent(arg.name) : ""}`).then((r) => r.json()).catch((e) => ({ ok: false, error: e.message }))
      .then((x) => { F13FUND[arg.cik] = x; if (WINS.FLNG?.arg?.cik === arg.cik) winPaint("FLNG"); });
  }
}
SUGGEST_ACT.flng = (inp, sym, x) => { if (x?.cik) openFn("FLNG", { cik: x.cik, name: x.name }); };
