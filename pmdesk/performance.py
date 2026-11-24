"""Time-weighted return vs SPY.

History comes from the research folder/portfolio/performance-log.md (the source of truth, built from
the full Robinhood activity export). After the anchor row named in settings.yaml, the desk
rebuilds each trading day itself and chains it on:

  shares on day d  = shares today, minus fills after d (buys), plus fills after d (sells)
  cash on day d    = anchor cash + sell proceeds - buy costs, through day d
  external flows   = bank-funded recurring buys (Robinhood debits the bank, not the cash balance)
  daily return     = (NAV_d - flow_d) / NAV_(d-1) - 1, at official closes; today uses live marks

Cash left over after replaying every fill is reported. A few dollars is dividends and lending
income (return). A larger gap is a deposit or withdrawal the desk cannot date, so it is taken
out of today's return and flagged for the log.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from . import market

ET = ZoneInfo("America/New_York")
_num = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _val(cell: str) -> float | None:
    s = cell.replace("**", "").replace("−", "-").replace("$", "").strip()
    m = _num.search(s)
    return float(m.group(0).replace(",", "")) if m else None


def parse_log(path: Path) -> list[dict[str, Any]]:
    """Rows of the log table: date, account value, cumulative TWR and SPY (as fractions)."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.search(r"\d{4}-\d{2}-\d{2}", cells[0]) if cells else None
        if not m or len(cells) < 4:
            continue
        twr, spy = _val(cells[2]), _val(cells[3])
        if twr is None or spy is None:
            continue
        label = re.sub(r"\*|\(current\)", "", cells[0]).replace(m.group(0), "").strip(" ()")
        rows.append({"date": m.group(0), "value": _val(cells[1]), "twr": twr / 100, "spy": spy / 100,
                     "label": label, "source": "log"})
    rows.sort(key=lambda r: r["date"])
    return rows


def _et_date(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET).date().isoformat()


def _closes(symbols: list[str], start: str) -> pd.DataFrame:
    """Official (unadjusted) daily closes from start, columns in the caller's spelling."""
    to_y = {s: market.yahoo_symbol(s) for s in symbols}
    ys = sorted(set(to_y.values()))
    df = yf.download(ys, start=start, interval="1d", auto_adjust=False, progress=False, threads=True)
    out = market._close_frame(df, ys).rename(columns={v: k for k, v in to_y.items()})
    out.index = [pd.Timestamp(i).date().isoformat() for i in out.index]
    return out.sort_index()


