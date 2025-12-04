"""
grocery_sense.data.repositories.items_repo

SQLite-backed persistence for Item objects.
"""

from __future__ import annotations

from typing import List, Optional
from contextlib import closing
from datetime import datetime

from grocery_sense.data.connection import get_connection
from grocery_sense.domain.models import Item


# ---------- Row mapping helpers ----------

def _row_to_item(row) -> Item:
    """
    Convert a SQLite row tuple into an Item dataclass.
    Ordering must match the SELECT statements below.
    """
    (
        item_id,
        canonical_name,
        category,
        default_unit,
        typical_package_size,
        typical_package_unit,
        is_tracked,
        notes,
        created_at,
    ) = row

    return Item(
        id=item_id,
        canonical_name=canonical_name,
        category=category,
        default_unit=default_unit,
        typical_package_size=typical_package_size,
        typical_package_unit=typical_package_unit,
        is_tracked=bool(is_tracked),
        notes=notes,
    )


# ---------- CRUD operations ----------

def create_item(
    canonical_name: str,
    category: Optional[str] = None,
    default_unit: Optional[str] = None,
    typical_package_size: Optional[float] = None,
    typical_package_unit: Optional[str] = None,
    is_tracked: bool = True,
    notes: Optional[str] = None,
) -> Item:
    """
    Insert a new Item and return it.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO items (
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                is_tracked,
                notes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                1 if is_tracked else 0,
                notes,
                now,
            ),
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT
                id,
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                is_tracked,
                notes,
                created_at
            FROM items
            WHERE id = ?
            """,
            (new_id,),
        )
        row = cur.fetchone()

    return _row_to_item(row)


def get_item_by_id(item_id: int) -> Optional[Item]:
    """
    Lookup an item by its ID.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                is_tracked,
                notes,
                created_at
            FROM items
            WHERE id = ?
            """,
            (item_id,),
        )
        row = cur.fetchone()

    return _row_to_item(row) if row else None


def get_item_by_name(canonical_name: str) -> Optional[Item]:
    """
    Lookup an item by its canonical_name (case-insensitive).
    """
    name_normalized = canonical_name.strip().lower()
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                is_tracked,
                notes,
                created_at
            FROM items
            WHERE lower(canonical_name) = ?
            """,
            (name_normalized,),
        )
        row = cur.fetchone()

    return _row_to_item(row) if row else None


def list_items(
    only_tracked: bool = False,
    search_text: Optional[str] = None,
) -> List[Item]:
    """
    List items, optionally filtered by is_tracked and/or a search substring.
    """
    clauses = []
    params: list = []

    if only_tracked:
        clauses.append("is_tracked = 1")

    if search_text:
        clauses.append("lower(canonical_name) LIKE ?")
        params.append(f"%{search_text.strip().lower()}%")

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    query = f"""
        SELECT
            id,
            canonical_name,
            category,
            default_unit,
            typical_package_size,
            typical_package_unit,
            is_tracked,
            notes,
            created_at
        FROM items
        {where_sql}
        ORDER BY canonical_name ASC
    """

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [_row_to_item(r) for r in rows]


def set_item_tracked(item_id: int, is_tracked: bool) -> None:
    """
    Mark an item as tracked/untracked.
    Untracked items remain in history but won't show up by default.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE items
            SET is_tracked = ?
            WHERE id = ?
            """,
            (1 if is_tracked else 0, item_id),
        )


def update_item_package_info(
    item_id: int,
    typical_package_size: Optional[float],
    typical_package_unit: Optional[str],
) -> None:
    """
    Update typical package size/unit for an item.
    Helps normalize per-kg / per-unit comparisons later.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE items
            SET typical_package_size = ?, typical_package_unit = ?
            WHERE id = ?
            """,
            (typical_package_size, typical_package_unit, item_id),
        )
