"""ALT source adapters. Each module offers:

    candidates(profile) -> list of specs this source could offer for a company (by its industry, name, text)
    fetch(spec) -> pd.Series of values by date (monthly, weekly or daily), or raises

A spec is a dict: id, source, title, unit, freq (M / W / D), agg (sum / mean / last: how to roll up to quarters),
growth (pct / diff: how to measure change), kind (company / industry / macro / context), reason (why it should
matter), needs (None, or the free key it waits for), plus the adapter's own params.
"""
from __future__ import annotations

import time
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Keel market terminal (research)"}


def get(url: str, params: dict[str, Any] | None = None, timeout: int = 60, **kw: Any) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers={**UA, **kw.pop("headers", {})}, timeout=timeout, **kw)
            if r.status_code in (200, 404):
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:80]
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last or "no response")


def has(text: str, *words: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in words)


def all_modules():
    from . import cftc, ctgov, eia860m, eia_grid, flights, openfda
    return [openfda, ctgov, eia860m, eia_grid, cftc, flights]
