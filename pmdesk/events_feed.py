"""EVT: market-wide event-driven idea feed from SEC filings (last 30 days by default).

  spin-off       Form 10-12B (registration of a new company, usually a spin-off before it lists)
  tender offer   SC TO-T (someone bids for the shares) and SC TO-I (the company buys its own shares back)
  merger vote    DEFM14A / PREM14A (merger proxy: a deal agreed, shareholders to vote)
  going private  SC 13E3 (insiders or a controlling holder taking the company private)
  activist       SCHEDULE 13D, first filing only (a holder above 5% who intends to push for change)
Private funds, trusts and blank-check companies are left out, and so is anything without a stock ticker except
spin-offs (they have none yet). A list of situations to look at for the event pod, not a recommendation.
Rule-based; no AI.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import requests

from . import equity, sec, sec_events

TYPES = {  # SEC form -> (event type, label)
    "10-12B": ("spinoff", "Spin-off / new company registration"),
    "SC TO-T": ("tender", "Tender offer for the shares"),
    "SC TO-I": ("buyback_tender", "Company tender for its own shares"),
    "DEFM14A": ("merger", "Merger vote (definitive proxy)"),
    "PREM14A": ("merger", "Merger vote (preliminary proxy)"),
    "SC 13E3": ("going_private", "Going-private transaction"),
    "SCHEDULE 13D": ("activist", "New activist stake (13D)"),
}
SKIP = re.compile(r"\b(fund|trust|acquisition corp|acquisition co|spac|capital corp i|bdc|portfolio|interval|lending|credit|"
                  r"private markets|income|municipal|etf|holdings ii)\b", re.I)
NAME = re.compile(r"^(.*?)\s+\(([A-Z0-9., -]+)\)\s+\(CIK (\d+)\)$")
NAME_NO_TICKER = re.compile(r"^(.*?)\s+\(CIK (\d+)\)$")


def parse_name(display: str) -> dict[str, Any]:
    """'TOAST, INC.  (TOST)  (CIK 0001650164)' -> name, first ticker, cik."""
    d = " ".join((display or "").split())
    m = NAME.match(d)
    if m:
        return {"name": m.group(1).strip(), "ticker": m.group(2).split(",")[0].strip(), "cik": int(m.group(3))}
    m = NAME_NO_TICKER.match(d)
    if m:
        return {"name": m.group(1).strip(), "ticker": None, "cik": int(m.group(2))}
    return {"name": d, "ticker": None, "cik": None}


def classify(form: str, names: list[str]) -> dict[str, Any] | None:
    """One filing -> an event, or None if it is not an operating-company situation worth showing."""
    base = form.replace("/A", "")
    if base not in TYPES or (base == "SCHEDULE 13D" and form.endswith("/A")):
        return None  # activist amendments are follow-ups, not new situations
    kind, label = TYPES[base]
    parsed = [parse_name(n) for n in names]
    if not parsed:
        return None
    subject = parsed[0]
    others = [p["name"] for p in parsed[1:]]
    if SKIP.search(subject["name"]):
        return None
    if kind != "spinoff" and not subject["ticker"]:
        return None
    return {"type": kind, "label": label, "form": form, "company": subject["name"], "ticker": subject["ticker"],
            "cik": subject["cik"], "counterparty": ", ".join(others[:2]) or None}


def _search(form: str, start: str, end: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for page in range(3):
        for attempt in range(3):
            try:
                with sec._lock:
                    r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"forms": form, "dateRange": "custom",
                                     "startdt": start, "enddt": end, "from": page * 100}, headers=sec._ua(), timeout=30)
                    time.sleep(0.4)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                r = None
            time.sleep(1.5 * (attempt + 1))
        else:
            break
        got = r.json().get("hits", {}).get("hits", [])
        hits += got
        if len(got) < 100:
            break
    return hits


def build(covered: dict[str, str], days: int = 30) -> dict[str, Any]:
    """covered: {ticker: 'held'|'watch'|'tracked'} to tag events on your own names."""
    end = date.today()
    start = (end - timedelta(days=days)).isoformat()
    events: dict[tuple[str, Any], dict[str, Any]] = {}
    failed = []
    for form in TYPES:
        try:
            hits = _search(form, start, end.isoformat())
        except Exception:
            failed.append(form)
            continue
        for h in hits:
            s = h.get("_source", {})
            ev = classify(s.get("form") or form, s.get("display_names") or [])
            if not ev:
                continue
            acc, doc = h["_id"].split(":", 1)
            ev.update({"date": s.get("file_date"), "url": sec_events._url(int((s.get("ciks") or [0])[0]), acc, doc)})
            k = (ev["type"], ev["cik"] or ev["company"])
            if k not in events or (ev["date"] or "") > (events[k]["date"] or ""):
                events[k] = ev  # one row per situation, newest filing
    rows = sorted(events.values(), key=lambda e: e["date"] or "", reverse=True)

    def enrich(e: dict[str, Any]) -> dict[str, Any]:
        if e["ticker"]:
            try:
                i = equity.info(e["ticker"]) or {}
                e["mcap"] = i.get("marketCap")
                e["sector"] = i.get("sector")
            except Exception:
                pass
        e["own"] = covered.get(e["ticker"] or "")
        return e
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(enrich, rows))
    counts: dict[str, int] = {}
    for e in rows:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    return {"ok": True, "events": rows, "counts": counts, "from": start, "to": end.isoformat(), "failed": failed,
            "labels": {v[0]: v[1] for v in TYPES.values()}}
