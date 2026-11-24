/* MAP: market heat map (finviz-style treemap). Sector -> industry -> stock, area = market cap (or position value
   for your book), color = move over the chosen period. Squarified layout, drawn as positioned divs. */
"use strict";

const MAPD = {};
let mapBusy = false;
const mapUni = () => lsGet("map:uni") || "sp500", mapPer = () => lsGet("map:per") || "1D";
// Color scale cap by period: a 3% day is as strong as a 10% month
const MAP_SCALE = { "1D": 0.03, "1W": 0.06, "1M": 0.10, "3M": 0.20, YTD: 0.30, "1Y": 0.40 };

async function loadMap(force = false) {
  const k = mapUni() + "|" + mapPer(), hit = MAPD[k];
  if (mapBusy || (!force && hit && Date.now() - hit.t < (mapPer() === "1D" ? 60e3 : 15 * 60e3))) return;
  mapBusy = true;
  try { MAPD[k] = { t: Date.now(), d: await (await fetch(`/api/heatmap?universe=${mapUni()}&period=${mapPer()}`)).json() }; }
  catch (e) { MAPD[k] = { t: Date.now(), d: { ok: false, error: e.message } }; }
  mapBusy = false;
  if (page === "MAP") render();
}
function mapSet(k, v) { lsSet("map:" + k, v); render(); }

function mapColor(chg, cap) {
  if (chg == null) return "#2a2a2a";
  const t = Math.max(-1, Math.min(1, chg / cap)), base = [65, 69, 84], pos = [48, 204, 90], neg = [246, 53, 56];
  const to = t >= 0 ? pos : neg, a = Math.abs(t) ** 0.8;
  return `rgb(${base.map((b, i) => Math.round(b + (to[i] - b) * a)).join(",")})`;
}

