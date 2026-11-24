"""Book risk: volatility, beta, VaR, factor loadings, risk contribution, stress tests.

All weights are fractions of total account value (cash has zero risk).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, float]:
    """Return (coefficients incl. intercept, R^2)."""
    Xc = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ coef
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
    return coef, r2


def factor_returns(daily: pd.DataFrame, factors: dict[str, dict[str, str]]) -> pd.DataFrame:
    out = {}
    for name, spec in factors.items():
        long_ = spec["long"]
        if long_ not in daily:
            continue
        r = daily[long_]
        short = spec.get("short")
        if short:
            if short not in daily:
                continue
            r = r - daily[short]
        out[name] = r
    return pd.DataFrame(out)


def compute(weights: dict[str, float], prices: pd.DataFrame, rules: dict[str, Any],
            nav: float, benchmark: str = "SPY") -> dict[str, Any]:
    """weights: ticker -> fraction of NAV. prices: adjusted closes incl. benchmark and proxy ETFs."""
    names = [t for t, w in weights.items() if w and t in prices.columns]
    missing = [t for t, w in weights.items() if w and t not in prices.columns]
    if not names or benchmark not in prices.columns:
        return {"ok": False, "error": "Not enough price history yet", "missing": missing}

    daily = prices.pct_change(fill_method=None).iloc[1:]
    last_1y = daily.iloc[-TRADING_DAYS:]
    w = pd.Series({t: weights[t] for t in names})

    # Per-name stats
    weekly = prices.resample("W-FRI").last().pct_change(fill_method=None).iloc[1:]
    per: dict[str, dict[str, Any]] = {}
    for t in names:
        r = last_1y[t].dropna()
        wk = pd.concat([weekly[t], weekly[benchmark]], axis=1).dropna()
        beta = None
        if len(wk) >= 26:
            cov = np.cov(wk.iloc[:, 0], wk.iloc[:, 1])
            beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else None
        per[t] = {
            "vol": float(r.std() * np.sqrt(TRADING_DAYS)) if len(r) > 20 else None,
            "beta": beta,
            "history_days": int(prices[t].notna().sum()),
        }

    # Portfolio covariance on the last year, names with enough data
    sub = last_1y[names].dropna(axis=1, thresh=120).fillna(0.0)
    used = list(sub.columns)
    wu = w[used]
    cov = sub.cov() * TRADING_DAYS
    port_var = float(wu @ cov @ wu)
    port_vol = float(np.sqrt(port_var)) if port_var > 0 else 0.0
    mcr = (cov @ wu) * wu / port_var if port_var > 0 else wu * 0
    for t in used:
        per[t]["risk_share"] = float(mcr[t])

    port_daily = (sub * wu).sum(axis=1)
    bench = last_1y[benchmark].reindex(port_daily.index).fillna(0.0)
    bcov = np.cov(port_daily, bench)
    port_beta = float(bcov[0, 1] / bcov[1, 1]) if bcov[1, 1] > 0 else None
    var_param = 1.645 * port_vol / np.sqrt(TRADING_DAYS) * nav
    var_hist = float(-np.percentile(port_daily, 5) * nav)
    es_hist = float(-port_daily[port_daily <= np.percentile(port_daily, 5)].mean() * nav)
    curve = (1 + port_daily).cumprod()
    max_dd = float((curve / curve.cummax() - 1).min())

    # Factor model (daily, 1y)
    fac = factor_returns(last_1y, rules.get("factors", {})).dropna()
    factor_out: dict[str, Any] = {"loadings": {}, "systematic_share": None}
    if not fac.empty:
        idx = fac.index.intersection(sub.index)
        F = fac.loc[idx].values
        idio_var = {}
        for t in used:
            y = sub.loc[idx, t].values
            coef, r2 = _ols(y, F)
            per[t]["factors"] = {k: float(c) for k, c in zip(fac.columns, coef[1:])}
            per[t]["r2"] = float(r2)
            resid = y - np.column_stack([np.ones(len(F)), F]) @ coef
            idio_var[t] = float(resid.var() * TRADING_DAYS)
        B = np.array([[per[t]["factors"][k] for k in fac.columns] for t in used])
        port_load = wu.values @ B
        factor_out["loadings"] = {k: float(v) for k, v in zip(fac.columns, port_load)}
        cov_f = np.cov(F.T) * TRADING_DAYS
        sys_var = float(port_load @ cov_f @ port_load)
        idio = float(sum(wu[t] ** 2 * idio_var[t] for t in used))
        if sys_var + idio > 0:
            factor_out["systematic_share"] = sys_var / (sys_var + idio)

    # Correlation (1y daily) for the heatmap, ordered by weight
    order = list(wu.sort_values(ascending=False).index)
    corr = sub[order].corr().round(2)

    # Stress scenarios: regress each name on the scenario's drivers (2y weekly)
    scenarios = []
    for sc in rules.get("scenarios", []):
        drivers = [d for d in sc["shocks"] if d in weekly.columns]
        if not drivers:
            continue
        shock = np.array([sc["shocks"][d] for d in drivers])
        by_name = {}
        for t in used:
            df = weekly[[t] + drivers].dropna()
            if len(df) < 40:
                continue
            coef, _ = _ols(df[t].values, df[drivers].values)
            move = float(coef[1:] @ shock)
            by_name[t] = {"move": move, "pnl": move * weights[t] * nav}
        total = sum(v["pnl"] for v in by_name.values())
        worst = sorted(by_name.items(), key=lambda kv: kv[1]["pnl"])[:3]
        scenarios.append({
            "name": sc["name"], "shocks": sc["shocks"], "pnl": total, "pct": total / nav if nav else 0,
            "worst": [{"ticker": k, **v} for k, v in worst],
        })

    # Historical replay: worst 20-day benchmark window in the history, current weights
    replay = None
    roll = (1 + daily[benchmark]).rolling(20).apply(np.prod, raw=True) - 1
    if roll.notna().any():
        end = roll.idxmin()
        start_i = daily.index.get_loc(end) - 19
        window = daily.iloc[start_i:daily.index.get_loc(end) + 1]
        name_moves = ((1 + window[used].fillna(0)).prod() - 1)
        pnl = float((name_moves * wu).sum() * nav)
        replay = {"start": window.index[0].date().isoformat(), "end": end.date().isoformat(),
                  "benchmark_move": float(roll.min()), "pnl": pnl, "pct": pnl / nav if nav else 0}

    wsq = float((w ** 2).sum())
    return {
        "ok": True,
        "as_of": prices.index[-1].date().isoformat(),
        "names": per,
        "missing": missing,
        "portfolio": {
            "vol": port_vol, "beta": port_beta, "var95_param": var_param, "var95_hist": var_hist,
            "es95_hist": es_hist, "max_drawdown_1y": max_dd,
            "effective_n": (w.sum() ** 2 / wsq) if wsq > 0 else None,
            "top5_weight": float(w.sort_values(ascending=False).head(5).sum()),
            "gross_invested": float(w.sum()),
        },
        "factors": factor_out,
        "corr": {"tickers": order, "matrix": corr.values.tolist()},
        "scenarios": scenarios,
        "replay": replay,
    }
