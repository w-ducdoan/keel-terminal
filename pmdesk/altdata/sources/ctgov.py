"""ClinicalTrials.gov (free API, no key): new industry-sponsored drug and device trials starting each month,
the demand that contract research organizations (IQV) and trial-tech vendors sell into."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from . import get, has

API = "https://clinicaltrials.gov/api/v2/studies"


def candidates(p: dict[str, Any]) -> list[dict[str, Any]]:
    text = " ".join([p.get("industry", ""), p.get("sector", ""), p.get("summary", "")])
    if not (has(p.get("industry", ""), "diagnostics & research", "biotechnology", "drug manufacturers", "health information", "medical instruments")
            or has(text, "clinical trial", "clinical research", "contract research")):
        return []
    return [{"id": "ctgov:industry_starts", "source": "ClinicalTrials.gov", "title": "New industry-sponsored clinical trials starting (US registry)",
             "unit": "trials", "freq": "M", "agg": "sum", "growth": "pct", "kind": "industry",
             "reason": "Drug and device makers start trials before paying CROs and trial vendors to run them."}]


def fetch(spec: dict[str, Any]) -> pd.Series:
    out = {}
    end = pd.Timestamp(date.today()).to_period("M") - 1  # last complete month
    for per in pd.period_range(end - 59, end, freq="M"):
        a, b = per.start_time.date().isoformat(), per.end_time.date().isoformat()
        r = get(API, {"query.term": f"AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyType]INTERVENTIONAL AND AREA[StartDate]RANGE[{a},{b}]",
                      "countTotal": "true", "pageSize": 1, "fields": "NCTId"}, timeout=40)
        if r.status_code == 200:
            out[per.end_time.normalize()] = float(r.json().get("totalCount") or 0)
    return pd.Series(out, dtype=float).sort_index()
