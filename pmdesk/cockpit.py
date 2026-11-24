"""Position cockpit: the thesis on one screen. Reads the coverage folder's thesis (read-only) and pulls out
the Bear / Base / Bull values, the KPIs to watch and the thesis killers with their current status.

Theses are written by hand in a few styles, so every parser is tolerant: a table, a numbered list or
headings like "### Bear Case - $115-$130 Target, 25% Probability" all work, and anything it can't read
is shown as "not found" with a link to the file rather than guessed.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .hq import clean, parse_tables
from .security import coverage

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")


def _sections(text: str) -> list[tuple[str, int, list[str]]]:
    """(heading, level, body lines) for every markdown heading."""
    out: list[tuple[str, int, list[str]]] = []
    cur: tuple[str, int, list[str]] | None = None
    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            cur = (clean(m.group(2)), len(m.group(1)), [])
            out.append(cur)
        elif cur:
            cur[2].append(line)
    return out


def _find(secs: list[tuple[str, int, list[str]]], pattern: str) -> tuple[str, int, list[str], int] | None:
    """Last matching section (theses keep the newest version lower down), with its sub-sections folded in."""
    hit = None
    for i, (h, lvl, body) in enumerate(secs):
        if re.search(pattern, h, re.I):
            hit = i
    if hit is None:
        return None
    h, lvl, body = secs[hit]
    lines = list(body)
    for h2, lvl2, body2 in secs[hit + 1:]:
        if lvl2 <= lvl:
            break
        lines += [f"{'#' * lvl2} {h2}"] + body2
    return h, lvl, lines, hit


def _status(text: str) -> str:
    t = text.lower()
    if re.search(r"\bnot (yet )?triggered\b|\bintact\b|\bpass\b|\bnone of\b", t):
        return "ok"
    if re.search(r"\bamber\b|\bwatch\b|\bapproaching\b|\bunconfirmed\b|\bbent\b", t):
        return "watch"
    if re.search(r"\b(triggered|fired|tripped)\b", t):  # explicit status words only, not "if X fails"
        return "hit"
    return "unknown"


def _scenarios(sec: tuple[str, int, list[str], int] | None) -> tuple[list[dict[str, Any]], float | None]:
    if not sec:
        return [], None
    _, _, lines, _ = sec
    cases: dict[str, dict[str, Any]] = {}
    weighted = None
    for t in parse_tables(lines):
        hdr = [h.lower() for h in t["header"]]
        pcol = next((i for i, h in enumerate(hdr) if "prob" in h), None)
        vcol = next((i for i, h in enumerate(hdr) if re.search(r"value|target|price", h)), None)
        for raw in t["rows"]:
            name = clean(raw[0]).lower()
            case = next((c for c in ("bear", "base", "bull") if c in name), None)
            cells = [clean(c) for c in raw]
            vtxt = cells[vcol] if vcol is not None and vcol < len(cells) else " ".join(cells[1:])
            mv = MONEY.search(vtxt)
            if "weighted" in name and mv:
                weighted = float(mv.group(1).replace(",", ""))
                continue
            if not case or not mv:
                continue
            ptxt = cells[pcol] if pcol is not None and pcol < len(cells) else ""
            mp = PCT.search(ptxt)
            cases[case] = {"case": case, "value": float(mv.group(1).replace(",", "")),
                           "prob": float(mp.group(1)) / 100 if mp else None, "text": " | ".join(cells[1:])[:300]}
    if not cases:  # paragraph style: "**Bull (25%): $1,242.**" or "**Bull case - 25% probability / ~$230 target**"
        for line in lines:
            if line.startswith("#") or line.lstrip().startswith("|"):
                continue
            c = clean(line)
            m = re.match(r"^(Bear|Base|Bull)\b(.{0,140})", c, re.I)
            if not m:
                continue
            mv, mp = MONEY.search(m.group(2)), PCT.search(m.group(2))
            if mv and m.group(1).lower() not in cases:
                cases[m.group(1).lower()] = {"case": m.group(1).lower(), "value": float(mv.group(1).replace(",", "")),
                                             "prob": float(mp.group(1)) / 100 if mp else None, "text": c[:300]}
    if not cases:  # heading style: "### Bear Case - $115-$130 Target, 25% Probability"
        for line in lines:
            m = re.match(r"^#{2,4}\s+(Bear|Base|Bull)\b(.*)", clean(line) if not line.startswith("#") else line, re.I)
            if not m:
                continue
            vals = [float(x.replace(",", "")) for x in MONEY.findall(m.group(2))]
            mp = PCT.search(m.group(2))
            if vals:
                cases[m.group(1).lower()] = {"case": m.group(1).lower(), "value": sum(vals) / len(vals),
                                             "prob": float(mp.group(1)) / 100 if mp else None, "text": clean(m.group(2))[:300]}
    if weighted is None:
        m = re.search(r"(?:probability-)?weighted (?:value|target)[^$]{0,40}\$([\d,]+(?:\.\d+)?)", "\n".join(lines), re.I)
        if m:
            weighted = float(m.group(1).replace(",", ""))
    if weighted is None and cases and all(c.get("prob") for c in cases.values()):
        weighted = sum(c["value"] * c["prob"] for c in cases.values())
    order = {"bear": 0, "base": 1, "bull": 2}
    return sorted(cases.values(), key=lambda c: order[c["case"]]), weighted


def _killers(sec: tuple[str, int, list[str], int] | None) -> list[dict[str, Any]]:
    if not sec:
        return []
    _, _, lines, _ = sec
    out = []
    tables = parse_tables(lines)
    if tables:
        t = tables[0]
        for raw in t["rows"]:
            cells = [clean(c) for c in raw]
            text = cells[0]
            rest = " ".join(cells[1:])
            out.append({"text": text, "detail": rest[:300], "status": _status(rest + " " + text)})
        return out
    for line in lines:
        m = re.match(r"^\s*(?:\d+[.)]|[-*])\s+(.*)", line)
        if m:
            text = clean(m.group(1))
            out.append({"text": text[:400], "detail": "", "status": _status(text)})
    return out


def _kpis(sec: tuple[str, int, list[str], int] | None) -> dict[str, Any] | None:
    if not sec:
        return None
    tables = parse_tables(sec[2])
    if not tables:
        return None
    t = tables[0]
    hdr = t["header"]
    scol = next((i for i, h in enumerate(hdr) if re.search(r"status|read|signal", h, re.I)), None)
    rows = []
    for raw in t["rows"][:14]:
        cells = [clean(c)[:220] for c in raw]
        st = _status(cells[scol]) if scol is not None and scol < len(cells) else "unknown"
        rows.append({"cells": cells, "status": st})
    return {"header": hdr, "rows": rows, "status_col": scol}


def _thesis_file(folder: Path) -> Path | None:
    t = folder / "thesis.md"
    if t.exists():
        return t
    memos = sorted(folder.glob("*memo*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return memos[0] if memos else None


def load(hq: Path, ticker: str) -> dict[str, Any]:
    cov = coverage(hq, ticker)
    if not cov:
        return {"ok": False, "covered": False,
                "error": "Not in coverage. New names start at Gate 0 (the research folder/workflows/underwriting-process.md)."}
    folder = Path(cov["folder"])
    f = _thesis_file(folder)
    models = sorted((p for p in (folder / "model-notes").glob("*Model*.xlsx") if not p.name.startswith("~$")),
                    key=lambda p: p.stat().st_mtime, reverse=True) if (folder / "model-notes").exists() else []
    model = ({"path": str(models[0]), "name": models[0].name,
              "modified": datetime.fromtimestamp(models[0].stat().st_mtime).strftime("%Y-%m-%d"),
              "open_in_excel": any((folder / "model-notes" / ("~$" + m.name)).exists() for m in models[:1])} if models else None)
    if not f:
        return {"ok": False, "covered": True, "bucket": cov["bucket"], "model": model,
                "error": "Coverage folder has no thesis.md or memo yet."}
    key, mt = str(f), f.stat().st_mtime
    hit = _cache.get(key)
    if hit and hit[0] == mt:
        return {**hit[1], "model": model}
    text = f.read_text(encoding="utf-8")
    secs = _sections(text)
    title = next((h for h, lvl, _ in secs if lvl == 1), f.stem)
    m = re.search(r"(?m)^last_updated:\s*(\S+)", text)
    scen, weighted = _scenarios(_find(secs, r"(?=.*\bbear\b)(?=.*\bbase\b)(?=.*\bbull\b)|recommendation with bull"))
    out = {
        "ok": True, "covered": True, "bucket": cov["bucket"], "path": str(f), "file": f.name, "title": title,
        "updated": m.group(1).strip("'\"") if m else datetime.fromtimestamp(mt).strftime("%Y-%m-%d"),
        "scenarios": scen, "weighted": weighted,
        "kpis": _kpis(_find(secs, r"\bKPI")),
        "killers": _killers(_find(secs, r"thesis killers")),
    }
    _cache[key] = (mt, out)
    return {**out, "model": model}
