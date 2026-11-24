"""Market-wide news wire for MKTS: what is moving markets, not only your names.

Free sources, fetched in parallel every 5 minutes: RSS feeds (CNBC, MarketWatch), Google News
topic searches (Fed, policy, geopolitics, energy, metals, credit...), and Yahoo news on market
symbols (S&P, 10Y, oil, gold, dollar, VIX, high yield, Bitcoin). Stories are tagged by topic,
duplicates across sources are merged, and anything mentioning a name you hold or watch is flagged.
Sources live in config/settings.yaml (news:). Each source fails on its own.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import requests
import yfinance as yf

log = logging.getLogger("pmdesk.news")
UA = {"User-Agent": "Mozilla/5.0 (Keel; local news reader)"}

# Keyword tags. A story can carry several. Order = display order.
TOPICS: dict[str, list[str]] = {
    "Fed & rates": ["fed ", "federal reserve", "powell", "rate cut", "rate hike", "fomc", "treasury yield", "yields", "bond market", "inflation", "cpi", "pce", "jobs report", "payroll", "waller", "ecb", "bank of japan", "boj"],
    "US policy": ["trump", "white house", "tariff", "congress", "senate", "administration", "executive order", "budget", "shutdown", "sec ", "regulat", "antitrust", "treasury secretary", "sanction"],
    "Geopolitics": ["war", "ukraine", "russia", "iran", "israel", "gaza", "china", "taiwan", "missile", "strike", "military", "nato", "north korea", "middle east", "houthi"],
    "Energy": ["oil", "crude", "opec", "brent", "natural gas", "lng", "gasoline", "refin", "energy"],
    "Metals": ["gold", "silver", "copper", "platinum", "metals", "lithium", "aluminum"],
    "Credit": ["credit", "high yield", "junk", "private credit", "bdc", "spread", "default", "bond issu", "leveraged loan", "clo", "downgrade", "moody", "fitch"],
    "Equities": ["stock", "s&p", "nasdaq", "dow", "earnings", "shares", "rally", "selloff", "sell-off", "ipo", "buyback", "guidance"],
    "AI & tech": ["ai ", "artificial intelligence", "nvidia", "chip", "semiconductor", "data center", "openai", "hyperscaler"],
    "FX & EM": ["dollar", "yen", "euro", "yuan", "currency", "emerging market", "peso", "rupee"],
    "Crypto": ["bitcoin", "crypto", "ether", "stablecoin", "coinbase"],
}

_cache: dict[str, Any] = {}
_lock = threading.Lock()


def _clean(s: str | None) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _when(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_rss(text: str | bytes, source: str, topic: str | None = None) -> list[dict[str, Any]]:
    """RSS 2.0 items -> stories. Pass raw bytes so the feed's own encoding declaration is used.
    Google News puts the publisher in <source>."""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        if not title:
            continue
        pub = it.find("source")
        publisher = _clean(pub.text) if pub is not None and pub.text else source
        if publisher != source and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "summary": _clean(it.findtext("description"))[:500],
                    "time": _when(it.findtext("pubDate")), "source": publisher, "via": source, "topics": [topic] if topic else []})
    return out


def tag(story: dict[str, Any]) -> list[str]:
    text = " " + (story["title"] + " " + story.get("summary", "")).lower() + " "
    tags = list(story.get("topics") or [])
    for t, words in TOPICS.items():
        if t not in tags and any(w in text for w in words):
            tags.append(t)
    return tags[:3]


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())[:70]


GENERIC = {"ishares", "vanguard", "first", "global", "group", "core", "american", "general", "united", "national", "international"}


def book_names(state: dict[str, Any]) -> dict[str, str]:
    """Words that identify a held or watched name in a headline -> ticker.
    Tickers match exactly (upper case); company names match in any case ("Nvidia" = "NVIDIA")."""
    names: dict[str, str] = {}
    for row in (state.get("positions") or []) + (state.get("watchlist") or []):
        t = row.get("ticker") or ""
        if len(t) >= 3 and t.isalpha():
            names[t] = t
        first = re.split(r"[\s,.()]", (row.get("company") or "").strip())[0]
        if len(first) >= 4 and first.lower() not in GENERIC:
            names["~" + first.lower()] = t
    return names


def mentions(text: str, names: dict[str, str]) -> list[str]:
    hits = set()
    low = text.lower()
    for w, t in names.items():
        if w.startswith("~"):
            if re.search(r"(?<![a-z])" + re.escape(w[1:]) + r"(?![a-z])", low):
                hits.add(t)
        elif re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", text):
            hits.add(t)
    return sorted(hits)


def build(cfg: dict[str, Any], state: dict[str, Any], days: int = 2) -> dict[str, Any]:
    days = max(1, min(int(days), 30))
    feeds = cfg.get("feeds", [])
    topics = cfg.get("google_topics", [])
    ysyms = cfg.get("yahoo_symbols", [])
    jobs: list[tuple[str, Any]] = []
    for f in feeds:
        jobs.append((f["name"], lambda f=f: parse_rss(requests.get(f["url"], headers=UA, timeout=12).content, f["name"])))
    # Google News returns the newest ~100 per search, so a longer look-back searches each older span on its own:
    # the last 2 days, then week by week (up to 5 spans), keeping ~25 stories per span.
    today = datetime.now(timezone.utc).date()
    spans: list[tuple[str, str]] = [(f"when:{min(days, 2)}d", "")]
    a = today - timedelta(days=2)
    while (today - a).days < days and len(spans) < 6:
        b = max(today - timedelta(days=days), a - timedelta(days=7))
        spans.append((f"after:{b.isoformat()} before:{a.isoformat()}", f" ({b.strftime('%b %d')})"))
        a = b
    for t in topics:
        for when, lab in spans:
            url = f"https://news.google.com/rss/search?q={quote_plus(t['query'] + ' ' + when)}&hl=en-US&gl=US&ceid=US:en"
            per = 15 if not lab else 25
            jobs.append((f"Google News: {t['topic']}{lab}", lambda u=url, tp=t["topic"], per=per: parse_rss(requests.get(u, headers=UA, timeout=12).content, "Google News", tp)[:per]))
    for s in ysyms:
        def yjob(s=s):
            out = []
            for it in (yf.Ticker(s).news or [])[:8]:
                c = it.get("content") or it
                if c.get("title"):
                    out.append({"title": _clean(c["title"]), "url": ((c.get("clickThroughUrl") or c.get("canonicalUrl") or {}) or {}).get("url"),
                                "summary": _clean(c.get("summary"))[:500], "time": _when(c.get("pubDate")),
                                "source": (c.get("provider") or {}).get("displayName", "Yahoo"), "via": f"Yahoo {s}", "topics": []})
            return out
        jobs.append((f"Yahoo {s}", yjob))

    status: dict[str, Any] = {}
    stories: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {name: ex.submit(fn) for name, fn in jobs}
        for name, fut in futs.items():
            try:
                got = fut.result(timeout=25)
                status[name] = {"ok": True, "n": len(got)}
                stories += got
            except Exception as e:
                status[name] = {"ok": False, "error": str(e)[:120]}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(float(cfg.get("max_age_hours", 48)), 24.0 * days))
    names = book_names(state)
    merged: dict[str, dict[str, Any]] = {}
    for s in stories:
        if s["time"] and s["time"] < cutoff:
            continue
        k = _key(s["title"])
        if k in merged:  # same story from another source: keep the earliest time, list every source
            m = merged[k]
            if s["source"] not in m["sources"]:
                m["sources"].append(s["source"])
            m["topics"] = list(dict.fromkeys(m["topics"] + s["topics"]))
            if s["time"] and (not m["time"] or s["time"] < m["time"]):
                m["time"] = s["time"]
            continue
        merged[k] = {**s, "sources": [s["source"]]}
    out = []
    for s in merged.values():
        s["topics"] = tag(s)
        hay = s["title"] + " " + s.get("summary", "")
        s["book"] = mentions(hay, names)
        s["time"] = s["time"].isoformat() if s["time"] else None
        out.append(s)
    out.sort(key=lambda s: s["time"] or "", reverse=True)
    return {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(), "items": out[: int(cfg.get("max_items", 160)) if days <= 2 else 1500],
            "topics": list(TOPICS), "sources": status, "days": days}


def get(cfg: dict[str, Any], state: dict[str, Any], fresh: bool = False, days: int = 2) -> dict[str, Any]:
    days = max(1, min(int(days), 30))
    ttl = 60 * float(cfg.get("refresh_minutes", 5)) * (1 if days <= 2 else 3)
    key = f"v{days}" if days != 2 else "v"
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            out = build(cfg, state, days)
        except Exception as e:
            log.warning("news failed: %s", e)
            return hit[1] if hit else {"ok": False, "error": str(e)}
        _cache[key] = (time.time(), out)
        return out
