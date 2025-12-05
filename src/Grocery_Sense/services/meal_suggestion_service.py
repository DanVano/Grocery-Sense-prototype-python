"""
grocery_sense.services.meal_suggestion_service

Suggests recipes based on:
- User preferences (diet, avoid ingredients, preferred meats, etc.)
- Current flyer deals (via deals_service)
- Historical baseline prices (via a PriceHistoryService passed in)

This service is deliberately decoupled from the DB and UI:
- It expects a list of recipe dicts as input.
- It relies on an injected price_history_service with a simple interface.

Recipe shape (expected minimum for scoring):
    {
        "id": str | int,
        "name": str,
        "ingredients": List[str],
        "tags": List[str]   # e.g. ["30min", "gluten-free", "chicken"]
    }

Profile shape (flexible, keys are optional):
    {
        "diet": "omnivore" | "vegetarian" | "vegan" | ...,
        "avoid_ingredients": [str, ...],
        "allergies": [str, ...],
        "disliked_ingredients": [str, ...],
        "prefer_meats": [str, ...],   # e.g. ["chicken", "fish"]
        "avoid_meats": [str, ...],    # e.g. ["pork", "lamb"]
    }

PriceHistoryService expected interface:
    class PriceHistoryService:
        def get_baseline_price(self, ingredient_name: str, window_days: int = 90) -> float | None:
            ...

We do not call the DB directly here; the caller is responsible for:
- Getting recipe dicts
- Creating / injecting PriceHistoryService
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from grocery_sense.services.deals_service import (
    Deal,
    search_deals,
    rank_recipes_by_deals,
)


# ---------------------------------------------------------------------------
# Small data structures
# ---------------------------------------------------------------------------


@dataclass
class ScoredRecipe:
    recipe: Dict[str, Any]
    total_score: float
    price_score: float
    preference_score: float
    variety_score: float


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _lower_list(values: Optional[Iterable[str]]) -> List[str]:
    if not values:
        return []
    return [v.lower().strip() for v in values if isinstance(v, str) and v.strip()]


def _extract_core_ingredients(recipe: Dict[str, Any]) -> List[str]:
    """
    For now, treat all ingredients as 'core'. In the future, you can
    add a flag in your recipe schema to mark core vs. minor (oil, salt).
    """
    ings = recipe.get("ingredients") or []
    return [str(i).strip() for i in ings if str(i).strip()]


def _recipe_has_disallowed_ingredients(
    recipe: Dict[str, Any],
    avoid_terms: List[str],
) -> bool:
    """
    Return True if any avoid term appears in the ingredients list.
    """
    if not avoid_terms:
        return False
    ingredients_text = " ".join(_extract_core_ingredients(recipe)).lower()
    return any(term in ingredients_text for term in avoid_terms)


def _compute_preference_score(
    recipe: Dict[str, Any],
    profile: Dict[str, Any],
) -> float:
    """
    Score recipe based on user preferences (0–1 range, roughly).

    - Penalize recipes that conflict with diet / avoid_ingredients / allergies.
      (Those should ideally be filtered out entirely before scoring.)
    - Reward recipes that contain preferred meats or tags.
    """
    ingredients = " ".join(_extract_core_ingredients(recipe)).lower()
    tags = [t.lower() for t in (recipe.get("tags") or [])]

    prefer_meats = _lower_list(profile.get("prefer_meats"))
    avoid_meats = _lower_list(profile.get("avoid_meats"))
    favorite_tags = _lower_list(profile.get("favorite_tags"))  # optional future key

    score = 0.0

    # Prefer meats
    for meat in prefer_meats:
        if meat and meat in ingredients:
            score += 0.3  # per matched preferred meat

    # Avoid meats (soft penalty if they slip through)
    for meat in avoid_meats:
        if meat and meat in ingredients:
            score -= 0.5

    # Favorite tags (e.g. "30min", "high-protein")
    for tag in favorite_tags:
        if tag and tag in tags:
            score += 0.2

    # Clamp to [0, 1]
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return score


def _compute_price_score_for_ingredient(
    name: str,
    baseline_price: Optional[float],
    deals: List[Deal],
) -> float:
    """
    Compute a [0, 1] score contribution for a single ingredient:

    - If we have both baseline and at least one deal, score by discount ratio.
    - If only deals exist (no baseline), give a small positive bump.
    - If neither, score is 0.
    """
    name_low = name.lower()
    relevant_deals = [d for d in deals if name_low in d.name.lower()]

    if not relevant_deals and baseline_price is None:
        return 0.0

    # Best deal price we know
    deal_price = None
    for d in relevant_deals:
        if d.price is None:
            continue
        if deal_price is None or d.price < deal_price:
            deal_price = d.price

    if baseline_price is not None and baseline_price > 0 and deal_price is not None:
        discount = (baseline_price - deal_price) / baseline_price
        # clamp between 0 and 1 (only care about positive discounts)
        discount = max(0.0, min(1.0, discount))
        return discount

    if deal_price is not None and baseline_price is None:
        # Some benefit, but we don't know how good compared to usual.
        return 0.15

    # baseline exists but no current deal
    return 0.0


def _compute_price_score_for_recipe(
    recipe: Dict[str, Any],
    price_history_service: Any,
    deals_by_ingredient: Dict[str, List[Deal]],
    baseline_window_days: int = 90,
) -> float:
    """
    Aggregate price score across all core ingredients.

    - For each ingredient, we look up a baseline price via price_history_service.
    - We look up any cached deals for that ingredient.
    - Average contributions to get a 0–1 score for the recipe.
    """
    ingredients = _extract_core_ingredients(recipe)
    if not ingredients:
        return 0.0

    contributions: List[float] = []

    for ing in ingredients:
        ing_low = ing.lower()
        baseline = None
        if price_history_service is not None:
            # We expect this method to exist on the injected service.
            try:
                baseline = price_history_service.get_baseline_price(
                    ing_low,
                    window_days=baseline_window_days,
                )
            except AttributeError:
                # PriceHistoryService not wired yet; treat as no baseline.
                baseline = None

        deals = deals_by_ingredient.get(ing_low, [])
        contrib = _compute_price_score_for_ingredient(ing_low, baseline, deals)
        contributions.append(contrib)

    if not contributions:
        return 0.0

    avg = sum(contributions) / len(contributions)
    # Ensure within [0, 1]
    if avg < 0.0:
        avg = 0.0
    if avg > 1.0:
        avg = 1.0
    return avg


def _compute_variety_score(
    recipe: Dict[str, Any],
    recently_used_recipe_ids: Optional[Iterable[Any]] = None,
) -> float:
    """
    Very simple variety heuristic:

    - If the recipe is in recently_used_recipe_ids, penalize slightly.
    - Otherwise, neutral.

    In the future, you can enrich this with:
    - rotation across cuisines,
    - balancing carb/fat/protein across the week, etc.
    """
    if not recently_used_recipe_ids:
        return 0.0

    rid = recipe.get("id")
    if rid is None:
        return 0.0

    if rid in recently_used_recipe_ids:
        return -0.2
    return 0.0


# ---------------------------------------------------------------------------
# MealSuggestionService
# ---------------------------------------------------------------------------


class MealSuggestionService:
    """
    Core engine for suggesting recipes based on:
    - Profile preferences
    - Current flyer deals (via search_deals)
    - Historical baseline prices (via injected PriceHistoryService)

    Usage pattern:

        service = MealSuggestionService(price_history_service)
        suggestions = service.suggest_meals(profile, recipes, max_recipes=5)

    Where:
        profile: dict (see module docstring)
        recipes: list of recipe dicts (id, name, ingredients, tags)
    """

    def __init__(self, price_history_service: Any | None = None):
        self.price_history_service = price_history_service

    # ---- Public API -----------------------------------------------------

    def suggest_meals(
        self,
        profile: Dict[str, Any],
        recipes: List[Dict[str, Any]],
        max_recipes: int = 5,
        recently_used_recipe_ids: Optional[Iterable[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Main entrypoint.

        1) Filter recipes that violate hard constraints (allergies, avoid_ingredients).
        2) Collect a set of core ingredients across remaining recipes.
        3) Fetch deals for those ingredients (one flyer search per ingredient).
        4) Compute:
            - price_score  (0–1, from deals + baseline)
            - preference_score (0–1)
            - variety_score (≈ -0.2 to 0.0)
        5) Combine into total_score with weights:
            total = 0.5 * price + 0.3 * preference + 0.2 * variety
        6) Return top N recipe dicts.
        """
        filtered = self._filter_recipes_by_hard_constraints(profile, recipes)
        if not filtered:
            return []

        # Build a list of ingredient names across all recipes for deal fetch
        all_ingredients = self._collect_all_ingredients(filtered)
        deals_by_ingredient = self._fetch_deals_for_ingredients(all_ingredients)

        scored: List[ScoredRecipe] = []

        for r in filtered:
            price_score = _compute_price_score_for_recipe(
                r,
                self.price_history_service,
                deals_by_ingredient,
            )
            preference_score = _compute_preference_score(r, profile)
            variety_score = _compute_variety_score(r, recently_used_recipe_ids)

            # Combine scores with your chosen weights:
            # Version C: preferences + sales + historical avg (weighted lower)
            # Here:
            #   price_score (deals + baseline) -> weight 0.5
            #   preference_score              -> weight 0.3
            #   variety_score                 -> weight 0.2
            total = (0.5 * price_score) + (0.3 * preference_score) + (0.2 * variety_score)

            scored.append(
                ScoredRecipe(
                    recipe=r,
                    total_score=total,
                    price_score=price_score,
                    preference_score=preference_score,
                    variety_score=variety_score,
                )
            )

        scored.sort(key=lambda x: x.total_score, reverse=True)
        return [s.recipe for s in scored[:max_recipes]]

    # ---- Internal helpers ----------------------------------------------

    def _filter_recipes_by_hard_constraints(
        self,
        profile: Dict[str, Any],
        recipes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Remove recipes that violate hard rules:
        - allergies
        - avoid_ingredients
        - disliked_ingredients

        This is where you could also enforce dietary tags (vegan/vegetarian).
        """
        avoid = _lower_list(profile.get("avoid_ingredients"))
        allergies = _lower_list(profile.get("allergies"))
        disliked = _lower_list(profile.get("disliked_ingredients"))

        disallow_terms = set(avoid + allergies + disliked)

        filtered: List[Dict[str, Any]] = []
        for r in recipes:
            if _recipe_has_disallowed_ingredients(r, list(disallow_terms)):
                continue
            # TODO: enforce diet vs recipe tags if you want (vegan, etc.)
            filtered.append(r)
        return filtered

    def _collect_all_ingredients(
        self,
        recipes: List[Dict[str, Any]],
    ) -> List[str]:
        seen = set()
        result: List[str] = []
        for r in recipes:
            for ing in _extract_core_ingredients(r):
                low = ing.lower()
                if low not in seen:
                    seen.add(low)
                    result.append(low)
        return result

    def _fetch_deals_for_ingredients(
        self,
        ingredients: List[str],
        max_age_days: int = 7,
    ) -> Dict[str, List[Deal]]:
        """
        For each ingredient name, call search_deals once and cache
        the resulting Deal list in a dict keyed by ingredient.

        NOTE:
        - This can be slow if the ingredient list is large.
        - You might want to limit to top-N frequent ingredients per user,
          or prefetch deals elsewhere and pass them in.
        """
        deals_by_ing: Dict[str, List[Deal]] = {}
        for ing in ingredients:
            try:
                deals = search_deals(ing, max_age_days=max_age_days)
            except Exception:
                deals = []
            deals_by_ing[ing] = deals
        return deals_by_ing
