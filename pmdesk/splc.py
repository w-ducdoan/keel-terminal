"""Supplier / customer map (SPLC) from public filings, free.

Three sources, each link labeled with where it came from:
1. The company's own latest 10-K: customers named with a share of revenue ("Walmart accounted for 18% of net
   sales"), unnamed concentration ("one customer accounted for 31%"), and suppliers it names.
2. Other companies' 10-Ks that mention it (SEC full-text search): the sentence around each mention decides
   whether that filer is a customer, a supplier, a competitor or just a partner / mention.
3. your own links (stored in Keel).
Coverage is partial by nature: many companies call their biggest customer "Customer A". Nothing is guessed:
a link the text doesn't support is shown as a plain mention.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import requests

from . import sec, sec_events, store

LEGAL = re.compile(r"[,.]?\s+(incorporated|inc|corp|corporation|co|company|ltd|limited|plc|llc|lp|n\.?v|s\.?a|ag|se|holdings?|group)\.?$", re.I)
NAME = r"([A-Z][A-Za-z0-9&'\-]+(?:[ .,&'\-]+[A-Z][A-Za-z0-9&'\-]+){0,5})"
CUST_PCT = re.compile(NAME + r",?\s+(?:accounted|represented|comprised|made up)\s+(?:for\s+)?(?:approximately\s+|about\s+|roughly\s+)?(\d{1,2}(?:\.\d)?)\s?%\s+of\s+(?:our\s+|the\s+company's\s+)?(?:total\s+|net\s+|consolidated\s+)?(?:revenue|revenues|net sales|sales)")
UNNAMED_PCT = re.compile(r"(one|two|three|a single|our largest|the largest|customer [A-Z])\s+customers?\b[^.]{0,80}?(\d{1,2}(?:\.\d)?)\s?%\s+of\s+(?:our\s+)?(?:total\s+|net\s+|consolidated\s+)?(?:revenue|revenues|net sales|sales)", re.I)
# Sentence-level customer concentration: "... one end customer, Apple Inc., ... represented approximately 91 percent, 89 percent,
# and 87 percent, of the Company's total net sales" or "Walmart accounted for 18% of net sales".
CONC_VERB = re.compile(r"\b(account(s|ed)?|represent(s|ed)?|comprise(s|d)?|make(s)? up|made up|constitute(s|d)?)\b", re.I)
# Inline XBRL tags in the filing name concentrated customers: "us-gaap:CustomerConcentrationRiskMember pg:WalmartInc.AndAffiliatesMember"
XBRL_CUSTOMER = re.compile(r"CustomerConcentrationRiskMember\s+[a-z\-]+:([A-Za-z0-9.]+?)Member\b")
PCT_OF_SALES = re.compile(r"(\d{1,2}(?:\.\d)?)\s?(?:%|percent)(?:[^.]{0,60}?)\s+of\s+(?:our\s+|the\s+company.s\s+|its\s+)?(?:total\s+|net\s+|consolidated\s+)*(?:revenue|revenues|net sales|sales)", re.I)
CONC_SKIP = re.compile(r"\b(top|largest) (\d+|two|three|five|ten|twenty)\b|\b(ten|five|twenty) largest\b|international|outside (the|of)|foreign|no other customer|"
                       r"(segment|product|region|country|geograph)|\b(increase|decrease|growth|grew|declined|expense|expenses|united states|domestic|backlog)\b|"
                       r"\$\s?[\d.,]+\s?(million|billion)|\b\d+ largest\b|less than \d|\bacqui\w*|subsidiar\w*|united kingdom|\bu\.k\.|british|europe|"
                       r"internal control|total assets", re.I)
# Common words that must never match a company on their own ("Selling, General and Administrative" is not GE)
COMMON = {"general", "research", "united", "states", "services", "solutions", "health", "energy", "global", "national", "american",
          "international", "capital", "financial", "data", "systems", "technologies", "technology", "medical", "business", "commercial",
          "total", "net", "revenue", "product", "products", "operating", "company", "customers", "customer", "group", "holdings",
          "industries", "resources", "partners", "management", "development", "clinical", "network", "networks", "power", "water"}
UNNAMED = re.compile(r"\b(one|a single|our largest|the largest|one end|customer [A-Z])\s+(?:end\s+)?customers?\b", re.I)
LEGAL_NAME = re.compile(r"\b([A-Z][A-Za-z0-9&'\-]+(?:\s+[A-Z][A-Za-z0-9&'\-]+){0,4},?\s+(?:Inc|Corp|Corporation|Co|Company|Ltd|plc|LLC|N\.V|S\.A|AG|SE)\.?)", re.UNICODE)
CAP_PHRASE = re.compile(r"\b([A-Z][A-Za-z0-9&'\-]{2,}(?:\s+[A-Z][A-Za-z0-9&'\-]+){0,3})\b")
SUPPLIERS_LIST = re.compile(r"(?:suppliers|vendors|manufacturers|foundr(?:y|ies)) (?:include|such as|including)\s+([^.;:]{5,240})", re.I)
CUSTOMERS_LIST = re.compile(r"customers (?:include|such as|including)\s+([^.;:]{5,240})", re.I)

# Words near a mention that say what the relationship is, from the mentioning company's point of view.
AS_SUPPLIER = re.compile(r"\b(supplier|suppliers|vendor|vendors|purchase[sd]? from|sourced from|supplied by|manufactured by|license[sd]? from|rely on|depend on|provided by|"
                         r"we (use|utilize|license)|our (third-party )?(provider|platform|processor|software provider))\b", re.I)
# Mentions that say nothing about a business relationship: executive bios, pay peer groups, index lists, boilerplate.
NOISE = re.compile(r"\b(served|serves|serving) (as|on|in)|held (roles|positions)|previously (served|was|worked)|vice president|chief \w+ officer of|"
                   r"\b(he|she) (was|is|has|served|worked|joined)\b|worked (in|at|for)|positions (at|for|with)|senior executive|as (svp|evp|ceo|cfo)\b|"
                   r"\bmr\.|\bms\.|\bdr\.|"
                   r"board of directors|director of|peer group|compensation (peer|committee)|index (includes|consists)|"
                   r"the following companies|formerly known|currently known|acquisition corp", re.I)
AS_CUSTOMER = re.compile(r"\b(customer|customers|client|clients|sales to|sell to|sold to|revenue from|contract with|accounted for|licensee|licensees)\b", re.I)
AS_COMPETITOR = re.compile(r"\b(compete|competes|competitor|competitors|competition|competitive|rivals?|market share)\b|\bnon-[A-Z]", re.I)
# Capital deals are not supply relationships ("we issued and sold to NVIDIA 2,000,000 shares")
AS_INVESTMENT = re.compile(r"\b(shares|common stock|preferred stock|warrants?|convertible|equity investment|invested|investor|stake)\b", re.I)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def short_name(entity: str) -> str:
    """'IQVIA HOLDINGS INC.' -> 'IQVIA Holdings' style search phrase without the legal suffix."""
    n = entity.strip()
    for _ in range(2):
        n2 = LEGAL.sub("", n).strip(" ,.")
        if n2 == n:
            break
        n = n2
    n = " ".join(w if (len(w) <= 4 and w.isupper()) else w.title() for w in n.split()) if n.isupper() else n
    return n


def _tickers_by_title() -> dict[str, tuple[str, int]]:
    data = sec._get_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json", 7 * 86400) or {}
    out = {}
    for v in data.values():
        key = re.sub(r"[^a-z0-9 ]", "", LEGAL.sub("", v["title"].lower())).strip()
        out.setdefault(key, (v["ticker"], int(v["cik_str"])))
    return out


def resolve(name: str, idx: dict[str, tuple[str, int]], exact: bool = False) -> tuple[str | None, int | None]:
    """Name -> (ticker, cik). exact=True only accepts the full name (used for plain capitalized words in prose)."""
    key = re.sub(r"[^a-z0-9 ]", "", LEGAL.sub("", name.lower())).strip()
    if not key or all(w in COMMON for w in key.split()):
        return None, None
    if key in idx:
        return idx[key]
    if exact:
        return None, None
    words = key.split()
    for n in range(len(words), 0, -1):  # "walmart stores" -> "walmart"; whole words only, never a common word alone
        sub = " ".join(words[:n])
        if len(sub) < 5 or (n == 1 and sub in COMMON):
            continue
        if sub in idx:
            return idx[sub]
        for k, v in idx.items():
            if k.startswith(sub + " ") and n >= 1 and len(sub) >= 5:
                return v
    return None, None


def _own_10k(cik: int) -> tuple[str, str, str] | None:
    rec = sec_events._submissions(cik)["filings"]["recent"]
    for form, fdate, acc, doc in zip(rec["form"], rec["filingDate"], rec["accessionNumber"], rec["primaryDocument"]):
        if form in ("10-K", "20-F", "40-F"):
            return fdate, acc, doc
    return None


def _split_names(s: str) -> list[str]:
    s = re.sub(r"\(.*?\)", "", s)
    parts = re.split(r",\s*|\s+and\s+|\s+or\s+", s)
    out = []
    for p in parts:
        p = p.strip(" .")
        if 2 < len(p) <= 60 and p[0].isupper() and not re.match(r"(the|our|other|many|various|certain|large|leading|major)\b", p, re.I):
            out.append(p)
    return out[:12]


def own_links(cik: int, idx: dict[str, tuple[str, int]]) -> tuple[list[dict[str, Any]], str | None]:
    f = _own_10k(cik)
    if not f:
        return [], None
    fdate, acc, doc = f
    url = sec_events._url(cik, acc, doc)
    text = sec_events._text(cik, acc, doc)
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", text):
        if len(s) > 900 or not CONC_VERB.search(s):
            continue
        pm = PCT_OF_SALES.search(s)
        if not pm or CONC_SKIP.search(s):
            continue
        pct = float(pm.group(1))
        names = []
        legal = LEGAL_NAME.findall(s)
        for cand in legal + CAP_PHRASE.findall(s):
            cand = cand.strip(" ,")
            if re.match(r"(The|Our|We|For|In|No|Each|Company|Customer|Sales|Revenue|Fiscal)\b", cand) or len(cand) < 4:
                continue
            t, c = resolve(cand, idx, exact=cand not in legal)
            if t or LEGAL_NAME.fullmatch(cand):
                names.append((cand, t, c))
        snippet = s.strip()[:340]
        if names:
            for name, t, c in names[:2]:
                k = (t or name).lower()
                if k in seen:
                    continue
                seen.add(k)
                links.append({"name": name, "ticker": t, "cik": c, "relation": "customer", "pct": pct, "source": "own 10-K",
                              "confidence": "high", "snippet": snippet, "url": url, "date": fdate})
        elif UNNAMED.search(s):
            label = f"Unnamed customer ({UNNAMED.search(s).group(1).lower()})"
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            links.append({"name": label, "ticker": None, "cik": None, "relation": "customer", "pct": pct, "source": "own 10-K",
                          "confidence": "high", "snippet": snippet, "url": url, "date": fdate, "unnamed": True})
    # Named customers from the filing's own XBRL tags (no percentage here; the sentence pass above adds it when it can)
    for raw in dict.fromkeys(XBRL_CUSTOMER.findall(text)):
        if re.match(r"(Customer|Major|One|Two|Three|Other|Largest|Top)", raw) or re.search(r"(Customers?|Distributors?)(One|Two|A|B|C)?$", raw):
            continue  # "CustomerA", "MajorCustomerOne": unnamed
        name = re.sub(r"(?<=[a-z])(?=[A-Z])|\.(?=[A-Z])", " ", raw).replace(" And Affiliates", "").replace("And Affiliates", "").strip(" .")
        if all(w.lower() in COMMON | {"accounts", "receivable", "sales", "revenue", "net", "benchmark", "total", "segment"} for w in name.split()):
            continue  # a generic tag ("Company", "Accounts Receivable"), not a customer name
        t, c = resolve(name, idx)
        k = (t or name).lower()
        if k in seen or any((x.get("ticker") or "").lower() == k for x in links):
            continue
        seen.add(k)
        links.append({"name": name, "ticker": t, "cik": c, "relation": "customer", "pct": None, "source": "own 10-K (XBRL tag)",
                      "confidence": "high", "snippet": f"Tagged as a customer concentration in the filing: {raw}", "url": url, "date": fdate})
    for rx, rel in ((SUPPLIERS_LIST, "supplier"), (CUSTOMERS_LIST, "customer")):
        for m in rx.finditer(text):
            for name in _split_names(m.group(1)):
                t, c = resolve(name, idx)
                if not t:
                    continue  # only names that resolve to a listed company; free text lists are too loose otherwise
                k = t.lower()
                if k in seen:
                    continue
                seen.add(k)
                links.append({"name": name, "ticker": t, "cik": c, "relation": rel, "pct": None, "source": "own 10-K",
                              "confidence": "medium", "snippet": text[max(0, m.start() - 40): m.end() + 20][:300], "url": url, "date": fdate})
    return links, url


SHORT_WORDS = {"on", "at", "it", "us", "one", "all", "box", "key", "arm", "net", "hub", "fox", "gap", "ford", "target", "block",
               "match", "snap", "apple", "visa", "shell", "ball", "crown", "dover", "ross", "toast", "on"}


def search_phrase(entity: str) -> str:
    """How other filers write the name. 'NVIDIA CORP' -> 'Nvidia'; 'On Holding AG' -> 'On Holding' (a bare 'On'
    is an English word); 'EMCOR Group, Inc.' -> 'Emcor'. Never the SEC's formal filer name, which others rarely use."""
    short = short_name(entity)
    first = short.split()[0] if short else entity
    if len(short.split()) == 1 and (len(short) < 4 or short.lower() in SHORT_WORDS | COMMON):
        # Too generic alone: keep the next word of the legal name ("On Holding", "Target Corporation")
        words = entity.strip().rstrip(".").replace(",", "").split()
        return " ".join(w if not w.isupper() or len(w) <= 4 else w.title() for w in words[:2])
    if len(short.split()) >= 2 and first.lower() not in COMMON | SHORT_WORDS and len(first) >= 5 and short.split()[1].lower() in (
            "group", "holdings", "industries", "technologies", "corporation", "systems", "brands", "international", "worldwide"):
        return first  # "Emcor Group" -> "Emcor", "Valmont Industries" -> "Valmont"
    return short


