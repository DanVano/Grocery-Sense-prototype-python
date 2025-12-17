"""
Grocery_Sense.services.shopping_list_service

Service layer for shopping list behavior.

This wraps the shopping_list_repo (SQLite) and provides
higher-level operations suitable for UI / future mobile integration.

✅ Now includes Ingredient → Item mapping:
- add_single_item() will attempt to map display_name to a canonical item_id
- high-confidence fuzzy matches auto-learn aliases (via IngredientMappingService)
- mapping info is optionally appended to notes for visibility/debug
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional

from Grocery_Sense.data.repositories.shopping_list_repo import (
    add_item,
    get_item_by_id,
    list_active_items,
    mark_checked_off,
    clear_checked_off_items,
    soft_delete_item,
)
from Grocery_Sense.domain.models import ShoppingListItem

from Grocery_Sense.data.repositories import items_repo
from Grocery_Sense.services.ingredient_mapping_service import IngredientMappingService, MappingResult


class ShoppingListService:
    """
    High-level shopping list operations.
    The UI (Tkinter now, mobile later) should call this instead of
    talking to the repository directly.
    """

    def __init__(
        self,
        mapping_service: Optional[IngredientMappingService] = None,
        map_debug_to_notes: bool = True,
    ) -> None:
        # IngredientMappingService expects an object with list_all_item_names()
        # Passing the items_repo module works because it exposes that function.
        self.mapping_service = mapping_service or IngredientMappingService(items_repo=items_repo)
        self.map_debug_to_notes = map_debug_to_notes

    # ---------- Mapping helpers ----------

    def map_ingredient_name(self, raw_name: str) -> MappingResult:
        """
        Map a raw user/recipe ingredient string to a canonical item_id.
        This also auto-learns high-confidence fuzzy matches into item_aliases
        (handled inside IngredientMappingService).
        """
        return self.mapping_service.map_ingredient(raw_name)

    def _append_mapping_note(
        self,
        notes: Optional[str],
        mapping: MappingResult,
    ) -> Optional[str]:
        """
        Optional: add mapping info to notes so it’s visible during prototyping.
        """
        if not self.map_debug_to_notes:
            return notes

        if not mapping or not mapping.item_id:
            return notes

        canonical = mapping.canonical_name
        if not canonical:
            it = items_repo.get_item_by_id(mapping.item_id)
            canonical = it.canonical_name if it else None

        canonical = canonical or f"item_id={mapping.item_id}"
        tag = f"map→{canonical} ({mapping.confidence:.2f}, {mapping.method})"

        if notes and notes.strip():
            return f"{notes.strip()} | {tag}"
        return tag

    # ---------- Basic list operations ----------

    def add_single_item(
        self,
        name: str,
        quantity: Optional[float] = None,
        unit: Optional[str] = None,
        planned_store_id: Optional[int] = None,
        added_by: Optional[str] = None,
        notes: Optional[str] = None,
        item_id: Optional[int] = None,
        auto_map: bool = True,
    ) -> ShoppingListItem:
        """
        Add a single item to the shopping list.

        ✅ If item_id is not provided and auto_map=True, we try to map the name
        to a canonical item_id using IngredientMappingService.
        """
        cleaned_name = self._normalize_name(name)

        mapping: Optional[MappingResult] = None
        final_item_id = item_id

        if final_item_id is None and auto_map:
            mapping = self.map_ingredient_name(cleaned_name)
            if mapping and mapping.item_id:
                final_item_id = mapping.item_id
                notes = self._append_mapping_note(notes, mapping)

        return add_item(
            display_name=cleaned_name,
            quantity=quantity,
            unit=unit,
            item_id=final_item_id,  # ✅ mapped if possible
            planned_store_id=planned_store_id,
            added_by=added_by,
            notes=notes,
        )

    def add_items_from_text(
        self,
        text: str,
        planned_store_id: Optional[int] = None,
        added_by: Optional[str] = None,
    ) -> List[ShoppingListItem]:
        """
        Parse a comma-separated text input (e.g. 'apples, 2x milk, chicken thighs')
        into multiple shopping list entries.

        Very simple parsing for now:
          - split by commas
          - trim whitespace
          - ignore empty segments
          - add each segment as an item (and attempt mapping)
        """
        if not text:
            return []

        parts = [p.strip() for p in text.split(",")]
        parts = [p for p in parts if p]

        created_items: List[ShoppingListItem] = []
        for part in parts:
            item = self.add_single_item(
                name=part,
                quantity=None,
                unit=None,
                planned_store_id=planned_store_id,
                added_by=added_by,
                notes=None,
                item_id=None,
                auto_map=True,
            )
            created_items.append(item)

        return created_items

    def get_item(self, item_id: int) -> Optional[ShoppingListItem]:
        """
        Fetch a shopping list item by ID.
        """
        return get_item_by_id(item_id)

    def get_active_items(
        self,
        include_checked_off: bool = False,
        store_id: Optional[int] = None,
    ) -> List[ShoppingListItem]:
        """
        Return the list of active shopping list items.
        """
        return list_active_items(
            include_checked_off=include_checked_off,
            store_id=store_id,
        )

    def get_active_items_grouped_by_store(
        self,
    ) -> Dict[Optional[int], List[ShoppingListItem]]:
        """
        Return active items grouped by planned_store_id.
        """
        items = list_active_items(include_checked_off=False, store_id=None)
        grouped: Dict[Optional[int], List[ShoppingListItem]] = {}
        for item in items:
            key = item.planned_store_id
            grouped.setdefault(key, []).append(item)
        return grouped

    def check_off_item(self, item_id: int, checked: bool = True) -> None:
        """
        Mark a single item as checked or unchecked.
        """
        mark_checked_off(item_id, checked)

    def clear_checked_off(self) -> int:
        """
        Soft-delete (deactivate) any checked-off items.
        Returns the number cleared.
        """
        return clear_checked_off_items()

    def soft_delete_item(self, item_id: int) -> None:
        """
        Soft-delete (deactivate) an item.
        """
        soft_delete_item(item_id)

    def export_active_items_as_dicts(
        self,
        include_checked_off: bool = False,
        store_id: Optional[int] = None,
    ) -> List[Dict]:
        """
        Convenience method for UI/export. Returns list of dicts.
        """
        items = self.get_active_items(include_checked_off=include_checked_off, store_id=store_id)
        return [asdict(i) for i in items]

    # ---------- Utility ----------

    @staticmethod
    def _normalize_name(name: str) -> str:
        """
        Basic normalization for display_name:
          - strip whitespace
          - collapse double spaces
        """
        if not name:
            return ""
        cleaned = " ".join(name.split())
        return cleaned
