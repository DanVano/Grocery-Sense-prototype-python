"""
grocery_sense.data.repositories.shopping_list_repo

SQLite-backed persistence for ShoppingListItem objects.
"""

from __future__ import annotations

from typing import List, Optional
from contextlib import closing
from datetime import datetime

from grocery_sense.data.connection import get_connection
from grocery_sense.domain.models import ShoppingListItem


# ---------- Row mapping helpers ----------

def _row_to_shopping_item(row) -> ShoppingListItem:
    """
    Convert a SQLite row tuple into a ShoppingListItem dataclass.
    Ordering must match the SELECTs below.
    """
    (
        item_id,
        display_name,
        quantity,
        unit,
        item_ref_id,
        planned_store_id,
        added_by,
        added_at,
        is_checked_off,
        is_active,
        notes,
    ) = row

    return ShoppingListItem(
        id=item_id,
        display_name=display_name,
        quantity=quantity,
        unit=unit,
        item_id=item_ref_id,
        planned_store_id=planned_store_id,
        added_by=added_by,
        added_at=added_at,
        is_checked_off=bool(is_checked_off),
        is_active=bool(is_active),
        notes=notes,
    )


# ---------- CRUD operations ----------

def add_item(
    display_name: str,
    quantity: Optional[float] = None,
    unit: Optional[str] = None,
    item_id: Optional[int] = None,
    planned_store_id: Optional[int] = None,
    added_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> ShoppingListItem:
    """
    Add a new item to the shopping list and return it.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO shopping_list (
                display_name,
                quantity,
                unit,
                item_id,
                planned_store_id,
                added_by,
                added_at,
                is_checked_off,
                is_active,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
            """,
            (
                display_name,
                quantity,
                unit,
                item_id,
                planned_store_id,
                added_by,
                now,
                notes,
            ),
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT
                id, display_name, quantity, unit,
                item_id, planned_store_id,
                added_by, added_at,
                is_checked_off, is_active, notes
            FROM shopping_list
            WHERE id = ?
            """,
            (new_id,),
        )
        row = cur.fetchone()

    return _row_to_shopping_item(row)


def get_item_by_id(item_id: int) -> Optional[ShoppingListItem]:
    """
    Fetch a single shopping list item by ID.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id, display_name, quantity, unit,
                item_id, planned_store_id,
                added_by, added_at,
                is_checked_off, is_active, notes
            FROM shopping_list
            WHERE id = ?
            """,
            (item_id,),
        )
        row = cur.fetchone()

    return _row_to_shopping_item(row) if row else None


def list_active_items(
    include_checked_off: bool = False,
    store_id: Optional[int] = None,
) -> List[ShoppingListItem]:
    """
    List items that are still active. Optionally filter by store,
    and optionally include those already checked off.
    """
    where_clauses = ["is_active = 1"]

    params = []

    if not include_checked_off:
        where_clauses.append("is_checked_off = 0")

    if store_id is not None:
        where_clauses.append("planned_store_id = ?")
        params.append(store_id)

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT
            id, display_name, quantity, unit,
            item_id, planned_store_id,
            added_by, added_at,
            is_checked_off, is_active, notes
        FROM shopping_list
        WHERE {where_sql}
        ORDER BY added_at ASC, id ASC
    """

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [_row_to_shopping_item(r) for r in rows]


def mark_checked_off(item_id: int, checked: bool = True) -> None:
    """
    Mark a shopping item as checked off (or undo).
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE shopping_list
            SET is_checked_off = ?
            WHERE id = ?
            """,
            (1 if checked else 0, item_id),
        )


def soft_delete_item(item_id: int) -> None:
    """
    Soft-delete an item (keep history, but hide from active list).
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE shopping_list
            SET is_active = 0
            WHERE id = ?
            """,
            (item_id,),
        )


def clear_checked_off_items() -> None:
    """
    Mark all checked-off items as inactive. Useful after a completed shop.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE shopping_list
            SET is_active = 0
            WHERE is_checked_off = 1
            """
        )
