from grocery_sense.data.schema import initialize_database
from grocery_sense.data.items_repo import ItemsRepo
from grocery_sense.services.ingredient_mapping_service import IngredientMappingService

def main():
    initialize_database()

    items = ItemsRepo()
    # Ensure some baseline items exist in your DB for testing
    # (insert chicken thighs / ground beef / basil etc using your repo methods)

    mapper = IngredientMappingService(items_repo=items)

    samples = [
        "CHK THG BP SKLS",
        "Chicken Thighs Value Pack",
        "chicken thighs bulk",
        "GRND BF",
        "Fresh basil",
    ]

    for s in samples:
        res = mapper.map_to_item(s)
        print("\nINPUT:", s)
        print(" ->", res)

if __name__ == "__main__":
    main()
