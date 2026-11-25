"""Data when Yahoo's signed-in endpoints refuse (they do from cloud hosts such as Render): the public edition's
screener, heat map and company profiles, rebuilt from free sources.

- Prices, 52-week range, beta, volume, market cap: Yahoo's chart and shares data (open endpoints that still work).
- Fundamentals: each company's latest annual report on SEC. US GAAP filers come from SEC "frames" (one download per
  line item covers every filer); foreign filers (20-F / 40-F, often IFRS in their own currency) come one company at a
  time from Keel's SEC statements, converted to dollars at today's FX rate.
- Profiles: SEC (name, industry, address, fiscal year), Wikipedia (summary), Wikidata (employees, website, HQ).
  Listings with no SEC filings (Tokyo, Hong Kong...) get prices, the chart's name and exchange, and Wikipedia only.
- Universe for the screener and MAP: the S&P 500, 400 and 600 (Wikipedia, with GICS sectors) plus US-listed
  foreign companies that file with SEC.
Ratios are from the latest annual report (not trailing twelve months), so they can lag Yahoo's by a few quarters.
"""
from __future__ import annotations

import io
import re
import logging
import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from . import market, sec
from .config import DATA_DIR

log = logging.getLogger("pmdesk.fallback")
WIKI_UA = {"User-Agent": "Keel market terminal (open-source research tool)"}
GICS_TO_YAHOO = {"Information Technology": "Technology", "Health Care": "Healthcare", "Financials": "Financial Services",
                 "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive", "Materials": "Basic Materials",
                 "Communication Services": "Communication Services", "Industrials": "Industrials", "Energy": "Energy",
                 "Utilities": "Utilities", "Real Estate": "Real Estate"}
_mem: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
STATE: dict[str, Any] = {"stage": "idle", "error": None, "built": None}


class NotReady(Exception):
    """The SEC table is still being built (first start after a deploy takes a few minutes)."""


def _memo(key: str, ttl: float, fn):
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _mem[key] = (time.time(), v)
    return v


