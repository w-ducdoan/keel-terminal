"""13F tracker: what notable fundamental funds own in your names, and what they changed last quarter.

Sources: each fund's two latest 13F-HR filings on SEC (holdings at quarter end, filed within 45 days).
Holdings are matched to your tickers by issuer name, since SEC publishes CUSIPs but no free CUSIP-to-ticker map;
share classes of one issuer (e.g. Alphabet A and C) are combined. Options lines (puts and calls) are skipped.
13Fs show long US positions only, 45+ days late: a picture of who owns what, not of what they are doing today.
Rule-based; no AI.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import requests

from . import sec, sec_events, store

DEFAULT_FUNDS = ["Berkshire Hathaway Inc", "Pershing Square Capital Management", "Baupost Group", "Lone Pine Capital",
                 "Viking Global Investors", "Coatue Management", "Tiger Global Management", "Akre Capital Management",
                 "ValueAct Capital Management", "Third Point", "Durable Capital Partners", "Altimeter Capital Management",
                 "D1 Capital Partners", "Egerton Capital", "TCI Fund Management", "Fundsmith"]
SUFFIX = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|L P|AG|SA|NV|N V|SE|HLDGS|HOLDINGS|HOLDING|GROUP|"
                    r"CL [A-Z]|CLASS [A-Z]|COM|NEW|THE|DEL|/DE/|/MD/)\b")


def norm(name: str) -> str:
    """'ALPHABET INC CAP STK CL C' and 'Alphabet Inc.' -> 'ALPHABET'; 'Toast, Inc.' -> 'TOAST'."""
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    s = re.sub(r"\bCAP STK\b|\bCOM STK\b|\bSHS\b|\bORD\b|\bSPONSORED ADR\b|\bADR\b", " ", s)
    s = SUFFIX.sub(" ", s)
    return " ".join(s.split())


def parse_infotable(xml_text: str) -> list[dict[str, Any]]:
    """Rows from a 13F information table, namespace-agnostic. value is in dollars (SEC rule since 2023)."""
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter():
        if not it.tag.endswith("infoTable"):
            continue
        get = {}
        for ch in it.iter():
            tag = ch.tag.split("}")[-1]
            if ch.text and ch.text.strip():
                get[tag] = ch.text.strip()
        if get.get("putCall"):
            continue  # options exposure, not shares owned
        try:
            out.append({"issuer": get.get("nameOfIssuer", ""), "cls": get.get("titleOfClass", ""), "cusip": get.get("cusip", ""),
                        "value": float(get.get("value", 0)), "shares": float(get.get("sshPrnamt", 0))})
        except ValueError:
            continue
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """By normalized issuer name: summed value and shares, the issuer name as filed."""
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = norm(r["issuer"])
        a = agg.setdefault(k, {"issuer": r["issuer"], "value": 0.0, "shares": 0.0})
        a["value"] += r["value"]
        a["shares"] += r["shares"]
    return agg


def change(now: dict[str, Any] | None, prev: dict[str, Any] | None) -> dict[str, Any]:
    if now and not prev:
        return {"action": "new", "share_chg": None}
    if prev and not now:
        return {"action": "exited", "share_chg": -1.0}
    if not now:
        return {"action": None, "share_chg": None}
    c = now["shares"] / prev["shares"] - 1 if prev["shares"] else None
    act = "held" if c is None or abs(c) < 0.02 else "added" if c > 0 else "trimmed"
    return {"action": act, "share_chg": c}


# ---------- SEC ----------
def _files_13f(cik: int) -> bool:
    """Has this entity filed a 13F-HR in the last ~13 months? (Fund families have many entities; one files.)"""
    try:
        rec = sec_events._submissions(cik)["filings"]["recent"]
    except Exception:
        return False
    cutoff = (date.today() - timedelta(days=400)).isoformat()
    return any(f == "13F-HR" and d >= cutoff for f, d in zip(rec["form"], rec["filingDate"]))


def resolve_fund(name: str) -> dict[str, Any] | None:
    """Fund name -> the entity that files its 13F. Entries can pin a CIK as 'Name|1234567'."""
    if "|" in name:
        n, c = name.split("|", 1)
        return {"name": n.strip(), "cik": int(c)}
    k = f"f13cik:{name.lower()}"
    hit = store.kv_get(k)
    if hit:
        return hit
    hits = []
    for attempt in range(3):
        try:
            with sec._lock:
                r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"keysTyped": name}, headers=sec._ua(), timeout=30)
                time.sleep(0.15)
            if r.ok:
                hits = r.json().get("hits", {}).get("hits", [])
                break
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    for h in hits[:8]:
        cik = int(h["_id"])
        if _files_13f(cik):
            out = {"name": re.sub(r"\s*\(.*?\)\s*$", "", h["_source"].get("entity") or name), "cik": cik}
            store.kv_set(k, out)  # only a filer that actually files 13Fs is remembered
            return out
    return None


def _filing_rows(cik: int, acc: str) -> list[dict[str, Any]]:
    cache = sec.CACHE / f"13f_{acc}.xml"
    if cache.exists():
        return parse_infotable(cache.read_text(encoding="utf-8"))
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    with sec._lock:
        idx = requests.get(f"{base}/index.json", headers=sec._ua(), timeout=30)
        time.sleep(0.15)
    idx.raise_for_status()
    names = [x["name"] for x in idx.json().get("directory", {}).get("item", [])]
    xmlf = next((n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()), None)
    if not xmlf:
        return []
    with sec._lock:
        r = requests.get(f"{base}/{xmlf}", headers=sec._ua(), timeout=60)
        time.sleep(0.15)
    r.raise_for_status()
    sec.CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(r.text, encoding="utf-8")
    return parse_infotable(r.text)


def fund_quarters(fund: dict[str, Any]) -> dict[str, Any] | None:
    """The latest two 13F-HR holdings for a fund (amendments that restate the whole table are skipped)."""
    rec = sec_events._submissions(fund["cik"])["filings"]["recent"]
    filings = [(acc, rd, fd) for f, acc, rd, fd in zip(rec["form"], rec["accessionNumber"], rec.get("reportDate", [""] * len(rec["form"])), rec["filingDate"])
               if f == "13F-HR"]
    if not filings:
        return None
    seen, picked = set(), []
    for acc, rd, fd in filings:  # one per report quarter, newest first
        if rd in seen:
            continue
        seen.add(rd)
        picked.append((acc, rd, fd))
        if len(picked) == 2:
            break
    now = aggregate(_filing_rows(fund["cik"], picked[0][0]))
    prev = aggregate(_filing_rows(fund["cik"], picked[1][0])) if len(picked) > 1 else {}
    total = sum(x["value"] for x in now.values())
    # SEC switched 13F values from $ thousands to dollars in 2023; a few filers still report thousands.
    # A multi-position book worth under $50M is that: scale its dollar figures (weights are unaffected).
    if total and total < 5e7 and len(now) >= 10:
        for book in (now, prev):
            for x in book.values():
                x["value"] *= 1000
        total *= 1000
    return {**fund, "quarter": picked[0][1], "filed": picked[0][2], "prev_quarter": picked[1][1] if len(picked) > 1 else None,
            "now": now, "prev": prev, "total": total}


def build(names: dict[str, str], funds: list[str] | None = None) -> dict[str, Any]:
    """names: {ticker: company name} for your held / watch / tracked names."""
    wanted = funds or DEFAULT_FUNDS
    resolved = {n: resolve_fund(n) for n in wanted}
    with ThreadPoolExecutor(max_workers=3) as ex:
        loaded = list(ex.map(lambda f: _safe(fund_quarters, f) if f else None, resolved.values()))
    # A fund whose latest report is over ~9 months old has stopped filing (or the match is wrong): leave it out
    cutoff = (date.today() - timedelta(days=270)).isoformat()
    data = [d for d in loaded if d and (d.get("quarter") or "") >= cutoff]
    stale = [d["name"] for d in loaded if d and (d.get("quarter") or "") < cutoff]
    keys = {t: norm(n) for t, n in names.items() if n}
    by_ticker: dict[str, list[dict[str, Any]]] = {t: [] for t in names}
    for d in data:
        for t, k in keys.items():
            if not k:
                continue
            now, prev = d["now"].get(k), d["prev"].get(k)
            if not now and not prev:
                continue
            ch = change(now, prev)
            by_ticker[t].append({"fund": d["name"], "cik": d["cik"], "quarter": d["quarter"], **ch,
                                 "value": now["value"] if now else 0.0, "shares": now["shares"] if now else 0.0,
                                 "pct_of_fund": (now["value"] / d["total"]) if now and d["total"] else None})
    rows = []
    for t, hs in by_ticker.items():
        holders = [h for h in hs if h["action"] != "exited"]
        rows.append({"ticker": t, "holders": len(holders), "new": sum(h["action"] == "new" for h in hs),
                     "added": sum(h["action"] == "added" for h in hs), "trimmed": sum(h["action"] == "trimmed" for h in hs),
                     "exited": sum(h["action"] == "exited" for h in hs),
                     "funds": sorted(hs, key=lambda h: -(h["pct_of_fund"] or 0))})
    rows.sort(key=lambda r: (-r["holders"], -(r["new"] + r["added"])))
    # Idea feed: each fund's biggest new positions and adds, outside your names
    mine = set(keys.values())
    ideas = []
    for d in data:
        for k, now in d["now"].items():
            if k in mine or not d["total"]:
                continue
            ch = change(now, d["prev"].get(k))
            w = now["value"] / d["total"]
            if ch["action"] == "new" and w >= 0.01 or ch["action"] == "added" and (ch["share_chg"] or 0) >= 0.5 and w >= 0.02:
                ideas.append({"fund": d["name"], "issuer": now["issuer"], "action": ch["action"], "share_chg": ch["share_chg"],
                              "pct_of_fund": w, "value": now["value"], "quarter": d["quarter"]})
    ideas.sort(key=lambda x: -x["pct_of_fund"])
    return {"ok": True, "funds": [{"name": d["name"], "cik": d["cik"], "quarter": d["quarter"], "filed": d["filed"],
                                   "positions": len(d["now"]), "total": d["total"]} for d in data],
            "unresolved": [n.split("|")[0] for n, f in resolved.items() if not f], "stale": stale,
            "rows": rows, "ideas": ideas[:40], "as_of": date.today().isoformat(),
            "note": "13F = long US holdings at quarter end, filed up to 45 days later. Matched to your names by issuer name."}


# ---------- your fund list ----------
def fund_list(settings_list: list[str] | None) -> list[str]:
    """Your edited list (saved in Keel) wins; else settings.yaml; else the built-in default. Entries are 'Name|CIK'."""
    mine = store.kv_get("f13_funds")
    return list(mine) if mine is not None else list(settings_list or DEFAULT_FUNDS)


def add_fund(settings_list: list[str] | None, name: str, cik: int) -> list[str]:
    cur = fund_list(settings_list)
    if not any(e.split("|")[-1].strip() == str(cik) for e in cur if "|" in e):
        cur.append(f"{name.strip()}|{int(cik)}")
    store.kv_set("f13_funds", cur)
    return cur


def remove_fund(settings_list: list[str] | None, key: str) -> list[str]:
    """key: a CIK or a name as listed."""
    cur = fund_list(settings_list)
    out = [e for e in cur if not (e.split("|")[-1].strip() == key or e.split("|")[0].strip().lower() == key.lower())]
    for e in cur:  # a plain-name entry: match by the CIK it resolved to
        if "|" not in e and (resolve_fund(e) or {}).get("cik") == (int(key) if key.isdigit() else None):
            out = [x for x in out if x != e]
    store.kv_set("f13_funds", out)
    return out


def parse_company_feed(xml: str) -> list[dict[str, Any]]:
    """SEC company search (atom). Several matches -> a list of companies; exactly one -> that company's filing feed."""
    import html as _h
    if "Company Search Feed" not in xml and "<company-info>" in xml:  # exactly one match: its own filing feed
        cik = re.search(r"<cik>(\d+)</cik>", xml)
        name = re.search(r"<conformed-name>(.*?)</conformed-name>", xml)
        return [{"name": _h.unescape(name.group(1)) if name else "", "cik": int(cik.group(1))}] if cik else []
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        cik, name = re.search(r"<cik>(\d+)</cik>", entry), re.search(r"<title>(.*?)</title>", entry)
        city, state = re.search(r"<city>(.*?)</city>", entry), re.search(r"<state>(.*?)</state>", entry)
        if cik and name:
            out.append({"name": _h.unescape(name.group(1)).strip(), "cik": int(cik.group(1)),
                        "where": ", ".join(x for x in [city.group(1).title() if city else "", state.group(1) if state else ""] if x)})
    return out


