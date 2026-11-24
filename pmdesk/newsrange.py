"""Company news over any date range, and "why did it move?" for one point on a price chart.

- company(): CN with a look-back you choose (1 week to 2 years). Google News search with after:/before:
  dates, split into monthly windows so a long range is not crowded out by the latest week, plus Yahoo's
  recent stories and your digest mentions. Headlines must name the company or its ticker.
- why(): the move between two chart points (stock vs SPY, volume vs its 20-day average), the headlines
  dated a day either side, and the company's SEC filings in that window (8-K items with what they mean).
Free sources only (the Google News RSS search the market wire already uses, Yahoo, SEC EDGAR).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import requests

from . import news

SUFFIX = re.compile(r"[,.]?\s+(inc|corp|corporation|co|company|ltd|limited|plc|ag|sa|nv|se|holdings?|group|class [a-c]|common stock|\(the\))\.?$", re.I)


def short_name(name: str | None) -> str:
    """'Toast, Inc.' -> 'Toast'; 'On Holding AG' -> 'On Holding' (a bare 'On' would match every headline)."""
    s = (name or "").strip()
    for _ in range(3):
        cut = SUFFIX.sub("", s).strip(" ,.")
        if len(cut) < 4 and len(s) > len(cut):
            break  # keep enough of the name to be distinctive
        s = cut
    return s


BIZ = re.compile(r"\b(shares?|stocks?|earnings|revenue|sales|profit|loss|guidance|outlook|analysts?|upgrade|downgrade|price target|"
                 r"rating|investors?|quarter|q[1-4]|ceo|cfo|chief|executive|board|acqui\w+|merger|deal|buyback|dividend|ipo|layoffs?|"
                 r"lawsuit|sec\b|nyse|nasdaq|inc\b|corp\b|company|brand|customers?|launch\w*|partners?\w*|stake|valuation|market)\b", re.I)
JUNK = re.compile(r"\b(put|call) \([A-Z]{1,6}\d{6}[CP]\d+\)|Stock Price, News, Quote|Historical Prices|Options Chain", re.I)


def relevant(text: str, t: str, name: str | None) -> bool:
    """A headline is about the company when it carries the ticker in capitals, or the company name as written
    (capitalized) next to business words. 'French toast recipe' and 'plans on holding' are dropped."""
    if JUNK.search(text):
        return False
    base = t.split(".")[0].split("=")[0].lstrip("^")
    if base.isalpha() and len(base) >= 2 and re.search(r"(?<![A-Za-z])" + re.escape(base) + r"(?![A-Za-z])", text):
        if len(base) >= 3 or re.search(r"[(:]\s*" + re.escape(base) + r"\b", text):
            return True
    sn = short_name(name)
    if len(sn) < 3:
        return False
    if not re.search(r"(?<![A-Za-z])" + re.escape(sn) + r"(?![a-z])", text):  # case-sensitive: the name as a name
        return False
    return bool(BIZ.search(text))


def _query(t: str, name: str | None) -> str:
    sn = short_name(name)
    base = t.split(".")[0].split("=")[0].lstrip("^")
    parts = []
    if sn:
        parts.append(f'"{sn}"')
    if base.isalpha() and len(base) >= 2:
        parts.append(base)
    return " OR ".join(parts) or t


def search(t: str, name: str | None, start: date, end: date) -> list[dict[str, Any]]:
    """Google News stories dated start..end (inclusive) that name the company or ticker."""
    q = f"{_query(t, name)} after:{start.isoformat()} before:{(end + timedelta(days=1)).isoformat()}"
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, headers=news.UA, timeout=15)
    r.raise_for_status()
    return [s for s in news.parse_rss(r.content, "Google News") if relevant(s["title"] + " " + s.get("summary", ""), t, name)]


def _windows(start: date, end: date, step: int = 30) -> list[tuple[date, date]]:
    out, a = [], start
    while a <= end:
        b = min(end, a + timedelta(days=step - 1))
        out.append((a, b))
        a = b + timedelta(days=1)
    return out


def _merge(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for s in stories:
        k = news._key(s["title"])
        if k in merged:
            m = merged[k]
            if s["source"] not in m["sources"]:
                m["sources"].append(s["source"])
            continue
        merged[k] = {**s, "sources": [s["source"]]}
    out = []
    for s in merged.values():
        tm = s["time"]
        s["time"] = tm.isoformat() if isinstance(tm, datetime) else tm
        out.append(s)
    out.sort(key=lambda s: s["time"] or "", reverse=True)
    return out


def company(t: str, name: str | None, days: int, recent: list[dict[str, Any]] | None = None,
            today: date | None = None) -> dict[str, Any]:
    """CN over the last `days` days. `recent` = items already found (Yahoo, digest), kept when in range."""
    days = max(1, min(int(days), 730))
    end = today or date.today()
    start = end - timedelta(days=days)
    wins = _windows(start, end)[-24:]
    stories: list[dict[str, Any]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for f in [ex.submit(search, t, name, a, b) for a, b in wins]:
            try:
                stories += f.result(timeout=30)
            except Exception:
                errors += 1
    items = [{"time": s["time"], "title": s["title"], "summary": s.get("summary", ""), "source": s["source"], "url": s["url"], "kind": "news"}
             for s in _merge(stories)]
    seen = {news._key(x["title"]) for x in items}
    cut = start.isoformat()
    for x in recent or []:
        if (x.get("time") or "")[:10] >= cut and news._key(x["title"]) not in seen:
            items.append(x)
            seen.add(news._key(x["title"]))
    items.sort(key=lambda x: x["time"] or "", reverse=True)
    note = f"{len(items)} stories over {days} days" + (f" ({errors} of {len(wins)} searches failed; try again)" if errors else "")
    if not items:
        return {"ok": False, "error": "No news found in this range." if not errors else "News search failed; try again.", "days": days}
    return {"ok": True, "items": items[:400], "days": days, "start": cut, "note": note}


# ---------- why did it move? ----------
def filings_between(t: str, start: date, end: date) -> list[dict[str, Any]]:
    """SEC filings dated start..end: 8-K items in plain words, offerings, 13Ds, reports. Form 4s are left out."""
    from . import sec, sec_events
    c = sec.cik(t)
    if c is None:
        return []
    rec = sec_events._submissions(c)["filings"]["recent"]
    a, b = start.isoformat(), end.isoformat()
    out = []
    for form, fdate, items, acc, doc in zip(rec["form"], rec["filingDate"], rec["items"], rec["accessionNumber"], rec["primaryDocument"]):
        if fdate < a:
            break
        if fdate > b:
            continue
        ev = {"form": form, "date": fdate, "url": sec_events._url(c, acc, doc)}
        if form in ("8-K", "8-K/A", "6-K"):
            codes = [x.strip() for x in (items or "").split(",") if x.strip() and x.strip() != "9.01"]
            parts = [sec_events.ITEMS.get(x, (f"Item {x}", "low")) for x in codes]
            ev["title"] = "; ".join(p[0] for p in parts) or ("Foreign issuer report" if form == "6-K" else "Current report")
            ev["severity"] = min((p[1] for p in parts), key=lambda s: sec_events.SEV_RANK[s]) if parts else "low"
        elif form in sec_events.OTHER_FORMS:
            ev["title"], ev["severity"] = sec_events.OTHER_FORMS[form]
        else:
            continue
        out.append(ev)
    return out


def move_stats(px: Any, spy: Any, start: date, end: date) -> dict[str, Any]:
    """Close on the last trading day before `start` -> close on `end`; SPY over the same days; volume vs 20-day average.
    px / spy: daily DataFrames with Close (and Volume) indexed by date."""
    def closes(df):
        s = df["Close"].dropna()
        s.index = [d.date() if hasattr(d, "date") else d for d in s.index]
        return s
    c = closes(px)
    before = c[[d < start for d in c.index]]
    upto = c[[d <= end for d in c.index]]
    if before.empty or upto.empty:
        return {}
    p0, p1 = float(before.iloc[-1]), float(upto.iloc[-1])
    out: dict[str, Any] = {"from": str(before.index[-1]), "to": str(upto.index[-1]), "p0": p0, "p1": p1, "ret": p1 / p0 - 1}
    if spy is not None and not getattr(spy, "empty", True):
        s = closes(spy)
        sb, su = s[[d <= before.index[-1] for d in s.index]], s[[d <= upto.index[-1] for d in s.index]]
        if not sb.empty and not su.empty:
            out["spy"] = float(su.iloc[-1]) / float(sb.iloc[-1]) - 1
            out["excess"] = out["ret"] - out["spy"]
    if "Volume" in px:
        v = px["Volume"].dropna()
        v.index = [d.date() if hasattr(d, "date") else d for d in v.index]
        win = v[[start <= d <= end for d in v.index]]
        base = v[[d < start for d in v.index]].tail(20)
        if not win.empty and not base.empty and base.mean() > 0:
            out["vol_x"] = float(win.max()) / float(base.mean())
    return out


def why(t: str, name: str | None, start: date, end: date) -> dict[str, Any]:
    import yfinance as yf
    if end < start:
        start, end = end, start
    if (end - start).days > 120:
        start = end - timedelta(days=120)  # a weekly/monthly bar: look at the last part of it
    jobs: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        jobs["news"] = ex.submit(search, t, name, start - timedelta(days=1), end + timedelta(days=1))
        jobs["filings"] = ex.submit(filings_between, t, start - timedelta(days=1), end)
        lo, hi = start - timedelta(days=45), end + timedelta(days=2)
        jobs["px"] = ex.submit(lambda: yf.Ticker(t).history(start=lo.isoformat(), end=hi.isoformat(), auto_adjust=False))
        jobs["spy"] = ex.submit(lambda: yf.Ticker("SPY").history(start=lo.isoformat(), end=hi.isoformat(), auto_adjust=False))
    got: dict[str, Any] = {}
    errs = []
    for k, f in jobs.items():
        try:
            got[k] = f.result(timeout=40)
        except Exception as e:
            got[k] = None
            errs.append(f"{k}: {str(e)[:80]}")
    move = {}
    if got.get("px") is not None and not got["px"].empty:
        try:
            move = move_stats(got["px"], got.get("spy"), start, end)
        except Exception as e:
            errs.append(f"move: {str(e)[:80]}")
    stories = _merge(got.get("news") or [])
    a, b = (start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat()
    # stories closest to the move first: inside the window, then the day either side
    stories.sort(key=lambda s: (0 if start.isoformat() <= (s["time"] or "")[:10] <= end.isoformat() else 1, -(datetime.fromisoformat(s["time"]).timestamp() if s["time"] else 0)))
    return {"ok": True, "ticker": t, "start": start.isoformat(), "end": end.isoformat(), "window": [a, b], "move": move,
            "news": [{"time": s["time"], "title": s["title"], "source": ", ".join(s["sources"][:2]), "url": s["url"]} for s in stories[:40]],
            "filings": got.get("filings") or [], "errors": errs,
            "as_of": datetime.now(timezone.utc).isoformat()}