def _disk(name: str, ttl: float, fn):
    """A pickle in data/ reused while fresh; rebuilt otherwise (the last good copy is kept if a rebuild fails)."""
    p = DATA_DIR / name
    if p.exists() and time.time() - p.stat().st_mtime < ttl:
        with p.open("rb") as f:
            return pickle.load(f)
    try:
        v = fn()
    except Exception:
        if p.exists():
            with p.open("rb") as f:
                return pickle.load(f)
        raise
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(v, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(p)
    return v


US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR".split())


def sic_sector(sic: int | None) -> str | None:
    """SEC industry code -> Yahoo-style sector (the screener's sector names)."""
    if not sic:
        return None
    for lo, hi, s in [(100, 999, "Consumer Defensive"), (1000, 1299, "Basic Materials"), (1300, 1399, "Energy"), (1400, 1499, "Basic Materials"),
                      (1500, 1799, "Industrials"), (2000, 2199, "Consumer Defensive"), (2200, 2399, "Consumer Cyclical"), (2400, 2499, "Basic Materials"),
                      (2500, 2599, "Consumer Cyclical"), (2600, 2699, "Basic Materials"), (2700, 2799, "Communication Services"),
                      (2800, 2829, "Basic Materials"), (2830, 2836, "Healthcare"), (2837, 2899, "Basic Materials"), (2900, 2999, "Energy"),
                      (3000, 3199, "Consumer Cyclical"), (3200, 3399, "Basic Materials"), (3400, 3569, "Industrials"), (3570, 3579, "Technology"),
                      (3580, 3599, "Industrials"), (3600, 3699, "Technology"), (3700, 3719, "Consumer Cyclical"), (3720, 3799, "Industrials"),
                      (3800, 3829, "Industrials"), (3830, 3851, "Healthcare"), (3852, 3999, "Consumer Cyclical"), (4000, 4799, "Industrials"),
                      (4800, 4899, "Communication Services"), (4900, 4999, "Utilities"), (5000, 5199, "Industrials"), (5200, 5399, "Consumer Cyclical"),
                      (5400, 5499, "Consumer Defensive"), (5500, 5999, "Consumer Cyclical"), (6000, 6499, "Financial Services"),
                      (6500, 6799, "Real Estate"), (7000, 7299, "Consumer Cyclical"), (7300, 7369, "Industrials"), (7370, 7379, "Technology"),
                      (7380, 7799, "Industrials"), (7800, 7999, "Communication Services"), (8000, 8099, "Healthcare"), (8100, 9999, "Industrials")]:
        if lo <= sic <= hi:
            return s
    return None


# ---------- prices (Yahoo chart data: open, works from cloud hosts) ----------
def price_stats(c: pd.Series, v: pd.Series | None = None, spy: pd.Series | None = None) -> dict[str, Any]:
    c = c.dropna()
    if len(c) < 2:
        return {}
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    hi, lo = float(c.max()), float(c.min())
    out: dict[str, Any] = {"last": last, "prev": prev, "chg_pct": last / prev - 1, "hi52": hi, "lo52": lo,
                           "chg52": last / float(c.iloc[0]) - 1, "from_high": last / hi - 1}
    if v is not None and len(v.dropna()):
        out["avgvol"] = float(v.dropna().iloc[-63:].mean())
    if spy is not None:
        r = pd.concat([c.pct_change(), spy.pct_change()], axis=1, join="inner").dropna()
        if len(r) > 60 and r.iloc[:, 1].var() > 0:
            out["beta"] = float(r.iloc[:, 0].cov(r.iloc[:, 1]) / r.iloc[:, 1].var())
    return out


def _spy() -> pd.Series:
    def run():
        h = yf.Ticker("SPY").history(period="1y", auto_adjust=True)
        s = h["Close"]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s
    return _memo("spy", 6 * 3600, run)


def one_price(t: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(price stats, chart metadata) for one symbol."""
    tk = yf.Ticker(market.yahoo_symbol(t))
    h = tk.history(period="1y", auto_adjust=True)
    meta = getattr(tk, "history_metadata", {}) or {}
    if h.empty:
        return {}, meta
    c = h["Close"]
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    try:
        spy = _spy()
    except Exception:
        spy = None
    return price_stats(c, h.get("Volume"), spy), meta


def market_cap(t: str) -> float | None:
    try:
        v = yf.Ticker(market.yahoo_symbol(t)).fast_info.market_cap
        return float(v) if v else None
    except Exception:
        return None


def fx_usd(cur: str | None) -> float | None:
    """Dollars per one unit of a currency, today (Yahoo chart)."""
    if not cur or cur == "USD":
        return 1.0
    minor = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
    if cur in minor:  # pence / cents
        base = fx_usd(minor[cur])
        return base / 100 if base else None

    def run():
        for sym, inv in ((f"{cur}USD=X", False), (f"{cur}=X", True)):
            try:
                h = yf.Ticker(sym).history(period="5d")
                if not h.empty:
                    x = float(h["Close"].dropna().iloc[-1])
                    return 1 / x if inv else x
            except Exception:
                continue
        return None
    return _memo("fx:" + cur, 12 * 3600, run)


# ---------- fundamentals ----------
KEYS = ["rev", "gp", "da", "ebit", "intexp", "ni", "eps", "shares", "cfo", "capex", "cash", "inv", "ca", "std", "cl", "ltd", "equity", "assets"]


def from_statements(st: dict[str, Any]) -> dict[str, Any] | None:
    """Latest and prior annual values from Keel's SEC statements (any filer, US GAAP or IFRS), in dollars."""
    if not st.get("ok"):
        return None
    rows = {r["key"]: r for s in st["statements"].values() for r in s}
    ends = st["periods"]["A"]
    if not ends:
        return None
    e1, e0 = ends[-1], (ends[-2] if len(ends) > 1 else None)
    fx = fx_usd(st.get("currency")) or None
    if fx is None:
        return None
    f: dict[str, Any] = {"end": e1, "currency": st.get("currency"), "standard": st.get("standard"), "fx": fx}
    for k in KEYS:
        r = rows.get(k)
        if not r:
            continue
        v1 = r["A"].get(e1)
        scale = 1.0 if k in ("shares",) else fx
        if v1 is not None:
            f[k] = v1 * scale
        if e0 and r["A"].get(e0) is not None and k in ("rev", "ni", "eps", "ebit", "da", "cfo", "capex"):
            f[k + "_p"] = r["A"][e0] * scale
    return f


def metrics(f: dict[str, Any] | None, mcap: float | None, px: dict[str, Any]) -> dict[str, Any]:
    """Screener fields (FIELDS keys; percents as whole numbers) from annual fundamentals, market cap and prices."""
    f = f or {}
    g = f.get
    m: dict[str, Any] = {"mcap": mcap, "price": px.get("last"), "beta": px.get("beta"), "avgvol": px.get("avgvol"),
                         "chg52": px["chg52"] * 100 if px.get("chg52") is not None else None}

    def div(a, b, pct=False):
        if a is None or not b:
            return None
        return a / b * (100 if pct else 1)
    # EBITDA = operating income + D&A; with no D&A tag, operating income alone (understates EBITDA: conservative)
    ebitda = g("ebit") + abs(g("da") or 0) if g("ebit") is not None else None
    ebitda_p = g("ebit_p") + abs(g("da_p") or 0) if g("ebit_p") is not None else None
    debt = (g("std") or 0) + (g("ltd") or 0) if (g("std") is not None or g("ltd") is not None) else (0.0 if g("assets") else None)
    cash = g("cash")
    ev = mcap + (debt or 0) - (cash or 0) if mcap else None
    fcf = g("cfo") - abs(g("capex")) if g("cfo") is not None and g("capex") is not None else None
    fcf_p = g("cfo_p") - abs(g("capex_p")) if g("cfo_p") is not None and g("capex_p") is not None else None
    m.update({
        "pe": div(mcap, g("ni")) if (g("ni") or 0) > 0 else None,
        "pb": div(mcap, g("equity")) if (g("equity") or 0) > 0 else None,
        "ps": div(mcap, g("rev")), "ev_ebitda": div(ev, ebitda) if (ebitda or 0) > 0 else None, "ev_sales": div(ev, g("rev")),
        "roe": div(g("ni"), g("equity"), True) if (g("equity") or 0) > 0 else None, "roa": div(g("ni"), g("assets"), True),
        "rotc": div(g("ebit"), (debt or 0) + (g("equity") or 0), True) if ((debt or 0) + (g("equity") or 0)) > 0 else None,
        "gm": div(g("gp"), g("rev"), True), "ebitdam": div(ebitda, g("rev"), True), "nm": div(g("ni"), g("rev"), True),
        "revg": (div(g("rev"), g("rev_p")) - 1) * 100 if g("rev") and (g("rev_p") or 0) > 0 else None,
        "epsg": (div(g("eps"), g("eps_p")) - 1) * 100 if g("eps") is not None and (g("eps_p") or 0) > 0 else None,
        "ebitdag": (div(ebitda, ebitda_p) - 1) * 100 if ebitda is not None and (ebitda_p or 0) > 0 else None,
        "fcfg": (div(fcf, fcf_p) - 1) * 100 if fcf is not None and (fcf_p or 0) > 0 else None,
        "nd_ebitda": div((debt or 0) - (cash or 0), ebitda) if (ebitda or 0) > 0 and debt is not None else (0.0 if debt == 0 else None),
        "debt_eq": div(debt, g("equity"), True) if (g("equity") or 0) > 0 and debt is not None else None,
        # Interest cover; with no interest tag, estimated at 5% a year on the debt (no debt = covered)
        "int_cov": div(ebitda, abs(g("intexp"))) if g("intexp") and ebitda is not None
        else 999.0 if debt == 0 else div(ebitda, debt * 0.05) if (debt and ebitda is not None) else None,
        "cur": div(g("ca"), g("cl")), "quick": div((g("ca") or 0) - (g("inv") or 0), g("cl")) if g("ca") is not None else None,
        "altz": None, "short": None, "insider": None, "divy": None, "peg": None,
        "_ebitda": ebitda, "_debt": debt, "_cash": cash, "_fcf": fcf, "_ev": ev,
    })
    # A margin above 100% means the revenue line found isn't the company's total (a segment, or a mismatched tag):
    # blank the revenue-based ratios rather than show nonsense.
    if any(m.get(k) is not None and m[k] > 100 for k in ("gm", "ebitdam", "nm")):
        for k in ("gm", "ebitdam", "nm", "ps", "ev_sales", "revg"):
            m[k] = None
    return m


# ---------- US GAAP frames (every filer, one request per line item and period) ----------
FRAME_TAGS: dict[str, tuple[bool, list[str]]] = {  # key: (instant?, us-gaap tags in order of preference)
    "rev": (False, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]),
    "gp": (False, ["GrossProfit"]), "ebit": (False, ["OperatingIncomeLoss"]),
    "da": (False, ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet",
                   "DepreciationAmortizationAndOther", "Depreciation"]),
    "intexp": (False, ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestPaidNet", "InterestCostsIncurred"]),
    "ni": (False, ["NetIncomeLoss", "ProfitLoss"]), "eps": (False, ["EarningsPerShareDiluted"]),
    "cfo": (False, ["NetCashProvidedByUsedInOperatingActivities"]), "capex": (False, ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    "cash": (True, ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    "inv": (True, ["InventoryNet"]), "ca": (True, ["AssetsCurrent"]), "cl": (True, ["LiabilitiesCurrent"]),
    "std": (True, ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]),
    "ltd": (True, ["LongTermDebtNoncurrent", "LongTermDebt"]),
    "equity": (True, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    "assets": (True, ["Assets"]),
}


def _frame(tag: str, unit: str, period: str) -> list[dict[str, Any]]:
    for attempt in range(3):
        try:
            r = requests.get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json", headers=sec._ua(), timeout=60)
            time.sleep(0.15)
            if r.status_code == 404:
                return []
            if r.status_code == 200:
                return r.json().get("data", [])
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return []


def frames_table() -> dict[int, dict[str, Any]]:
    """cik -> latest and prior annual US GAAP values (rebuilt weekly)."""
    def build():
        y = date.today().year
        years = [y, y - 1, y - 2]
        inst = [f"CY{y}Q3I", f"CY{y}Q2I", f"CY{y}Q1I", f"CY{y - 1}Q4I", f"CY{y - 1}Q3I"]
        dur: dict[int, dict[str, dict[int, float]]] = {}
        out: dict[int, dict[str, Any]] = {}
        STATE.update(stage="reading SEC frames (US GAAP)")
        for k, (instant, tags) in FRAME_TAGS.items():
            unit = "USD/shares" if k == "eps" else "USD"
            if instant:
                for tag in tags:
                    for per in inst:  # newest balance sheet wins
                        for d in _frame(tag, unit, per):
                            row = out.setdefault(int(d["cik"]), {})
                            if k not in row:
                                row[k] = float(d["val"])
            else:
                for tag in tags:
                    for yy in years:
                        for d in _frame(tag, unit, f"CY{yy}"):
                            slot = dur.setdefault(int(d["cik"]), {}).setdefault(k, {})
                            slot.setdefault(yy, float(d["val"]))
        for cik, ks in dur.items():
            row = out.setdefault(cik, {})
            anchor = ks.get("rev") or ks.get("ni") or {}
            if not anchor:
                continue
            y1 = max(anchor)
            row["end"] = str(y1)
            for k, vals in ks.items():
                if y1 in vals:
                    row[k] = vals[y1]
                if y1 - 1 in vals:
                    row[k + "_p"] = vals[y1 - 1]
        return out
    return _disk("fb_frames.pkl", 7 * 86400, build)


# ---------- universe ----------
def _sp_list(page: str) -> list[dict[str, str]]:
    r = requests.get(f"https://en.wikipedia.org/wiki/{page}", headers=WIKI_UA, timeout=30)
    r.raise_for_status()
    for t in pd.read_html(io.StringIO(r.text)):
        if "Symbol" in t.columns and "GICS Sector" in t.columns:
            return [{"t": str(x["Symbol"]).strip(), "name": str(x["Security"]), "sector": GICS_TO_YAHOO.get(str(x["GICS Sector"]), str(x["GICS Sector"])),
                     "gics": str(x["GICS Sector"]), "industry": str(x["GICS Sub-Industry"]), "src": page} for _, x in t.iterrows()]
    return []


def sp1500() -> list[dict[str, str]]:
    def build():
        out, seen = [], set()
        for page in ("List_of_S%26P_500_companies", "List_of_S%26P_400_companies", "List_of_S%26P_600_companies"):
            for x in _sp_list(page):
                if x["t"] not in seen:
                    seen.add(x["t"])
                    out.append(x)
        if len(out) < 1000:
            raise ValueError("S&P lists incomplete")
        return out
    return _disk("fb_sp1500.pkl", 7 * 86400, build)


def _tickers() -> dict[str, dict[str, Any]]:
    j = sec._get_json("https://www.sec.gov/files/company_tickers_exchange.json", "company_tickers_exchange.json", 7 * 86400) or {}
    f = j.get("fields", [])
    return {row[f.index("ticker")].upper(): {"cik": int(row[f.index("cik")]), "name": row[f.index("name")], "exchange": row[f.index("exchange")]}
            for row in j.get("data", [])}


def foreign_filers() -> list[dict[str, Any]]:
    """US-listed (NYSE / Nasdaq) companies that filed a 20-F or 40-F in the last 15 months."""
    def build():
        STATE.update(stage="listing foreign filers (SEC search)")
        ciks: set[int] = set()
        start = (date.today() - timedelta(days=450)).isoformat()
        for form in ("20-F", "40-F"):
            for offset in range(0, 3000, 100):
                r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"forms": form, "dateRange": "custom", "startdt": start,
                                 "enddt": date.today().isoformat(), "from": offset}, headers=sec._ua(), timeout=30)
                time.sleep(0.3)
                if r.status_code != 200:
                    break
                hits = r.json().get("hits", {}).get("hits", [])
                for h in hits:
                    for c in h["_source"].get("ciks", []):
                        ciks.add(int(c))
                if len(hits) < 100:
                    break
        tk = _tickers()
        return [{"t": t, "cik": v["cik"], "name": v["name"], "exchange": v["exchange"]} for t, v in tk.items()
                if v["cik"] in ciks and v["exchange"] in ("NYSE", "Nasdaq") and t.isalpha()]
    return _disk("fb_foreign_list.pkl", 7 * 86400, build)


def foreign_table(only: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """ticker -> fundamentals for foreign filers worth $300M+, one company at a time from SEC (weekly)."""
    def build():
        out: dict[str, dict[str, Any]] = {}
        keep = set(only or [])
        lst = [x for x in foreign_filers() if not keep or x["t"] in keep]
        for i, x in enumerate(lst):
            STATE.update(stage=f"reading foreign filers' annual reports ({i + 1} of {len(lst)})")
            cached = sec.CACHE / f"CIK{x['cik']:010d}.json"
            had = cached.exists()
            try:
                f = from_statements(sec.statements(x["t"]))
                if f:
                    out[x["t"]] = f
            except Exception as e:
                log.debug("foreign %s: %s", x["t"], e)
            finally:
                if not had:  # don't keep ~500 full filings on disk (a few MB each); FA re-fetches on demand
                    cached.unlink(missing_ok=True)
        return out
    return _disk("fb_foreign.pkl", 7 * 86400, build)


_foreign_busy = threading.Event()


def foreign_ready(only: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """The foreign table if it exists (even if a week old); otherwise start building it in the background and
    return nothing for now, so the first screen never waits on ~400 annual reports."""
    p = DATA_DIR / "fb_foreign.pkl"
    fresh = p.exists() and time.time() - p.stat().st_mtime < 7 * 86400
    if not fresh and not _foreign_busy.is_set():
        _foreign_busy.set()

        def run():
            try:
                foreign_table(only)
                _mem.pop("table", None)  # next request rebuilds the table with the foreign rows
            except Exception as e:
                log.warning("foreign table failed: %s", e)
            finally:
                _foreign_busy.clear()
        threading.Thread(target=run, daemon=True).start()
    if p.exists():
        with p.open("rb") as f:
            return pickle.load(f)
    return {}


def prices(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Price stats for many symbols from batches of daily closes (Yahoo chart data), refreshed every 6 hours."""
    def build():
        STATE.update(stage=f"downloading a year of prices for {len(tickers)} names")
        ysym = {t: market.yahoo_symbol(t) for t in tickers}
        spy = _spy()
        out: dict[str, dict[str, Any]] = {}
        syms = sorted(set(ysym.values()))
        for i in range(0, len(syms), 150):
            chunk = syms[i:i + 150]
            try:
                df = yf.download(chunk, period="1y", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="column")
            except Exception as e:
                log.warning("price batch failed: %s", e)
                continue
            if df.empty:
                continue
            for s in chunk:
                try:
                    c = df["Close"][s] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
                    v = df["Volume"][s] if isinstance(df.columns, pd.MultiIndex) else df["Volume"]
                except KeyError:
                    continue
                c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
                st = price_stats(c, v, spy)
                if st:
                    out[s] = st
        back = {y: t for t, y in ysym.items()}
        return {back.get(s, s): v for s, v in out.items()}
    return _disk("fb_prices.pkl", 6 * 3600, build)


def shares(tickers: list[str], px: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Shares in issue implied by Yahoo's market cap (handles ADR ratios and share classes); weekly."""
    def build():
        STATE.update(stage=f"reading market caps for {len(tickers)} names")
        out: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for t, mc in zip(tickers, ex.map(market_cap, tickers)):
                last = (px.get(t) or {}).get("last")
                if mc and last:
                    out[t] = mc / last
        return out
    return _disk("fb_shares.pkl", 7 * 86400, build)


def table() -> list[dict[str, Any]]:
    """Every name in the universe with prices, market cap and screener fields. Raises NotReady while building."""
    hit = _mem.get("table")
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    if not _lock.acquire(blocking=False):  # another thread is building: use the last table if there is one
        if hit:
            return hit[1]
        raise NotReady(STATE.get("stage") or "building")
    try:
        sp = sp1500()
        fr = frames_table()
        tk = _tickers()
        fl = foreign_filers()
        names = {x["t"]: x for x in sp}
        for x in fl:
            names.setdefault(x["t"], {"t": x["t"], "name": x["name"], "sector": None, "industry": None, "src": "foreign"})
        uni = list(names)
        px = prices(uni)
        sh = shares(uni, px)
        big = [x["t"] for x in fl if sh.get(x["t"]) and (px.get(x["t"]) or {}).get("last") and sh[x["t"]] * px[x["t"]]["last"] >= 3e8]
        ft = foreign_ready(big)
        rows = []
        for t, x in names.items():
            p = px.get(t)
            if not p:
                continue
            info = tk.get(t.replace(".", "-")) or tk.get(t) or {}
            f = ft.get(t) if x.get("src") == "foreign" else fr.get(info.get("cik"))
            if x.get("src") == "foreign" and not f:
                f = fr.get(info.get("cik"))  # a foreign filer that reports in US GAAP
            mcap = sh[t] * p["last"] if sh.get(t) else None
            m = metrics(f, mcap, p)
            rows.append({"t": t, "name": x.get("name") or info.get("name"), "sector": x.get("sector"), "industry": x.get("industry"),
                         "exchange": info.get("exchange"), "px": p, "m": m, "fy": (f or {}).get("end"), "foreign": x.get("src") == "foreign"})
        STATE.update(stage="ready", built=time.time(), error=None)
        _mem["table"] = (time.time(), rows)
        return rows
    except NotReady:
        raise
    except Exception as e:
        STATE.update(stage="failed", error=str(e)[:200])
        raise
    finally:
        _lock.release()


def passes(m: dict[str, Any], sector: str | None, c: dict[str, Any]) -> bool:
    if c.get("sector"):
        return sector in c["sector"]
    v = m.get(c.get("field"))
    if v is None:
        return False
    op, t = c.get("op"), c.get("value")
    if op == "btwn":
        return float(t[0]) <= v <= float(t[1])
    return {"gte": v >= t, "lte": v <= t, "gt": v > t, "lt": v < t}.get(op, True)


def screen(rows_spec: list[dict[str, Any]], sort_key: str, asc: bool) -> tuple[list[dict[str, Any]], int]:
    """Yahoo-screener-shaped quotes that pass every criterion, sorted like Yahoo would."""
    rows = [r for r in table() if all(passes(r["m"], r["sector"], c) for c in rows_spec)]
    key = sort_key if rows and sort_key in rows[0]["m"] else "mcap"
    have = sorted((r for r in rows if r["m"].get(key) is not None), key=lambda r: r["m"][key], reverse=not asc)
    rows = have + [r for r in rows if r["m"].get(key) is None]
    out = []
    for r in rows:
        p, m = r["px"], r["m"]
        out.append({"symbol": r["t"], "longName": r["name"], "exchange": r["exchange"], "quoteType": "EQUITY",
                    "regularMarketPrice": p.get("last"), "regularMarketChangePercent": p["chg_pct"] * 100 if p.get("chg_pct") is not None else None,
                    "marketCap": m.get("mcap"), "trailingPE": m.get("pe"), "forwardPE": None, "priceToBook": m.get("pb"),
                    "fiftyTwoWeekChangePercent": p["chg52"] * 100 if p.get("chg52") is not None else None,
                    "fiftyTwoWeekHigh": p.get("hi52"), "fiftyTwoWeekLow": p.get("lo52"), "dividendYield": None,
                    "_fb": {"sector": r["sector"], "industry": r["industry"], "beta": m.get("beta"), "nd_ebitda": m.get("nd_ebitda"),
                            "roe": m.get("roe"), "cur": m.get("cur"), "gm": m.get("gm"), "ebitdam": m.get("ebitdam"), "revg": m.get("revg"),
                            "ev_ebitda": m.get("ev_ebitda"), "summary": f"Fundamentals from the {r['fy'] or 'latest'} annual report on SEC."}})
    return out, len(out)


def heat_quotes() -> dict[str, dict[str, Any]]:
    """MAP: market cap and today's move for the universe (S&P 500 names among them)."""
    out = {}
    for r in table():
        p, m = r["px"], r["m"]
        if m.get("mcap"):
            out[market.yahoo_symbol(r["t"])] = {"mcap": m["mcap"], "chg": p.get("chg_pct"), "price": p.get("last"), "name": r["name"]}
    return out


def status() -> dict[str, Any]:
    return dict(STATE)


def warm() -> None:
    """Public edition: build the table in the background after start, then keep it fresh."""
    time.sleep(40)
    while True:
        try:
            table()
        except NotReady:
            pass
        except Exception as e:
            log.warning("fallback table failed: %s", e)
        time.sleep(1800)


# ---------- company profile (Yahoo-info shaped) ----------
def wiki(name: str) -> dict[str, Any]:
    """Wikipedia summary and Wikidata facts for a company name; empty when no page clearly matches."""
    if not name:
        return {}
    from .newsrange import short_name
    core = short_name(name).lower()
    first = next((w for w in name.replace(",", " ").split() if len(w) > 2 and w.lower() not in ("the", "inc", "corp", "holding", "holdings", "group")), name.split()[0]).lower()
    for attempt in range(3):  # Wikipedia answers bursts with a throttle page: back off and try again
        out = _wiki_once(name, core, first)
        if out is not None:
            return out
        time.sleep(2 * (attempt + 1))
    return {}


def _wiki_once(name: str, core: str, first: str) -> dict[str, Any] | None:
    """None = a request failed (try again); {} = no matching page."""
    try:
        s = requests.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": f"{name} company",
                         "srlimit": 3, "format": "json"}, headers=WIKI_UA, timeout=15).json()
        hits = [h["title"] for h in s.get("query", {}).get("search", [])]
        for title in hits:
            sm = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(title.replace(" ", "_")),
                              headers=WIKI_UA, timeout=15).json()
            ext = (sm.get("extract") or "").lower()
            word = lambda w, txt: bool(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", txt))  # noqa: E731
            if not ((core and word(core, ext[:300])) or (len(first) > 2 and word(first, title.lower()))):
                continue
            if not COMPANY.search(ext[:400]):  # "Toast" the bread, "Nidec" the river...: only a page about a business
                continue
            out = {"summary": sm.get("extract"), "wiki_url": (sm.get("content_urls") or {}).get("desktop", {}).get("page")}
            qid = sm.get("wikibase_item")
            if qid:
                wd = wikidata(qid) or wikidata(qid)  # one retry: Wikidata sometimes drops a request
                out.update(wd)
            return out
        return {}
    except Exception as e:
        log.debug("wiki %s: %s", name, e)
        return None


COMPANY = re.compile(r"\b(company|companies|corporation|conglomerate|manufacturer|maker|firm|retailer|bank|insurer|provider|operator|"
                     r"developer|producer|supplier|group|holding|brand|chain|airline|utility|trust|fund|publisher|platform|business)\b")


def wikidata(qid: str) -> dict[str, Any]:
    try:
        e = requests.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"},
                         headers=WIKI_UA, timeout=15).json()["entities"][qid]["claims"]
    except Exception:
        return {}

    def val(p):
        cs = e.get(p) or []
        best = sorted(cs, key=lambda c: c.get("rank") == "preferred", reverse=True)
        for c in best:
            dv = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
            if dv is not None:
                return dv
        return None
    out: dict[str, Any] = {}
    emp = e.get("P1128") or []
    if emp:  # newest count
        def when(c):
            q = (c.get("qualifiers") or {}).get("P585") or []
            return (q[0].get("datavalue", {}).get("value", {}).get("time") or "") if q else ""
        c = max(emp, key=when)
        amt = (c.get("mainsnak") or {}).get("datavalue", {}).get("value", {}).get("amount")
        out["employees"] = int(float(amt)) if amt else None
    w = val("P856")
    if isinstance(w, str):
        out["website"] = w
    ind = val("P452")
    ids = [x.get("id") for x in (val("P159"), val("P17")) if isinstance(x, dict) and x.get("id")]
    if isinstance(ind, dict) and ind.get("id"):
        ids_ind = ind["id"]
    else:
        ids_ind = None
    if ids:
        try:
            lab = requests.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": "|".join(ids + ([ids_ind] if ids_ind else [])), "props": "labels",
                               "languages": "en", "format": "json"}, headers=WIKI_UA, timeout=15).json()["entities"]
            names = [lab[i]["labels"].get("en", {}).get("value") for i in ids]
            hq = val("P159")
            if isinstance(hq, dict) and hq.get("id") == ids[0] and names[0] and not re.match(r"(?i)(headquarters|head office|tower|building)", names[0]):
                out["city"] = names[0]
            out["country"] = names[-1] if len(ids) > 1 or not out.get("city") else None
            if ids_ind:
                out["industry"] = (lab[ids_ind]["labels"].get("en", {}).get("value") or "").capitalize() or None
        except Exception:
            pass
    return out


