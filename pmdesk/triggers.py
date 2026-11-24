"""Action Queue: rule-based triggers from MANDATE caps, watchlist Buy-below levels,
event-pod entries, price moves, earnings, digest flags and broker reconciliation."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

SEVERITY_ORDER = {"act": 0, "review": 1, "watch": 2}


def rh_link(ticker: str) -> str:
    return f"https://robinhood.com/stocks/{ticker}"


def _action(kind: str, severity: str, ticker: str | None, title: str, detail: str,
            suggestion: dict[str, Any] | None = None, source: str | None = None) -> dict[str, Any]:
    return {
        "id": f"{kind}:{ticker or 'book'}",
        "type": kind, "severity": severity, "ticker": ticker, "title": title, "detail": detail,
        "suggestion": suggestion, "source": source,
        "link": rh_link(ticker) if ticker else None,
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def build(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    rules = ctx["rules"]
    caps = rules["caps"]
    warn = caps.get("warn_ratio", 0.9)
    nav: float = ctx["nav"]
    book: list[dict[str, Any]] = ctx["positions"]
    agg = ctx["aggregates"]
    prices: pd.DataFrame | None = ctx.get("history")
    today: date = ctx["today"]
    actions: list[dict[str, Any]] = []
    held = {p["ticker"]: p for p in book}

    if nav <= 0:
        return actions

    # 1. Single-name cap
    for p in book:
        if p["sleeve"] in ("hedge", "etf"):
            continue
        cap = caps["single_name"]
        if p["weight"] > cap:
            excess = p["value"] - cap * nav
            sh = excess / p["price"] if p["price"] else None
            actions.append(_action("cap_name", "act", p["ticker"],
                                   f"Over the {_pct(cap)} single-name cap ({_pct(p['weight'])})",
                                   "MANDATE: breaches are actioned the moment they are legally actionable.",
                                   {"side": "sell", "shares": sh, "dollars": excess}))
        elif p["weight"] > cap * warn:
            actions.append(_action("cap_name", "watch", p["ticker"],
                                   f"Near the single-name cap ({_pct(p['weight'])} of {_pct(cap)})", ""))

    # 2. Sector cap (GICS; ETFs and hedges excluded)
    for sector, wt in agg["sector"].items():
        if sector in ("ETF", "Hedge", "Cash"):
            continue
        cap = caps["sector"]
        if wt > cap:
            actions.append(_action(f"cap_sector_{sector}", "act", None,
                                   f"{sector} over the {_pct(cap)} sector cap ({_pct(wt)})",
                                   f"Trim about ${(wt - cap) * nav:,.0f} across {sector} names."))
        elif wt > cap * warn:
            actions.append(_action(f"cap_sector_{sector}", "watch", None,
                                   f"{sector} near the sector cap ({_pct(wt)} of {_pct(cap)})", ""))

    # 3. Consumer Discretionary sleeve
    cd = agg["sector"].get("Consumer Discretionary", 0.0)
    if cd > caps["consumer_discretionary"]:
        actions.append(_action("cap_cd", "act", None,
                               f"Consumer Discretionary sleeve over {_pct(caps['consumer_discretionary'])} ({_pct(cd)})", ""))

    # 4. Hedge sleeve band
    hedge = agg["sleeve"].get("hedge", 0.0)
    if hedge > caps["hedge_sleeve_cap"]:
        actions.append(_action("hedge_sleeve", "act", None,
                               f"Hedge sleeve over its {_pct(caps['hedge_sleeve_cap'])} cap ({_pct(hedge)})", ""))
    elif hedge < caps["hedge_sleeve_floor"]:
        gap = (caps["hedge_sleeve_target"] - hedge) * nav
        actions.append(_action("hedge_sleeve", "review", None,
                               f"Hedge sleeve under target ({_pct(hedge)} vs ~{_pct(caps['hedge_sleeve_target'])})",
                               f"About ${gap:,.0f} to reach target. DBMF / IAU entry rules still apply."))

    # 5. Event pod cap
    pod = agg["sleeve"].get("event_pod", 0.0)
    if pod > caps["event_pod"]:
        actions.append(_action("event_pod_cap", "act", None,
                               f"Event-driven pod over {_pct(caps['event_pod'])} ({_pct(pod)})", ""))

    # 6. Beta hard-remove
    hard = rules["beta"]["hard_remove"]
    for p in book:
        b = p.get("beta")
        if b is None or p["sleeve"] in ("hedge", "etf"):
            continue
        if b > hard:
            override = ctx["beta_overrides"].get(p["ticker"])
            if override:
                continue
            actions.append(_action("beta", "act", p["ticker"],
                                   f"Beta {b:.2f} above the {hard} hard-remove line",
                                   f"Measured {rules['beta']['window']}. Exit, or write a dated override with a sunset "
                                   "(then add it to config/overrides.yaml)."))

    # 7. Watchlist Buy-below
    approach = rules["watchlist"]["approach_pct"]
    for n in ctx["watchlist"]:
        bb, px = n.get("buy_below"), n.get("price")
        if not bb or not px:
            continue
        is_held = n["ticker"] in held
        if px <= bb:
            sug = None
            if n.get("starter_pct"):
                dollars = n["starter_pct"] / 100 * nav
                sug = {"side": "buy", "dollars": dollars, "shares": dollars / px, "limit": bb,
                       "basis": f"Starter {n['starter_pct']:g}% from watchlist"}
            actions.append(_action("buy_below", "review" if is_held else "act", n["ticker"],
                                   f"At Buy-below: ${px:,.2f} vs ${bb:,.2f}" + (" (held: add?)" if is_held else ""),
                                   "Run the Gate 0 trigger-hit refresh before any Buy. "
                                   + (n.get("entry_criteria") or "")[:300], sug, n.get("thesis_path")))
        elif px <= bb * (1 + approach):
            actions.append(_action("buy_below", "watch", n["ticker"],
                                   f"Approaching Buy-below: ${px:,.2f}, {((px / bb) - 1) * 100:.1f}% above ${bb:,.2f}",
                                   "", None, n.get("thesis_path")))

    # 8. Event pod armed entries
    for e in ctx["event_pod"]:
        px = e.get("price")
        if e.get("status") == "armed" and e.get("entry_below") and px:
            if px <= e["entry_below"]:
                actions.append(_action("event_entry", "act", e["ticker"],
                                       f"Event pod entry armed and hit: ${px:,.2f} <= ${e['entry_below']:,.2f}",
                                       f"Entry rule: {e['entry_text']}. Check the trade file's conditions first.",
                                       None, e["path"]))
            elif px <= e["entry_below"] * (1 + approach):
                actions.append(_action("event_entry", "watch", e["ticker"],
                                       f"Event pod entry close: ${px:,.2f} vs ${e['entry_below']:,.2f}", "", None, e["path"]))

    # 9-10. Price moves and drawdown on held names
    mv = rules["moves"]
    for p in book:
        t = p["ticker"]
        if p.get("day_pct") is not None:
            # Flag only the part of the move the market doesn't explain (beta x benchmark).
            bench = ctx.get("bench_day_pct")
            expected = (p.get("beta") or 1.0) * bench if bench is not None else 0.0
            resid = p["day_pct"] - expected
            if abs(resid) >= mv["day_move_pct"]:
                actions.append(_action("day_move", "review", t,
                                       f"{'Up' if p['day_pct'] > 0 else 'Down'} {abs(p['day_pct']) * 100:.1f}% today "
                                       f"({resid * 100:+.1f}% vs market)",
                                       "Stock-specific move. Check the news and the thesis before reacting."))
        if prices is not None and t in prices.columns:
            s = prices[t].dropna()
            if len(s) >= 6 and p.get("price"):
                ref = float(s.iloc[-6])
                chg = p["price"] / ref - 1
                if chg <= -mv["five_day_drop_pct"]:
                    half = p["price"] + (ref - p["price"]) / 2
                    actions.append(_action("five_day_drop", "review", t,
                                           f"Down {abs(chg) * 100:.1f}% over 5 sessions",
                                           "Trim execution window rule: if a valuation or size trim is due and there is "
                                           f"no company news, use a limit sell at ${ref:,.2f} (pre-drop) or ${half:,.2f} "
                                           "(halfway back), not a market order.",
                                           {"side": "sell", "limit": half, "limit_alt": ref}))
        if p.get("avg_cost") and p.get("price") and p["price"] / p["avg_cost"] - 1 <= -mv["drawdown_from_cost"]:
            actions.append(_action("drawdown", "review", t,
                                   f"{(1 - p['price'] / p['avg_cost']) * 100:.0f}% below cost",
                                   "Thesis check: is this a fluctuation or a permanent-loss signal (Marks test)?",
                                   None, p.get("thesis_path")))
        elif prices is not None and t in prices.columns and p.get("price") and mv.get("drawdown_from_high"):
            hi = prices[t].dropna().iloc[-126:]  # about six months of sessions
            if len(hi) >= 20 and p["price"] / float(hi.max()) - 1 <= -mv["drawdown_from_high"]:
                actions.append(_action("drawdown_high", "review", t,
                                       f"{(1 - p['price'] / float(hi.max())) * 100:.0f}% below its 6-month high",
                                       "A winner giving back gains: has the thesis changed, or only the price? Re-check the value band.",
                                       None, p.get("thesis_path")))

    # 10b. Thesis tripwires (bear value, weighted or base value, your add-below level)
    for tw in ctx.get("tripwires") or []:
        for s in tw.get("status") or []:
            t, v, lvl = tw["ticker"], s["value"], s["level"]
            if s["state"] != "hit":
                continue
            if lvl == "review_at":
                actions.append(_action("trip_review", "act", t, f"At the thesis bear value (${v:,.2f})",
                                       "The market is pricing your downside case. Re-underwrite: has a thesis killer happened? "
                                       "If not, this can be an add; if yes, exit per the killer rule.", None, tw.get("source", {}).get("review_at")))
            elif lvl == "trim_above":
                actions.append(_action("trip_trim", "review", t, f"Above your trim level (${v:,.2f})",
                                       "Most of the underwritten upside is paid for. Trim toward target weight or raise the value with new evidence.",
                                       {"side": "sell"}, tw.get("source", {}).get("trim_above")))
            elif lvl == "add_below":
                actions.append(_action("trip_add", "watch", t, f"Below your add level (${v:,.2f})",
                                       "Your own add level. Check caps and the regime layer before adding.", {"side": "buy"}))

    # 11. Earnings
    horizon = rules["earnings_window_days"]
    watch_names = {n["ticker"] for n in ctx["watchlist"]}
    for t, e in ctx["earnings"].items():
        if not e or not e.get("date"):
            continue
        days = (date.fromisoformat(e["date"]) - today).days
        if 0 <= days <= horizon and (t in held or t in watch_names):
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            actions.append(_action("earnings", "review" if t in held else "watch", t,
                                   f"Earnings {when} ({e['date']}{', ' + e['timing'] if e.get('timing') else ''})",
                                   "Pre-print: confirm the KPIs that would change the thesis and the size you'd hold through it."))

    # 12. Digest flags
    lookback = rules["digest_lookback_days"]
    relevant = set(held) | {n["ticker"] for n in ctx["watchlist"]}
    seen: set[str] = set()
    for entry in ctx["digest"]:
        if (today - date.fromisoformat(entry["date"])).days > lookback:
            continue
        for f in entry["flags"]:
            for t in f["tickers"]:
                if t in relevant and t not in seen:
                    seen.add(t)
                    actions.append(_action("digest", "review" if t in held else "watch", t,
                                           f"Digest flag {entry['date']}", f["text"][:400], None, entry["path"]))

    # 12b. SEC filings on held and watchlist names (last 3 days)
    watch_set = {n["ticker"] for n in ctx["watchlist"]}
    for ev in ctx.get("sec_events", []):
        t = ev["ticker"]
        if ev["severity"] == "low" or (t not in held and t not in watch_set):
            continue
        sev = "act" if (ev["severity"] == "high" and t in held) else "review" if t in held else "watch"
        actions.append(_action(f"sec_{ev['acc']}", sev, t, f"{ev['form']} filed {ev['date']}: {ev['title'][:110]}",
                               "Read the filing and check it against the thesis killers.", None, None) | {"link": ev["url"]})

    # 13. Broker reconciliation
    for r in ctx.get("recon", []):
        actions.append(_action("recon", "review", r["ticker"], r["title"], r["detail"]))

    # 14. Data warnings
    for wmsg in ctx.get("warnings", [])[:5]:
        actions.append(_action("data_" + hashlib.md5(wmsg.encode()).hexdigest()[:8], "watch", None, "Data check", wmsg))

    # Apply acknowledgements and snoozes
    states = ctx.get("states", {})
    now = datetime.now(timezone.utc).isoformat()
    for a in actions:
        st = states.get(a["id"])
        a["state"] = "open"
        if st and st.get("until") and st["until"] > now:
            a["state"] = st["state"]
            a["until"] = st["until"]

    actions.sort(key=lambda a: (a["state"] != "open", SEVERITY_ORDER[a["severity"]], a["type"], a["ticker"] or ""))
    return actions
