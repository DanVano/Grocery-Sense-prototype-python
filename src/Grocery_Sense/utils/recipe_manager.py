def prioritize_by_pantry(recipes, days_fresh=3):
    fresh_items, stale_items = get_fresh_items(days_fresh)
    expiring_items = set(i for i, days in stale_items if days >= days_fresh)
    fresh_items_set = set(i for i, _ in fresh_items)

    def score(recipe):
        ingr = set(i.lower() for i in recipe["ingredients"])
        matches_expiring = len(ingr & expiring_items)
        matches_fresh = len(ingr & fresh_items_set)
        # heavier weight for expiring food
        return (matches_expiring * 10) + matches_fresh

    recipes.sort(key=score, reverse=True)
    return recipes
