/* ERN: next earnings report. What options price (term-structure implied move), how the stock actually moved
   on past reports, and how far each thesis case sits from one report's priced range. Read-only Robinhood
   option quotes; nothing is ordered. */
"use strict";

function bodyErn(t) {
  const d = eqGet(t, "ern");
  if (!d) return '<div class="loading">Pricing the option chain...</div>';
  if (!d.ok) return `<div class="dim" style="padding:6px">${esc(d.error || "No data.")}</div>`;
  const nx = d.next, im = d.implied, h = d.hist || {};
  const days = nx ? Math.round((new Date(nx.date + "T12:00:00") - new Date()) / 864e5) : null;
  let out = nx
    ? `<div><b class="amb">${esc(nx.date)}</b> <span class="dim">${esc((nx.timing || "").toUpperCase() || "time n/a")} · ${nx.verified ? "confirmed" : "tentative"} · ${days <= 0 ? "today" : days + " days"}${nx.est != null ? ` · EPS est ${n(nx.est, 2)}` : ""}</span></div>`
    : '<div class="dim">No upcoming report on Robinhood\'s calendar.</div>';
  if (im) {
    const rel = im.vs_hist, tag = rel == null ? "" : rel > 1.2 ? ["RICH", "var(--neg)"] : rel < 0.8 ? ["CHEAP", "var(--pos)"] : ["IN LINE", "#9a9a9a"];
    const pos = posBy(t), atRisk = pos && pos.value ? pos.value * im.move : null;
    out += `<div style="margin:6px 0"><span style="font-size:20px" class="amb">${im.upper_bound ? "≤" : ""}±${n(im.move * 100, 1)}%</span>
      <span class="dim"> priced · ${money(im.low, 2)} to ${money(im.high, 2)}</span>
      ${tag ? ` <span class="dtag" style="color:${tag[1]};border-color:${tag[1]}">${tag[0]} vs past</span>` : ""}</div>
      ${kv([["Past reports (avg | max)", h.avg != null ? `±${n(h.avg * 100, 1)}% | ±${n(h.max * 100, 1)}% · up ${h.up} of ${h.n}` : "--"],
            ["Options vs history", rel != null ? n(rel, 2) + "x" : "--"],
            ...(atRisk ? [["Your position, one priced move", "±" + money(atRisk, 0)]] : []),
            ["Estimate quality", `${esc(im.quality)}`]])}
      <div class="small dim">${esc(im.method)}: expiries ${esc(im.before?.expiry || "(none before)")} → ${esc(im.after.expiry)}, ${im.days_after} days after the report${im.quality === "low" ? "; the closer expiry lists nearer the date will sharpen this" : ""}.</div>`;
  } else if (nx) {
    out += `<div class="dim small" style="margin:6px 0">${esc(d.implied_note || "No implied move.")}</div>`;
    if (h.avg != null) out += kv([["Past reports (avg | max)", `±${n(h.avg * 100, 1)}% | ±${n(h.max * 100, 1)}% · up ${h.up} of ${h.n}`]]);
  }
  if (d.thesis?.length) {
    out += `<div class="sec2">THESIS VS ONE REPORT</div><table>${d.thesis.map((x) => `<tr><td>${esc(x.case.toUpperCase())}</td><td class="num">${money(x.value, 2)}</td>
      <td class="num ${x.gap < 0 ? "neg" : "pos"}">${spct(x.gap, 0)}</td><td class="small dim">${x.inside ? "inside the priced range" : `≈${n(x.reports, 1)} reports' worth of move`}</td></tr>`).join("")}</table>`;
  }
  if (d.past?.length) {
    const mx = Math.max(...d.past.map((m) => Math.abs(m.move)), 0.01);
    out += `<div class="sec2">PAST REACTIONS <span class="dim">close to close · net = minus SPY</span></div><table>
      ${d.past.slice().reverse().map((m) => { const beat = m.eps_act != null && m.eps_est != null ? (m.eps_act >= m.eps_est ? '<span class="pos">beat</span>' : '<span class="neg">miss</span>') : "";
        return `<tr><td class="dim">${esc(m.date)}</td><td>${beat}</td><td style="width:90px"><div style="height:8px;width:${Math.round(Math.abs(m.move) / mx * 80)}px;background:${m.move >= 0 ? "var(--pos)" : "var(--neg)"}"></div></td>
          <td class="num ${m.move >= 0 ? "pos" : "neg"}">${spct(m.move, 1)}</td><td class="num dim">${m.net != null ? spct(m.net, 1) : ""}</td></tr>`; }).join("")}</table>`;
  }
  return out + `<div class="cnote">Implied move = event premium in the at-the-money IV across the expiries either side of the report (expected size of the move, either direction). Robinhood option quotes, read-only.</div>`;
}