/** Squarified treemap (Bruls, Huizing, van Wijk). items: [{v, ...}] sorted desc; returns [{x, y, w, h, item}]. */
function squarify(items, x, y, w, h) {
  const out = [], total = items.reduce((s, i) => s + i.v, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.map((i) => ({ ...i, a: i.v * scale }));
  const worst = (row, side) => { const s = row.reduce((t, r) => t + r.a, 0), mx = Math.max(...row.map((r) => r.a)), mn = Math.min(...row.map((r) => r.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn)); };
  while (rest.length) {
    const side = Math.min(w, h);
    let row = [rest[0]], i = 1;
    while (i < rest.length && worst([...row, rest[i]], side) <= worst(row, side)) { row.push(rest[i]); i++; }
    rest = rest.slice(i);
    const s = row.reduce((t, r) => t + r.a, 0);
    if (w >= h) {  // lay the row as a column on the left
      const cw = s / h; let cy = y;
      for (const r of row) { const rh = r.a / cw; out.push({ x, y: cy, w: cw, h: rh, item: r }); cy += rh; }
      x += cw; w -= cw;
    } else {       // lay the row along the top
      const rh = s / w; let cx = x;
      for (const r of row) { const rw = r.a / rh; out.push({ x: cx, y, w: rw, h: rh, item: r }); cx += rw; }
      y += rh; h -= rh;
    }
  }
  return out;
}

function mapHTML(d, W, H) {
  const cap = MAP_SCALE[d.period] || 0.03, H1 = 15, H2 = 12, PAD = 1;
  const sectors = {};
  for (const it of d.items) ((sectors[it.sector] ||= {})[it.industry] ||= []).push(it);
  const secList = Object.entries(sectors).map(([s, inds]) => ({ s, inds, v: Object.values(inds).flat().reduce((t, i) => t + i.size, 0) })).sort((a, b) => b.v - a.v);
  let html = "";
  for (const sb of squarify(secList, 0, 0, W, H)) {
    const S2 = sb.item;
    html += `<div class="hm-sec" style="left:${sb.x}px;top:${sb.y}px;width:${sb.w - PAD}px;height:${sb.h - PAD}px"><div class="hm-sh">${esc(S2.s.toUpperCase())}</div></div>`;
    const inds = Object.entries(S2.inds).map(([n, st]) => ({ n, st, v: st.reduce((t, i) => t + i.size, 0) })).sort((a, b) => b.v - a.v);
    for (const ib of squarify(inds, sb.x, sb.y + H1, sb.w - PAD, sb.h - H1 - PAD)) {
      const showInd = ib.w > 60 && ib.h > 40 && inds.length > 1, top = showInd ? H2 : 0;
      if (showInd) html += `<div class="hm-ih" style="left:${ib.x}px;top:${ib.y}px;width:${ib.w - PAD}px">${esc(ib.item.n.toUpperCase())}</div>`;
      const stocks = ib.item.st.map((i) => ({ ...i, v: i.size })).sort((a, b) => b.v - a.v);
      for (const b of squarify(stocks, ib.x, ib.y + top, ib.w - PAD, ib.h - top - PAD)) {
        const it = b.item, w = b.w - PAD, h = b.h - PAD;
        const fs = Math.max(0, Math.min(44, w / (Math.max(3, it.t.length) * 0.66), h / 2.4));
        const label = fs >= 7 ? `<b style="font-size:${fs.toFixed(0)}px">${esc(it.t)}</b>${h > fs * 2.1 && fs >= 9 ? `<i style="font-size:${Math.max(8, fs * 0.55).toFixed(0)}px">${it.chg == null ? "" : spct(it.chg, 2)}</i>` : ""}` : "";
        html += `<div class="hm-c" data-drill="tkr:${esc(it.t)}" data-tip="${esc(it.t)}|${esc(it.name || "")}|${esc(it.industry)}|${it.chg ?? ""}|${it.mcap ?? ""}|${it.size}"
          style="left:${b.x}px;top:${b.y}px;width:${Math.max(0, w)}px;height:${Math.max(0, h)}px;background:${mapColor(it.chg, cap)}">${label}</div>`;
      }
    }
  }
  return html;
}

function pageMap() {
  const k = mapUni() + "|" + mapPer(), d = MAPD[k]?.d;
  if (!d) setTimeout(() => loadMap(), 0);
  const cap = MAP_SCALE[mapPer()] || 0.03;
  const legend = [-1, -2 / 3, -1 / 3, 0, 1 / 3, 2 / 3, 1].map((f) => `<span style="background:${mapColor(f * cap, cap)}">${f === 0 ? "0%" : spct(f * cap, 0)}</span>`).join("");
  const bar = `<div class="rbar" style="margin-bottom:6px">${[["sp500", "S&P 500"], ["book", "YOUR BOOK"], ["watch", "WATCHLIST"]].map(([u, l]) => `<button class="${mapUni() === u ? "on" : ""}" onclick="mapSet('uni','${u}')">${l}</button>`).join("")}
    <span style="width:14px"></span>${["1D", "1W", "1M", "3M", "YTD", "1Y"].map((p) => `<button class="${mapPer() === p ? "on" : ""}" onclick="mapSet('per','${p}')">${p}</button>`).join("")}
    <span style="margin-left:auto" class="hm-leg">${legend}</span><button onclick="loadMap(true)">REFRESH</button></div>`;
  const head = d?.ok ? `<span class="dim small">${esc(d.note)} ${d.breadth.up} up · ${d.breadth.down} down · weighted ${spct(d.breadth.weighted, 2)} · ${esc(d.as_of)}</span>` : "";
  const body = !d ? `<div class="loading">Loading ${mapPer() === "1D" ? "today's moves" : "price history (first time about 30 seconds)"}...</div>`
    : !d.ok ? `<div class="neg">${esc(d.error || "")}</div>` : `<div class="hm" id="hm"></div><div class="hm-tip" id="hmtip" hidden></div>`;
  if (d?.ok) setTimeout(drawMap, 0);
  return `<div class="p span2 hm-panel" data-pid="HEATMAP"><h3>MAP <i>market heat map · click a box for its card</i></h3><div class="pb">${bar}${head}${body}</div></div>`;
}
function drawMap() {
  const el = document.getElementById("hm"), d = MAPD[mapUni() + "|" + mapPer()]?.d;
  if (!el || !d?.ok) return;
  const W = Math.floor(el.clientWidth), H = Math.max(420, Math.floor(window.innerHeight - el.getBoundingClientRect().top - 24));
  el.style.height = H + "px";
  el.innerHTML = mapHTML(d, W, H);
}
window.addEventListener("resize", () => { if (page === "MAP") { clearTimeout(drawMap._t); drawMap._t = setTimeout(drawMap, 150); } });
document.addEventListener("mousemove", (e) => {
  const tip = document.getElementById("hmtip");
  if (!tip) return;
  const c = e.target.closest && e.target.closest(".hm-c");
  if (!c) { tip.hidden = true; return; }
  const [t, name, ind, chg, mcap, size] = c.dataset.tip.split("|");
  tip.innerHTML = `<b class="amb">${esc(t)}</b> ${esc(name)}<div class="dim">${esc(ind)}</div><div>${chg === "" ? "--" : `<b class="${+chg >= 0 ? "pos" : "neg"}">${spct(+chg, 2)}</b>`} · ${mcap ? bigUsd(+mcap) + " mkt cap" : money(+size, 0) + " position"}</div>`;
  tip.hidden = false;
  tip.style.left = Math.min(window.innerWidth - 260, e.clientX + 14) + "px";
  tip.style.top = Math.min(window.innerHeight - 80, e.clientY + 14) + "px";
});
