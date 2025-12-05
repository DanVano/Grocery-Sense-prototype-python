"""
grocery_sense.config_store

Central place for user-level configuration and lightweight JSON caching.

Responsibilities:
- Store & retrieve user postal code (for Flipp or other region-based lookups)
- Store & retrieve preferred store ordering (for planning)
- Provide a simple JSON-based cache for HTTP responses (e.g. Flipp raw JSON)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


# Base directory for config-related files (next to this module)
_BASE_DIR = Path(__file__).resolve().parent
_CONFIG_DIR = _BASE_DIR / "config"
_CONFIG_FILE = _CONFIG_DIR / "user_config.json"
_CACHE_FILE = _CONFIG_DIR / "cache.json"


@dataclass
class UserConfig:
    """
    In-memory representation of user-level config.

    This can grow over time (e.g. adding diet preferences, household size, etc.)
    without changing the public accessors.
    """
    postal_code: Optional[str] = None
    store_priority: List[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "postal_code": self.postal_code,
            "store_priority": self.store_priority or [],
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "UserConfig":
        return UserConfig(
            postal_code=data.get("postal_code"),
            store_priority=list(data.get("store_priority", [])),
        )


# ---- Internal JSON helpers -------------------------------------------------


def _ensure_config_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # For robustness: ignore corrupt files and start fresh
        return default


def _save_json(path: Path, data: Any) -> None:
    _ensure_config_dir()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---- Public: User config accessors -----------------------------------------


def load_user_config() -> UserConfig:
    """
    Load user configuration from disk. If none exists or it is invalid,
    returns a default UserConfig.
    """
    raw = _load_json(_CONFIG_FILE, {})
    return UserConfig.from_dict(raw)


def save_user_config(cfg: UserConfig) -> None:
    """
    Persist the given UserConfig to disk.
    """
    _save_json(_CONFIG_FILE, cfg.to_dict())


def get_postal_code() -> Optional[str]:
    """
    Convenience accessor: return current user postal code (e.g. 'V3J 0P6').
    """
    cfg = load_user_config()
    return cfg.postal_code


def set_postal_code(code: str) -> None:
    """
    Set and persist the user postal code. Trims and uppercases for consistency.
    """
    cfg = load_user_config()
    cleaned = (code or "").strip().upper() or None
    cfg.postal_code = cleaned
    save_user_config(cfg)


def get_store_priority() -> List[str]:
    """
    Return user-preferred store ordering as a list of store names or IDs.

    Example:
        ["Real Canadian Superstore", "Costco", "Save-On-Foods"]
    """
    cfg = load_user_config()
    return cfg.store_priority or []


def set_store_priority(stores: List[str]) -> None:
    """
    Persist user-preferred store ordering. Empty or whitespace-only entries
    are stripped.
    """
    cleaned = [s.strip() for s in stores if s and s.strip()]
    cfg = load_user_config()
    cfg.store_priority = cleaned
    save_user_config(cfg)


# ---- Public: Lightweight JSON cache ----------------------------------------


def cache_get(key: str, max_age_days: int = 7) -> Any:
    """
    Return cached value for key if it exists and is not older than max_age_days.
    Otherwise returns None.

    This is intended for caching HTTP responses (e.g. Flipp deal JSON) so the
    app is resilient to network noise and avoids hammering external services.
    """
    raw = _load_json(_CACHE_FILE, {})
    entry = raw.get(key)
    if not entry:
        return None

    ts_str = entry.get("timestamp")
    if not ts_str:
        return None

    try:
        ts = datetime.fromisoformat(ts_str)
    except Exception:
        return None

    if datetime.now() - ts > timedelta(days=max_age_days):
        # expired
        return None

    return entry.get("value")


def cache_set(key: str, value: Any) -> None:
    """
    Store key → value in the cache with a current timestamp. Overwrites any
    existing entry for the same key.
    """
    raw = _load_json(_CACHE_FILE, {})
    raw[key] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "value": value,
    }
    _save_json(_CACHE_FILE, raw)
