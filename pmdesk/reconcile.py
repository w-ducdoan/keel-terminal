"""RECONCILE list: everything the desk saw at Robinhood that the research folder's files don't reflect yet.

Each item says what changed, carries a ready-to-paste draft, and says whether it is pure bookkeeping
(the scheduled reconcile task may write it) or needs your judgment (left for you). The list is
rebuilt from live data on every call, so an item disappears by itself once the files match.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

HOLDING_COLS = "| Ticker | Company | Category | Shares | Avg Cost | Est. Entry Value | Portfolio Weight | Framework Alignment | Thesis Status | Action Flag |"


def _category(p: dict[str, Any]) -> str:
    if p.get("sleeve") == "etf":
        return "Index fund - " + (p.get("industry") or "ETF")
    if p.get("sleeve") == "hedge":
        return "Diversifier - " + (p.get("industry") or "fund")
    sector = {"Information Technology": "Technology", "Health Care": "Healthcare"}.get(p.get("sector") or "", p.get("sector") or "Unclassified")
    return f"Equity - {sector}"


def holding_row(p: dict[str, Any], today: str) -> str:
    avg, sh = p.get("avg_cost"), p.get("shares") or 0
    cost = f"${avg * sh:,.2f}" if avg else "--"
    return (f"| {p['ticker']} | {p.get('company') or p['ticker']} | {_category(p)} | {sh:.4f} | "
            f"{f'${avg:,.2f}' if avg else '--'} | {cost} | {p.get('weight', 0) * 100:.1f}% (desk, {today}) | "
            f"TBD | TBD (no thesis yet) | Added by reconcile {today}; sector from Yahoo, confirm |")


def build(snap: dict[str, Any], transfers: list[dict[str, Any]], coverage: Callable[[str], dict[str, Any]],
          done: set[str], today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    pos = {p["ticker"]: p for p in snap.get("positions", [])}
    items: list[dict[str, Any]] = []

    if snap.get("source") != "robinhood":
        return {"ok": False, "live": False, "items": [],
                "note": "Robinhood is not connected, so there is nothing to reconcile against."}

    for r in snap.get("recon", []):
        t, title = r["ticker"], r["title"]
        p = pos.get(t, {})
        if title.startswith("Held at Robinhood"):
            cov = coverage(t)
            new_name = cov.get("status") == "none"
            items.append({
                "id": f"add:{t}:{p.get('shares')}", "kind": "add_holding", "ticker": t,
                "title": f"New position {t}: add a Holdings row to portfolio.md",
                "detail": f"{p.get('shares', 0):g} sh at ${p.get('avg_cost') or 0:,.2f}. Sector {p.get('sector')} is provisional (from Yahoo).",
                "draft": holding_row(p, today), "file": "portfolio/portfolio.md (## Holdings)",
                "bookkeeping": True,
                "judgment": ("Not in any coverage bucket: Gate 0 was skipped. Run Gate 0 or write why it was bought." if new_name
                             else "Confirm the sector and theme bucket."),
            })
        elif title.startswith("Share count differs"):
            items.append({
                "id": f"shares:{t}:{p.get('shares')}", "kind": "update_shares", "ticker": t,
                "title": f"{t}: share count changed", "detail": r["detail"],
                "draft": f"{t}: Shares -> {p.get('shares', 0):.4f}, Avg Cost -> ${p.get('avg_cost') or 0:,.2f} (Robinhood, {today})",
                "file": "portfolio/portfolio.md (## Holdings)", "bookkeeping": True, "judgment": None,
            })
        elif title.startswith("In portfolio.md but not held"):
            items.append({
                "id": f"exit:{t}", "kind": "log_exit", "ticker": t,
                "title": f"{t} is no longer held: log the exit", "detail": r["detail"],
                "draft": f"Move {t} from ## Holdings to ## Exited Positions (exit date and price from Robinhood order history).",
                "file": "portfolio/portfolio.md", "bookkeeping": True,
                "judgment": "Add a one-line reason for the exit if it isn't already in the position's notes.",
            })

    for tr in transfers:
        if tr.get("logged"):
            continue
        kind = "Deposit" if tr["amount"] > 0 else "Withdrawal"
        items.append({
            "id": f"transfer:{tr['id']}", "kind": "transfer", "ticker": None,
            "title": f"{kind} of ${abs(tr['amount']):,.2f} on {tr['date']}",
            "detail": "Seen by the desk as a cash jump no trade explains; already excluded from returns.",
            "draft": f"{tr['date']}: external {kind.lower()} {'+' if tr['amount'] > 0 else '-'}${abs(tr['amount']):,.2f} (dated by Keel on the day it landed)",
            "file": "portfolio/performance-log.md (flows note)", "bookkeeping": True, "judgment": None,
        })

    items = [i for i in items if i["id"] not in done]
    return {"ok": True, "live": True, "as_of": snap.get("as_of"), "items": items,
            "needs_you": sum(1 for i in items if i.get("judgment")), "holding_columns": HOLDING_COLS}
