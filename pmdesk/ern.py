"""ERN: what the options market prices for the next earnings report, against past reactions and your thesis.

Implied move (term-structure method): the at-the-money implied volatility of the last expiry BEFORE the
report is the stock's normal day-to-day volatility. The first expiry AFTER the report carries that plus the
report itself. Event variance = T_after * (IV_after^2 - IV_before^2); expected absolute move =
sqrt(2/pi) * sqrt(event variance). With no expiry before the report, the after-expiry straddle is used and
flagged as an upper bound (it includes ordinary movement until expiry).

Robinhood is read-only here: option chains, contracts, quotes and earnings history. Nothing is ordered.
"""
from __future__ import annotations

import math
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from . import cockpit, market, rh_mcp, store

SQ = math.sqrt(2 / math.pi)  # expected |move| per unit of standard deviation
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# ---------- pure helpers ----------
def reaction_day(report: str, timing: str | None, days: list[str]) -> str | None:
    """First trading day whose close reflects the report: the report day for 'am', the next day otherwise."""
    if timing == "am":
        return next((d for d in days if d >= report), None)
    return next((d for d in days if d > report), None)


def past_moves(closes: pd.DataFrame, t: str, reports: list[dict[str, Any]], bench: str = "SPY") -> list[dict[str, Any]]:
    """Close-to-close move over each report's reaction day, raw and net of the market."""
    if t not in closes:
        return []
    s = closes[t].dropna()
    days = [d.strftime("%Y-%m-%d") for d in s.index]
    b = closes[bench].dropna() if bench in closes else None
    out = []
    for r in reports:
        rd = reaction_day(r["date"], r.get("timing"), days)
        if not rd:
            continue
        i = days.index(rd)
        if i == 0:
            continue
        mv = s.iloc[i] / s.iloc[i - 1] - 1
        mk = None
        if b is not None and pd.Timestamp(rd) in b.index:
            j = b.index.get_loc(pd.Timestamp(rd))
            mk = float(b.iloc[j] / b.iloc[j - 1] - 1) if j > 0 else None
        out.append({"date": r["date"], "timing": r.get("timing"), "reaction_day": rd, "move": float(mv),
                    "net": float(mv - mk) if mk is not None else None, "eps_est": r.get("est"), "eps_act": r.get("act")})
    return out


def pick_expiries(expiries: list[str], report: str, timing: str | None) -> tuple[str | None, str | None]:
    """(last expiry that settles before the report reaches the price, first expiry that includes it)."""
    # An expiry on the report day itself includes an 'am' report but not a 'pm' one.
    after = [e for e in expiries if e > report or (e == report and timing == "am")]
    before = [e for e in expiries if e not in after and e >= date.today().isoformat()]
    return (max(before) if before else None), (min(after) if after else None)


def implied_move(spot: float, iv_after: float, t_after: float, iv_before: float | None = None,
                 straddle_after: float | None = None, days_after: int = 0) -> dict[str, Any]:
    """Expected absolute earnings move as a fraction of spot. t_after in years; days_after = expiry minus report date.

    quality: high when the after-expiry is within a week of the report, medium within a month, low beyond
    (the event premium is diluted across ordinary volatility and the estimate gets noisy)."""
    quality = "high" if days_after <= 7 else "medium" if days_after <= 30 else "low"
    if iv_before is not None and iv_after > iv_before:
        ev = t_after * (iv_after ** 2 - iv_before ** 2)
        return {"move": SQ * math.sqrt(ev), "method": "term structure", "upper_bound": False, "quality": quality}
    if days_after <= 10 and straddle_after and spot:
        return {"move": straddle_after / spot, "method": "straddle", "upper_bound": True, "quality": quality}
    return {"move": None, "method": None, "upper_bound": False, "quality": quality,
            "note": "Options do not separate the report from normal movement yet (no event premium in the term structure). Check closer to the date."}


