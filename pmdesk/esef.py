"""Full financials for EU and UK listings (tier 2a): annual reports in ESEF, the machine-readable format EU and UK
listed companies must file, from filings.xbrl.org (free, community-run, no key).

Ticker -> company: the name from Yahoo's chart data, looked up in GLEIF (the global register of Legal Entity
Identifiers, LEI), keeping only a candidate registered in the exchange's country (.L -> GB, .PA -> FR ...).
A wrong match can be pinned in config/lei_overrides.yaml ("SHEL.L: 21380068P1DRHMJ8KU70").
Filings -> the same shape SEC's companyfacts uses, so Keel's IFRS statement code reads them unchanged: only
standard IFRS line items without breakdowns; the newer filing wins where two years overlap (restatements).
Annual figures only (European filers don't tag quarterly results). Each filing's extract is cached for good.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Any

import requests
import yaml

from . import store
from .config import CONFIG_DIR, DATA_DIR

log = logging.getLogger("pmdesk.esef")
UA = {"User-Agent": "Keel market terminal (open-source research tool)"}
BASE = "https://filings.xbrl.org"
CACHE = DATA_DIR / "esef"

# Yahoo exchange suffix -> ISO country of the company's registered address
SUFFIX_COUNTRY = {"L": "GB", "IL": "GB", "PA": "FR", "DE": "DE", "F": "DE", "MU": "DE", "SG": "DE", "AS": "NL", "BR": "BE", "MI": "IT",
                  "MC": "ES", "LS": "PT", "IR": "IE", "VI": "AT", "ST": "SE", "CO": "DK", "HE": "FI", "OL": "NO", "WA": "PL",
                  "AT": "GR", "PR": "CZ", "BD": "HU", "LU": "LU", "TL": "EE", "RG": "LV", "VS": "LT", "IC": "IS"}
# Companies listed in one place and registered in another (Dutch- or Jersey-registered London listings are common)
ALSO_OK = {"GB": {"JE", "GG", "IM", "IE", "NL", "BM", "KY", "CY", "LU"}, "NL": {"GB"}, "IE": {"GB"}, "FR": {"NL", "LU"}, "DE": {"NL", "LU"},
           "BE": {"NL"}, "IT": {"NL", "LU"}, "SE": {"FI"}, "DK": {"SE"}}


def suffix(t: str) -> str | None:
    m = re.match(r"^[A-Z0-9.\-]+\.([A-Z]{1,3})$", t.upper())
    return m.group(1) if m and m.group(1) in SUFFIX_COUNTRY else None


def is_eu(t: str) -> bool:
    return suffix(t) is not None


def _get(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code in (200, 404):
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:80]
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or "no response")


# ---------- ticker -> LEI ----------
SUFFIXES = re.compile(r"\b(plc|p\.l\.c|ag|se|sa|s\.a|nv|n\.v|bv|ab|asa|a/s|as|oyj|spa|s\.p\.a|ltd|limited|group|holdings?|inc|corp(oration)?|company|co|kgaa|gmbh)\b\.?", re.I)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", SUFFIXES.sub(" ", re.sub(r"[^\w\s&./-]", " ", name or ""))).strip().lower()


def match_lei(name: str, country: str, candidates: list[dict[str, Any]]) -> str | None:
    """Pick the candidate registered in the listing's country whose cleaned name is closest; None if nothing is close."""
    ok = {country} | ALSO_OK.get(country, set())
    best, score = None, 0.0
    for c in candidates:
        if c.get("country") not in ok or c.get("status", "ACTIVE") != "ACTIVE":
            continue
        s = difflib.SequenceMatcher(None, _clean(fold(name)), _clean(fold(c["name"]))).ratio()
        if c.get("country") != country:
            s -= 0.05  # prefer a home-country registration on a tie
        if s > score:
            best, score = c["lei"], s
    return best if score >= 0.72 else None


