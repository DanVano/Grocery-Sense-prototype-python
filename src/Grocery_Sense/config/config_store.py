# from old storage.persistent_storage

import json
import os
from datetime import datetime, timedelta

CONFIG_FILE = os.path.join("storage", "user_config.json")
CACHE_FILE = os.path.join("storage", "cache.json")


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_postal_code():
    cfg = _load_json(CONFIG_FILE, {})
    return cfg.get("postal_code")


def set_postal_code(code: str):
    cfg = _load_json(CONFIG_FILE, {})
    cfg["postal_code"] = (code or "").strip().upper()
    _save_json(CONFIG_FILE, cfg)


def get_store_priority():
    cfg = _load_json(CONFIG_FILE, {})
    return cfg.get("store_priority", [])


def set_store_priority(stores):
    cfg = _load_json(CONFIG_FILE, {})
    cfg["store_priority"] = [s.strip() for s in stores if s.strip()]
    _save_json(CONFIG_FILE, cfg)


def cache_get(key: str, max_age_days: int = 7):
    cache = _load_json(CACHE_FILE, {})
    entry = cache.get(key)
    if not entry:
        return None
    ts = entry.get("timestamp")
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
    except Exception:
        return None
    if datetime.now() - t > timedelta(days=max_age_days):
        return None
    return entry.get("value")


def cache_set(key: str, value):
    cache = _load_json(CACHE_FILE, {})
    cache[key] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "value": value,
    }
    _save_json(CACHE_FILE, cache)
