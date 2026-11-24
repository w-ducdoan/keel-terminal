"""Multi-year history for the screener, built from SEC annual filings.

SEC's "frames" endpoint returns one line item for every filer for one year in a single request, so a
few hundred requests give a local table of about 15 fiscal years for the whole US market. The screener
then computes any window (1 to 15 years) from that table: CAGR, years of growth, worst year, average,
minimum, change. Figures are as most recently filed (restated where the company restated them).

A value that can't be computed honestly is None, never a guess: a CAGR from a zero or negative start,
a missing year, a company whose latest annual report is stale, or EPS across a share-count jump
(old EPS may not be split-adjusted).
"""
from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from datetime import date, timedelta
from typing import Any

import requests

from . import sec
from .config import DATA_DIR

log = logging.getLogger("pmdesk.history")
FILE = DATA_DIR / "history_sec.pkl"
FIRST_YEAR = 2009
MAX_YEARS = 15
STALE_DAYS = 550        # the latest fiscal year must have ended within this
REFRESH_DAYS = 7        # re-pull the last three years
FULL_DAYS = 60          # re-pull everything

# raw key: (unit, [tags in priority order], instant?)
RAW: dict[str, tuple[str, list[str], bool]] = {
    "rev": ("USD", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"], False),
    "gp": ("USD", ["GrossProfit"], False),
    "cogs": ("USD", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], False),
    "ebit": ("USD", ["OperatingIncomeLoss"], False),
    "da": ("USD", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                   "DepreciationAmortizationAndAccretionNet"], False),
    "ni": ("USD", ["NetIncomeLoss"], False),
    "eps": ("USD-per-shares", ["EarningsPerShareDiluted"], False),
    "shares": ("shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"], False),
    "cfo": ("USD", ["NetCashProvidedByUsedInOperatingActivities"], False),
    "capex": ("USD", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], False),
    "sbc": ("USD", ["ShareBasedCompensation"], False),
    "equity": ("USD", ["StockholdersEquity"], True),
}

# metric: (label, kind). level = a dollar/count line (growth stats); ratio = a percent (average/min/change stats)
METRICS: dict[str, tuple[str, str]] = {
    "rev": ("Revenue", "level"), "gp": ("Gross profit", "level"), "ebitda": ("EBITDA", "level"), "ebit": ("Operating income", "level"),
    "ni": ("Net income", "level"), "eps": ("Diluted EPS", "level"), "cfo": ("Operating cash flow", "level"), "fcf": ("Free cash flow", "level"),
    "fcfps": ("FCF per share", "level"), "shares": ("Diluted share count", "level"),
    "gm": ("Gross margin", "ratio"), "om": ("Operating margin", "ratio"), "nm": ("Net margin", "ratio"), "fcfm": ("FCF margin", "ratio"),
    "roe": ("Return on equity", "ratio"), "sbcr": ("Stock comp / revenue", "ratio"),
}
# stat: (label, kinds, unit)
STATS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "cagr": ("CAGR", ("level",), "%"),
    "up": ("years of growth", ("level",), "yrs"),
    "worst": ("worst year's growth", ("level",), "%"),
    "pos": ("years positive", ("level",), "yrs"),
    "avg": ("average", ("ratio",), "%"),
    "min": ("lowest year", ("ratio",), "%"),
    "chg": ("change", ("ratio",), "pts"),
}

_lock = threading.Lock()
_mem: dict[str, Any] | None = None
STATUS: dict[str, Any] = {"state": "not built", "done": 0, "of": 0, "error": None}


# ---------- building the table ----------

def _frame(tag: str, unit: str, period: str) -> list[dict[str, Any]]:
    url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json"
    for attempt in range(3):
        with sec._lock:  # shared with the rest of Keel: SEC allows 10 requests a second
            r = requests.get(url, headers=sec._ua(), timeout=60)
            time.sleep(0.15)
        if r.status_code == 404:
            return []  # nobody used this tag that year
        if r.ok:
            return r.json().get("data", [])
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()
    return []


def merge_tags(by_tag: list[dict[int, float]]) -> dict[int, float]:
    """One series from several tags that mean the same line. The base is the tag covering the newest
    year (most recent years, then priority); gaps are filled only from tags that agree with it."""
    have = [s for s in by_tag if s]
    if not have:
        return {}
    newest = max(max(s) for s in have)
    recent = range(newest - 5, newest + 1)
    # Some filers leave a stray or partial value under a second tag: drop any far below its peers.
    top = max(abs(s[newest]) for s in have if newest in s)
    have = [s for s in have if newest not in s or abs(s[newest]) >= 0.5 * top]
    base = max((s for s in have if newest in s), key=lambda s: sum(y in s for y in recent))
    out = dict(base)
    for s in have:
        if s is base:
            continue
        both = [y for y in s if y in base]
        if any(abs(s[y] - base[y]) > 0.05 * max(abs(base[y]), 1e-9) for y in both):
            continue  # a different definition: mixing it in would invent growth
        for y, v in s.items():
            out.setdefault(y, v)
    return out


