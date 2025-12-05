# from old meal_planner.py

from typing import Dict, List, Any

MEAT_KEYWORDS = {
    "chicken", "beef", "pork", "turkey", "salmon", "fish",
    "steak", "thighs", "wings", "ribs", "ground beef", "ground pork",
}
MEAT_WEIGHT = 1.5
DEAL_BASE = 1.0
MAX_STORES = 3


def _is_meat_item(name: str) -> bool:
    low = (name or "").lower()
    return any(k in low for k in MEAT_KEYWORDS)


def _group_deals_by_store(deals: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_store: Dict[str, List[Dict[str, Any]]] = {}
    for d in deals:
        store = d.get("store") or "Unknown"
        by_store.setdefault(store, []).append(d)
    return by_store


def _choose_stores_min_trips(
    by_store: Dict[str, List[Dict[str, Any]]],
    allow_singleton_for_meat: bool = True,
    store_priority: List[str] | None = None,
) -> List[str]:
    """
    Greedy: prefer stores covering most high-weight deals; cap at MAX_STORES.
    Resolve ties by store_priority list (user preference).
    """
    store_priority = [s.lower() for s in (store_priority or [])]

    # score stores by count with meat-weighting
    store_scores: List[tuple[float, str]] = []
    for s, ds in by_store.items():
        score = 0.0
        for d in ds:
            score += (MEAT_WEIGHT if _is_meat_item(d.get("name", "")) else 1.0)
        # priority boost if in user-preferred list
        if s.lower() in store_priority:
            score += 0.5
        store_scores.append((score, s))
    store_scores.sort(reverse=True, key=lambda x: x[0])

    chosen: List[str] = []
    for _, s in store_scores:
        if len(chosen) >= MAX_STORES:
            break
        chosen.append(s)

    # If any chosen store has only 1 selected item, drop it unless it’s meat/fish
    pruned: List[str] = []
    for s in chosen:
        items = by_store.get(s, [])
        if len(items) == 1 and allow_singleton_for_meat:
            if not _is_meat_item(items[0].get("name", "")):
                continue
        pruned.append(s)

    # Always return at least one store if any exist
    return pruned or (chosen[:1] if chosen else [])


def _collect_favorite_ingredients(favs: List[Dict[str, Any]]) -> List[str]:
    counts: Dict[str, int] = {}
    for r in favs:
        for ing in r.get("ingredients", []):
            k = ing.lower().strip()
            if k:
                counts[k] = counts.get(k, 0) + 1
    # take top ~20 common ingredients
    return [k for k, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:20]]


def _plan_from_deals(
    favs: List[Dict[str, Any]],
    deals: List[Dict[str, Any]],
    max_recipes: int = 9
) -> List[Dict[str, Any]]:
    """
    Rank favorite recipes by how many ingredients have deals (meat-weighted).
    """
    deal_names = [d.get("name", "").lower() for d in deals]
    scored: List[tuple[float, Dict[str, Any]]] = []
    for r in favs:
        score = 0.0
        for ing in r.get("ingredients", []):
            low = ing.lower()
            # credit if any deal name contains this ingredient
            hit = any(low in dn for dn in deal_names)
            if hit:
                score += DEAL_BASE + (MEAT_WEIGHT if _is_meat_item(low) else 0.0)
        if score > 0:
            scored.append((score, r))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [r for _, r in scored[:max_recipes]]
