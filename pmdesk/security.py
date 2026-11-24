"""Security page data (DES): one ticker's fundamentals, analyst ratings, earnings history,
financials, SEC filings and, when held, tax lots and realized trades. All from Robinhood's
read-only tools, plus where the name sits in the research folder coverage. Cached 30 minutes.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from . import rh_mcp

TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
BUCKETS = ["_holdings", "_watchlist", "_passed", "_closed-positions"]
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()
TTL = 30 * 60


def coverage(hq: Path, ticker: str) -> dict[str, Any] | None:
    """Which the research folder coverage bucket holds this ticker, and its folder's notes."""
    base = hq / "wiki" / "coverage"
    aliases = {"GOOG": "GOOGL", "GOOGL": "GOOG", "BRK.B": "BRK-B", "BRK-B": "BRK.B"}  # share classes filed under one folder
    for name in (ticker, aliases.get(ticker)):
        hit = _coverage_one(base, name) if name else None
        if hit:
            return hit
    return None


def _coverage_one(base: Path, ticker: str) -> dict[str, Any] | None:
    for b in BUCKETS:
        d = base / b
        if not d.exists():
            continue
        for sub in d.iterdir():
            if sub.is_dir() and (sub.name == ticker or sub.name.upper().startswith(ticker + "-")):
                files = sorted((p for p in sub.rglob("*.md")), key=lambda p: p.stat().st_mtime, reverse=True)
                return {"bucket": b.strip("_"), "folder": str(sub),
                        "files": [{"name": p.name, "path": str(p), "rel": str(p.relative_to(sub))} for p in files[:12]]}
    return None


def _calls(t: str, account: str | None) -> list[tuple[str, str, dict[str, Any]]]:
    out = [
        ("fundamentals", "get_equity_fundamentals", {"symbols": [t]}),
        ("ratings", "get_equity_analyst_ratings", {"symbols": [t]}),
        ("earnings", "get_earnings_results", {"symbol": t}),
        ("fin_q", "get_financials", {"symbols": [t], "period": "quarterly", "limit": 8}),
        ("fin_a", "get_financials", {"symbols": [t], "period": "annual", "limit": 5}),
        ("filings", "get_sec_filing_index", {"symbol": t, "form_type": ["10-K", "10-Q", "8-K", "DEF 14A", "S-1", "SC 13D"]}),
    ]
    if account:
        out += [("lots", "get_equity_tax_lots", {"account_number": account, "symbol": t}),
                ("trades", "get_pnl_trade_history", {"account_number": account, "symbol": t, "span": "all"})]
    return out


def _first(res: Any) -> Any:
    rows = (res or {}).get("results") or []
    return rows[0] if rows else None


def load(ticker: str, broker: Any, hq: Path, held: bool) -> dict[str, Any]:
    t = ticker.upper().strip()
    if not TICKER.match(t):
        return {"ok": False, "error": "Not a ticker"}
    key = f"{t}:{held}"
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    account = getattr(broker, "account_number", None)
    if not account or not isinstance(broker, rh_mcp.McpBroker):
        return {"ok": False, "error": "The security page needs the Robinhood MCP feed (python -m pmdesk.rh_login).",
                "coverage": coverage(hq, t)}
    calls = _calls(t, account if held else None)
    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:  # one session for everything
        for (name, _, _), r in zip(calls, rh_mcp.call_many([(tool, args) for _, tool, args in calls])):
            raw[name] = r
    except Exception:  # one tool failed: retry each so the rest still shows
        for name, tool, args in calls:
            try:
                raw[name] = rh_mcp.call(tool, args)
            except Exception as e:
                errors[name] = str(e)[:200]

    f = _first(raw.get("fundamentals"))
    ratings = (_first(raw.get("ratings")) or {}).get("ratings")
    fq, fa = _first(raw.get("fin_q")), _first(raw.get("fin_a"))
    out = {
        "ok": True, "ticker": t, "as_of": time.time(),
        "fundamentals": f, "ratings": ratings,
        "earnings": sorted((raw.get("earnings") or {}).get("results") or [], key=lambda r: (r.get("report") or {}).get("date") or ""),
        "financials": {"quarterly": (fq or {}).get("financials") or [], "annual": (fa or {}).get("financials") or []},
        "filings": ((raw.get("filings") or {}).get("filings") or [])[:30],
        "lots": (raw.get("lots") or {}).get("tax_lots") or [],
        "trades": (raw.get("trades") or {}).get("trades") or [],
        "coverage": coverage(hq, t), "errors": errors,
    }
    if f is None and not out["earnings"] and not out["filings"]:
        out = {"ok": False, "ticker": t, "error": f"Robinhood has no data for {t}.", "coverage": out["coverage"], "errors": errors}
    with _lock:
        _cache[key] = (time.time(), out)
    return out