def search_funds(q: str) -> list[dict[str, Any]]:
    """Funds by name among entities that have filed 13F-HR (SEC company search filtered to that form)."""
    try:
        with sec._lock:
            r = requests.get("https://www.sec.gov/cgi-bin/browse-edgar", params={"company": q, "type": "13F-HR", "dateb": "", "owner": "include",
                             "count": 40, "action": "getcompany", "output": "atom"}, headers=sec._ua(), timeout=30)
            time.sleep(0.15)
        if not r.ok:
            return []
    except Exception:
        return []
    rows = [{**x, "files13f": True} for x in parse_company_feed(r.text)][:10]

    def enrich(x: dict[str, Any]) -> dict[str, Any]:
        """Latest 13F and the quarter it covers, plus stock tickers when the filer is itself listed (e.g. Berkshire)."""
        try:
            sub = sec_events._submissions(x["cik"])
            rec = sub["filings"]["recent"]
            last = next(((d, rd) for f, d, rd in zip(rec["form"], rec["filingDate"], rec.get("reportDate", [""] * len(rec["form"]))) if f == "13F-HR"), None)
            x.update({"tickers": sub.get("tickers") or [], "last13f": last[0] if last else None, "quarter": last[1] if last else None,
                      "name": x["name"] or sub.get("name"), "where": x.get("where") or ""})
        except Exception:
            pass
        return x
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(enrich, rows))
    # Funds still filing first (the stale ones are usually closed or renamed entities)
    rows.sort(key=lambda x: x.get("last13f") or "", reverse=True)
    return rows


