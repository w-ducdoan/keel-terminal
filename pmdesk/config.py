"""Load settings, rules and overrides from config/*.yaml."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
SECRETS_DIR = ROOT / ".secrets"


def _load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    settings: dict[str, Any]
    rules: dict[str, Any]
    overrides: dict[str, Any]

    @property
    def hq(self) -> Path:
        return Path(self.settings["investing_hq"])

    def ticker_override(self, ticker: str) -> dict[str, Any]:
        return (self.overrides.get("tickers") or {}).get(ticker.upper(), {})

    def beta_override(self, ticker: str) -> str | None:
        return (self.overrides.get("beta_overrides") or {}).get(ticker.upper())


def load_config() -> Config:
    DATA_DIR.mkdir(exist_ok=True)
    return Config(_load("settings.yaml"), _load("rules.yaml"), _load("overrides.yaml"))
