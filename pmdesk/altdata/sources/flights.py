"""Company aircraft fleets: the FAA aircraft registry (free download) lists every US-registered plane and its owner;
OpenSky Network (free account: .secrets/opensky.json with client_id and client_secret) logs each plane's flights.
A company that owns three or more aircraft gets a flights-per-month series. TMDX flies donor organs on its own jets,
so its flights track transplant volume. Company aircraft only; no individuals' planes are looked up."""
from __future__ import annotations

import io
import json
import logging
import pickle
import re
import time
import zipfile
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import requests

from ...config import DATA_DIR, SECRETS_DIR
from . import UA, get

log = logging.getLogger("pmdesk.alt.flights")
REGISTRY = DATA_DIR / "alt_faa_registry.pkl"
FLIGHTS = DATA_DIR / "alt_flights.json"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API = "https://opensky-network.org/api/flights/aircraft"


def creds() -> dict[str, str] | None:
    f = SECRETS_DIR / "opensky.json"
    try:
        c = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return c if c and c.get("client_id") and c.get("client_secret") else None
    except Exception:
        return None


def registry() -> pd.DataFrame:
    """Owner name, N-number and transponder code for every US aircraft (re-downloaded weekly)."""
    if REGISTRY.exists() and time.time() - REGISTRY.stat().st_mtime < 7 * 86400:
        with REGISTRY.open("rb") as f:
            return pickle.load(f)
    z = zipfile.ZipFile(io.BytesIO(get("https://registry.faa.gov/database/ReleasableAircraft.zip", timeout=300).content))
    m = pd.read_csv(z.open("MASTER.txt"), dtype=str, encoding="latin-1", usecols=lambda c: c.strip().lstrip("﻿ï»¿") in
                    ("N-NUMBER", "NAME", "MODE S CODE HEX", "TYPE AIRCRAFT"))
    m.columns = [c.strip().lstrip("﻿ï»¿") for c in m.columns]
    m = m.apply(lambda c: c.str.strip())
    DATA_DIR.mkdir(exist_ok=True)
    with REGISTRY.open("wb") as f:
        pickle.dump(m, f)
    return m


def fleet(name: str) -> list[dict[str, str]]:
    if len(name) < 4:
        return []
    m = registry()
    hit = m[m["NAME"].str.contains(r"\b" + re.escape(name.upper()) + r"\b", regex=True, na=False)]
    return [{"n": "N" + r["N-NUMBER"], "hex": (r["MODE S CODE HEX"] or "").lower()} for _, r in hit.iterrows() if r["MODE S CODE HEX"]]


def candidates(p: dict[str, Any]) -> list[dict[str, Any]]:
    short = (p.get("short") or "").upper()
    if p.get("quote_type") not in (None, "EQUITY") or "AIRLINE" in (p.get("industry") or "").upper():
        return []
    try:
        fl = fleet(short)
    except Exception as e:
        log.warning("FAA registry: %s", e)
        return []
    if len(fl) < 3:
        return []
    return [{"id": f"flights:{short}", "source": "FAA registry + OpenSky", "title": f"Flights by {short.title()}'s own aircraft ({len(fl)} registered)",
             "unit": "flights", "freq": "M", "agg": "sum", "growth": "pct", "kind": "company", "fleet": fl,
             "needs": None if creds() else "OpenSky account",
             "reason": "A company flying its own fleet for its product (organ transport, logistics) flies more when business grows."}]


def _token(c: dict[str, str]) -> str:
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": c["client_id"], "client_secret": c["client_secret"]},
                      headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch(spec: dict[str, Any]) -> pd.Series:
    """Flights per month across the fleet. Each aircraft-month is pulled once and kept, so the first build can
    resume across days within OpenSky's free daily limit."""
    c = creds()
    if not c:
        raise RuntimeError("needs an OpenSky account (.secrets/opensky.json)")
    done: dict[str, int] = json.loads(FLIGHTS.read_text(encoding="utf-8")) if FLIGHTS.exists() else {}
    tok, tok_at = _token(c), time.time()
    this = date.today().replace(day=1)
    months = [(this - pd.DateOffset(months=k)).date() for k in range(1, 61)]
    for ac in spec["fleet"]:
        for m0 in months:
            key = f"{ac['hex']}|{m0.isoformat()[:7]}"
            if key in done:
                continue
            m1 = (pd.Timestamp(m0) + pd.offsets.MonthBegin(1)).date()
            if time.time() - tok_at > 1500:
                tok, tok_at = _token(c), time.time()
            b = int(datetime(m0.year, m0.month, 1, tzinfo=timezone.utc).timestamp())
            e = int(datetime(m1.year, m1.month, 1, tzinfo=timezone.utc).timestamp()) - 1
            r = requests.get(API, params={"icao24": ac["hex"], "begin": b, "end": e}, headers={**UA, "Authorization": f"Bearer {tok}"}, timeout=40)
            if r.status_code == 429:  # daily limit: keep what we have, finish tomorrow
                FLIGHTS.write_text(json.dumps(done), encoding="utf-8")
                break
            done[key] = len(r.json()) if r.status_code == 200 else 0 if r.status_code == 404 else done.get(key, 0)
            time.sleep(0.3)
        FLIGHTS.write_text(json.dumps(done), encoding="utf-8")
    hexes = {a["hex"] for a in spec["fleet"]}
    tot: dict[pd.Timestamp, float] = {}
    for k, v in done.items():
        h, ym = k.split("|")
        if h in hexes:
            ts = pd.Period(ym, "M").end_time.normalize()
            tot[ts] = tot.get(ts, 0) + v
    return pd.Series(tot, dtype=float).sort_index()

