/* Company card: name, price, valuation, a short description and coverage status, for any ticker.
   Used in ticker pop-ups (data-card placeholders) and the command-bar preview. */
"use strict";

const CARDS = {}; // ticker -> {t, d}
async function getCard(t) {
  const hit = CARDS[t];
  if (hit && Date.now() - hit.t < 60e3) return hit.d;
  const r = await fetch("/api/equity/" + encodeURIComponent(t) + "/card");
  const d = r.ok ? await r.json() : { ok: false, error: "Not a ticker" };
  CARDS[t] = { t: Date.now(), d };
  return d;
}

const COV = {
  held: ["HELD", "var(--pos)"], watchlist: ["WATCHLIST", "var(--blue)"], passed: ["PASSED", "#9aa"],
  "closed-positions": ["CLOSED", "#9aa"], holdings: ["HELD", "var(--pos)"], none: ["NOT IN COVERAGE · Gate 0 first", "var(--amber)"], tracked: ["TRACKED · Keel list, not underwritten", "#c9a0ff"],
};
const bigN = (x) => (x == null ? "--" : x >= 1e12 ? "$" + n(x / 1e12, 2) + "T" : x >= 1e9 ? "$" + n(x / 1e9, 1) + "B" : x >= 1e6 ? "$" + n(x / 1e6, 0) + "M" : money(x, 0));

function cardHTML(c, opts = {}) {
  if (!c) return '<span class="dim">Loading company...</span>';
  if (!c.ok) return `<span class="dim">${esc(c.error || "No company data.")}</span>`;
  const cov = COV[c.coverage?.status] || COV.none;
  const pos = c.hi52 && c.lo52 && c.last ? Math.max(0, Math.min(1, (c.last - c.lo52) / (c.hi52 - c.lo52))) : null;
  return `<div class="ccard">
    <div class="ch"><b class="amb">${esc(c.ticker)}</b> <span>${esc(c.name || "")}</span>
      <span class="tag" style="color:${cov[1]};border-color:${cov[1]}">${cov[0]}</span>
      ${c.last != null ? `<span style="margin-left:auto"><b>${n(c.last)}</b> <span class="${cls(c.chg_pct)}">${spct(c.chg_pct)}</span></span>` : ""}</div>
    <div class="dim small">${esc([c.exchange, c.sector, c.industry, c.country].filter(Boolean).join(" · "))}</div>
    ${c.blurb ? `<div class="cblurb">${esc(opts.full ? c.summary : c.blurb)}</div>` : ""}
    <div class="cstats">
      <span>Mkt cap <b>${bigN(c.market_cap)}</b></span><span>P/E <b>${c.pe ? n(c.pe, 1) : "--"}</b></span><span>Fwd P/E <b>${c.fwd_pe ? n(c.fwd_pe, 1) : "--"}</b></span>
      <span>EV/EBITDA <b>${c.ev_ebitda ? n(c.ev_ebitda, 1) : "--"}</b></span><span>Div <b>${c.div_yield ? n(c.div_yield, 2) + "%" : "--"}</b></span><span>Beta <b>${c.beta ? n(c.beta, 2) : "--"}</b></span>
      ${pos != null ? `<span>52w <b>${n(c.lo52)}</b> <span class="rng" style="width:60px"><i style="left:${(pos * 100).toFixed(0)}%"></i></span> <b>${n(c.hi52)}</b></span>` : ""}
      ${c.target ? `<span>Target <b>${n(c.target)}</b> <span class="${cls(c.target - c.last)}">(${spct(c.target / c.last - 1, 0)})</span> ${esc(c.rating || "")}${c.analysts ? " · " + c.analysts : ""}</span>` : ""}
    </div></div>`;
}

/** Fill every [data-card] placeholder under root. */
function fillCards(root = document) {
  root.querySelectorAll("[data-card]").forEach(async (el) => {
    const t = el.dataset.card;
    try { const c = await getCard(t); if (el.dataset.card === t) el.innerHTML = cardHTML(c, { full: el.dataset.full === "1" }); }
    catch (e) { el.innerHTML = `<span class="dim">Company data unavailable: ${esc(e.message)}</span>`; }
  });
}
