"""openFDA (FDA's free API, no key): device problem reports and recalls filed against a device maker, by month."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from . import get, has

API = "https://api.fda.gov/device"


def _firm(profile: dict[str, Any]) -> str:
    return (profile.get("short") or "").upper().replace(",", "").strip()


def _total(path: str, search: str) -> int:
    r = get(f"{API}/{path}.json", {"search": search, "limit": 1})
    return int(((r.json().get("meta") or {}).get("results") or {}).get("total") or 0) if r.status_code == 200 else 0


def candidates(p: dict[str, Any]) -> list[dict[str, Any]]:
    if not has(p.get("sector", "") + " " + p.get("industry", ""), "health", "medical") or len(_firm(p)) < 3:
        return []
    firm = _firm(p)
    out = []
    try:
        if _total("event", f'device.manufacturer_d_name:"{firm}"') >= 50:
            out.append({"id": f"openfda:events:{firm}", "source": "openFDA", "title": f"FDA device problem reports naming {firm.title()}",
                        "unit": "reports", "freq": "M", "agg": "sum", "growth": "pct", "kind": "company", "firm": firm, "what": "events",
                        "reason": "More devices in use means more reports filed; a spike beyond usage can flag a quality problem."})
        if _total("recall", f'recalling_firm:"{firm}"') >= 3:
            out.append({"id": f"openfda:recalls:{firm}", "source": "openFDA", "title": f"FDA recalls by {firm.title()}",
                        "unit": "recalls", "freq": "M", "agg": "sum", "growth": "diff", "kind": "company", "firm": firm, "what": "recalls",
                        "reason": "Recalls cost money and share; a cluster is a thesis risk."})
    except Exception:
        return out
    return out


def fetch(spec: dict[str, Any]) -> pd.Series:
    firm, out = spec["firm"], {}
    if spec["what"] == "events":
        y0 = date.today().year - 6
        for y in range(y0, date.today().year + 1):  # one year per request keeps each count under openFDA's 1,000 buckets
            r = get(f"{API}/event.json", {"search": f'device.manufacturer_d_name:"{firm}" AND date_received:[{y}0101 TO {y}1231]',
                                          "count": "date_received"})
            for x in (r.json().get("results") or []) if r.status_code == 200 else []:
                out[pd.Timestamp(x["time"])] = x["count"]
    else:
        r = get(f"{API}/recall.json", {"search": f'recalling_firm:"{firm}"', "count": "event_date_initiated"})
        for x in (r.json().get("results") or []) if r.status_code == 200 else []:
            out[pd.Timestamp(x["time"])] = x["count"]
    s = pd.Series(out, dtype=float).sort_index()
    if s.empty:
        return s
    m = s.resample("ME").sum()
    return m.reindex(pd.date_range(m.index[0], pd.Timestamp(date.today()) + pd.offsets.MonthEnd(0), freq="ME"), fill_value=0.0)
