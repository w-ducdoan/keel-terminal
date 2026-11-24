"""Performance attribution: what drove the book's return, and its gap to SPY.

Contribution by name: each day's P&L on a position, divided by the book's value just before it, summed over the
period. P&L per day is exact from the fills: value today - value yesterday - net cash spent on it today.
The denominator is the daily rebuilt NAV where the desk has it, else the latest performance-log month-end.

Vs SPY, by sector (Brinson-Fachler, sector weights of SPY today, each sector's return from its SPDR fund):
  allocation  = (book weight - SPY weight) x (sector return - SPY return)   -> did you own the right sectors
  selection   = book weight x (your return in the sector - sector return)    -> did you pick the right names
  cash drag   = cash weight x (0 - SPY return)
Names outside GICS sectors (hedges, index ETFs) are judged against SPY itself. Whatever is left between the sum of
the parts and the actual time-weighted return (dividends, cash income, compounding, weights drifting inside the
period) is shown as "other", never hidden. Rule-based; no AI.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

# GICS sector -> (SPY sector key in Yahoo's fund data, sector SPDR used as that sector's benchmark return)
SECTORS = {
    "Information Technology": ("technology", "XLK"), "Health Care": ("healthcare", "XLV"), "Financials": ("financial_services", "XLF"),
    "Consumer Discretionary": ("consumer_cyclical", "XLY"), "Consumer Staples": ("consumer_defensive", "XLP"),
    "Communication Services": ("communication_services", "XLC"), "Industrials": ("industrials", "XLI"), "Energy": ("energy", "XLE"),
    "Materials": ("basic_materials", "XLB"), "Utilities": ("utilities", "XLU"), "Real Estate": ("realestate", "XLRE"),
}
PERIODS = ("1M", "MTD", "QTD", "3M", "YTD")


def period_start(period: str, today: date) -> date:
    """The last day before the period (returns are measured from its close)."""
    if period == "MTD":
        return today.replace(day=1) - timedelta(days=1)
    if period == "QTD":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1) - timedelta(days=1)
    if period == "YTD":
        return date(today.year, 1, 1) - timedelta(days=1)
    if period == "3M":
        return today - timedelta(days=91)
    return today - timedelta(days=30)  # 1M


def daily_pnl(shares_now: dict[str, float], fills: list[dict[str, Any]], closes: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """P&L per ticker per day (rows = days[1:]), exact from fills: q_d*P_d - q_(d-1)*P_(d-1) - net cash spent on day d."""
    tickers = sorted(set(shares_now) | {f["symbol"] for f in fills})

    def shares_on(d: str) -> dict[str, float]:
        sh = {t: shares_now.get(t, 0.0) for t in tickers}
        for f in fills:
            if f["date"] > d:
                sh[f["symbol"]] += -f["qty"] if f["side"] == "buy" else f["qty"]
        return sh

    def px(t: str, d: str) -> float | None:
        if t not in closes.columns:
            return None
        s = closes[t].loc[:d].dropna()
        return float(s.iloc[-1]) if len(s) else None

    rows = {}
    prev = shares_on(days[0])
    for i in range(1, len(days)):
        d, dp = days[i], days[i - 1]
        cur = shares_on(d)
        row = {}
        for t in tickers:
            p1, p0 = px(t, d), px(t, dp)
            if p1 is None or (p0 is None and prev[t]):
                row[t] = 0.0
                continue
            spent = sum((f["qty"] * f["price"]) * (1 if f["side"] == "buy" else -1) for f in fills if f["date"] == d and f["symbol"] == t)
            row[t] = cur[t] * p1 - prev[t] * (p0 or 0.0) - spent
        rows[d] = row
        prev = cur
    return pd.DataFrame.from_dict(rows, orient="index").fillna(0.0)


def values(shares_now: dict[str, float], fills: list[dict[str, Any]], closes: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """Market value per ticker at each day's close (for average weights)."""
    tickers = sorted(set(shares_now) | {f["symbol"] for f in fills})
    out = {}
    for d in days:
        row = {}
        for t in tickers:
            q = shares_now.get(t, 0.0) + sum((-f["qty"] if f["side"] == "buy" else f["qty"]) for f in fills if f["symbol"] == t and f["date"] > d)
            s = closes[t].loc[:d].dropna() if t in closes.columns else pd.Series(dtype=float)
            row[t] = q * float(s.iloc[-1]) if len(s) and abs(q) > 1e-9 else 0.0
        out[d] = row
    return pd.DataFrame.from_dict(out, orient="index").fillna(0.0)


def nav_before(points: list[dict[str, Any]], d: str) -> float | None:
    prior = [p for p in points if p["date"] < d and p.get("nav")]
    return float(prior[-1]["nav"]) if prior else None


def brinson(w_p: dict[str, float], r_p: dict[str, float], w_b: dict[str, float], r_b: dict[str, float], R_b: float,
            cash_w: float) -> dict[str, Any]:
    """Sector effects. w_p / r_p: book weight and return per group; w_b / r_b: SPY weight and sector-fund return."""
    rows = []
    for g in sorted(set(w_p) | set(w_b)):
        wp, wb = w_p.get(g, 0.0), w_b.get(g, 0.0)
        rb = r_b.get(g, R_b)  # non-GICS groups (hedge, index ETFs) are judged against SPY
        rp = r_p.get(g)
        alloc = (wp - wb) * (rb - R_b)
        sel = wp * ((rp if rp is not None else rb) - rb)
        rows.append({"group": g, "w_book": wp, "w_spy": wb, "r_book": rp, "r_bench": rb, "allocation": alloc, "selection": sel})
    cash = cash_w * (0.0 - R_b)
    return {"rows": rows, "allocation": sum(r["allocation"] for r in rows), "selection": sum(r["selection"] for r in rows), "cash": cash}


