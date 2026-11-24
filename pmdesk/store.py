"""Small SQLite store: NAV snapshots and Action Queue state (acknowledged / snoozed)."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .config import DATA_DIR

_lock = threading.Lock()


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Open, commit and always close (sqlite3's own context manager doesn't close)."""
    c = _open()
    try:
        with c:
            yield c
    finally:
        c.close()


def _open() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DATA_DIR / "pmdesk.db")
    c.execute("CREATE TABLE IF NOT EXISTS nav (ts TEXT PRIMARY KEY, nav REAL, cash REAL, source TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS action_state (id TEXT PRIMARY KEY, state TEXT, until TEXT, ts TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, ts TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS transfers (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, amount REAL, "
              "source TEXT, seen_ts TEXT, logged INTEGER DEFAULT 0)")
    return c


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_nav(nav: float, cash: float, source: str) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO nav VALUES (?,?,?,?)", (now(), nav, cash, source))


def nav_since(ts_iso: str) -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT ts, nav FROM nav WHERE ts >= ? ORDER BY ts", (ts_iso,)).fetchall()
    return [{"ts": r[0], "nav": r[1]} for r in rows]


def set_action(action_id: str, state: str, until: str | None) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO action_state VALUES (?,?,?,?)", (action_id, state, until, now()))


def action_states() -> dict[str, dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, state, until, ts FROM action_state").fetchall()
    return {r[0]: {"state": r[1], "until": r[2], "ts": r[3]} for r in rows}


def kv_get(key: str) -> Any:
    with _lock, _conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def kv_set(key: str, value: Any) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, json.dumps(value), now()))


def kv_del(key: str) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM kv WHERE key=?", (key,))


def add_transfer(day: str, amount: float, source: str = "observed") -> None:
    """A deposit (+) or withdrawal (-) the desk saw land in the account on `day`."""
    with _lock, _conn() as c:
        c.execute("INSERT INTO transfers (date, amount, source, seen_ts) VALUES (?,?,?,?)", (day, amount, source, now()))


def transfers(since: str = "") -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, date, amount, source, seen_ts, logged FROM transfers WHERE date > ? ORDER BY date, id",
                         (since,)).fetchall()
    return [{"id": r[0], "date": r[1], "amount": r[2], "source": r[3], "seen_ts": r[4], "logged": bool(r[5])} for r in rows]


def mark_transfer_logged(tid: int) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE transfers SET logged = 1 WHERE id = ?", (tid,))


def _changes_table(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS position_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, date TEXT, "
              "ticker TEXT, kind TEXT, old REAL, new REAL, delta REAL, price REAL, fills INTEGER, matched INTEGER, "
              "note TEXT, seen INTEGER DEFAULT 0)")


def add_change(day: str, ch: dict[str, Any]) -> None:
    """One position change found between refreshes (new, add, trim, exit)."""
    with _lock, _conn() as c:
        _changes_table(c)
        c.execute("INSERT INTO position_changes (ts, date, ticker, kind, old, new, delta, price, fills, matched, note) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (now(), day, ch["ticker"], ch["kind"], ch["old"], ch["new"], ch["delta"], ch.get("price"),
                   ch.get("fills", 0), int(bool(ch.get("matched"))), ch.get("note")))


def recent_changes(since: str) -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        _changes_table(c)
        rows = c.execute("SELECT id, ts, date, ticker, kind, old, new, delta, price, fills, matched, note, seen "
                         "FROM position_changes WHERE date >= ? ORDER BY id DESC", (since,)).fetchall()
    keys = ["id", "ts", "date", "ticker", "kind", "old", "new", "delta", "price", "fills", "matched", "note", "seen"]
    return [dict(zip(keys, r)) | {"matched": bool(r[10]), "seen": bool(r[12])} for r in rows]


def mark_changes_seen() -> None:
    with _lock, _conn() as c:
        _changes_table(c)
        c.execute("UPDATE position_changes SET seen = 1 WHERE seen = 0")


def _journal_table(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, date TEXT, ticker TEXT, "
              "kind TEXT, shares REAL, price REAL, spy REAL, source TEXT, reason TEXT DEFAULT '', expect TEXT DEFAULT '', "
              "horizon INTEGER DEFAULT 90, verdict TEXT, review_note TEXT, reviewed_ts TEXT, key TEXT UNIQUE)")


def add_journal(day: str, ticker: str, kind: str, shares: float, price: float | None, spy: float | None,
                source: str, key: str, reason: str = "") -> bool:
    """One decision. `key` keeps the same trade from being logged twice. Returns True if it was new."""
    with _lock, _conn() as c:
        _journal_table(c)
        cur = c.execute("INSERT OR IGNORE INTO journal (ts, date, ticker, kind, shares, price, spy, source, reason, key) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", (now(), day, ticker, kind, shares, price, spy, source, reason, key))
        return cur.rowcount > 0


