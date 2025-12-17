"""
Grocery_Sense.services.planning_service

Service layer for planning which stores to visit for the current shopping list.

This version:
  - Reads active shopping list items.
  - Reads all known stores (with favorite/priority info).
  - Looks at historical prices per item per store (avg unit_price).
  - Chooses up to `max_stores` that cover most items, biased by favorites/priority.
  - Assigns each item to a chosen store, with fallback behavior.

Return shape:
{
  "stores": {
      store_id: {
          "store": Store,
          "items": list[ShoppingListItem]
      },
      ...
  },
  "unassigned": list[ShoppingListItem],
  "summary": str,
  "chosen_store_ids": list[int],
}
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from Grocery_Sense.data.repositories.stores_repo import list_stores
from Grocery_Sense.data.repositories.items_repo import get_item_by_name
from Grocery_Sense.data.repositories.prices_repo import get_prices_for_item
from Grocery_Sense.domain.models import Store, ShoppingListItem
from Grocery_Sense.services.shopping_list_service import ShoppingListService


class PlanningService:
    def __init__(self, shopping_list_service: ShoppingListService) -> None:
        self._shopping = shopping_list_service

    def build_plan_for_active_list(self, max_stores: int = 3) -> Dict[str, object]:
        """
        Build a store visit plan for all active shopping list items.

        Strategy (v1, greedy & simple):
          1) Get all active shopping list items.
          2) Get all stores.
          3) For each item, find cheapest store (by avg historical unit_price).
          4) Score stores by how many items they win, plus a bias for favorites/priority.
          5) Choose up to `max_stores` with highest scores.
          6) Assign items to their chosen store if it's in that set; otherwise
             assign to a generic fallback store (favorite/priority) if possible.
        """
        max_stores = max(1, int(max_stores))

        items = self._shopping.get_active_items(include_checked_off=False, store_id=None)
        stores = list_stores()

        if not items or not stores:
            return {
                "stores": {},
                "unassigned": items or [],
                "summary": "No plan possible (no items or no stores configured).",
                "chosen_store_ids": [],
            }

        store_by_id: Dict[int, Store] = {s.id: s for s in stores}

        # Step 3: best (cheapest) store per item using history
        item_best_store: Dict[int, Optional[int]] = {}
        for itm in items:
            best_store_id, _best_price = self._find_best_store_for_item(itm, stores)
            item_best_store[itm.id] = best_store_id

        # Step 4: score stores by how many items they serve, with bias
        store_scores: Dict[int, float] = {}
        for itm in items:
            chosen_store_id = item_best_store.get(itm.id)
            if chosen_store_id is None:
                continue
            store = store_by_id.get(chosen_store_id)
            if not store:
                continue

            base = 1.0
            if store.is_favorite:
                base += 0.5
            base += (store.priority or 0) * 0.1

            store_scores[chosen_store_id] = store_scores.get(chosen_store_id, 0.0) + base

        # Step 5: choose stores
        if not store_scores:
            chosen_store_ids = self._fallback_stores(stores, max_stores)
        else:
            chosen_store_ids = [
                s_id
                for s_id, _ in sorted(store_scores.items(), key=lambda kv: kv[1], reverse=True)[:max_stores]
            ]

        # Step 6: assign items to selected stores (or fallback)
        plan_by_store: Dict[int, List[ShoppingListItem]] = {sid: [] for sid in chosen_store_ids}
        unassigned: List[ShoppingListItem] = []

        fallback_store_id = self._choose_generic_fallback_store(stores, chosen_store_ids)

        for itm in items:
            best_store_id = item_best_store.get(itm.id)
            if best_store_id in chosen_store_ids:
                plan_by_store.setdefault(best_store_id, []).append(itm)
            elif fallback_store_id is not None:
                plan_by_store.setdefault(fallback_store_id, []).append(itm)
            else:
                unassigned.append(itm)

        summary = self._build_summary(plan_by_store, unassigned, store_by_id)

        stores_struct: Dict[int, Dict[str, object]] = {}
        for sid, its in plan_by_store.items():
            st = store_by_id.get(sid)
            if not st:
                continue
            stores_struct[sid] = {"store": st, "items": its}

        return {
            "stores": stores_struct,
            "unassigned": unassigned,
            "summary": summary,
            "chosen_store_ids": chosen_store_ids,
        }

    def _find_best_store_for_item(
        self,
        shopping_item: ShoppingListItem,
        stores: List[Store],
        days_back: int = 90,
    ) -> Tuple[Optional[int], Optional[float]]:
        """
        Find the best (cheapest avg unit_price) store for a shopping item.

        Uses:
          - shopping_item.item_id if present
          - otherwise tries items_repo.get_item_by_name(shopping_item.display_name)

        Returns:
          (best_store_id, best_avg_unit_price)
        """
        item_id: Optional[int] = shopping_item.item_id

        if item_id is None:
            # fallback mapping: try exact canonical_name match on display_name
            name = (shopping_item.display_name or "").strip()
            if not name:
                return None, None
            item_row = get_item_by_name(name)
            if not item_row:
                return None, None
            item_id = item_row.id

        best_store_id: Optional[int] = None
        best_price: Optional[float] = None

        for store in stores:
            history = get_prices_for_item(
                item_id=item_id,
                days_back=days_back,
                store_id=store.id,
                limit=None,
            )
            if not history:
                continue

            avg_price = sum(p.unit_price for p in history) / max(1, len(history))
            if best_price is None or avg_price < best_price:
                best_price = avg_price
                best_store_id = store.id

        return best_store_id, best_price

    def _fallback_stores(self, stores: List[Store], max_stores: int) -> List[int]:
        """
        If we don't have any price history, pick stores by:
          1) favorites
          2) highest priority
          3) name sort (stable)
        """
        sorted_stores = sorted(
            stores,
            key=lambda s: (
                0 if s.is_favorite else 1,
                -(s.priority or 0),
                (s.name or "").lower(),
            ),
        )
        return [s.id for s in sorted_stores[:max_stores]]

    def _choose_generic_fallback_store(self, stores: List[Store], chosen_store_ids: List[int]) -> Optional[int]:
        """
        If some items don't have a best store (no history), put them somewhere sensible:
          - first favorite within chosen
          - otherwise the first chosen store
          - otherwise None
        """
        chosen_set = set(chosen_store_ids)

        favorites_in_chosen = [s.id for s in stores if s.id in chosen_set and s.is_favorite]
        if favorites_in_chosen:
            return favorites_in_chosen[0]

        if chosen_store_ids:
            return chosen_store_ids[0]

        return None

    def _build_summary(
        self,
        plan_by_store: Dict[int, List[ShoppingListItem]],
        unassigned: List[ShoppingListItem],
        store_by_id: Dict[int, Store],
    ) -> str:
        parts: List[str] = []
        parts.append("Store plan summary:")

        if not plan_by_store:
            parts.append("- No stores selected.")
        else:
            for sid, items in sorted(plan_by_store.items(), key=lambda kv: len(kv[1]), reverse=True):
                st = store_by_id.get(sid)
                if not st:
                    continue
                fav_flag = " ★" if st.is_favorite else ""
                parts.append(f"- {st.name}{fav_flag}: {len(items)} item(s)")

        if unassigned:
            parts.append(f"- Unassigned: {len(unassigned)} item(s)")

        return "\n".join(parts)