def fold(s: str) -> str:
    """Strip accents: 'Moët' -> 'Moet'."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def name_variants(name: str) -> list[str]:
    """'LVMH Moët Hennessy - Louis Vuitton, Société Européenne' -> the full name, accent-free, before the comma, bare."""
    f = fold(name)
    head = re.split(r",| - | \(", f)[0].strip()
    out = [name, f, f.replace(" - ", " "), head, _clean(f)]
    return [v for i, v in enumerate(out) if v and v not in out[:i]]


def _overrides() -> dict[str, str]:
    p = CONFIG_DIR / "lei_overrides.yaml"
    try:
        return {str(k).upper(): str(v) for k, v in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).items()} if p.exists() else {}
    except Exception:
        return {}


def gleif_candidates(name: str) -> list[dict[str, Any]]:
    r = _get("https://api.gleif.org/api/v1/fuzzycompletions", {"field": "entity.legalName", "q": name})
    ids = [d["relationships"]["lei-records"]["data"]["id"] for d in (r.json().get("data") or [])
           if d.get("relationships", {}).get("lei-records", {}).get("data")][:8]
    if not ids:
        return []
    rr = _get("https://api.gleif.org/api/v1/lei-records", {"filter[lei]": ",".join(ids), "page[size]": len(ids)})
    out = []
    for d in rr.json().get("data") or []:
        e = d["attributes"]["entity"]
        out.append({"lei": d["id"], "name": e["legalName"]["name"], "country": (e.get("legalAddress") or {}).get("country"),
                    "status": e.get("status")})
    return out


def lei_for(t: str, name: str | None = None) -> str | None:
    """LEI for a European listing (pinned override, then a cached match, then GLEIF by name)."""
    t = t.upper()
    ov = _overrides().get(t)
    if ov:
        return ov
    cache = store.kv_get("lei:" + t)
    if cache and time.time() - cache.get("at", 0) < (30 if cache.get("lei") else 1) * 86400:  # a miss is retried daily
        return cache.get("lei")
    sfx = suffix(t)
    if not sfx:
        return None
    if not name:
        from .fallback import one_price
        try:
            name = (one_price(t)[1] or {}).get("longName")
        except Exception:
            name = None
    lei = None
    if name:
        try:
            for q in name_variants(name):  # full name, then without accents / the legal-form tail
                lei = match_lei(name, SUFFIX_COUNTRY[sfx], gleif_candidates(q))
                if lei:
                    break
        except Exception as e:
            log.warning("GLEIF lookup for %s failed: %s", t, e)
            return None  # a failed lookup is not cached
    store.kv_set("lei:" + t, {"lei": lei, "name": name, "at": time.time()})
    return lei


# ---------- filings -> companyfacts shape ----------
def filings(lei: str, n: int = 5) -> list[dict[str, Any]]:
    """The latest annual filings (one per year end), newest first."""
    r = _get(f"{BASE}/api/filings", {"filter[entity.identifier]": lei, "sort": "-period_end", "page[size]": 20})
    seen, out = set(), []
    for d in r.json().get("data") or []:
        a = d["attributes"]
        if not a.get("json_url") or a.get("period_end") in seen:
            continue
        seen.add(a["period_end"])
        out.append({"id": d["id"], "period_end": a["period_end"], "added": (a.get("date_added") or "")[:10], "json_url": a["json_url"],
                    "viewer_url": a.get("viewer_url"), "country": a.get("country")})
        if len(out) >= n:
            break
    return out


def _day(s: str, end: bool) -> str:
    """xBRL-JSON times are midnight boundaries: a period ending '2026-01-01T00:00:00' is the year to 31 Dec 2025."""
    d = date.fromisoformat(s[:10])
    return (d - timedelta(days=1)).isoformat() if end and s[10:].startswith("T00:00:00") else d.isoformat()


def _unit(u: str | None) -> str | None:
    if not u:
        return "pure"
    parts = [p.split(":")[-1] for p in u.split("/")]
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}/{parts[1]}" if parts[1] == "shares" else None


def extract(doc: dict[str, Any], accn: str, added: str) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Standard IFRS facts with no breakdowns -> {concept: {unit: [{start, end, val, ...}]}}."""
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for f in (doc.get("facts") or {}).values():
        dm = f.get("dimensions") or {}
        concept = dm.get("concept") or ""
        if not concept.startswith("ifrs-full:") or set(dm) - {"concept", "entity", "period", "unit", "language"}:
            continue
        try:
            val = float(f.get("value"))
        except (TypeError, ValueError):
            continue
        unit = _unit(dm.get("unit"))
        per = dm.get("period") or ""
        if not unit or not per:
            continue
        if "/" in per:
            a, b = per.split("/")
            row = {"start": _day(a, False), "end": _day(b, True)}
        else:
            row = {"end": _day(per, True)}
        row.update(val=val, accn=accn, fy=int(row["end"][:4]), fp="FY", form="20-F", filed=added)
        out.setdefault(concept.split(":", 1)[1], {}).setdefault(unit, []).append(row)
    return out


def filing_facts(f: dict[str, Any]) -> dict[str, Any]:
    """One filing's extract, cached on disk for good (published filings don't change)."""
    p = CACHE / f"{f['id']}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = _get(BASE + f["json_url"], timeout=120)
    if r.status_code != 200:
        return {}
    ex = extract(r.json(), f"esef-{f['id']}", f["added"])
    CACHE.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ex), encoding="utf-8")
    return ex


def merge(extracts: list[dict[str, Any]]) -> dict[str, Any]:
    """Newest first: a period already filled by a newer filing keeps the newer number."""
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    seen: set[tuple[str, str, str | None, str]] = set()
    for ex in extracts:
        for concept, units in ex.items():
            for unit, rows in units.items():
                for r in rows:
                    k = (concept, unit, r.get("start"), r["end"])
                    if k in seen:
                        continue
                    seen.add(k)
                    out.setdefault(concept, {}).setdefault(unit, []).append(r)
    return {c: {"label": c, "units": u} for c, u in out.items()}


def sec_twin(t: str) -> dict[str, Any] | None:
    """A European listing whose company also files with SEC (SAP, Shell, ASML...): use those filings."""
    from . import sec
    name = (store.kv_get("lei:" + t.upper()) or {}).get("name")
    if not name:
        return None
    j = sec._get_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json", 7 * 86400) or {}
    target = _clean(fold(name))
    best, score = None, 0.0
    for v in j.values():
        r = difflib.SequenceMatcher(None, target, _clean(fold(v.get("title") or ""))).ratio()
        if r > score:
            best, score = v, r
    if not best or score < 0.85:
        return None
    try:
        f = sec.facts(best["ticker"])
    except Exception:
        return None
    if f:
        f = {**f, "source": "SEC (dual listing)", "sec_ticker": best["ticker"]}
    return f


def facts(t: str) -> dict[str, Any] | None:
    """companyfacts-shaped data for a European listing, or None (not European, no LEI match, or no ESEF filings)."""
    if not is_eu(t):
        return None
    lei = lei_for(t)
    if not lei:
        return None
    fl = filings(lei)
    if not fl:
        return sec_twin(t)
    ex = [filing_facts(f) for f in fl]
    ifrs = merge([e for e in ex if e])
    if not ifrs:
        return None
    name = (store.kv_get("lei:" + t.upper()) or {}).get("name")
    return {"cik": None, "entityName": name or t, "facts": {"ifrs-full": ifrs}, "source": "ESEF", "lei": lei,
            "filings": [{"period_end": f["period_end"], "viewer": BASE + f["viewer_url"] if f.get("viewer_url") else None} for f in fl]}
