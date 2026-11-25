/* Security windows: the terminal's normal way to look at one security (the pages are your Launchpad).
   Search a security and it opens in its own window, titled with its asset class (Equity, Fund, Index, Curncy,
   Comdty, Crypto, Index·FRED). A function strip under the title lists what fits that class; typing a function
   code (SI, FA, GP ...) runs it in the active window, BACK steps through what you opened, and LAUNCHPAD opens the
   stock's full DES page. "TOST SI" opens TOST's window straight on SI. Several windows can sit side by side. */
"use strict";

// code -> [name, section(s) it loads from /api/equity/<t>/<section>, stock only?]
const SECFN = {
  DES: ["Description", null, false], GP: ["Price chart", null, false], FA: ["Financial analysis", ["fa"], true],
  SI: ["Short interest history", null, true], RV: ["Relative valuation", ["rv"], true], ERN: ["Earnings: implied move vs history", ["ern"], true],
  EE: ["Earnings estimates", ["ee"], true], ANR: ["Analyst recommendations", ["anr"], true], HDS: ["Holders", ["hds"], false],
  INS: ["Insider transactions", ["ins"], true], SPLC: ["Supply chain", ["splc"], true], CN: ["Company news", ["cn"], false],
  CACS: ["Corporate actions", ["cacs"], true], THESIS: ["Your thesis cockpit (Keel)", ["cockpit", "flags"], true],
};
const CLASS_FNS = {
  Equity: ["DES", "GP", "FA", "RV", "ERN", "EE", "ANR", "SI", "HDS", "INS", "SPLC", "CN", "CACS", "THESIS"],
  Fund: ["DES", "GP", "HDS", "CN"], Index: ["DES", "GP"], Curncy: ["DES", "GP"], Comdty: ["DES", "GP"], Crypto: ["DES", "GP"],
  "Index · FRED": ["DES", "GP"],
};
const CLASS_OF = { EQUITY: "Equity", ETF: "Fund", MUTUALFUND: "Fund", INDEX: "Index", CURRENCY: "Curncy", FUTURE: "Comdty", CRYPTOCURRENCY: "Crypto" };
let LOADED = lsGet("sec:loaded") || "";
let secSeq = 0;  // the newest open request wins the front, even if an older one finishes loading later

const secKey = (t) => "SEC:" + t;
/** Does a function fit the loaded security's asset class? (SI fits a stock, not a FRED series or a currency.)
    Unknown class (loaded from the DES page, window not opened yet) counts as a stock. */
function secFits(code, t = LOADED) {
  if (!t || !SECFN[code]) return false;
  const cls = WINS[secKey(t)]?.cls || (isFred(t) ? "Index · FRED" : "Equity");
  return (CLASS_FNS[cls] || []).includes(code);
}
const isFred = (t) => t.startsWith("FRED:");
async function secClass(t) {
  if (isFred(t)) return "Index · FRED";
  const c = await getCard(t).catch(() => null);
  return CLASS_OF[(c?.type || "").toUpperCase()] || (c?.ok ? "Equity" : "Equity");
}

