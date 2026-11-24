"""Robinhood feed (read-only) through the unofficial API in robin_stocks.

The app never prompts for credentials. Sign in once with `python -m pmdesk.login`;
that saves a session token in .secrets/. This module only loads that token,
tries a refresh when it expires, and reports `needs_login` otherwise.
Nothing here places, changes or cancels orders.
"""
from __future__ import annotations

import logging
import pickle
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import SECRETS_DIR

log = logging.getLogger("pmdesk.broker")

PICKLE_NAME = "pmdesk"
PICKLE_PATH = SECRETS_DIR / f"robinhood{PICKLE_NAME}.pickle"
CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"  # Robinhood web client id used by robin_stocks

_lock = threading.Lock()


def _f(x: Any) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


class Broker:
    def __init__(self) -> None:
        self.status: dict[str, Any] = {"connected": False, "state": "not_started", "error": None,
                                       "last_ok": None}
        self._symbols: dict[str, str] = {}

    # ---------- session ----------

    def connect(self) -> bool:
        with _lock:
            return self._connect()

    def _connect(self) -> bool:
        from robin_stocks.robinhood import helper, urls

        if not PICKLE_PATH.exists():
            self.status.update(connected=False, state="needs_login",
                               error="No saved Robinhood session. Run: python -m pmdesk.login")
            return False
        try:
            with PICKLE_PATH.open("rb") as f:
                data = pickle.load(f)
        except Exception as e:
            self.status.update(connected=False, state="needs_login", error=f"Session file unreadable: {e}")
            return False

        def try_token(tok: dict[str, Any]) -> bool:
            helper.update_session("Authorization", f"{tok['token_type']} {tok['access_token']}")
            helper.set_login_state(True)
            try:
                res = helper.request_get(urls.positions_url(), "pagination", {"nonzero": "true"},
                                         jsonify_data=False)
                res.raise_for_status()
                return True
            except Exception:
                return False

        if try_token(data):
            self.status.update(connected=True, state="live", error=None)
            return True

        # Access token expired: try the refresh token once.
        try:
            payload = {
                "grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                "scope": "internal", "client_id": CLIENT_ID, "expires_in": 86400,
                "device_token": data.get("device_token"),
            }
            helper.update_session("Authorization", None)
            new = helper.request_post(urls.login_url(), payload)
            if new and "access_token" in new:
                data.update(token_type=new["token_type"], access_token=new["access_token"],
                            refresh_token=new.get("refresh_token", data["refresh_token"]))
                with PICKLE_PATH.open("wb") as f:
                    pickle.dump(data, f)
                if try_token(data):
                    self.status.update(connected=True, state="live", error=None)
                    return True
        except Exception as e:
            log.info("token refresh failed: %s", e)

        helper.set_login_state(False)
        self.status.update(connected=False, state="needs_login",
                           error="Robinhood session expired. Run: python -m pmdesk.login")
        return False

    # ---------- data ----------

    def _symbol(self, instrument_url: str) -> str | None:
        from robin_stocks.robinhood import stocks

        if instrument_url in self._symbols:
            return self._symbols[instrument_url]
        try:
            sym = stocks.get_instrument_by_url(instrument_url).get("symbol")
        except Exception:
            sym = None
        if sym:
            self._symbols[instrument_url] = sym
        return sym

    def snapshot(self) -> dict[str, Any] | None:
        """Positions, quotes and cash in a handful of calls. None on failure."""
        with _lock:
            if not self.status.get("connected") and not self._connect():
                return None
            try:
                return self._snapshot()
            except Exception as e:
                log.warning("Robinhood snapshot failed: %s", e)
                self.status.update(error=f"Snapshot failed: {e}")
                # A 401 means the token died mid-session; next call reconnects.
                if "401" in str(e):
                    self.status.update(connected=False, state="needs_login")
                return None

    def _snapshot(self) -> dict[str, Any]:
        from robin_stocks.robinhood import account, profiles, stocks

        raw = account.get_open_stock_positions() or []
        positions = []
        for p in raw:
            qty = _f(p.get("quantity"))
            if not qty:
                continue
            sym = p.get("symbol") or self._symbol(p.get("instrument", ""))
            if not sym:
                continue
            positions.append({"ticker": sym, "shares": qty, "avg_cost": _f(p.get("average_buy_price"))})

        symbols = [p["ticker"] for p in positions]
        quotes: dict[str, dict[str, Any]] = {}
        if symbols:
            for q in stocks.get_quotes(symbols) or []:
                if not q:
                    continue
                last = _f(q.get("last_extended_hours_trade_price")) or _f(q.get("last_trade_price"))
                regular = _f(q.get("last_trade_price"))
                prev = _f(q.get("adjusted_previous_close")) or _f(q.get("previous_close"))
                quotes[q["symbol"]] = {
                    "last": last, "regular": regular, "prev_close": prev,
                    "change": (last - prev) if (last and prev) else None,
                    "change_pct": ((last - prev) / prev) if (last and prev) else None,
                    "time": q.get("updated_at"), "source": "robinhood",
                }

        acct = profiles.load_account_profile() or {}
        port = profiles.load_portfolio_profile() or {}
        cash = _f(acct.get("cash")) or 0.0
        uncleared = _f(acct.get("uncleared_deposits")) or 0.0
        equity = _f(port.get("extended_hours_equity")) or _f(port.get("equity"))
        prev_equity = _f(port.get("adjusted_equity_previous_close")) or _f(port.get("equity_previous_close"))

        self.status.update(connected=True, state="live", error=None,
                           last_ok=datetime.now(timezone.utc).isoformat())
        return {
            "positions": positions, "quotes": quotes, "cash": cash + uncleared,
            "buying_power": _f(acct.get("buying_power")), "equity": equity,
            "prev_equity": prev_equity, "account_suffix": str(acct.get("account_number", ""))[-4:],
        }

    def recent_orders(self, days: int = 30) -> list[dict[str, Any]]:
        from robin_stocks.robinhood import orders

        with _lock:
            if not self.status.get("connected"):
                return []
            try:
                start = (date.today() - timedelta(days=days)).isoformat()
                raw = orders.get_all_stock_orders(start_date=start) or []
            except Exception as e:
                log.warning("orders failed: %s", e)
                return []
            out = []
            for o in raw:
                sym = o.get("symbol") or self._symbol(o.get("instrument", ""))
                out.append({
                    "ticker": sym, "side": o.get("side"), "state": o.get("state"),
                    "type": o.get("type"), "quantity": _f(o.get("cumulative_quantity")) or _f(o.get("quantity")),
                    "price": _f(o.get("average_price")) or _f(o.get("price")),
                    "limit": _f(o.get("price")), "created_at": o.get("created_at"),
                    "updated_at": o.get("last_transaction_at") or o.get("updated_at"),
                })
            return out

    def next_earnings(self, symbol: str) -> dict[str, Any] | None:
        from robin_stocks.robinhood import stocks

        with _lock:
            if not self.status.get("connected"):
                return None
            try:
                rows = stocks.get_earnings(symbol) or []
            except Exception:
                return None
        today = date.today().isoformat()
        upcoming = []
        for r in rows:
            rep = (r or {}).get("report") or {}
            d = rep.get("date")
            if d and d >= today:
                upcoming.append({"date": d, "timing": rep.get("timing"),
                                 "verified": rep.get("verified"), "source": "robinhood"})
        return min(upcoming, key=lambda x: x["date"]) if upcoming else None
