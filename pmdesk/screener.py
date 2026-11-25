"""Screener: a mandate-aware first pass that feeds Gate 0.

Screens run on Yahoo's equity screener (fundamentals, valuation, leverage, liquidity, growth, price).
The rows on screen are then cross-checked: Robinhood's own fundamentals for market cap and the 52-week
range (a gap over 10% is flagged, not hidden), Yahoo's company profile for the screened ratios, and
Keel's coverage tags (held / watchlist / passed / tracked / new). Finalists can be verified against SEC
filings. Criteria are editable rows; presets are saved; anything looser than the mandate is labeled.
Multi-year rows ({"hist": metric, "stat": ..., "years": N}) run on Keel's own SEC table (history.py),
over every Yahoo match rather than the first page.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import yfinance as yf

from . import equity, history, rh_mcp, store

# key: (label, yahoo field, unit, group). unit: usd | pct (whole percent) | x | n
FIELDS: dict[str, tuple[str, str, str, str]] = {
    "mcap": ("Market cap", "intradaymarketcap", "usd", "Size"),
    "price": ("Price", "intradayprice", "usd", "Size"),
    "avgvol": ("Avg daily volume (3m)", "avgdailyvol3m", "n", "Size"),
    "beta": ("Beta", "beta", "x", "Risk"),
    "chg52": ("52-week change", "fiftytwowkpercentchange", "pct", "Price"),
    "pe": ("P/E (ttm)", "peratio.lasttwelvemonths", "x", "Valuation"),
    "peg": ("PEG (5y)", "pegratio_5y", "x", "Valuation"),
    "pb": ("Price / book", "pricebookratio.quarterly", "x", "Valuation"),
    "ps": ("Price / sales", "lastclosemarketcaptotalrevenue.lasttwelvemonths", "x", "Valuation"),
    "ev_ebitda": ("EV / EBITDA", "lastclosetevebitda.lasttwelvemonths", "x", "Valuation"),
    "ev_sales": ("EV / revenue", "lastclosetevtotalrevenue.lasttwelvemonths", "x", "Valuation"),
    "divy": ("Dividend yield", "forward_dividend_yield", "pct", "Valuation"),
    "roe": ("Return on equity", "returnonequity.lasttwelvemonths", "pct", "Returns"),
    "roa": ("Return on assets", "returnonassets.lasttwelvemonths", "pct", "Returns"),
    "rotc": ("Return on total capital", "returnontotalcapital.lasttwelvemonths", "pct", "Returns"),
    "gm": ("Gross margin", "grossprofitmargin.lasttwelvemonths", "pct", "Margins"),
    "ebitdam": ("EBITDA margin", "ebitdamargin.lasttwelvemonths", "pct", "Margins"),
    "nm": ("Net margin", "netincomemargin.lasttwelvemonths", "pct", "Margins"),
    "revg": ("Revenue growth (1y)", "totalrevenues1yrgrowth.lasttwelvemonths", "pct", "Growth"),
    "epsg": ("EPS growth (1y)", "dilutedeps1yrgrowth.lasttwelvemonths", "pct", "Growth"),
    "ebitdag": ("EBITDA growth (1y)", "ebitda1yrgrowth.lasttwelvemonths", "pct", "Growth"),
    "fcfg": ("Levered FCF growth (1y)", "leveredfreecashflow1yrgrowth.lasttwelvemonths", "pct", "Growth"),
    "nd_ebitda": ("Net debt / EBITDA", "netdebtebitda.lasttwelvemonths", "x", "Leverage"),
    "debt_eq": ("Total debt / equity", "totaldebtequity.lasttwelvemonths", "pct", "Leverage"),
    "int_cov": ("EBITDA / interest", "ebitdainterestexpense.lasttwelvemonths", "x", "Leverage"),
    "cur": ("Current ratio", "currentratio.lasttwelvemonths", "x", "Liquidity"),
    "quick": ("Quick ratio", "quickratio.lasttwelvemonths", "x", "Liquidity"),
    "altz": ("Altman Z-score", "altmanzscoreusingtheaveragestockinformationforaperiod.lasttwelvemonths", "x", "Liquidity"),
    "short": ("Short % of float", "short_percentage_of_float.value", "pct", "Positioning"),
    "insider": ("% held by insiders", "pctheldinsider", "pct", "Positioning"),
}
SECTORS = ["Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy", "Financial Services",
           "Healthcare", "Industrials", "Real Estate", "Technology", "Utilities"]
US_LISTED = ["NYQ", "NMS", "NGM", "NCM", "ASE", "BTS", "PCX"]  # excludes OTC (PNK), which duplicates foreign names

# The mandate floor (MANDATE.md). Rows a screen loosens get labeled; MANDATE.md stays the source of truth.
MANDATE = [
    {"field": "mcap", "op": "gte", "value": 200e6, "mandate": True},
    {"field": "beta", "op": "lte", "value": 1.8, "mandate": True},
    {"field": "nd_ebitda", "op": "lte", "value": 3.0, "mandate": True},
    {"field": "int_cov", "op": "gte", "value": 2.0, "mandate": True},
    {"field": "cur", "op": "gte", "value": 1.5, "mandate": True, "soft": "SaaS / subscription carve-out: deferred revenue can depress the current ratio"},
]

PRESETS: dict[str, dict[str, Any]] = {
    "Mandate base": {"criteria": [], "sort": "mcap"},
    "Quality under a cloud": {"criteria": [{"field": "roe", "op": "gte", "value": 15}, {"field": "chg52", "op": "lte", "value": -25},
                                           {"field": "ebitdam", "op": "gte", "value": 15}], "sort": "chg52", "asc": True},
    "Defensive quality": {"criteria": [{"field": "beta", "op": "lte", "value": 0.9}, {"field": "roe", "op": "gte", "value": 15},
                                       {"field": "nd_ebitda", "op": "lte", "value": 2.5}, {"field": "gm", "op": "gte", "value": 35}], "sort": "mcap"},
    "Small / mid alpha": {"criteria": [{"field": "mcap", "op": "btwn", "value": [300e6, 10e9]}, {"field": "revg", "op": "gte", "value": 10},
                                       {"field": "roe", "op": "gte", "value": 12}, {"field": "ev_ebitda", "op": "lte", "value": 14}], "sort": "revg"},
    "Grid and AI-infrastructure industrials": {"criteria": [{"sector": ["Industrials", "Utilities"]}, {"field": "revg", "op": "gte", "value": 8},
                                                           {"field": "mcap", "op": "gte", "value": 2e9}], "sort": "revg"},
    "Compounders at a fair price": {"criteria": [{"field": "rotc", "op": "gte", "value": 12}, {"field": "revg", "op": "gte", "value": 7},
                                                 {"field": "pe", "op": "lte", "value": 25}, {"field": "fcfg", "op": "gte", "value": 0}], "sort": "rotc"},
    "Durable growers (5y)": {"criteria": [{"hist": "rev", "stat": "cagr", "years": 5, "op": "gte", "value": 8},
                                          {"hist": "rev", "stat": "up", "years": 5, "op": "gte", "value": 4},
                                          {"hist": "om", "stat": "min", "years": 5, "op": "gte", "value": 10},
                                          {"hist": "shares", "stat": "cagr", "years": 5, "op": "lte", "value": 1}], "sort": "hist:0"},
}


def presets() -> dict[str, dict[str, Any]]:
    """Built-in presets plus your saved ones (saved ones win on a name clash)."""
    return {**PRESETS, **(store.kv_get("screens") or {})}


def save_preset(name: str, spec: dict[str, Any]) -> None:
    saved = store.kv_get("screens") or {}
    saved[name[:60]] = {"criteria": spec.get("criteria", []), "sort": spec.get("sort", "mcap"), "asc": bool(spec.get("asc")),
                        "mandate_off": spec.get("mandate_off", [])}
    store.kv_set("screens", saved)


def delete_preset(name: str) -> None:
    saved = store.kv_get("screens") or {}
    saved.pop(name, None)
    store.kv_set("screens", saved)


def _rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Mandate rows (unless switched off) merged with the screen's own rows; a screen row on the same field replaces the mandate row."""
    own = spec.get("criteria", [])
    own_fields = {c.get("field") for c in own if c.get("field")}
    off = set(spec.get("mandate_off", []))
    rows = [dict(m) for m in MANDATE if m["field"] not in own_fields and m["field"] not in off]
    return rows + [dict(c) for c in own]


