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
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

# Project root: .../Grocery-Sense-Prototype-Python/src
_BASE_DIR = Path(__file__).resolve().parents[1]

# Config directory: src/config/
_CONFIG_DIR = _BASE_DIR / "config"
_CONFIG_FILE = _CONFIG_DIR / "user_config.json"

# Make sure the config directory exists when we first write
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

_VALID_DIETS = {
    "vegan",
    "vegetarian",
    "meat eater",
    "pescatarian",
    "keto",
    "omnivore",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class UserConfig:
    """
    Top-level config structure.

    Stored as JSON at: src/config/user_config.json
    """
    postal_code: str = ""
    city: str = ""
    country: str = "CA"

    # Optional: store-level preferences (by name or ID)
    # Example: {"Costco": 10, "Save-On-Foods": 8}
    store_priority: Dict[str, int] = field(default_factory=dict)

    # Optional list of store IDs you consider "favorite"
    favorite_store_ids: List[int] = field(default_factory=list)

    # Arbitrary profile dict (see default_profile() below)
    profile: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers for JSON I/O
# ---------------------------------------------------------------------------


def _read_raw_config() -> Dict[str, Any]:
    if not _CONFIG_FILE.exists():
        return {}
    try:
        with _CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        # If config is corrupted, start fresh
        return {}


def _write_raw_config(data: Dict[str, Any]) -> None:
    with _CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _from_raw_config(raw: Dict[str, Any]) -> UserConfig:
    """
    Convert dict -> UserConfig, applying defaults if keys are missing.
    """
    return UserConfig(
        postal_code=raw.get("postal_code", "") or "",
        city=raw.get("city", "") or "",
        country=raw.get("country", "") or "CA",
        store_priority=raw.get("store_priority", {}) or {},
        favorite_store_ids=raw.get("favorite_store_ids", []) or [],
        profile=raw.get("profile", {}) or {},
    )


def _to_raw_config(cfg: UserConfig) -> Dict[str, Any]:
    return asdict(cfg)


# ---------------------------------------------------------------------------
# Public config API
# ---------------------------------------------------------------------------


def load_config() -> UserConfig:
    """
    Load full config from JSON, filling in missing profile fields if needed.
    """
    raw = _read_raw_config()
    cfg = _from_raw_config(raw)

    # Ensure profile has all expected keys
    if not cfg.profile:
        cfg.profile = default_profile()
    else:
        cfg.profile = ensure_profile_defaults(cfg.profile)

    return cfg


def save_config(cfg: UserConfig) -> None:
    """
    Persist the entire config to disk.
    """
    raw = _to_raw_config(cfg)
    _write_raw_config(raw)


# --- Postal code / region -----------------------------------------------


def get_postal_code() -> str:
    return load_config().postal_code


def set_postal_code(postal_code: str) -> None:
    cfg = load_config()
    pc = postal_code.strip().upper()
    if pc and not validate_postal(pc):
        raise ValueError(f"Invalid postal code format: {postal_code!r}")
    cfg.postal_code = pc
    save_config(cfg)


def get_city() -> str:
    return load_config().city


def set_city(city: str) -> None:
    cfg = load_config()
    cfg.city = city.strip()
    save_config(cfg)


def get_country() -> str:
    return load_config().country or "CA"


def set_country(country: str) -> None:
    cfg = load_config()
    cfg.country = country.strip().upper() or "CA"
    save_config(cfg)


# --- Store preferences ---------------------------------------------------


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


def set_store_priority(store_name: str, priority: int) -> None:
    cfg = load_config()
    name = store_name.strip()
    if not name:
        return
    if priority < 0:
        priority = 0
    cfg.store_priority[name] = priority
    save_config(cfg)


def get_favorite_store_ids() -> List[int]:
    return list(load_config().favorite_store_ids)


def set_favorite_store_ids(store_ids: List[int]) -> None:
    cfg = load_config()
    cfg.favorite_store_ids = [int(sid) for sid in store_ids]
    save_config(cfg)


# --- User profile --------------------------------------------------------


def default_profile() -> Dict[str, Any]:
    """
    Base shape of the user profile for Grocery Sense.

    This is *the* canonical structure both apps should aim to share.
    """
    return {
        "diet": "meat eater",            # or "vegetarian", "vegan", etc.
        "allergies": [],                 # e.g. ["peanut", "shellfish"]
        "avoid_ingredients": [],         # general dislikes or intolerances
        "restrictions": [],              # e.g. ["no_pork", "no_beef"]
        "disliked_ingredients": [],      # softer dislikes
        "prefer_meats": [],              # e.g. ["chicken", "fish"]
        "avoid_meats": [],               # e.g. ["lamb"]
        "favorite_cuisines": [],         # e.g. ["italian", "mexican"]
        "favorite_tags": [],             # e.g. ["under_30_min", "high-protein"]
        "price_sensitivity": "medium",   # "low" | "medium" | "high"
    }


def ensure_profile_defaults(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill in any missing keys in an existing profile with defaults.
    """
    base = default_profile()
    merged = base.copy()
    for k, v in profile.items():
        merged[k] = v
    # Normalize some fields
    diet = str(merged.get("diet", "")).lower()
    if not validate_diet(diet):
        diet = "meat eater"
    merged["diet"] = diet
    merged["allergies"] = sanitize_list_input_list(merged.get("allergies", []))
    merged["avoid_ingredients"] = sanitize_list_input_list(merged.get("avoid_ingredients", []))
    merged["disliked_ingredients"] = sanitize_list_input_list(merged.get("disliked_ingredients", []))
    merged["restrictions"] = sanitize_list_input_list(merged.get("restrictions", []))
    merged["prefer_meats"] = sanitize_list_input_list(merged.get("prefer_meats", []))
    merged["avoid_meats"] = sanitize_list_input_list(merged.get("avoid_meats", []))
    merged["favorite_cuisines"] = sanitize_list_input_list(merged.get("favorite_cuisines", []))
    merged["favorite_tags"] = sanitize_list_input_list(merged.get("favorite_tags", []))
    sensitivity = str(merged.get("price_sensitivity", "medium")).lower()
    if sensitivity not in {"low", "medium", "high"}:
        sensitivity = "medium"
    merged["price_sensitivity"] = sensitivity
    return merged


def get_user_profile() -> Dict[str, Any]:
    cfg = load_config()
    # load_config already ensures defaults
    return cfg.profile.copy()


def save_user_profile(profile: Dict[str, Any]) -> None:
    """
    Save a new profile, merging with defaults + validation.
    """
    cfg = load_config()
    cfg.profile = ensure_profile_defaults(profile or {})
    save_config(cfg)


def update_user_profile(**updates: Any) -> Dict[str, Any]:
    """
    Convenience: update a few keys in the profile and return the result.
    """
    profile = get_user_profile()
    profile.update(updates)
    save_user_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Utility functions (ported from user_profile_tools)
# ---------------------------------------------------------------------------


def sanitize_list_input(raw_input: str) -> List[str]:
    """
    Convert a comma-separated input string into a list of
    lowercased, stripped tokens.
    """
    if not isinstance(raw_input, str):
        return []
    return [item.strip().lower() for item in raw_input.split(",") if item.strip()]


def sanitize_list_input_list(values: Any) -> List[str]:
    """
    Normalize a list-like input into clean, lowercased strings.
    Accepts:
        - list of strings
        - comma-separated string
    """
    if isinstance(values, str):
        return sanitize_list_input(values)
    if isinstance(values, list):
        return [str(v).strip().lower() for v in values if str(v).strip()]
    return []


def validate_diet(diet: str) -> bool:
    if not isinstance(diet, str):
        return False
    return diet.lower() in _VALID_DIETS


def validate_postal(postal_code: str) -> bool:
    """
    Very light validation: at least 6 chars and starts with a letter.
    This is enough for Canadian-style postal codes at this stage.
    """
    if not isinstance(postal_code, str):
        return False
    pc = postal_code.strip().replace(" ", "")
    if len(pc) < 6:
        return False
    return pc[0].isalpha()
