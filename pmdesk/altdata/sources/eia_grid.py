"""EIA grid data (free key: .secrets/eia_api_key): daily electricity demand and generation in ERCOT, the Texas grid,
monthly. Demand growth is what lets Texas generators (VST) sell more power at better prices."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from ...config import SECRETS_DIR
from . import get, has

API = "https://api.eia.gov/v2/electricity/rto/daily-region-data/data/"


def key() -> str | None:
    import os
    k = os.environ.get("EIA_API_KEY")
    f = SECRETS_DIR / "eia_api_key"
    return k or (f.read_text(encoding="utf-8").strip() if f.exists() else None)


def candidates(p: dict[str, Any]) -> list[dict[str, Any]]:
    if not (has(p.get("industry", "") + " " + p.get("sector", ""), "utilities", "independent power") and has(p.get("summary", ""), "texas", "ercot")):
        return []
    return [{"id": "eia:ercot_demand", "source": "EIA grid data", "title": "Electricity demand in ERCOT (Texas grid)", "unit": "MWh",
             "freq": "M", "agg": "sum", "growth": "pct", "kind": "industry", "needs": None if key() else "EIA API key",
             "reason": "More Texas power demand means more and pricier sales for ERCOT generators."}]


def fetch(spec: dict[str, Any]) -> pd.Series:
    k = key()
    if not k:
        raise RuntimeError("needs an EIA API key (.secrets/eia_api_key)")
    start = (date.today() - timedelta(days=6 * 365)).isoformat()
    out: dict[pd.Timestamp, float] = {}
    offset = 0
    while True:
        r = get(API, {"api_key": k, "frequency": "daily", "data[0]": "value", "facets[respondent][]": "ERCO", "facets[type][]": "D",
                      "facets[timezone][]": "Central", "start": start, "sort[0][column]": "period", "sort[0][direction]": "asc",
                      "offset": offset, "length": 5000})
        rows = (r.json().get("response") or {}).get("data") or []
        for x in rows:
            out[pd.Timestamp(x["period"])] = float(x["value"])
        if len(rows) < 5000:
            break
        offset += 5000
    s = pd.Series(out, dtype=float).sort_index()
    return s.resample("ME").sum() if not s.empty else s
