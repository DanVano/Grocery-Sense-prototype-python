"""
Grocery_Sense.services.meal_suggestion_service

High-level engine for suggesting value-focused meals for the week.

Combines:
- User profile (diet, allergies, meat prefs, favorite tags)
- Recipe data (from recipes.json via RecipeEngine)
- Current flyer deals (via deals_service.search_deals)
- Historical baseline prices (via injected PriceHistoryService)

This is the "Choice C" brain:
    preferences + sales + historical avg (weighted less)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from Grocery_Sense.config_store import get_user_profile
from Grocery_Sense.recipes.recipe_engine import (
    RecipeEngine,
    filter_recipes_by_ingredients_and_profile,
)
from Grocery_Sense.services.deals_service import Deal, search_deals


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


@dataclass
class SuggestedMeal:
    recipe: dict
    total_score: float
    preference_score: float
    deal_score: float
    price_score: float
    variety_score: float
    reasons: list[str]
    explanation: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _lower_list(values: Optional[Iterable[str]]) -> List[str]:
    if not values:
        return []
    return [v.strip().lower() for v in values if isinstance(v, str) and v.strip()]


def _extract_core_ingredients(recipe: Dict[str, Any]) -> List[str]:
    ings = recipe.get("ingredients") or []
    return [str(i).strip() for i in ings if str(i).strip()]


def _recipe_has_disallowed_ingredients(recipe: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    """
    Hard filter using allergies / avoid_ingredients / restrictions.
    This is a safety net.
    """
    avoid = set(_lower_list(profile.get("avoid_ingredients")))
    allergies = set(_lower_list(profile.get("allergies")))
    restrictions = set(_lower_list(profile.get("dietary_restrictions")))

    # A tiny heuristic: if vegan/vegetarian, disallow common meats.
    if "vegan" in restrictions:
        avoid |= {"beef", "pork", "chicken", "turkey", "fish", "salmon", "tuna", "egg", "eggs", "milk", "cheese"}
    elif "vegetarian" in restrictions:
        avoid |= {"beef", "pork", "chicken", "turkey", "fish", "salmon", "tuna", "gelatin"}

    for ing in _extract_core_ingredients(recipe):
        low = ing.lower()
        for bad in avoid:
            if bad and bad in low:
                return True
        for bad in allergies:
            if bad and bad in low:
                return True

    return False


def _collect_all_ingredients(recipes: Sequence[Dict[str, Any]]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for r in recipes:
        for ing in _extract_core_ingredients(r):
            k = ing.strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(ing)
    return out


def _fetch_deals_for_ingredients(ingredients: Sequence[str]) -> Dict[str, List[Deal]]:
    """
    Fetch flyer deals for a list of ingredient strings.

    Returns:
        dict: ingredient_lower -> list[Deal]
    """
    deals_by_ing: Dict[str, List[Deal]] = {}
    for ing in ingredients:
        q = ing.strip()
        if not q:
            continue
        deals = search_deals(q)
        if deals:
            deals_by_ing[q.lower()] = deals
    return deals_by_ing


def _compute_preference_score(recipe: Dict[str, Any], profile: Dict[str, Any]) -> float:
    """
    Quick preference score from recipe tags + preferred meats.
    This is intentionally simple for a prototype.
    """
    tags = set(_lower_list(recipe.get("tags")))
    preferred_tags = set(_lower_list(profile.get("favorite_tags")))
    preferred_meats = set(_lower_list(profile.get("preferred_meats")))

    score = 0.0

    # Tag matches
    if preferred_tags and tags:
        overlap = len(tags & preferred_tags)
        score += min(1.0, overlap / max(1, len(preferred_tags)))

    # Meat heuristic: if any preferred meat appears in ingredients, add a bump
    if preferred_meats:
        ings = " ".join(_extract_core_ingredients(recipe)).lower()
        if any(m in ings for m in preferred_meats):
            score += 0.25

    return min(1.0, score)


def _compute_variety_score(recipe: Dict[str, Any], recently_used_recipe_ids: Optional[Iterable[Any]]) -> float:
    """
    Negative score if recently used, neutral otherwise.
    This keeps it compatible with "add to total" weighting.
    """
    if not recently_used_recipe_ids:
        return 0.0
    rid = recipe.get("id")
    if rid is None:
        return 0.0
    used = set(recently_used_recipe_ids)
    return -0.5 if rid in used else 0.0


def _best_deal_for_ingredient(deals: List[Deal]) -> Optional[Deal]:
    """
    Pick the "best" deal from a list. Prototype heuristic:
    - prefer higher discount_percent (if available)
    - otherwise prefer lower price (if numeric)
    """
    if not deals:
        return None

    def key(d: Deal) -> Tuple[float, float]:
        disc = float(d.discount_percent or 0.0)
        price = float(d.price or 999999.0)
        # higher discount first, lower price second
        return (disc, -price)

    return sorted(deals, key=key, reverse=True)[0]


def _compute_price_score_for_recipe(
    recipe: Dict[str, Any],
    price_history_service: Any | None,
    deals_by_ingredient: Dict[str, List[Deal]],
    reasons: List[str],
) -> float:
    """
    Prototype score (0..1-ish) for how "good value" this recipe is.

    It blends:
    - flyer deals (if present)
    - optional historical baseline (if available)

    Because the data model is still forming, we do ingredient-level heuristics.
    """
    ings = _extract_core_ingredients(recipe)
    if not ings:
        return 0.0

    score = 0.0
    hits = 0

    for ing in ings:
        key = ing.lower()
        deals = deals_by_ingredient.get(key) or []
        best = _best_deal_for_ingredient(deals)
        if best:
            hits += 1
            # discount_percent is already a nice normalized signal
            if best.discount_percent is not None:
                score += min(1.0, float(best.discount_percent) / 50.0)  # 50% ~= max
            else:
                score += 0.25  # some deal exists, unknown quality

            store = best.store_name or "a store"
            if best.discount_percent is not None:
                reasons.append(f"Deal on '{ing}' at {store} (~{best.discount_percent:.0f}% off).")
            else:
                reasons.append(f"Deal on '{ing}' at {store}.")

        # Optional: use price history baseline if you can map ing -> item_id later
        # (kept as a placeholder; not required for Tkinter compile pass)
        if price_history_service:
            # You can wire this once IngredientMappingService returns item_id
            pass

    if hits == 0:
        return 0.0

    # Normalize by ingredient count, capped
    return min(1.0, score / max(1, len(ings)))


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class MealSuggestionService:
    """
    Suggests recipes based on:
    - user profile (via config_store or passed-in)
    - recipe set (via RecipeEngine or passed-in)
    - flyer deals (deals_service)
    - receipt-based historical prices (PriceHistoryService)
    """

    def __init__(
        self,
        price_history_service: Any | None = None,
        recipe_engine: RecipeEngine | None = None,
    ) -> None:
        self.price_history_service = price_history_service
        self.recipe_engine = recipe_engine or RecipeEngine()

    # ---- Public API -----------------------------------------------------

    def suggest_meals_for_week(
        self,
        profile: Optional[Dict[str, Any]] = None,
        target_ingredients: Optional[Iterable[str]] = None,
        max_recipes: int = 6,
        recently_used_recipe_ids: Optional[Iterable[Any]] = None,
    ) -> List[SuggestedMeal]:
        """
        High-level entrypoint.

        profile:
            If None, uses config_store.get_user_profile().

        target_ingredients:
            - If provided, we first filter recipes using
              filter_recipes_by_ingredients_and_profile().

        max_recipes:
            - How many to return.

        recently_used_recipe_ids:
            - Optional list/set of recipe IDs to slightly deprioritize.

        Returns:
            list[SuggestedMeal]
        """
        profile = profile or get_user_profile()

        # 1) Base recipe selection
        recipes: List[Dict[str, Any]]
        if target_ingredients:
            recipes = filter_recipes_by_ingredients_and_profile(
                target_ingredients=target_ingredients,
                profile=profile,
                recipe_engine=self.recipe_engine,
            )
        else:
            recipes = self.recipe_engine.load_all_recipes()

        if not recipes:
            return []

        # Safety: re-check hard constraints, in case recipes.json changed
        filtered: List[Dict[str, Any]] = []
        for r in recipes:
            if _recipe_has_disallowed_ingredients(r, profile):
                continue
            filtered.append(r)

        if not filtered:
            return []

        # 2) Fetch deals for ingredients across all candidate recipes
        all_ingredients = _collect_all_ingredients(filtered)
        deals_by_ingredient = _fetch_deals_for_ingredients(all_ingredients)

        # 3) Score each recipe
        suggestions: List[SuggestedMeal] = []

        for r in filtered:
            reasons: List[str] = []

            price_score = _compute_price_score_for_recipe(
                r,
                self.price_history_service,
                deals_by_ingredient,
                reasons,
            )
            preference_score = _compute_preference_score(r, profile)
            variety_score = _compute_variety_score(r, recently_used_recipe_ids)

            deal_score = 0.0  # TODO: flyer/Flipp deal scoring

            # Choice C weighting:
            #  - price_score       -> 0.5
            #  - preference_score  -> 0.3
            #  - variety_score     -> 0.2
            total = (0.5 * price_score) + (0.3 * preference_score) + (0.2 * variety_score)

            # Add some generic reasons when scores are non-zero
            if preference_score > 0.5:
                reasons.append("Matches your meat or tag preferences.")
            if variety_score < 0:
                reasons.append("You cooked this recently, slightly deprioritized.")

            suggestions.append(
                SuggestedMeal(
                    recipe=r,
                    total_score=total,
                    preference_score=preference_score,
                    deal_score=deal_score,
                    price_score=price_score,
                    variety_score=variety_score,
                    reasons=reasons,
                )
            )

        # 4) Sort & truncate
        suggestions.sort(key=lambda s: s.total_score, reverse=True)
        return suggestions[:max_recipes]


# ---------------------------------------------------------------------------
# Explanation helpers (UI/debug)
# ---------------------------------------------------------------------------


def format_meal_explanation(
    recipe_name: str,
    preference_score: float,
    deal_score: float,
    price_score: float,
    variety_score: float,
    reasons: list[str],
    max_reasons: int = 4,
) -> str:
    """
    Build a human-readable explanation string for why a meal was suggested.

    This is intentionally generic and does NOT depend on any particular
    dataclass type – you pass in the pieces you already have.
    """
    lines: list[str] = []

    lines.append(f"Why we suggested '{recipe_name}':")

    summary_bits: list[str] = []

    if price_score >= 0.65:
        summary_bits.append("strong value (deals/prices)")
    elif price_score >= 0.35:
        summary_bits.append("some value signals")

    if preference_score >= 0.65:
        summary_bits.append("fits your preferences")
    elif preference_score >= 0.35:
        summary_bits.append("partly matches your preferences")

    if deal_score >= 0.35:
        summary_bits.append("has flyer deal coverage")

    if variety_score < 0:
        summary_bits.append("recently cooked (slightly deprioritized)")

    if summary_bits:
        lines.append("Summary: " + "; ".join(summary_bits))
    else:
        lines.append("Summary: general suggestion based on available signals.")

    # Include scores for debugging/transparency
    lines.append("")
    lines.append(
        f"Scores: price={price_score:.2f}  preference={preference_score:.2f}  deals={deal_score:.2f}  variety={variety_score:.2f}"
    )

    if reasons:
        lines.append("")
        lines.append("Details:")
        for r in reasons[:max_reasons]:
            lines.append(f" • {r}")

    return "\n".join(lines)


def explain_suggested_meal(s: SuggestedMeal) -> str:
    """UI helper: produce a readable explanation for a SuggestedMeal."""
    if getattr(s, "explanation", None):
        return str(s.explanation)

    recipe_name = s.recipe.get("name") or s.recipe.get("title") or "Recipe"
    return format_meal_explanation(
        recipe_name=str(recipe_name),
        preference_score=float(s.preference_score),
        deal_score=float(s.deal_score),
        price_score=float(s.price_score),
        variety_score=float(s.variety_score),
        reasons=list(s.reasons or []),
    )