def looser(spec: dict[str, Any]) -> list[str]:
    """Plain-language list of where this screen is looser than the mandate."""
    out = []
    own = {c.get("field"): c for c in spec.get("criteria", []) if c.get("field")}
    for m in MANDATE:
        f = m["field"]
        label = FIELDS[f][0]
        if f in set(spec.get("mandate_off", [])):
            out.append(f"{label} check switched off")
            continue
        c = own.get(f)
        if not c:
            continue
        v = c["value"][1] if c["op"] == "btwn" and m["op"] == "lte" else c["value"][0] if c["op"] == "btwn" else c["value"]
        if (m["op"] == "gte" and v < m["value"]) or (m["op"] == "lte" and v > m["value"]):
            out.append(f"{label} {('at least' if m['op'] == 'gte' else 'at most')} {_fmt(v, FIELDS[f][2])} (mandate {_fmt(m['value'], FIELDS[f][2])})")
    return out


def _fmt(v: float, unit: str) -> str:
    if unit == "usd":
        return f"${v / 1e9:,.1f}B" if v >= 1e9 else f"${v / 1e6:,.0f}M"
    if unit == "pct":
        return f"{v:g}%"
    return f"{v:g}x" if unit == "x" else f"{v:g}"


PAGE, MAX_MATCHES = 250, 3000
MATCH_TTL = 600
_matches: dict[str, tuple[float, tuple[list[dict[str, Any]], int]]] = {}


