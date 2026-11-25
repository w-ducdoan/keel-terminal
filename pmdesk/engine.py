"""Refresh loop: pull broker + market data, merge with the research folder, compute risk and actions."""
from __future__ import annotations

import logging
import pickle
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from . import changes, equity, hq, market, risk, store, triggers, tripwires
from .broker import Broker
from .config import DATA_DIR, Config

log = logging.getLogger("pmdesk.engine")
ET = ZoneInfo("America/New_York")
HISTORY_FILE = DATA_DIR / "history.pkl"
PROXIES = ["SPY", "IWM", "IWD", "IWF", "MTUM", "QUAL", "TLT", "HYG", "SMH", "USO", "UUP"]


def session(now: datetime | None = None) -> str:
    t = (now or datetime.now(ET)).astimezone(ET)
    if t.weekday() >= 5:
        return "closed"
    hm = t.hour * 60 + t.minute
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "market"
    if 4 * 60 <= hm < 20 * 60:
        return "extended"
    return "closed"


def _ern(t: str, e: dict[str, Any]) -> dict[str, Any] | None:
    """The options-implied earnings move ERN last priced for this report date, if any."""
    x = store.kv_get(f"ern:{t}")
    return x if x and x.get("date") == e.get("date") else None


def _alt_cards() -> list[dict[str, Any]]:
    """DO TODAY cards from promoted ALT signals that moved far outside their history (never breaks a refresh)."""
    try:
        from . import altdata
        return altdata.cards()
    except Exception:
        return []


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        rh = cfg.settings.get("robinhood") or {}
        if rh.get("via", "mcp") == "mcp":  # official Robinhood MCP server (OAuth)
            from .rh_mcp import McpBroker
            self.broker = McpBroker(rh.get("account_suffix", ""))
        else:  # legacy unofficial API via robin_stocks
            self.broker = Broker()
        self.state: dict[str, Any] = {"ready": False}
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._wake = threading.Event()
        self._slow_running = False
        self.last_changes: list[dict[str, Any]] = []
        self.history: pd.DataFrame | None = None
        self.history_at: float = 0.0
        self.risk: dict[str, Any] = {"ok": False, "error": "Loading price history"}
        self.earnings: dict[str, Any] = store.kv_get("earnings") or {}
        self.fred: dict[str, Any] = store.kv_get("fred") or {}
        self._load_history_cache()

    # ---------- caches ----------

    def _load_history_cache(self) -> None:
        if HISTORY_FILE.exists():
            try:
                with HISTORY_FILE.open("rb") as f:
                    self.history, self.history_at = pickle.load(f)
            except Exception:
                self.history, self.history_at = None, 0.0

    def _refresh_history(self, symbols: list[str]) -> None:
        stale = time.time() - self.history_at > self.cfg.settings["refresh"]["history_hours"] * 3600
        missing = self.history is None or any(s not in self.history.columns for s in symbols)
        if not (stale or missing):
            return
        try:
            df = market.history(sorted(set(symbols) | set(PROXIES)), self.cfg.settings.get("history_period", "2y"))
            if not df.empty:
                self.history, self.history_at = df, time.time()
                with HISTORY_FILE.open("wb") as f:
                    pickle.dump((df, self.history_at), f)
        except Exception as e:
            log.warning("history refresh failed: %s", e)

    def _refresh_slow(self, held: list[str], watch: list[str]) -> None:
        """Earnings dates and FRED, once a day. Runs in its own thread."""
        today = date.today().isoformat()
        fred_ok = bool(self.fred) or not self.cfg.settings.get("fred") or not market.fred_key()
        if store.kv_get("slow_day") == today and self.earnings and fred_ok:
            return
        earnings: dict[str, Any] = {}
        for t in sorted(set(held) | set(watch)):
            e = self.broker.next_earnings(t) if self.broker.status.get("connected") else None
            earnings[t] = e or market.next_earnings_yahoo(t)
        self.earnings = earnings
        store.kv_set("earnings", earnings)
        fred = {}
        for s in self.cfg.settings.get("fred", []):
            v = market.fred_latest(s["series"])
            if v:
                fred[s["series"]] = {**v, "label": s["label"], "scale": s.get("scale", 1)}
        if fred:
            self.fred = fred
            store.kv_set("fred", fred)
        store.kv_set("slow_day", today)

    # ---------- position changes ----------

    def _detect_changes(self, cur: dict[str, float], positions: list[dict[str, Any]],
                        warnings: list[str]) -> list[dict[str, Any]]:
        prev = store.kv_get("holdings_last")
        now_et = datetime.now(ET)
        if prev and changes.looks_broken(prev.get("shares") or {}, cur):
            warnings.append("Robinhood returned an incomplete position list; change check skipped this refresh.")
            return []
        found: list[dict[str, Any]] = []
        if prev:
            diffs = changes.diff(prev.get("shares") or {}, cur)
            if diffs:
                fills: list[dict[str, Any]] = []
                fills_fn = getattr(self.broker, "fills_since", None)
                if fills_fn:
                    since_day = (date.fromisoformat(prev["date"]) - timedelta(days=1)).isoformat()
                    try:
                        fills = fills_fn(since_day)
                    except Exception as e:  # the change still gets recorded, just without its fill
                        log.warning("fills lookup failed: %s", e)
                weights = {p["ticker"]: p.get("weight") for p in positions}
                prices = {p["ticker"]: p.get("price") for p in positions}
                try:
                    spy = equity.quote("SPY").get("last")
                except Exception:
                    spy = None
                for d in diffs:
                    m = changes.match(d, fills, prev.get("ts_utc"))
                    m["weight"] = weights.get(d["ticker"])
                    store.add_change(now_et.date().isoformat(), m)
                    found.append(m)
                    # Decision journal: one entry per trade, waiting for a one-line reason.
                    side = "buy" if d["delta"] > 0 else "sell"
                    day = now_et.date().isoformat()
                    store.add_journal(day, d["ticker"], d["kind"], abs(d["delta"]), m.get("price") or prices.get(d["ticker"]),
                                      spy, "detected", f"fill:{day}:{d['ticker']}:{side}")
        store.kv_set("holdings_last", {"shares": cur, "date": now_et.date().isoformat(),
                                       "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")})
        return found

    # ---------- main refresh ----------

    def refresh(self) -> None:
        with self._refresh_lock:
            try:
                self._refresh()
            except Exception as e:  # keep the loop alive; show the error in the UI
                log.exception("refresh failed")
                with self._lock:
                    self.state["error"] = str(e)

    def _refresh(self) -> None:
        cfg = self.cfg
        H = cfg.hq
        today = datetime.now(ET).date()
        warnings: list[str] = []

        port = hq.parse_portfolio(H)
        wl = hq.parse_watchlist(H)
        pod = hq.parse_event_pod(H)
        regime = hq.parse_regime(H)
        digest = hq.parse_digest(H, today)
        warnings += port["warnings"] + wl["warnings"]
        hq_by = {h["ticker"]: h for h in port["holdings"]}

        snap = self.broker.snapshot()
        if snap:
            source = "robinhood"
            raw_positions = snap["positions"]
            quotes = dict(snap["quotes"])
            cash = snap["cash"]
        else:
            source = "fallback"
            raw_positions = [{"ticker": h["ticker"], "shares": h["shares"], "avg_cost": h["avg_cost"]}
                             for h in port["holdings"] if h["shares"]]
            quotes = {}
            cash = port["cash_estimate"] or 0.0

        held = [p["ticker"] for p in raw_positions]
        watch_names = [n for n in wl["names"] if not n.get("held_only")]
        watch = [n["ticker"] for n in watch_names]
        pod_t = [e["ticker"] for e in pod]
        cross = [c["symbol"] for c in cfg.settings.get("cross_asset", [])]
        bench = cfg.settings.get("benchmark", "SPY")
        need = [s for s in held if s not in quotes] + watch + pod_t + cross + [bench]
        quotes.update({k: v for k, v in market.quotes(need).items() if k not in quotes})

        # Positions
        positions = []
        for p in raw_positions:
            t = p["ticker"]
            q = quotes.get(t, {})
            info = hq_by.get(t, {})
            ov = cfg.ticker_override(t)
            price = q.get("last")
            shares = p["shares"] or 0.0
            sector = ov.get("sector") or info.get("sector")
            sleeve = ov.get("sleeve") or info.get("sleeve")
            prov = None
            if not sector:  # bought but not yet in portfolio.md: classify from Yahoo until the row exists
                prov = equity.provisional_class(t)
                sector, sleeve = prov["sector"], sleeve or prov["sleeve"]
            sleeve = sleeve or "core"
            if any(e["ticker"] == t and e["status"] in ("open", "live", "entered") for e in pod):
                sleeve = "event_pod"
            value = shares * price if price else 0.0
            prev = q.get("prev_close")
            positions.append({
                "ticker": t, "company": info.get("company") or (prov or {}).get("company", ""), "shares": shares,
                "provisional": bool(prov), "industry": (prov or {}).get("industry", ""),
                "avg_cost": p.get("avg_cost"), "price": price, "prev_close": prev,
                "day_pct": q.get("change_pct"), "day_pnl": shares * (price - prev) if (price and prev) else None,
                "value": value, "cost": shares * p["avg_cost"] if p.get("avg_cost") else None,
                "sector": sector, "sleeve": sleeve, "bucket": port["buckets"].get(t, ""),
                "thesis_path": info.get("thesis_path"), "thesis_status": info.get("thesis_status", ""),
                "action_flag": info.get("action_flag", ""), "quote_time": q.get("time"),
                "quote_source": q.get("source"),
            })
            if not price:
                warnings.append(f"No price for {t}")

        invested = sum(p["value"] for p in positions)
        nav = invested + cash
        for p in positions:
            p["weight"] = p["value"] / nav if nav else 0.0
            p["unreal_pnl"] = (p["value"] - p["cost"]) if p.get("cost") else None
            p["unreal_pct"] = (p["price"] / p["avg_cost"] - 1) if (p.get("avg_cost") and p.get("price")) else None

        # Reconciliation (broker vs portfolio.md)
        recon = []
        if source == "robinhood":
            bro = {p["ticker"]: p["shares"] for p in raw_positions}
            for t, sh in bro.items():
                if t not in hq_by:
                    recon.append({"ticker": t, "title": "Held at Robinhood, missing from portfolio.md",
                                  "detail": f"{sh:g} shares at the broker. Add a Holdings row."})
                elif hq_by[t]["shares"] is not None and abs(hq_by[t]["shares"] - sh) > 1e-4:
                    recon.append({"ticker": t, "title": "Share count differs from portfolio.md",
                                  "detail": f"Broker {sh:g} vs portfolio.md {hq_by[t]['shares']:g}."})
            for t, h in hq_by.items():
                if t not in bro and h["shares"]:
                    recon.append({"ticker": t, "title": "In portfolio.md but not held at Robinhood",
                                  "detail": f"portfolio.md shows {h['shares']:g} shares. Log the exit."})

        # Position changes since the last Robinhood refresh (new, add, trim, exit), matched to fills
        found: list[dict[str, Any]] = []
        if source == "robinhood":
            found = self._detect_changes({p["ticker"]: float(p["shares"] or 0) for p in raw_positions}, positions, warnings)
        self.last_changes = found

        # Aggregates
        def group(key: str) -> dict[str, float]:
            out: dict[str, float] = {}
            for p in positions:
                k = p[key] or "Unassigned"
                out[k] = out.get(k, 0.0) + p["weight"]
            return dict(sorted(out.items(), key=lambda kv: -kv[1]))

        aggregates = {"sector": group("sector"), "sleeve": group("sleeve"), "bucket": group("bucket"),
                      "cash": cash / nav if nav else 0.0}

        # History + risk (history refresh is skipped when the cache is fresh)
        self._refresh_history(held + [n["ticker"] for n in watch_names if n.get("buy_below")] + [bench])
        if self.history is not None and nav > 0:
            weights = {p["ticker"]: p["weight"] for p in positions if p["price"]}
            self.risk = risk.compute(weights, self.history, cfg.rules, nav, bench)
        if self.risk.get("ok"):
            for p in positions:
                r = self.risk["names"].get(p["ticker"], {})
                p.update(beta=r.get("beta"), vol=r.get("vol"), risk_share=r.get("risk_share"),
                         factors=r.get("factors"), r2=r.get("r2"))
        if self.history is not None:
            for p in positions:
                if p["ticker"] in self.history.columns:
                    p["spark"] = [round(float(x), 2) for x in self.history[p["ticker"]].dropna().iloc[-90:]]

        # Watchlist + event pod with live prices
        watchlist = []
        for n in watch_names:
            q = quotes.get(n["ticker"], {})
            px = q.get("last")
            bb = n.get("buy_below")
            watchlist.append({
                **{k: n.get(k) for k in ("ticker", "company", "sector", "buy_below", "starter_pct", "base_value",
                                         "entry_criteria", "next_catalyst", "thesis_path", "fair_value")},
                "notes": n.get("notes", [])[:2], "flags": n.get("flags", [])[-3:],
                "price": px, "day_pct": q.get("change_pct"),
                "dist_pct": (px / bb - 1) if (px and bb) else None,
                "upside_base": (n["base_value"] / px - 1) if (px and n.get("base_value")) else None,
                "held": n["ticker"] in held, "earnings": self.earnings.get(n["ticker"]),
            })
        watchlist.sort(key=lambda w: (w["dist_pct"] is None, w["dist_pct"] if w["dist_pct"] is not None else 0))
        for e in pod:
            e["price"] = quotes.get(e["ticker"], {}).get("last")
            e["dist_pct"] = (e["price"] / e["entry_below"] - 1) if (e["price"] and e.get("entry_below")) else None
        for p in positions:
            p["earnings"] = self.earnings.get(p["ticker"])

        # Earnings + FRED in the background (daily)
        stock_names = [p["ticker"] for p in positions if p["sleeve"] not in ("etf", "hedge")]
        if not self._slow_running:
            self._slow_running = True

            def slow() -> None:
                try:
                    self._refresh_slow(stock_names, watch)
                finally:
                    self._slow_running = False

            threading.Thread(target=slow, daemon=True).start()

        try:
            trips = tripwires.evaluate(cfg.hq, positions)
        except Exception as e:
            trips = []
            log.warning("tripwires failed: %s", e)
        actions = triggers.build({
            "rules": cfg.rules, "nav": nav, "positions": positions, "aggregates": aggregates,
            "history": self.history, "today": today,
            "bench_day_pct": quotes.get(bench, {}).get("change_pct"), "watchlist": watchlist, "event_pod": pod,
            "earnings": self.earnings, "digest": digest, "recon": [], "warnings": warnings,  # recon lives in RECONCILE
            "sec_events": store.sec_events((today - timedelta(days=3)).isoformat()),
            "beta_overrides": cfg.overrides.get("beta_overrides") or {}, "states": store.action_states(),
            "tripwires": trips, "alt_cards": _alt_cards(),
        })

        # Book-level P&L
        day_pnl = sum(p["day_pnl"] or 0 for p in positions)
        prev_nav = nav - day_pnl
        if source == "robinhood" and snap and snap.get("prev_equity"):
            prev_nav = snap["prev_equity"]
            day_pnl = nav - prev_nav
        store.record_nav(nav, cash, source)
        start_et = datetime.now(ET).replace(hour=4, minute=0, second=0, microsecond=0)
        intraday = store.nav_since(start_et.astimezone(timezone.utc).isoformat())

        cross_rows = []
        for c in cfg.settings.get("cross_asset", []):
            q = quotes.get(c["symbol"], {})
            cross_rows.append({**c, "last": q.get("last"), "change": q.get("change"),
                               "change_pct": q.get("change_pct"), "time": q.get("time")})

        movers = sorted([p for p in positions if p.get("day_pnl") is not None], key=lambda p: p["day_pnl"])
        upcoming = sorted(
            [{"ticker": t, **e, "held": t in held, "implied": _ern(t, e)} for t, e in self.earnings.items()
             if e and e.get("date") and 0 <= (date.fromisoformat(e["date"]) - today).days <= 14],
            key=lambda x: x["date"])

        state = {
            "ready": True,
            "error": None,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "session": session(),
            "source": source,
            "broker": dict(self.broker.status),
            "account": {"nav": nav, "cash": cash, "invested": invested, "day_pnl": day_pnl,
                        "day_pct": day_pnl / prev_nav if prev_nav else None,
                        "unreal_pnl": sum(p["unreal_pnl"] or 0 for p in positions),
                        "cash_note": None if source == "robinhood" else "Cash is the portfolio.md estimate",
                        "intraday": intraday},
            "positions": sorted(positions, key=lambda p: -p["value"]),
            "aggregates": aggregates,
            "risk": self.risk,
            "watchlist": watchlist,
            "event_pod": pod,
            "actions": actions,
            "regime": regime,
            "digest": digest[:7],
            "cross_asset": cross_rows,
            "fred": list(self.fred.values()),
            "movers": {"up": movers[::-1][:3], "down": movers[:3]},
            "earnings_upcoming": upcoming,
            "tripwires": trips,
            "caps": cfg.rules["caps"],
            "benchmark": {"symbol": bench, **quotes.get(bench, {})},
            "recon": recon,
            "sec_events": store.sec_events((datetime.now(ET).date() - timedelta(days=14)).isoformat()),
            "changes": store.recent_changes((datetime.now(ET).date() - timedelta(days=14)).isoformat()),
            "changes_found": found,
            "warnings": warnings,
            "paths": {"portfolio": port["path"], "watchlist": wl["path"], "regime": regime["path"]},
        }
        with self._lock:
            self.state = state

    # ---------- loop ----------

    def interval(self) -> int:
        r = self.cfg.settings["refresh"]
        return int(r.get(session(), r["closed"]))

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

    def ack(self, action_id: str, state: str, hours: float) -> None:
        until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        store.set_action(action_id, state, until)
        with self._lock:
            for a in self.state.get("actions", []):
                if a["id"] == action_id:
                    a["state"], a["until"] = state, until
