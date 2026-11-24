"""THEMES: one row per exposure bucket, with the outside data that drives it.

Drivers come from config/settings.yaml (themes:), one list per bucket name. Each driver is one of:
  {fred: SERIES, label, calc: yoy | chg3m, good: up | down}       FRED (needs the FRED key)
  {alt: series_id, label, good: up | down}                         alt-data feed in the research folder (local CSV)
  {bottleneck: "name contains", label}                             alt-data bottleneck tightness and direction
  {etf: SYMBOL, label, good: up}                                   3-month return minus SPY (Yahoo)
A driver reads +1 (tailwind), -1 (headwind) or 0. good: omitted means show the number with no read.
Rule-based: no AI, nothing written anywhere.
"""
from __future__ import annotations

import csv
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from . import market

ALT_DIR = Path("data/singularity-research")  # relative to the the research folder folder
STALE_DAYS = 10                               # the feed README's staleness rule
_cache: dict[str, tuple[float, Any]] = {}


def _memo(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


# ---------- pure helpers ----------
def sign(change: float | None, good: str | None, dead: float = 0.0) -> int:
    if change is None or good not in ("up", "down") or abs(change) <= dead:
        return 0
    return (1 if change > 0 else -1) * (1 if good == "up" else -1)


def value_ago(obs: list[tuple[str, float]], days: int) -> float | None:
    """obs newest first as (date, value); the latest value at or before `days` before the newest date."""
    if not obs:
        return None
    cut = (date.fromisoformat(obs[0][0]) - timedelta(days=days)).isoformat()
    return next((v for d, v in obs if d <= cut), None)


def read(score: int, n: int) -> str:
    """n = drivers that vote. A read needs the net vote to be at least half of them (2 up 1 down is mixed)."""
    if not n:
        return "no drivers set"
    return "tailwind" if score / n >= 0.5 else "headwind" if score / n <= -0.5 else "mixed"


# ---------- sources ----------
def _fred_obs(series: str) -> list[tuple[str, float]]:
    key = market.fred_key()
    if not key:
        return []
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": series, "api_key": key, "file_type": "json", "sort_order": "desc",
                             "observation_start": (date.today() - timedelta(days=500)).isoformat()}, timeout=20)
    r.raise_for_status()
    return [(o["date"], float(o["value"])) for o in r.json().get("observations", []) if o.get("value") not in (".", None)]


def fred_driver(d: dict[str, Any]) -> dict[str, Any]:
    obs = _memo("fred:" + d["fred"], 6 * 3600, lambda: _fred_obs(d["fred"]))
    if not obs:
        return {"value": None, "note": "FRED unavailable (key missing or request failed)"}
    last, asof = obs[0][1], obs[0][0]
    if d.get("calc") == "chg3m":
        prev = value_ago(obs, 91)
        ch = None if prev is None else last - prev
        return {"value": last, "change": ch, "change_txt": None if ch is None else f"{ch:+.2f} over 3 months",
                "signal": sign(ch, d.get("good")), "asof": asof}
    prev = value_ago(obs, 360)
    ch = None if not prev else last / prev - 1
    return {"value": last, "change": ch, "change_txt": None if ch is None else f"{ch * 100:+.1f}% y/y",
            "signal": sign(ch, d.get("good"), 0.005), "asof": asof}


def _alt_rows(hq: Path, name: str) -> list[dict[str, str]]:
    f = hq / ALT_DIR / name
    if not f.exists():
        return []
    with f.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def alt_driver(hq: Path, d: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in _memo("alt:series", 1800, lambda: _alt_rows(hq, "series-history.csv")) if r.get("series_id") == d["alt"]]
    if not rows:
        return {"value": None, "note": "not in the alt-data feed"}
    rows.sort(key=lambda r: (r["period"], r["captured_date"]))
    last = rows[-1]
    prior = next((r for r in reversed(rows) if r["period"] < last["period"]), None)
    v = float(last["value"])
    ch = v / float(prior["value"]) - 1 if prior and float(prior["value"]) else None
    stale = (date.today() - date.fromisoformat(last["captured_date"])).days > STALE_DAYS
    return {"value": v, "unit": last.get("unit"), "change": ch,
            "change_txt": (f"{ch * 100:+.1f}% vs prior print" if ch is not None else "") + (f" · {last['note']}" if last.get("note") else ""),
            "signal": sign(ch, d.get("good"), 0.005), "asof": last["period"], "stale": stale}