def name_pattern(phrase: str) -> re.Pattern:
    """How the name must appear in a sentence: each word capitalized as companies are written (so "On Holding"
    matches but "rely on holding" does not), the rest of each word in any case ("NVIDIA" or "Nvidia"), and
    punctuation-tolerant ("Toast Inc" matches "Toast, Inc.")."""
    parts = []
    for w in phrase.split():
        head, rest = w[0], w[1:]
        parts.append((re.escape(head.upper()) if head.isalpha() else re.escape(head)) + (f"(?i:{re.escape(rest)})" if rest else ""))
    return re.compile(r"\b" + r"[\s,.]+".join(parts) + r"\b")


def _classify(sentence: str) -> str:
    # From the mentioning filer's point of view: "X is our supplier" means X supplies the filer.
    if AS_COMPETITOR.search(sentence):
        return "competitor"
    if AS_INVESTMENT.search(sentence):
        return "mention"
    sup, cus = AS_SUPPLIER.search(sentence), AS_CUSTOMER.search(sentence)
    if sup and not cus:
        return "filer_buys"      # the target supplies the filer -> filer is the target's CUSTOMER
    if cus and not sup:
        return "filer_sells"     # the target buys from the filer -> filer is the target's SUPPLIER
    return "mention"


# Annual and quarterly reports, US and foreign filers. 8-Ks were tried: press-release wording ("secures contract
# for NVIDIA GPUs") is too loose for keyword rules and filled the chart with SPACs and resellers pointing the wrong way.
SEARCH_FORMS = "10-K,20-F,40-F,10-Q"
# Filers that are not operating companies: securitization trusts, blank-check shells, funds
NOT_OPERATING = re.compile(r"\b(trust|mortgage|acquisition corp|acquisition co|spac|fund|etf|receivables|funding|"
                           r"securitization|pass[- ]through|blank check)\b", re.I)


