"""Configuration loading with ${ENV:default} expansion and .env support."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            return os.environ.get(m.group(1)) or (m.group(2) or "")
        return _ENV_RE.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _coerce_numbers(value: Any) -> Any:
    """YAML gives strings after env expansion; coerce obvious numerics."""
    if isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d*\.\d+", s):
            return float(s)
        return value
    if isinstance(value, dict):
        return {k: _coerce_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_numbers(v) for v in value]
    return value


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return _coerce_numbers(_expand(yaml.safe_load(f)))


@dataclass
class Config:
    settings: dict = field(default_factory=dict)
    constitution: dict = field(default_factory=dict)
    universe: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        return cls(
            settings=load_yaml("settings.yaml"),
            constitution=load_yaml("constitution.yaml"),
            universe=load_yaml("universe.yaml"),
        )

    # Convenience accessors -------------------------------------------------
    @property
    def hard_limits(self) -> dict:
        return self.constitution["hard_limits"]

    @property
    def principles(self) -> list[dict]:
        return self.constitution["principles"]

    @property
    def capital(self) -> float:
        return float(self.settings["fund"]["capital_usd"])

    @property
    def models(self) -> list[str]:
        raw = self.settings["llm"]["models"]
        if isinstance(raw, list):
            return raw
        return [m.strip() for m in str(raw).split(",") if m.strip()]

    @property
    def checklist(self) -> list[dict]:
        try:
            return load_yaml("checklist.yaml").get("items", [])
        except FileNotFoundError:
            return []

    @property
    def data_dir(self) -> Path:
        d = ROOT / str(self.settings["data"].get("cache_dir", "data"))
        d.mkdir(parents=True, exist_ok=True)
        return d
