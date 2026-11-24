"""Company data for any ticker, covered or not: the quick card, name search, and (later) the DES
Launchpad sections. Yahoo via yfinance; each piece has its own cache so a slow source never holds
up the rest, and a failure keeps the last good copy.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yfinance as yf

from . import market

log = logging.getLogger("pmdesk.equity")
TICKER = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")
US_EXCH = {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "BTS", "NYS", "NAS", "PNK", "OQX"}

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


_key_locks: dict[str, threading.Lock] = {}


def cached(key: str, ttl: float, build: Callable[[], Any]) -> Any:
    """TTL cache with one build per key at a time: the DES page asks for the same profile from
    several panels at once, and parallel yfinance calls for one ticker can return partial data."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        klock = _key_locks.setdefault(key, threading.Lock())
    with klock:
        with _lock:  # another thread may have built it while this one waited
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        try:
            val = build()
        except Exception as e:  # keep the last good copy
            log.warning("%s failed: %s", key, e)
            if hit:
                return hit[1]
            raise
        with _lock:
            _cache[key] = (time.time(), val)
        return val


def _num(x: Any) -> float | None:
    try:
        f = float(x)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def info(t: str) -> dict[str, Any]:
    """Yahoo's company profile and valuation snapshot (slow-moving: cached 6 hours)."""
    def full(i: dict[str, Any]) -> bool:  # a stock's real profile has a sector or a business summary
        return i.get("quoteType") not in (None, "EQUITY") or bool(i.get("sector") or i.get("longBusinessSummary"))

    def build() -> dict[str, Any]:
        # Yahoo sometimes answers with a stub (a name and a price, no profile): retry, and never cache a stub.
        best: dict[str, Any] = {}
        for k in range(3):
            i = yf.Ticker(market.yahoo_symbol(t)).info or {}
            if full(i):
                return i
            if i.get("longName") or i.get("shortName"):
                best = i
            time.sleep(0.6 * (k + 1))
        if best:
            return {**best, "_partial": True}
        raise ValueError(f"Yahoo returned no profile for {t}")

    try:
        out = cached(f"info:{t}", 6 * 3600, build)
    except ValueError:
        return {}
    if out.get("_partial"):  # use it now, ask again next time
        with _lock:
            _cache.pop(f"info:{t}", None)
    return out


def quote(t: str) -> dict[str, Any]:
    """Last price and prior close (cached 60 seconds)."""
    def build() -> dict[str, Any]:
        fi = yf.Ticker(market.yahoo_symbol(t)).fast_info
        last, prev = _num(fi.last_price), _num(fi.previous_close)
        return {"last": last, "prev": prev, "chg": (last - prev) if last and prev else None,
                "chg_pct": (last / prev - 1) if last and prev else None, "market_cap": _num(fi.market_cap),
                "currency": getattr(fi, "currency", None)}
    return cached(f"quote:{t}", 60, build)


