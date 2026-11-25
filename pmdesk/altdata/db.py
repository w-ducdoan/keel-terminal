"""ALT storage (data/alt.db): series, their points (with a revision log), and each ticker's signals."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from ..config import DATA_DIR

_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DATA_DIR / "alt.db")
    c.execute("CREATE TABLE IF NOT EXISTS series (id TEXT PRIMARY KEY, source TEXT, title TEXT, unit TEXT, freq TEXT, agg TEXT, "
              "growth TEXT, kind TEXT, meta TEXT, pulled TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS points (series_id TEXT, date TEXT, value REAL, pulled TEXT, PRIMARY KEY (series_id, date))")
    c.execute("CREATE TABLE IF NOT EXISTS revisions (series_id TEXT, date TEXT, old REAL, new REAL, pulled TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS signals (ticker TEXT, series_id TEXT, status TEXT, fit REAL, lead INTEGER, n INTEGER, "
              "holdout TEXT, reason TEXT, note TEXT, user_status TEXT, updated TEXT, PRIMARY KEY (ticker, series_id))")
    try:
        with _lock, c:
            yield c
    finally:
        c.close()


def save_series(spec: dict[str, Any], s: pd.Series) -> None:
    """Upsert a series and its points; a changed value is logged as a revision, never silently lost."""
    ts = now()
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (spec["id"], spec["source"], spec["title"], spec.get("unit", ""), spec.get("freq", "M"), spec.get("agg", "sum"),
                   spec.get("growth", "pct"), spec.get("kind", "industry"), json.dumps(spec.get("meta") or {}), ts))
        old = dict(c.execute("SELECT date, value FROM points WHERE series_id = ?", (spec["id"],)).fetchall())
        for d, v in s.dropna().items():
            k = pd.Timestamp(d).date().isoformat()
            v = float(v)
            if k in old and abs(old[k] - v) > 1e-9:
                c.execute("INSERT INTO revisions VALUES (?,?,?,?,?)", (spec["id"], k, old[k], v, ts))
            c.execute("INSERT OR REPLACE INTO points VALUES (?,?,?,?)", (spec["id"], k, v, ts))


def series(series_id: str) -> pd.Series:
    with conn() as c:
        rows = c.execute("SELECT date, value FROM points WHERE series_id = ? ORDER BY date", (series_id,)).fetchall()
    return pd.Series({pd.Timestamp(d): v for d, v in rows}, dtype=float)


def series_meta(series_id: str) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute("SELECT id, source, title, unit, freq, agg, growth, kind, meta, pulled FROM series WHERE id = ?", (series_id,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "source": r[1], "title": r[2], "unit": r[3], "freq": r[4], "agg": r[5], "growth": r[6], "kind": r[7],
            "meta": json.loads(r[8] or "{}"), "pulled": r[9]}


def save_signal(ticker: str, series_id: str, status: str, fit: float | None, lead: int | None, n: int | None,
                holdout: str | None, reason: str, note: str = "") -> None:
    """Keeps the user's own call (promoted / dismissed) when a rerun updates the numbers."""
    with conn() as c:
        prev = c.execute("SELECT user_status FROM signals WHERE ticker = ? AND series_id = ?", (ticker, series_id)).fetchone()
        c.execute("INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (ticker, series_id, status, fit, lead, n, holdout, reason, note, prev[0] if prev else None, now()))


def set_user_status(ticker: str, series_id: str, user_status: str | None) -> None:
    with conn() as c:
        c.execute("UPDATE signals SET user_status = ? WHERE ticker = ? AND series_id = ?", (user_status, ticker, series_id))


def signals(ticker: str | None = None) -> list[dict[str, Any]]:
    q = ("SELECT s.ticker, s.series_id, s.status, s.fit, s.lead, s.n, s.holdout, s.reason, s.note, s.user_status, s.updated, "
         "m.title, m.source, m.unit, m.freq, m.growth, m.kind FROM signals s LEFT JOIN series m ON m.id = s.series_id")
    args: tuple = ()
    if ticker:
        q += " WHERE s.ticker = ?"
        args = (ticker,)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    keys = ["ticker", "series_id", "status", "fit", "lead", "n", "holdout", "reason", "note", "user_status", "updated",
            "title", "source", "unit", "freq", "growth", "kind"]
    return [dict(zip(keys, r)) for r in rows]
