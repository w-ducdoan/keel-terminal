"""SEC filing alerts and the red-flag scan.

- Events: for every held, watchlist and tracked name, read the company's SEC filing list (one request each,
  cached 25 minutes) and turn new filings into events: 8-K items (with what each item means and how serious
  it is), Form 4 insider trades (open-market buys and sales, not grants or option exercises), Form 144
  planned sales, late-filing notices, offerings, activist 13D stakes and new 10-K / 10-Q reports.
- Red flags: read the latest 10-K and 10-Q text and look for warning language (going concern, material
  weakness, restatement, impairment, covenant trouble, investigations), skipping sentences that say the
  opposite ("did not identify any material weakness"). Results say "found in the filing", never a verdict.
"""
from __future__ import annotations

import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

import requests

from . import sec, store

# 8-K items: (what it means, severity). high = could break a thesis; medium = read it; low = routine.
ITEMS = {
    "1.01": ("Material agreement signed", "medium"), "1.02": ("Material agreement ended", "medium"),
    "1.03": ("Bankruptcy or receivership", "high"), "1.05": ("Material cybersecurity incident", "high"),
    "2.01": ("Acquisition or sale of assets completed", "medium"), "2.02": ("Results of operations (earnings)", "low"),
    "2.03": ("New debt or off-balance-sheet obligation", "medium"), "2.04": ("Debt acceleration or default trigger", "high"),
    "2.05": ("Restructuring or exit costs", "medium"), "2.06": ("Material impairment", "high"),
    "3.01": ("Delisting notice or listing rule failure", "high"), "3.02": ("Unregistered share sale", "medium"),
    "3.03": ("Change to shareholder rights", "medium"), "4.01": ("Auditor change", "high"),
    "4.02": ("Prior financials can no longer be relied on (restatement)", "high"), "5.01": ("Change in control", "high"),
    "5.02": ("Director or officer departure / appointment", "medium"), "5.03": ("Bylaw or charter change", "low"),
    "5.05": ("Code of ethics change or waiver", "low"), "5.07": ("Shareholder vote results", "low"),
    "7.01": ("Regulation FD disclosure", "low"), "8.01": ("Other events", "low"), "9.01": ("Exhibits", "low"),
}
OTHER_FORMS = {
    "NT 10-K": ("Late annual report notice", "high"), "NT 10-Q": ("Late quarterly report notice", "high"),
    "SC 13D": ("Activist / 5%+ stake with intent", "medium"), "SC 13D/A": ("Activist stake amended", "low"),
    "S-1": ("Share offering registration", "medium"), "S-3": ("Shelf registration (possible offering)", "low"),
    "424B5": ("Offering prospectus (shares or debt sold)", "medium"), "424B2": ("Offering prospectus", "low"),
    "10-K": ("Annual report filed", "low"), "10-Q": ("Quarterly report filed", "low"),
    "DEF 14A": ("Proxy statement", "low"), "144": ("Planned insider sale notice", "low"),
}
SEV_RANK = {"high": 0, "medium": 1, "low": 2}
_sub_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def _submissions(cik: int) -> dict[str, Any]:
    hit = _sub_cache.get(cik)
    if hit and time.time() - hit[0] < 25 * 60:
        return hit[1]
    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=sec._ua(), timeout=30)
    r.raise_for_status()
    d = r.json()
    _sub_cache[cik] = (time.time(), d)
    time.sleep(0.15)
    return d


