"""
Grocery_Sense.data.repositories.prices_repo

SQLite-backed persistence for PricePoint objects and basic price statistics.
"""

from __future__ import annotations

from typing import List, Optional, Tuple
from contextlib import closing
from datetime import datetime, timedelta

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.domain.models import PricePoint


# ---------- Row mapping helpers ----------

def _row_to_price_point(row) -> PricePoint:
    """
    Convert a SQLite row tuple into a PricePoint dataclass.
    Ordering must match the SELECTs below.
    """
    (
        price_id,
        item_id,
        store_id,
        receipt_id,
        flyer_source_id,
        source,
        date,
        unit_price,
        unit,
        quantity,
        total_price,
        raw_name,
        confidence,
        created_at,
    ) = row

    return PricePoint(
        id=price_id,
        item_id=item_id,
        store_id=store_id,
        source=source,
        date=date,
        unit_price=unit_price,
        unit=unit,
        quantity=quantity,
        total_price=total_price,
        receipt_id=receipt_id,
        flyer_source_id=flyer_source_id,
        raw_name=raw_name,
        confidence=confidence,
    )


# ---------- Insert operations ----------

def add_price_point(
    item_id: int,
    store_id: int,
    source: str,
    date: str,
    unit_price: float,
    unit: str,
    quantity: Optional[float] = None,
    total_price: Optional[float] = None,
    receipt_id: Optional[int] = None,
    flyer_source_id: Optional[int] = None,
    raw_name: Optional[str] = None,
    confidence: Optional[int] = None,
) -> PricePoint:
    """
    Insert a new price history entry and return the PricePoint.

    `source` should be one of: 'receipt', 'flyer', 'manual'.
    `date` is 'YYYY-MM-DD'.
    `unit_price` should be normalized (e.g. per kg).
    """
    now = datetime.utcnow().isoformat(timespec="seconds")

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO prices (
                item_id,
                store_id,
                receipt_id,
                flyer_source_id,
                source,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                store_id,
                receipt_id,
                flyer_source_id,
                source,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence,
                now,
            ),
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT
                id,
                item_id,
                store_id,
                receipt_id,
                flyer_source_id,
                source,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence,
                created_at
            FROM prices
            WHERE id = ?
            """,
            (new_id,),
        )
        row = cur.fetchone()

    return _row_to_price_point(row)


# ---------- Query helpers ----------

def get_prices_for_item(
    item_id: int,
    days_back: Optional[int] = None,
    store_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[PricePoint]:
    """
    Fetch price history for a given item.

    - Optionally restrict to a store.
    - Optionally restrict to the last `days_back` days.
    - Optionally limit number of records (most recent first).
    """
    clauses = ["item_id = ?"]
    params: list = [item_id]

    if store_id is not None:
        clauses.append("store_id = ?")
        params.append(store_id)

    if days_back is not None and days_back > 0:
        cutoff_date = (datetime.utcnow() - timedelta(days=days_back)).date().isoformat()
        clauses.append("date >= ?")
        params.append(cutoff_date)

    where_sql = " AND ".join(clauses)
    limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""

    query = f"""
        SELECT
            id,
            item_id,
            store_id,
            receipt_id,
            flyer_source_id,
            source,
            date,
            unit_price,
            unit,
            quantity,
            total_price,
            raw_name,
            confidence,
            created_at
        FROM prices
        WHERE {where_sql}
        ORDER BY date DESC, id DESC
        {limit_sql}
    """

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [_row_to_price_point(r) for r in rows]


def get_most_recent_price(
    item_id: int,
    store_id: Optional[int] = None,
) -> Optional[PricePoint]:
    """
    Get the single most recent price entry for an item, optionally for one store.
    """
    pts = get_prices_for_item(
        item_id=item_id,
        days_back=None,
        store_id=store_id,
        limit=1,
    )
    return pts[0] if pts else None


def get_price_stats_for_item(
    item_id: int,
    window_days: int = 180,
) -> Optional[Tuple[float, float, float, int]]:
    """
    Compute basic statistics (avg, min, max, count) for an item over a window.

    Returns:
        (avg_unit_price, min_unit_price, max_unit_price, sample_count)
    or None if there are no data points.
    """
    cutoff_date = (datetime.utcnow() - timedelta(days=window_days)).date().isoformat()

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                AVG(unit_price) AS avg_price,
                MIN(unit_price) AS min_price,
                MAX(unit_price) AS max_price,
                COUNT(*)        AS sample_count
            FROM prices
            WHERE item_id = ?
              AND date >= ?
            """,
            (item_id, cutoff_date),
        )
        row = cur.fetchone()

    if not row:
        return None

    avg_price, min_price, max_price, count = row
    if count == 0 or avg_price is None:
        return None

    return float(avg_price), float(min_price), float(max_price), int(count)