def build(full: bool = False) -> dict[str, Any]:
    """Pull the frames and save the table. Incremental by default: only the last three years."""
    old = load() or {}
    full = full or not old.get("raw")
    this = date.today().year
    years = list(range(FIRST_YEAR, this + 1)) if full else list(range(this - 2, this + 1))
    jobs = [(k, i, tag, unit, inst, y) for k, (unit, tags, inst) in RAW.items() for i, tag in enumerate(tags) for y in years]
    STATUS.update(state="building", done=0, of=len(jobs), error=None)
    raw: dict[int, dict[str, list[dict[int, float]]]] = {} if full else old["raw"]
    ends: dict[int, dict[int, str]] = {} if full else old["ends"]
    for k, i, tag, unit, inst, y in jobs:
        for d in _frame(tag, unit, f"CY{y}Q4I" if inst else f"CY{y}"):
            slots = raw.setdefault(d["cik"], {}).setdefault(k, [{} for _ in RAW[k][1]])
            slots[i][y] = float(d["val"])
            if k in ("rev", "ni"):
                ends.setdefault(d["cik"], {})[y] = d["end"]
        STATUS["done"] += 1
    tick = sec._get_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json", 7 * 86400) or {}
    now = time.time()
    out = {"raw": raw, "ends": ends, "ciks": {v["ticker"].upper(): int(v["cik_str"]) for v in tick.values()},
           "built": now, "full": now if full else old.get("full", now)}
    tmp = FILE.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, FILE)
    global _mem
    with _lock:
        _mem = out
    STATUS.update(state="ready")
    return out


def load() -> dict[str, Any] | None:
    """The saved table (our own file), kept in memory."""
    global _mem
    with _lock:
        if _mem is None and FILE.exists():
            try:
                with FILE.open("rb") as f:
                    _mem = pickle.load(f)
                STATUS.update(state="ready")
            except Exception as e:  # unreadable: rebuild
                log.warning("history table unreadable: %s", e)
        return _mem


def refresh_if_due() -> None:
    d = load()
    now = time.time()
    if d and now - d["built"] < REFRESH_DAYS * 86400:
        return
    try:
        build(full=not d or now - d.get("full", 0) > FULL_DAYS * 86400)
    except Exception as e:
        log.warning("history build failed: %s", e)
        STATUS.update(state="ready" if d else "failed", error=str(e)[:200])


def watch() -> None:
    """Background loop: build on first start, then a weekly top-up."""
    time.sleep(45)  # let the desk finish starting
    while True:
        refresh_if_due()
        time.sleep(6 * 3600)


def status() -> dict[str, Any]:
    d = load()
    return {**STATUS, "built": d["built"] if d else None, "companies": len(d["raw"]) if d else 0}


# ---------- per-company series and statistics ----------

def _combine(a: dict[int, float], b: dict[int, float], fn: Any) -> dict[int, float]:
    out = {}
    for y in a.keys() & b.keys():
        v = fn(a[y], b[y])
        if v is not None:
            out[y] = v
    return out


def series(raw: dict[str, list[dict[int, float]]], metric: str) -> dict[int, float]:
    """One metric by fiscal year. Ratios are in percent."""
    def r(k: str) -> dict[int, float]:
        return merge_tags(raw.get(k, []))

    def pct(a: dict[int, float], b: dict[int, float]) -> dict[int, float]:
        return _combine(a, b, lambda x, y: x / y * 100 if y > 0 else None)

    def gp() -> dict[int, float]:
        filed = r("gp")
        return filed or _combine(r("rev"), r("cogs"), lambda x, y: x - y)

    def fcf() -> dict[int, float]:
        return _combine(r("cfo"), r("capex"), lambda x, y: x - y)

    if metric == "gp":
        return gp()
    if metric == "ebitda":
        return _combine(r("ebit"), r("da"), lambda x, y: x + y)
    if metric == "fcf":
        return fcf()
    if metric == "fcfps":
        return _combine(fcf(), r("shares"), lambda x, y: x / y if y > 0 else None)
    if metric == "gm":
        return pct(gp(), r("rev"))
    if metric == "om":
        return pct(r("ebit"), r("rev"))
    if metric == "nm":
        return pct(r("ni"), r("rev"))
    if metric == "fcfm":
        return pct(fcf(), r("rev"))
    if metric == "roe":
        return pct(r("ni"), r("equity"))
    if metric == "sbcr":
        return pct(r("sbc"), r("rev"))
    return r(metric)