/** Open (or bring forward) a security's window on a function. opt: extra settings for the function (e.g. SI tab). */
async function openSec(t, fn = null, opt = {}) {
  t = t.toUpperCase().startsWith("FRED:") ? "FRED:" + t.slice(5) : t.toUpperCase();
  closeDrill?.();
  const key = secKey(t), seq = ++secSeq;
  let w = WINS[key];
  if (!w) {
    w = makeWin(key, `<b class="amb">${esc(isFred(t) ? t.slice(5) : t)}</b> <span class="seccls dim">…</span> <span class="secname"></span>`, 920, 620,
      `<button data-back title="Back (Backspace)">&lt; BACK</button><button data-launch title="Full DES Launchpad page" hidden>LAUNCHPAD</button>`);
    winFront(key);  // in front at once; the asset-class lookup below can take a moment
    w.el.querySelector(".fnbody").innerHTML = '<div class="loading">Loading...</div>';
    w.t = t; w.hist = []; w.cls = await secClass(t); w.label = opt.label || "";
    w.el.querySelector("[data-back]").onclick = () => secBack(key);
    w.el.querySelector("[data-launch]").onclick = () => { location.hash = "SEC/" + encodeURIComponent(t); };
    w.el.querySelector(".seccls").textContent = w.cls;
    w.el.querySelector("[data-launch]").hidden = w.cls !== "Equity";
    if (!isFred(t)) getCard(t).then((c) => { const s = w.el.querySelector(".secname"); if (s) s.textContent = c?.name || w.label || ""; }).catch(() => {});
    if (isFred(t)) w.el.querySelector(".secname").textContent = w.label || (opt.fx || "").split("|")[1] || "";
  }
  fn = (fn || w.fn || "DES").toUpperCase();
  if (w.fn && (w.fn !== fn || JSON.stringify(w.opt) !== JSON.stringify(opt))) w.hist.push([w.fn, w.opt]);
  w.fn = fn; w.opt = opt;
  if (seq === secSeq) { winFront(key); setLoaded(t); }
  secPaint(key);
}
function secBack(key) { const w = WINS[key]; if (!w?.hist.length) return; [w.fn, w.opt] = w.hist.pop(); secPaint(key); }
/** Run a function on the active security window (the one in front), else on the loaded security. */
function runSecFn(code, opt = {}) {
  const top = Object.keys(WINS).filter((k) => k.startsWith("SEC:")).sort((a, b) => +WINS[b].el.style.zIndex - +WINS[a].el.style.zIndex)[0];
  const t = top ? WINS[top].t : LOADED;
  if (t) openSec(t, code, opt);
}

function secStrip(w) {
  const fns = CLASS_FNS[w.cls] || ["DES", "GP"];
  const keep = isFred(w.t) && w.opt ? esc(JSON.stringify({ fx: w.opt.fx, tf: w.opt.tf, label: w.opt.label })) : "{}";
  return `<div class="secstrip rbar">${fns.map((c) => `<button class="${c === w.fn ? "on" : ""}" title="${esc(SECFN[c][0])}" onclick="openSec('${esc(w.t)}','${c}',${keep})">${c}</button>`).join("")}</div>`;
}

function secPaint(key) {
  const w = WINS[key];
  if (!w) return;
  const body = w.el.querySelector(".fnbody"), t = w.t, fn = w.fn, f = SECFN[fn];
  w.el.querySelector(".fnsub").textContent = `${fn} · ${f ? f[0] : ""}`;
  w.el.querySelector("[data-back]").disabled = !w.hist.length;
  let html;
  if (f && f[2] && w.cls !== "Equity") {
    html = `<div class="note">${fn} is for stocks; ${esc(t)} is ${/^[AEIOU]/.test(w.cls) ? "an" : "a"} ${esc(w.cls)}. Try ${(CLASS_FNS[w.cls] || ["GP"]).join(" or ")}.</div>`;
  } else if (isFred(t)) {
    html = fredBody(w);
  } else if (fn === "DES") {
    const q = (typeof DRILL.tkr === "function" ? DRILL.tkr(t) : null)?.body || `<div data-card="${esc(t)}" data-full="1"></div>`;
    html = q + (w.cls === "Equity" ? `<div class="sec2">PROFILE · KEY STATS</div><div data-weq="des" data-wt="${esc(t)}">${desBody("des", t)}</div>` : "");
    if (w.cls === "Equity") loadEq(t, "des");
  } else if (fn === "GP") {
    html = chartSlot(t, "sw-gp", 360, "1Y");
  } else if (fn === "SI") {
    html = siWinBody(t, w.opt?.what || "pct");
  } else if (fn === "ALT" && typeof altWinBody === "function") {
    html = altWinBody(t);
  } else if (f && f[1]) {
    const shown = f[1][0];
    html = `<div data-weq="${shown}" data-wt="${esc(t)}">${desBody(shown, t)}</div>`;
    for (const s of f[1]) loadEq(t, s);
  } else html = `<div class="dim">Unknown function ${esc(fn)}.</div>`;
  body.innerHTML = secStrip(w) + (w.opt?.act && typeof actBanner === "function" ? actBanner(w.opt.act) : "") + `<div class="secbody">${html}</div>`;
  body.scrollTop = 0;
  hydrate(body);
  fillCards(body);
}
/** A DES section finished loading (or a tab inside it changed): redraw it in every window showing it. */
function secWinPaint(t, s) {
  const shown = s === "flags" ? "cockpit" : s;
  document.querySelectorAll(`.fnwin [data-weq="${shown}"]${t ? `[data-wt="${CSS.escape(t)}"]` : ""}`).forEach((el) => {
    el.innerHTML = desBody(shown, el.dataset.wt);
    hydrate(el);
  });
}

