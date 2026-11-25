"""EIA-860M (free, no key): the government's monthly list of power plants planned to come online. Each monthly
file is a snapshot; Keel keeps, per month, the megawatts planned to start within the next 12 months by
technology (solar, batteries, gas, wind) and for ERCOT (the Texas grid). Solar pipeline -> NXT's tracker demand;
gas turbines -> GEV; ERCOT build-out -> VST. Files are read once; only the monthly totals are kept."""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Any

import pandas as pd

from ...config import DATA_DIR
from . import get, has

log = logging.getLogger("pmdesk.alt.eia860m")
PAGE = "https://www.eia.gov/electricity/data/eia860m/"
CACHE = DATA_DIR / "alt_eia860m.json"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"], 1)}
GROUPS = {"solar": ["Solar Photovoltaic", "Solar Thermal with Energy Storage", "Solar Thermal without Energy Storage"],
          "battery": ["Batteries"], "gas": ["Natural Gas Fired Combined Cycle", "Natural Gas Fired Combustion Turbine",
                                            "Natural Gas Internal Combustion Engine", "Natural Gas Steam Turbine"],
          "wind": ["Onshore Wind Turbine", "Offshore Wind Turbine"]}
SPECS = {
    "solar_12m": ("Utility-scale solar planned to come online in the next 12 months (US)", "Solar farms need trackers and inverters (NXT) before they start up."),
    "gas_12m": ("Gas-fired power plants planned in the next 12 months (US)", "New gas plants need turbines and grid gear (GEV, electrical equipment)."),
    "battery_12m": ("Battery storage planned in the next 12 months (US)", "Storage projects buy inverters, switchgear and grid connections."),
    "total_12m": ("All power plants planned in the next 12 months (US)", "Every new plant needs grid connection, switchgear and construction labor."),
    "ercot_12m": ("Power plants planned in ERCOT (Texas grid) in the next 12 months", "More new supply in Texas pressures power prices for ERCOT generators (VST)."),
}


def candidates(p: dict[str, Any]) -> list[dict[str, Any]]:
    ind, text = (p.get("industry") or "") + " " + (p.get("sector") or ""), p.get("summary", "")
    keys: list[str] = []
    if has(ind, "solar") or has(text, "solar tracker", "solar project", "utility-scale solar"):
        keys += ["solar_12m"]
    if has(ind, "utilities", "independent power", "renewable"):
        keys += ["total_12m", "gas_12m", "solar_12m"]
    if has(ind, "electrical equipment", "specialty industrial machinery", "engineering & construction", "electrical") or \
            has(text, "gas turbine", "switchgear", "transformer", "grid", "power generation"):
        keys += ["gas_12m", "battery_12m", "total_12m"]
    if has(text, "texas", "ercot"):
        keys += ["ercot_12m"]
    return [{"id": f"eia860m:{k}", "source": "EIA-860M", "title": SPECS[k][0], "unit": "MW", "freq": "M", "agg": "mean", "growth": "pct",
             "kind": "industry", "key": k, "reason": SPECS[k][1]} for k in dict.fromkeys(keys)]


def file_links() -> dict[str, str]:
    """'YYYY-MM' -> URL of that month's file (current and archive)."""
    html = get(PAGE).text
    out = {}
    for href in re.findall(r'href="([^"]+_generator(\d{4})\.xlsx)"', html, re.I):
        url, yr = href
        m = re.search(r"/([a-z]+)_generator\d{4}\.xlsx", url, re.I)
        if m and m.group(1).lower() in MONTHS:
            out[f"{yr}-{MONTHS[m.group(1).lower()]:02d}"] = url if url.startswith("http") else "https://www.eia.gov" + url
    return out


def summarize(xlsx: bytes, ym: str) -> dict[str, float]:
    raw = pd.read_excel(io.BytesIO(xlsx), "Planned", header=None)
    hdr = next(i for i in range(10) if "Technology" in [str(x).strip() for x in raw.iloc[i].tolist()])
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(x).strip() for x in raw.iloc[hdr].tolist()]
    df = df[pd.to_numeric(df.get("Nameplate Capacity (MW)"), errors="coerce").notna()]
    mw = pd.to_numeric(df["Nameplate Capacity (MW)"], errors="coerce")
    when = pd.to_numeric(df["Planned Operation Year"], errors="coerce") * 12 + pd.to_numeric(df["Planned Operation Month"], errors="coerce")
    y, m = map(int, ym.split("-"))
    soon = (when >= y * 12 + m) & (when <= y * 12 + m + 12)
    out = {f"{g}_12m": float(mw[soon & df["Technology"].isin(techs)].sum()) for g, techs in GROUPS.items()}
    out["total_12m"] = float(mw[soon].sum())
    ba = df.get("Balancing Authority Code")
    out["ercot_12m"] = float(mw[soon & (ba.astype(str).str.strip() == "ERCO")].sum()) if ba is not None else None
    return out


def table(months: int = 54) -> dict[str, dict[str, float]]:
    """Monthly totals, newest files added as they appear; each file is downloaded once."""
    have = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    links = file_links()
    want = sorted(links)[-months:]
    for ym in want:
        if ym in have:
            continue
        try:
            have[ym] = summarize(get(links[ym], timeout=180).content, ym)
            CACHE.write_text(json.dumps(have), encoding="utf-8")
        except Exception as e:
            log.warning("EIA-860M %s: %s", ym, e)
    return have


def fetch(spec: dict[str, Any]) -> pd.Series:
    t = table()
    return pd.Series({pd.Period(ym, "M").end_time.normalize(): v.get(spec["key"]) for ym, v in t.items() if v.get(spec["key"]) is not None},
                     dtype=float).sort_index()

