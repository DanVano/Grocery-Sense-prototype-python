"""
grocery_sense.services.weekly_planner_service
from grocery_sense.data.repositories.items_repo import ItemsRepository


WeeklyPlannerService:
- Orchestrates MealSuggestionService to pick recipes for the week
- Aggregates ingredients into a combined shopping list view
- Optionally persists items into ShoppingListService

This is still backend-only: no UI, no HTTP calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from grocery_sense.services.meal_suggestion_service import (
    MealSuggestionService,
    SuggestedMeal,
)
from grocery_sense.services.shopping_list_service import ShoppingListService


# ---------------------------------------------------------------------------
# Data structures returned by the planner
# ---------------------------------------------------------------------------


@dataclass
class PlannedIngredient:
    """
    Aggregated ingredient across multiple recipes.

    The counts are approximate because recipes.json typically has no
    structured quantities yet; we just count how many recipes use it.

    item_id:
        Optional link to items table in SQLite. None if we couldn't map it.
    """
    name: str
    recipe_names: List[str]
    approximate_count: int
    item_id: Optional[int] = None



@dataclass
class WeeklyPlan:
    """
    High-level representation of a weekly plan.

    - suggestions: the underlying SuggestedMeal objects for transparency
    - planned_ingredients: aggregated ingredient view
    """
    suggestions: List[SuggestedMeal]
    planned_ingredients: List[PlannedIngredient]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _normalize_ingredient_name(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _extract_ingredients_from_recipe(recipe: Dict[str, Any]) -> List[str]:
    ings = recipe.get("ingredients") or []
    return [str(i).strip() for i in ings if str(i).strip()]


def _aggregate_ingredients_from_suggestions(
    suggestions: Sequence[SuggestedMeal],
) -> List[PlannedIngredient]:
    """
    Build a cross-recipe ingredient list.

    Example result row:
        name="chicken thighs",
        recipe_names=["Honey Garlic Chicken", "Chicken Fried Rice"],
        approximate_count=2
    """
    agg: Dict[str, Dict[str, Any]] = {}

    for s in suggestions:
        recipe = s.recipe
        recipe_name = str(recipe.get("name", "")).strip() or "Unnamed recipe"
        for ing in _extract_ingredients_from_recipe(recipe):
            key = _normalize_ingredient_name(ing)
            if not key:
                continue
            entry = agg.setdefault(
                key,
                {
                    "name": key,
                    "recipes": set(),
                    "count": 0,
                },
            )
            entry["recipes"].add(recipe_name)
            entry["count"] += 1

    planned: List[PlannedIngredient] = []
    for key, raw in agg.items():
        planned.append(
            PlannedIngredient(
                name=raw["name"],
                recipe_names=sorted(raw["recipes"]),
                approximate_count=int(raw["count"]),
            )
        )

    # Sort alphabetically for now; later we could sort by meat/produce/etc.
    planned.sort(key=lambda p: p.name)
    return planned


# ---------------------------------------------------------------------------
# WeeklyPlannerService
# ---------------------------------------------------------------------------


class WeeklyPlannerService:
    """
    Orchestrates weekly meal selection + shopping list aggregation.

    Dependencies:
    - MealSuggestionService (value + preference scoring)
    - ShoppingListService (optional) for persisting planned ingredients
    """

    def __init__(
        self,
        meal_suggestion_service: MealSuggestionService,
        shopping_list_service: Optional[ShoppingListService] = None,
    ) -> None:
        self.meal_suggestion_service = meal_suggestion_service
        self.shopping_list_service = shopping_list_service

    # ---- Public API -----------------------------------------------------

    def build_weekly_plan(
        self,
        num_recipes: int = 6,
        target_ingredients: Optional[Iterable[str]] = None,
        recently_used_recipe_ids: Optional[Iterable[Any]] = None,
        persist_to_shopping_list: bool = False,
        planned_store_id: Optional[int] = None,
        added_by: str = "weekly_planner",
    ) -> WeeklyPlan:
        """
        Main orchestration method.

        - num_recipes:
            How many recipes to suggest for the week (e.g., 6–9).
        - target_ingredients:
            Optional list guiding suggestion (e.g., ["chicken", "rice"]).
            If None, suggestions are based purely on profile + value.
        - recently_used_recipe_ids:
            Optional list/set of recipe IDs cooked recently; improves variety.
        - persist_to_shopping_list:
            If True and a ShoppingListService is configured, we will
            create shopping list entries for the aggregated ingredients.
        - planned_store_id:
            Optional store ID to assign to all generated shopping list items.
        - added_by:
            Metadata tag for ShoppingListService (e.g. "weekly_planner").
        """
        # 1) Ask the MealSuggestionService for good-value recipes
        suggestions = self.meal_suggestion_service.suggest_meals_for_week(
            target_ingredients=target_ingredients,
            max_recipes=num_recipes,
            recently_used_recipe_ids=recently_used_recipe_ids,
        )

        if not suggestions:
            # No plan possible; return empty structure
            return WeeklyPlan(suggestions=[], planned_ingredients=[])

        # 2) Aggregate ingredients across suggested recipes
        planned_ingredients = _aggregate_ingredients_from_suggestions(suggestions)

        # 3) Optionally persist to ShoppingListService
        if persist_to_shopping_list and self.shopping_list_service is not None:
            self._persist_ingredients_to_shopping_list(
                planned_ingredients=planned_ingredients,
                planned_store_id=planned_store_id,
                added_by=added_by,
            )

        return WeeklyPlan(
            suggestions=suggestions,
            planned_ingredients=planned_ingredients,
        )

    # ---- Internal helpers -----------------------------------------------

    def _persist_ingredients_to_shopping_list(
        self,
        planned_ingredients: Sequence[PlannedIngredient],
        planned_store_id: Optional[int],
        added_by: str,
    ) -> None:
        """
        Send the aggregated ingredients into the ShoppingListService.

        For now:
        - quantity is approximate_count (how many recipes use it)
        - unit is "each"
        - item_id is left None (we haven't implemented item mapping yet)
        """
        if self.shopping_list_service is None:
            return

        for ing in planned_ingredients:
            # Human-readable notes: which recipes this ingredient appears in
            if ing.recipe_names:
                notes = "Used in: " + ", ".join(ing.recipe_names)
            else:
                notes = ""

            # Approximate quantity: number of recipes using this ingredient
            quantity = max(1.0, float(ing.approximate_count))

            self.shopping_list_service.add_item(
                display_name=ing.name,
                quantity=quantity,
                unit="each",
                item_id=None,
                planned_store_id=planned_store_id,
                notes=notes,
                added_by=added_by,
            )