def rebuild(anchor: dict[str, Any], anchor_cash: float, positions: list[dict[str, Any]], cash_now: float,
            fills: list[dict[str, Any]], closes: pd.DataFrame, nav_live: float | None, spy_live: float | None,
            today: str, income_tolerance: float = 10.0, transfers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Daily NAV and chained TWR from the anchor row to today. Pure function (tested offline).
    transfers: deposits (+) and withdrawals (-) the desk saw land, [{date, amount}]; they move cash
    and are external flows, so they never count as gains or losses."""
    a_date = anchor["date"]
    transfers = [t for t in (transfers or []) if t["date"] > a_date]
    fills = sorted([f for f in fills if f["date"] > a_date], key=lambda f: f["ts"])
    shares_now = {p["ticker"]: float(p["shares"]) for p in positions}
    tickers = sorted(set(shares_now) | {f["symbol"] for f in fills})

    def shares_on(d: str) -> dict[str, float]:
        sh = dict.fromkeys(tickers, 0.0)
        sh.update(shares_now)
        for f in fills:
            if f["date"] > d:
                sh[f["symbol"]] = sh.get(f["symbol"], 0.0) + (-f["qty"] if f["side"] == "buy" else f["qty"])
        return sh

    def cash_on(d: str) -> float:
        c = anchor_cash + sum(t["amount"] for t in transfers if t["date"] <= d)
        for f in fills:
            if f["date"] <= d:
                amt = f["qty"] * f["price"]
                if f["side"] == "sell":
                    c += amt
                elif not f.get("recurring_amount"):  # bank-funded recurring buys never touch cash
                    c -= amt
        return c

    def flow_on(d: str) -> float:
        return (sum(f["recurring_amount"] for f in fills if f["date"] == d and f.get("recurring_amount"))
                + sum(t["amount"] for t in transfers if t["date"] == d))

    days = [d for d in closes.index if a_date <= d < today]
    missing: set[str] = set()

    def nav_at(d: str) -> float:
        total = cash_on(d)
        for t, q in shares_on(d).items():
            if abs(q) < 1e-9:
                continue
            px = closes.at[d, t] if t in closes.columns else None
            if px is None or pd.isna(px):
                prior = closes[t].loc[:d].dropna() if t in closes.columns else pd.Series(dtype=float)
                px = float(prior.iloc[-1]) if len(prior) else None
            if px is None:
                missing.add(t)
                continue
            total += q * float(px)
        return total

    model_anchor = nav_at(a_date) if a_date in closes.index else None
    residual = cash_now - cash_on(today)
    unexplained = residual if abs(residual) > income_tolerance else 0.0

    pts = [{"date": a_date, "nav": anchor["value"], "twr": anchor["twr"], "spy": anchor["spy"], "flow": 0.0, "source": "log"}]
    spy_prev = float(closes.at[a_date, "SPY"]) if a_date in closes.index else None
    # Chain from the rebuilt anchor value so day one compares like with like (holdings the
    # order history cannot date, such as a stock reward, sit on both sides of the first return).
    nav_prev = model_anchor if model_anchor else anchor["value"]
    for d in days[1:]:
        nav = nav_at(d)
        flow = flow_on(d)
        r = (nav - flow) / nav_prev - 1 if nav_prev else 0.0
        spy_c = float(closes.at[d, "SPY"])
        rs = spy_c / spy_prev - 1 if spy_prev else 0.0
        last = pts[-1]
        pts.append({"date": d, "nav": nav, "twr": (1 + last["twr"]) * (1 + r) - 1, "spy": (1 + last["spy"]) * (1 + rs) - 1,
                    "flow": flow, "ret": r, "spy_ret": rs, "source": "rebuilt"})
        nav_prev, spy_prev = nav, spy_c
    if nav_live and spy_live and spy_prev:
        flow = flow_on(today) + unexplained
        r = (nav_live - flow) / nav_prev - 1
        rs = spy_live / spy_prev - 1
        last = pts[-1]
        pts.append({"date": today, "nav": nav_live, "twr": (1 + last["twr"]) * (1 + r) - 1, "spy": (1 + last["spy"]) * (1 + rs) - 1,
                    "flow": flow, "ret": r, "spy_ret": rs, "source": "live"})
    return {
        "points": pts,
        "check": {
            "anchor_date": a_date, "anchor_value_log": anchor["value"], "anchor_value_rebuilt": model_anchor,
            "anchor_gap": (model_anchor - anchor["value"]) if model_anchor is not None else None,
            "cash_residual": residual, "income_tolerance": income_tolerance,
            "unexplained_flow": unexplained, "fills": len(fills),
            "recurring_flows": sum(f.get("recurring_amount") or 0 for f in fills),
            "transfers": transfers,
            "missing_prices": sorted(missing),
        },
    }


def periods(points: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """1D, MTD, QTD, YTD, 1Y and since-inception returns from a cumulative series."""
    t = date.fromisoformat(today)
    last = points[-1]

    def base_before(d: str) -> dict[str, Any] | None:
        prior = [p for p in points if p["date"] < d]
        return prior[-1] if prior else None

    q_start = date(t.year, 3 * ((t.month - 1) // 3) + 1, 1)
    specs = [
        ("1D", base_before(last["date"])),
        ("MTD", base_before(t.replace(day=1).isoformat())),
        ("QTD", base_before(q_start.isoformat())),
        ("YTD", base_before(date(t.year, 1, 1).isoformat())),
        ("1Y", base_before((t - timedelta(days=364)).isoformat())),
        ("ITD", points[0]),
    ]
    out = []
    for name, b in specs:
        if not b:
            continue
        book = (1 + last["twr"]) / (1 + b["twr"]) - 1
        spy = (1 + last["spy"]) / (1 + b["spy"]) - 1
        out.append({"period": name, "base_date": b["date"], "book": book, "spy": spy, "excess": book - spy})
    itd_days = (t - date.fromisoformat(points[0]["date"])).days
    if itd_days > 365 and out and out[-1]["period"] == "ITD":
        out[-1]["book_ann"] = (1 + out[-1]["book"]) ** (365 / itd_days) - 1
        out[-1]["spy_ann"] = (1 + out[-1]["spy"]) ** (365 / itd_days) - 1
    return out


def detect_transfer(residual: float, baseline: float | None, threshold: float) -> float | None:
    """A deposit or withdrawal shows up as a jump in the cash no trade explains. Small drift
    (dividends, lending income, a few cents of rounding) stays below the threshold."""
    if baseline is None:
        return None
    delta = residual - baseline
    return round(delta, 2) if abs(delta) >= threshold else None


def compute(log_path: Path, anchor_date: str, anchor_cash: float, broker: Any, positions: list[dict[str, Any]],
            cash_now: float, nav_live: float | None, spy_live: float | None,
            transfers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = parse_log(log_path)
    if not rows:
        return {"ok": False, "error": "No rows found in performance-log.md"}
    anchor = next((r for r in rows if r["date"] == anchor_date), None)
    today = datetime.now(ET).date().isoformat()
    history = [r for r in rows if r["date"] <= anchor_date] if anchor else rows
    out: dict[str, Any] = {"ok": True, "log_rows": len(rows), "anchor_date": anchor_date, "today": today}
    later_rows = [r["date"] for r in rows if r["date"] > anchor_date]
    if later_rows:
        out["warning"] = (f"performance-log.md has rows after the anchor ({', '.join(later_rows)}). "
                          "Move performance.anchor_date and anchor_cash in settings.yaml to the newest row.")
    fills_fn = getattr(broker, "fills_since", None)
    if not anchor or not fills_fn or not getattr(broker, "account_number", None):
        out.update(points=history, periods=periods(history, today),
                   note="Live rebuild unavailable (needs the Robinhood MCP feed and a matching anchor row); showing the log only.")
        return out
    fills = fills_fn(anchor_date)
    symbols = sorted({p["ticker"] for p in positions} | {f["symbol"] for f in fills} | {"SPY"})
    closes = _closes(symbols, anchor_date)
    built = rebuild(anchor, anchor_cash, positions, cash_now, fills, closes, nav_live, spy_live, today, transfers=transfers)
    pts = history[:-1] + built["points"]
    out.update(points=pts, periods=periods(pts, today), check=built["check"])
    return out
