"""
grocery_sense.services.shopping_list_service

Service layer for shopping list behavior.

This wraps the shopping_list_repo (which talks to SQLite) and provides
higher-level operations suitable for UI / future mobile integration.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional

from grocery_sense.data.repositories.shopping_list_repo import (
    add_item,
    get_item_by_id,
    list_active_items,
    mark_checked_off,
    clear_checked_off_items,
    soft_delete_item,
)
from grocery_sense.domain.models import ShoppingListItem


class ShoppingListService:
    """
    High-level shopping list operations.

    The UI (Tkinter now, mobile later) should call this instead of
    talking to the repository directly.
    """

    # ---------- Basic list operations ----------

    def add_single_item(
        self,
        name: str,
        quantity: Optional[float] = None,
        unit: Optional[str] = None,
        planned_store_id: Optional[int] = None,
        added_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> ShoppingListItem:
        """
        Add a single item to the shopping list.
        Does a bit of normalization and returns the created item.
        """
        cleaned_name = self._normalize_name(name)
        return add_item(
            display_name=cleaned_name,
            quantity=quantity,
            unit=unit,
            item_id=None,  # canonical Item mapping can be added later
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
          - send each segment as a display_name

        Returns the list of created ShoppingListItem objects.
        """
        if not text:
            return []

        segments = [seg.strip() for seg in text.split(",")]
        segments = [s for s in segments if s]  # drop empties

        created_items: List[ShoppingListItem] = []
        for seg in segments:
            cleaned = self._normalize_name(seg)
            item = add_item(
                display_name=cleaned,
                quantity=None,
                unit=None,
                item_id=None,
                planned_store_id=planned_store_id,
                added_by=added_by,
                notes=None,
            )
            created_items.append(item)

        return created_items

    def get_active_items(
        self,
        include_checked_off: bool = False,
        store_id: Optional[int] = None,
    ) -> List[ShoppingListItem]:
        """
        Return the list of active shopping list items, optionally filtered by store,
        optionally including already checked-off items.
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

        The key is the store_id (or None if not assigned).
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
        mark_checked_off(item_id, checked=checked)

    def soft_delete_item(self, item_id: int) -> None:
        """
        Soft delete an item from the list (is_active = 0).
        """
        soft_delete_item(item_id)

    def clear_all_checked_off(self) -> None:
        """
        Clear all items that have been checked off from the active list.
        The records remain in the DB but are marked inactive.
        """
        clear_checked_off_items()

    # ---------- Higher-level helpers ----------

    def summarize_list_for_display(
        self,
        include_checked_off: bool = False,
    ) -> str:
        """
        Return a human-readable summary of the current list.
        This is mainly for debugging / CLI usage now, later can drive UI.
        """
        items = list_active_items(include_checked_off=include_checked_off)
        if not items:
            return "Shopping list is currently empty."

        lines: List[str] = []
        for item in items:
            status = "✓" if item.is_checked_off else " "
            qty = f"{item.quantity:g} " if item.quantity is not None else ""
            unit = f"{item.unit} " if item.unit else ""
            store_str = f"(store={item.planned_store_id})" if item.planned_store_id else ""
            lines.append(
                f"[{status}] #{item.id} {qty}{unit}{item.display_name} {store_str}".strip()
            )
        return "\n".join(lines)

    def get_item_as_dict(self, item_id: int) -> Optional[dict]:
        """
        Utility for UI or debugging: fetch a single item and return as a dict.
        """
        item = get_item_by_id(item_id)
        return asdict(item) if item else None

    # ---------- Internal helpers ----------

    @staticmethod
    def _normalize_name(name: str) -> str:
        """
        Basic normalization for display_name:
          - strip whitespace
          - collapse double spaces
          - keep original casing for user readability, but we could lower()
            if we later want strict deduplication.
        """
        if not name:
            return ""
        cleaned = " ".join(name.split())
        return cleaned
