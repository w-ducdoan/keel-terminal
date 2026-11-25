/* Top bar extras and table tools:
   TAPE   a scrolling ticker of indices, rates, FX, commodities and crypto in place of the NAV / P&L strip (toggle)
   LIGHT  light or dark theme (dark is the default; remembered per browser)
   sort   click any column header of a table marked .sortable (HDS, 13F holdings...) to sort it; click again to flip
   HDS    click a holder: an institution opens its 13F in FLNG, a fund opens its own security window */
"use strict";

// ---------- theme ----------
function themeApply() {
  const light = lsGet("theme") === "light";
  document.documentElement.classList.toggle("light", light);
  const b = $("themebtn");
  if (b) { b.textContent = light ? "DARK" : "LIGHT"; b.title = light ? "Switch to the dark theme" : "Switch to the light theme"; }
}
function themeToggle() { lsSet("theme", lsGet("theme") === "light" ? "dark" : "light"); themeApply(); }

// ---------- ticker tape ----------
const TAPE_SYMS = ["^GSPC", "^DJI", "^IXIC", "^RUT", "^VIX", "^STOXX50E", "^FTSE", "^GDAXI", "^N225", "^HSI", "^TNX", "DX-Y.NYB",
  "EURUSD=X", "JPY=X", "CL=F", "BZ=F", "NG=F", "GC=F", "SI=F", "HG=F", "BTC-USD"];
let TAPE = null, tapeAt = 0, tapeBusy = false;
const tapeOn = () => lsGet("tape") === "1";
async function tapeLoad() {
  if (tapeBusy) return;
  tapeBusy = true;
  try { const d = await (await fetch("/api/markets")).json(); TAPE = { rows: d.quotes?.rows || {}, labels: d.labels || {} }; tapeAt = Date.now(); }
  catch { /* keep the last copy */ }
  tapeBusy = false;
  tapePaint();
}
const tapeVal = (sym, q) => (sym === "^TNX" ? n(q.last, 2) + "%" : n(q.last, q.last >= 1000 ? 0 : 2));
function tapeItem(sym) {
  const q = TAPE.rows[sym] || {}, lab = (TAPE.labels[sym] || sym).replace(" Comp", "");
  return `<span class="ti" data-drill="mkt:${esc(sym)}" data-tsym="${esc(sym)}"><i>${esc(lab)}</i> <b>${q.last != null ? tapeVal(sym, q) : "--"}</b> <em class="${cls(q.chg_pct)}">${q.chg_pct != null ? spct(q.chg_pct, 2) : ""}</em></span>`;
}
function tapePaint() {
  const on = tapeOn(), tape = $("tape"), k = $("kpis"), b = $("tapebtn");
  if (b) b.classList.toggle("on", on);
  if (!tape) return;
  tape.hidden = !on;
  if (k) k.hidden = on;
  if (!on) return;
  if (!TAPE) { tape.innerHTML = '<span class="dim small">loading the tape...</span>'; if (!tapeBusy) tapeLoad(); return; }
  const syms = TAPE_SYMS.filter((s) => TAPE.rows[s]);
  const track = tape.querySelector(".track");
  if (track && track.dataset.n === String(syms.length)) {  // update the numbers in place, so the scroll never jumps
    for (const el of track.querySelectorAll("[data-tsym]")) {
      const q = TAPE.rows[el.dataset.tsym] || {};
      el.querySelector("b").textContent = q.last != null ? tapeVal(el.dataset.tsym, q) : "--";
      const em = el.querySelector("em");
      em.textContent = q.chg_pct != null ? spct(q.chg_pct, 2) : ""; em.className = cls(q.chg_pct);
    }
    return;
  }
  const one = syms.map(tapeItem).join("");
  tape.innerHTML = `<div class="track" data-n="${syms.length}">${one}${one}</div>`;
  const tr = tape.querySelector(".track");
  requestAnimationFrame(() => { tr.style.animationDuration = Math.max(30, tr.scrollWidth / 2 / 55) + "s"; });  // about 55 px a second
}
function tapeToggle() { lsSet("tape", tapeOn() ? "0" : "1"); tapePaint(); }
setInterval(() => { if (tapeOn()) tapeLoad(); }, 60e3);

