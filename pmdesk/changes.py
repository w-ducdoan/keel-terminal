"""Position changes between refreshes: what was bought, added to, trimmed or exited, matched to fills.

The desk keeps the share counts from its last Robinhood refresh. Each refresh compares the new counts
with those, records every difference, and looks up the Robinhood fills that explain it. A change with
no matching fill (a split, a transfer in or out, a stock reward) is flagged instead of guessed.
"""
from __future__ import annotations

from typing import Any

TOL = 1e-6


def diff(prev: dict[str, float], cur: dict[str, float]) -> list[dict[str, Any]]:
    out = []
    for t in sorted(set(prev) | set(cur)):
        old, new = prev.get(t, 0.0) or 0.0, cur.get(t, 0.0) or 0.0
        d = new - old
        if abs(d) <= TOL:
            continue
        kind = "new" if old <= TOL else "exit" if new <= TOL else "add" if d > 0 else "trim"
        out.append({"ticker": t, "kind": kind, "old": old, "new": new, "delta": d})
    return out


def looks_broken(prev: dict[str, float], cur: dict[str, float]) -> bool:
    """A feed hiccup can return no positions or drop most of them at once. Don't record that as a
    wave of exits; keep the old baseline and try again next refresh."""
    if not prev:
        return False
    if not cur:
        return True
    gone = sum(1 for t in prev if prev[t] > TOL and cur.get(t, 0.0) <= TOL)
    return len(prev) >= 4 and gone / len(prev) > 0.5


def match(change: dict[str, Any], fills: list[dict[str, Any]], since_ts: str | None) -> dict[str, Any]:
    """Attach the fills for this ticker after the last snapshot; say whether they explain the change."""
    fs = [f for f in fills if f["symbol"] == change["ticker"] and (not since_ts or (f.get("ts") or "") > since_ts)]
    signed = sum(f["qty"] if f["side"] == "buy" else -f["qty"] for f in fs)
    qty = sum(f["qty"] for f in fs)
    avg = sum(f["qty"] * f["price"] for f in fs) / qty if qty else None
    matched = bool(fs) and abs(signed - change["delta"]) < 1e-4
    note = None
    if not fs:
        note = "No Robinhood fill found: a split, a transfer, a stock reward, or a fill older than the last check."
    elif not matched:
        note = f"Fills add up to {signed:+.4f} sh, the position moved {change['delta']:+.4f} sh."
    return {**change, "price": avg, "fills": len(fs), "last_fill": max((f["ts"] for f in fs), default=None),
            "matched": matched, "note": note,
            "recurring": any(f.get("recurring_amount") for f in fs)}
