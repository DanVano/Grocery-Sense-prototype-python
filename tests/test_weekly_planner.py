"""
Smoke tests for WeeklyPlannerService.

Covers:
- building a weekly plan
- aggregating ingredients
- mapping ingredients to Items table via ItemsRepository
- optionally persisting to the shopping_list table
- summarizing the weekly plan

Run with:
    python -m grocery_sense.tests.test_weekly_planner_smoke
"""

from grocery_sense.data.connection import get_connection
from grocery_sense.data.schema import initialize_database
from grocery_sense.data.items_repo import ItemsRepository
from grocery_sense.data.stores_repo import StoresRepository
from grocery_sense.data.shopping_list_repo import ShoppingListRepository

from grocery_sense.services.meal_suggestion_service import MealSuggestionService
from grocery_sense.services.shopping_list_service import ShoppingListService
from grocery_sense.services.weekly_planner_service import (
    WeeklyPlannerService,
    summarize_weekly_plan,
)


def _ensure_sample_store(stores_repo: StoresRepository) -> int:
    """
    Ensure we have at least one store and return its id.
    """
    stores = stores_repo.list_all(limit=1)
    if stores:
        return stores[0].id

    print("Creating a sample store for planner test...")
    store = stores_repo.create_store(
        name="Planner Test Grocery",
        address="123 Grocery Lane",
        city="Coquitlam",
        postal_code="V3J 0P6",
        flipp_store_id="TEST_STORE_PLANNER",
        is_favorite=True,
        priority=10,
        notes="Created by weekly planner smoke test",
    )
    return store.id


def _seed_items_for_mapping(items_repo: ItemsRepository) -> None:
    """
    Insert some items that should map to typical recipes.json ingredients.
    Safe to call multiple times.
    """
    if items_repo.list_all(limit=1):
        print("Items table not empty, skipping seeding.")
        return

    print("Seeding a few items for ingredient → item mapping...")
    items_repo.create_item(
        name="Chicken Thighs",
        canonical_name="chicken thighs",
        category="meat",
        typical_unit="kg",
        typical_package_size=1.8,
        typical_package_unit="kg",
        is_meat=True,
        is_produce=False,
    )
    items_repo.create_item(
        name="Broccoli",
        canonical_name="broccoli",
        category="produce",
        typical_unit="each",
        typical_package_size=None,
        typical_package_unit=None,
        is_meat=False,
        is_produce=True,
    )
    items_repo.create_item(
        name="White Rice",
        canonical_name="rice",
        category="pantry",
        typical_unit="kg",
        typical_package_size=2.0,
        typical_package_unit="kg",
        is_meat=False,
        is_produce=False,
    )
    print("Item seeding complete.\n")


def main():
    print("=== WeeklyPlannerService smoke test ===")

    # 1) Ensure DB schema exists
    initialize_database()
    conn = get_connection()

    # 2) Repos & services
    stores_repo = StoresRepository(conn)
    items_repo = ItemsRepository(conn)
    sl_repo = ShoppingListRepository(conn)

    _seed_items_for_mapping(items_repo)
    store_id = _ensure_sample_store(stores_repo)

    sl_service = ShoppingListService(sl_repo, stores_repo)
    meal_svc = MealSuggestionService(price_history_service=None)

    planner = WeeklyPlannerService(
        meal_suggestion_service=meal_svc,
        shopping_list_service=sl_service,
        items_repo=items_repo,
    )

    # 3) Build a weekly plan and persist to shopping list
    print("\n[1] Building weekly plan...")
    plan = planner.build_weekly_plan(
        num_recipes=6,
        target_ingredients=None,          # let profile + deals drive it
        recently_used_recipe_ids=None,    # no history yet
        persist_to_shopping_list=True,
        planned_store_id=store_id,
        added_by="weekly_planner_smoke",
    )

    # 4) Summarize the plan
    print("\n[2] Weekly plan summary:")
    summary_lines = summarize_weekly_plan(plan)
    for line in summary_lines:
        print(" ", line)

    # 5) Show details of suggested recipes and ingredients
    print("\n[3] Suggested recipes (with scores):")
    for s in plan.suggestions:
        name = s.recipe.get("name", "Unnamed recipe")
        print(
            f" - {name}: total={s.total_score:.3f}, "
            f"price={s.price_score:.3f}, pref={s.preference_score:.3f}, "
            f"variety={s.variety_score:.3f}"
        )
        if s.reasons:
            for r in s.reasons:
                print(f"    reason: {r}")

    print("\n[4] Aggregated ingredients:")
    for ing in plan.planned_ingredients:
        print(
            f" - {ing.name} | count={ing.approximate_count} | "
            f"item_id={ing.item_id} | recipes={', '.join(ing.recipe_names)}"
        )

    # 6) Inspect what ended up on the shopping list
    print("\n[5] Shopping list items created:")
    items = sl_service.list_active_items(include_checked_off=True)
    if not items:
        print("   No shopping list items found.")
    else:
        for it in items:
            print(
                f"   [id={it.id}] {it.display_name} x{it.quantity} {it.unit}, "
                f"item_id={it.item_id}, planned_store_id={it.planned_store_id}, "
                f"notes={it.notes!r}"
            )

    print("\n=== WeeklyPlannerService smoke test complete ===")


if __name__ == "__main__":
    main()
