/* SPLC: supply chain for the open ticker. Suppliers on the left, customers on the right, the company in
   the middle; competitors and plain mentions listed underneath. Sources: the company's own 10-K (named
   major customers with % of revenue), XBRL customer-concentration tags, other companies' 10-Ks that name
   it (SEC full-text search), and links you add yourself. Double-click the title for the full window. */
"use strict";

const SPLC_COV = { held: ["HELD", "var(--pos)"], watch: ["WATCH", "var(--amber)"], tracked: ["TRACKED", "#c9a0ff"] };
const SPLC_CONF = { high: "their own 10-K", medium: "another filer's 10-K", low: "mention only", yours: "added by you" };

function splcAll(d) {
  const mine = (d.manual || []).map((x) => ({ ...x, ticker: /^[A-Z][A-Z.\-]{0,6}$/.test(x.ticker || "") ? x.ticker : null }));
  return [...mine, ...(d.links || [])];
}
const splcTag = (x) => (x.cov ? `<span class="dtag" style="color:${SPLC_COV[x.cov][1]};border-color:${SPLC_COV[x.cov][1]}">${SPLC_COV[x.cov][0]}</span>` : "");
const splcName = (x) => (x.ticker ? `<a class="amb" href="#SEC/${encodeURIComponent(x.ticker)}" onclick="closeDrill()">${esc(x.ticker)}</a> <span class="dim">${esc(x.name || "")}</span>` : esc(x.name || ""));

