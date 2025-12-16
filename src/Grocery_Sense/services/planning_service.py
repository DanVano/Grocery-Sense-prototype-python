"""
Grocery_Sense.services.planning_service

Service layer for planning which stores to visit for the current shopping list.

This version:
  - Reads active shopping list items.
  - Reads all known stores (with favorite/priority info).
  - Looks at historical prices per item per store.
  - Chooses up to `max_stores` that cover most items, biased by favorites/priority.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional, Tuple
from statistics import mean

from Grocery_Sense.data.repositories.stores_repo import list_stores
from Grocery_Sense.data.repositories.items_repo import get_item_by_name
from Grocery_Sense.data.repositories.prices_repo import get_prices_for_item
from Grocery_Sense.domain.models import Store, ShoppingListItem
from Grocery_Sense.services.shopping_list_service import ShoppingListService


class PlanningService:
    """
    High-level store planning.

    Key method:
      - build_plan_for_active_list(max_stores=3)

    Returns a dict with:
      {
        "stores": {
          store_id: {
            "store": Store,
            "items": [ShoppingListItem, ...],
          },
          ...
        },
        "unassigned": [ShoppingListItem, ...],
        "summary": str,
      }
    """

    def __init__(self) -> None:
        self._shopping = ShoppingListService()

    # ---------- Public API ----------

    def build_plan_for_active_list(
        self,
        max_stores: int = 3,
    ) -> Dict[str, object]:
        """
        Build a store visit plan for all active shopping list items.

        Strategy (v1, greedy & simple):
          1) Get all active shopping list items.
          2) Get all stores.
          3) For each item, find cheapest store (by avg historical unit_price).
          4) Score stores by how many items they win, plus a bias for favorites/priority.
          5) Choose up to `max_stores` with highest scores.
          6) Assign items to their chosen store if it's in that set; otherwise
             assign to the first favorite store or the top-scoring store.
        """
        items = self._shopping.get_active_items(include_checked_off=False, store_id=None)
        stores = list_stores()

        if not items or not stores:
            return {
                "stores": {},
                "unassigned": items or [],
                "summary": "No plan possible (no items or no stores configured).",
            }

        # Map store_id -> Store for quick lookup
        store_by_id: Dict[int, Store] = {s.id: s for s in stores}

        # Step 3: best (cheapest) store per item using history
        item_best_store: Dict[int, Optional[int]] = {}
        for itm in items:
            best_store_id, _ = self._find_best_store_for_item(itm, stores)
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

        if not store_scores:
            # No price history at all; fall back to favorites / highest priority
            chosen_store_ids = self._fallback_stores(stores, max_stores)
        else:
            chosen_store_ids = [
                s_id
                for s_id, _ in sorted(
                    store_scores.items(),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:max_stores]
            ]

        # Step 6: assign items to stores, or leave unassigned
        plan_by_store: Dict[int, List[ShoppingListItem]] = {sid: [] for sid in chosen_store_ids}
        unassigned: List[ShoppingListItem] = []

        # Choose a generic fallback store if needed
        fallback_store_id = self._choose_generic_fallback_store(stores, chosen_store_ids)

        for itm in items:
            best_store_id = item_best_store.get(itm.id)
            if best_store_id in chosen_store_ids:
                plan_by_store[best_store_id].append(itm)
            elif fallback_store_id is not None:
                plan_by_store.setdefault(fallback_store_id, []).append(itm)
            else:
                unassigned.append(itm)

        summary = self._build_summary(plan_by_store, unassigned, store_by_id)

        # Convert to final structure with Store objects
        stores_struct: Dict[int, Dict[str, object]] = {}
        for sid, its in plan_by_store.items():
            st = store_by_id.get(sid)
            if not st:
                continue
            stores_struct[sid] = {
                "store": st,
                "items": its,
            }

        return {
            "stores": stores_struct,
            "unassigned": unassigned,
            "summary": summary,
        }

    # ---------- Internal helpers ----------

    def _find_best_store_for_item(
        self,
        shopping_item: ShoppingListItem,
        stores: List[Store],
        days_back: int = 180,
    ) -> Tuple[Optional[int], Optional[float]]:
        """
        For a given ShoppingListItem, check historical prices across stores
        and return (best_store_id, best_avg_price) or (None, None) if no data.

        Uses the shopping_item.display_name as the canonical_name for now.
        Later, we can wire ShoppingListItem.item_id -> Item directly.
        """
        name = shopping_item.display_name.strip()
        item_row = get_item_by_name(name)
        if not item_row:
            return None, None

        best_store_id: Optional[int] = None
        best_price: Optional[float] = None

        for store in stores:
            history = get_prices_for_item(
                item_id=item_row.id,
                days_back=days_back,
                store_id=store.id,
                limit=10,
            )
            if not history:
                continue
            avg_price = mean(p.unit_price for p in history if p.unit_price is not None)
            if best_price is None or avg_price < best_price:
                best_price = avg_price
                best_store_id = store.id

        return best_store_id, best_price

    @staticmethod
    def _fallback_stores(
        stores: List[Store],
        max_stores: int,
    ) -> List[int]:
        """
        When there's no price history at all, choose stores based on:
          - favorites first
          - then by priority
          - then by name
        """
        sorted_stores = sorted(
            stores,
            key=lambda s: (
                0 if s.is_favorite else 1,
                -(s.priority or 0),
                s.name.lower(),
            ),
        )
        return [s.id for s in sorted_stores[:max_stores]]

    @staticmethod
    def _choose_generic_fallback_store(
        stores: List[Store],
        chosen_store_ids: List[int],
    ) -> Optional[int]:
        """
        Choose a single store to use as a fallback when an item has no price history
        or its best store is outside the chosen set.

        Strategy:
          - If any chosen store is favorite, return the favorite with highest priority.
          - Else return the first chosen store.
          - Else, fall back to the first store overall (if any).
        """
        if not stores:
            return None

        # Restrict to stores we already decided to visit
        chosen_stores = [s for s in stores if s.id in chosen_store_ids]

        # 1) favorite among chosen
        favs = [s for s in chosen_stores if s.is_favorite]
        if favs:
            fav_sorted = sorted(favs, key=lambda s: -(s.priority or 0))
            return fav_sorted[0].id

        # 2) first chosen
        if chosen_stores:
            return chosen_stores[0].id

        # 3) any store
        return stores[0].id if stores else None

    @staticmethod
    def _build_summary(
        plan_by_store: Dict[int, List[ShoppingListItem]],
        unassigned: List[ShoppingListItem],
        store_by_id: Dict[int, Store],
    ) -> str:
        """
        Build a human-readable summary of the plan for debugging / UI.
        """
        parts: List[str] = []

        total_items = sum(len(lst) for lst in plan_by_store.values()) + len(unassigned)
        parts.append(f"Planned {total_items} item(s) across {len(plan_by_store)} store(s).")

        for sid, items in plan_by_store.items():
            st = store_by_id.get(sid)
            if not st:
                continue
            fav_flag = " (favorite)" if st.is_favorite else ""
            parts.append(f"- {st.name}{fav_flag}: {len(items)} item(s)")
            preview_names = ", ".join(i.display_name for i in items[:5])
            if preview_names:
                parts.append(f"    e.g. {preview_names}")

        if unassigned:
            parts.append(
                f"Unassigned items (no price history or no stores configured): "
                + ", ".join(i.display_name for i in unassigned[:5])
            )

        return "\n".join(parts)
