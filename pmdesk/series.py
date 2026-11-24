"""Chart series for any range: a ticker, a FRED series, or the book (today's holdings replayed)."""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from . import market

# range -> (yfinance period, interval, cache seconds)
RANGES: dict[str, tuple[str, str, int]] = {
    "1D": ("1d", "5m", 60),
    "5D": ("5d", "15m", 120),
    "1M": ("1mo", "1h", 600),
    "3M": ("3mo", "1d", 3600),
    "6M": ("6mo", "1d", 3600),
    "YTD": ("ytd", "1d", 3600),
    "1Y": ("1y", "1d", 3600),
    "5Y": ("5y", "1wk", 6 * 3600),
    "MAX": ("max", "1mo", 6 * 3600),
}
FRED_DAYS = {"1D": 7, "5D": 10, "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "5Y": 1827, "MAX": 365 * 40}

_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _cached(key: tuple[str, str], ttl: int, build) -> dict[str, Any]:
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    out = build()
    if out.get("points"):
        with _lock:
            _cache[key] = (time.time(), out)
    return out


def _summary(symbol: str, rng: str, interval: str, s: pd.Series, note: str | None = None,
             base: float | None = None) -> dict[str, Any]:
    """base = the value to measure change from (prior close for 1D); defaults to the first point."""
    s = s.dropna()
    if s.empty:
        return {"symbol": symbol, "range": rng, "interval": interval, "points": [], "note": note or "No data for this range."}
    first, last = (float(base) if base else float(s.iloc[0])), float(s.iloc[-1])
    return {
        "symbol": symbol, "range": rng, "interval": interval,
        "points": [[pd.Timestamp(i).isoformat(), round(float(v), 6)] for i, v in s.items()],
        "first": first, "last": last, "change": last - first,
        "change_pct": (last / first - 1) if first else None,
        "high": float(s.max()), "low": float(s.min()), "note": note, "base": first,
    }


yahoo_symbol = market.yahoo_symbol


def _closes(symbols: list[str], rng: str) -> tuple[pd.DataFrame, dict[str, float]]:
    """Closes for the range (columns keep the caller's symbols), plus prior-session closes for 1D."""
    to_y = {s: yahoo_symbol(s) for s in symbols}
    back = {v: k for k, v in to_y.items()}
    out, prev = _closes_yahoo(sorted(set(to_y.values())), rng)
    out = out.rename(columns=back).dropna(axis=1, how="all")
    return out, {back.get(k, k): v for k, v in prev.items()}


def _closes_yahoo(symbols: list[str], rng: str) -> tuple[pd.DataFrame, dict[str, float]]:
    period, interval, _ = RANGES[rng]
    df = yf.download(symbols, period=period, interval=interval, auto_adjust=True,
                     prepost=False, progress=False, threads=True)
    out = market._close_frame(df, symbols)
    prev: dict[str, float] = {}
    if rng == "1D" and not out.empty:  # keep only the latest session; measure from the prior close
        idx = pd.to_datetime(out.index)
        last_day = idx[-1].date()
        out = out[[d.date() == last_day for d in idx]]
        daily = market._close_frame(yf.download(symbols, period="10d", interval="1d", auto_adjust=True,
                                                progress=False, threads=True), symbols)
        if not daily.empty:
            earlier = daily[[d.date() < last_day for d in pd.to_datetime(daily.index)]]
            prev = {s: float(earlier[s].dropna().iloc[-1]) for s in earlier.columns if not earlier[s].dropna().empty}
    return out, prev


def ticker_series(symbol: str, rng: str) -> dict[str, Any]:
    _, interval, ttl = RANGES[rng]
    def build() -> dict[str, Any]:
        df, prev = _closes([symbol], rng)
        if df.empty or symbol not in df.columns:
            return _summary(symbol, rng, interval, pd.Series(dtype=float), "Yahoo returned no data.")
        return _summary(symbol, rng, interval, df[symbol], base=prev.get(symbol))
    return _cached((symbol, rng), ttl, build)


def fred_series(label: str, series_id: str, scale: float, rng: str, transform_name: str = "level") -> dict[str, Any]:
    """FRED chart. transform_name: level | yoy | mom_diff | mom_pct (monthly data shows releases, not days)."""
    from .terminal import transform

    def build() -> dict[str, Any]:
        key = market.fred_key()
        if not key:
            return _summary(label, rng, "1d", pd.Series(dtype=float), "No FRED key set.")
        start = date(date.today().year, 1, 1) if rng == "YTD" else date.today() - timedelta(days=FRED_DAYS[rng])
        fetch_from = start - timedelta(days=400) if transform_name != "level" else start
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": series_id, "api_key": key, "file_type": "json",
                                 "observation_start": fetch_from.isoformat()}, timeout=20)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None)]
        s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs}, dtype=float).sort_index()
        s = transform(s, transform_name) * scale
        s = s[s.index >= pd.Timestamp(start)] if rng not in ("1D", "5D") else s
        gap = (s.index[1] - s.index[0]).days if len(s) > 1 else 1
        freq = "once a day" if gap <= 3 else "weekly" if gap <= 8 else "monthly" if gap <= 31 else "quarterly"
        note = f"FRED, published {freq}."
        if rng == "1D":
            s, note = s.iloc[-2:], f"FRED publishes {freq}: 1D is the last two prints."
        elif rng == "5D":
            s, note = s.iloc[-6:], f"FRED publishes {freq}: 5D is the last six prints."
        elif not s.empty and (s.index[0].date() - start).days > 60:
            note = f"FRED only has this series from {s.index[0].date()}."
        if rng in ("5Y", "MAX") and len(s) > 600:
            s = s.resample("W").last()
        return _summary(label, rng, "1d", s, note)
    return _cached(("FRED:" + series_id + ":" + transform_name + ":" + str(scale), rng), 3600, build)