def window_ok(ends: dict[int, str], y0: int, y1: int, every: bool) -> bool:
    """The year-ends really are one year apart. SEC files years into calendar slots, so a 52/53-week
    year-end near June 30 can skip a slot, and a company can change its fiscal year."""
    def gap(a: int, b: int) -> bool:
        if a not in ends or b not in ends:
            return False
        days = (date.fromisoformat(ends[b]) - date.fromisoformat(ends[a])).days
        return abs(days - 365.25 * (b - a)) <= 20
    return all(gap(y, y + 1) for y in range(y0, y1)) if every else (y0 == y1 or gap(y0, y1))


def share_jump(raw: dict[str, list[dict[int, float]]], y0: int, y1: int) -> bool:
    """A share count move too big for buybacks or normal issuance: likely a split or a merger."""
    s = merge_tags(raw.get("shares", []))
    for y in range(y0 + 1, y1 + 1):
        a, b = s.get(y - 1), s.get(y)
        if a and b and not 0.7 < b / a < 1.4:
            return True
    return False


def stat(s: dict[int, float], how: str, years: int, last: int) -> dict[str, Any] | None:
    """One statistic over the `years` ending in fiscal year `last`. None when it can't be computed."""
    if how in ("cagr", "up", "worst"):
        need = range(last - years, last + 1)       # N years of growth needs N+1 points
    elif how == "chg":
        need = range(last - years, last + 1)
    else:
        need = range(last - years + 1, last + 1)
    y0 = need[0]
    if how in ("cagr", "chg"):                     # endpoints only
        if y0 not in s or last not in s:
            return None
        a, b = s[y0], s[last]
        if how == "chg":
            return {"v": b - a, "from": y0, "to": last, "a": a, "b": b}
        if a <= 0 or b <= 0:
            return None
        return {"v": ((b / a) ** (1 / years) - 1) * 100, "from": y0, "to": last, "a": a, "b": b}
    if any(y not in s for y in need):
        return None
    vals = [s[y] for y in need]
    if how == "pos":
        v: float = sum(x > 0 for x in vals)
    elif how == "avg":
        v = sum(vals) / len(vals)
    elif how == "min":
        v = min(vals)
    elif how == "up":
        v = sum(b > a for a, b in zip(vals, vals[1:]))
    else:  # worst year's growth
        if any(x <= 0 for x in vals[:-1]):
            return None
        v = min(b / a - 1 for a, b in zip(vals, vals[1:])) * 100
    return {"v": v, "from": y0, "to": last, "a": vals[0], "b": vals[-1]}


def last_year(ends: dict[int, str], s: dict[int, float], today: date | None = None) -> int | None:
    """Newest fiscal year with this metric, if that year ended recently enough to screen on."""
    if not s:
        return None
    y = max(s)
    end = ends.get(y)
    cutoff = ((today or date.today()) - timedelta(days=STALE_DAYS)).isoformat()
    return y if end and end >= cutoff else None


def value(ticker: str, metric: str, how: str, years: int, table: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The statistic for one ticker, or None."""
    d = table or load()
    if not d:
        return None
    cik = d["ciks"].get(ticker.upper().replace(".", "-"))
    raw = d["raw"].get(cik) if cik else None
    if not raw:
        return None
    s = series(raw, metric)
    last = last_year(d["ends"].get(cik, {}), s)
    if last is None:
        return None
    ends = d["ends"].get(cik, {})
    out = stat(s, how, years, last)
    if out and not window_ok(ends, out["from"], last, every=how not in ("cagr", "chg")):
        return None
    if out and metric in ("eps", "fcfps") and share_jump(raw, out["from"], last):
        return None
    if out:
        out["end"] = ends.get(last)
    return out


def clean_row(c: dict[str, Any]) -> dict[str, Any]:
    """Validate one multi-year criterion from the page."""
    m, how = c.get("hist"), c.get("stat")
    if m not in METRICS or how not in STATS or METRICS[m][1] not in STATS[how][1]:
        raise ValueError(f"Unknown multi-year filter: {m} {how}")
    return {**c, "years": max(1, min(MAX_YEARS, int(c.get("years") or 5)))}


def label(c: dict[str, Any]) -> str:
    lab = METRICS[c["hist"]][0]
    how, _, _ = STATS[c["stat"]]
    return f"{lab} {c['years']}y {how}"


def passes(v: float, op: str, target: Any) -> bool:
    if op == "btwn":
        return float(target[0]) <= v <= float(target[1])
    return v >= float(target) if op == "gte" else v <= float(target)
