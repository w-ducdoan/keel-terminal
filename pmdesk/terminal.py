"""MKTS page data: world quotes, the Treasury curve, money markets, credit spreads, economic
releases. The universe lives in config/settings.yaml (markets:).

Each part has its own cache so a slow source never holds up the rest:
quotes 2-30 min by session, FRED curve/spreads 3 h, economic data 6 h. News lives in news.py.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from . import market

log = logging.getLogger("pmdesk.terminal")
ET = ZoneInfo("America/New_York")
FRED_URL = "https://api.stlouisfed.org/fred"


# ---------- helpers ----------

def fred_obs(series: str, start: date) -> pd.Series:
    key = market.fred_key()
    if not key:  # no key: FRED's public CSV
        return pd.Series({pd.Timestamp(d): v for d, v in market.fred_csv(series, start.isoformat())}, dtype=float).sort_index()
    r = requests.get(f"{FRED_URL}/series/observations", params={
        "series_id": series, "api_key": key, "file_type": "json", "observation_start": start.isoformat()}, timeout=20)
    r.raise_for_status()
    obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None)]
    return pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs}, dtype=float).sort_index()


def fred_next_release(series: str) -> dict[str, Any] | None:
    key = market.fred_key()
    if not key:
        return None
    rel = requests.get(f"{FRED_URL}/series/release", params={"series_id": series, "api_key": key, "file_type": "json"},
                       timeout=15).json().get("releases") or []
    if not rel:
        return None
    rid, name = rel[0]["id"], rel[0]["name"]
    dates = requests.get(f"{FRED_URL}/release/dates", params={
        "release_id": rid, "api_key": key, "file_type": "json", "realtime_start": date.today().isoformat(),
        "realtime_end": "9999-12-31", "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 1},
        timeout=15).json().get("release_dates") or []
    return {"release": name, "next": dates[0]["date"] if dates else None}


def transform(s: pd.Series, how: str) -> pd.Series:
    """level | yoy (%) | mom_diff | mom_pct (%). Frequency inferred from the dates."""
    if how == "level" or s.empty:
        return s
    if how == "yoy":
        per_year = 52 if (s.index[-1] - s.index[-2]).days <= 8 else 4 if (s.index[-1] - s.index[-2]).days > 80 else 12
        return (s / s.shift(per_year) - 1).dropna() * 100
    if how == "mom_diff":
        return s.diff().dropna()
    if how == "mom_pct":
        return (s.pct_change() * 100).dropna()
    return s


def _pct_rank(s: pd.Series, v: float) -> float | None:
    return float((s <= v).mean()) if len(s) > 10 else None


# ---------- the data source ----------

class Markets:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.cfg = settings.get("markets") or {}
        self._lock = threading.Lock()
        self._parts: dict[str, tuple[float, Any]] = {}

    def _get(self, name: str, ttl: float, build) -> Any:
        hit = self._parts.get(name)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            val = build()
            self._parts[name] = (time.time(), val)
            return val
        except Exception as e:  # keep the last good copy and say it is old
            log.warning("markets %s failed: %s", name, e)
            if hit:
                return {**hit[1], "stale_error": str(e)} if isinstance(hit[1], dict) else hit[1]
            return {"error": str(e)}

    def snapshot(self, session: str, held: list[str], watch: list[str], fresh: bool = False) -> dict[str, Any]:
        mins = (self.cfg.get("refresh_minutes") or {}).get(session, 5)
        with self._lock:
            if fresh:  # manual refresh: re-pull quotes now (FRED keeps its own clock)
                self._parts.pop("quotes", None)
            with ThreadPoolExecutor(max_workers=4) as ex:
                fq = ex.submit(self._get, "quotes", 60 * mins, self._quotes)
                fr = ex.submit(self._get, "rates", 3 * 3600, self._rates)
                fe = ex.submit(self._get, "economy", 6 * 3600, self._economy)
                out = {"quotes": fq.result(), "rates": fr.result(), "economy": fe.result()}
        out["groups"] = [{"id": g["id"], "label": g["label"], "yield": bool(g.get("yield")),
                          "rows": [r["symbol"] for r in g["rows"]]} for g in self.cfg.get("groups", [])]
        out["labels"] = {r["symbol"]: r["label"] for g in self.cfg.get("groups", []) for r in g["rows"]}
        out["as_of"] = {k: datetime.fromtimestamp(v[0], ET).isoformat() for k, v in self._parts.items()}
        return out

    # ----- quotes -----
    def _quotes(self) -> dict[str, Any]:
        syms = [r["symbol"] for g in self.cfg.get("groups", []) for r in g["rows"]]
        daily = market._close_frame(yf.download(syms, period="1y", interval="1d", auto_adjust=False,
                                                progress=False, threads=True), syms)
        intra = market._close_frame(yf.download(syms, period="5d", interval="15m", auto_adjust=False,
                                                progress=False, threads=True), syms)
        year_start = pd.Timestamp(date.today().year, 1, 1)
        out: dict[str, Any] = {}
        for s in syms:
            d = daily[s].dropna() if s in daily.columns else pd.Series(dtype=float)
            i = intra[s].dropna() if s in intra.columns else pd.Series(dtype=float)
            if d.empty and i.empty:
                out[s] = {"error": "no data"}
                continue
            last, ts = (float(i.iloc[-1]), i.index[-1]) if not i.empty else (float(d.iloc[-1]), d.index[-1])
            last_day = pd.Timestamp(ts).tz_localize(None).normalize() if pd.Timestamp(ts).tzinfo is None else pd.Timestamp(ts).tz_convert(None).normalize()
            dn = d.copy()
            dn.index = pd.to_datetime(dn.index).tz_localize(None) if getattr(dn.index, "tz", None) is None else dn.index.tz_convert(None)
            prior = dn[dn.index.normalize() < last_day]
            prev = float(prior.iloc[-1]) if len(prior) else None
            before_year = dn[dn.index < year_start]
            base_ytd = float(before_year.iloc[-1]) if len(before_year) else None
            # Last two sessions of 15-minute bars for the sparkline (like a 2-day chart)
            spark = []
            if not i.empty:
                days = sorted({pd.Timestamp(x).date() for x in i.index})[-2:]
                sp = i[[pd.Timestamp(x).date() in days for x in i.index]]
                step = max(1, len(sp) // 48)
                spark = [round(float(v), 6) for v in sp.iloc[::step].tolist()]
            hi, lo = (float(dn.max()), float(dn.min())) if len(dn) else (None, None)
            out[s] = {
                "last": last, "prev": prev, "chg": (last - prev) if prev is not None else None,
                "chg_pct": (last / prev - 1) if prev else None, "ytd": (last / base_ytd - 1) if base_ytd else None,
                "hi52": max(hi, last) if hi is not None else None, "lo52": min(lo, last) if lo is not None else None,
                "time": pd.Timestamp(ts).isoformat(), "spark": spark,
            }
        return {"rows": out}

    # ----- rates, money markets, spreads (FRED) -----
    def _rates(self) -> dict[str, Any]:
        start = date.today() - timedelta(days=5 * 366)
        curve, money, spreads = self.cfg.get("curve", []), self.cfg.get("money", []), self.cfg.get("spreads", [])
        ids = [c["series"] for c in curve + money + spreads]
        with ThreadPoolExecutor(max_workers=8) as ex:
            data = dict(zip(ids, ex.map(lambda sid: fred_obs(sid, start), ids)))

        def at(s: pd.Series, days_ago: int) -> float | None:
            if s.empty:
                return None
            cut = s[s.index <= s.index[-1] - pd.Timedelta(days=days_ago)]
            return float(cut.iloc[-1]) if len(cut) else None

        def row(spec: dict[str, Any], scale: float = 1.0) -> dict[str, Any]:
            s = data.get(spec["series"], pd.Series(dtype=float)) * scale
            if s.empty:
                return {**spec, "error": "no data"}
            last, y1 = float(s.iloc[-1]), s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
            return {**spec, "last": last, "date": s.index[-1].date().isoformat(),
                    "d1": last - float(s.iloc[-2]) if len(s) > 1 else None,
                    "w1": (last - at(s, 7)) if at(s, 7) is not None else None,
                    "m1": (last - at(s, 30)) if at(s, 30) is not None else None,
                    "lo1y": float(y1.min()), "hi1y": float(y1.max()), "rank1y": _pct_rank(y1, last),
                    "rank_all": _pct_rank(s, last), "history_from": s.index[0].date().isoformat()}

        curve_rows = []
        for c in curve:
            s = data.get(c["series"], pd.Series(dtype=float))
            curve_rows.append({**c, "now": float(s.iloc[-1]) if len(s) else None, "date": s.index[-1].date().isoformat() if len(s) else None,
                               "w1": at(s, 7), "m1": at(s, 30), "y1": at(s, 365)})
        return {"curve": curve_rows, "money": [row(m) for m in money], "spreads": [row(s, 100.0) for s in spreads]}

    # ----- economic releases (FRED) -----
    def _economy(self) -> dict[str, Any]:
        start = date.today() - timedelta(days=4 * 366)
        specs = self.cfg.get("economy", [])

        def one(spec: dict[str, Any]) -> dict[str, Any]:
            raw = fred_obs(spec["series"], start)
            s = transform(raw, spec.get("transform", "level")) * float(spec.get("scale", 1))
            if s.empty:
                return {**spec, "error": "no data"}
            try:
                rel = fred_next_release(spec["series"])
            except Exception:
                rel = None
            return {**spec, "last": float(s.iloc[-1]), "period": s.index[-1].date().isoformat(),
                    "prior": float(s.iloc[-2]) if len(s) > 1 else None,
                    "yr_ago": float(s[s.index <= s.index[-1] - pd.Timedelta(days=360)].iloc[-1]) if len(s[s.index <= s.index[-1] - pd.Timedelta(days=360)]) else None,
                    "spark": [round(float(v), 4) for v in s.iloc[-24:].tolist()],
                    "release": (rel or {}).get("release"), "next": (rel or {}).get("next")}

        with ThreadPoolExecutor(max_workers=6) as ex:
            rows = list(ex.map(one, specs))
        return {"rows": rows}
