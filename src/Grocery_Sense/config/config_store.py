"""
Grocery_Sense.config_store

Centralized configuration & user profile management for Grocery Sense.

Responsibilities:
- Persist user config to a JSON file (local, no cloud for now)
- Provide helpers for:
    - postal_code / city / country
    - store priority / favorites (by store name or ID)
    - user_profile (diet, allergies, preferences)

This module is the *single source of truth* for profile & config
that both Grocery Sense and (later) AI Chef can share.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_DIR = Path.home() / ".grocery_sense"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class UserProfile:
    dietary_restrictions: List[str] = field(default_factory=list)
    allergies: List[str] = field(default_factory=list)
    avoid_ingredients: List[str] = field(default_factory=list)
    preferred_meats: List[str] = field(default_factory=list)
    favorite_tags: List[str] = field(default_factory=list)


@dataclass
class AppConfig:
    postal_code: str = ""
    city: str = ""
    country: str = "CA"

    # store_name (lower) -> priority int
    store_priority: Dict[str, int] = field(default_factory=dict)

    # store ids marked as favorites (if you later use numeric ids)
    favorite_store_ids: List[int] = field(default_factory=list)

    user_profile: UserProfile = field(default_factory=UserProfile)


# ---------------------------------------------------------------------------
# Private file IO
# ---------------------------------------------------------------------------


def _ensure_config_dir() -> None:
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_raw_config(path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    _ensure_config_dir()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_raw_config(raw: Dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    _ensure_config_dir()
    path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public load/save
# ---------------------------------------------------------------------------


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    raw = _read_raw_config(path)

    prof_raw = raw.get("user_profile", {}) or {}
    prof = UserProfile(
        dietary_restrictions=list(prof_raw.get("dietary_restrictions", []) or []),
        allergies=list(prof_raw.get("allergies", []) or []),
        avoid_ingredients=list(prof_raw.get("avoid_ingredients", []) or []),
        preferred_meats=list(prof_raw.get("preferred_meats", []) or []),
        favorite_tags=list(prof_raw.get("favorite_tags", []) or []),
    )

    cfg = AppConfig(
        postal_code=str(raw.get("postal_code", "") or ""),
        city=str(raw.get("city", "") or ""),
        country=str(raw.get("country", "CA") or "CA"),
        store_priority=dict(raw.get("store_priority", {}) or {}),
        favorite_store_ids=list(raw.get("favorite_store_ids", []) or []),
        user_profile=prof,
    )
    return cfg


def save_config(cfg: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    raw = asdict(cfg)
    _write_raw_config(raw, path)


# ---------------------------------------------------------------------------
# Convenience getters/setters
# ---------------------------------------------------------------------------


def get_postal_code() -> str:
    return load_config().postal_code


def set_postal_code(value: str) -> None:
    cfg = load_config()
    cfg.postal_code = value or ""
    save_config(cfg)


def get_city() -> str:
    return load_config().city


def set_city(value: str) -> None:
    cfg = load_config()
    cfg.city = value or ""
    save_config(cfg)


def get_country() -> str:
    return load_config().country


def set_country(value: str) -> None:
    cfg = load_config()
    cfg.country = value or "CA"
    save_config(cfg)


def get_user_profile() -> Dict[str, Any]:
    """
    Returns a dict profile for downstream services (simple + stable interface).
    """
    prof = load_config().user_profile
    return {
        "dietary_restrictions": list(prof.dietary_restrictions),
        "allergies": list(prof.allergies),
        "avoid_ingredients": list(prof.avoid_ingredients),
        "preferred_meats": list(prof.preferred_meats),
        "favorite_tags": list(prof.favorite_tags),
    }


def set_user_profile(profile: Dict[str, Any]) -> None:
    cfg = load_config()
    cfg.user_profile = UserProfile(
        dietary_restrictions=list(profile.get("dietary_restrictions", []) or []),
        allergies=list(profile.get("allergies", []) or []),
        avoid_ingredients=list(profile.get("avoid_ingredients", []) or []),
        preferred_meats=list(profile.get("preferred_meats", []) or []),
        favorite_tags=list(profile.get("favorite_tags", []) or []),
    )
    save_config(cfg)


def get_store_priority_map() -> Dict[str, int]:
    """
    Returns a mapping of store_name -> priority (int).
    Higher priority means more preferred when prices are similar.
    """
    return load_config().store_priority.copy()


def set_store_priority_map(priority_map: Dict[str, int]) -> None:
    cfg = load_config()
    cfg.store_priority = priority_map or {}
    save_config(cfg)


def get_store_priority(store_name: str, default: int = 0) -> int:
    """Return a numeric priority for a store name (higher = more preferred)."""
    if not store_name:
        return int(default)
    mp = get_store_priority_map()
    return int(mp.get(store_name.strip().lower(), default))


def set_store_priority(store_name: str, priority: int) -> None:
    cfg = load_config()
    if not store_name:
        return
    cfg.store_priority[store_name.strip().lower()] = int(priority)
    save_config(cfg)


def get_favorite_store_ids() -> List[int]:
    return list(load_config().favorite_store_ids)


def set_favorite_store_ids(store_ids: List[int]) -> None:
    cfg = load_config()
    cfg.favorite_store_ids = list(store_ids or [])
    save_config(cfg)


# ---------------------------------------------------------------------------
# Lightweight local cache (used by deals_service)
# ---------------------------------------------------------------------------


def cache_get(key: str, max_age_days: int = 7) -> Any | None:
    """Get a cached value if present and not older than max_age_days."""
    if not key:
        return None
    try:
        raw = _read_raw_config()
        cache = raw.get("cache", {}) or {}
        entry = cache.get(key)
        if not isinstance(entry, dict):
            return None
        ts = float(entry.get("ts", 0))
        age_seconds = max(0.0, time.time() - ts)
        if age_seconds > (max_age_days * 86400):
            return None
        return entry.get("value")
    except Exception:
        return None


def cache_set(key: str, value: Any) -> None:
    """Store a JSON-serializable value in the local cache."""
    if not key:
        return
    raw = _read_raw_config()
    cache = raw.get("cache", {}) or {}
    cache[key] = {"ts": time.time(), "value": value}
    raw["cache"] = cache
    _write_raw_config(raw)
