"""
Grocery_Sense.services.weekly_planner_service


WeeklyPlannerService:
- Orchestrates MealSuggestionService to pick recipes for the week
- Aggregates ingredients into a combined shopping list view
- Optionally persists items into ShoppingListService

This is still backend-only: no UI, no HTTP calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from Grocery_Sense.services.meal_suggestion_service import (
    MealSuggestionService,
    SuggestedMeal,
)
from Grocery_Sense.services.shopping_list_service import ShoppingListService


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
# Helpers
# ---------------------------------------------------------------------------


def _normalize_ingredient_name(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def _extract_ingredients(recipe: Dict[str, Any]) -> List[str]:
    ings = recipe.get("ingredients") or []
    out: List[str] = []
    for x in ings:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _aggregate_ingredients(
    suggestions: Sequence[SuggestedMeal],
) -> List[PlannedIngredient]:
    """
    Build a cross-recipe ingredient list.
    """
    agg: Dict[str, Dict[str, Any]] = {}

    for s in suggestions:
        recipe = s.recipe
        recipe_name = str(recipe.get("name", "")).strip() or "Unnamed Recipe"

        for ing in _extract_ingredients(recipe):
            norm = _normalize_ingredient_name(ing)
            if not norm:
                continue

            if norm not in agg:
                agg[norm] = {
                    "display": ing.strip(),
                    "recipes": set(),
                    "count": 0,
                    "item_id": None,
                }

            agg[norm]["recipes"].add(recipe_name)
            agg[norm]["count"] += 1

    planned: List[PlannedIngredient] = []
    for norm, data in agg.items():
        planned.append(
            PlannedIngredient(
                name=str(data["display"]),
                recipe_names=sorted(list(data["recipes"])),
                approximate_count=int(data["count"]),
                item_id=data.get("item_id"),
            )
        )

    planned.sort(key=lambda x: (-x.approximate_count, x.name.lower()))
    return planned


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class WeeklyPlannerService:
    """
    Orchestrates meal selection and optionally persists aggregated ingredients
    into ShoppingListService.
    """

    def __init__(
        self,
        meal_suggestion_service: MealSuggestionService,
        shopping_list_service: ShoppingListService,
    ) -> None:
        self.meal_suggestion_service = meal_suggestion_service
        self.shopping_list_service = shopping_list_service

    def build_weekly_plan(
        self,
        num_recipes: int = 6,
        target_ingredients: Optional[Iterable[str]] = None,
        recently_used_recipe_ids: Optional[Iterable[Any]] = None,
        persist_to_shopping_list: bool = False,
        planned_store_id: Optional[int] = None,
        added_by: Optional[str] = None,
    ) -> WeeklyPlan:
        """
        Returns a WeeklyPlan with SuggestedMeal list + aggregated ingredients.

        If persist_to_shopping_list=True, aggregated ingredients get added
        into the shopping list as best-effort items (no canonical item_id yet).
        """
        suggestions = self.meal_suggestion_service.suggest_meals_for_week(
            target_ingredients=target_ingredients,
            max_recipes=num_recipes,
            recently_used_recipe_ids=recently_used_recipe_ids,
        )

        planned_ingredients = _aggregate_ingredients(suggestions)

        plan = WeeklyPlan(
            suggestions=list(suggestions),
            planned_ingredients=planned_ingredients,
        )

        if persist_to_shopping_list:
            self._persist_plan_to_shopping_list(
                plan=plan,
                planned_store_id=planned_store_id,
                added_by=added_by,
            )

        return plan

    def _persist_plan_to_shopping_list(
        self,
        plan: WeeklyPlan,
        planned_store_id: Optional[int],
        added_by: Optional[str],
    ) -> None:
        """
        Best-effort persistence: add each PlannedIngredient into the list.
        """
        for ing in plan.planned_ingredients:
            if ing.recipe_names:
                notes = "Used in: " + ", ".join(ing.recipe_names)
            else:
                notes = ""

            quantity = max(1.0, float(ing.approximate_count))

            self.shopping_list_service.add_single_item(
                name=ing.name,
                quantity=quantity,
                unit="each",
                planned_store_id=planned_store_id,
                notes=notes,
                added_by=added_by,
            )


def summarize_weekly_plan(plan: WeeklyPlan) -> list[str]:
    """UI/helper summary for a WeeklyPlan."""
    lines: list[str] = []
    lines.append(f"Weekly plan: {len(plan.suggestions)} recipes")
    for i, s in enumerate(plan.suggestions, 1):
        name = s.recipe.get("name") or s.recipe.get("title") or f"Recipe {i}"
        lines.append(f"{i}. {name} (score={s.total_score:.2f})")
    if plan.planned_ingredients:
        lines.append(f"Planned ingredients: {len(plan.planned_ingredients)} unique items")
    return lines