function siWinBody(t, what) {
  const r = (Object.values(CROWD).map((x) => x?.d?.rows || []).flat()).find((x) => x.ticker === t) || {};
  if (!r.ticker && !CROWD[crowdWatch()]) {
    const again = () => { if (WINS[secKey(t)]?.fn === "SI") secPaint(secKey(t)); };
    if (crowdBusy) setTimeout(again, 1500); else loadCrowd().then(() => { if (CROWD[crowdWatch()]) again(); });
  }
  const sym = { pct: "SIP:", si: "SI:", dtc: "DTC:" }[what] + t;
  const tabs = [["pct", "SHORT % FLOAT"], ["si", "SHARES SHORT"], ["dtc", "DAYS TO COVER"]]
    .map(([k, l]) => `<button class="${k === what ? "on" : ""}" onclick="openSec('${esc(t)}','SI',{what:'${k}'})">${l}</button>`).join("");
  return `<div class="rbar" style="margin-bottom:4px">${tabs}</div>${chartSlot(sym, "sw-si", 300, "1Y")}
    ${kv([["Short % of float", dPct(r.short_pct_float)], ["Change vs prior report", r.short_change == null ? "--" : spct(r.short_change, 0)], ["Days to cover", r.days_to_cover == null ? "--" : n(r.days_to_cover, 1)], ["As of", esc(r.short_as_of || "--")]])}
    <div class="note">FINRA consolidated short interest, reported for mid-month and month-end settlement dates (about two weeks old when published). Short % of float divides by today's float.</div>`;
}

function fredBody(w) {
  const fx = w.opt?.fx;
  if (fx) {  // opened from MKTS (curve, money markets, spreads, economy rows): keep that row's facts and scaling
    if (w.fn === "DES") return DRILL.fredx(fx).body;
    const head = fx.split("|")[0], kind = head.slice(0, head.indexOf(":")), id = head.slice(head.indexOf(":") + 1);
    return chartSlot("FREDID:" + id + (kind === "BP" ? "*100" : ""), "sw-fred", 340, kind === "ECO" ? "5Y" : "1Y");
  }
  const id = w.t.slice(5), tf = w.opt?.tf || "level";
  const bar = `<div class="rbar" style="margin-bottom:4px">${[["level", "LEVEL"], ["yoy", "% Y/Y"], ["mom_pct", "% M/M"], ["mom_diff", "CHANGE"]]
    .map(([k, l]) => `<button class="${tf === k ? "on" : ""}" onclick="openSec('${esc(w.t)}','${w.fn}',{tf:'${k}', label: ${esc(JSON.stringify(w.label || ""))}})">${l}</button>`).join("")}</div>`;
  return bar + chartSlot("FREDID:" + id + (tf === "level" ? "" : "~" + tf), "sw-fred", 340, "5Y")
    + `<div class="note">Source: FRED (St. Louis Fed). <a class="amb" href="https://fred.stlouisfed.org/series/${encodeURIComponent(id)}" target="_blank" rel="noopener">Open on FRED</a></div>`;
}

// ---------- the loaded security (header chip) ----------
function setLoaded(t) {
  if (!t || LOADED === t) { renderLoaded(); return; }
  LOADED = t;
  lsSet("sec:loaded", t);
  renderLoaded();
}
async function renderLoaded() {
  const el = document.getElementById("loaded");
  if (!el) return;
  if (!LOADED) { el.innerHTML = ""; return; }
  const t = LOADED, cls = WINS[secKey(t)]?.cls || (await secClass(t));
  if (t !== LOADED) return;  // another security was loaded while this one's class was being looked up
  el.innerHTML = `<span title="Loaded security: function codes you type (DES, GP, FA, SI ...) run on it">${esc(isFred(LOADED) ? LOADED.slice(5) : LOADED)} <i>${esc(cls)}</i></span>`;
  el.onclick = () => openSec(LOADED);
}
document.addEventListener("DOMContentLoaded", renderLoaded);
setTimeout(renderLoaded, 0);
