"""Public edition: Keel as a market terminal with no portfolio behind it.

Turned on with the environment variable KEEL_PUBLIC=1. Then:
- no broker connection, no research folder, no positions, watchlist, journal or actions;
- the state carries only market data (cross-asset quotes, the benchmark, FRED macro series);
- the API answers only market endpoints (allowlist below) and refuses every write, so one visitor
  can never change what another sees, and nothing on the host's disk can be opened.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, datetime, timezone
from typing import Any

from . import market, store
from .config import Config

log = logging.getLogger("pmdesk.public")


def enabled() -> bool:
    """KEEL_PUBLIC=1, or a PUBLIC_EDITION file next to run.py (the public repo ships one)."""
    from .config import ROOT
    return os.environ.get("KEEL_PUBLIC", "").strip().lower() in ("1", "true", "yes", "on") or (ROOT / "PUBLIC_EDITION").exists()


# GET routes a public visitor may use. Everything else (and every POST) answers 404.
ALLOW = [
    r"/api/state", r"/api/series", r"/api/markets", r"/api/lookup(/more)?", r"/api/news", r"/api/cnrange/[^/]+",
    r"/api/why/[^/]+", r"/api/equity/[^/]+/card", r"/api/equity/[^/]+/(des|anr|cacs|rv|fa|ee|hds|ins|cn|flags|splc)",
    r"/api/splc/[^/]+", r"/api/13f", r"/api/13f/funds/search", r"/api/13f/fund/[^/]+", r"/api/heatmap", r"/api/eventfeed",
    r"/api/calendar", r"/api/sec/[^/]+",
]
_ALLOW = re.compile(r"^(" + "|".join(ALLOW) + r")/?$")


def allowed(method: str, path: str) -> bool:
    if not path.startswith("/api/"):
        return method in ("GET", "HEAD")
    return method in ("GET", "HEAD") and bool(_ALLOW.match(path))


class NullBroker:
    status = {"connected": False, "mode": "public"}

    def snapshot(self) -> None:
        return None

    def next_earnings(self, t: str) -> None:
        return None


class PublicEngine:
    """Same interface the server uses (snapshot, refresh, run_forever, poke), market data only."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.broker = NullBroker()
        self.state: dict[str, Any] = {"ready": False, "public": True}
        self.last_changes: list[dict[str, Any]] = []
        self.fred: dict[str, Any] = store.kv_get("fred") or {}
        self._lock = threading.Lock()
        self._wake = threading.Event()

    def _fred(self) -> None:
        today = date.today().isoformat()
        if self.fred and store.kv_get("fred_day") == today:  # no key: market.fred_latest reads the public CSV
            return
        out = {}
        for s in self.cfg.settings.get("fred", []):
            v = market.fred_latest(s["series"])
            if v:
                out[s["series"]] = {**v, "label": s["label"], "scale": s.get("scale", 1)}
        if out:
            self.fred = out
            store.kv_set("fred", out)
            store.kv_set("fred_day", today)

    def refresh(self) -> None:
        try:
            self._refresh()
        except Exception as e:
            log.exception("public refresh failed")
            with self._lock:
                self.state = {**self.state, "error": str(e)}

    def _refresh(self) -> None:
        from .engine import session
        cfg = self.cfg
        bench = cfg.settings.get("benchmark", "SPY")
        cross = cfg.settings.get("cross_asset", [])
        quotes = market.quotes([c["symbol"] for c in cross] + [bench])
        threading.Thread(target=self._fred, daemon=True).start()
        rows = [{**c, "last": quotes.get(c["symbol"], {}).get("last"), "change": quotes.get(c["symbol"], {}).get("change"),
                 "change_pct": quotes.get(c["symbol"], {}).get("change_pct"), "time": quotes.get(c["symbol"], {}).get("time")} for c in cross]
        state = {
            "ready": True, "public": True, "error": None, "as_of": datetime.now(timezone.utc).isoformat(), "session": session(),
            "source": "public", "broker": dict(self.broker.status),
            "account": {"nav": 0, "cash": 0, "invested": 0, "day_pnl": 0, "day_pct": None, "unreal_pnl": 0, "intraday": []},
            "positions": [], "aggregates": {"sector": {}, "sleeve": {}, "bucket": {}, "cash": 0}, "risk": {"ok": False, "error": "Public edition"},
            "watchlist": [], "event_pod": [], "actions": [], "regime": {}, "digest": [], "cross_asset": rows,
            "fred": list(self.fred.values()), "movers": {"up": [], "down": []}, "earnings_upcoming": [], "tripwires": [],
            "caps": cfg.rules.get("caps", {}), "benchmark": {"symbol": bench, **quotes.get(bench, {})}, "recon": [],
            "sec_events": [], "changes": [], "changes_found": [], "warnings": [], "paths": {},
        }
        with self._lock:
            self.state = state

    def interval(self) -> int:
        from .engine import session
        r = self.cfg.settings["refresh"]
        return max(60, int(r.get(session(), r["closed"])))

    def run_forever(self) -> None:
        while True:
            self.refresh()
            self._wake.wait(self.interval())
            self._wake.clear()

    def poke(self) -> None:
        self._wake.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self.state

    def ack(self, *a: Any, **k: Any) -> None:
        return None


def inject(html: str) -> str:
    """index.html for the public edition: its own title, the flag, and public.js after shell.js."""
    html = html.replace("<title>Keel · Portfolio Terminal</title>", "<title>Keel · Market Terminal</title>")
    html = html.replace("<script src=\"/static/js/core.js", "<script>window.KEEL_PUBLIC = true;</script>\n  <script src=\"/static/js/core.js", 1)
    m = re.search(r'(  <script src="/static/js/shell\.js\?v=(\d+)"></script>\n)', html)
    if m:
        html = html.replace(m.group(1), m.group(1) + f'  <script src="/static/js/public.js?v={m.group(2)}"></script>\n', 1)
    return html
