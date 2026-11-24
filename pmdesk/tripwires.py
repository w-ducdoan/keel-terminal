"""Thesis tripwires: three prices per holding that turn a price move into a decision.

  add below   - buy more under this price (off unless you set it)
  review at   - the thesis bear value: at this price the market is pricing your downside case; re-underwrite
  trim above  - the probability-weighted value (else the base case): the upside you underwrote is mostly paid for

Defaults come from the thesis cockpit (bear / base / bull and the weighted value in the the research folder thesis);
any level can be overridden per name and is stored in Keel only. Rule-based; no AI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import cockpit, store

NEAR = 0.05  # within 5% of a level = approaching


def defaults(ck: dict[str, Any]) -> dict[str, Any]:
    sc = {s["case"]: s.get("value") for s in (ck.get("scenarios") or []) if s.get("value")}
    trim = ck.get("weighted") or sc.get("base")
    return {"add_below": None, "review_at": sc.get("bear"), "trim_above": trim,
            "source": {"review_at": "thesis bear value" if sc.get("bear") else None,
                       "trim_above": ("probability-weighted value" if ck.get("weighted") else "thesis base value") if trim else None}}


def levels_for(t: str, ck: dict[str, Any]) -> dict[str, Any]:
    d = defaults(ck)
    mine = store.kv_get(f"trip:{t}") or {}
    out = {k: d[k] for k in ("add_below", "review_at", "trim_above")}
    src = dict(d["source"])
    for k in out:
        if mine.get(k) is not None:
            out[k] = float(mine[k])
            src[k] = "set by you"
    return {**out, "source": src, "note": mine.get("note")}


def status(price: float | None, lv: dict[str, Any]) -> list[dict[str, Any]]:
    """Which levels are hit or near. Order: review (downside) first, then trim, then add."""
    if not price:
        return []
    out = []
    r, tr, ad = lv.get("review_at"), lv.get("trim_above"), lv.get("add_below")
    if r:
        if price <= r:
            out.append({"level": "review_at", "state": "hit", "value": r})
        elif price <= r * (1 + NEAR):
            out.append({"level": "review_at", "state": "near", "value": r})
    if tr:
        if price >= tr:
            out.append({"level": "trim_above", "state": "hit", "value": tr})
        elif price >= tr * (1 - NEAR):
            out.append({"level": "trim_above", "state": "near", "value": tr})
    if ad:
        if price <= ad:
            out.append({"level": "add_below", "state": "hit", "value": ad})
        elif price <= ad * (1 + NEAR):
            out.append({"level": "add_below", "state": "near", "value": ad})
    return out


def evaluate(hq: Path, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in positions:
        if p.get("sleeve") in ("hedge", "etf"):
            continue
        t = p["ticker"]
        try:
            ck = cockpit.load(hq, t)
        except Exception:
            ck = {}
        lv = levels_for(t, ck if ck.get("ok") else {})
        price = p.get("price")
        dist = {k: (price / v - 1) if price and v else None for k, v in lv.items() if k in ("add_below", "review_at", "trim_above")}
        rows.append({"ticker": t, "price": price, "weight": p.get("weight"), **lv, "distance": dist, "status": status(price, lv),
                     "has_thesis": bool(ck.get("ok"))})
    return rows