def fund_detail(cik: int, name: str | None = None) -> dict[str, Any]:
    """One fund's full 13F: every position with weight, change since last quarter and a ticker where one matches."""
    from . import splc
    if not name:
        try:
            name = sec_events._submissions(cik).get("name") or str(cik)
        except Exception:
            name = str(cik)
    d = fund_quarters({"name": name, "cik": cik})
    if not d:
        return {"ok": False, "error": f"{name} has no 13F-HR filings on SEC."}
    try:
        idx = splc._tickers_by_title()
    except Exception:
        idx = {}

    def tick(issuer: str) -> str | None:
        try:
            return splc.resolve(issuer.title(), idx)[0]
        except Exception:
            return None
    tot = d["total"] or 1.0
    rows = []
    for k, now in d["now"].items():
        ch = change(now, d["prev"].get(k))
        rows.append({"issuer": now["issuer"], "ticker": tick(now["issuer"]), "value": now["value"], "shares": now["shares"],
                     "pct": now["value"] / tot, **ch})
    exits = [{"issuer": p["issuer"], "ticker": tick(p["issuer"]), "prev_value": p["value"], "action": "exited"}
             for k, p in d["prev"].items() if k not in d["now"]]
    rows.sort(key=lambda r: -r["pct"])
    top10 = sum(r["pct"] for r in rows[:10])
    counts = {a: sum(r["action"] == a for r in rows) for a in ("new", "added", "trimmed", "held")}
    counts["exited"] = len(exits)
    return {"ok": True, "name": d["name"], "cik": cik, "quarter": d["quarter"], "prev_quarter": d["prev_quarter"], "filed": d["filed"],
            "total": d["total"], "positions": len(rows), "top10": top10, "counts": counts, "rows": rows,
            "exits": sorted(exits, key=lambda x: -x["prev_value"]),
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR"}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None
