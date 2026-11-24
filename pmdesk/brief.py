"""TODAY: a live, rule-based market brief tailored to the book. No AI service: every sentence is
built from numbers pulled in the same call (Yahoo 1-minute bars, the desk's own state, MKTS data).

It answers, in order: how is the tape moving (from the prior close and from the open, where in
the day's range), what is under the hood (breadth, sectors, rates, credit, VIX), what the close
needs to show, which of your names have relative strength, how your book is doing against its
beta, and what is next on the calendar. Cached 30 seconds.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from . import digest, market

ET = ZoneInfo("America/New_York")
TAPE = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("IWM", "Russell 2000"), ("DIA", "Dow"), ("RSP", "S&P equal-weight")]
GAUGES = [("^VIX", "VIX"), ("^TNX", "10Y yield"), ("HYG", "High yield"), ("DX-Y.NYB", "Dollar"), ("CL=F", "WTI"), ("GC=F", "Gold"), ("BTC-USD", "Bitcoin")]
SECTORS = [("XLK", "Tech"), ("XLF", "Financials"), ("XLV", "Health Care"), ("XLE", "Energy"), ("XLI", "Industrials"), ("XLY", "Discretionary"),
           ("XLP", "Staples"), ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real Estate"), ("XLC", "Communication"), ("SMH", "Semis")]

_cache: dict[str, Any] = {}
_lock = threading.Lock()


def _stats(daily: pd.Series, intra: pd.Series) -> dict[str, Any] | None:
    """Prior close, open, high, low, last, and the week before today, for one symbol."""
    d, i = daily.dropna(), intra.dropna()
    if i.empty and d.empty:
        return None
    if not i.empty:
        last_day = pd.Timestamp(i.index[-1]).tz_convert(ET).date() if pd.Timestamp(i.index[-1]).tzinfo else pd.Timestamp(i.index[-1]).date()
        today = i[[(pd.Timestamp(x).tz_convert(ET).date() if pd.Timestamp(x).tzinfo else pd.Timestamp(x).date()) == last_day for x in i.index]]
    else:
        last_day, today = pd.Timestamp(d.index[-1]).date(), pd.Series(dtype=float)
    dn = d[[pd.Timestamp(x).date() < last_day for x in d.index]]
    prev = float(dn.iloc[-1]) if len(dn) else None
    week_ago = float(dn.iloc[-6]) if len(dn) >= 6 else None
    last = float(today.iloc[-1]) if len(today) else float(d.iloc[-1])
    opn = float(today.iloc[0]) if len(today) else None
    hi = float(today.max()) if len(today) else None
    lo = float(today.min()) if len(today) else None
    out = {"last": last, "prev": prev, "open": opn, "high": hi, "low": lo,
           "chg": (last / prev - 1) if prev else None, "from_open": (last / opn - 1) if opn else None,
           "week": (prev / week_ago - 1) if (prev and week_ago) else None, "day": last_day.isoformat()}
    if hi is not None and lo is not None and hi > lo:
        out["range_pos"] = (last - lo) / (hi - lo)
    if prev and hi and hi > prev and last > prev:
        out["giveback"] = (hi - last) / (hi - prev)   # share of the day's best gain given back
    if prev and lo and lo < prev and last < prev:
        out["recovered"] = (last - lo) / (prev - lo)  # share of the day's worst loss recovered
    if len(today):
        out["path"] = [round(float(v), 4) for v in today.iloc[:: max(1, len(today) // 60)].tolist()]
    return out


def _fetch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    to_y = {s: market.yahoo_symbol(s) for s in symbols}
    ys = sorted(set(to_y.values()))
    daily = market._close_frame(yf.download(ys, period="15d", interval="1d", auto_adjust=False, progress=False, threads=True), ys)
    intra = market._close_frame(yf.download(ys, period="2d", interval="1m", auto_adjust=False, prepost=False, progress=False, threads=True), ys)
    out = {}
    for s, y in to_y.items():
        st = _stats(daily[y] if y in daily.columns else pd.Series(dtype=float), intra[y] if y in intra.columns else pd.Series(dtype=float))
        if st:
            out[s] = st
    return out


def _p(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def build(state: dict[str, Any], mk: dict[str, Any] | None) -> dict[str, Any]:
    now = datetime.now(ET)
    held = [p for p in state.get("positions", []) if p.get("shares")]
    watch = state.get("watchlist", [])
    names = sorted({p["ticker"] for p in held} | {w["ticker"] for w in watch if w.get("price")})
    data = _fetch([s for s, _ in TAPE + GAUGES + SECTORS] + names)
    spy = data.get("SPY") or {}
    session = state.get("session") or "closed"
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    mins_left = int((close_t - now).total_seconds() // 60) if session == "market" else None
    is_today = spy.get("day") == now.date().isoformat()

    # ---------- headline ----------
    chg, wk, rp = spy.get("chg"), spy.get("week"), spy.get("range_pos")
    if chg is None:
        headline = "No live S&P data yet."
    else:
        kind = ("Bounce day" if chg > 0.01 and (wk or 0) < -0.02 else "Rally" if chg > 0.01 else "Selloff" if chg < -0.01
                else "Flat tape")
        ctx = f" after a {_p(wk)} week" if wk is not None and abs(wk) >= 0.02 else ""
        where = ("trading near the day's high" if rp is not None and rp >= 0.75 else "trading near the day's low" if rp is not None and rp <= 0.25
                 else "mid-range on the day")
        when = (f"{mins_left // 60}h{mins_left % 60:02d}m to the close" if mins_left is not None and mins_left > 0
                else "after the close" if is_today else "last session")
        headline = f"{kind}{ctx}: S&P {_p(chg)}, {where}, {when}."

    # ---------- tape ----------
    tape = []
    for s, label in TAPE:
        t = data.get(s)
        if not t:
            continue
        bits = [f"{_p(t['chg'])} on the day"]
        if t.get("from_open") is not None:
            bits.append(f"{_p(t['from_open'])} from the open")
        if t.get("range_pos") is not None:
            bits.append(f"at {t['range_pos'] * 100:.0f}% of its range")
        if t.get("giveback") is not None and t["giveback"] >= 0.15:
            bits.append(f"gave back {t['giveback'] * 100:.0f}% of its best gain")
        if t.get("recovered") is not None and t["recovered"] >= 0.15:
            bits.append(f"recovered {t['recovered'] * 100:.0f}% of its worst loss")
        if t.get("week") is not None:
            bits.append(f"week before {_p(t['week'])}")
        tape.append({"sym": s, "label": label, "text": ", ".join(bits), "chg": t["chg"], "path": t.get("path"), "prev": t.get("prev")})
    rsp, spyc = (data.get("RSP") or {}).get("chg"), spy.get("chg")
    iwm = (data.get("IWM") or {}).get("chg")
    breadth = []
    if rsp is not None and spyc is not None:
        gap = rsp - spyc
        breadth.append(f"Equal-weight S&P {_p(rsp)} vs cap-weight {_p(spyc)}: "
                       + ("the move is narrow, led by the largest names." if gap < -0.004 else
                          "the average stock is doing better than the index, a broad move." if gap > 0.004 else "breadth is in line with the index."))
    if iwm is not None and spyc is not None:
        breadth.append(f"Small caps {_p(iwm)}: " + ("risk appetite is broadening." if iwm - spyc > 0.004 else "small caps lag, so this is not a full risk-on move." if iwm - spyc < -0.004 else "in line."))
    secs = sorted([(lab, data[s]["chg"], s) for s, lab in SECTORS if data.get(s) and data[s].get("chg") is not None], key=lambda x: -x[1])
    if secs:
        up = sum(1 for _, c, _ in secs if c > 0)
        breadth.append(f"{up} of {len(secs)} sector groups up. Leading: " + ", ".join(f"{l} {_p(c)}" for l, c, _ in secs[:3])
                       + ". Lagging: " + ", ".join(f"{l} {_p(c)}" for l, c, _ in secs[-3:]) + ".")
    gauges = []
    for s, label in GAUGES:
        g = data.get(s)
        if not g or g.get("chg") is None:
            continue
        if s == "^TNX":
            gauges.append({"label": label, "text": f"{g['last']:.2f}% ({(g['last'] - g['prev']) * 100:+.0f} bp)", "chg": -(g['last'] - g['prev'])})
        elif s == "^VIX":
            gauges.append({"label": label, "text": f"{g['last']:.1f} ({_p(g['chg'])})", "chg": -g["chg"]})
        else:
            gauges.append({"label": label, "text": f"{g['last']:,.2f} ({_p(g['chg'])})", "chg": g["chg"]})

    # ---------- into the close ----------
    close_read = []
    if chg is not None and spy.get("high") and spy.get("low"):
        hi, lo, op, pc = spy["high"], spy["low"], spy.get("open"), spy.get("prev")
        fo = spy.get("from_open")
        if fo is not None and op and pc and abs(chg) > 0.01:
            gap = op / pc - 1
            if abs(fo) < 0.003:
                close_read.append(f"All of today's move came at the open (gap {_p(gap)}); the tape has gone sideways since. "
                                  "Overnight buying, not steady buying through the session.")
            elif (fo < 0) == (chg > 0):
                close_read.append(f"Opened {_p(gap)} and has faded {_p(fo)} since: the gap is being sold into.")
            else:
                close_read.append(f"Opened {_p(gap)} and kept going {_p(fo)} from the open: buyers added through the session.")
        if chg > 0.003:
            if (rp or 0) >= 0.7:
                close_read.append("Buyers are holding the gains. A close near the high would say real demand came back, not just short covering.")
            elif (rp or 0) <= 0.35:
                close_read.append(f"The bounce is being sold: S&P has given back {(spy.get('giveback') or 0) * 100:.0f}% of its best gain. "
                                  "A close near the low would mark this as exit liquidity, holders using the rally to get out.")
            else:
                close_read.append("Mid-range. The tell is whether the index holds above its open into the last hour.")
        elif chg < -0.003:
            if (rp or 1) <= 0.3:
                close_read.append("Sellers in control, closing near the lows would carry pressure into tomorrow.")
            elif (rp or 0) >= 0.65:
                close_read.append(f"Dip being bought: S&P has recovered {(spy.get('recovered') or 0) * 100:.0f}% of its worst loss.")
            else:
                close_read.append("Down but mid-range; watch the open as the line.")
        else:
            close_read.append("Flat; no message from the tape yet.")
        close_read.append(f"S&P levels (SPY): prior close {pc:.2f}, open {op:.2f}, high {hi:.2f}, low {lo:.2f}, last {spy['last']:.2f}.")
        if wk is not None and wk < -0.03 and chg > 0:
            close_read.append("Don't read the index color alone. After a correction, leadership usually changes: watch which groups keep their gains into the close.")

    # ---------- relative strength in your names ----------
    beta = {p["ticker"]: p.get("beta") or 1.0 for p in held}
    rs = []
    for t in names:
        d = data.get(t)
        if not d or d.get("chg") is None or spyc is None:
            continue
        b = beta.get(t, 1.0)
        today_rs = d["chg"] - b * spyc
        week_rs = (d["week"] - b * wk) if (d.get("week") is not None and wk is not None) else None
        tag = ("leading both ways" if today_rs > 0.005 and (week_rs or 0) > 0.01 else
               "bounce only (fell more last week)" if today_rs > 0.005 and (week_rs or 0) < -0.01 else
               "held up last week, lagging today" if today_rs < -0.005 and (week_rs or 0) > 0.01 else
               "lagging both ways" if today_rs < -0.005 and (week_rs or 0) < -0.01 else "in line")
        rs.append({"ticker": t, "held": t in beta, "chg": d["chg"], "week": d.get("week"), "rs_today": today_rs, "rs_week": week_rs,
                   "range_pos": d.get("range_pos"), "giveback": d.get("giveback"), "tag": tag})
    rs.sort(key=lambda x: -(x["rs_today"] + (x["rs_week"] or 0)))

    # ---------- your book ----------
    a = state.get("account") or {}
    rp_b = (state.get("risk") or {}).get("portfolio") or {}
    bookb = rp_b.get("beta") or 1.0
    book = []
    if a.get("day_pct") is not None and spyc is not None:
        exp = bookb * spyc
        book.append(f"Book {_p(a['day_pct'], 2)} ({'+' if (a.get('day_pnl') or 0) >= 0 else '-'}${abs(a.get('day_pnl') or 0):,.2f}) vs "
                    f"{_p(exp, 2)} expected from its {bookb:.2f} beta: {'alpha' if a['day_pct'] >= exp else 'shortfall'} of {(a['day_pct'] - exp) * 100:+.2f} pts.")
    movers = sorted([p for p in held if p.get("day_pnl") is not None], key=lambda p: -p["day_pnl"])
    if movers:
        book.append("Driving it: " + ", ".join(f"{p['ticker']} {'+' if p['day_pnl'] >= 0 else '-'}${abs(p['day_pnl']):.2f}" for p in movers[:3])
                    + ". Dragging: " + ", ".join(f"{p['ticker']} {'+' if p['day_pnl'] >= 0 else '-'}${abs(p['day_pnl']):.2f}" for p in movers[-2:]) + ".")
    fading = [r["ticker"] for r in rs if r["held"] and (r.get("giveback") or 0) >= 0.4]
    if fading:
        book.append("Fading from their highs (gave back 40%+ of the day's gain): " + ", ".join(fading) + ".")
    acts = [x for x in state.get("actions", []) if x.get("state") == "open" and x.get("severity") == "act"]
    if acts:
        book.append(f"{len(acts)} ACT card(s) open: " + "; ".join(f"{x.get('ticker')} {x.get('title')}" for x in acts[:3]) + ".")

    # ---------- next ----------
    nxt = []
    today_s, tmr = now.date().isoformat(), (now.date() + timedelta(days=1)).isoformat()
    for e in ((mk or {}).get("economy") or {}).get("rows", []):
        if e.get("next") in (today_s, tmr):
            nxt.append(f"{'Today' if e['next'] == today_s else 'Tomorrow'}: {e['label']} (last {e.get('last', 0):.2f}{e.get('unit', '')}).")
    for x in state.get("earnings_upcoming", []):
        if x.get("date") and x["date"] <= (now.date() + timedelta(days=3)).isoformat():
            nxt.append(f"{x['ticker']} reports {x['date']} {x.get('timing') or ''} ({'held' if x.get('held') else 'watchlist'}).")
    for w in watch:
        if w.get("dist_pct") is not None and -0.02 <= w["dist_pct"] <= 0.02:
            nxt.append(f"{w['ticker']} is within 2% of its Buy-below ({_p(w['dist_pct'])}): trigger-hit refresh before any Buy.")

    # ---------- 1-minute digest: decisions, not description ----------
    try:
        dg = digest.build(state, {
            "spy": spy, "rsp": rsp, "iwm": iwm, "sectors": secs, "tnx": data.get("^TNX"), "vix": data.get("^VIX"),
            "hyg": data.get("HYG"), "rs": rs, "next_macro": [x for x in nxt if x.startswith(("Today:", "Tomorrow:"))]})
    except Exception as e:  # the descriptive brief still shows if a digest rule breaks
        dg = {"verdict": "", "items": [], "net": f"Digest unavailable: {e}"}

    return {"ok": True, "as_of": now.isoformat(), "session": session, "mins_to_close": mins_left, "headline": headline,
            "tape": tape, "breadth": breadth, "gauges": gauges, "close": close_read, "rs": rs[:14], "book": book, "next": nxt[:8],
            "digest": dg}


def get(state: dict[str, Any], mk: dict[str, Any] | None, fresh: bool = False, ttl: float = 30.0) -> dict[str, Any]:
    if not state.get("ready") or not state.get("positions"):
        # Right after a restart the book isn't loaded yet; a brief built now would be wrong and get cached.
        return {"ok": False, "error": "The desk is still loading the book; the brief appears in a few seconds."}
    with _lock:
        hit = _cache.get("v")
        if hit and not fresh and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            out = build(state, mk)
        except Exception as e:
            return hit[1] if hit else {"ok": False, "error": f"Brief failed: {e}"}
        _cache["v"] = (time.time(), out)
        return out
