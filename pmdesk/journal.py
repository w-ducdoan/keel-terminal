"""Decision journal: why each trade was made, and later, whether it worked for the reason given.

Entries come in three ways: automatically from the position changes Keel detects, from a pre-trade check
("log this plan"), or by backfilling recent Robinhood fills once. Each one asks for a one-line reason and
what you expect; at its review date it asks for a verdict: right for the reason, right for another reason
(lucky), wrong, or too early. The scorecard separates process from outcome.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

VERDICTS = {"right": "Right, for the reason given", "lucky": "Worked, but not for the reason given",
            "wrong": "Wrong", "early": "Too early to tell"}


def enrich(entries: list[dict[str, Any]], prices: dict[str, float], spy_now: float | None, today: date) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        px = prices.get(e["ticker"])
        e = dict(e)
        e["price_now"] = px
        d0 = date.fromisoformat(e["date"])
        e["days"] = (today - d0).days
        e["review_date"] = (d0 + timedelta(days=int(e.get("horizon") or 90))).isoformat()
        e["due"] = not e.get("verdict") or e["verdict"] == "early"
        e["due"] = e["due"] and e["review_date"] <= today.isoformat()
        e["needs_reason"] = not (e.get("reason") or "").strip()
        if px and e.get("price"):
            move = px / e["price"] - 1
            # A buy wins when the stock rises; a trim or exit "wins" when it falls after you sold.
            e["result"] = move if e["kind"] in ("buy", "new", "add", "plan_buy") else -move
            e["spy_move"] = (spy_now / e["spy"] - 1) if (spy_now and e.get("spy")) else None
            e["excess"] = (e["result"] - (e["spy_move"] if e["kind"] in ("buy", "new", "add", "plan_buy") else -e["spy_move"])) \
                if e["spy_move"] is not None else None
        else:
            e["result"] = e["spy_move"] = e["excess"] = None
        out.append(e)
    return out


def scorecard(entries: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [e for e in entries if e.get("verdict") in ("right", "lucky", "wrong")]
    with_excess = [e for e in entries if e.get("excess") is not None and e["days"] >= 7]
    beat = [e for e in with_excess if e["excess"] > 0]
    return {
        "entries": len(entries),
        "need_reason": sum(1 for e in entries if e["needs_reason"]),
        "due": sum(1 for e in entries if e["due"]),
        "reviewed": len(reviewed),
        "right": sum(1 for e in reviewed if e["verdict"] == "right"),
        "lucky": sum(1 for e in reviewed if e["verdict"] == "lucky"),
        "wrong": sum(1 for e in reviewed if e["verdict"] == "wrong"),
        "process_rate": (sum(1 for e in reviewed if e["verdict"] == "right") / len(reviewed)) if reviewed else None,
        "beat_spy_rate": (len(beat) / len(with_excess)) if with_excess else None,
        "avg_excess": (sum(e["excess"] for e in with_excess) / len(with_excess)) if with_excess else None,
        "scored": len(with_excess),
    }


def from_fills(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group Robinhood fills into one decision per ticker, day and side (several partial fills = one decision)."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for f in fills:
        if f.get("recurring_amount"):
            continue  # recurring buys are a standing plan, not a decision
        k = (f["date"], f["symbol"], f["side"])
        g = groups.setdefault(k, {"date": f["date"], "ticker": f["symbol"], "kind": "buy" if f["side"] == "buy" else "sell",
                                  "shares": 0.0, "cost": 0.0})
        g["shares"] += f["qty"]
        g["cost"] += f["qty"] * f["price"]
    out = []
    for g in groups.values():
        g["price"] = g["cost"] / g["shares"] if g["shares"] else None
        g["key"] = f"fill:{g['date']}:{g['ticker']}:{g['kind']}"
        out.append(g)
    return sorted(out, key=lambda g: g["date"])