def book_series(positions: list[dict[str, Any]], cash: float, rng: str) -> dict[str, Any]:
    """Today's share counts priced through history, plus today's cash. Not actual NAV history."""
    shares = {p["ticker"]: float(p["shares"]) for p in positions if p.get("shares")}
    _, interval, ttl = RANGES[rng]
    sig = "BOOK:" + ",".join(f"{k}={v}" for k, v in sorted(shares.items()))
    def build() -> dict[str, Any]:
        syms = sorted(shares)
        df, prev = _closes(syms, rng)
        if df.empty:
            return _summary("BOOK", rng, interval, pd.Series(dtype=float), "No price data.")
        df = df.ffill()
        missing = [s for s in syms if s not in df.columns]
        complete = df[[s for s in syms if s in df.columns]].dropna()
        note = "Today's holdings and cash replayed over the range, not your actual NAV history."
        if len(complete) < len(df):
            note += f" Starts {pd.Timestamp(complete.index[0]).date()} because a holding has no earlier prices." if len(complete) else ""
        if missing:
            note += " Missing prices: " + ", ".join(missing) + "."
        value = sum(complete[s] * shares[s] for s in complete.columns) + cash
        base = None
        if prev and all(s in prev for s in complete.columns):
            base = sum(prev[s] * shares[s] for s in complete.columns) + cash
        return _summary("BOOK", rng, interval, value, note, base=base)
    return _cached((sig, rng), ttl, build)


TWR_DAYS = {"5D": 7, "1M": 31, "3M": 92, "6M": 183, "1Y": 365, "5Y": 1827}


def book_twr_series(perf: dict[str, Any], nav_now: float | None, rng: str) -> dict[str, Any]:
    """The actual book for any range: today's NAV carried back through the time-weighted return,
    so deposits and withdrawals do not show as gains or losses. Ends at today's NAV."""
    pts = (perf or {}).get("points") or []
    if not (perf or {}).get("ok") or len(pts) < 2 or not nav_now:
        return _summary("BOOK", rng, "1d", pd.Series(dtype=float), (perf or {}).get("error") or "Returns are still loading.")
    today = date.fromisoformat(perf["today"])
    start = date(today.year, 1, 1) if rng == "YTD" else date(1900, 1, 1) if rng == "MAX" else today - timedelta(days=TWR_DAYS[rng])
    before = [p for p in pts if p["date"] < start.isoformat()]
    inside = [p for p in pts if p["date"] >= start.isoformat()]
    sel = (before[-1:] if before else []) + inside
    last = 1 + pts[-1]["twr"]
    s = pd.Series({pd.Timestamp(p["date"]): nav_now * (1 + p["twr"]) / last for p in sel}, dtype=float)
    monthly = any(p.get("source") == "log" for p in sel[1:])
    note = ("Your actual book, flow-adjusted: today's NAV carried back by the time-weighted return, so deposits "
            "and withdrawals are not counted as gains." + (" Monthly marks from performance-log.md before "
            f"{perf.get('anchor_date')}, daily after." if monthly else " Daily closes, live today."))
    return _summary("BOOK", rng, "1d", s, note)
