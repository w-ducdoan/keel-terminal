"""Pre-trade check: what the book looks like after a proposed buy or sell, and whether it passes the mandate.

Nothing here places an order. It takes the live book, applies the trade on paper, recomputes weights,
caps, beta and risk share, and lists every check as PASS / WARN / FAIL with the rule behind it.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from . import equity, market, risk


def _p(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def build(state: dict[str, Any], history: pd.DataFrame | None, rules: dict[str, Any], beta_overrides: dict[str, str],
          hq_coverage: dict[str, Any], ticker: str, side: str, shares: float | None, dollars: float | None,
          price: float | None = None) -> dict[str, Any]:
    t = ticker.upper()
    side = side.lower()
    if side not in ("buy", "sell"):
        return {"ok": False, "error": "Side must be buy or sell."}
    positions = [dict(p) for p in state.get("positions", []) if p.get("shares")]
    by = {p["ticker"]: p for p in positions}
    acct = state.get("account") or {}
    nav, cash = acct.get("nav") or 0.0, acct.get("cash") or 0.0
    caps = state.get("caps") or rules.get("caps") or {}
    w = next((x for x in state.get("watchlist", []) if x.get("ticker") == t), None)

    px = price or (by.get(t) or {}).get("price") or (w or {}).get("price")
    if not px:
        q = equity.quote(t)
        px = q.get("last")
    if not px:
        return {"ok": False, "error": f"No live price for {t}."}
    held = by.get(t)

    # Size: shares, dollars, or the default starter (2% of NAV, or the watchlist's starter %)
    starter_pct = ((w or {}).get("starter_pct") or 2.0) / 100
    if shares is None and dollars is None:
        dollars = starter_pct * nav if side == "buy" else (held or {}).get("value")
    if shares is None:
        shares = (dollars or 0) / px
    dollars = shares * px
    if side == "sell" and held:
        shares = min(shares, held["shares"])
        dollars = shares * px

    checks: list[dict[str, Any]] = []

    def check(level: str, name: str, text: str) -> None:
        checks.append({"level": level, "name": name, "text": text})

    # Classification for a name not held yet
    if held:
        sector, sleeve, company = held.get("sector"), held.get("sleeve"), held.get("company")
    else:
        prov = equity.provisional_class(t)
        sector, sleeve, company = prov["sector"], prov["sleeve"], prov["company"]

    # Apply the trade on paper
    signed = shares if side == "buy" else -shares
    if held:
        held["shares"] += signed
        held["value"] = held["shares"] * px
    elif side == "buy":
        positions.append({"ticker": t, "shares": shares, "value": dollars, "price": px, "sector": sector,
                          "sleeve": sleeve, "company": company, "beta": None})
    new_cash = cash - dollars if side == "buy" else cash + dollars
    new_nav = nav  # a trade moves cash into stock or back; NAV is unchanged before costs
    for p in positions:
        p["weight_after"] = p["value"] / new_nav if new_nav else 0.0
    after = next((p for p in positions if p["ticker"] == t), None)
    w_before = (by.get(t) or {}).get("weight") or 0.0
    w_after = after["weight_after"] if after else 0.0

    # Cash
    if side == "buy":
        if dollars > cash + 0.01:
            check("fail", "Cash", f"Needs ${dollars:,.2f}; cash is ${cash:,.2f}.")
        else:
            check("pass", "Cash", f"${dollars:,.2f} of ${cash:,.2f} cash; {_p(new_cash / new_nav if new_nav else None)} cash after.")

    # Single-name cap
    if sleeve not in ("etf", "hedge"):
        cap = caps.get("single_name", 0.15)
        lvl = "fail" if w_after > cap else "warn" if w_after > cap * 0.9 else "pass"
        check(lvl, "Single-name cap", f"{t} {_p(w_before)} -> {_p(w_after)} of the {_p(cap, 0)} cap.")

    # Sector cap and sleeves
    sec_after: dict[str, float] = {}
    sleeve_after: dict[str, float] = {}
    for p in positions:
        sec_after[p.get("sector") or "Unclassified"] = sec_after.get(p.get("sector") or "Unclassified", 0.0) + p["weight_after"]
        sleeve_after[p.get("sleeve") or "core"] = sleeve_after.get(p.get("sleeve") or "core", 0.0) + p["weight_after"]
    if sector not in ("ETF", "Hedge", "Unclassified"):
        cap = caps.get("consumer_discretionary") if sector == "Consumer Discretionary" else caps.get("sector", 0.30)
        before = (state.get("aggregates") or {}).get("sector", {}).get(sector, 0.0)
        a = sec_after.get(sector, 0.0)
        lvl = "fail" if a > cap else "warn" if a > cap - 0.03 else "pass"
        check(lvl, "Sector cap", f"{sector} {_p(before)} -> {_p(a)} of the {_p(cap, 0)} cap.")
    elif sector == "Unclassified":
        check("warn", "Sector", f"Yahoo has no sector for {t}; the cap check can't run. Classify it in portfolio.md.")
    if sleeve == "hedge":
        h = sleeve_after.get("hedge", 0.0)
        lvl = "fail" if h > caps.get("hedge_sleeve_cap", 0.12) else "pass"
        check(lvl, "Hedge sleeve", f"Hedge sleeve after: {_p(h)} (target {_p(caps.get('hedge_sleeve_target', 0.10), 0)}, cap {_p(caps.get('hedge_sleeve_cap', 0.12), 0)}).")

    # Coverage and triggers (buys)
    status = hq_coverage.get("status")
    if side == "buy":
        if status == "none":
            check("fail", "Gate 0", f"{t} is in no coverage bucket. New names start at Gate 0 before any buy.")
        elif status == "passed":
            check("fail", "Passed name", f"{t} is in _passed. Re-run Gate 0 only if its re-look condition has been met.")
        elif w and w.get("buy_below"):
            d = px / w["buy_below"] - 1
            if d > 0:
                check("warn", "Buy-below", f"Price {px:,.2f} is {_p(d)} above the Buy-below {w['buy_below']:,.2f}. Wait, or write why paying up is justified.")
            else:
                check("pass", "Buy-below", f"At or through the Buy-below ({px:,.2f} vs {w['buy_below']:,.2f}). Run the trigger-hit refresh first.")
            if dollars > starter_pct * nav * 1.25:
                check("warn", "Size", f"${dollars:,.0f} is above the {starter_pct * 100:g}% starter (${starter_pct * nav:,.0f}).")

    # Beta
    beta = (held or {}).get("beta")
    hist = history
    if beta is None and history is not None:
        try:
            if t not in history.columns:
                extra = market.history([t], "2y")
                hist = history.join(extra, how="left") if not extra.empty else history
            if t in hist.columns:
                wk = hist[[t, "SPY"]].resample("W-FRI").last().pct_change(fill_method=None).dropna()
                if len(wk) >= 26:
                    c = wk.cov()
                    beta = float(c.loc[t, "SPY"] / c.loc["SPY", "SPY"])
        except Exception:
            beta = None
    if beta is not None and sleeve not in ("etf", "hedge"):
        hard = (rules.get("beta") or {}).get("hard_remove", 1.8)
        if beta > hard and not beta_overrides.get(t):
            check("fail" if side == "buy" else "pass", "Beta", f"Beta {beta:.2f} is above the {hard} hard-remove line with no written override.")
        else:
            check("pass", "Beta", f"Beta {beta:.2f} (limit {hard}{', override on file' if beta_overrides.get(t) else ''}).")

    # Portfolio risk before / after
    risk_out: dict[str, Any] = {}
    if hist is not None and new_nav:
        try:
            wb = {p["ticker"]: p.get("weight") or 0.0 for p in state.get("positions", []) if p.get("price")}
            wa = {p["ticker"]: p["weight_after"] for p in positions if p["weight_after"] > 0}
            rb = risk.compute(wb, hist, rules, nav)
            ra = risk.compute(wa, hist, rules, new_nav)
            if rb.get("ok") and ra.get("ok"):
                pb, pa = rb["portfolio"], ra["portfolio"]
                risk_out = {"beta": [pb.get("beta"), pa.get("beta")], "vol": [pb.get("vol"), pa.get("vol")],
                            "var95": [pb.get("var95_hist"), pa.get("var95_hist")],
                            "risk_share": (ra["names"].get(t) or {}).get("risk_share")}
                rs = risk_out["risk_share"]
                if rs is not None and side == "buy" and w_after and rs > 2 * w_after and rs > 0.08:
                    check("warn", "Risk share", f"{t} would be {_p(w_after)} of the book but {_p(rs)} of its risk.")
        except Exception as e:  # risk is informative; never block the check on it
            risk_out = {"error": str(e)[:120]}

    # Earnings soon
    e = next((x for x in state.get("earnings_upcoming", []) if x.get("ticker") == t), None)
    if e:
        check("warn", "Earnings", f"{t} reports {e.get('date')}{' ' + e['timing'] if e.get('timing') else ''}: decide whether to trade before or after.")

    # Sells: tax and execution
    if side == "sell" and held:
        gain = (px - (held.get("avg_cost") or px)) * shares
        check("pass" if gain >= 0 else "warn", "Realized P&L",
              f"About {'+' if gain >= 0 else '-'}${abs(gain):,.2f} on {shares:.4f} sh vs average cost"
              + ("" if gain >= 0 else "; a loss with a repurchase within 30 days is a wash sale") + ".")
        if history is not None and t in history.columns:
            s = history[t].dropna()
            if len(s) >= 6 and px / float(s.iloc[-6]) - 1 <= -(rules.get("moves") or {}).get("five_day_drop_pct", 0.08):
                ref = float(s.iloc[-6])
                check("warn", "Trim window", f"Down {_p(1 - px / ref)} in 5 sessions: if this is a valuation or size trim, "
                                             f"use a limit at {ref:,.2f} or {(px + ref) / 2:,.2f}, not market.")
        if held["shares"] <= 1e-9:
            check("pass", "Exit", f"This closes the {t} position; log the exit reason in the journal.")

    worst = "fail" if any(c["level"] == "fail" for c in checks) else "warn" if any(c["level"] == "warn" for c in checks) else "pass"
    verdict = {"fail": "FAILS the mandate", "warn": "Passes, with warnings", "pass": "Passes"}[worst]
    return {"ok": True, "ticker": t, "company": company, "side": side, "shares": shares, "price": px, "dollars": dollars,
            "weight_before": w_before, "weight_after": w_after, "cash_after": new_cash, "sector": sector, "sleeve": sleeve,
            "coverage": status, "checks": checks, "verdict": verdict, "level": worst, "risk": risk_out,
            "note": "Paper only. Keel never places orders."}