def journal_entries() -> list[dict[str, Any]]:
    keys = ["id", "ts", "date", "ticker", "kind", "shares", "price", "spy", "source", "reason", "expect",
            "horizon", "verdict", "review_note", "reviewed_ts"]
    with _lock, _conn() as c:
        _journal_table(c)
        rows = c.execute(f"SELECT {', '.join(keys)} FROM journal ORDER BY date DESC, id DESC").fetchall()
    return [dict(zip(keys, r)) for r in rows]


def update_journal(jid: int, **fields: Any) -> None:
    allowed = {"reason", "expect", "horizon", "verdict", "review_note", "reviewed_ts"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with _lock, _conn() as c:
        _journal_table(c)
        c.execute(f"UPDATE journal SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), jid))


def _tracked_table(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS tracked (ticker TEXT PRIMARY KEY, added TEXT, price REAL, spy REAL, theme TEXT, "
              "sector TEXT, company TEXT, note TEXT, status TEXT DEFAULT 'tracking', updated TEXT)")


def track(ticker: str, price: float | None, spy: float | None, theme: str, sector: str, company: str, note: str) -> None:
    """Add (or update the theme and note of) a name on Keel's tracking list. Separate from watchlist.md."""
    with _lock, _conn() as c:
        _tracked_table(c)
        row = c.execute("SELECT ticker FROM tracked WHERE ticker = ?", (ticker,)).fetchone()
        if row:
            c.execute("UPDATE tracked SET theme = ?, note = ?, status = 'tracking', updated = ? WHERE ticker = ?",
                      (theme, note, now(), ticker))
        else:
            c.execute("INSERT INTO tracked (ticker, added, price, spy, theme, sector, company, note, status, updated) "
                      "VALUES (?,?,?,?,?,?,?,?, 'tracking', ?)",
                      (ticker, now()[:10], price, spy, theme, sector, company, note, now()))


def tracked(include_removed: bool = False) -> list[dict[str, Any]]:
    keys = ["ticker", "added", "price", "spy", "theme", "sector", "company", "note", "status", "updated"]
    with _lock, _conn() as c:
        _tracked_table(c)
        q = f"SELECT {', '.join(keys)} FROM tracked" + ("" if include_removed else " WHERE status != 'removed'") + " ORDER BY added DESC"
        rows = c.execute(q).fetchall()
    return [dict(zip(keys, r)) for r in rows]


def set_tracked_status(ticker: str, status: str) -> None:
    with _lock, _conn() as c:
        _tracked_table(c)
        c.execute("UPDATE tracked SET status = ?, updated = ? WHERE ticker = ?", (status, now(), ticker))


def _events_table(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS sec_events (acc TEXT PRIMARY KEY, ticker TEXT, form TEXT, date TEXT, severity TEXT, "
              "title TEXT, url TEXT, kind TEXT, seen INTEGER DEFAULT 0, found_ts TEXT)")


def add_sec_event(ev: dict[str, Any]) -> bool:
    """Store one filing event; returns True if it is new."""
    with _lock, _conn() as c:
        _events_table(c)
        cur = c.execute("INSERT OR IGNORE INTO sec_events (acc, ticker, form, date, severity, title, url, kind, found_ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", (ev["acc"], ev["ticker"], ev["form"], ev["date"], ev.get("severity", "low"),
                                                       ev.get("title", ""), ev.get("url", ""), ev.get("kind", ""), now()))
        return cur.rowcount > 0


def sec_events(since: str) -> list[dict[str, Any]]:
    keys = ["acc", "ticker", "form", "date", "severity", "title", "url", "kind", "seen", "found_ts"]
    with _lock, _conn() as c:
        _events_table(c)
        rows = c.execute(f"SELECT {', '.join(keys)} FROM sec_events WHERE date >= ? ORDER BY date DESC, found_ts DESC", (since,)).fetchall()
    return [dict(zip(keys, r)) | {"seen": bool(r[8])} for r in rows]


def mark_events_seen() -> None:
    with _lock, _conn() as c:
        _events_table(c)
        c.execute("UPDATE sec_events SET seen = 1 WHERE seen = 0")


def _links_table(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS supply_links (ticker TEXT, other TEXT, relation TEXT, pct REAL, note TEXT, ts TEXT, "
              "PRIMARY KEY (ticker, other, relation))")


def add_supply_link(ticker: str, other: str, relation: str, pct: float | None, note: str) -> None:
    with _lock, _conn() as c:
        _links_table(c)
        c.execute("INSERT OR REPLACE INTO supply_links VALUES (?,?,?,?,?,?)", (ticker, other, relation, pct, note, now()))


def supply_links(ticker: str) -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        _links_table(c)
        rows = c.execute("SELECT other, relation, pct, note, ts FROM supply_links WHERE ticker = ?", (ticker,)).fetchall()
    return [{"name": r[0], "ticker": r[0] if re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", r[0]) else None, "relation": r[1], "pct": r[2], "note": r[3], "source": "you", "confidence": "yours"} for r in rows]


def delete_supply_link(ticker: str, other: str, relation: str) -> None:
    with _lock, _conn() as c:
        _links_table(c)
        c.execute("DELETE FROM supply_links WHERE ticker = ? AND other = ? AND relation = ?", (ticker, other, relation))