def card(t: str, coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the ticker pop-up and the command-bar preview show."""
    t = t.upper().strip()
    if not TICKER.match(t):
        return {"ok": False, "error": "Not a ticker"}
    try:
        i = info(t)
    except Exception as e:
        return {"ok": False, "ticker": t, "error": f"No data: {e}"}
    try:
        q = quote(t)
    except Exception:
        q = {}
    name = i.get("longName") or i.get("shortName")
    if not name and not q.get("last"):
        return {"ok": False, "ticker": t, "error": f"Yahoo has no data for {t}."}
    summary = i.get("longBusinessSummary") or ""
    first = re.split(r"(?<=[.!?])\s+", summary.strip())
    return {
        "ok": True, "ticker": t, "name": name, "type": i.get("quoteType"), "exchange": i.get("fullExchangeName") or i.get("exchange"),
        "sector": i.get("sector") or i.get("category"), "industry": i.get("industry"), "country": i.get("country"),
        "website": i.get("website"), "employees": i.get("fullTimeEmployees"),
        "last": q.get("last") or _num(i.get("currentPrice")) or _num(i.get("regularMarketPrice")),
        "chg_pct": q.get("chg_pct"), "chg": q.get("chg"), "currency": q.get("currency") or i.get("currency"),
        "market_cap": q.get("market_cap") or _num(i.get("marketCap")), "pe": _num(i.get("trailingPE")), "fwd_pe": _num(i.get("forwardPE")),
        "ev_ebitda": _num(i.get("enterpriseToEbitda")), "div_yield": _num(i.get("dividendYield")), "beta": _num(i.get("beta")),
        "hi52": _num(i.get("fiftyTwoWeekHigh")), "lo52": _num(i.get("fiftyTwoWeekLow")),
        "target": _num(i.get("targetMeanPrice")), "rating": i.get("recommendationKey"), "analysts": i.get("numberOfAnalystOpinions"),
        "blurb": " ".join(first[:2])[:420], "summary": summary,
        "coverage": coverage,
    }


def lookup(q: str) -> list[dict[str, Any]]:
    """Name or ticker search (Yahoo). US listings first; equities and ETFs only."""
    q = q.strip()
    if not q or len(q) > 60:
        return []

    def build() -> list[dict[str, Any]]:
        res = yf.Search(q, max_results=10, news_count=0)
        out = []
        for x in res.quotes or []:
            if x.get("quoteType") not in ("EQUITY", "ETF", "INDEX", "MUTUALFUND", "FUTURE", "CURRENCY", "CRYPTOCURRENCY"):
                continue
            out.append({"symbol": x.get("symbol"), "name": x.get("longname") or x.get("shortname"), "exchange": x.get("exchDisp") or x.get("exchange"),
                        "type": x.get("quoteType"), "us": x.get("exchange") in US_EXCH, "sector": x.get("sectorDisp") or x.get("sector")})
        out.sort(key=lambda r: (not r["us"], r["type"] != "EQUITY"))
        us = [r for r in out if r["us"]]
        foreign = [r for r in out if not r["us"]][: 2 if us else 8]  # skip the pile of duplicate foreign listings
        return (us + foreign)[:8]
    return cached(f"search:{q.lower()}", 600, build)


def coverage_status(hq: Path, t: str, held: set[str], watch: set[str]) -> dict[str, Any]:
    """Where the name sits for the research folder: held, watchlist, passed, closed, or not covered."""
    from .security import coverage
    cov = coverage(hq, t)
    status = "held" if t in held else "watchlist" if t in watch else (cov or {}).get("bucket") or "none"
    return {"status": status, "folder": (cov or {}).get("folder")}


# ======================================================================================
# DES Launchpad sections. Each returns {"ok": True, ...} or {"ok": False, "error": ...}.
# ======================================================================================
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pandas as pd

BUY = {"buy", "strong buy", "outperform", "overweight", "positive", "accumulate", "sector outperform", "market outperform",
       "add", "conviction buy", "top pick", "speculative buy", "long-term buy", "moderate buy", "outperformer"}
SELL = {"sell", "strong sell", "underperform", "underweight", "negative", "reduce", "sector underperform",
        "market underperform", "moderate sell", "underperformer"}


def grade_class(g: str | None) -> str:
    g = (g or "").strip().lower()
    return "buy" if g in BUY else "sell" if g in SELL else "hold"


def _tk(t: str):
    return yf.Ticker(market.yahoo_symbol(t))


def _df(t: str, attr: str, ttl: float) -> pd.DataFrame:
    def build():
        v = getattr(_tk(t), attr)
        return v if isinstance(v, (pd.DataFrame, pd.Series)) else pd.DataFrame()
    return cached(f"{attr}:{t}", ttl, build)


def _f(x: Any) -> float | None:
    v = _num(x)
    return None if v is None or math.isinf(v) else v


def _row(df: pd.DataFrame, *names: str) -> pd.Series | None:
    for nm in names:
        if df is not None and not df.empty and nm in df.index:
            return pd.to_numeric(df.loc[nm], errors="coerce")
    return None


def _div(a: Any, b: Any) -> float | None:
    a, b = _f(a), _f(b)
    return a / b if a is not None and b not in (None, 0) else None


def _is_fund(i: dict[str, Any]) -> bool:
    return (i.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND", "INDEX", "FUTURE", "CURRENCY", "CRYPTOCURRENCY")


# ---------- DES: key stats ----------
def sec_des(t: str) -> dict[str, Any]:
    i = info(t)
    if not i:
        return {"ok": False, "error": f"Yahoo has no profile for {t}."}
    stats = {
        "Market cap": _f(i.get("marketCap")), "Enterprise value": _f(i.get("enterpriseValue")),
        "EV/EBITDA": _f(i.get("enterpriseToEbitda")), "EV/Revenue": _f(i.get("enterpriseToRevenue")),
        "P/E (TTM)": _f(i.get("trailingPE")), "P/E (fwd)": _f(i.get("forwardPE")), "PEG": _f(i.get("trailingPegRatio")),
        "P/B": _f(i.get("priceToBook")), "P/S": _f(i.get("priceToSalesTrailing12Months")),
        "Dividend yield %": _f(i.get("dividendYield")), "Payout ratio": _f(i.get("payoutRatio")), "Beta": _f(i.get("beta")),
        "Revenue (TTM)": _f(i.get("totalRevenue")), "Revenue growth": _f(i.get("revenueGrowth")), "Earnings growth": _f(i.get("earningsGrowth")),
        "Gross margin": _f(i.get("grossMargins")), "Operating margin": _f(i.get("operatingMargins")), "Net margin": _f(i.get("profitMargins")),
        "ROE": _f(i.get("returnOnEquity")), "ROA": _f(i.get("returnOnAssets")),
        "Cash": _f(i.get("totalCash")), "Debt": _f(i.get("totalDebt")), "Debt/Equity": _f(i.get("debtToEquity")),
        "Current ratio": _f(i.get("currentRatio")), "Free cash flow": _f(i.get("freeCashflow")),
        "Shares out": _f(i.get("sharesOutstanding")), "Float": _f(i.get("floatShares")),
        "Short % of float": _f(i.get("shortPercentOfFloat")), "Days to cover": _f(i.get("shortRatio")),
        "Avg volume (3M)": _f(i.get("averageVolume")), "52w high": _f(i.get("fiftyTwoWeekHigh")), "52w low": _f(i.get("fiftyTwoWeekLow")),
        "52w change": _f(i.get("52WeekChange")),
    }
    officers = [{"name": o.get("name"), "title": o.get("title"), "pay": _f(o.get("totalPay"))} for o in (i.get("companyOfficers") or [])[:6]]
    return {"ok": True, "name": i.get("longName"), "type": i.get("quoteType"), "fund": _is_fund(i), "summary": i.get("longBusinessSummary"),
            "sector": i.get("sector") or i.get("category"), "industry": i.get("industry"), "website": i.get("website"),
            "city": i.get("city"), "state": i.get("state"), "country": i.get("country"), "employees": i.get("fullTimeEmployees"),
            "fiscal_year_end": i.get("lastFiscalYearEnd"), "officers": officers, "stats": stats}


# ---------- ANR: analyst recommendations ----------
def rating_history(ud: pd.DataFrame, months: int = 24, today: date | None = None) -> list[dict[str, Any]]:
    """Month-end consensus rebuilt from broker actions: each firm's latest grade in the prior 12 months."""
    if ud is None or ud.empty:
        return []
    d = ud.copy()
    d.index = pd.to_datetime(d.index).tz_localize(None) if getattr(pd.to_datetime(d.index), "tz", None) is None else pd.to_datetime(d.index).tz_convert(None)
    d = d.sort_index()
    today = today or date.today()
    out = []
    for k in range(months - 1, -1, -1):
        end = (pd.Timestamp(today.year, today.month, 1) - pd.DateOffset(months=k)) + pd.offsets.MonthEnd(0)
        win = d[(d.index <= end) & (d.index > end - pd.DateOffset(months=12))]
        if win.empty:
            continue
        last = win.groupby("Firm").tail(1)
        cls = last["ToGrade"].map(grade_class)
        tgt = pd.to_numeric(last.get("currentPriceTarget"), errors="coerce")
        tgt = tgt[tgt > 0] if tgt is not None else pd.Series(dtype=float)
        out.append({"month": end.date().isoformat(), "buy": int((cls == "buy").sum()), "hold": int((cls == "hold").sum()),
                    "sell": int((cls == "sell").sum()), "target": float(tgt.mean()) if len(tgt) else None})
    return out


def sec_anr(t: str) -> dict[str, Any]:
    i = info(t)
    if _is_fund(i):
        return {"ok": False, "error": "Funds have no analyst coverage."}
    ud = _df(t, "upgrades_downgrades", 6 * 3600)
    rec = _df(t, "recommendations", 6 * 3600)
    firms = []
    if isinstance(ud, pd.DataFrame) and not ud.empty:
        d = ud.copy()
        d.index = pd.to_datetime(d.index)
        recent = d[d.index >= d.index.max() - pd.DateOffset(months=12)].sort_index()
        for firm, g in recent.groupby("Firm"):
            r = g.iloc[-1]
            firms.append({"firm": firm, "grade": r.get("ToGrade"), "from": r.get("FromGrade"), "class": grade_class(r.get("ToGrade")),
                          "action": r.get("Action"), "target": _f(r.get("currentPriceTarget")) or None, "prior_target": _f(r.get("priorPriceTarget")) or None,
                          "target_action": r.get("priceTargetAction"), "date": g.index[-1].date().isoformat()})
        firms.sort(key=lambda x: x["date"], reverse=True)
    trend = []
    if isinstance(rec, pd.DataFrame) and not rec.empty:
        for _, r in rec.iterrows():
            trend.append({"period": r.get("period"), "strong_buy": int(r.get("strongBuy", 0)), "buy": int(r.get("buy", 0)),
                          "hold": int(r.get("hold", 0)), "sell": int(r.get("sell", 0)), "strong_sell": int(r.get("strongSell", 0))})
    last = _f(i.get("currentPrice")) or _f(i.get("regularMarketPrice"))
    mean = _f(i.get("targetMeanPrice"))
    if not firms and not trend and mean is None:
        return {"ok": False, "error": "No analyst coverage on Yahoo."}
    return {"ok": True, "consensus": i.get("recommendationKey"), "score": _f(i.get("recommendationMean")), "analysts": i.get("numberOfAnalystOpinions"),
            "target_mean": mean, "target_median": _f(i.get("targetMedianPrice")), "target_high": _f(i.get("targetHighPrice")),
            "target_low": _f(i.get("targetLowPrice")), "last": last, "upside": _div(mean, last) - 1 if mean and last else None,
            "trend": trend, "history": rating_history(ud if isinstance(ud, pd.DataFrame) else None), "firms": firms[:40]}


# ---------- CACS: corporate actions and events ----------
def sec_cacs(t: str, filings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ev: list[dict[str, Any]] = []
    div = _df(t, "dividends", 12 * 3600)
    if isinstance(div, pd.Series):
        for d, v in div.tail(16).items():
            ev.append({"date": pd.Timestamp(d).date().isoformat(), "type": "Dividend", "text": f"Cash dividend ${float(v):.4f} per share"})
    spl = _df(t, "splits", 12 * 3600)
    if isinstance(spl, pd.Series):
        for d, v in spl.items():
            ev.append({"date": pd.Timestamp(d).date().isoformat(), "type": "Split", "text": f"Stock split {float(v):g}-for-1"})
    ed = _df(t, "earnings_dates", 6 * 3600)
    if isinstance(ed, pd.DataFrame) and not ed.empty:
        for d, r in ed.iterrows():
            rep, est = _f(r.get("Reported EPS")), _f(r.get("EPS Estimate"))
            txt = (f"Earnings: EPS {rep:.2f} vs {est:.2f} est ({_f(r.get('Surprise(%)')) or 0:+.1f}%)" if rep is not None and est is not None
                   else f"Earnings scheduled (est {est:.2f})" if est is not None else "Earnings")
            ev.append({"date": pd.Timestamp(d).date().isoformat(), "type": "Earnings", "text": txt, "future": rep is None})
    for f in filings or []:
        if f.get("form_type") in ("8-K", "10-K", "10-Q", "DEF 14A", "S-1", "SC 13D", "SC 13G"):
            ev.append({"date": f.get("date_filed"), "type": f.get("form_type"), "text": (f.get("description") or "")[:120],
                       "url": f"https://www.sec.gov/Archives/edgar/data/{f.get('cik')}/{(f.get('accession_number') or '').replace('-', '')}/{f.get('accession_number')}-index.htm" if f.get("cik") else None})
    ev.sort(key=lambda e: e["date"] or "", reverse=True)
    if not ev:
        return {"ok": False, "error": "No corporate actions found."}
    return {"ok": True, "events": ev[:80], "types": sorted({e["type"] for e in ev})}


# ---------- RV: relative valuation ----------
RV_FIELDS = [("mcap", "marketCap"), ("ev", "enterpriseValue"), ("ev_ebitda", "enterpriseToEbitda"), ("ev_rev", "enterpriseToRevenue"),
             ("pe", "trailingPE"), ("fwd_pe", "forwardPE"), ("pb", "priceToBook"), ("ps", "priceToSalesTrailing12Months"),
             ("peg", "trailingPegRatio"), ("rev_growth", "revenueGrowth"), ("eps_growth", "earningsGrowth"),
             ("gross_margin", "grossMargins"), ("op_margin", "operatingMargins"), ("net_margin", "profitMargins"),
             ("roe", "returnOnEquity"), ("roa", "returnOnAssets"), ("div_yield", "dividendYield"), ("beta", "beta")]
# Multiples where a lower number is cheaper: the premium / discount row is shown for these
RV_MULTIPLES = ("ev_ebitda", "ev_rev", "ev_fcf", "pe", "fwd_pe", "pb", "ps", "peg", "nd_ebitda")


def _rv_derived(ii: dict[str, Any]) -> dict[str, float | None]:
    """Columns Yahoo does not give directly: EV / FCF, FCF yield, net debt / EBITDA."""
    fcf, ev, mc, ebitda = _f(ii.get("freeCashflow")), _f(ii.get("enterpriseValue")), _f(ii.get("marketCap")), _f(ii.get("ebitda"))
    debt, cash = _f(ii.get("totalDebt")), _f(ii.get("totalCash"))
    return {"ev_fcf": ev / fcf if ev and fcf and fcf > 0 else None,
            "fcf_yield": fcf / mc if fcf is not None and mc else None,
            "nd_ebitda": ((debt or 0) - (cash or 0)) / ebitda if ebitda and ebitda > 0 and (debt is not None or cash is not None) else None}


def peers_for(t: str, overrides: dict[str, list[str]] | None = None, n: int = 10) -> list[str]:
    from . import store
    mine = store.kv_get(f"rv_peers:{t}")  # the list you edited on the RV panel wins
    if mine is not None:
        return [p.upper() for p in mine][:25]
    return peers_for_default(t, overrides, n)


def peers_for_default(t: str, overrides: dict[str, list[str]] | None = None, n: int = 10) -> list[str]:
    """The list without your edits: settings.yaml, else Yahoo's industry leaders."""
    if overrides and t in overrides:
        return [p.upper() for p in overrides[t]][:n]
    key = info(t).get("industryKey")
    if not key:
        return []

    def build():
        tc = yf.Industry(key).top_companies
        return [s for s in (list(tc.index) if tc is not None else []) if isinstance(s, str)]
    return [p for p in cached(f"industry:{key}", 24 * 3600, build) if p != t][:n]


def pe_history(t: str) -> list[list[Any]]:
    """Weekly trailing P/E on adjusted EPS: price / sum of the last four reported quarters."""
    ed = _df(t, "earnings_dates", 6 * 3600)
    if not isinstance(ed, pd.DataFrame) or ed.empty or "Reported EPS" not in ed:
        return []
    eps = pd.to_numeric(ed["Reported EPS"], errors="coerce").dropna()
    eps.index = pd.to_datetime(eps.index).tz_localize(None) if getattr(pd.to_datetime(eps.index), "tz", None) is None else pd.to_datetime(eps.index).tz_convert(None)
    eps = eps.sort_index()
    ttm = eps.rolling(4).sum().dropna()
    if ttm.empty:
        return []
    px = cached(f"wk5y:{t}", 12 * 3600, lambda: market._close_frame(yf.download(market.yahoo_symbol(t), period="6y", interval="1wk", auto_adjust=False, progress=False), [market.yahoo_symbol(t)]))
    if px.empty:
        return []
    s = px.iloc[:, 0].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None) if getattr(s.index, "tz", None) is None else s.index.tz_convert(None)
    out = []
    for d, p in s.items():
        prior = ttm[ttm.index <= d]
        if len(prior) and prior.iloc[-1] > 0:
            out.append([d.date().isoformat(), round(float(p) / float(prior.iloc[-1]), 2)])
    return out


def sec_rv(t: str, overrides: dict[str, list[str]] | None = None) -> dict[str, Any]:
    i = info(t)
    if _is_fund(i):
        return {"ok": False, "error": "Relative valuation is for operating companies."}
    peers = peers_for(t, overrides)
    names = [t] + peers
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = dict(zip(names, ex.map(_safe_info, names)))
    rows = []
    for s in names:
        ii = infos.get(s) or {}
        rows.append({"ticker": s, "name": ii.get("shortName") or ii.get("longName") or s, "self": s == t,
                     **{k: _f(ii.get(f)) for k, f in RV_FIELDS}, **_rv_derived(ii)})
    keys = [k for k, _ in RV_FIELDS] + ["ev_fcf", "fcf_yield", "nd_ebitda"]
    med = {}
    for k in keys:
        vals = sorted(v for r in rows if not r["self"] for v in [r[k]] if v is not None and (k not in RV_MULTIPLES or k == "nd_ebitda" or v > 0))
        med[k] = (vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2) if vals else None
    me = rows[0]
    prem = {k: (me[k] / med[k] - 1) if me.get(k) and med.get(k) and k in RV_MULTIPLES and med[k] > 0 else None for k in keys}
    hist = pe_history(t)
    from . import store
    src = ("your list" if store.kv_get(f"rv_peers:{t}") is not None else
           "settings.yaml" if overrides and t in overrides else "Yahoo industry leaders")
    return {"ok": True, "industry": i.get("industry"), "peer_source": src, "peers": peers,
            "default_peers": peers_for_default(t, overrides),
            "rows": rows, "median": med, "premium": prem, "pe_history": hist,
            "pe_note": "Trailing P/E on adjusted EPS (sum of the last four reported quarters, Yahoo)."}


# ---------- FA: financial analysis ----------
def _fa_table(inc: pd.DataFrame, bs: pd.DataFrame, cf: pd.DataFrame, quarterly: bool) -> dict[str, Any]:
    if inc is None or inc.empty:
        return {"periods": [], "groups": {}}
    cols = sorted(inc.columns)[-8 if quarterly else -5:]
    ann = 4 if quarterly else 1
    lag = 4 if quarterly else 1

    def r(df, *names):
        x = _row(df, *names)
        return x.reindex(cols) if x is not None else pd.Series(index=cols, dtype=float)
    rev, gp = r(inc, "Total Revenue", "Operating Revenue"), r(inc, "Gross Profit")
    ebitda, ebit = r(inc, "EBITDA", "Normalized EBITDA"), r(inc, "EBIT")
    op, pre = r(inc, "Operating Income", "Total Operating Income As Reported"), r(inc, "Pretax Income")
    ni, eps = r(inc, "Net Income Common Stockholders", "Net Income"), r(inc, "Diluted EPS")
    tax, shs = r(inc, "Tax Rate For Calcs"), r(inc, "Diluted Average Shares")
    eq, ta = r(bs, "Stockholders Equity", "Common Stock Equity"), r(bs, "Total Assets")
    cash, debt = r(bs, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"), r(bs, "Total Debt")
    netdebt = r(bs, "Net Debt")
    netdebt = netdebt.where(netdebt.notna(), debt - cash)
    ca, cl = r(bs, "Current Assets"), r(bs, "Current Liabilities")
    cfo, capex, fcf = r(cf, "Operating Cash Flow"), r(cf, "Capital Expenditure"), r(cf, "Free Cash Flow")
    fcf = fcf.where(fcf.notna(), cfo + capex)
    buyback, divs, sbc = r(cf, "Repurchase Of Capital Stock"), r(cf, "Cash Dividends Paid", "Common Stock Dividend Paid"), r(cf, "Stock Based Compensation")
    avg = lambda s: (s + s.shift(1)) / 2
    yoy = lambda s: (s / s.shift(lag) - 1).where(s.shift(lag).abs() > 0)

    def ser(s, pct=False):
        return [None if (v is None or pd.isna(v) or math.isinf(v)) else round(float(v) * (100 if pct else 1), 4) for v in s.tolist()]
    groups = {
        "Profitability": [("Return on equity %", ser(ni * ann / avg(eq), True)), ("Return on assets %", ser(ni * ann / avg(ta), True)),
                          ("Return on invested capital %", ser(ebit * (1 - tax.fillna(0.21)) * ann / avg(debt + eq), True)),
                          ("Gross margin %", ser(gp / rev, True)), ("EBITDA margin %", ser(ebitda / rev, True)), ("Operating margin %", ser(op / rev, True)),
                          ("Pretax margin %", ser(pre / rev, True)), ("Net margin %", ser(ni / rev, True))],
        "Growth": [("Revenue growth %", ser(yoy(rev), True)), ("EBITDA growth %", ser(yoy(ebitda), True)), ("Operating income growth %", ser(yoy(op), True)),
                   ("Net income growth %", ser(yoy(ni), True)), ("Diluted EPS growth %", ser(yoy(eps), True))],
        "Income statement": [("Revenue", ser(rev)), ("Gross profit", ser(gp)), ("EBITDA", ser(ebitda)), ("Operating income", ser(op)),
                             ("Pretax income", ser(pre)), ("Net income", ser(ni)), ("Diluted EPS", ser(eps))],
        "Balance sheet": [("Cash & ST investments", ser(cash)), ("Total debt", ser(debt)), ("Net debt", ser(netdebt)), ("Shareholders' equity", ser(eq)),
                          ("Net debt / EBITDA (x)", ser(netdebt / (ebitda * ann))), ("Debt / equity (x)", ser(debt / eq)), ("Current ratio (x)", ser(ca / cl))],
        "Cash flow": [("Operating cash flow", ser(cfo)), ("Capex", ser(capex)), ("Free cash flow", ser(fcf)), ("FCF margin %", ser(fcf / rev, True)),
                      ("FCF / net income (x)", ser(fcf / ni)), ("Buybacks", ser(buyback)), ("Dividends paid", ser(divs)), ("Stock-based comp", ser(sbc))],
        "Per share": [("Diluted EPS", ser(eps)), ("Book value / share", ser(eq / shs)), ("FCF / share", ser(fcf / shs)), ("Revenue / share", ser(rev / shs))],
    }
    return {"periods": [pd.Timestamp(c).date().isoformat() for c in cols],
            "groups": {g: [{"label": lbl, "values": v} for lbl, v in rows if any(x is not None for x in v)] for g, rows in groups.items()}}


def sec_fa(t: str) -> dict[str, Any]:
    i = info(t)
    if _is_fund(i):
        return {"ok": False, "error": "Funds have no company financials."}
    a = _fa_table(_df(t, "income_stmt", 12 * 3600), _df(t, "balance_sheet", 12 * 3600), _df(t, "cashflow", 12 * 3600), False)
    q = _fa_table(_df(t, "quarterly_income_stmt", 12 * 3600), _df(t, "quarterly_balance_sheet", 12 * 3600), _df(t, "quarterly_cashflow", 12 * 3600), True)
    if not a["periods"] and not q["periods"]:
        return {"ok": False, "error": "Yahoo has no financial statements for this name."}
    return {"ok": True, "annual": a, "quarterly": q, "currency": i.get("financialCurrency") or i.get("currency"),
            "note": "Yahoo statements. Quarterly returns are annualized; growth is year over year."}


# ---------- EE: earnings estimates ----------
PERIOD_NAMES = {"0q": "Current qtr", "+1q": "Next qtr", "0y": "Current FY", "+1y": "Next FY"}


def _tbl(df: Any) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out = []
    for idx, r in df.iterrows():
        out.append({"period": PERIOD_NAMES.get(str(idx), str(idx)), **{str(k): (_f(v) if not isinstance(v, str) else v) for k, v in r.items()}})
    return out


def sec_ee(t: str) -> dict[str, Any]:
    if _is_fund(info(t)):
        return {"ok": False, "error": "Funds have no earnings estimates."}
    eps, rev = _tbl(_df(t, "earnings_estimate", 6 * 3600)), _tbl(_df(t, "revenue_estimate", 6 * 3600))
    trend, revs = _tbl(_df(t, "eps_trend", 6 * 3600)), _tbl(_df(t, "eps_revisions", 6 * 3600))
    ed = _df(t, "earnings_dates", 6 * 3600)
    surprises = []
    if isinstance(ed, pd.DataFrame) and not ed.empty:
        for d, r in ed.sort_index().iterrows():
            rep, est = _f(r.get("Reported EPS")), _f(r.get("EPS Estimate"))
            surprises.append({"date": pd.Timestamp(d).date().isoformat(), "estimate": est, "actual": rep, "surprise": _f(r.get("Surprise(%)"))})
    if not eps and not rev and not surprises:
        return {"ok": False, "error": "No estimates on Yahoo."}
    return {"ok": True, "eps": eps, "revenue": rev, "trend": trend, "revisions": revs, "surprises": surprises[-12:],
            "note": "Consensus from Yahoo: current and next quarter, current and next fiscal year."}


# ---------- HDS: holders ----------
def _holders(df: Any) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    return [{"holder": r.get("Holder"), "pct": _f(r.get("pctHeld")), "shares": _f(r.get("Shares")), "value": _f(r.get("Value")),
             "chg": _f(r.get("pctChange")), "date": str(r.get("Date Reported"))[:10]} for _, r in df.iterrows()]


def sec_hds(t: str) -> dict[str, Any]:
    inst, funds = _holders(_df(t, "institutional_holders", 24 * 3600)), _holders(_df(t, "mutualfund_holders", 24 * 3600))
    mh = _df(t, "major_holders", 24 * 3600)
    summary = {}
    if isinstance(mh, pd.DataFrame) and not mh.empty and "Value" in mh:
        summary = {str(k): _f(v) for k, v in mh["Value"].items()}
    if not inst and not funds:
        return {"ok": False, "error": "No holder data on Yahoo."}
    return {"ok": True, "institutions": inst, "funds": funds, "summary": summary, "shares_out": _f(info(t).get("sharesOutstanding"))}


# ---------- INS: insider transactions ----------
def insider_kind(text: str) -> str:
    s = (text or "").lower()
    return ("buy" if "purchase" in s or "buy" in s else "sell" if "sale" in s or "sold" in s else
            "option" if "exercise" in s or "conversion" in s else "award" if "award" in s or "grant" in s or "gift" in s else "other")


def sec_ins(t: str) -> dict[str, Any]:
    df = _df(t, "insider_transactions", 12 * 3600)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {"ok": False, "error": "No insider transactions on Yahoo."}
    tx = []
    for _, r in df.iterrows():
        kind = insider_kind(r.get("Text") or r.get("Transaction"))
        tx.append({"date": str(r.get("Start Date"))[:10], "insider": r.get("Insider"), "position": r.get("Position"), "kind": kind,
                   "text": r.get("Text") or r.get("Transaction"), "shares": _f(r.get("Shares")), "value": _f(r.get("Value"))})
    tx.sort(key=lambda x: x["date"], reverse=True)
    cut = (date.today() - timedelta(days=365)).isoformat()
    yr = [x for x in tx if x["date"] >= cut]
    roster = []
    ro = _df(t, "insider_roster_holders", 24 * 3600)
    if isinstance(ro, pd.DataFrame) and not ro.empty:
        roster = [{"name": r.get("Name"), "position": r.get("Position"), "latest": r.get("Most Recent Transaction"),
                   "date": str(r.get("Latest Transaction Date"))[:10], "shares": _f(r.get("Shares Owned Directly"))} for _, r in ro.iterrows()]
    return {"ok": True, "transactions": tx[:60], "roster": roster,
            "year": {"buys": sum(1 for x in yr if x["kind"] == "buy"), "sells": sum(1 for x in yr if x["kind"] == "sell"),
                     "buy_value": sum(x["value"] or 0 for x in yr if x["kind"] == "buy"), "sell_value": sum(x["value"] or 0 for x in yr if x["kind"] == "sell")}}


# ---------- CN: company news ----------
def sec_cn(t: str, digest: list[dict[str, Any]] | None = None, name: str | None = None) -> dict[str, Any]:
    items = []
    for it in cached(f"news:{t}", 15 * 60, lambda: _tk(t).news or [])[:20]:
        c = it.get("content") or it
        if c.get("title"):
            items.append({"time": c.get("pubDate"), "title": c.get("title"), "summary": (c.get("summary") or "")[:400],
                          "source": (c.get("provider") or {}).get("displayName", "Yahoo"), "url": ((c.get("clickThroughUrl") or c.get("canonicalUrl") or {}) or {}).get("url"), "kind": "news"})
    word = (name or "").split(" ")[0].lower() if name else ""
    for d in digest or []:
        for h in d.get("headlines") or []:
            low = h.lower()
            if re.search(r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])", h) or (len(word) >= 4 and word in low):
                items.append({"time": d.get("date"), "title": h[:300], "summary": "", "source": "Your digest", "url": None, "kind": "digest"})
    items.sort(key=lambda x: x["time"] or "", reverse=True)
    return {"ok": True, "items": items} if items else {"ok": False, "error": "No recent news."}


def _safe_info(s: str) -> dict[str, Any]:
    try:
        return info(s)
    except Exception:
        return {}


# Yahoo's sector names -> the GICS names portfolio.md and the caps use.
YAHOO_TO_GICS = {
    "Technology": "Information Technology", "Healthcare": "Health Care", "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
    "Basic Materials": "Materials", "Communication Services": "Communication Services", "Industrials": "Industrials",
    "Energy": "Energy", "Utilities": "Utilities", "Real Estate": "Real Estate",
}


def provisional_class(t: str) -> dict[str, Any]:
    """Sector and sleeve for a name not yet in portfolio.md, so caps and risk work the minute it is bought.
    Shown as provisional until the Holdings row confirms it."""
    try:
        i = info(t)
    except Exception:
        i = {}
    name = i.get("longName") or i.get("shortName") or ""
    if not i:
        return {"sector": "Unclassified", "sleeve": "core", "company": name, "industry": ""}
    if _is_fund(i):
        return {"sector": "ETF", "sleeve": "etf", "company": name, "industry": i.get("category") or ""}
    return {"sector": YAHOO_TO_GICS.get(i.get("sector") or "", "Unclassified"), "sleeve": "core",
            "company": name, "industry": i.get("industry") or ""}
