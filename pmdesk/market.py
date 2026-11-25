"""Free market data: Yahoo (via yfinance) for quotes and history, FRED for credit and rates."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests
import yfinance as yf

log = logging.getLogger("pmdesk.market")


def _close_frame(df: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Return a Close-price frame with one column per symbol, whatever shape yfinance returns."""
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        if "Close" in lvl0:
            out = df["Close"]
        else:  # group_by="ticker"
            out = df.xs("Close", axis=1, level=1)
    else:
        out = df[["Close"]].rename(columns={"Close": symbols[0]})
    if isinstance(out, pd.Series):
        out = out.to_frame(symbols[0])
    return out


def quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Latest price and previous close per symbol. Uses 1-minute bars (incl. pre/post) when available."""
    symbols = sorted({s for s in symbols if s})
    if not symbols:
        return {}
    out: dict[str, dict[str, Any]] = {}
    to_y = {s: yahoo_symbol(s) for s in symbols}
    back = {v: k for k, v in to_y.items()}
    ysyms = sorted(set(to_y.values()))
    try:
        daily = _close_frame(yf.download(ysyms, period="10d", interval="1d", auto_adjust=False,
                                         progress=False, threads=True), ysyms).rename(columns=back)
    except Exception as e:  # network or parsing failure
        log.warning("daily quote download failed: %s", e)
        daily = pd.DataFrame()
    try:
        intra = _close_frame(yf.download(ysyms, period="1d", interval="1m", prepost=True,
                                         auto_adjust=False, progress=False, threads=True), ysyms).rename(columns=back)
    except Exception as e:
        log.warning("intraday quote download failed: %s", e)
        intra = pd.DataFrame()

    for s in symbols:
        last = None
        ts = None
        if not intra.empty and s in intra.columns:
            series = intra[s].dropna()
            if not series.empty:
                last = float(series.iloc[-1])
                ts = series.index[-1]
        dser = daily[s].dropna() if (not daily.empty and s in daily.columns) else pd.Series(dtype=float)
        if last is None and not dser.empty:
            last = float(dser.iloc[-1])
            ts = dser.index[-1]
        if last is None:
            continue
        last_day = pd.Timestamp(ts).date() if ts is not None else None
        prev = None
        if not dser.empty and last_day is not None:
            earlier = dser[[d.date() < last_day for d in dser.index]]
            if not earlier.empty:
                prev = float(earlier.iloc[-1])
        change = (last - prev) if prev else None
        out[s] = {
            "last": last,
            "prev_close": prev,
            "change": change,
            "change_pct": (change / prev) if (prev and change is not None) else None,
            "time": pd.Timestamp(ts).isoformat() if ts is not None else None,
            "source": "yahoo",
        }
    return out


def yahoo_symbol(sym: str) -> str:
    """Robinhood writes share classes with a dot (BRK.B); Yahoo uses a dash (BRK-B).
    Exchange suffixes (DX-Y.NYB, 000001.SS, FTSEMIB.MI) are left alone."""
    import re
    return sym.replace(".", "-") if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", sym) else sym


def history(symbols: list[str], period: str = "2y") -> pd.DataFrame:
    """Adjusted daily closes, one column per symbol (columns keep the caller's spelling)."""
    symbols = sorted({s for s in symbols if s})
    if not symbols:
        return pd.DataFrame()
    to_y = {s: yahoo_symbol(s) for s in symbols}
    ysyms = sorted(set(to_y.values()))
    df = yf.download(ysyms, period=period, interval="1d", auto_adjust=True,
                     progress=False, threads=True)
    closes = _close_frame(df, ysyms).rename(columns={v: k for k, v in to_y.items()})
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    return closes.sort_index()


def next_earnings_yahoo(symbol: str) -> dict[str, Any] | None:
    try:
        cal = yf.Ticker(symbol).calendar
    except Exception:
        return None
    if not cal:
        return None
    dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if not dates:
        return None
    d = dates[0]
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date) or d < date.today():
        return None  # Yahoo sometimes returns the last report, not the next one
    return {"date": d.isoformat(), "timing": None, "source": "yahoo"}


def fred_key() -> str | None:
    """FRED_API_KEY env var, else .secrets/fred_api_key."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        from .config import SECRETS_DIR
        f = SECRETS_DIR / "fred_api_key"
        key = f.read_text(encoding="utf-8").strip() if f.exists() else None
    return key or None


def fred_csv(series: str, start: str | None = None) -> list[tuple[str, float]]:
    """FRED's public CSV download (no API key): [(YYYY-MM-DD, value)] oldest first. Used when no key is set,
    so the curve, spreads and macro series still fill on a copy that has no FRED key (the public edition)."""
    params = {"id": series}
    if start:
        params["cosd"] = start
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params=params, timeout=25,
                     headers={"User-Agent": "Mozilla/5.0 (Keel market terminal)"})
    r.raise_for_status()
    out = []
    for line in r.text.splitlines()[1:]:
        d, _, v = line.partition(",")
        try:
            out.append((d.strip(), float(v)))
        except ValueError:  # "." = no value that day
            continue
    return out


def fred_latest(series: str) -> dict[str, Any] | None:
    key = fred_key()
    if not key:
        try:
            obs = fred_csv(series, (date.today() - timedelta(days=400)).isoformat())
        except Exception as e:
            log.warning("FRED CSV %s failed: %s", series, e)
            return None
        if not obs:
            return None
        return {"last": obs[-1][1], "prev": obs[-2][1] if len(obs) > 1 else None, "date": obs[-1][0]}
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series, "api_key": key, "file_type": "json",
                    "sort_order": "desc", "limit": 10},
            timeout=15,
        )
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None)]
    except Exception as e:
        log.warning("FRED %s failed: %s", series, e)
        return None
    if not obs:
        return None
    last = float(obs[0]["value"])
    prev = float(obs[1]["value"]) if len(obs) > 1 else None
    return {"last": last, "prev": prev, "date": obs[0]["date"]}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