def _url(cik: int, acc: str, doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"


def _form4(cik: int, acc: str, doc: str) -> dict[str, Any] | None:
    """Open-market buys and sales in one Form 4 (grants, option exercises and tax withholding are skipped)."""
    cache = sec.CACHE / f"f4_{acc}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raw = doc.split("/")[-1]
    r = requests.get(_url(cik, acc, raw), headers=sec._ua(), timeout=30)
    time.sleep(0.15)
    if r.status_code != 200:
        return None
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return None
    t = lambda node, path: (node.findtext(path) or "").strip()  # noqa: E731
    owner = t(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    title = t(rel, "officerTitle") if rel is not None else ""
    if not title and rel is not None:
        title = "Director" if t(rel, "isDirector") in ("1", "true") else "10% owner" if t(rel, "isTenPercentOwner") in ("1", "true") else ""
    buys = sells = 0.0
    bv = sv = 0.0
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = t(tx, "transactionCoding/transactionCode")
        try:
            sh = float(t(tx, "transactionAmounts/transactionShares/value") or 0)
            px = float(t(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        if code == "P":
            buys += sh
            bv += sh * px
        elif code == "S":
            sells += sh
            sv += sh * px
    out = {"owner": owner.title(), "title": title, "buy_sh": buys, "buy_value": bv, "sell_sh": sells, "sell_value": sv}
    sec.CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def _money(v: float) -> str:
    return f"${v / 1e6:,.1f}M" if v >= 1e6 else f"${v:,.0f}"


def events_for(ticker: str, days: int = 30) -> list[dict[str, Any]]:
    c = sec.cik(ticker)
    if c is None:
        return []
    rec = _submissions(c)["filings"]["recent"]
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for form, fdate, items, acc, doc in zip(rec["form"], rec["filingDate"], rec["items"], rec["accessionNumber"], rec["primaryDocument"]):
        if fdate < cutoff:
            break
        ev = {"acc": acc, "ticker": ticker, "form": form, "date": fdate, "url": _url(c, acc, doc)}
        if form in ("8-K", "8-K/A"):
            codes = [x.strip() for x in (items or "").split(",") if x.strip() and x.strip() != "9.01"]
            if not codes:
                continue
            parts = [ITEMS.get(x, (f"Item {x}", "low")) for x in codes]
            ev["severity"] = min((p[1] for p in parts), key=lambda s: SEV_RANK[s])
            ev["title"] = "; ".join(f"{p[0]} ({x})" for p, x in zip(parts, codes))
            ev["items"] = codes
        elif form == "4":
            f4 = _form4(c, acc, doc)
            if not f4 or (not f4["buy_sh"] and not f4["sell_sh"]):
                continue  # grants, option exercises, tax withholding: not a decision signal
            who = f"{f4['owner']}{' (' + f4['title'] + ')' if f4['title'] else ''}"
            if f4["buy_sh"]:
                ev["severity"] = "medium" if f4["buy_value"] >= 10_000 else "low"  # a few shares (often a dividend plan) is noise
                ev["title"] = f"Insider BUY: {who} bought {f4['buy_sh']:,.0f} sh ({_money(f4['buy_value'])}) on the open market"
                ev["kind"] = "insider_buy"
            else:
                big = f4["sell_value"] >= 1e6 or re.search(r"chief executive|ceo|chief financial|cfo|president", f4["title"], re.I)
                ev["severity"] = "medium" if big else "low"
                ev["title"] = f"Insider sale: {who} sold {f4['sell_sh']:,.0f} sh ({_money(f4['sell_value'])})"
                ev["kind"] = "insider_sell"
        elif form in OTHER_FORMS:
            ev["title"], ev["severity"] = OTHER_FORMS[form]
        else:
            continue
        out.append(ev)
    return out


def poll(tickers: list[str]) -> list[dict[str, Any]]:
    """Check every name; store new events; return the ones not seen before."""
    new = []
    for t in sorted(set(tickers)):
        try:
            for ev in events_for(t):
                if store.add_sec_event(ev):
                    new.append(ev)
        except sec.NoContact:
            raise
        except Exception:
            continue  # one company failing never stops the others
    return new


# ---------- red-flag scan ----------
FLAGS = [
    ("Going concern doubt", "high", re.compile(r"substantial doubt[^.]{0,160}going concern|going concern[^.]{0,160}substantial doubt", re.I),
     re.compile(r"\b(no|not|alleviat\w*)\b[^.]{0,60}substantial doubt|substantial doubt[^.]{0,40}(has been|was|were) alleviated", re.I)),
    # Needs an actual finding ("we identified a material weakness", "controls were not effective"), not the
    # auditor's standard description of what an audit tests or the textbook definition of the term.
    ("Material weakness in internal controls", "high",
     re.compile(r"(identified|discovered|disclosed|existence of)\s+(a |two |several |certain )?material weakness(es)?|"
                r"material weakness(es)?[^.]{0,60}(was|were|has been|have been) identified|"
                r"(internal control over financial reporting|disclosure controls and procedures) (was|were) not effective", re.I),
     re.compile(r"(did not identify|have not identified|has not identified|no material weakness|not identified any|absence of)|"
                r"risk that a material weakness|a material weakness is a deficiency|if we (identify|fail)|could (identify|result)|may (identify|be)|in the future", re.I)),
    ("Restatement of past financials", "high", re.compile(r"restatement of (our |the )?(previously issued|prior period|historical)|restated (our |the )?(previously issued|consolidated) financial", re.I),
     re.compile(r"\b(no|not|without)\b[^.]{0,30}restat|if we (are required to )?restate|could (be required to )?restate", re.I)),
    # Needs an actual breach or waiver, not the credit agreement's list of what would count as a default.
    ("Covenant trouble", "high",
     re.compile(r"(were|was|are|is) not in compliance with[^.]{0,80}covenant|(obtained|received|entered into|requested)[^.]{0,40}(a |an )?(waiver|forbearance)[^.]{0,100}covenant|"
                r"(breached|violated|were in breach of|was in breach of)[^.]{0,60}covenant", re.I),
     re.compile(r"(were|was|are|is|remain(ed)?) in compliance with (all|each|the)[^.]{0,40}covenant|if we (fail|are unable) to comply|events? of default|could|may", re.I)),
    ("Goodwill or asset impairment charge", "medium", re.compile(r"(recorded|recognized|recognised|incurred)[^.]{0,60}(goodwill|intangible|asset)[^.]{0,30}impairment|(goodwill|intangible asset) impairment (charge|loss)[^.]{0,40}\$\s?[\d.,]+", re.I),
     re.compile(r"\bno\b[^.]{0,40}impairment|(did not|have not|has not|not) (record|recognize|recognise)\w*[^.]{0,40}impairment|not impaired|"
                r"there (was|were) no[^.]{0,40}impairment", re.I)),
    ("Government investigation or subpoena", "medium", re.compile(r"\b(subpoena|Wells notice|civil investigative demand|grand jury)\b", re.I), None),
    ("Customer concentration", "low", re.compile(r"(one|two|three|single|largest|a) customers?\b[^.]{0,100}\b\d{1,2}(\.\d)?\s?%\s?of (our )?(total |net |consolidated )?(revenue|net sales|sales)", re.I),
     re.compile(r"(top|largest) \d+ customers|\d+ largest customers|less than \d+(\.\d)?\s?%", re.I)),
]


def _text(cik: int, acc: str, doc: str) -> str:
    cache = sec.CACHE / f"txt_{acc}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    for attempt in range(3):
        with sec._lock:  # shared with every other SEC call: stays under SEC's 10 requests a second
            r = requests.get(_url(cik, acc, doc), headers=sec._ua(), timeout=60)
            time.sleep(0.15)
        if r.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))  # throttled: back off and retry instead of dropping the filing
    r.raise_for_status()
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(t))
    sec.CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(t, encoding="utf-8")
    return t


def scan_text(text: str) -> list[dict[str, Any]]:
    out = []
    sentences = re.split(r"(?<=[.;])\s+(?=[A-Z])", text)
    for label, sev, pat, neg in FLAGS:
        hits = []
        for s in sentences:
            if len(s) > 1500 or not pat.search(s):
                continue
            if neg and neg.search(s):
                continue
            m = pat.search(s)  # show the words around the match, not the start of a long table-text chunk
            a, b = max(0, m.start() - 140), min(len(s), m.end() + 180)
            hits.append(("..." if a else "") + s[a:b].strip() + ("..." if b < len(s) else ""))
            if len(hits) >= 3:
                break
        if hits:
            out.append({"flag": label, "severity": sev, "count": len(hits), "snippets": hits})
    return out


def red_flags(ticker: str) -> dict[str, Any]:
    c = sec.cik(ticker)
    if c is None:
        return {"ok": False, "error": f"{ticker} has no SEC filings (foreign filer, fund or not SEC-registered)."}
    rec = _submissions(c)["filings"]["recent"]
    picked: dict[str, tuple[str, str, str]] = {}
    for form, fdate, acc, doc in zip(rec["form"], rec["filingDate"], rec["accessionNumber"], rec["primaryDocument"]):
        if form in ("10-K", "10-Q") and form not in picked:
            picked[form] = (fdate, acc, doc)
        if len(picked) == 2:
            break
    reports = []
    for form, (fdate, acc, doc) in sorted(picked.items(), key=lambda kv: kv[1][0], reverse=True):
        try:
            found = scan_text(_text(c, acc, doc))
        except Exception as e:
            reports.append({"form": form, "date": fdate, "error": str(e)[:120], "flags": []})
            continue
        reports.append({"form": form, "date": fdate, "url": _url(c, acc, doc), "flags": found})
    worst = min((f["severity"] for r in reports for f in r["flags"]), key=lambda s: SEV_RANK[s], default=None)
    return {"ok": True, "ticker": ticker, "reports": reports, "worst": worst,
            "note": "Words found in the filing text, with sentences that say the opposite skipped. Read the snippet before acting."}