def bottleneck_driver(hq: Path, d: dict[str, Any]) -> dict[str, Any]:
    rows = _memo("alt:bn", 1800, lambda: _alt_rows(hq, "bottlenecks-history.csv"))
    hits = [r for r in rows if d["bottleneck"].lower() in (r.get("bottleneck") or "").lower()]
    if not hits:
        return {"value": None, "note": "bottleneck not found"}
    last_day = max(r["captured_date"] for r in hits)
    cur = next(r for r in hits if r["captured_date"] == last_day)
    prev = [r for r in hits if r["captured_date"] < last_day]
    t = float(cur["tightness"]) if (cur.get("tightness") or "").strip() else None
    tp = float(prev[-1]["tightness"]) if prev and (prev[-1].get("tightness") or "").strip() else None
    dirn = cur.get("direction") or ""
    # A tightening bottleneck supports the names that sell into it (pricing power, backlog).
    sig = 1 if dirn == "tightening" else -1 if dirn == "easing" else 0
    return {"value": t, "unit": "tightness /100", "change": None if t is None or tp is None else t - tp,
            "change_txt": dirn + (f" · {t - tp:+.0f} since {prev[-1]['captured_date']}" if t is not None and tp is not None else "")
            + (f" · lead time {cur['lead_time']}" if cur.get("lead_time") else ""),
            "signal": sig, "asof": last_day, "stale": (date.today() - date.fromisoformat(last_day)).days > STALE_DAYS}


def etf_driver(d: dict[str, Any]) -> dict[str, Any]:
    def run():
        h = market.history([d["etf"], "SPY"], "6mo")
        if d["etf"] not in h or "SPY" not in h:
            return None
        h = h.dropna()
        if len(h) < 64:
            return None
        r = h.iloc[-1] / h.iloc[-64] - 1
        return {"etf": float(r[d["etf"]]), "spy": float(r["SPY"]), "asof": h.index[-1].strftime("%Y-%m-%d")}
    x = _memo("etf:" + d["etf"], 3 * 3600, run)
    if not x:
        return {"value": None, "note": "price history unavailable"}
    rel = x["etf"] - x["spy"]
    return {"value": x["etf"], "unit": "3M return", "change": rel, "change_txt": f"{rel * 100:+.1f} pts vs SPY over 3 months",
            "signal": sign(rel, d.get("good", "up"), 0.01), "asof": x["asof"]}


def driver(hq: Path, d: dict[str, Any]) -> dict[str, Any]:
    try:
        if "fred" in d:
            out, src = fred_driver(d), f"FRED {d['fred']}"
        elif "alt" in d:
            out, src = alt_driver(hq, d), "alt-data feed"
        elif "bottleneck" in d:
            out, src = bottleneck_driver(hq, d), "alt-data bottlenecks"
        elif "etf" in d:
            out, src = etf_driver(d), f"{d['etf']} vs SPY"
        else:
            return {"label": d.get("label", "?"), "value": None, "note": "unknown driver type"}
    except Exception as e:  # one driver fails, the panel still draws
        out, src = {"value": None, "note": str(e)[:120]}, "error"
    if out.get("stale"):
        out["signal"] = 0  # stale feed: show it, never let it vote
    out["votes"] = bool(("good" in d or "bottleneck" in d or "etf" in d) and out.get("value") is not None and not out.get("stale"))
    return {"label": d.get("label") or src, "source": src, **out}


def build(cfg_themes: dict[str, Any], snap: dict[str, Any], hq: Path) -> dict[str, Any]:
    buckets = (snap.get("aggregates") or {}).get("bucket") or {}
    names: dict[str, list[str]] = {}
    for p in snap.get("positions", []):
        names.setdefault(p.get("bucket") or "Unassigned", []).append(p["ticker"])
    rows = []
    for b, w in sorted(buckets.items(), key=lambda x: -x[1]):
        spec = cfg_themes.get(b) or {}
        ds = [driver(hq, d) for d in (spec.get("drivers") or [])]
        score = sum(x.get("signal") or 0 for x in ds)
        voting = sum(1 for x in ds if x.get("votes"))
        rows.append({"bucket": b, "weight": w, "names": names.get(b, []), "why": spec.get("why"), "drivers": ds,
                     "score": score, "read": read(score, voting)})
    return {"ok": True, "rows": rows, "fred": bool(market.fred_key()), "as_of": date.today().isoformat()}