def _screen(**kw: Any) -> dict[str, Any]:
    """Yahoo screen call. A 401 means its session token went stale: get a new one and try once more."""
    try:
        return yf.screen(**kw)
    except Exception as e:
        if "401" not in str(e):
            raise
        try:
            from yfinance.data import YfData
            d = YfData()
            d._crumb = d._cookie = None
        except Exception:
            pass  # yfinance internals moved: the plain retry may still work
        time.sleep(2)
        return yf.screen(**kw)


def _all_matches(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Every Yahoo match (paged), so a multi-year filter sees the whole list, not the first 250."""
    # Changing a multi-year row (years, CAGR, metric) doesn't change what Yahoo is asked, so reuse its list.
    key = json.dumps([[c for c in _rows(spec) if not c.get("hist")], spec.get("us_listed", True)], sort_keys=True)
    hit = _matches.get(key)
    if hit and time.time() - hit[0] < MATCH_TTL:
        return hit[1]
    quotes: dict[str, dict[str, Any]] = {}
    q, total, offset = query(spec), 0, 0
    while offset < MAX_MATCHES:
        r = _screen(query=q, size=PAGE, offset=offset, sortField="intradaymarketcap", sortAsc=False)
        got = r.get("quotes") or []
        total = r.get("total") or total
        for x in got:
            quotes[x["symbol"]] = x
        offset += PAGE
        if len(got) < PAGE or offset >= total:
            break
    if len(_matches) > 20:
        _matches.clear()
    _matches[key] = (time.time(), (list(quotes.values()), total))
    return _matches[key][1]


def apply_history(rows: list[dict[str, Any]], hist: list[dict[str, Any]], table: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep rows that pass every multi-year criterion. A name with no usable history is dropped and counted."""
    kept, no_data, failed = [], 0, 0
    for x in rows:
        vals = [history.value(x["ticker"], c["hist"], c["stat"], c["years"], table) for c in hist]
        if any(v is None for v in vals):
            no_data += 1
        elif all(history.passes(v["v"], c["op"], c["value"]) for v, c in zip(vals, hist)):
            kept.append({**x, "hist": vals})
        else:
            failed += 1
    return kept, {"no_data": no_data, "failed": failed, "checked": len(rows)}


def query(spec: dict[str, Any]) -> Any:
    Q = yf.EquityQuery
    parts = [Q("eq", ["region", "us"]), Q("is-in", ["exchange", *US_LISTED])] if spec.get("us_listed", True) else [Q("eq", ["region", "us"])]
    for c in _rows(spec):
        if c.get("sector"):
            parts.append(Q("is-in", ["sector", *c["sector"]]) if len(c["sector"]) > 1 else Q("eq", ["sector", c["sector"][0]]))
            continue
        f = FIELDS.get(c.get("field", ""))
        if not f:
            continue
        op = c["op"]
        if op == "btwn":
            parts.append(Q("btwn", [f[1], float(c["value"][0]), float(c["value"][1])]))
        elif op in ("gte", "lte", "gt", "lt"):
            parts.append(Q(op, [f[1], float(c["value"])]))
    return Q("and", parts)


def _info_metrics(t: str) -> dict[str, Any]:
    """The screened ratios for one result row, from Yahoo's profile (the screener doesn't return them)."""
    try:
        i = equity.info(t)
    except Exception:
        return {}
    ebitda, debt, cash = i.get("ebitda"), i.get("totalDebt"), i.get("totalCash")
    nd = ((debt or 0) - (cash or 0)) / ebitda if ebitda and ebitda > 0 and debt is not None else None
    return {"sector": i.get("sector"), "industry": i.get("industry"), "beta": i.get("beta"), "nd_ebitda": nd,
            "roe": (i.get("returnOnEquity") or 0) * 100 if i.get("returnOnEquity") is not None else None,
            "cur": i.get("currentRatio"), "gm": (i.get("grossMargins") or 0) * 100 if i.get("grossMargins") is not None else None,
            "ebitdam": (i.get("ebitdaMargins") or 0) * 100 if i.get("ebitdaMargins") is not None else None,
            "revg": (i.get("revenueGrowth") or 0) * 100 if i.get("revenueGrowth") is not None else None,
            "ev_ebitda": i.get("enterpriseToEbitda"), "summary": (i.get("longBusinessSummary") or "")[:240]}


def run(spec: dict[str, Any], coverage: Callable[[str], dict[str, Any]], show: int = 40) -> dict[str, Any]:
    try:
        hist = [history.clean_row(c) for c in spec.get("criteria", []) if c.get("hist")]
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    table = history.load() if hist else None
    if hist and not table:
        st = history.status()
        return {"ok": False, "error": "The multi-year table is still being built from SEC filings "
                f"({st['done']} of {st['of'] or '?'} requests; about 10 minutes the first time)."
                + (f" Last error: {st['error']}" if st.get("error") else "")}
    sort_key = str(spec.get("sort", "mcap"))
    sort = FIELDS.get(sort_key, FIELDS["mcap"])[1]
    source = "yahoo"
    try:
        if hist:
            found, total = _all_matches(spec)
        else:
            r = _screen(query=query(spec), size=250, sortField=sort, sortAsc=bool(spec.get("asc")))
            found = r.get("quotes") or []
            total = r.get("total") or len(found)
    except Exception as e:
        # Yahoo refuses its screener from cloud hosts: run the same criteria on Keel's own SEC + price table.
        from . import fallback
        try:
            found, total = fallback.screen([c for c in _rows(spec) if not c.get("hist")], sort_key, bool(spec.get("asc")))
            source = "sec"
        except fallback.NotReady as nr:
            return {"ok": False, "error": f"Yahoo's screener isn't reachable from this server, so Keel is building its own table from SEC "
                    f"filings and price data ({nr}). The first build after a restart takes about 5-10 minutes; try again shortly."}
        except Exception as e2:
            return {"ok": False, "error": f"Yahoo screener failed ({str(e)[:100]}) and the SEC-based screen failed too ({str(e2)[:100]})."}
    quotes = [q for q in found if q.get("quoteType") == "EQUITY"]
    rows = []
    for q in quotes:
        last, hi = q.get("regularMarketPrice"), q.get("fiftyTwoWeekHigh")
        rows.append({"ticker": q["symbol"], "name": q.get("longName") or q.get("shortName"), "exchange": q.get("exchange"),
                     "last": last, "chg_pct": (q.get("regularMarketChangePercent") or 0) / 100 if q.get("regularMarketChangePercent") is not None else None,
                     "mcap": q.get("marketCap"), "pe": q.get("trailingPE"), "fwd_pe": q.get("forwardPE"), "pb": q.get("priceToBook"),
                     "chg52": (q.get("fiftyTwoWeekChangePercent") or 0) / 100 if q.get("fiftyTwoWeekChangePercent") is not None else None,
                     "from_high": (last / hi - 1) if (last and hi) else None, "lo52": q.get("fiftyTwoWeekLow"), "hi52": hi,
                     "divy": q.get("dividendYield"), "_fb": q.get("_fb")})
    hist_info = None
    if hist:
        rows, hist_info = apply_history(rows, hist, table)
        hist_info.update(yahoo_total=total, built=table["built"])
        total = len(rows)
        if sort_key.startswith("hist:"):
            i = min(int(sort_key[5:]) if sort_key[5:].isdigit() else 0, len(hist) - 1)
            rows.sort(key=lambda x: x["hist"][i]["v"], reverse=not spec.get("asc"))
        elif sort_key in ("mcap", "price", "pe", "pb", "chg52", "divy"):  # Yahoo returns these per row
            k = {"price": "last"}.get(sort_key, sort_key)
            have = sorted((x for x in rows if x.get(k) is not None), key=lambda x: x[k], reverse=not spec.get("asc"))
            rows = have + [x for x in rows if x.get(k) is None]
        else:
            hist_info["sort_note"] = f"{FIELDS[sort_key][0] if sort_key in FIELDS else sort_key} can't be sorted on alongside multi-year rows; sorted by market cap."
    for x in rows:
        x["coverage"] = (coverage(x["ticker"]) or {}).get("status", "none")
    # New since the last run of this preset
    key = "screen_last:" + spec.get("name", "adhoc")
    prev = set(store.kv_get(key) or [])
    now_set = {x["ticker"] for x in rows}
    store.kv_set(key, sorted(now_set))
    entered, left = sorted(now_set - prev) if prev else [], sorted(prev - now_set) if prev else []
    shown = rows if spec.get("show_all") else [x for x in rows if x["coverage"] in ("none", "tracked")] or rows
    shown = shown[:show]
    # Enrich the rows on screen: Yahoo profile ratios (in parallel) and Robinhood's market cap for a cross-check
    with ThreadPoolExecutor(max_workers=8) as ex:
        for x, m in zip(shown, ex.map(lambda r_: r_.get("_fb") or _info_metrics(r_["ticker"]), shown)):
            x.update({k: v for k, v in m.items() if v is not None})
    for x in rows:
        x.pop("_fb", None)
    try:
        rh: dict[str, Any] = {}
        syms = [x["ticker"] for x in shown]
        for i in range(0, len(syms), 20):
            res = rh_mcp.call("get_equity_fundamentals", {"symbols": syms[i:i + 20]})
            for f in (res or {}).get("results", []) or []:
                if f and f.get("symbol"):
                    rh[f["symbol"]] = f
        for x in shown:
            f = rh.get(x["ticker"])
            if not f:
                continue
            rmc = float(f["market_cap"]) if f.get("market_cap") else None
            x["mcap_rh"] = rmc
            if rmc and x.get("mcap") and abs(x["mcap"] / rmc - 1) > 0.10:
                x["conflict"] = f"Market cap: Yahoo {_fmt(x['mcap'], 'usd')} vs Robinhood {_fmt(rmc, 'usd')}"
        rh_ok = True
    except Exception as e:  # Robinhood is a cross-check; the screen still shows without it
        rh_ok = False
        for x in shown:
            x.setdefault("note", "")
        rh_err = str(e)[:120]
    out = {"ok": True, "total": total, "returned": len(rows), "shown": shown, "entered": entered, "left": left,
           "counts": {k: sum(1 for x in rows if x["coverage"] == k) for k in ("none", "tracked", "held", "watchlist", "passed", "closed-positions", "holdings")},
           "hist": [{**c, "label": history.label(c), "unit": history.STATS[c["stat"]][2], "kind": history.METRICS[c["hist"]][1]} for c in hist],
           "hist_info": hist_info,
           "criteria": [{**c, "label": history.label(history.clean_row(c)) if c.get("hist") else FIELDS[c["field"]][0] if c.get("field") in FIELDS else "Sector", "unit": history.STATS[c["stat"]][2] if c.get("hist") else FIELDS[c["field"]][2] if c.get("field") in FIELDS else ""}
                        for c in _rows(spec)],
           "looser": looser(spec), "robinhood_checked": rh_ok, "source": source}
    if source == "sec":
        out["source_note"] = ("Yahoo's screener isn't available on this server, so this ran on Keel's own table: the S&P 1500 plus "
                              "US-listed foreign companies; ratios from each company's latest annual SEC report (foreign figures "
                              "converted to dollars), prices and market caps from Yahoo chart data.")
    if not rh_ok:
        out["robinhood_error"] = rh_err
    return out


def verify(t: str, sec_statements: dict[str, Any]) -> dict[str, Any]:
    """Compare Yahoo's latest figures with the company's own SEC filing for one finalist."""
    if not sec_statements.get("ok"):
        return {"ok": False, "error": sec_statements.get("error")}
    i = equity.info(t)
    rows = {r["key"]: r for s in sec_statements["statements"].values() for r in s}
    q_ends = sec_statements["periods"]["Q"]

    def latest(key: str, ttm: bool) -> tuple[float | None, str | None]:
        r = rows.get(key)
        if not r:
            return None, None
        if ttm:
            vals = [r["Q"].get(e) for e in q_ends[-4:]]
            return (sum(vals), q_ends[-1]) if len(q_ends) >= 4 and all(v is not None for v in vals) else (None, None)
        e = next((e for e in reversed(q_ends) if r["Q"].get(e) is not None), None)
        return (r["Q"].get(e), e) if e else (None, None)

    checks = []
    for label, key, ttm, yv in [("Revenue (TTM)", "rev", True, i.get("totalRevenue")), ("Total debt", "debt", False, i.get("totalDebt")),
                                ("Cash", "cash", False, i.get("totalCash")), ("Net income (TTM)", "ni", True, i.get("netIncomeToCommon"))]:
        sv, e = latest(key, ttm)
        diff = (yv / sv - 1) if (yv and sv) else None
        checks.append({"label": label, "sec": sv, "yahoo": yv, "as_of": e, "diff": diff,
                       "ok": None if diff is None else abs(diff) <= 0.05})
    return {"ok": True, "ticker": t, "checks": checks,
            "verdict": "match" if all(c["ok"] for c in checks if c["ok"] is not None) else "differences found"}
