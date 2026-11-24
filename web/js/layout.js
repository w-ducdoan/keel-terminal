/* Panel layout for HOME, PORT, WATCH and DES: drag a title to move a panel, drag its corner to resize
   (12-column grid, free height), × to close, restore from the toolbar. Saved per page in this browser.
   It decorates whatever a page renders, so pages stay plain HTML. MKTS keeps its own grid (mkts.js). */
"use strict";

const slug = (s) => String(s || "").toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
const layKey = () => "lay:" + page;
function getLay() {
  try { return JSON.parse(lsGet(layKey()) || "null") || { order: [], span: {}, h: {}, hidden: [] }; }
  catch { return { order: [], span: {}, h: {}, hidden: [] }; }
}
const putLay = (l) => lsSet(layKey(), JSON.stringify(l));
function resetPageLayout() { lsSet(layKey(), ""); render(); }
function hidePanel(id) { const l = getLay(); if (!l.hidden.includes(id)) l.hidden.push(id); putLay(l); render(); }
function showPanel(id) { const l = getLay(); l.hidden = l.hidden.filter((x) => x !== id); putLay(l); render(); }

function applyLayout() {
  const grid = $("main").querySelector(":scope > .grid");
  if (!grid) return;
  grid.classList.add("lgrid");
  const lay = getLay(), panels = [...grid.children].filter((x) => x.classList.contains("p"));
  const names = {};
  panels.forEach((p, i) => {
    const h3 = p.querySelector(":scope > h3");
    const title = h3 ? (h3.childNodes[0]?.textContent || h3.textContent) : "PANEL" + i;
    const id = p.dataset.pid || slug(title) || "P" + i;
    p.dataset.pid = id;
    names[id] = title.trim();
    p.classList.add("lp");
    const span = lay.span[id] || +p.dataset.span || (p.classList.contains("span2") ? 12 : 6);
    p.style.gridColumn = `span ${span}`;
    const oi = lay.order.indexOf(id);
    p.style.order = oi >= 0 ? oi : 100 + i;
    const hh = lay.h[id] || +p.dataset.h;
    if (hh) { p.style.height = hh + "px"; p.classList.add("fixedh"); }
    if (lay.hidden.includes(id)) p.style.display = "none";
    if (h3 && !h3.querySelector(".pclose")) {
      h3.setAttribute("draggable", "true");
      h3.classList.add("ldrag");
      h3.insertAdjacentHTML("beforeend", `<span class="pclose" title="Close panel" onclick="event.stopPropagation();hidePanel('${id}')">×</span>`);
    }
    if (!p.querySelector(":scope > .rz")) p.insertAdjacentHTML("beforeend", EDGES_HTML);
  });
  const hidden = lay.hidden.filter((id) => names[id]);
  const tb = document.createElement("div");
  tb.className = "mtool ltool";
  tb.innerHTML = `${hidden.length ? '<span class="dim">CLOSED</span>' + hidden.map((id) => `<button onclick="showPanel('${id}')">+ ${esc(names[id].slice(0, 28))}</button>`).join("") : ""}
    <button onclick="resetPageLayout()">RESET LAYOUT</button><span class="dim" style="margin-left:auto">drag a title to move · drag any edge to resize · × to close</span>`;
  grid.before(tb);
}

// ---------- resize: drag any side or the bottom edge (the title bar moves the panel) ----------
document.addEventListener("mousedown", (e) => {
  const h = e.target.closest(".lp > .rz");
  if (!h) return;
  const p = h.closest(".lp");
  edgeStart(e, p, p.parentElement, 120, (changes) => {
    const l = getLay();
    for (const c of changes) {
      const id = c.panel.dataset.pid;
      if (c.span != null) l.span[id] = c.span;
      if (c.h != null) l.h[id] = c.h;
    }
    putLay(l);
  });
});

// ---------- move ----------
document.addEventListener("dragstart", (e) => {
  const t = e.target.closest?.(".lp > .ldrag");
  if (!t) return;
  e.dataTransfer.setData("text/lp", t.parentElement.dataset.pid);
  e.dataTransfer.effectAllowed = "move";
  window.mkDragging = true;
});
document.addEventListener("dragover", (e) => { const p = e.target.closest?.(".lp"); if (p && e.dataTransfer.types.includes("text/lp")) { e.preventDefault(); p.classList.add("dropto"); } });
document.addEventListener("dragleave", (e) => { const p = e.target.closest?.(".lp"); if (p) p.classList.remove("dropto"); });
document.addEventListener("drop", (e) => {
  const target = e.target.closest?.(".lp"), from = e.dataTransfer.getData("text/lp");
  if (!target || !from) return;
  e.preventDefault();
  window.mkDragging = false;
  const to = target.dataset.pid;
  if (from === to) return render();
  // Current visual order, then move `from` in front of `to`.
  const ids = [...target.parentElement.children].filter((x) => x.classList.contains("lp")).sort((a, b) => (+a.style.order) - (+b.style.order)).map((x) => x.dataset.pid);
  const order = ids.filter((x) => x !== from);
  order.splice(order.indexOf(to), 0, from);
  const l = getLay(); l.order = order; putLay(l);
  render();
});
document.addEventListener("dragend", () => { window.mkDragging = false; document.querySelectorAll(".dropto").forEach((x) => x.classList.remove("dropto")); });