class SearchFailed(RuntimeError):
    """SEC full-text search refused or timed out; the result must not be cached as 'no links'."""


def _efts(phrase: str, start: str, offset: int) -> list[dict[str, Any]]:
    """One page of SEC full-text search, retried when SEC throttles (429) or hiccups (5xx)."""
    last = None
    for attempt in range(4):
        try:
            r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"q": f'"{phrase}"', "forms": SEARCH_FORMS,
                             "dateRange": "custom", "startdt": start, "enddt": date.today().isoformat(), "from": offset},
                             headers=sec._ua(), timeout=30)
            if r.status_code == 200:
                return r.json().get("hits", {}).get("hits", [])
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:80]
        time.sleep(1.5 * (attempt + 1))
    raise SearchFailed(last or "no response")


def mentions(entity: str, own_cik: int, max_filers: int = 40) -> list[dict[str, Any]]:
    """Other companies' recent filings that name this company, with the relationship read from the sentence.
    Raises SearchFailed if the first page cannot be read (so an outage is never cached as 'nothing found')."""
    phrase = search_phrase(entity)
    start = (date.today() - timedelta(days=730)).isoformat()
    hits: list[dict[str, Any]] = []
    for page in range(3):  # SEC full-text search returns 100 hits a page
        try:
            got = _efts(phrase, start, page * 100)
        except SearchFailed:
            if page == 0:
                raise
            break  # later pages are a bonus
        hits += got
        if len(got) < 100:
            break
    filers: dict[int, dict[str, Any]] = {}
    for h in hits:
        s = h.get("_source", {})
        ciks = [int(c) for c in s.get("ciks", []) if str(c).isdigit()]
        if not ciks or ciks[0] == own_cik or ciks[0] in filers:
            continue
        acc, doc = h["_id"].split(":", 1)
        form = s.get("form") or s.get("root_form") or ""
        if re.search(r"ex\d|exhibit", doc, re.I):
            continue  # exhibits are subsidiary lists, consents and certifications
        if NOT_OPERATING.search((s.get("display_names") or [""])[0]):
            continue
        name = (s.get("display_names") or [""])[0]
        m = re.search(r"\(([A-Z.\-]{1,6})\)", name)
        filers[ciks[0]] = {"cik": ciks[0], "acc": acc, "doc": doc, "date": s.get("file_date"), "form": form,
                           "name": re.sub(r"\s*\(.*$", "", name).title(), "ticker": m.group(1) if m else None}
        if len(filers) >= max_filers:
            break
    rx = name_pattern(phrase)

    def one(f: dict[str, Any]) -> dict[str, Any] | None:
        try:
            text = sec_events._text(f["cik"], f["acc"], f["doc"])
        except Exception:
            return None
        sents = [s for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", text) if rx.search(s) and len(s) < 1200 and not NOISE.search(s)]
        if not sents:
            return None  # only bios, peer groups or index lists: not a business relationship
        votes = [_classify(s) for s in sents]
        best = next((k for k in ("filer_buys", "filer_sells", "competitor") if k in votes), "mention")
        snip = next(s for s, v in zip(sents, votes) if v == best)
        rel = {"filer_buys": "customer", "filer_sells": "supplier", "competitor": "competitor", "mention": "mention"}[best]
        return {**{k: f[k] for k in ("name", "ticker", "cik", "date")}, "relation": rel, "pct": None, "source": f"their {f.get('form') or 'filing'}",
                "confidence": "medium" if rel in ("customer", "supplier") else "low", "snippet": snip[:320],
                "url": sec_events._url(f["cik"], f["acc"], f["doc"]), "mentions": len(sents)}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return [x for x in ex.map(one, filers.values()) if x]


def build(ticker: str, fresh: bool = False) -> dict[str, Any]:
    t = ticker.upper()
    hit = _cache.get(t)
    if hit and not fresh and time.time() - hit[0] < 7 * 86400:
        return {**hit[1], "manual": store.supply_links(t)}
    cik = sec.cik(t)
    if cik is None:
        return {"ok": False, "error": f"{t} has no SEC filings (foreign filer, fund or not SEC-registered)."}
    cache = sec.CACHE / f"splc_{t}.json"
    if cache.exists() and not fresh and time.time() - cache.stat().st_mtime < 7 * 86400:
        out = json.loads(cache.read_text(encoding="utf-8"))
        _cache[t] = (time.time(), out)
        return {**out, "manual": store.supply_links(t)}
    entity = (sec_events._submissions(cik).get("name") or t)
    idx = _tickers_by_title()
    own, own_url = own_links(cik, idx)
    try:
        theirs, search_error = mentions(entity, cik), None
    except SearchFailed as e:
        theirs, search_error = [], f"SEC full-text search failed ({e}); showing this company's own filing only. Reload to retry."
    # Merge: a company found both ways keeps the stronger source
    links: dict[str, dict[str, Any]] = {}
    for x in own + theirs:
        k = (x.get("ticker") or x["name"]).upper()
        if k == t:
            continue
        cur = links.get(k)
        if not cur or (cur["confidence"] != "high" and x["confidence"] == "high") or (cur["relation"] == "mention" and x["relation"] != "mention"):
            links[k] = x
    out = {"ok": True, "ticker": t, "entity": entity, "search_name": search_phrase(entity), "own_10k": own_url,
           "links": sorted(links.values(), key=lambda x: (-(x.get("pct") or 0), x["relation"], x["name"])),
           "built": date.today().isoformat(), "search_error": search_error}
    if not search_error:  # never cache an outage as "no links" for a week
        sec.CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out), encoding="utf-8")
        _cache[t] = (time.time(), out)
    return {**out, "manual": store.supply_links(t)}
