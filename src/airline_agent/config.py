"""Settings and YAML config loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "gemini": "gemini-3.8-flash"}


def agent_provider() -> str:
    """AGENT_PROVIDER if set; otherwise whichever API key is present (Anthropic first)."""
    explicit = (os.getenv("AGENT_PROVIDER") or "").strip().lower()
    if explicit in DEFAULT_MODELS:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    return "anthropic"


def agent_model(provider: str | None = None) -> str:
    provider = provider or agent_provider()
    env = "GEMINI_MODEL" if provider == "gemini" else "AGENT_MODEL"
    return os.getenv(env) or DEFAULT_MODELS[provider]


def judge_model(provider: str | None = None) -> str:
    return os.getenv("JUDGE_MODEL") or agent_model(provider)


def cache_ttl_hours() -> float:
    return float(os.getenv("POLICY_CACHE_TTL_HOURS") or 6)


def testair_base_url() -> str:
    return (os.getenv("TESTAIR_BASE_URL") or "http://127.0.0.1:8001").rstrip("/")


def policy_db_path() -> Path:
    p = Path(os.getenv("POLICY_DB_PATH") or "data/policy_snapshots.db")
    return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def policy_sources() -> dict[str, Any]:
    return load_yaml("policy_sources.yaml")


def fare_mapping() -> dict[str, dict[str, str]]:
    return load_yaml("fare_mapping.yaml")


def normalize_fare(airline_key: str, raw_fare: str) -> str:
    """Map a raw fare code to a policy fare category; 'Unknown' if unmapped."""
    table = fare_mapping().get(airline_key, {})
    raw = (raw_fare or "").strip()
    if raw in table:
        return table[raw]
    upper = {k.upper(): v for k, v in table.items()}
    return upper.get(raw.upper(), "Unknown")
