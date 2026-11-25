"""ALT: alternative data found per company, tested against what the company reports, kept only when it earns trust.

    profile(t)      what Keel knows about a company: name, sector, industry, business description, fund or stock
    discover(t)     ask every source what it could offer for that company, pull it, test it, record the result
    run_all()       discovery for the book, watchlist and tracking list (nightly, private Keel only)
    cards()         DO TODAY cards for promoted signals that just moved far outside their own history

Statuses: proposed (passed the test, waiting for you), weak, rejected (failed), context (positioning data: shown,
not tested), needs_key (waits for a free key), untested (no quarterly revenue to test against), no_data.
Your own call (promoted / dismissed) sticks across reruns. Rule-based throughout; no AI calls.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

import pandas as pd

from . import db, validate
from .sources import all_modules

log = logging.getLogger("pmdesk.alt")
REFRESH_DAYS = {"M": 7, "W": 3, "D": 1}
_running: set[str] = set()
_run_lock = threading.Lock()


def profile(t: str) -> dict[str, Any]:
    from .. import equity
    from ..newsrange import short_name
    i = equity.info(t) or {}
    name = i.get("longName") or i.get("shortName") or t
    return {"ticker": t, "name": name, "short": short_name(name), "sector": i.get("sector") or i.get("category") or "",
            "industry": i.get("industry") or "", "summary": (i.get("longBusinessSummary") or "")[:3000],
            "quote_type": i.get("quoteType")}


def _fresh(spec: dict[str, Any]) -> bool:
    m = db.series_meta(spec["id"])
    if not m or not m.get("pulled"):
        return False
    age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(m["pulled"])).total_seconds() / 86400
    return age < REFRESH_DAYS.get(spec.get("freq", "M"), 7)


def pull(spec: dict[str, Any]) -> pd.Series:
    if not _fresh(spec):
        s = spec["_mod"].fetch(spec)
        db.save_series({k: v for k, v in spec.items() if not k.startswith("_") and k not in ("fleet",)} |
                       {"meta": {k: spec[k] for k in ("reason", "needs") if spec.get(k)}}, s)
    return db.series(spec["id"])


def discover(t: str) -> dict[str, Any]:
    """Find, pull and test every candidate for one company. Safe to call repeatedly (pulls are cached)."""
    t = t.upper()
    with _run_lock:
        if t in _running:
            return {"ok": True, "running": True}
        _running.add(t)
    try:
        p = profile(t)
        specs = []
        for mod in all_modules():
            try:
                for s in mod.candidates(p):
                    specs.append({**s, "_mod": mod})
            except Exception as e:
                log.warning("ALT %s candidates %s: %s", mod.__name__, t, e)
        rev = validate.revenue(t) if p.get("quote_type") in (None, "EQUITY") else None
        found = 0
        for s in specs:
            if s.get("needs"):
                db.save_series({k: v for k, v in s.items() if not k.startswith("_") and k != "fleet"} | {"meta": {"reason": s["reason"], "needs": s["needs"]}}, pd.Series(dtype=float))
                db.save_signal(t, s["id"], "needs_key", None, None, None, None, s["reason"], f"Waits for a free {s['needs']}.")
                continue
            try:
                series = pull(s)
            except Exception as e:
                log.warning("ALT pull %s for %s: %s", s["id"], t, e)
                db.save_signal(t, s["id"], "no_data", None, None, None, None, s["reason"], f"Couldn't pull it: {str(e)[:120]}")
                continue
            if series.dropna().empty:
                db.save_signal(t, s["id"], "no_data", None, None, None, None, s["reason"], "The source returned nothing.")
                continue
            found += 1
            if s.get("kind") == "context":
                db.save_signal(t, s["id"], "context", None, None, None, None, s["reason"], "Positioning context: shown, not tested against revenue.")
                continue
            if rev is None or rev.dropna().size < 12:
                db.save_signal(t, s["id"], "untested", None, None, None, None, s["reason"], "No quarterly revenue on file to test against.")
                continue
            r = validate.test(series, rev, s.get("agg", "sum"), s.get("growth", "pct"))
            db.save_signal(t, s["id"], r["status"], r.get("fit"), r.get("lead"), r.get("n"), r.get("holdout"), s["reason"], r.get("note", ""))
        return {"ok": True, "ticker": t, "candidates": len(specs), "with_data": found}
    finally:
        with _run_lock:
            _running.discard(t)


def run_all(tickers: list[str]) -> None:
    for t in tickers:
        try:
            discover(t)
        except Exception as e:
            log.warning("ALT discover %s: %s", t, e)


def watch(get_tickers) -> None:
    """Private Keel: discovery for every name once a night (pulls are cached, so reruns are cheap)."""
    time.sleep(120)
    while True:
        try:
            run_all(get_tickers())
        except Exception as e:
            log.warning("ALT nightly run failed: %s", e)
        time.sleep(24 * 3600)


def view(t: str) -> dict[str, Any]:
    """Everything the ALT window shows for one ticker."""
    rows = db.signals(t.upper())
    for r in rows:
        s = db.series(r["series_id"])
        q = validate.latest_change(s, r.get("growth") or "pct", r.get("freq") or "M")
        r.update(q)
        r["spark"] = [round(float(x), 3) for x in s.dropna().iloc[-36:]]
        r["last_date"] = s.dropna().index[-1].date().isoformat() if not s.dropna().empty else None
        m = db.series_meta(r["series_id"]) or {}
        r["needs"] = (m.get("meta") or {}).get("needs")
        r["shown_status"] = r["user_status"] or r["status"]
    order = {"active": 0, "proposed": 1, "context": 2, "weak": 3, "needs_key": 4, "untested": 5, "rejected": 6, "no_data": 7, "dismissed": 8}
    rows.sort(key=lambda r: (order.get(r["shown_status"], 9), -(r.get("fit") or 0)))
    return {"ok": True, "ticker": t.upper(), "signals": rows, "running": t.upper() in _running}


def cards() -> list[dict[str, Any]]:
    """Promoted signals whose latest change is far outside their own history (|z| >= 2)."""
    out = []
    for r in db.signals():
        if r.get("user_status") != "active":
            continue
        s = db.series(r["series_id"])
        q = validate.latest_change(s, r.get("growth") or "pct", r.get("freq") or "M")
        if q.get("z") is not None and abs(q["z"]) >= 2:
            h = hashlib.md5(f"{r['ticker']}|{r['series_id']}|{q.get('as_of')}".encode()).hexdigest()[:8]
            out.append({"id": f"alt_{h}", "ticker": r["ticker"], "series_id": r["series_id"],
                        "title": f"ALT: {r['title']} {'jumped' if q['z'] > 0 else 'fell'} ({q['label']})",
                        "detail": f"Far outside its usual range (z {q['z']:+.1f}). {r['reason']} Check it against the thesis."})
    return out