def profile(t: str) -> dict[str, Any]:
    """A Yahoo-info-shaped profile without Yahoo's signed-in endpoints. Empty when nothing is known."""
    t = t.upper()
    px, meta = {}, {}
    try:
        px, meta = one_price(t)
    except Exception as e:
        log.debug("chart %s: %s", t, e)
    kind = (meta.get("instrumentType") or "EQUITY").upper()
    out: dict[str, Any] = {"quoteType": kind, "longName": meta.get("longName") or meta.get("shortName"), "shortName": meta.get("shortName"),
                           "fullExchangeName": meta.get("fullExchangeName") or meta.get("exchangeName"), "currency": meta.get("currency"),
                           "_source": "fallback"}
    if px:
        out.update({"currentPrice": px["last"], "regularMarketPrice": px["last"], "previousClose": px["prev"], "fiftyTwoWeekHigh": px["hi52"],
                    "fiftyTwoWeekLow": px["lo52"], "52WeekChange": px["chg52"], "beta": px.get("beta"), "averageVolume": px.get("avgvol")})
    if kind != "EQUITY":
        return out if out.get("longName") or px else {}
    mcap = market_cap(t)
    minor = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}.get(meta.get("currency") or "")
    out["marketCap"] = mcap / minor[1] if (mcap and minor) else mcap  # shown in pounds, not pence
    if minor:
        out["currency"] = minor[0]
    # SEC: profile and the latest annual report (US GAAP or IFRS, in dollars)
    f, sub = None, {}
    cik = sec.cik(t)
    from . import esef
    if not cik and esef.is_eu(t):  # EU / UK listing: annual reports in ESEF
        try:
            f = from_statements(sec.statements(t))
        except Exception as e:
            log.debug("esef %s: %s", t, e)
    if cik:
        try:
            from . import sec_events
            sub = sec_events._submissions(cik)
        except Exception:
            sub = {}
        try:
            f = from_statements(sec.statements(t))
        except Exception as e:
            log.debug("statements %s: %s", t, e)
    if sub:
        biz = (sub.get("addresses") or {}).get("business") or {}
        code = biz.get("stateOrCountry") or ""
        us = code in US_STATES
        out.update({"longName": out.get("longName") or (sub.get("name") or "").title(), "industry": (sub.get("sicDescription") or "").title().replace(", Nec", "").replace(" Nec", "") or None,
                    "sector": sic_sector(int(sub["sic"])) if str(sub.get("sic") or "").isdigit() else None,
                    "city": re.sub(r"^[\d\s-]+", "", biz.get("city") or "").title() or None, "state": code if us else None,
                    "country": "United States" if us else (biz.get("stateOrCountryDescription") or None),
                    "website": sub.get("website") or None, "lastFiscalYearEnd": sub.get("fiscalYearEnd")})
    for x in sp1500_safe():
        if x["t"] == t or market.yahoo_symbol(x["t"]) == t:
            out.update({"sector": x["sector"], "industry": x["industry"]})
            break
    w = wiki(out.get("longName") or "")
    if not w and out.get("longName"):  # long or accented legal names: retry with the plain short form
        from .esef import name_variants
        for alt in name_variants(out["longName"])[1:4]:
            w = wiki(alt)
            if w:
                break
    out.update({"longBusinessSummary": w.get("summary"), "fullTimeEmployees": w.get("employees"),
                "website": out.get("website") or w.get("website")})
    for k in ("city", "country", "industry"):
        if w.get(k) and (not out.get(k) or (k == "city" and not out.get("state"))):  # Wikidata's HQ beats a foreign mailing address
            out[k] = w[k]
    if f:
        m = metrics(f, mcap if (meta.get("currency") in (None, "USD")) else (mcap * (fx_usd(meta.get("currency")) or 0) or None), px)
        out.update({"trailingPE": m.get("pe"), "priceToBook": m.get("pb"), "priceToSalesTrailing12Months": m.get("ps"),
                    "enterpriseValue": m.get("_ev"), "enterpriseToEbitda": m.get("ev_ebitda"), "enterpriseToRevenue": m.get("ev_sales"),
                    "totalRevenue": f.get("rev"), "ebitda": m.get("_ebitda"), "netIncomeToCommon": f.get("ni"),
                    "revenueGrowth": m["revg"] / 100 if m.get("revg") is not None else None,
                    "earningsGrowth": m["epsg"] / 100 if m.get("epsg") is not None else None,
                    "grossMargins": m["gm"] / 100 if m.get("gm") is not None else None,
                    "operatingMargins": (f["ebit"] / f["rev"]) if f.get("ebit") is not None and f.get("rev") else None,
                    "ebitdaMargins": m["ebitdam"] / 100 if m.get("ebitdam") is not None else None,
                    "profitMargins": m["nm"] / 100 if m.get("nm") is not None else None,
                    "returnOnEquity": m["roe"] / 100 if m.get("roe") is not None else None,
                    "returnOnAssets": m["roa"] / 100 if m.get("roa") is not None else None,
                    "totalCash": m.get("_cash"), "totalDebt": m.get("_debt"), "debtToEquity": m.get("debt_eq"), "currentRatio": m.get("cur"),
                    "freeCashflow": m.get("_fcf"), "sharesOutstanding": (mcap / px["last"]) if mcap and px.get("last") else f.get("shares"),
                    "_basis": f"latest annual report ({f.get('end')}, {f.get('standard')}{', converted from ' + f['currency'] if f.get('currency') not in (None, 'USD') else ''})"})
    return out if out.get("longName") else {}


def sp1500_safe() -> list[dict[str, str]]:
    try:
        return sp1500()
    except Exception:
        return []

