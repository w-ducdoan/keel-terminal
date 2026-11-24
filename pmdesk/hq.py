"""Read-only parsers for the research folder markdown files.

The files are written by hand, so every parser is defensive: a row it can't
read is reported in `warnings` rather than guessed.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TICKER_RE = re.compile(r"^[A-Z][A-Z.]{0,5}$")
MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
STOPWORDS = {"AI", "ETF", "US", "CEO", "CFO", "EPS", "FCF", "GDP", "CPI", "PCE", "FOMC", "SEC", "IPO", "YOY", "NAV", "BDC"}


# ---------- markdown helpers ----------

def clean(text: str) -> str:
    """Strip bold, wiki links and markdown links down to plain text."""
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip()


def first_link(text: str) -> str | None:
    m = re.search(r"\]\(([^)]+\.md)\)", text)
    return m.group(1) if m else None


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_separator(line: str) -> bool:
    return bool(re.match(r"^\|?\s*:?-{2,}", line.strip()))


def parse_tables(lines: list[str]) -> list[dict[str, Any]]:
    """Return tables as {'header': [...], 'rows': [[raw cells]...]}."""
    tables: list[dict[str, Any]] = []
    i = 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and is_separator(lines[i + 1]):
            header = [clean(h) for h in split_row(lines[i])]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append(split_row(lines[j]))
                j += 1
            tables.append({"header": header, "rows": rows, "line": i + 1})
            i = j
        else:
            i += 1
    return tables


def section(text: str, heading: str, level: int = 2) -> list[str]:
    """Lines under a heading that starts with `heading`, up to the next heading of the same level."""
    marker = "#" * level + " "
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith(marker):
            if inside:
                break
            inside = line[len(marker):].strip().startswith(heading)
            continue
        if inside:
            out.append(line)
    return out


def to_float(text: str) -> float | None:
    m = NUM_RE.search(text.replace("~", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def ticker_from_cell(cell: str) -> str | None:
    t = clean(cell).split(" ")[0].strip()
    return t if TICKER_RE.match(t) else None


def resolve(base_dir: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    try:
        return str((base_dir / rel).resolve())
    except OSError:
        return None


SECTOR_MAP = {
    "technology": "Information Technology",
    "information technology": "Information Technology",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "industrials": "Industrials",
    "utilities": "Utilities",
    "energy": "Energy",
    "financials": "Financials",
    "consumer discretionary": "Consumer Discretionary",
    "consumer staples": "Consumer Staples",
    "communication services": "Communication Services",
    "materials": "Materials",
    "real estate": "Real Estate",
}


def sector_from_category(category: str) -> tuple[str, str]:
    """Map 'Equity - Healthcare' style text to (gics_sector, sleeve)."""
    c = category.lower()
    if c.startswith("index fund"):
        return "ETF", "etf"
    if c.startswith("diversifier"):
        return "Hedge", "hedge"
    if c.startswith("income"):
        return "Financials", "income"
    rest = category.split("-", 1)[1] if "-" in category else category
    first = re.split(r"[/(]", rest)[0].strip().lower()
    return SECTOR_MAP.get(first, first.title() or "Unclassified"), "core"


# ---------- portfolio.md ----------

def parse_portfolio(hq: Path) -> dict[str, Any]:
    path = hq / "portfolio" / "portfolio.md"
    text = path.read_text(encoding="utf-8")
    warnings: list[str] = []
    holdings: list[dict[str, Any]] = []

    tables = parse_tables(section(text, "Holdings"))
    if not tables:
        warnings.append("portfolio.md: Holdings table not found")
    else:
        t = tables[0]
        idx = {h.lower(): k for k, h in enumerate(t["header"])}
        for raw in t["rows"]:
            def cell(name: str) -> str:
                k = idx.get(name)
                return raw[k] if k is not None and k < len(raw) else ""

            ticker = ticker_from_cell(cell("ticker"))
            if not ticker:
                warnings.append(f"portfolio.md: skipped row '{clean(cell('ticker'))[:30]}'")
                continue
            shares = to_float(cell("shares"))
            avg_cost = None
            m = MONEY_RE.search(cell("avg cost"))
            if m:
                avg_cost = float(m.group(1).replace(",", ""))
            category = clean(cell("category"))
            sector, sleeve = sector_from_category(category)
            holdings.append({
                "ticker": ticker,
                "company": clean(cell("company")),
                "category": category,
                "sector": sector,
                "sleeve": sleeve,
                "shares": shares,
                "avg_cost": avg_cost,
                "thesis_path": resolve(path.parent, first_link(cell("ticker"))),
                "thesis_status": clean(cell("thesis status"))[:400],
                "action_flag": clean(cell("action flag"))[:600],
            })

    buckets: dict[str, str] = {}
    held = {h["ticker"] for h in holdings}
    for t in parse_tables(section(text, "Exposure Buckets")):
        if "exposure bucket" not in [h.lower() for h in t["header"]]:
            continue
        for raw in t["rows"]:
            if len(raw) < 2:
                continue
            bucket = clean(raw[0])
            names = re.split(r"\(", clean(raw[1]))[0]
            for tok in re.findall(r"\b[A-Z][A-Z.]{0,5}\b", names):
                if tok in held:
                    buckets[tok] = bucket

    cash = None
    m = re.search(r"Cash re-estimated at ~?\$([\d,]+(?:\.\d+)?)", text)
    if m:
        cash = float(m.group(1).replace(",", ""))

    return {"holdings": holdings, "buckets": buckets, "cash_estimate": cash,
            "path": str(path), "warnings": warnings}


# ---------- watchlist.md ----------

BUY_BELOW_RE = re.compile(r"[Bb]uy-below\s*~?\s*\$\s?([\d,]+(?:\.\d+)?)")
STARTER_RE = re.compile(r"Starter\s*(\d+(?:\.\d+)?)\s*%")
BASE_RE = re.compile(r"Base\s*~?\$([\d,]+(?:\.\d+)?)")


def parse_watchlist(hq: Path) -> dict[str, Any]:
    path = hq / "portfolio" / "watchlist.md"
    text = path.read_text(encoding="utf-8")
    lines = section(text, "Active Watchlist")
    warnings: list[str] = []
    names: dict[str, dict[str, Any]] = {}

    for t in parse_tables(lines):
        header = [h.lower() for h in t["header"]]
        for raw in t["rows"]:
            ticker = ticker_from_cell(raw[0]) if raw else None
            if not ticker:
                continue
            row = {h: clean(raw[k]) if k < len(raw) else "" for k, h in enumerate(header)}
            entry = names.setdefault(ticker, {
                "ticker": ticker, "company": "", "sector": "", "entry_criteria": "",
                "fair_value": "", "next_catalyst": "", "notes": [], "buy_below": None,
                "starter_pct": None, "base_value": None, "price_ref": None,
                "thesis_path": resolve(path.parent, first_link(raw[0])), "flags": [],
            })
            entry["company"] = row.get("company") or entry["company"]
            entry["sector"] = row.get("sector") or entry["sector"]
            if row.get("entry criteria"):
                entry["entry_criteria"] = row["entry criteria"]
            if row.get("fair value est."):
                entry["fair_value"] = row["fair value est."]
            if row.get("next catalyst"):
                entry["next_catalyst"] = row["next catalyst"]
            if row.get("current action and next test"):
                entry["notes"].append(row["current action and next test"])
            price_text = row.get("current price") or row.get("close") or ""
            if price_text and entry["price_ref"] is None:
                entry["price_ref"] = to_float(price_text.split("(")[0])
            whole = " ".join(clean(c) for c in raw)
            m = BUY_BELOW_RE.search(entry["entry_criteria"]) or BUY_BELOW_RE.search(whole)
            if m:
                entry["buy_below"] = float(m.group(1).replace(",", ""))
            m = STARTER_RE.search(entry["entry_criteria"]) or STARTER_RE.search(whole)
            if m:
                entry["starter_pct"] = float(m.group(1))
            m = BASE_RE.search(entry["fair_value"])
            if m:
                entry["base_value"] = float(m.group(1).replace(",", ""))

    # Dated notes above the tables ("> **2026-11-11 NVDA (...)... Buy-below $137.24",
    # "GEV (Buy-below ~$660), MTZ (~$164)"). Only fill names with no table trigger.
    for line in lines:
        if not line.startswith(">") or "uy-below" not in line:
            continue
        plain = clean(line)
        found: dict[str, float] = {}
        for tok, px in re.findall(r"\b([A-Z]{2,5}) \((?:Buy-below )?~?\$([\d,]+(?:\.\d+)?)\)", plain):
            found[tok] = float(px.replace(",", ""))
        m = re.match(r">\s*(\d{4}-\d{2}-\d{2}) ([A-Z]{2,5})\b", plain)
        bb = BUY_BELOW_RE.search(plain)
        if m and bb and m.group(2) not in found and m.group(2) not in STOPWORDS:
            found[m.group(2)] = float(bb.group(1).replace(",", ""))
        for tok, px in found.items():
            entry = names.setdefault(tok, {
                "ticker": tok, "company": "", "sector": "", "entry_criteria": "",
                "fair_value": "", "next_catalyst": "", "notes": [], "buy_below": None,
                "starter_pct": None, "base_value": None, "price_ref": None,
                "thesis_path": None, "flags": [],
            })
            if entry["buy_below"] is None:
                entry["buy_below"] = px
                entry["notes"].append(plain[:400])
                sm = STARTER_RE.search(plain)
                if sm and entry["starter_pct"] is None:
                    entry["starter_pct"] = float(sm.group(1))

    # Digest flag bullets ("- **TICKER (held) — Digest flag YYYY-MM-DD ...")
    for line in text.splitlines():
        m = re.match(r"^-\s+\*\*([A-Z][A-Z. /]+?)\s*(?:\(held\))?\s*[—-]\s*Digest flag (\d{4}-\d{2}-\d{2})", line)
        if m:
            for tok in re.findall(r"[A-Z][A-Z.]{0,5}", m.group(1)):
                names.setdefault(tok, {"ticker": tok, "flags": [], "notes": [], "buy_below": None,
                                       "starter_pct": None, "base_value": None, "price_ref": None,
                                       "company": "", "sector": "", "entry_criteria": "",
                                       "fair_value": "", "next_catalyst": "", "thesis_path": None,
                                       "held_only": True})
                names[tok]["flags"].append({"date": m.group(2), "text": clean(line)[:500]})

    if not names:
        warnings.append("watchlist.md: no rows parsed from Active Watchlist")
    return {"names": list(names.values()), "path": str(path), "warnings": warnings}


# ---------- event pod ----------

def parse_event_pod(hq: Path) -> list[dict[str, Any]]:
    out = []
    folder = hq / "event-pod" / "trades"
    if not folder.exists():
        return out
    for f in sorted(folder.glob("*.md")):
        head = f.read_text(encoding="utf-8").split("---")
        if len(head) < 3:
            continue
        fm: dict[str, str] = {}
        for line in head[1].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        ticker = fm.get("ticker", "").upper()
        if not ticker:
            continue
        entry_text = fm.get("entry_price", "")
        m = re.search(r"<=\s*\$([\d,]+(?:\.\d+)?)", entry_text)
        out.append({
            "ticker": ticker,
            "status": fm.get("status", ""),
            "entry_text": entry_text,
            "entry_below": float(m.group(1).replace(",", "")) if m else None,
            "path": str(f),
        })
    return out


# ---------- macro-regime.md ----------

def parse_regime(hq: Path) -> dict[str, Any]:
    path = hq / "portfolio" / "macro-regime.md"
    text = path.read_text(encoding="utf-8")
    label = ""
    m = re.search(r"### Regime Label\s*\n\*\*[^*]*?label:\*\*\s*(.+)", text)
    if not m:
        m = re.search(r"### Regime Label\s*\n(.+)", text)
    if m:
        label = clean(m.group(1))
        label = re.sub(r"^[A-Z][a-z]{2} \d{1,2} label:\s*", "", label)
    as_of = ""
    m = re.search(r"\*\*As of:\*\*\s*([0-9-]{10})", text)
    if m:
        as_of = m.group(1)
    axes = []
    for t in parse_tables(section(text, "Current Regime Summary")):
        if [h.lower() for h in t["header"]][:2] == ["axis", "classification"]:
            for raw in t["rows"]:
                if len(raw) >= 2:
                    cls = clean(raw[1])
                    cls = re.split(r"\s*\(prior", cls)[0]
                    axes.append({"axis": clean(raw[0]), "classification": cls})
            break
    short = re.split(r"\s+[—-]{1,2}\s+", label)[0] if label else ""
    return {"label": label, "short": short[:160], "as_of": as_of, "axes": axes, "path": str(path)}


# ---------- market-digest ----------

def parse_digest(hq: Path, today: date, days: int = 10) -> list[dict[str, Any]]:
    folder = hq / "market-digest"
    months = {today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")}
    entries: list[dict[str, Any]] = []
    cutoff = today - timedelta(days=days)
    for month in sorted(months):
        f = folder / f"{month}.md"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        for block in re.split(r"\n(?=## \d{4}-\d{2}-\d{2})", text):
            m = re.match(r"## (\d{4}-\d{2}-\d{2})", block)
            if not m:
                continue
            d = date.fromisoformat(m.group(1))
            if d < cutoff:
                continue
            entries.append({
                "date": d.isoformat(),
                "headlines": _bullets(block, "Headlines"),
                "flags": _flags(_bullets(block, "Watchlist Flags")),
                "snapshot": _para(block, "Market Snapshot"),
                "macro_chair": _lens(block, "Macro Chair"),
                "risk_officer": _lens(block, "Risk Officer"),
                "path": str(f),
            })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def _bullets(block: str, label: str) -> list[str]:
    m = re.search(rf"\*\*{label}\*\*\s*\n((?:\s*-.*\n?)+)", block)
    if not m:
        return []
    return [clean(b.strip()[1:]) for b in m.group(1).splitlines() if b.strip().startswith("-")]


def _para(block: str, label: str) -> str:
    m = re.search(rf"\*\*{label}\*\*\s*\n(.+)", block)
    return clean(m.group(1)) if m else ""


def _lens(block: str, label: str) -> str:
    m = re.search(rf"^{label}:\s*(.+)$", block, re.M)
    return clean(m.group(1)) if m else ""


def _flags(bullets: list[str]) -> list[dict[str, Any]]:
    out = []
    for b in bullets:
        if ":" not in b:
            continue
        head, body = b.split(":", 1)
        tickers = [t for t in re.findall(r"\b[A-Z][A-Z.]{0,5}\b", head) if t not in STOPWORDS]
        if tickers:
            out.append({"tickers": tickers, "text": body.strip()})
    return out
