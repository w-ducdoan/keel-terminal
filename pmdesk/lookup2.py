"""Search beyond tickers, and history for metrics that are not prices.

  eco_search(q)       FRED series search (any economic series: "core cpi", "jobless claims", "mortgage rate")
  index_proxies(q)    licensed indexes (Bloomberg / Barclays Aggregate, MSCI, ...) -> the funds that track them
  short_history(t)    FINRA's twice-monthly short interest for an exchange-listed stock (shares short, days to
                      cover, average volume), from June 2021; short % of float uses today's float (labeled)
Rule-based; no AI.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

from . import market

_cache: dict[str, tuple[float, Any]] = {}


def _memo(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


# ---------- economic data ----------
SHORTEN = [("Consumer Price Index for All Urban Consumers: ", "CPI: "), ("Consumer Price Index", "CPI"),
           ("All Items Less Food and Energy", "Core (ex food & energy)"), ("All Items", "All items"),
           (" in U.S. City Average", ""), ("Personal Consumption Expenditures", "PCE"), ("Producer Price Index", "PPI"),
           ("Gross Domestic Product", "GDP"), ("Federal Reserve Bank of ", "Fed "), ("Market Yield on U.S. Treasury Securities at ", "Treasury yield, "),
           (" Constant Maturity, Quoted on an Investment Basis", ""), ("Seasonally Adjusted", "SA"), ("Not Seasonally Adjusted", "NSA")]


def short_title(t: str) -> str:
    for a, b in SHORTEN:
        t = t.replace(a, b)
    return t.strip()

def eco_search(q: str, limit: int = 8) -> list[dict[str, Any]]:
    key = market.fred_key()
    q = q.strip()
    if not key or len(q) < 3:
        return []

    def run():
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/search", params={
                "search_text": q, "api_key": key, "file_type": "json", "order_by": "popularity", "sort_order": "desc", "limit": limit}, timeout=15)
            rows = r.json().get("seriess", []) if r.ok else []
        except Exception:
            return []
        return [{"id": s["id"], "title": short_title(s["title"]), "full_title": s["title"], "freq": s.get("frequency_short"), "units": s.get("units_short"),
                 "sa": s.get("seasonal_adjustment_short"), "last": s.get("observation_end"), "popularity": s.get("popularity")}
                for s in rows if s.get("observation_end", "") >= (date.today() - timedelta(days=800)).isoformat()]  # skip discontinued
    return _memo("eco:" + q.lower(), 600, run)


# ---------- indexes you can only see through funds ----------
# (words to match, display name, symbols to offer; ^ = Yahoo index, others are ETFs that track it)
INDEX_PROXIES = [
    (("aggregate", "agg ", "barclays agg", "bloomberg agg", "us agg", "lbustruu", "core bond"), "Bloomberg US Aggregate Bond Index", ["AGG", "BND"]),
    (("high yield", "junk", "hy corporate"), "Bloomberg US Corporate High Yield Index", ["HYG", "JNK"]),
    (("investment grade", "ig corporate", "us corporate"), "Bloomberg US Corporate (investment grade) Index", ["LQD", "VCIT"]),
    (("treasury index", "us treasury", "govt bond"), "Bloomberg US Treasury Index", ["GOVT", "IEF", "TLT"]),
    (("tips", "inflation protected", "inflation-linked"), "Bloomberg US TIPS Index", ["TIP", "SCHP"]),
    (("municipal", "muni"), "Bloomberg Municipal Bond Index", ["MUB"]),
    (("emerging market", "msci em"), "MSCI Emerging Markets Index", ["EEM", "IEMG"]),
    (("eafe", "developed market", "msci efa"), "MSCI EAFE Index", ["EFA", "IEFA"]),
    (("msci world",), "MSCI World Index", ["URTH"]),
    (("acwi", "all country"), "MSCI ACWI Index", ["ACWI"]),
    (("russell 2000", "small cap"), "Russell 2000", ["^RUT", "IWM"]),
    (("russell 1000",), "Russell 1000", ["^RUI", "IWB"]),
    (("nasdaq 100", "ndx"), "Nasdaq-100", ["^NDX", "QQQ"]),
    (("s&p 500", "sp500", "spx"), "S&P 500", ["^GSPC", "SPY"]),
    (("dow jones", "djia"), "Dow Jones Industrial Average", ["^DJI", "DIA"]),
    (("equal weight",), "S&P 500 Equal Weight", ["RSP"]),
    (("commodity index", "bloomberg commodity", "bcom"), "Bloomberg Commodity Index", ["DBC", "BCI"]),
    (("dollar index", "dxy"), "US Dollar Index", ["DX-Y.NYB", "UUP"]),
    (("volatility", "vix"), "Cboe Volatility Index", ["^VIX"]),
    (("gold",), "Gold", ["GC=F", "GLD"]),
    (("crude", "oil", "wti"), "WTI crude oil", ["CL=F", "USO"]),
    (("bitcoin",), "Bitcoin", ["BTC-USD", "IBIT"]),
]


def index_proxies(q: str) -> list[dict[str, Any]]:
    s = " " + q.lower().strip() + " "
    out = []
    for words, name, syms in INDEX_PROXIES:
        if any(w in s for w in words):
            for sym in syms:
                out.append({"symbol": sym, "index": name, "proxy": not sym.startswith("^") and "=" not in sym and sym != "DX-Y.NYB"})
    return out[:6]


# ---------- short interest history (FINRA) ----------
def short_history(t: str) -> list[dict[str, Any]]:
    """Twice-monthly consolidated short interest for an exchange-listed stock, oldest first."""
    sym = t.upper().replace("-", ".")

    def run():
        r = requests.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest",
                          json={"limit": 1000, "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": sym}],
                                "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": "2021-01-01", "endDate": date.today().isoformat()}]},
                          headers={"Accept": "application/json"}, timeout=60)
        if not r.ok:
            return []
        rows = r.json() if r.text.strip().startswith("[") else []
        out = {}
        for x in rows:  # a revised report for the same date replaces the first
            out[x["settlementDate"]] = {"date": x["settlementDate"], "short": x.get("currentShortPositionQuantity"),
                                        "dtc": x.get("daysToCoverQuantity"), "adv": x.get("averageDailyVolumeQuantity")}
        return sorted(out.values(), key=lambda x: x["date"])
    return _memo("si:" + sym, 6 * 3600, run)


def short_series(t: str, what: str, float_shares: float | None) -> pd.Series:
    rows = short_history(t)
    idx = [pd.Timestamp(r["date"]) for r in rows]
    if what == "dtc":
        vals = [r["dtc"] for r in rows]
    elif what == "pct":
        vals = [(r["short"] / float_shares) if float_shares and r["short"] is not None else None for r in rows]
    else:
        vals = [r["short"] for r in rows]
    return pd.Series(vals, index=idx, dtype=float)