def compute(period: str, positions: list[dict[str, Any]], perf_points: list[dict[str, Any]], fills_fn: Callable[[str], list[dict[str, Any]]],
            closes_fn: Callable[[list[str], str], pd.DataFrame], spy_weights: dict[str, float], classify: Callable[[str], tuple[str, str]],
            today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    base = period_start(period, today).isoformat()
    fills = sorted(fills_fn(base), key=lambda f: f["ts"])
    shares_now = {p["ticker"]: float(p["shares"]) for p in positions}
    tickers = sorted(set(shares_now) | {f["symbol"] for f in fills})
    etfs = sorted({e for _, e in SECTORS.values()})
    closes = closes_fn(sorted(set(tickers) | set(etfs) | {"SPY"}), (date.fromisoformat(base) - timedelta(days=7)).isoformat())
    days = [d for d in closes.index if d >= base and pd.notna(closes.at[d, "SPY"])]
    if days and days[0] > base:  # base was not a trading day: start from the last close at or before it
        prior = [d for d in closes.index if d <= base and pd.notna(closes.at[d, "SPY"])]
        days = prior[-1:] + days
    if len(days) < 2:
        return {"ok": False, "error": "Not enough trading days in the period yet."}
    pnl = daily_pnl(shares_now, fills, closes, days)
    vals = values(shares_now, fills, closes, days[:-1])  # start-of-day values for weights
    den = pd.Series({d: nav_before(perf_points, d) for d in pnl.index}, dtype=float)
    if den.isna().all():
        return {"ok": False, "error": "No book value history before the period (performance-log.md)."}
    den = den.ffill().bfill()
    contrib_daily = pnl.div(den, axis=0)
    contrib = contrib_daily.sum()
    # Weight on each P&L day = that position's value at the prior close / the book value used for that day
    wts = pd.DataFrame(vals.values / den.values[:, None], index=pnl.index, columns=vals.columns)
    avg_w = wts.mean()

    # Actual returns over the period: book TWR from the perf series, SPY from closes
    def twr_at(d: str) -> float | None:
        prior = [p for p in perf_points if p["date"] <= d]
        return prior[-1]["twr"] if prior else None
    t0, t1 = twr_at(days[0]), perf_points[-1]["twr"] if perf_points else None
    book_ret = (1 + t1) / (1 + t0) - 1 if t0 is not None and t1 is not None else float(contrib.sum())
    spy_ret = float(closes.at[days[-1], "SPY"] / closes.at[days[0], "SPY"] - 1)

    names = []
    for t in tickers:
        bucket, sector = classify(t)
        names.append({"ticker": t, "contribution": float(contrib.get(t, 0.0)), "pnl": float(pnl[t].sum()) if t in pnl else 0.0,
                      "avg_weight": float(avg_w.get(t, 0.0)), "held_now": t in shares_now, "bucket": bucket, "sector": sector})
    names.sort(key=lambda x: -x["contribution"])

    def group(key: str) -> list[dict[str, Any]]:
        g: dict[str, dict[str, float]] = {}
        for n in names:
            x = g.setdefault(n[key] or "Unassigned", {"contribution": 0.0, "avg_weight": 0.0, "pnl": 0.0})
            x["contribution"] += n["contribution"]; x["avg_weight"] += n["avg_weight"]; x["pnl"] += n["pnl"]
        # Contribution / average weight is a fair return only for names held the whole period; for names sold midway the
        # average weight counts the days after the sale as zero and overstates the return, so none is shown.
        return sorted([{"group": k, **v, "ret": v["contribution"] / v["avg_weight"] if v["avg_weight"] > 1e-6 and k != "Sold in period" else None}
                       for k, v in g.items()], key=lambda x: -x["contribution"])

    by_sector = group("sector")
    # Benchmark side: SPY's sector weights (today's) and each sector fund's return over the period
    w_b, r_b = {}, {}
    for sec_name, (k, etf) in SECTORS.items():
        w_b[sec_name] = float(spy_weights.get(k, 0.0))
        if etf in closes.columns and pd.notna(closes.at[days[0], etf]) and pd.notna(closes.at[days[-1], etf]):
            r_b[sec_name] = float(closes.at[days[-1], etf] / closes.at[days[0], etf] - 1)
    tot_w = sum(w_b.values()) or 1.0
    w_b = {k: v / tot_w for k, v in w_b.items()}
    w_p = {r["group"]: r["avg_weight"] for r in by_sector}
    r_p = {r["group"]: r["ret"] for r in by_sector if r["ret"] is not None}
    cash_w = max(0.0, 1.0 - sum(w_p.values()))
    br = brinson(w_p, r_p, w_b, r_b, spy_ret, cash_w)
    excess = book_ret - spy_ret
    other = excess - (br["allocation"] + br["selection"] + br["cash"])
    return {"ok": True, "period": period, "from": days[0], "to": days[-1], "book": book_ret, "spy": spy_ret, "excess": excess,
            "contrib_total": float(contrib.sum()), "names": names, "buckets": group("bucket"), "sectors": br["rows"],
            "effects": {"allocation": br["allocation"], "selection": br["selection"], "cash": br["cash"], "other": other},
            "cash_weight": cash_w, "fills": len(fills),
            "note": "Sector weights for SPY are today's; sector returns from SPDR sector funds. Dividends and cash income sit in 'other'."}