// ---------- sortable tables ----------
const SORTS = {};  // table key -> {col, dir}
function sortVal(td) {
  if (!td) return null;
  if (td.dataset.v !== undefined && td.dataset.v !== "") return +td.dataset.v;
  const t = td.textContent.trim();
  if (!t || t === "--") return null;
  if (/^\d{4}-\d\d-\d\d/.test(t)) return Date.parse(t.slice(0, 10));
  const m = t.replace(/[,$%x+]/g, "").match(/^(-?\d*\.?\d+)\s*([KMBT])?$/i);
  if (m) return +m[1] * ({ K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[(m[2] || "").toUpperCase()] || 1);
  return t.toLowerCase();
}
function sortTable(tb, col, dir) {
  const rows = [...tb.querySelectorAll("tr")].filter((r) => !r.querySelector("th") && r.parentElement.closest("table") === tb);
  if (!rows.length) return;
  const parent = rows[0].parentElement;
  const keyed = rows.map((r, i) => ({ r, i, v: sortVal(r.children[col]) }));
  keyed.sort((a, b) => {
    if (a.v == null && b.v == null) return a.i - b.i;
    if (a.v == null) return 1;
    if (b.v == null) return -1;
    if (typeof a.v === "number" && typeof b.v === "number") return (a.v - b.v) * dir || a.i - b.i;
    return String(a.v).localeCompare(String(b.v)) * dir || a.i - b.i;
  });
  for (const k of keyed) parent.appendChild(k.r);
  tb.querySelectorAll("th").forEach((th, i) => th.classList.toggle("sorted", i === col));
  tb.querySelectorAll("th .sarr").forEach((x) => x.remove());
  const th = tb.querySelectorAll("tr:first-child th")[col];
  if (th) th.insertAdjacentHTML("beforeend", `<span class="sarr">${dir > 0 ? " ▲" : " ▼"}</span>`);
  tb.dataset.sorted = `${col}|${dir}`;
}
document.addEventListener("click", (e) => {
  const th = e.target.closest("table.sortable th");
  if (!th) return;
  e.stopPropagation();
  const tb = th.closest("table"), col = [...th.parentElement.children].indexOf(th), key = tb.dataset.sortkey || "t";
  const first = [...tb.querySelectorAll("tr")].find((r) => !r.querySelector("th"));
  const numeric = typeof sortVal(first?.children[col]) === "number";
  const cur = SORTS[key];
  const dir = cur && cur.col === col ? -cur.dir : numeric ? -1 : 1;  // numbers start biggest first, text A to Z
  SORTS[key] = { col, dir };
  sortTable(tb, col, dir);
}, true);
// Keep a chosen sort when a panel or window redraws.
let sortQueued = false;
new MutationObserver(() => {
  if (sortQueued) return;
  sortQueued = true;
  requestAnimationFrame(() => {
    sortQueued = false;
    document.querySelectorAll("table.sortable[data-sortkey]").forEach((tb) => {
      const s = SORTS[tb.dataset.sortkey];
      if (s && tb.dataset.sorted !== `${s.col}|${s.dir}`) sortTable(tb, s.col, s.dir);
    });
  });
}).observe(document.body, { childList: true, subtree: true });

// ---------- HDS: click a holder ----------
const holderClean = (s) => s.replace(/[,.]?\s+(inc|llc|lp|l\.p|ltd|corp|co|company|plc|n\.a|na|group|the)\.?$/gi, "").replace(/[,.]+$/, "").trim();
async function openHolder(name, kind) {
  if (kind === "inst") {
    const q = holderClean(holderClean(name));
    try {
      const d = await (await fetch(`/api/13f/funds/search?q=${encodeURIComponent(q)}`)).json();
      const hit = (d.results || []).find((r) => r.files13f) || null;
      if (hit) return openFn("FLNG", { cik: hit.cik, name: hit.name });
    } catch { /* fall through to the search box */ }
    return openFn("FLNG", { q: q.toLowerCase() });
  }
  // A fund: "VANGUARD STAR FUNDS-Vanguard Total International Stock Index Fund" -> look up the share class
  // The part before the dash is the fund family ("Principal Funds, Inc"): search with it, and only accept that family,
  // so "MidCap Fund" never lands on another company's mid-cap fund.
  const cut = name.indexOf("-"), head = cut > 0 ? name.slice(0, cut) : "", tail = cut > 0 ? name.slice(cut + 1).trim() : name;
  const family = head.replace(/[,.]/g, " ").split(/\s+/).filter((w) => w && !/^(funds?|fd|trust|inc|series|star|etf|investments?|portfolios?|co|the)$/i.test(w)).slice(0, 2).join(" ");
  const short = tail.replace(/\b(index )?fund\b|\bportfolio\b/gi, "").replace(/\s+/g, " ").trim();
  const fam1 = (family.split(" ")[0] || "").toLowerCase();
  const named = fam1 && tail.toLowerCase().startsWith(fam1);  // "Dodge & Cox Funds-Dodge & Cox International Stock Fund"
  const tries = family && !named ? [`${family} ${tail}`, `${family} ${short}`, tail] : [tail, short, tail.split(/\s+/).slice(0, 4).join(" ")];
  for (const q of [...new Set(tries)].filter((x) => x.length >= 3)) {
    try {
      const d = await (await fetch(`/api/lookup?q=${encodeURIComponent(q)}`)).json();
      const hit = (d.results || []).find((r) => ["MUTUALFUND", "ETF"].includes(r.type) && (!fam1 || (r.name || "").toLowerCase().includes(fam1)));
      if (hit) return openSec(hit.symbol, "DES");
    } catch { /* try the next form of the name */ }
  }
  alert(`Couldn't find "${tail}"${family ? ` (${family})` : ""} on Yahoo, so nothing was opened rather than guessing.`);
}
document.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-holder]");
  if (!tr || e.target.closest("a, button")) return;
  openHolder(tr.dataset.holder, tr.dataset.hk);
});

// ---------- wire up the header ----------
$("themebtn")?.addEventListener("click", themeToggle);
$("tapebtn")?.addEventListener("click", tapeToggle);
themeApply();
tapePaint();
