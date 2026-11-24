"""Short interest, crowding and liquidity for the book (and the pipeline on request).

Short interest comes from exchange reports published twice a month (Yahoo's copy), so it is about two weeks old:
a warning sign, not a live borrow feed. Borrow cost is not available free and is not shown.
Liquidity: days to exit = position value / (share of the stock's average daily dollar volume you would trade).
At today's book size every name exits in minutes, so the useful number is the fund size at which a position
stops being sellable in a day. Rule-based; no AI.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import equity

PARTICIPATION = 0.20          # share of daily volume you could trade without moving the price much
SCALES = [1e6, 1e7, 1e8]      # fund sizes to test liquidity at


def flags(short_pct: float | None, days_cover: float | None, short_chg: float | None, inst: float | None) -> list[dict[str, str]]:
    out = []
    if short_pct is not None:
        if short_pct >= 0.20:
            out.append({"flag": f"Heavily shorted ({short_pct:.0%} of float)", "severity": "high"})
        elif short_pct >= 0.10:
            out.append({"flag": f"Elevated short interest ({short_pct:.0%} of float)", "severity": "medium"})
    if days_cover is not None and days_cover >= 7:
        out.append({"flag": f"{days_cover:.1f} days to cover: a squeeze or a slow unwind either way", "severity": "medium"})
    if short_chg is not None and short_pct is not None and short_pct >= 0.03:
        if short_chg >= 0.20:
            out.append({"flag": f"Shorts adding ({short_chg:+.0%} in a month)", "severity": "medium"})
        elif short_chg <= -0.20:
            out.append({"flag": f"Shorts covering ({short_chg:+.0%} in a month)", "severity": "low"})
    # Institutional ownership alone is not a crowding signal: 95%+ is normal for mid caps and 13F double counting
    # pushes many past 100%. Hedge-fund concentration (from 13F filings) is the real test; inst % is shown, not flagged.
    return out


def liquidity(value: float, nav: float, price: float | None, adv_shares: float | None) -> dict[str, Any]:
    if not price or not adv_shares or not nav:
        return {"adv_usd": None}
    adv = price * adv_shares
    w = value / nav if nav else 0.0
    cap = PARTICIPATION * adv / w if w > 0 else None   # fund size at which this weight takes one day to exit
    return {"adv_usd": adv, "days_now": value / (PARTICIPATION * adv) if adv else None,
            "days_at": {str(int(s)): (w * s) / (PARTICIPATION * adv) for s in SCALES} if w > 0 else {},
            "one_day_fund_size": cap}


def _row(t: str, own: str, value: float, nav: float) -> dict[str, Any]:
    try:
        i = equity.info(t) or {}
    except Exception:
        i = {}
    if equity._is_fund(i):
        return {"ticker": t, "own": own, "fund": True, **liquidity(value, nav, i.get("regularMarketPrice") or i.get("previousClose"),
                                                                  i.get("averageDailyVolume3Month") or i.get("averageVolume"))}
    sp, prior, now = i.get("shortPercentOfFloat"), i.get("sharesShortPriorMonth"), i.get("sharesShort")
    chg = (now / prior - 1) if now and prior else None
    d = i.get("dateShortInterest")
    out = {"ticker": t, "own": own, "fund": False, "short_pct_float": sp, "short_shares": now, "short_change": chg,
           "days_to_cover": i.get("shortRatio"), "short_as_of": datetime.fromtimestamp(d, tz=timezone.utc).date().isoformat() if isinstance(d, (int, float)) else None,
           "inst_pct": i.get("heldPercentInstitutions"), "insider_pct": i.get("heldPercentInsiders"),
           **liquidity(value, nav, i.get("regularMarketPrice") or i.get("currentPrice") or i.get("previousClose"),
                       i.get("averageDailyVolume3Month") or i.get("averageVolume"))}
    out["flags"] = flags(sp, out["days_to_cover"], chg, out["inst_pct"])
    return out


def build(snap: dict[str, Any], include_watch: bool = False) -> dict[str, Any]:
    nav = (snap.get("account") or {}).get("nav") or 0.0
    names = [(p["ticker"], "held", float(p.get("value") or 0.0)) for p in snap.get("positions", [])]
    if include_watch:
        held = {t for t, _, _ in names}
        names += [(w["ticker"], "watch", 0.0) for w in snap.get("watchlist", []) if w["ticker"] not in held]
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(lambda x: _row(x[0], x[1], x[2], nav), names))
    rows.sort(key=lambda r: (r.get("fund", False), -(r.get("short_pct_float") or 0)))
    held_rows = [r for r in rows if r["own"] == "held" and not r.get("fund")]
    tight = [r for r in rows if r["own"] == "held" and r.get("one_day_fund_size")]
    return {"ok": True, "rows": rows, "nav": nav, "participation": PARTICIPATION, "scales": SCALES,
            "flagged": sum(1 for r in held_rows if r.get("flags")),
            "book_one_day_size": min((r["one_day_fund_size"] for r in tight), default=None),
            "binding_name": min(tight, key=lambda r: r["one_day_fund_size"])["ticker"] if tight else None,
            "note": "Short interest is exchange-reported twice a month (about two weeks old). Borrow cost is not available free."}
