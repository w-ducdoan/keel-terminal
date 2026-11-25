"""FastAPI app: JSON API plus the static front end. Binds to localhost only."""
from __future__ import annotations

from datetime import date

import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import newsrange, public
from . import attribution, brief, catalysts, cockpit, crowding, events_feed, funds13f, heatmap, lookup2, equity, ern, history, journal, market, news, performance, reconcile, screener, sec, sec_events, security, splc, themes, series, store, whatif
from .config import ROOT, load_config
from .engine import Engine
from .terminal import Markets

WEB = ROOT / "web"


def clean_json(x: Any) -> Any:
    """Make numpy types and NaN/inf JSON-safe."""
    if isinstance(x, dict):
        return {str(k): clean_json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean_json(v) for v in x]
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def create_app(start_loop: bool = True) -> FastAPI:
    cfg = load_config()
    pub = public.enabled()
    YAHOO_ONLY = {"anr": "Analyst ratings", "ee": "Earnings estimates", "hds": "Holder lists", "ins": "Insider transactions"}
    engine = public.PublicEngine(cfg) if pub else Engine(cfg)
    app = FastAPI(title="Keel", docs_url=None, redoc_url=None)
    app.state.engine = engine

    if pub:  # public edition: market routes only, no writes, nothing tied to an account or a disk
        @app.middleware("http")
        async def public_only(request: Request, call_next):  # type: ignore[no-untyped-def]
            if not public.allowed(request.method, request.url.path):
                return JSONResponse({"ok": False, "error": "Not available in the public edition."}, status_code=404)
            return await call_next(request)

    if start_loop:
        threading.Thread(target=engine.run_forever, daemon=True).start()

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse(clean_json(engine.snapshot()))

    @app.post("/api/refresh")
    def refresh(wait: bool = False) -> dict[str, Any]:
        """wait=true runs a full refresh now (Robinhood + Yahoo, about 6 s) and returns when done."""
        if not wait:
            engine.poke()
            return {"ok": True}
        t0 = time.time()
        engine.refresh()
        return clean_json({"ok": True, "seconds": round(time.time() - t0, 2), "as_of": engine.snapshot().get("as_of"),
                           "source": engine.snapshot().get("source"), "changes": engine.last_changes})

    @app.post("/api/changes/seen")
    def changes_seen() -> dict[str, Any]:
        """Clear the NEW marks on the POSITION CHANGES panel."""
        store.mark_changes_seen()
        engine.poke()
        return {"ok": True}

    @app.post("/api/actions/{action_id}/ack")
    def ack(action_id: str, state: str = "ack", hours: float = 24) -> dict[str, Any]:
        if state not in ("ack", "snoozed", "open"):
            raise HTTPException(400, "state must be ack, snoozed or open")
        engine.ack(action_id, state, 0 if state == "open" else hours)
        return {"ok": True}

    @app.get("/api/series")
    def get_series(symbol: str, range: str = "1Y") -> JSONResponse:  # noqa: A002 - query name
        """Chart data. symbol = a ticker, BOOK, or FRED:<label from settings.yaml>."""
        rng = range.upper()
        if rng not in series.RANGES:
            raise HTTPException(400, "range must be one of " + ", ".join(series.RANGES))
        try:
            if symbol == "BOOK" and rng != "1D":
                # Actual book, flow-adjusted: today's NAV scaled back by the time-weighted return.
                out = series.book_twr_series(perf_data(), (engine.snapshot().get("account") or {}).get("nav"), rng)
            elif symbol == "BOOK":
                snap = engine.snapshot()
                out = series.book_series(snap.get("positions", []), snap.get("account", {}).get("cash") or 0, rng)
            elif symbol.startswith("FRED:"):
                label = symbol[5:]
                spec = next((s for s in cfg.settings.get("fred", []) if s["label"] == label), None)
                if not spec:
                    raise HTTPException(404, "Unknown FRED label")
                out = series.fred_series(label, spec["series"], spec.get("scale", 1), rng)
            elif symbol.startswith("FREDID:"):
                # Any FRED series by id, e.g. FREDID:DGS10, FREDID:BAMLH0A0HYM2*100, FREDID:CPIAUCSL~yoy
                body = symbol[7:]
                head, _, sc = body.partition("*")
                sid, _, tf = head.partition("~")
                if (not re.fullmatch(r"[A-Z0-9_]{2,40}", sid) or tf not in ("", "level", "yoy", "mom_diff", "mom_pct")
                        or not re.fullmatch(r"[0-9.]*", sc)):
                    raise HTTPException(400, "Bad FRED id")
                out = series.fred_series(body, sid, float(sc or 1), rng, transform_name=tf or "level")
            elif symbol.split(":")[0] in ("SI", "SIP", "DTC"):
                kind, t = symbol.split(":", 1)
                if not equity.TICKER.match(t.upper()):
                    raise HTTPException(400, "Not a ticker")
                fl = (equity.info(t.upper()) or {}).get("floatShares") if kind == "SIP" else None
                s = lookup2.short_series(t.upper(), {"SI": "short", "SIP": "pct", "DTC": "dtc"}[kind], fl)
                if not s.empty and rng not in ("MAX",):
                    days = series.FRED_DAYS.get(rng, 366)
                    s = s[s.index >= s.index[-1] - pd.Timedelta(days=max(days, 45))]  # at least a few reports
                note = {"SI": "Shares short, FINRA (exchange reports twice a month).",
                        "SIP": "Short interest as % of float: FINRA shares short / today's float (Yahoo), so older points assume today's float.",
                        "DTC": "Days to cover: shares short / average daily volume, FINRA."}[kind]
                out = series._summary(symbol, rng, "semi-monthly", s, note)
            else:
                out = series.ticker_series(symbol.upper(), rng)
        except HTTPException:
            raise
        except Exception as e:  # network or parsing failure: say so in the chart
            out = {"symbol": symbol, "range": rng, "points": [], "note": f"Data source failed: {e}"}
        return JSONResponse(clean_json(out))

    mkts = Markets(cfg.settings)

    def mkts_snapshot(fresh: bool = False) -> dict[str, Any]:
        snap = engine.snapshot()
        held = [p["ticker"] for p in snap.get("positions", [])]
        watch = [w["ticker"] for w in snap.get("watchlist", [])]
        return mkts.snapshot(snap.get("session") or "closed", held, watch, fresh=fresh)

    if start_loop:  # warm the MKTS cache so the first visit is fast
        threading.Thread(target=lambda: (time.sleep(20), mkts_snapshot()), daemon=True).start()

    @app.get("/api/markets")
    def get_markets(fresh: bool = False) -> JSONResponse:
        """MKTS page: quotes by region and asset class, curve, money markets, spreads, economy, news."""
        return JSONResponse(clean_json(mkts_snapshot(fresh)))

    def _cov(t: str) -> dict[str, Any]:
        snap = engine.snapshot()
        out = equity.coverage_status(cfg.hq, t, {p["ticker"] for p in snap.get("positions", [])},
                                     {w["ticker"] for w in snap.get("watchlist", [])})
        if out.get("status") == "none" and any(x["ticker"] == t for x in store.tracked()):
            out = {**out, "status": "tracked"}  # on Keel's tracking list, not yet in the research folder coverage
        return out

    # ---------- tracking list (Keel only; separate from the research folder's watchlist.md) ----------
    @app.get("/api/tracking")
    def get_tracking() -> JSONResponse:
        rows = store.tracked()
        snap = engine.snapshot()
        spy_now = (snap.get("benchmark") or {}).get("last")
        earn = {e["ticker"]: e for e in snap.get("earnings_upcoming", [])}
        out = []
        for r in rows:
            try:
                c = equity.card(r["ticker"], None)
            except Exception:
                c = {}
            last = c.get("last")
            r = dict(r)
            r.update(last=last, chg_pct=c.get("chg_pct"), lo52=c.get("lo52"), hi52=c.get("hi52"),
                     market_cap=c.get("market_cap"), fwd_pe=c.get("fwd_pe"), earnings=(earn.get(r["ticker"]) or {}).get("date"),
                     since=(last / r["price"] - 1) if (last and r.get("price")) else None,
                     spy_since=(spy_now / r["spy"] - 1) if (spy_now and r.get("spy")) else None)
            r["vs_spy"] = (r["since"] - r["spy_since"]) if (r["since"] is not None and r["spy_since"] is not None) else None
            out.append(r)
        themes = sorted({x["theme"] for x in rows if x.get("theme")} | set((snap.get("aggregates") or {}).get("bucket", {}).keys()) - {"Unassigned", ""})
        return JSONResponse(clean_json({"ok": True, "items": out, "themes": themes}))

    @app.post("/api/tracking")
    def add_tracking(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        t = str(payload.get("ticker", "")).upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        c = equity.card(t, None)
        if not c.get("ok"):
            raise HTTPException(400, c.get("error") or "No data for that ticker")
        spy = (engine.snapshot().get("benchmark") or {}).get("last")
        prov = equity.provisional_class(t)
        store.track(t, c.get("last"), spy, str(payload.get("theme") or "Unsorted")[:60], prov["sector"],
                    c.get("name") or prov["company"], str(payload.get("note") or "")[:400])
        return {"ok": True}

    @app.post("/api/tracking/{ticker}/status")
    def tracking_status(ticker: str, status: str) -> dict[str, Any]:
        """tracking | gate0 (queued for Gate 0 in chat) | removed"""
        if status not in ("tracking", "gate0", "removed"):
            raise HTTPException(400, "status must be tracking, gate0 or removed")
        store.set_tracked_status(ticker.upper(), status)
        return {"ok": True}

    @app.get("/api/lookup/more")
    def lookup_more(q: str) -> JSONResponse:
        """Command bar, beyond tickers: FRED economic series and indexes seen through the funds that track them."""
        try:
            return JSONResponse(clean_json({"ok": True, "eco": lookup2.eco_search(q), "proxies": lookup2.index_proxies(q)}))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:200], "eco": [], "proxies": []})

    @app.get("/api/lookup")
    def get_lookup(q: str) -> JSONResponse:
        """Command bar: search Yahoo by company name or ticker."""
        try:
            return JSONResponse(clean_json({"ok": True, "results": equity.lookup(q)}))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:200], "results": []})

    @app.get("/api/equity/{ticker}/card")
    def get_card(ticker: str) -> JSONResponse:
        """Ticker pop-up and command-bar preview: name, price, valuation, description, coverage."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        return JSONResponse(clean_json(equity.card(t, _cov(t))))

    @app.get("/api/news")
    def get_news(fresh: bool = False, days: int = 2) -> JSONResponse:
        """MKTS news wire: market-moving stories from RSS, Google News topics and Yahoo market symbols.
        days = how far back (1 to 30); RSS feeds only carry the last day or two, Google News goes back further."""
        return JSONResponse(clean_json(news.get(cfg.settings.get("news") or {}, engine.snapshot(), fresh, days)))

    def _name(t: str) -> str | None:
        try:
            i = equity.info(t) or {}
        except Exception:
            i = {}
        return i.get("longName") or i.get("shortName")

    @app.get("/api/cnrange/{ticker}")
    def get_cn_range(ticker: str, days: int = 30) -> JSONResponse:
        """CN with a chosen look-back: Google News by month plus Yahoo and digest mentions."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        days = max(1, min(days, 730))
        def build() -> dict[str, Any]:
            name = _name(t)
            try:
                recent = (equity.sec_cn(t, engine.snapshot().get("digest") or [], name) or {}).get("items") or []
            except Exception:
                recent = []
            return newsrange.company(t, name, days, recent)
        key = f"cnrange:{t}:{days}"
        out = equity.cached(key, 20 * 60, build)
        if not out.get("ok"):  # a failed search is never cached
            with equity._lock:
                equity._cache.pop(key, None)
        return JSONResponse(clean_json({**out, "ticker": t}))

    @app.get("/api/why/{ticker}")
    def get_why(ticker: str, start: str, end: str) -> JSONResponse:
        """Why did it move? The move between two chart points, the headlines around it and SEC filings that day."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        try:
            a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
        except ValueError:
            raise HTTPException(400, "Dates must be YYYY-MM-DD")
        key = f"why:{t}:{a}:{b}"
        out = equity.cached(key, 30 * 60, lambda: newsrange.why(t, _name(t), a, b))
        if out.get("errors"):  # partial answer: show it, but ask again next time
            with equity._lock:
                equity._cache.pop(key, None)
        return JSONResponse(clean_json(out))

    def splc_out(t: str, fresh: bool = False) -> dict[str, Any]:
        """Supply chain with your coverage tags on every name."""
        snap = engine.snapshot()
        held = {p["ticker"] for p in snap.get("positions", [])}
        watch = {w["ticker"] for w in snap.get("watchlist", [])}
        tracked = {x["ticker"] for x in store.tracked()}
        out = splc.build(t, fresh)
        for x in (out.get("links") or []) + (out.get("manual") or []):
            k = x.get("ticker")
            x["cov"] = "held" if k in held else "watch" if k in watch else "tracked" if k in tracked else None
        return out

    @app.get("/api/splc/{ticker}")
    def get_splc(ticker: str, fresh: bool = False) -> JSONResponse:
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        try:
            return JSONResponse(clean_json({**splc_out(t, fresh), "section": "splc"}))
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"SPLC failed: {str(e)[:200]}", "section": "splc"})

    @app.post("/api/splc/{ticker}/link")
    def add_splc_link(ticker: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        t = ticker.upper()
        other = str(payload.get("other", "")).strip()[:80]
        rel = str(payload.get("relation", ""))
        if not equity.TICKER.match(t) or not other or rel not in ("supplier", "customer", "competitor", "partner"):
            raise HTTPException(400, "Need a company and a relation (supplier, customer, competitor or partner)")
        pct = payload.get("pct")
        try:
            pct = float(pct) if pct not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "% of revenue must be a number")
        store.add_supply_link(t, other.upper() if equity.TICKER.match(other.upper()) else other, rel, pct, str(payload.get("note") or "")[:300])
        return {"ok": True}

    @app.delete("/api/splc/{ticker}/link")
    def del_splc_link(ticker: str, other: str, relation: str) -> dict[str, Any]:
        store.delete_supply_link(ticker.upper(), other, relation)
        return {"ok": True}

    @app.post("/api/rv/{ticker}/peers")
    def set_rv_peers(ticker: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Your peer list for the RV panel. {"peers": [...]} saves it; {"reset": true} goes back to the default."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        if payload.get("reset"):
            store.kv_del(f"rv_peers:{t}")
            return {"ok": True}
        peers = []
        for p in payload.get("peers") or []:
            p = str(p).strip().upper()
            if equity.TICKER.match(p) and p != t and p not in peers:
                peers.append(p)
        store.kv_set(f"rv_peers:{t}", peers[:25])
        return {"ok": True, "peers": peers[:25]}

    @app.get("/api/equity/{ticker}/{section}")
    def get_equity_section(ticker: str, section: str) -> JSONResponse:
        """DES Launchpad panels, one call each so a slow source never blocks the page."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        snap = engine.snapshot()
        held = any(p.get("ticker") == t for p in snap.get("positions", []))

        def robinhood() -> dict[str, Any]:  # filings, tax lots, realized trades, coverage notes
            return security.load(t, engine.broker, cfg.hq, held)

        loaders = {
            "des": lambda: equity.sec_des(t),
            "anr": lambda: equity.sec_anr(t),
            "cacs": lambda: equity.sec_cacs(t, (robinhood().get("filings") or [])),
            "rv": lambda: equity.sec_rv(t, cfg.settings.get("peers") or {}),
            "fa": lambda: equity.sec_fa(t),
            "ee": lambda: equity.sec_ee(t),
            "hds": lambda: equity.sec_hds(t),
            "ins": lambda: equity.sec_ins(t),
            "cn": lambda: equity.sec_cn(t, snap.get("digest") or [], (equity.info(t) or {}).get("longName")),
            "rh": robinhood,
            "cockpit": lambda: cockpit.load(cfg.hq, t),
            "flags": lambda: sec_events.red_flags(t),
            "splc": lambda: splc_out(t),
            "ern": lambda: ern.load(t, cfg.hq),
        }
        if section not in loaders:
            raise HTTPException(404, "Unknown section")
        try:
            out = loaders[section]()
        except Exception as e:  # one panel fails, the rest of the page still loads
            out = {"ok": False, "error": f"{section.upper()} failed: {str(e)[:200]}"}
        if pub and not out.get("ok") and section in YAHOO_ONLY:
            out = {**out, "error": f"{YAHOO_ONLY[section]} come from Yahoo, which doesn't serve this public site (it blocks cloud servers). "
                   "Run Keel on your own computer to see them."}
        return JSONResponse(clean_json({**out, "section": section, "ticker": t}))

    attr_cache: dict[str, tuple[float, Any]] = {}

    def spy_sector_weights() -> dict[str, float]:
        hit = store.kv_get("spy_sector_w")
        if hit and hit.get("day") == date.today().isoformat():
            return hit["w"]
        try:
            import yfinance as yf
            w = {k: float(v) for k, v in (yf.Ticker("SPY").funds_data.sector_weightings or {}).items()}
        except Exception:
            w = {}
        if w:
            store.kv_set("spy_sector_w", {"day": date.today().isoformat(), "w": w})
            return w
        return (hit or {}).get("w") or {}

    @app.get("/api/attribution")
    def get_attribution(period: str = "MTD", fresh: bool = False) -> JSONResponse:
        """PORT ATTRIBUTION: contribution by name, bucket and sector, and the gap to SPY split into allocation,
        selection and cash drag."""
        period = period.upper()
        if period not in attribution.PERIODS:
            raise HTTPException(400, f"period must be one of {', '.join(attribution.PERIODS)}")
        hit = attr_cache.get(period)
        if hit and not fresh and time.time() - hit[0] < 600:
            return JSONResponse(hit[1])
        snap = engine.snapshot()
        fills_fn = getattr(engine.broker, "fills_since", None)
        if not snap.get("ready") or not fills_fn or not getattr(engine.broker, "account_number", None):
            return JSONResponse({"ok": False, "error": "Needs the live Robinhood feed (fills) and a loaded book."})
        pos = {p["ticker"]: p for p in snap.get("positions", [])}

        def classify(t: str) -> tuple[str, str]:
            if t in pos:
                return (pos[t].get("bucket") or "Unassigned", pos[t].get("sector") or "Unclassified")
            return ("Sold in period", equity.provisional_class(t).get("sector") or "Unclassified")
        try:
            out = attribution.compute(period, snap.get("positions", []), perf_data().get("points") or [], fills_fn,
                                      performance._closes, spy_sector_weights(), classify)
        except Exception as e:
            out = {"ok": False, "error": f"Attribution failed: {str(e)[:200]}"}
        data = clean_json(out)
        if out.get("ok"):
            attr_cache[period] = (time.time(), data)
        return JSONResponse(data)

    crowd_cache: dict[bool, tuple[float, Any]] = {}

    @app.get("/api/crowding")
    def get_crowding(watch: bool = False, fresh: bool = False) -> JSONResponse:
        """PORT CROWDING & LIQUIDITY: short interest, days to cover, institutional ownership, days to exit."""
        hit = crowd_cache.get(watch)
        if hit and not fresh and time.time() - hit[0] < 3600:
            return JSONResponse(hit[1])
        snap = engine.snapshot()
        if not snap.get("ready"):
            return JSONResponse({"ok": False, "error": "Book still loading."})
        try:
            data = clean_json(crowding.build(snap, watch))
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Crowding failed: {str(e)[:200]}"})
        crowd_cache[watch] = (time.time(), data)
        return JSONResponse(data)

    @app.post("/api/tripwires/{ticker}")
    def set_tripwire(ticker: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Override any of add_below / review_at / trim_above (a number, or null for the thesis default)."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        cur = store.kv_get(f"trip:{t}") or {}
        for k in ("add_below", "review_at", "trim_above"):
            if k in payload:
                v = payload[k]
                if v in (None, ""):
                    cur.pop(k, None)
                else:
                    try:
                        cur[k] = float(v)
                    except (TypeError, ValueError):
                        raise HTTPException(400, f"{k} must be a price")
        if "note" in payload:
            cur["note"] = str(payload["note"] or "")[:200]
        if any(cur.get(k) is not None for k in ("add_below", "review_at", "trim_above")) or cur.get("note"):
            store.kv_set(f"trip:{t}", cur)
        else:
            store.kv_del(f"trip:{t}")
        engine.poke()
        return {"ok": True, "levels": cur}

    f13_cache: dict[str, tuple[float, Any]] = {}
    f13_lock = threading.Lock()

    @app.get("/api/13f")
    def get_13f(scope: str = "book", fresh: bool = False) -> JSONResponse:
        """13F tracker: notable funds' holdings and last-quarter changes in your names, plus their new buys elsewhere.
        scope: book (held) | all (held, watchlist and tracked)."""
        scope = "all" if scope == "all" else "book"
        with f13_lock:  # one build at a time: the first one reads ~30 filings from SEC
            hit = f13_cache.get(scope)
            if hit and not fresh and time.time() - hit[0] < 12 * 3600:
                return JSONResponse(hit[1])
            snap = engine.snapshot()
            if not snap.get("ready"):
                return JSONResponse({"ok": False, "error": "Book still loading."})
            tick = [p["ticker"] for p in snap.get("positions", [])]
            if scope == "all":
                tick += [w["ticker"] for w in snap.get("watchlist", [])] + [x["ticker"] for x in store.tracked()]
            names = {}
            for t in dict.fromkeys(tick):
                try:
                    c = sec.cik(t)
                    names[t] = (sec_events._submissions(c).get("name") if c else None) or (equity.info(t) or {}).get("longName") or t
                except Exception:
                    names[t] = t
            try:
                data = clean_json(funds13f.build(names, funds13f.fund_list(cfg.settings.get("funds13f"))))
            except sec.NoContact as e:
                return JSONResponse({"ok": False, "error": str(e)})
            except Exception as e:
                return JSONResponse({"ok": False, "error": f"13F failed: {str(e)[:200]}"})
            f13_cache[scope] = (time.time(), data)
            return JSONResponse(data)

    @app.get("/api/13f/funds/search")
    def f13_search(q: str) -> JSONResponse:
        """Find a fund on SEC by name (entities that file 13Fs first)."""
        if len(q.strip()) < 2:
            return JSONResponse({"ok": True, "results": []})
        return JSONResponse(clean_json({"ok": True, "results": funds13f.search_funds(q.strip())}))

    @app.post("/api/13f/funds")
    def f13_add(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Add a fund {name, cik}, remove one {remove: cik or name}, or {reset: true} back to settings.yaml."""
        if payload.get("reset"):
            store.kv_del("f13_funds")
        elif payload.get("remove"):
            funds13f.remove_fund(cfg.settings.get("funds13f"), str(payload["remove"]))
        else:
            try:
                cik = int(payload.get("cik"))
            except (TypeError, ValueError):
                raise HTTPException(400, "Need the fund's CIK (pick it from the search)")
            funds13f.add_fund(cfg.settings.get("funds13f"), str(payload.get("name") or cik), cik)
        f13_cache.clear()
        return {"ok": True, "funds": funds13f.fund_list(cfg.settings.get("funds13f"))}

    @app.get("/api/13f/fund/{cik}")
    def f13_fund(cik: int, name: str | None = None) -> JSONResponse:
        """One fund's full 13F with changes since the prior quarter."""
        try:
            return JSONResponse(clean_json(funds13f.fund_detail(cik, name)))
        except sec.NoContact as e:
            return JSONResponse({"ok": False, "error": str(e)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"13F failed: {str(e)[:200]}"})

    map_lock = threading.Lock()

    @app.get("/api/heatmap")
    def get_heatmap(universe: str = "sp500", period: str = "1D") -> JSONResponse:
        """MAP: S&P 500 (or your book, or the watchlist) as a treemap: area = size, color = move over the period."""
        with map_lock:  # the first long-period build downloads a year of closes for 500 names
            try:
                return JSONResponse(clean_json(heatmap.build(universe if universe in ("sp500", "book", "watch") and not pub else "sp500",
                                                              period.upper(), engine.snapshot())))
            except Exception as e:
                return JSONResponse({"ok": False, "error": f"Heat map failed: {str(e)[:200]}"})

    evt_cache: dict[str, tuple[float, Any]] = {}
    evt_lock = threading.Lock()

    @app.get("/api/eventfeed")
    def get_eventfeed(days: int = 30, fresh: bool = False) -> JSONResponse:
        """EVT: spin-offs, tender offers, merger votes, going-private deals and new activist stakes, market-wide."""
        with evt_lock:
            hit = evt_cache.get("v")
            if hit and not fresh and time.time() - hit[0] < 3 * 3600:
                return JSONResponse(hit[1])
            snap = engine.snapshot()
            cov = {x["ticker"]: "tracked" for x in store.tracked()}
            cov.update({w["ticker"]: "watch" for w in snap.get("watchlist", [])})
            cov.update({p["ticker"]: "held" for p in snap.get("positions", [])})
            try:
                data = clean_json(events_feed.build(cov, max(7, min(days, 90))))
            except sec.NoContact as e:
                return JSONResponse({"ok": False, "error": str(e)})
            except Exception as e:
                return JSONResponse({"ok": False, "error": f"Event feed failed: {str(e)[:200]}"})
            if not data.get("failed"):
                evt_cache["v"] = (time.time(), data)
            return JSONResponse(data)

    @app.get("/api/calendar")
    def get_calendar(days: int = 60, fresh: bool = False) -> JSONResponse:
        """CAL: earnings, dividends, lockups, market dates and macro releases for the book and pipeline."""
        snap = engine.snapshot()
        if not snap.get("ready"):
            return JSONResponse({"ok": False, "error": "Book still loading."})
        try:
            return JSONResponse(clean_json(catalysts.build(snap, [x["ticker"] for x in store.tracked()], max(7, min(days, 180)), fresh)))
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Calendar failed: {str(e)[:200]}"})

    @app.get("/api/themes")
    def get_themes() -> JSONResponse:
        """PORT THEMES: each exposure bucket against its drivers (FRED, alt-data feed, sector ETFs)."""
        snap = engine.snapshot()
        if not snap.get("ready"):
            return JSONResponse({"ok": False, "error": "Book still loading."})
        try:
            return JSONResponse(clean_json(themes.build(cfg.settings.get("themes") or {}, snap, cfg.hq)))
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"THEMES failed: {str(e)[:200]}"})

    @app.get("/api/brief")
    def get_brief(fresh: bool = False) -> JSONResponse:
        """HOME live brief: tape, internals, the close, relative strength, your book, what's next."""
        mk = mkts._parts.get("economy")
        return JSONResponse(clean_json(brief.get(engine.snapshot(), {"economy": mk[1]} if mk else None, fresh)))

    perf_cache: dict[str, Any] = {}
    perf_lock = threading.Lock()

    def perf_data(fresh: bool = False) -> dict[str, Any]:
        """Time-weighted return vs SPY: performance-log.md history plus the desk's daily rebuild."""
        pc = cfg.settings.get("performance") or {}
        ttl = 60 * float(pc.get("cache_minutes", 10))
        with perf_lock:
            hit = perf_cache.get("v")
            if hit and not fresh and time.time() - hit[0] < ttl:
                return hit[1]
            snap = engine.snapshot()
            anchor = str(pc.get("anchor_date", ""))

            def run() -> dict[str, Any]:
                return performance.compute(
                    cfg.hq / pc.get("log", "portfolio/performance-log.md"), anchor,
                    float(pc.get("anchor_cash", 0)), engine.broker, snap.get("positions", []),
                    (snap.get("account") or {}).get("cash") or 0.0, (snap.get("account") or {}).get("nav"),
                    (snap.get("benchmark") or {}).get("last"), transfers=store.transfers(anchor))
            try:
                out = run()
                # Deposits and withdrawals: Robinhood's feed has no transfer history, so the desk dates
                # them by watching for a jump in the cash no trade explains, on the day it lands.
                res = (out.get("check") or {}).get("cash_residual")
                if res is not None and snap.get("source") == "robinhood":
                    key = f"{anchor}|{pc.get('anchor_cash')}"  # a moved anchor resets the baseline, not a transfer
                    saved = store.kv_get("transfer_baseline") or {}
                    base = saved.get("value") if isinstance(saved, dict) and saved.get("anchor") == key else None
                    amt = performance.detect_transfer(res, base, float(pc.get("transfer_threshold", 20)))
                    if amt is not None:
                        store.add_transfer(out.get("today") or time.strftime("%Y-%m-%d"), amt)
                        out = run()
                        res = (out.get("check") or {}).get("cash_residual")
                    store.kv_set("transfer_baseline", {"anchor": key, "value": res})  # small drift (dividends) absorbed
            except Exception as e:  # keep the page up; say what failed
                out = {"ok": False, "error": f"Performance rebuild failed: {e}"}
            out = clean_json(out)
            out["computed_at"] = time.time()
            if out.get("ok"):
                perf_cache["v"] = (time.time(), out)
            return out

    def covered_tickers() -> list[str]:
        snap = engine.snapshot()
        return sorted({p["ticker"] for p in snap.get("positions", [])} | {w["ticker"] for w in snap.get("watchlist", [])}
                      | {x["ticker"] for x in store.tracked()})

    def events_watch() -> None:
        """Check SEC filings for held, watchlist and tracked names every 30 minutes."""
        time.sleep(120)
        while True:
            try:
                if engine.snapshot().get("ready"):
                    if sec_events.poll(covered_tickers()):
                        engine.poke()  # new filings: refresh the action queue and the digest now
            except sec.NoContact:
                pass
            except Exception:
                pass
            time.sleep(1800)

    def ern_watch() -> None:
        """Price the options-implied move for held names reporting within 10 days (every 6 hours)."""
        time.sleep(240)
        while True:
            try:
                snap = engine.snapshot()
                if snap.get("ready"):
                    soon = [e["ticker"] for e in snap.get("earnings_upcoming", []) if e.get("held")
                            and e.get("date") and (date.fromisoformat(e["date"]) - date.today()).days <= 10]
                    for t in soon:
                        try:
                            ern.load(t, cfg.hq, fresh=True)
                        except Exception:
                            pass
                    if soon:
                        engine.poke()
            except Exception:
                pass
            time.sleep(6 * 3600)

    if start_loop and pub:  # public edition: build the SEC + price table in the background (Yahoo's screener is blocked there)
        from . import fallback
        threading.Thread(target=fallback.warm, daemon=True).start()

    if start_loop:
        threading.Thread(target=events_watch, daemon=True).start()
        threading.Thread(target=ern_watch, daemon=True).start()
        threading.Thread(target=history.watch, daemon=True).start()  # multi-year screener table (weekly)

    @app.get("/api/events")
    def get_events(days: int = 14) -> JSONResponse:
        from datetime import date as _date, timedelta as _td
        since = (_date.today() - _td(days=max(1, min(days, 120)))).isoformat()
        return JSONResponse(clean_json({"ok": True, "events": store.sec_events(since)}))

    @app.post("/api/events/check")
    def check_events() -> dict[str, Any]:
        """Check SEC filings now (otherwise every 30 minutes)."""
        try:
            new = sec_events.poll(covered_tickers())
        except sec.NoContact as e:
            raise HTTPException(400, str(e)) from None
        engine.poke()
        return {"ok": True, "new": len(new)}

    @app.post("/api/events/seen")
    def events_seen() -> dict[str, Any]:
        store.mark_events_seen()
        return {"ok": True}

    def perf_watch() -> None:
        """Run the return rebuild every 30 minutes so transfers are dated even if PORT is never opened."""
        time.sleep(90)
        while True:
            try:
                if engine.snapshot().get("ready"):
                    perf_data(fresh=True)
            except Exception:
                pass
            time.sleep(1800)

    if start_loop:
        threading.Thread(target=perf_watch, daemon=True).start()

    # ---------- full financials from SEC filings ----------
    sec_cache: dict[str, tuple[float, Any]] = {}

    @app.get("/api/sec/{ticker}")
    def get_sec(ticker: str, fresh: bool = False) -> JSONResponse:
        """Expanded FA: statements, ratios and valuation history from 10-K / 10-Q XBRL facts."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        hit = sec_cache.get(t)
        if hit and not fresh and time.time() - hit[0] < 3600:
            return JSONResponse(hit[1])
        out = sec.statements(t)
        if out.get("ok"):
            try:
                import yfinance as yf
                h = yf.Ticker(market.yahoo_symbol(t)).history(period="max", interval="1d", auto_adjust=False)
                closes = h["Close"]
                closes.index = [i.date().isoformat() for i in closes.index]
                cur = out.get("currency") or "USD"
                if cur != "USD":  # US-listed price, foreign-currency statements: put the price in their currency
                    fxh = yf.Ticker(f"{cur}USD=X").history(period="max", interval="1d")["Close"]
                    fxh.index = [i.date().isoformat() for i in fxh.index]
                    fxs = fxh.reindex(sorted(set(fxh.index) | set(closes.index))).ffill().reindex(closes.index)
                    closes = (closes / fxs).dropna()
                    out["valuation_note"] = f"Price converted to {cur} at each date's {cur}/USD rate so multiples match the statements."
                # Dual-class filers tag one class on the cover page; scale to Yahoo's all-class count when it is clearly larger
                scale = 1.0
                shout = next((r for r in out["statements"].get("balance", []) if r["key"] == "shout"), None)
                last_sec = shout["A"].get(out["periods"]["A"][-1]) if shout and out["periods"]["A"] else None
                implied = (yf.Ticker(market.yahoo_symbol(t)).info or {}).get("impliedSharesOutstanding")
                if last_sec and implied and 1.05 <= implied / last_sec <= 3:
                    scale = implied / last_sec
                    out["shares_note"] = (f"SEC's share count ({last_sec / 1e6:,.0f}M) covers one share class; multiples use Yahoo's "
                                          f"all-class count ({implied / 1e6:,.0f}M), scaling history by {scale:.3f}.")
                out["valuation"] = sec.valuation(out, closes, scale)
            except Exception as e:  # multiples are a bonus; statements still show
                out["valuation"] = []
                out["valuation_error"] = str(e)[:160]
        data = clean_json(out)
        if out.get("ok"):
            sec_cache[t] = (time.time(), data)
        return JSONResponse(data)

    # ---------- screener ----------
    @app.get("/api/screens")
    def get_screens() -> JSONResponse:
        return JSONResponse(clean_json({
            "presets": screener.presets(), "builtin": sorted(screener.PRESETS),
            "fields": {k: {"label": v[0], "unit": v[2], "group": v[3]} for k, v in screener.FIELDS.items()},
            "sectors": screener.SECTORS, "mandate": screener.MANDATE,
            "hist_metrics": {k: {"label": v[0], "kind": v[1]} for k, v in history.METRICS.items()},
            "hist_stats": {k: {"label": v[0], "kinds": v[1], "unit": v[2]} for k, v in history.STATS.items()},
            "hist_max_years": history.MAX_YEARS, "hist_status": history.status()}))

    @app.post("/api/screen/run")
    def run_screen(payload: dict[str, Any] = Body(...)) -> JSONResponse:
        try:
            out = screener.run(payload, _cov, show=int(payload.get("show", 40)))
        except Exception as e:
            out = {"ok": False, "error": f"Screen failed: {str(e)[:200]}"}
        return JSONResponse(clean_json(out))

    @app.post("/api/screens")
    def save_screen(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Name the screen")
        screener.save_preset(name, payload)
        return {"ok": True}

    @app.delete("/api/screens/{name}")
    def delete_screen(name: str) -> dict[str, Any]:
        if name in screener.PRESETS:
            raise HTTPException(400, "Built-in presets can't be deleted; save a copy under a new name instead")
        screener.delete_preset(name)
        return {"ok": True}

    @app.get("/api/screen/verify/{ticker}")
    def verify_screen(ticker: str) -> JSONResponse:
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        try:
            out = screener.verify(t, sec.statements(t))
        except Exception as e:
            out = {"ok": False, "error": str(e)[:200]}
        return JSONResponse(clean_json(out))

    # ---------- pre-trade check ----------
    @app.get("/api/whatif")
    def get_whatif(ticker: str, side: str = "buy", shares: float | None = None, dollars: float | None = None) -> JSONResponse:
        """Paper trade: the book after a proposed buy or sell, checked against the mandate. Places nothing."""
        t = ticker.upper()
        if not equity.TICKER.match(t):
            raise HTTPException(400, "Not a ticker")
        snap = engine.snapshot()
        if side.lower() == "sell" and not any(p.get("ticker") == t for p in snap.get("positions", [])):
            return JSONResponse({"ok": False, "error": f"You don't hold {t}, so there is nothing to sell."})
        try:
            out = whatif.build(snap, engine.history, cfg.rules, cfg.overrides.get("beta_overrides") or {},
                               _cov(t), t, side, shares, dollars)
        except Exception as e:
            out = {"ok": False, "error": f"Check failed: {str(e)[:200]}"}
        return JSONResponse(clean_json(out))

    # ---------- decision journal ----------
    def _journal_prices(entries: list[dict[str, Any]]) -> tuple[dict[str, float], float | None]:
        snap = engine.snapshot()
        px = {p["ticker"]: p.get("price") for p in snap.get("positions", []) if p.get("price")}
        px.update({w["ticker"]: w.get("price") for w in snap.get("watchlist", []) if w.get("price") and w["ticker"] not in px})
        for t in {e["ticker"] for e in entries} - set(px):
            try:
                q = equity.quote(t)
                if q.get("last"):
                    px[t] = q["last"]
            except Exception:
                pass
        spy = (snap.get("benchmark") or {}).get("last")
        return px, spy

    @app.get("/api/journal")
    def get_journal() -> JSONResponse:
        entries = store.journal_entries()
        px, spy = _journal_prices(entries)
        from datetime import date as _date
        rich = journal.enrich(entries, px, spy, _date.today())
        return JSONResponse(clean_json({"ok": True, "entries": rich, "score": journal.scorecard(rich), "verdicts": journal.VERDICTS}))

    @app.post("/api/journal/{jid:int}")
    def update_journal(jid: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Save a reason / expectation / horizon, or a review verdict."""
        fields = {k: payload[k] for k in ("reason", "expect", "horizon", "verdict", "review_note") if k in payload}
        if "verdict" in fields:
            if fields["verdict"] not in journal.VERDICTS:
                raise HTTPException(400, "Unknown verdict")
            fields["reviewed_ts"] = store.now()
        if "horizon" in fields:
            fields["horizon"] = max(7, min(int(fields["horizon"]), 1095))
        for k in ("reason", "expect", "review_note"):
            if k in fields:
                fields[k] = str(fields[k])[:600]
        store.update_journal(jid, **fields)
        return {"ok": True}

    @app.post("/api/journal")
    def add_plan(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Log a planned trade from the pre-trade check, with its reason, before placing it."""
        t = str(payload.get("ticker", "")).upper()
        side = payload.get("side")
        if not equity.TICKER.match(t) or side not in ("buy", "sell"):
            raise HTTPException(400, "Need a ticker and side buy or sell")
        from datetime import date as _date
        day = _date.today().isoformat()
        spy = (engine.snapshot().get("benchmark") or {}).get("last")
        new = store.add_journal(day, t, "plan_" + side, float(payload.get("shares") or 0), payload.get("price"), spy,
                                "plan", f"plan:{day}:{t}:{side}:{time.time():.0f}", str(payload.get("reason", ""))[:600])
        return {"ok": new}

    @app.post("/api/journal/backfill")
    def journal_backfill(days: int = 30) -> dict[str, Any]:
        """One-time: create entries for Robinhood fills in the last N days that the journal doesn't have yet."""
        fills_fn = getattr(engine.broker, "fills_since", None)
        if not fills_fn:
            raise HTTPException(400, "Needs the Robinhood feed")
        from datetime import date as _date, timedelta as _td
        since = (_date.today() - _td(days=max(1, min(days, 365)))).isoformat()
        fills = fills_fn(since)
        hist = engine.history
        added = 0
        for g in journal.from_fills(fills):
            spy = None
            if hist is not None and "SPY" in hist.columns:
                s = hist["SPY"].loc[:g["date"]].dropna()
                spy = float(s.iloc[-1]) if len(s) else None
            if store.add_journal(g["date"], g["ticker"], g["kind"], g["shares"], g["price"], spy, "backfill", g["key"]):
                added += 1
        return {"ok": True, "added": added, "since": since}

    @app.get("/api/reconcile")
    def get_reconcile() -> JSONResponse:
        """What Robinhood shows that the research folder doesn't yet: new holdings, share changes, exits,
        deposits and withdrawals. Read by the RECONCILE panel and the scheduled reconcile task."""
        snap = engine.snapshot()
        anchor = str((cfg.settings.get("performance") or {}).get("anchor_date", ""))
        done = set(store.kv_get("reconcile_done") or [])
        out = reconcile.build(snap, store.transfers(anchor), _cov, done)
        # Keel's tracking list, for the reconcile task to mirror into the research folder/portfolio/tracking.md
        out["tracking"] = [{k: r.get(k) for k in ("ticker", "company", "sector", "theme", "note", "added", "price", "status")}
                           for r in store.tracked()]
        return JSONResponse(clean_json(out))

    @app.post("/api/reconcile/{item_id}/done")
    def reconcile_done(item_id: str) -> dict[str, Any]:
        """Mark an item handled (the task calls this after writing it to the research folder)."""
        if item_id.startswith("transfer:"):
            try:
                store.mark_transfer_logged(int(item_id.split(":", 1)[1]))
            except ValueError:
                raise HTTPException(400, "Bad transfer id") from None
        else:
            done = set(store.kv_get("reconcile_done") or [])
            done.add(item_id)
            store.kv_set("reconcile_done", sorted(done)[-500:])
        return {"ok": True}

    @app.get("/api/performance")
    def get_performance(fresh: bool = False) -> JSONResponse:
        return JSONResponse(perf_data(fresh))

    @app.post("/api/open")
    def open_file(path: str) -> dict[str, Any]:
        """Open an the research folder file in its default app (Obsidian/editor). Only paths inside the research folder."""
        target = Path(path).resolve()
        hq = cfg.hq.resolve()
        if hq not in target.parents or not target.exists():
            raise HTTPException(400, "Only existing files inside the research folder can be opened")
        os.startfile(str(target))  # noqa: S606 - local desktop app, path checked above
        return {"ok": True}

    @app.get("/")
    def index() -> Any:
        if pub:
            return HTMLResponse(public.inject((WEB / "index.html").read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
