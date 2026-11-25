"""Does a series track the company? Year-on-year change of the series (rolled up to the company's fiscal quarters)
against year-on-year revenue growth, with the series leading by 0, 1 or 2 quarters.

Rules, so coincidences don't pass:
- at least 8 quarters of overlap before the last 4;
- the best lag is picked on the earlier quarters only (correlation >= 0.5 to be proposed, 0.3 = weak);
- the last 4 quarters, which played no part in picking it, must agree in direction at least 3 times out of 4.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def revenue(t: str) -> pd.Series | None:
    """Quarterly revenue (3-month figures) from the company's SEC filings, by quarter end."""
    from .. import sec
    try:
        st = sec.statements(t, years=8, quarters=28)
    except Exception:
        return None
    if not st.get("ok"):
        return None
    row = next((r for s in st["statements"].values() for r in s if r["key"] == "rev"), None)
    if not row or not row.get("Q"):
        return None
    s = pd.Series({pd.Timestamp(e): v for e, v in row["Q"].items() if v is not None}, dtype=float).sort_index()
    return s[s > 0]


def to_quarters(s: pd.Series, ends: list[pd.Timestamp], agg: str) -> pd.Series:
    """Roll a monthly / weekly / daily series up to the company's quarter windows (end - 3 months, end]."""
    s = s.dropna().sort_index()
    out = {}
    for e in ends:
        start = (e - pd.DateOffset(months=3)) + pd.offsets.MonthEnd(0)  # the prior quarter's last day, excluded
        win = s[(s.index > start) & (s.index <= e + pd.Timedelta(days=3))]
        if win.empty:
            continue
        out[e] = win.sum() if agg == "sum" else win.iloc[-1] if agg == "last" else win.mean()
    return pd.Series(out, dtype=float)


def yoy(s: pd.Series, how: str) -> pd.Series:
    """Change against the same quarter a year earlier (4 quarters back by position)."""
    prev = s.shift(4)
    if how == "diff":
        return s - prev
    return (s / prev - 1).where(prev.abs() > 1e-9)


def test(series: pd.Series, rev: pd.Series, agg: str = "sum", growth: str = "pct") -> dict[str, Any]:
    ends = list(rev.index)
    q = to_quarters(series, ends, agg)
    sx, rx = yoy(q, growth), yoy(rev, "pct")
    best: dict[str, Any] | None = None
    for lag in (0, 1, 2):
        d = pd.concat([sx.shift(lag), rx], axis=1, keys=["s", "r"]).dropna()
        if len(d) < 12:
            continue
        fit_part, hold = d.iloc[:-4], d.iloc[-4:]
        if fit_part["s"].std() == 0 or fit_part["r"].std() == 0:
            continue
        c = float(np.corrcoef(fit_part["s"], fit_part["r"])[0, 1])
        if best is None or c > best["fit"]:
            agree = int((np.sign(hold["s"] - fit_part["s"].mean()) == np.sign(hold["r"] - fit_part["r"].mean())).sum())
            best = {"fit": round(c, 2), "lead": lag, "n": len(fit_part), "agree": agree}
    if not best:
        n = len(pd.concat([sx, rx], axis=1).dropna())
        return {"status": "untested", "note": f"Only {n} quarters overlap with revenue; needs 12 (8 to fit, 4 to check)."}
    hold = f"{best['agree']} of 4"
    if best["fit"] >= 0.5 and best["agree"] >= 3:
        status, note = "proposed", f"Moves with revenue growth (correlation {best['fit']:.2f}) and called {hold} of the last quarters it never saw."
    elif best["fit"] >= 0.3:
        status, note = "weak", f"Some link (correlation {best['fit']:.2f}; {hold} recent quarters agreed); not enough to rely on."
    else:
        status, note = "rejected", f"No useful link to revenue growth (best correlation {best['fit']:.2f})."
    return {"status": status, "fit": best["fit"], "lead": best["lead"], "n": best["n"], "holdout": hold, "note": note}


def latest_change(s: pd.Series, growth: str, freq: str) -> dict[str, Any]:
    """Latest value, its change on a year earlier, and how unusual that change is (z against its own history)."""
    s = s.dropna()
    if len(s) < 3:
        return {}
    step = {"M": 12, "W": 52, "D": 365}.get(freq, 12)
    last = float(s.iloc[-1])
    out: dict[str, Any] = {"last": last, "as_of": s.index[-1].date().isoformat()}
    if len(s) > step:
        ch = (s - s.shift(step)) if growth == "diff" else (s / s.shift(step) - 1).where(s.shift(step).abs() > 1e-9)
        ch = ch.dropna()
        if len(ch):
            c = float(ch.iloc[-1])
            out["yoy"] = c
            out["label"] = f"{c:+.1f} vs a year ago" if growth == "diff" else f"{c * 100:+.0f}% vs a year ago"
            hist = ch.iloc[:-1]
            if len(hist) >= 8 and hist.std() > 0:
                out["z"] = round((c - hist.mean()) / hist.std(), 2)
    return out
