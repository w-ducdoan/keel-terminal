/* Public edition (the server sets window.KEEL_PUBLIC): Keel as a market terminal with no portfolio.
   Pages: MKTS (start), DES Launchpad, MAP, CAL, EVT. Functions: security windows (DES GP FA RV EE ANR HDS INS SPLC CN
   CACS), N news, FLNG 13F, WHY on price charts. Everything tied to a book (PORT, WATCH, journal, actions, Robinhood,
   thesis, what-if) is hidden here and refused by the server. */
"use strict";
if (window.KEEL_PUBLIC) {
  document.body.classList.add("public");
  document.querySelector(".brand").insertAdjacentHTML("beforeend", ' <i class="pubtag" title="Market terminal: no portfolio, no account">PUBLIC</i>');

  // pages and aliases
  for (const k of ["HOME", "PORT", "WATCH", "JRNL"]) if (PAGES[k]) PAGES[k].hidden = true;
  for (const [a, k] of Object.entries(ALIASES)) if (PAGES[k]?.hidden) delete ALIASES[a];
  ALIASES.H = "MKTS"; ALIASES.HOME = "MKTS";
  currentPage = function () {
    const h = location.hash.replace("#", "").toUpperCase();
    if (h === "SEC" || h.startsWith("SEC/")) return "SEC";
    return PAGES[h] && !PAGES[h].hidden ? h : "MKTS";
  };
  renderNav = function () {
    const last = (page === "SEC" && secTicker()) || lsGet("des:last") || "";
    $("nav").innerHTML = Object.entries(PAGES).filter(([, p]) => !p.hidden)
      .map(([k, p]) => `<a href="${k === "SEC" ? "#SEC/" + encodeURIComponent(last) : "#" + k}" class="${k === page ? "on" : ""}">${p.label}<span>${k === "SEC" && last ? esc(last) : p.sub}</span></a>`).join("");
  };

  // functions: nothing that reads a book or needs a broker
  for (const k of ["ATTR", "TRIP", "SI", "THEME"]) delete FN[k];
  for (const k of Object.keys(BOOK_CODES)) delete BOOK_CODES[k];
  delete FN_LINKS.PORT;
  for (const k of ["THESIS", "SI", "ERN"]) delete SECFN[k];
  for (const c of Object.keys(CLASS_FNS)) CLASS_FNS[c] = CLASS_FNS[c].filter((f) => SECFN[f]);
  for (const s of ["cockpit", "rh", "ern"]) { const i = DES_SECTIONS.indexOf(s); if (i >= 0) DES_SECTIONS.splice(i, 1); }
  for (let i = DES_PANELS.length - 1; i >= 0; i--) if (["cockpit", "ern"].includes(DES_PANELS[i][0])) DES_PANELS.splice(i, 1);
  parseTrade = () => null;
  loadRecon = async () => null;

  // top bar: market levels instead of NAV and P&L
  const TOPS = ["^GSPC", "^IXIC", "^TNX", "^VIX", "DX-Y.NYB", "CL=F", "GC=F", "BTC-USD"];
  renderTop = function () {
    const rows = S.cross_asset || [], pick = TOPS.map((s) => rows.find((r) => r.symbol === s)).filter(Boolean);
    $("kpis").innerHTML = (pick.length ? pick : rows.slice(0, 6)).slice(0, 6).map((r) => `<div class="kpi" data-drill="mkt:${esc(r.symbol)}" title="${esc(r.label)}">
      <span>${esc((r.label || r.symbol).slice(0, 14))}</span><b class="${cls(r.change_pct)}">${n(r.last)} <small>${spct(r.change_pct, 1)}</small></b></div>`).join("");
    $("tags").innerHTML = `<span class="tag dim">${esc((S.session || "").toUpperCase())}</span>
      <span class="tag dim" title="Data as of">${esc(S.as_of ? new Date(S.as_of).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false }) : "--")}</span>`;
  };
  renderRail = function () { $("rail").innerHTML = ""; };
  document.querySelector(".shell").classList.add("norail");
  $("railbtn").hidden = true;

  // refresh: reload the snapshot and this page's data (the server refreshes market data on its own schedule)
  refreshAll = async function () { SC && Object.keys(SC).forEach((k) => delete SC[k]); await refresh(); };
  const rb = $("rfbtn"), nb = rb.cloneNode(true);
  rb.replaceWith(nb);
  nb.addEventListener("click", refreshAll);
  setAuto(lsGet("auto") || "0");

  // no coverage process here: no badge on the security card
  COV.none = ["", "transparent"];

  // 13F: the shared fund list and its new buys (no names of your own, no edits to the list)
  if (lsGet("f13:tab") !== "FUNDS") lsSet("f13:tab", "IDEAS");
  lsSet("f13:scope", "book");

  // SCRN: run the presets or your own criteria; no saving, tracking or Gate 0 here (nothing is stored for visitors)
  if (typeof scrResults === "function") {
    const scrBase = scrResults;
    scrResults = function () {
      return scrBase().replace(/<span>\d+ new · [^<]*<\/span>/, "")
        .replace(/<div class="small dim">Robinhood cross-check unavailable:[^<]*<\/div>/, "")
        .replace("<th>COV</th>", "<th></th>");
    };
  }

  // the map has one universe here
  lsSet("map:uni", "sp500");
}
