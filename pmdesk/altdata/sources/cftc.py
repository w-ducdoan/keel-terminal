"""CFTC Commitments of Traders (free, no key): how speculators (non-commercial traders) are positioned in futures,
weekly, as net long % of open interest. Context for gold (IAU), equities (index funds), Treasuries, crude and the
dollar; managed-futures funds (DBMF) ride these trends. Shown as context, not tested against revenue."""
from __future__ import annotations

from typing import Any

import pandas as pd

from . import get, has

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"  # legacy futures-only report
MARKETS = {
    "gold": ("088691", "Gold futures: speculators' net position, % of open interest", "Crowded gold longs can unwind fast; a washed-out position is a cleaner entry."),
    "sp500": ("13874A", "S&P 500 e-mini futures: speculators' net position, % of open interest", "Shows how leaned-in fast money is on US stocks."),
    "ust10y": ("043602", "10-year Treasury note futures: speculators' net position, % of open interest", "Positioning in rates: crowded shorts squeeze when yields fall."),
    "crude": ("067651", "WTI crude futures: speculators' net position, % of open interest", "Oil positioning: extremes often precede reversals."),
    "dollar": ("098662", "US dollar index futures: speculators' net position, % of open interest", "Dollar positioning matters for foreign earnings and emerging markets."),
}


def candidates(p: dict[str, Any]) -> list[dict[str, Any]]:
    text = " ".join([p.get("name", ""), p.get("summary", ""), p.get("industry", ""), p.get("sector", "")])
    keys: list[str] = []
    if has(text, "gold", "precious metal", "pawn"):
        keys.append("gold")
    if has(text, "managed futures", "trend following", "trend-following", "diversified futures"):
        keys += list(MARKETS)
    if p.get("quote_type") in ("ETF", "MUTUALFUND") and has(text, "s&p 500", "total stock market", "emerging market", "equity index"):
        keys.append("sp500")
    if has(text, "treasury", "government bond", "tips"):
        keys.append("ust10y")
    if has(p.get("sector", ""), "energy") or has(text, "crude oil"):
        keys.append("crude")
    return [{"id": f"cftc:{k}", "source": "CFTC", "title": MARKETS[k][1], "unit": "% of OI", "freq": "W", "agg": "mean", "growth": "diff",
             "kind": "context", "code": MARKETS[k][0], "reason": MARKETS[k][2]} for k in dict.fromkeys(keys)]


def fetch(spec: dict[str, Any]) -> pd.Series:
    r = get(API, {"$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all",
                  "$where": f"cftc_contract_market_code='{spec['code']}'", "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000})
    out = {}
    for x in r.json() if r.status_code == 200 else []:
        try:
            oi = float(x["open_interest_all"])
            out[pd.Timestamp(x["report_date_as_yyyy_mm_dd"][:10])] = (float(x["noncomm_positions_long_all"]) - float(x["noncomm_positions_short_all"])) / oi * 100
        except (KeyError, ValueError, ZeroDivisionError):
            continue
    s = pd.Series(out, dtype=float).sort_index()
    return s[s.index >= s.index.max() - pd.DateOffset(years=8)] if not s.empty else s
