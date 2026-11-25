"""MAP: market heat map. S&P 500 members grouped by GICS sector and sub-industry, sized by market cap, colored by
the move over the chosen period; or your own book sized by position value; or the watchlist.

Members and their sector / sub-industry: Wikipedia's S&P 500 constituents table (public, refreshed weekly, cached).
Market cap and today's move: Yahoo's screener (live). 1W / 1M / 3M / YTD / 1Y moves: one batch of daily closes,
refreshed every 6 hours. Rule-based; no AI.
"""
from __future__ import annotations

import io
import json
import time
from datetime import date
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from . import market, screener
from .config import DATA_DIR

PERIODS = ("1D", "1W", "1M", "3M", "YTD", "1Y")
MEMBERS_FILE = DATA_DIR / "heatmap_sp500.json"
_cache: dict[str, tuple[float, Any]] = {}


def _memo(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def members() -> list[dict[str, str]]:
    """S&P 500 constituents with GICS sector and sub-industry; the last good copy is used if Wikipedia is down."""
    if MEMBERS_FILE.exists() and time.time() - MEMBERS_FILE.stat().st_mtime < 7 * 86400:
        return json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
    try:
        r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                         headers={"User-Agent": "Keel portfolio terminal (personal use)"}, timeout=30)
        r.raise_for_status()
        t = pd.read_html(io.StringIO(r.text), attrs={"id": "constituents"})[0]
        rows = [{"t": str(x["Symbol"]).strip(), "name": str(x["Security"]), "sector": str(x["GICS Sector"]),
                 "industry": str(x["GICS Sub-Industry"])} for _, x in t.iterrows()]
        if len(rows) > 400:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            MEMBERS_FILE.write_text(json.dumps(rows), encoding="utf-8")
            return rows
    except Exception:
        pass
    return json.loads(MEMBERS_FILE.read_text(encoding="utf-8")) if MEMBERS_FILE.exists() else []


def quotes() -> dict[str, dict[str, Any]]:
    """Market cap and today's % move for every US-listed stock over $2B (Yahoo screener, paged)."""
    def run():
        try:
            return yahoo()
        except Exception:  # Yahoo refuses its screener from cloud hosts: Keel's own table (SEC + chart data)
            from . import fallback
            return fallback.heat_quotes()

    def yahoo():
        Q = yf.EquityQuery
        q = Q("and", [Q("eq", ["region", "us"]), Q("is-in", ["exchange", *screener.US_LISTED]), Q("gt", ["intradaymarketcap", 2e9])])
        out: dict[str, dict[str, Any]] = {}
        for page in range(12):
            r = screener._screen(query=q, sortField="intradaymarketcap", sortAsc=False, size=250, offset=page * 250)
            qs = r.get("quotes") or []
            for x in qs:
                out[x["symbol"]] = {"mcap": x.get("marketCap"), "chg": (x.get("regularMarketChangePercent") or 0) / 100,
                                    "price": x.get("regularMarketPrice"), "name": x.get("shortName") or x.get("longName")}
            if len(qs) < 250:
                break
        return out
    return _memo("quotes", 60, run)


def period_returns(symbols: list[str]) -> dict[str, dict[str, float]]:
    """1W / 1M / 3M / YTD / 1Y price moves from one batch of daily closes (yahoo spellings in, same out)."""
    key = "hist:" + str(len(symbols)) + ":" + ",".join(sorted(symbols))[:200]

    def run():
        df = yf.download(sorted(set(symbols)), period="1y", interval="1d", auto_adjust=True, progress=False, threads=True)
        closes = market._close_frame(df, sorted(set(symbols)))
        closes.index = pd.to_datetime(closes.index).tz_localize(None)
        out: dict[str, dict[str, float]] = {}
        ytd0 = closes[closes.index < pd.Timestamp(date(date.today().year, 1, 1))]
        for s in closes.columns:
            c = closes[s].dropna()
            if len(c) < 6:
                continue
            last = float(c.iloc[-1])
            r = {}
            for k, n in (("1W", 5), ("1M", 21), ("3M", 63), ("1Y", len(c) - 1)):
                if len(c) > n:
                    r[k] = last / float(c.iloc[-1 - n]) - 1
            y0 = ytd0[s].dropna() if s in ytd0 else pd.Series(dtype=float)
            if len(y0):
                r["YTD"] = last / float(y0.iloc[-1]) - 1
            out[s] = r
        return out
    return _memo(key, 6 * 3600, run)


def build(universe: str, period: str, snap: dict[str, Any]) -> dict[str, Any]:
    period = period if period in PERIODS else "1D"
    q = quotes()
    if universe == "book":
        pos = [p for p in snap.get("positions", []) if p.get("value")]
        items = [{"t": p["ticker"], "name": p.get("company") or p["ticker"], "sector": p.get("sector") or "Other",
                  "industry": p.get("bucket") or "Unassigned", "size": float(p["value"]), "chg1d": p.get("day_pct")} for p in pos]
        size_note = "Sized by position value; grouped by sector, then your exposure bucket."
    elif universe == "watch":
        items = [{"t": w["ticker"], "name": w.get("company") or w["ticker"], "sector": w.get("sector") or "Other",
                  "industry": w.get("theme") or w.get("bucket") or w.get("sector") or "Watchlist", "size": None, "chg1d": None}
                 for w in snap.get("watchlist", [])]
        size_note = "Sized by market cap."
    else:
        items = [{**m, "size": None, "chg1d": None} for m in members()]
        size_note = "S&P 500, sized by market cap; GICS sector and sub-industry."
    ysym = {it["t"]: market.yahoo_symbol(it["t"]) for it in items}
    hist = period_returns(list(ysym.values())) if period != "1D" else {}
    out = []
    for it in items:
        y = ysym[it["t"]]
        qq = q.get(y) or q.get(it["t"]) or {}
        size = it["size"] or qq.get("mcap")
        chg = (it["chg1d"] if it["chg1d"] is not None else qq.get("chg")) if period == "1D" else hist.get(y, {}).get(period)
        if not size:
            continue
        out.append({"t": it["t"], "name": qq.get("name") or it["name"], "sector": it["sector"], "industry": it["industry"],
                    "size": float(size), "chg": chg, "mcap": qq.get("mcap")})
    # Breadth for the header: share of names up, and the size-weighted move
    known = [x for x in out if x["chg"] is not None]
    tot = sum(x["size"] for x in known) or 1.0
    return {"ok": True, "universe": universe, "period": period, "items": out, "note": size_note,
            "breadth": {"up": sum(x["chg"] > 0 for x in known), "down": sum(x["chg"] < 0 for x in known),
                        "weighted": sum(x["size"] * x["chg"] for x in known) / tot}, "as_of": time.strftime("%H:%M")}
