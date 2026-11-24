"""1-minute digest: what matters right now and what it means for the book. Rule-based, no AI.

Every rule watches for one situation and stays silent unless it is happening; each line it writes
is a decision (act, don't, wait, watch) tied to your mandate, with a size or a level where one
applies. Lines are ranked by a score, so the most important thing is always first and the digest
reshapes itself as the day changes. Inputs are the brief's live market stats plus the desk state.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Sector ETF label (brief.SECTORS) -> GICS sector name used by the book and the caps.
ETF_TO_GICS = {"Tech": "Information Technology", "Semis": "Information Technology", "Financials": "Financials",
               "Health Care": "Health Care", "Energy": "Energy", "Industrials": "Industrials",
               "Discretionary": "Consumer Discretionary", "Staples": "Consumer Staples", "Utilities": "Utilities",
               "Materials": "Materials", "Real Estate": "Real Estate", "Communication": "Communication Services"}
KIND_ORDER = {"act": 0, "dont": 1, "wait": 2, "watch": 3, "note": 4}


def _p(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def _pct(x: float | None, d: int = 0) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def build(state: dict[str, Any], mkt: dict[str, Any]) -> dict[str, Any]:
    """mkt: {"spy", "rsp", "iwm", "sectors": [(label, chg, sym)], "tnx", "vix", "hyg", "rs": [...], "next_macro": [...]}"""
    items: list[dict[str, Any]] = []

    def add(kind: str, score: float, text: str, drill: str | None = None) -> None:
        items.append({"kind": kind, "score": score, "text": text, "drill": drill})

    spy = mkt.get("spy") or {}
    chg, fo, wk, rp = spy.get("chg"), spy.get("from_open"), spy.get("week"), spy.get("range_pos")
    gap = (spy["open"] / spy["prev"] - 1) if spy.get("open") and spy.get("prev") else None
    rsp, iwm = mkt.get("rsp"), mkt.get("iwm")
    secs = mkt.get("sectors") or []
    risk_on = chg is not None and chg > 0.01
    risk_off = chg is not None and chg < -0.01
    narrow = rsp is not None and chg is not None and rsp - chg < -0.004
    gap_only = chg is not None and fo is not None and gap is not None and abs(chg) > 0.01 and abs(fo) < 0.003
    fading = chg is not None and fo is not None and chg > 0.01 and fo < -0.004
    building = chg is not None and fo is not None and ((chg > 0.01 and fo > 0.004) or (chg < -0.01 and fo < -0.004))

    positions = [p for p in state.get("positions", []) if p.get("shares")]
    agg = state.get("aggregates") or {}
    caps = state.get("caps") or {}
    nav = (state.get("account") or {}).get("nav") or 0.0
    cash_w = agg.get("cash") or 0.0
    actions = [a for a in state.get("actions", []) if a.get("state") == "open"]
    watch = state.get("watchlist", [])

    # ---- verdict: one sentence built from whatever is true now ----
    if chg is None:
        verdict = "No live market data yet."
    else:
        parts = []
        size = "big " if abs(chg) >= 0.02 else ""
        base = ("bounce" if risk_on and (wk or 0) < -0.02 else "rally" if risk_on else
                "selloff" if risk_off else "quiet tape")
        parts.append(f"{size}{base}".capitalize() + (f" after a {_p(wk)} week" if wk is not None and abs(wk) >= 0.02 else ""))
        if abs(chg) > 0.005:
            parts.append("narrow, led by the largest names" if narrow else
                         "broad, the average stock is keeping up" if rsp is not None and rsp - chg > 0.004 else None)
            parts.append("all of it at the open" if gap_only else "being sold into since the open" if fading else
                         "building through the session" if building else None)
        if secs and abs(chg) > 0.005:
            lead = secs[0]
            if abs(lead[1] - chg) > 0.02:
                parts.append(f"{lead[0]} doing the lifting ({_p(lead[1])})" if chg > 0 else f"{secs[-1][0]} hit hardest ({_p(secs[-1][1])})")
        verdict = ", ".join(p for p in parts if p) + "."
        if abs(chg) >= 0.02 and (state.get("regime") or {}).get("short"):
            verdict += " One day doesn't change the regime call."

    # ---- ACT: open ACT cards (caps breached, triggers hit), with size and an execution note ----
    for a in actions:
        if a.get("severity") != "act":
            continue
        s = a.get("suggestion") or {}
        pre = ""
        if a.get("type") == "buy_below":
            pre = " Run the trigger-hit refresh first; if it passes:"
            if not s.get("dollars"):  # no starter size in the memo: use the desk's 2% default
                w = next((x for x in watch if x.get("ticker") == a.get("ticker")), {})
                if w.get("price") and nav:
                    s = {"side": "buy", "dollars": 0.02 * nav, "shares": 0.02 * nav / w["price"], "limit": w.get("buy_below")}
        size = ""
        if s.get("dollars"):
            size = f" {s.get('side', '').capitalize()} ${s['dollars']:,.0f}" + (f" ({s['shares']:.4f} sh)" if s.get("shares") else "")
            size += f", limit ${s['limit']:,.2f}." if s.get("limit") else "."
        how = ""
        if s.get("side") == "buy" and risk_on and gap_only:
            how = " On a gap-up day, use the limit; don't pay up into the open."
        elif s.get("side") == "sell" and risk_off:
            how = " Selling into a down day: use a limit near the prior close, not market."
        add("act", 100, f"{a.get('ticker') or 'Book'}: {a.get('title')}.{pre}{size}{how}", f"act:{a.get('id')}")

    # ---- caps getting close because of the move ----
    sec_cap = caps.get("sector") or 0.30
    sec_day: dict[str, float] = {}
    for p in positions:
        sec_day[p.get("sector") or ""] = sec_day.get(p.get("sector") or "", 0.0) + (p.get("day_pnl") or 0.0)
    for sec, w in (agg.get("sector") or {}).items():
        if sec in ("ETF", "Hedge", "Cash", "Unclassified"):
            continue
        cap = caps.get("consumer_discretionary") if sec == "Consumer Discretionary" else sec_cap
        if cap and cap - 0.04 <= w <= cap:
            moved = sec_day.get(sec, 0.0)
            why = f", up ${moved:,.0f} today" if moved > 1 else ""
            add("dont", 80, f"Don't add {sec}: {_pct(w)} of the {_pct(cap)} cap{why}. New money goes outside the sector.", f"sector:{sec}")
    name_cap = caps.get("single_name") or 0.15
    for p in positions:
        if p.get("sleeve") in ("etf", "hedge"):
            continue
        if name_cap * 0.85 <= (p.get("weight") or 0) <= name_cap:
            add("dont", 75, f"Don't add {p['ticker']}: {_pct(p['weight'], 1)} of the {_pct(name_cap)} single-name cap.", f"tkr:{p['ticker']}")

    # ---- chasing / buying weakness ----
    if risk_on and gap_only and spy.get("open"):
        add("dont", 70, f"Don't chase: S&P gapped {_p(gap)} and has gone nowhere since. "
                        f"A close below the open ({spy['open']:.2f} SPY) turns this into a fade.", "mkt:SPY")
    elif fading and spy.get("open"):
        add("dont", 72, f"The gap is being sold: S&P {_p(fo)} from the open. Don't buy strength into this; "
                        f"let triggers come to you.", "mkt:SPY")
    elif risk_off and (rp or 0) >= 0.65:
        add("watch", 55, f"Dip being bought: S&P recovered {_pct(spy.get('recovered'))} of its low. Names near triggers are worth a look.", "mkt:SPY")

    # ---- hedge sleeve: top up on red days, not green ones ----
    hedge = (agg.get("sleeve") or {}).get("hedge", 0.0)
    floor, target, hcap = caps.get("hedge_sleeve_floor", 0.07), caps.get("hedge_sleeve_target", 0.10), caps.get("hedge_sleeve_cap", 0.12)
    if hedge < target - 0.015:
        gap_usd = (target - hedge) * nav
        if risk_off:
            add("act" if hedge < floor else "watch", 85 if hedge < floor else 60,
                f"Hedge sleeve {_pct(hedge, 1)} vs {_pct(target)} target, and today is a red day: the right kind of day to add about ${gap_usd:,.0f} (DBMF / IAU entry rules still apply).", "sleeve:hedge")
        elif risk_on:
            add("wait", 45, f"Hedge sleeve {_pct(hedge, 1)} vs {_pct(target)} target: hedges lag on up days by design. Top up on a red day, not today.", "sleeve:hedge")
    elif hedge > hcap:
        add("act", 90, f"Hedge sleeve {_pct(hedge, 1)} is over its {_pct(hcap)} cap: trim back toward {_pct(target)}.", "sleeve:hedge")

    # ---- cash and triggers ----
    near = sorted([w for w in watch if w.get("dist_pct") is not None and 0 < w["dist_pct"] <= 0.05], key=lambda w: w["dist_pct"])
    if near:
        names = ", ".join(f"{w['ticker']} {_p(w['dist_pct'])}" for w in near[:4])
        if risk_off:
            add("watch", 65, f"Red day with {_pct(cash_w)} cash: watchlist names close to their Buy-below: {names}.", f"tkr:{near[0]['ticker']}")
        else:
            add("watch", 35, f"Closest to a Buy-below: {names}.", f"tkr:{near[0]['ticker']}")
    elif cash_w > 0.15 and not any(a.get("type") == "buy_below" for a in actions):
        nearest = min((w for w in watch if w.get("dist_pct") is not None and w["dist_pct"] > 0), key=lambda w: w["dist_pct"], default=None)
        if nearest:
            add("note", 25, f"{_pct(cash_w)} in cash and no trigger within 5%: nearest is {nearest['ticker']} at {_p(nearest['dist_pct'])}. "
                            "Patience is the position.", f"tkr:{nearest['ticker']}")

    # ---- your names: stock-specific moves worth a decision ----
    held = {p["ticker"]: p for p in positions}
    for r in mkt.get("rs") or []:
        p = held.get(r["ticker"])
        if not p or r.get("rs_today") is None:
            continue
        rs_t = r["rs_today"]
        if rs_t > 0.05 and (r.get("rs_week") or 0) < -0.02:
            add("dont", 50 + min(rs_t * 100, 20), f"Don't add {r['ticker']} on this bounce: {_p(r['chg'])} today after "
                                                   f"{_p(r.get('week'))} last week, a rebound, not leadership yet.", f"tkr:{r['ticker']}")
        elif rs_t > 0.05:
            add("note", 40, f"{r['ticker']} is leading ({_p(r['chg'])}, {rs_t * 100:+.0f} pts vs its beta). "
                            f"At {_pct(p.get('weight'), 1)} of the book, check it against its trim level, not just the tape.", f"tkr:{r['ticker']}")
        elif rs_t < -0.05:
            add("watch", 55 + min(-rs_t * 100, 20), f"{r['ticker']} {_p(r['chg'])} against the market ({rs_t * 100:+.0f} pts vs its beta). "
                                                     "Stock-specific: check the news and the thesis before the close.", f"tkr:{r['ticker']}")

    # ---- book vs beta, and why ----
    acct = state.get("account") or {}
    beta = ((state.get("risk") or {}).get("portfolio") or {}).get("beta") or 1.0
    if acct.get("day_pct") is not None and chg is not None and nav:
        alpha = acct["day_pct"] - beta * chg
        by_bucket: dict[str, float] = {}
        for p in positions:
            by_bucket[p.get("bucket") or p.get("sector") or "Other"] = by_bucket.get(p.get("bucket") or p.get("sector") or "Other", 0.0) + (p.get("day_pnl") or 0.0)
        ranked = sorted(by_bucket.items(), key=lambda kv: kv[1])
        if abs(alpha) >= 0.005 and ranked:
            src = ranked[0] if alpha < 0 else ranked[-1]
            add("note", 30 + min(abs(alpha) * 1000, 25),
                f"Book {_p(acct['day_pct'], 2)} vs {_p(beta * chg, 2)} expected from its {beta:.2f} beta "
                f"({alpha * 100:+.1f} pts), mostly {src[0]} (${src[1]:+,.0f}).", "risk:beta")
        lead = secs[0] if secs else None
        if lead and risk_on and lead[1] - chg > 0.03:
            gics = ETF_TO_GICS.get(lead[0])
            own = (agg.get("sector") or {}).get(gics, 0.0) if gics else 0.0
            if own < 0.08:
                add("note", 28, f"{lead[0]} led ({_p(lead[1])}) and you hold {_pct(own)} there: an expected lag, not a signal to chase.", None)

    # ---- rates and credit hitting your exposures ----
    tnx = mkt.get("tnx") or {}
    if tnx.get("last") is not None and tnx.get("prev") is not None:
        bp = (tnx["last"] - tnx["prev"]) * 100
        if abs(bp) >= 8:
            sens = [p["ticker"] for p in positions if p.get("sector") in ("Utilities", "Real Estate") or p.get("sleeve") == "income"]
            add("watch", 45, f"10Y {bp:+.0f} bp today" + (f": your rate-sensitive names are {', '.join(sens)}." if sens else "."), "mkt:^TNX")
    hyg = (mkt.get("hyg") or {}).get("chg")
    if hyg is not None and hyg <= -0.007:
        credit = [p["ticker"] for p in positions if p.get("sleeve") == "income"]
        add("watch", 50, f"High yield {_p(hyg)}: credit is weak today" + (f"; {', '.join(credit)} is your credit exposure." if credit else "."), "mkt:HYG")
    vix = mkt.get("vix") or {}
    if vix.get("last") and vix["last"] >= 25 and risk_on:
        add("note", 20, f"VIX still {vix['last']:.0f} even on an up day: the market is pricing big swings; keep position sizes at plan.", "mkt:^VIX")

    # ---- SEC filings on your names (last 2 days) ----
    held_set = {p["ticker"] for p in positions}
    cutoff = (date.today() - timedelta(days=2)).isoformat()
    for ev in state.get("sec_events", []):
        if ev["date"] < cutoff or ev["severity"] == "low" or ev["ticker"] not in held_set | {w["ticker"] for w in watch}:
            continue
        held_name = ev["ticker"] in held_set
        kind = "act" if ev["severity"] == "high" and held_name else "watch"
        add(kind, (92 if ev["severity"] == "high" else 58) + (5 if held_name else 0),
            f"{ev['ticker']} filed {ev['form']}: {ev['title'][:120]}. Read it against the thesis killers.", f"tkr:{ev['ticker']}")

    # ---- what's next that touches the book ----
    today = date.today()
    for e in state.get("earnings_upcoming", []):
        d = e.get("date")
        if d and d <= (today + timedelta(days=3)).isoformat():
            days = (date.fromisoformat(d) - today).days
            if e.get("held"):
                w = held.get(e["ticker"], {}).get("weight")
                im, priced = e.get("implied") or {}, ""
                if im.get("move"):
                    rel = im["move"] / im["hist"] if im.get("hist") else None
                    priced = (f"; options price {'up to ' if im.get('upper_bound') else ''}±{im['move'] * 100:.1f}%"
                              + (f" vs ±{im['hist'] * 100:.1f}% on past reports ({'rich' if rel > 1.2 else 'cheap' if rel < 0.8 else 'in line'})" if rel else "")
                              + (f", about {_pct(im['move'] * w, 2)} of the book" if w else ""))
                add("watch", 60 - days * 5, f"{e['ticker']} reports {'today' if days == 0 else 'tomorrow' if days == 1 else 'in ' + str(days) + ' days'}"
                                            f" ({_pct(w, 1)} of the book){priced}: decide now what you hold through it.", f"tkr:{e['ticker']}")
    for m in mkt.get("next_macro") or []:
        add("watch", 30, m, None)

    # ---- rank, trim, net ----
    items.sort(key=lambda x: (-x["score"], KIND_ORDER[x["kind"]]))
    items = items[:8]
    acts = [i for i in items if i["kind"] == "act"]
    if acts:
        tick = ", ".join(sorted({i["text"].split(":")[0] for i in acts}))
        net = f"Net: {len(acts)} action{'s' if len(acts) > 1 else ''} ({tick}). Everything else: hold."
    elif any(i["kind"] in ("dont", "watch") for i in items):
        net = "Net: no trades needed. Hold, and keep to the don'ts."
    else:
        net = "Nothing to do. The book is in line and no caps or triggers are near."
    return {"verdict": verdict, "items": items, "net": net}
