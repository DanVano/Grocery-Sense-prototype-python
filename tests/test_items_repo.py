"""
Smoke tests for ItemsRepository, especially find_best_match.

Run with:
    python -m Grocery_Sense.tests.test_items_repo_smoke
from the src/ directory (where grocery_sense/ package lives).
"""

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.schema import initialize_database
from Grocery_Sense.data.repositories.items_repo import ItemsRepository


def seed_sample_items(repo: ItemsRepository) -> None:
    """
    Insert a few sample items if the table is empty.
    Safe to call multiple times.
    """
    existing = repo.list_all(limit=5)
    if existing:
        print("Items already present, skipping seeding.")
        return

    print("Seeding sample items...")
    repo.create_item(
        name="Chicken Thighs",
        canonical_name="chicken thighs",
        category="meat",
        typical_unit="kg",
        typical_package_size=1.8,
        typical_package_unit="kg",
        is_meat=True,
        is_produce=False,
    )
    repo.create_item(
        name="Broccoli",
        canonical_name="broccoli",
        category="produce",
        typical_unit="each",
        typical_package_size=None,
        typical_package_unit=None,
        is_meat=False,
        is_produce=True,
    )
    repo.create_item(
        name="White Rice",
        canonical_name="rice",
        category="pantry",
        typical_unit="kg",
        typical_package_size=2.0,
        typical_package_unit="kg",
        is_meat=False,
        is_produce=False,
    )
    print("Seeding complete.\n")


def main():
    print("=== ItemsRepository smoke test ===")
    initialize_database()
    conn = get_connection()
    items_repo = ItemsRepository(conn)

    seed_sample_items(items_repo)

    queries = [
        "chicken thighs",
        "Chicken Thighs",
        "broccoli",
        "rice",
        "RICE",
        "unknown thing",
    ]

    for q in queries:
        item = items_repo.find_best_match(q)
        if item is None:
            print(f"Query '{q}': NO MATCH")
        else:
            print(
                f"Query '{q}': MATCH -> id={item.id}, "
                f"name={item.name}, canonical={item.canonical_name}, category={item.category}"
            )

    print("=== ItemsRepository smoke test complete ===")


if __name__ == "__main__":
    main()
