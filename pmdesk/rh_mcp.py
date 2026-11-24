"""Robinhood feed through Robinhood's official MCP server (agent.robinhood.com), read-only.

Sign in once with `python -m pmdesk.rh_login`: your browser opens Robinhood's own sign-in
page and the desk never sees your password. The OAuth client registration and tokens are
saved in .secrets/rh_mcp.json (git-ignored); the access token refreshes on its own.

The server also exposes trading tools. This module can only call the tools in READ_TOOLS;
anything else raises before a request is sent. tests/test_rh_mcp.py enforces that.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import SECRETS_DIR

log = logging.getLogger("pmdesk.rh_mcp")

SERVER_URL = "https://agent.robinhood.com/mcp/trading"
TOKEN_FILE = SECRETS_DIR / "rh_mcp.json"
CALLBACK_PORT = 6879
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/callback"

# Every tool the desk may call. All are reads; none places, changes or cancels anything.
READ_TOOLS = frozenset({
    "get_accounts", "get_portfolio", "get_equity_positions", "get_equity_quotes",
    "get_equity_orders", "get_equity_historicals", "get_equity_fundamentals",
    "get_equity_analyst_ratings", "get_equity_tax_lots", "get_realized_pnl",
    "get_pnl_trade_history", "get_earnings_calendar", "get_earnings_results",
    "get_financials", "get_index_quotes", "get_index_historicals", "get_indexes",
    "get_sec_filing_index", "get_sec_filing", "get_banking_transactions", "search",
    "get_option_chains", "get_option_instruments", "get_option_quotes",  # ERN implied move (quotes only)
})


class NotAllowed(RuntimeError):
    """Raised when code tries to call a Robinhood tool outside READ_TOOLS."""


class NeedsLogin(RuntimeError):
    """Raised when there is no usable Robinhood session and a browser sign-in is required."""


def _f(x: Any) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------- token storage ----------

class FileTokenStorage:
    """Keeps the OAuth tokens and client registration in .secrets/rh_mcp.json."""

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, key: str, value: dict[str, Any]) -> None:
        data = self._read()
        data[key] = value
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        t = self._read().get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens) -> None:
        self._write("tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        c = self._read().get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info) -> None:
        self._write("client", client_info.model_dump(mode="json", exclude_none=True))


def has_session() -> bool:
    return bool(FileTokenStorage()._read().get("tokens"))


# ---------- calls ----------

def _provider(interactive: bool):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    async def redirect(url: str) -> None:
        if not interactive:
            raise NeedsLogin("Robinhood sign-in needed. Run: .venv\\Scripts\\python -m pmdesk.rh_login")
        import webbrowser
        print("\nOpening Robinhood's sign-in page in your browser.\nIf it doesn't open, paste this link:\n" + url + "\n")
        webbrowser.open(url)

    async def callback():
        if not interactive:
            raise NeedsLogin("Robinhood sign-in needed.")
        from .rh_login import wait_for_callback
        return await wait_for_callback(CALLBACK_PORT)

    meta = OAuthClientMetadata(
        client_name="PM Desk (local, read-only)",
        redirect_uris=[REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="internal",
    )
    return OAuthClientProvider(SERVER_URL, meta, FileTokenStorage(), redirect_handler=redirect,
                               callback_handler=callback)


def _parse(result: Any) -> Any:
    if getattr(result, "isError", False):
        text = " ".join(getattr(c, "text", "") for c in result.content or [])
        raise RuntimeError(f"Robinhood tool error: {text[:300]}")
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        text = "".join(getattr(c, "text", "") for c in result.content or [])
        payload = json.loads(text) if text else {}
    return payload.get("data", payload) if isinstance(payload, dict) else payload


async def _call_many(calls: list[tuple[str, dict[str, Any]]], interactive: bool = False) -> list[Any]:
    for name, _ in calls:
        if name not in READ_TOOLS:
            raise NotAllowed(f"{name} is not a read-only tool; the desk never calls it")
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    async with create_mcp_http_client(auth=_provider(interactive)) as http:
        async with streamable_http_client(SERVER_URL, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                out = []
                for name, args in calls:
                    out.append(_parse(await session.call_tool(name, args)))
                return out


def call_many(calls: list[tuple[str, dict[str, Any]]], interactive: bool = False) -> list[Any]:
    """Run several read calls over one session. Blocking; safe from any non-async thread."""
    return asyncio.run(_call_many(calls, interactive))


def call(name: str, args: dict[str, Any] | None = None) -> Any:
    return call_many([(name, args or {})])[0]


# ---------- broker (same interface as broker.Broker) ----------

_lock = threading.Lock()


class McpBroker:
    def __init__(self, account_suffix: str) -> None:
        self.account_suffix = str(account_suffix)
        self.account_number: str | None = None
        self.status: dict[str, Any] = {"connected": False, "state": "not_started", "error": None,
                                       "last_ok": None, "via": "robinhood_mcp"}
        self._earn: dict[str, Any] = {}

    def _fail(self, e: Exception) -> None:
        if isinstance(e, NeedsLogin) or "401" in str(e) or "invalid_grant" in str(e):
            self.status.update(connected=False, state="needs_login",
                               error="Robinhood sign-in needed. Run: .venv\\Scripts\\python -m pmdesk.rh_login")
        else:
            self.status.update(error=f"Robinhood call failed: {e}")
        log.warning("Robinhood MCP: %s", e)

    def connect(self) -> bool:
        with _lock:
            return self._connect()

    def _connect(self) -> bool:
        if not has_session():
            self.status.update(connected=False, state="needs_login",
                               error="Robinhood not connected. Run: .venv\\Scripts\\python -m pmdesk.rh_login")
            return False
        try:
            accts = call("get_accounts").get("accounts", [])
        except Exception as e:
            self._fail(e)
            return False
        match = [a for a in accts if str(a.get("account_number", "")).endswith(self.account_suffix)]
        if len(match) != 1:
            self.status.update(connected=False, state="error",
                               error=f"No single Robinhood account ending {self.account_suffix}")
            return False
        self.account_number = match[0]["account_number"]
        self.status.update(connected=True, state="live", error=None)
        return True

    def snapshot(self) -> dict[str, Any] | None:
        with _lock:
            if not self.account_number and not self._connect():
                return None
            try:
                return self._snapshot()
            except Exception as e:
                self._fail(e)
                return None

    def _positions(self) -> list[dict[str, Any]]:
        rows, cursor = [], None
        for _ in range(10):
            args: dict[str, Any] = {"account_number": self.account_number}
            if cursor:
                args["cursor"] = cursor
            page = call("get_equity_positions", args)
            rows += page.get("positions", [])
            cursor = page.get("next")
            if not cursor:
                break
        return rows

    def _snapshot(self) -> dict[str, Any]:
        positions = []
        for p in self._positions():
            qty = _f(p.get("quantity"))
            if qty and p.get("symbol"):
                positions.append({"ticker": p["symbol"], "shares": qty, "avg_cost": _f(p.get("average_buy_price")),
                                  "locked": (_f(p.get("shares_available_for_sells")) or 0) < qty})
        symbols = [p["ticker"] for p in positions]
        calls: list[tuple[str, dict[str, Any]]] = [("get_portfolio", {"account_number": self.account_number})]
        calls += [("get_equity_quotes", {"symbols": symbols[i:i + 20]}) for i in range(0, len(symbols), 20)]
        port, *qpages = call_many(calls)

        quotes: dict[str, dict[str, Any]] = {}
        for page in qpages:
            for r in page.get("results", []):
                q = r.get("quote") or {}
                reg, ext = _f(q.get("last_trade_price")), _f(q.get("last_non_reg_trade_price"))
                use_ext = ext and (q.get("venue_last_non_reg_trade_time") or "") > (q.get("venue_last_trade_time") or "")
                last = ext if use_ext else reg
                prev = _f(q.get("adjusted_previous_close")) or _f(q.get("previous_close"))
                quotes[q.get("symbol")] = {
                    "last": last, "regular": reg, "prev_close": prev,
                    "change": (last - prev) if (last and prev) else None,
                    "change_pct": ((last - prev) / prev) if (last and prev) else None,
                    "time": q.get("venue_last_non_reg_trade_time") if use_ext else q.get("venue_last_trade_time"),
                    "bid": _f(q.get("bid_price")), "ask": _f(q.get("ask_price")), "source": "robinhood",
                }

        self.status.update(connected=True, state="live", error=None,
                           last_ok=datetime.now(timezone.utc).isoformat())
        return {
            "positions": positions, "quotes": quotes, "cash": _f(port.get("cash")) or 0.0,
            "buying_power": _f((port.get("buying_power") or {}).get("buying_power")),
            "equity": _f(port.get("total_value")), "prev_equity": None,
            "account_suffix": self.account_number[-4:],
        }

    def recent_orders(self, days: int = 30) -> list[dict[str, Any]]:
        from datetime import timedelta
        with _lock:
            if not self.account_number:
                return []
            try:
                start = (date.today() - timedelta(days=days)).isoformat()
                raw = call("get_equity_orders", {"account_number": self.account_number,
                                                 "created_at_gte": start}).get("orders", [])
            except Exception as e:
                self._fail(e)
                return []
        return [{
            "ticker": o.get("symbol"), "side": o.get("side"), "state": o.get("state"), "type": o.get("type"),
            "quantity": _f(o.get("cumulative_quantity")) or _f(o.get("quantity")),
            "price": _f(o.get("average_price")) or _f(o.get("price")), "limit": _f(o.get("price")),
            "placed_by": o.get("placed_agent"), "created_at": o.get("created_at"),
            "updated_at": o.get("last_transaction_at"),
        } for o in raw]

    def fills_since(self, day: str) -> list[dict[str, Any]]:
        """Every execution of a filled equity order since `day` (YYYY-MM-DD), for the TWR rebuild."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        orders: list[dict[str, Any]] = []
        with _lock:
            cursor = None
            for _ in range(20):
                args: dict[str, Any] = {"account_number": self.account_number, "state": "filled",
                                        "created_at_gte": (date.fromisoformat(day) - timedelta(days=7)).isoformat()}
                if cursor:
                    args["cursor"] = cursor
                page = call("get_equity_orders", args)
                orders += page.get("orders", [])
                cursor = page.get("next")
                if not cursor:
                    break
        out = []
        for o in orders:
            execs = o.get("executions") or []
            total_q = sum(_f(e.get("quantity")) or 0 for e in execs) or 1.0
            dba = (o.get("dollar_based_amount") or {}).get("amount")
            recurring = _f(dba) if (o.get("placed_agent") == "recurring" and o.get("side") == "buy") else None
            for e in execs:
                ts = e.get("timestamp") or o.get("last_transaction_at")
                q = _f(e.get("quantity")) or 0.0
                out.append({
                    "ts": ts, "date": datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(et).date().isoformat(),
                    "symbol": o.get("symbol"), "side": o.get("side"), "qty": q, "price": _f(e.get("price")) or 0.0,
                    "recurring_amount": (recurring * q / total_q) if recurring else None,
                })
        return [f for f in out if f["date"] > day]

    def next_earnings(self, symbol: str) -> dict[str, Any] | None:
        today = date.today().isoformat()
        hit = self._earn.get(symbol)
        if hit and hit[0] == today:
            return hit[1]
        with _lock:
            try:
                rows = call("get_earnings_calendar", {"symbols": [symbol], "days": 31}).get("results", [])
            except Exception as e:
                self._fail(e)
                return None
        up = [{"date": r["report"]["date"], "timing": r["report"].get("timing"),
               "verified": r["report"].get("verified"), "source": "robinhood"}
              for r in rows if (r.get("report") or {}).get("date", "") >= today]
        res = min(up, key=lambda x: x["date"]) if up else None
        self._earn[symbol] = (today, res)
        return res