def thesis_read(spot: float, move: float, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where each thesis case sits against one report's priced range."""
    out = []
    for s in scenarios:
        v = s.get("value")
        if not v or not spot:
            continue
        gap = v / spot - 1
        out.append({"case": s["case"], "value": v, "gap": gap, "reports": abs(gap) / move if move else None,
                    "inside": abs(gap) <= move})
    return out


# ---------- data ----------
def _instruments(chain_id: str, exp: str, kind: str) -> list[dict[str, Any]]:
    rows, cur = [], None
    for _ in range(6):
        args = {"chain_id": chain_id, "expiration_dates": exp, "type": kind}
        if cur:
            args["cursor"] = cur
        with rh_mcp._lock:
            d = rh_mcp.call("get_option_instruments", args)
        rows += d.get("instruments", [])
        cur = d.get("next")
        if not cur:
            break
    return rows


def _atm(chain_id: str, exp: str, spot: float) -> dict[str, Any] | None:
    calls, puts = _instruments(chain_id, exp, "call"), _instruments(chain_id, exp, "put")
    strikes = sorted({float(c["strike_price"]) for c in calls} & {float(p["strike_price"]) for p in puts})
    if not strikes:
        return None
    k = min(strikes, key=lambda x: abs(x - spot))
    c = next(x for x in calls if float(x["strike_price"]) == k)
    p = next(x for x in puts if float(x["strike_price"]) == k)
    with rh_mcp._lock:
        q = rh_mcp.call("get_option_quotes", {"instrument_ids": [c["id"], p["id"]]})
    qs = {r["quote"]["instrument_id"]: r["quote"] for r in q.get("results", []) if r.get("quote")}
    qc, qp = qs.get(c["id"]), qs.get(p["id"])
    if not qc or not qp:
        return None
    ivs = [float(x["implied_volatility"]) for x in (qc, qp) if x.get("implied_volatility")]
    marks = [float(x.get("mark_price") or 0) for x in (qc, qp)]
    return {"expiry": exp, "strike": k, "iv": sum(ivs) / len(ivs) if ivs else None, "straddle": sum(marks),
            "spread": sum(float(x.get("ask_price") or 0) - float(x.get("bid_price") or 0) for x in (qc, qp)),
            "oi": sum(int(x.get("open_interest") or 0) for x in (qc, qp)), "updated": qc.get("updated_at")}


def load(t: str, hq, fresh: bool = False) -> dict[str, Any]:
    t = t.upper()
    hit = _cache.get(t)
    if hit and not fresh and time.time() - hit[0] < 1800:
        return hit[1]
    if not rh_mcp.has_session():
        return {"ok": False, "error": "Robinhood is not signed in; ERN needs its option quotes."}
    with rh_mcp._lock:
        er = rh_mcp.call("get_earnings_results", {"symbol": t})
    rows = er.get("results", [])
    today = date.today().isoformat()
    reps = [{"date": r["report"]["date"], "timing": r["report"].get("timing"), "verified": r["report"].get("verified"),
             "est": _num(r.get("eps", {}).get("estimate")), "act": _num(r.get("eps", {}).get("actual"))}
            for r in rows if (r.get("report") or {}).get("date")]
    nxt = next((r for r in reps if r["act"] is None and r["date"] >= today), None)
    past = [r for r in reps if r["act"] is not None]

    closes = market.history([t, "SPY"], "3y")
    spot = float(closes[t].dropna().iloc[-1]) if t in closes and len(closes[t].dropna()) else None
    q = market.quotes([t]).get(t) or {}
    spot = q.get("last") or spot
    moves = past_moves(closes, t, past)
    absm = [abs(m["move"]) for m in moves]
    hist = {"avg": sum(absm) / len(absm) if absm else None, "max": max(absm) if absm else None,
            "up": sum(1 for m in moves if m["move"] > 0), "n": len(moves)}

    out: dict[str, Any] = {"ok": True, "ticker": t, "spot": spot, "next": nxt, "past": moves, "hist": hist, "implied": None}
    if nxt and spot:
        with rh_mcp._lock:
            ch = rh_mcp.call("get_option_chains", {"underlying_symbol": t}).get("chains", [])
        chain = next((c for c in ch if c.get("symbol") == t), ch[0] if ch else None)
        if not chain:
            out["implied_note"] = "No listed options."
        else:
            b, a = pick_expiries(chain.get("expiration_dates", []), nxt["date"], nxt.get("timing"))
            la = _atm(chain["id"], a, spot) if a else None
            lb = _atm(chain["id"], b, spot) if b else None
            if la and la.get("iv"):
                ta = max((date.fromisoformat(a) - date.today()).days, 1) / 365
                gap = (date.fromisoformat(a) - date.fromisoformat(nxt["date"])).days
                im = implied_move(spot, la["iv"], ta, lb.get("iv") if lb else None, la["straddle"], gap)
                if im["move"] is None:
                    out["implied_note"] = im["note"]
                    out["legs"] = {"after": la, "before": lb}
                else:
                    out["implied"] = {**im, "after": la, "before": lb, "low": spot * (1 - im["move"]), "high": spot * (1 + im["move"]),
                                      "vs_hist": im["move"] / hist["avg"] if hist["avg"] else None, "days_after": gap}
            else:
                out["implied_note"] = "No usable at-the-money quotes for the expiry after the report."
    ck = cockpit.load(hq, t)
    if ck.get("ok") and out.get("implied"):
        out["thesis"] = thesis_read(spot, out["implied"]["move"], ck.get("scenarios") or [])
        out["killers"] = [k.get("text") or k.get("killer") for k in (ck.get("killers") or [])][:5]
    if out.get("implied"):
        store.kv_set(f"ern:{t}", {"date": nxt["date"], "move": out["implied"]["move"], "hist": hist["avg"],
                                  "upper_bound": out["implied"]["upper_bound"], "quality": out["implied"]["quality"], "asof": today})
    _cache[t] = (time.time(), out)
    return out


def _num(x: Any) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None