function splcGraph(t, d, w = 760) {
  const all = splcAll(d);
  const side = (rel) => all.filter((x) => x.relation === rel).slice(0, 8);
  const sup = side("supplier"), cus = side("customer");
  if (!sup.length && !cus.length) return "";
  const rows = Math.max(sup.length, cus.length, 1), h = Math.max(120, rows * 30 + 30), cx = w / 2, cy = h / 2;
  const y = (i, k) => (h / (k + 1)) * (i + 1);
  const node = (x, xi, yi, anchor) => {
    const col = x.cov ? SPLC_COV[x.cov][1] : "var(--txt)";
    const lab = (x.ticker || x.name || "").slice(0, 26) + (x.pct ? `  ${n(x.pct, 0)}%` : "");
    const go = x.ticker ? ` style="cursor:pointer" onclick="location.hash='#SEC/${encodeURIComponent(x.ticker)}';closeDrill()"` : "";
    return `<g${go}><title>${esc(x.name || "")} · ${esc(SPLC_CONF[x.confidence] || x.source || "")}${x.snippet ? " · " + esc(x.snippet.slice(0, 200)) : ""}</title>
      <text x="${xi}" y="${yi + 4}" text-anchor="${anchor}" fill="${col}" font-size="12">${esc(lab)}</text></g>`;
  };
  const edge = (x, x2, y2) => {
    const sw = x.pct ? Math.min(8, 1 + x.pct / 8) : 1.2, dash = x.confidence === "high" || x.confidence === "yours" ? "" : ' stroke-dasharray="4 3"';
    return `<line x1="${cx}" y1="${cy}" x2="${x2}" y2="${y2}" stroke="#555" stroke-width="${sw}"${dash}/>`;
  };
  const L = 170, R = w - 170;
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" style="max-height:${h}px">
    <text x="12" y="14" fill="#888" font-size="10">SUPPLIERS</text><text x="${w - 12}" y="14" fill="#888" font-size="10" text-anchor="end">CUSTOMERS</text>
    ${sup.map((x, i) => edge(x, L + 6, y(i, sup.length))).join("")}${cus.map((x, i) => edge(x, R - 6, y(i, cus.length))).join("")}
    <rect x="${cx - 52}" y="${cy - 14}" width="104" height="28" rx="3" fill="var(--bg2,#1a1a1a)" stroke="var(--amber)"/>
    <text x="${cx}" y="${cy + 5}" text-anchor="middle" fill="var(--amber)" font-size="13" font-weight="bold">${esc(t)}</text>
    ${sup.map((x, i) => node(x, L, y(i, sup.length), "end")).join("")}${cus.map((x, i) => node(x, R, y(i, cus.length), "start")).join("")}
  </svg>`;
}

function bodySplc(t) {
  const d = eqGet(t, "splc");
  if (!d) return '<div class="loading">Reading 10-Ks (the first build for a name takes up to a minute; then cached a week)...</div>';
  if (!d.ok) return `<div class="dim" style="padding:6px">${esc(d.error || "No data.")}</div>`;
  const all = splcAll(d), other = all.filter((x) => x.relation === "competitor" || x.relation === "partner");
  const ment = all.filter((x) => x.relation === "mention");
  const g = splcGraph(t, d);
  const warn = d.search_error ? `<div class="neg small">${esc(d.search_error)}</div>` : "";
  return `${warn}${g || '<div class="dim small" style="padding:6px 0">No named suppliers or customers found. Most companies do not name them; add the ones you know in the full window (double-click the title).</div>'}
    ${other.length ? `<div class="sec2">COMPETITORS · PARTNERS</div><div class="small">${other.map((x) => `${splcName(x)} ${splcTag(x)}`).join(" · ")}</div>` : ""}
    ${ment.length ? `<div class="sec2">ALSO NAMED IN <span class="dim">${ment.length} other 10-K${ment.length > 1 ? "s" : ""}, relationship unclear</span></div><div class="small dim">${ment.slice(0, 10).map((x) => (x.ticker ? esc(x.ticker) : esc(x.name))).join(" · ")}</div>` : ""}
    <div class="cnote">Solid line: from ${esc(t)}'s own 10-K or added by you. Dashed: another company's 10-K. Thicker = bigger % of revenue. Hover a name for the source sentence. Built ${esc(d.built || "")}.
      <a href="#" class="amb" onclick="openDrill('splc:${esc(t)}');return false">Full window</a></div>`;
}

DRILL.splc = (t) => {
  const d = eqGet(t, "splc");
  if (!d || !d.ok) return { title: `SPLC · ${esc(t)}`, body: d ? esc(d.error || "") : '<div class="loading">Loading...</div>' };
  const all = splcAll(d);
  const order = { supplier: 0, customer: 1, competitor: 2, partner: 3, mention: 4 };
  all.sort((a, b) => order[a.relation] - order[b.relation] || (b.pct || 0) - (a.pct || 0));
  const rows = all.map((x) => `<tr><td>${esc(x.relation.toUpperCase())}</td><td>${splcName(x)} ${splcTag(x)}</td>
      <td class="num">${x.pct ? n(x.pct, 0) + "%" : "--"}</td>
      <td class="small">${esc(SPLC_CONF[x.confidence] || x.source || "")}${x.date ? ` <span class="dim">${esc(x.date)}</span>` : ""}</td>
      <td class="small" style="white-space:normal;max-width:420px">${esc(x.snippet || x.note || "")}${safeUrl(x.url) ? ` <a class="amb" href="${esc(x.url)}" target="_blank" rel="noopener">filing</a>` : ""}
      ${x.source === "you" ? ` <button class="mini" onclick="splcDel('${esc(t)}','${esc(x.name).replace(/'/g, "\\'")}','${esc(x.relation)}')">remove</button>` : ""}</td></tr>`).join("");
  return {
    title: `SPLC · ${esc(t)} <span class="dim">${esc(d.entity || "")}</span>`,
    body: `${splcGraph(t, d, 900)}
      <table><tr><th>Relation</th><th>Company</th><th class="num">% rev</th><th>Source</th><th>Evidence</th></tr>${rows || '<tr><td colspan="5" class="dim">Nothing found yet.</td></tr>'}</table>
      <div class="sec2">ADD A LINK YOU KNOW</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <input id="splc-o" data-suggest="splc" placeholder="Ticker or company" style="width:150px">
        <select id="splc-r"><option>supplier</option><option>customer</option><option>competitor</option><option>partner</option></select>
        <input id="splc-p" placeholder="% of revenue (optional)" style="width:150px">
        <input id="splc-n" placeholder="Note / source" style="flex:1;min-width:160px">
        <button class="mini" onclick="splcAdd('${esc(t)}')">Add</button>
        <button class="mini" onclick="splcRebuild('${esc(t)}')" title="Re-read the filings now (cached a week otherwise)">Rebuild from SEC</button></div>
      <div class="cnote">Coverage is partial by nature: companies only have to name customers above 10% of revenue, and suppliers rarely. ${safeUrl(d.own_10k) ? `<a class="amb" href="${esc(d.own_10k)}" target="_blank" rel="noopener">Latest 10-K</a>` : ""}</div>`,
  };
};

async function splcReload(t, fresh = false) {
  const d = await (await fetch(`/api/splc/${encodeURIComponent(t)}${fresh ? "?fresh=true" : ""}`)).json();
  EQ[t + "|splc"] = { t: Date.now(), d };
  desPaint("splc");
  if (!$("drill").hidden) openDrill("splc:" + t);
}
async function splcAdd(t) {
  const body = { other: $("splc-o").value.trim(), relation: $("splc-r").value, pct: $("splc-p").value.trim(), note: $("splc-n").value.trim() };
  if (!body.other) return;
  const r = await fetch(`/api/splc/${encodeURIComponent(t)}/link`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { const e = await r.json().catch(() => ({})); return alert(e.detail || `Server said ${r.status}`); }
  await splcReload(t);
}
async function splcDel(t, other, relation) {
  await fetch(`/api/splc/${encodeURIComponent(t)}/link?other=${encodeURIComponent(other)}&relation=${encodeURIComponent(relation)}`, { method: "DELETE" });
  await splcReload(t);
}
async function splcRebuild(t) {
  const b = event?.target; if (b) { b.disabled = true; b.textContent = "Reading filings..."; }
  await splcReload(t, true);
}

document.addEventListener("dblclick", (e) => {
  const h = e.target.closest('.p[data-sec="splc"] > h3');
  if (h && page === "SEC") openDrill("splc:" + secTicker());
});
