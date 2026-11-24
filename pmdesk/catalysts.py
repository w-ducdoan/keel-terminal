"""CAL: one catalyst calendar for the book.

Company events for held, watchlist and tracked names (earnings, ex-dividend dates, IPO lockup expiries) plus
market-wide dates (options expiry, quarterly index rebalances, Russell reconstitution, NYSE holidays and early
closes) and macro releases from FRED's release schedule (CPI, jobs, PCE, GDP, retail sales, PPI, JOLTS, Fed).
Rule-based: no AI. Estimated dates are labeled as estimates, never presented as confirmed.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import requests

from . import equity, market, sec, sec_events, store

_cache: dict[str, tuple[float, Any]] = {}

# FRED series whose release schedule we show, with the name used on the calendar and how much it moves markets
MACRO = [("CPIAUCSL", "CPI (inflation)", "high"), ("PAYEMS", "Jobs report", "high"), ("PCEPI", "PCE inflation", "high"),
         ("GDP", "GDP", "medium"), ("RSAFS", "Retail sales", "medium"), ("PPIFIS", "PPI (producer prices)", "medium"),
         ("JTSJOL", "JOLTS job openings", "low")]
# FRED has no meeting schedule for the Fed (its rate series update daily), so FOMC dates come from the Fed's own calendar page
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


# ---------- calendar rules (pure) ----------
def nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    """n-th weekday (Mon=0) of a month; n=-1 for the last one."""
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def easter(y: int) -> date:
    """Gregorian Easter (anonymous algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = (h + ll - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def _observed(d: date, saturday_ok: bool = True) -> date | None:
    """NYSE rule: a Saturday holiday moves to Friday, a Sunday one to Monday. New Year's on a Saturday is not moved
    (the NYSE does not close the last trading day of the year)."""
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_ok else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(y: int) -> dict[date, str]:
    out: dict[date, str] = {}
    for d, name, sat_ok in [(date(y, 1, 1), "New Year's Day", False), (nth_weekday(y, 1, 0, 3), "Martin Luther King Jr. Day", True),
                            (nth_weekday(y, 2, 0, 3), "Presidents' Day", True), (easter(y) - timedelta(days=2), "Good Friday", True),
                            (nth_weekday(y, 5, 0, -1), "Memorial Day", True), (date(y, 6, 19), "Juneteenth", True),
                            (date(y, 7, 4), "Independence Day", True), (nth_weekday(y, 9, 0, 1), "Labor Day", True),
                            (nth_weekday(y, 11, 3, 4), "Thanksgiving", True), (date(y, 12, 25), "Christmas", True)]:
        o = _observed(d, sat_ok)
        if o:
            out[o] = name
    return out


def nyse_early_closes(y: int) -> dict[date, str]:
    """1 pm ET closes: day after Thanksgiving, Christmas Eve and July 3 when they are ordinary weekdays."""
    out = {nth_weekday(y, 11, 3, 4) + timedelta(days=1): "Day after Thanksgiving"}
    hol = nyse_holidays(y)
    for d, name in [(date(y, 12, 24), "Christmas Eve"), (date(y, 7, 3), "Day before Independence Day")]:
        if d.weekday() < 5 and d not in hol:
            out[d] = name
    return out


def market_dates(start: date, end: date) -> list[dict[str, Any]]:
    out = []
    for y in range(start.year, end.year + 1):
        for d, name in nyse_holidays(y).items():
            out.append({"date": d, "kind": "holiday", "title": f"Market closed: {name}", "importance": "medium"})
        for d, name in nyse_early_closes(y).items():
            out.append({"date": d, "kind": "holiday", "title": f"Early close 1 pm ET: {name}", "importance": "low"})
        for m in range(1, 13):
            f3 = nth_weekday(y, m, 4, 3)
            if m in (3, 6, 9, 12):
                out.append({"date": f3, "kind": "market", "importance": "medium",
                            "title": "Quarterly options expiry and S&P index rebalance (effective after the close)",
                            "detail": "Heavy closing volume; index adds and deletes trade at the close."})
            else:
                out.append({"date": f3, "kind": "market", "importance": "low", "title": "Monthly options expiry"})
        out.append({"date": nth_weekday(y, 6, 4, -1), "kind": "market", "importance": "medium",
                    "title": "Russell index reconstitution (effective after the close; date per FTSE Russell's usual rule)",
                    "detail": "Check FTSE Russell's announced calendar; small and mid caps see heavy closing volume."})
    hol = {d for y in range(start.year, end.year + 1) for d in nyse_holidays(y)}
    for e in out:  # an expiry that falls on a holiday moves to the day before
        if e["kind"] == "market" and e["date"] in hol:
            e["date"] -= timedelta(days=1)
    return [e for e in out if start <= e["date"] <= end]


def next_dividend(ex: date | None, rate: float | None, today: date, horizon: date) -> tuple[date, bool] | None:
    """Yahoo gives the latest ex-dividend date, often a past one. A future date is used as is; for a regular
    payer whose last ex-date is within ~100 days, the next is estimated one quarter later (flagged)."""
    if not ex or not rate:
        return None
    if ex >= today:
        return (ex, False) if ex <= horizon else None
    if (today - ex).days <= 100:
        est = ex + timedelta(days=91)
        while est < today:
            est += timedelta(days=91)
        return (est, True) if est <= horizon else None
    return None


def lockup_from_filings(forms: list[str], dates: list[str], today: date) -> date | None:
    """IPO lockup (typically 180 days) from the final IPO prospectus (424B4) when the company listed recently."""
    ipo = sorted(d for f, d in zip(forms, dates) if f == "424B4")
    if not ipo:
        return None
    first = date.fromisoformat(ipo[0])
    if "S-1" not in forms and "F-1" not in forms:
        return None  # a 424B4 without an S-1/F-1 is a follow-on, not an IPO
    lock = first + timedelta(days=180)
    return lock if lock >= today and (today - first).days <= 200 else None


def parse_fomc(text: str) -> list[dict[str, Any]]:
    """Meeting dates from the Fed's calendar page text. The rate decision is announced on the last day.
    "April/May 30-1" style meetings end in the second month; "*" marks a meeting with new projections."""
    import re
    out = []
    mon = "|".join(MONTHS)
    for ym in re.finditer(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", text, re.S):
        year, body = int(ym.group(1)), ym.group(2)
        body = body.split("Note:")[0]  # the closing note names next year's first meeting
        for m in re.finditer(rf"\b({mon})(?:/({mon}))? (\d{{1,2}})-(\d{{1,2}})(\*?)", body):
            end_month = MONTHS.index(m.group(2) or m.group(1)) + 1
            try:
                d = date(year, end_month, int(m.group(4)))
            except ValueError:
                continue
            out.append({"date": d, "sep": bool(m.group(5))})
    return out


def fomc_meetings() -> list[dict[str, Any]]:
    hit = store.kv_get("cal_fomc")
    if hit and hit.get("day") and (date.today() - date.fromisoformat(hit["day"])).days < 7:
        return [{"date": date.fromisoformat(x["date"]), "sep": x["sep"]} for x in hit["m"]]
    try:
        import html
        import re
        r = requests.get(FOMC_URL, headers={"User-Agent": "Mozilla/5.0 (Keel portfolio terminal)"}, timeout=30)
        r.raise_for_status()
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", r.text)))
        ms = parse_fomc(text)
    except Exception:
        return [{"date": date.fromisoformat(x["date"]), "sep": x["sep"]} for x in (hit or {}).get("m", [])]  # last good copy
    if ms:
        store.kv_set("cal_fomc", {"day": date.today().isoformat(), "m": [{"date": x["date"].isoformat(), "sep": x["sep"]} for x in ms]})
    return ms


# ---------- data ----------
def _memo(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _macro(start: date, end: date) -> list[dict[str, Any]]:
    key = market.fred_key()
    if not key:
        return []

    def get(path: str, params: dict[str, Any], field: str) -> list[dict[str, Any]]:
        """FRED with retries: it answers 429 when Keel's other FRED calls land at the same moment."""
        for attempt in range(4):
            try:
                r = requests.get(f"https://api.stlouisfed.org/fred/{path}", params={**params, "api_key": key, "file_type": "json"}, timeout=15)
                if r.status_code == 200:
                    return r.json().get(field) or []
            except requests.RequestException:
                pass
            time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"FRED {path} failed")

    failed: list[str] = []

    def one(spec):
        sid, label, imp = spec
        try:
            rel = get("series/release", {"series_id": sid}, "releases")
            if not rel:
                return []
            dates = get("release/dates", {"release_id": rel[0]["id"], "realtime_start": start.isoformat(), "realtime_end": "9999-12-31",
                                          "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 12}, "release_dates")
        except RuntimeError:
            failed.append(label)
            return []
        return [{"date": date.fromisoformat(d["date"]), "kind": "macro", "title": label, "importance": imp,
                 "detail": f"FRED release: {rel[0]['name']}", "source": "FRED"} for d in dates
                if start <= date.fromisoformat(d["date"]) <= end]
    with ThreadPoolExecutor(max_workers=2) as ex:
        rows = [e for rows in ex.map(one, MACRO) for e in rows]
    if failed:
        rows.append({"date": start, "kind": "macro", "importance": "low", "failed": True,
                     "title": f"Could not load from FRED: {', '.join(failed)} (refresh to retry)"})
    for m in fomc_meetings():
        if start <= m["date"] <= end:
            rows.append({"date": m["date"], "kind": "macro", "importance": "high",
                         "title": "Fed rate decision (FOMC)" + (" with new projections" if m["sep"] else ""),
                         "detail": "Statement 2 pm ET, press conference 2:30 pm. Dates are tentative until confirmed at the prior meeting.",
                         "source": "federalreserve.gov"})
    return rows


def _company(t: str, own: str, earn: dict[str, Any] | None, today: date, horizon: date) -> list[dict[str, Any]]:
    out = []
    if earn is None:  # names the engine doesn't follow (tracked): ask Yahoo once a day
        k = f"cal_earn:{t}"
        hit = store.kv_get(k)
        if hit and hit.get("day") == today.isoformat():
            earn = hit.get("e")
        else:
            try:
                earn = market.next_earnings_yahoo(t)
            except Exception:
                earn = None
            store.kv_set(k, {"day": today.isoformat(), "e": earn})
    if earn and earn.get("date"):
        d = date.fromisoformat(earn["date"])
        if today <= d <= horizon:
            im = store.kv_get(f"ern:{t}") or {}
            priced = f"Options price ±{im['move'] * 100:.1f}%" if im.get("move") and im.get("date") == earn["date"] else ""
            out.append({"date": d, "kind": "earnings", "title": f"{t} earnings{' ' + earn['timing'].upper() if earn.get('timing') else ''}",
                        "importance": "high" if own == "held" else "medium", "estimated": earn.get("verified") is False,
                        "detail": "; ".join(x for x in [priced, "tentative date" if earn.get("verified") is False else ""] if x),
                        "source": earn.get("source") or "robinhood"})
    try:
        i = equity.info(t) or {}
    except Exception:
        i = {}
    exd = i.get("exDividendDate")
    exd = datetime.fromtimestamp(exd, tz=timezone.utc).date() if isinstance(exd, (int, float)) else None
    nd = next_dividend(exd, i.get("dividendRate"), today, horizon)
    if nd:
        d, est = nd
        out.append({"date": d, "kind": "dividend", "title": f"{t} ex-dividend" + (" (estimated)" if est else ""),
                    "importance": "low", "estimated": est,
                    "detail": f"${i.get('dividendRate'):.2f} a year" + ("; estimated from the quarterly pattern" if est else ""), "source": "Yahoo"})
    try:
        c = sec.cik(t)
        if c:
            rec = sec_events._submissions(c)["filings"]["recent"]
            lk = lockup_from_filings(rec["form"], rec["filingDate"], today)
            if lk and lk <= horizon:
                out.append({"date": lk, "kind": "lockup", "title": f"{t} IPO lockup expiry (typical 180 days)", "importance": "medium",
                            "estimated": True, "detail": "Insiders can usually start selling; check the IPO prospectus for the exact terms.",
                            "source": "SEC filings"})
    except Exception:
        pass
    for e in out:
        e.update({"ticker": t, "own": own})
    return out


def build(snap: dict[str, Any], tracked: list[str], days: int = 90, fresh: bool = False) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        today = date.today()
        horizon = today + timedelta(days=days)
        held = [p["ticker"] for p in snap.get("positions", [])]
        watch = [w["ticker"] for w in snap.get("watchlist", []) if w["ticker"] not in held]
        trk = [t for t in tracked if t not in held and t not in watch]
        earn = snap.get("earnings") or {}
        names = [(t, "held") for t in held] + [(t, "watch") for t in watch] + [(t, "tracked") for t in trk]
        with ThreadPoolExecutor(max_workers=6) as ex:
            company = [e for rows in ex.map(lambda x: _company(x[0], x[1], earn.get(x[0]) if x[0] in earn else None, today, horizon), names)
                       for e in rows]
        events = company + market_dates(today, horizon) + _macro(today, horizon)
        for e in events:
            e["date"] = e["date"].isoformat()
            e.setdefault("own", "market")
            e.setdefault("ticker", None)
        events.sort(key=lambda e: (e["date"], {"high": 0, "medium": 1, "low": 2}.get(e.get("importance"), 3), e.get("ticker") or ""))
        return {"ok": True, "events": events, "from": today.isoformat(), "to": horizon.isoformat(), "fred": bool(market.fred_key()),
                "as_of": datetime.now().isoformat(timespec="minutes")}
    if fresh:
        _cache.pop(f"cal:{days}", None)
    out = _memo(f"cal:{days}", 6 * 3600, run)
    if any(e.get("failed") for e in out["events"]):
        _cache.pop(f"cal:{days}", None)  # retry on the next request instead of keeping a partial calendar
    return out
