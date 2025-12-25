from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from Grocery_Sense.data.connection import get_connection


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_sha256(file_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    p = Path(file_path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass(frozen=True)
class StoreRow:
    id: int
    name: str


class FlyersRepo:
    """
    DB layer for Flyers / Flyer Assets / Raw JSON / Deals.

    Stores:
      - flyer_batches: one import session (store + date range + source)
      - flyer_assets: each PDF/image file attached to a batch
      - flyer_raw_json: Azure Layout raw result per asset
      - flyer_deals: extracted DealRecords per batch

    NOTE: We keep this minimal & robust so you can swap the extractor later.
    """

    def ensure_schema(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER,
                    valid_from TEXT,
                    valid_to TEXT,
                    source_type TEXT NOT NULL,     -- manual_upload | retailer_web | aggregator_partner
                    source_ref TEXT,              -- folder path, URL, etc
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    asset_path TEXT NOT NULL,
                    asset_type TEXT NOT NULL,      -- pdf | image
                    page_index INTEGER,
                    sha256 TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                );
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_raw_json (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    operation_id TEXT,
                    model_id TEXT NOT NULL DEFAULT 'prebuilt-layout',
                    json_path TEXT,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES flyer_assets(id) ON DELETE CASCADE
                );
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    store_id INTEGER,
                    asset_id INTEGER,
                    page_index INTEGER,

                    title TEXT,
                    description TEXT,
                    price_text TEXT,

                    deal_qty REAL,
                    deal_total REAL,
                    unit_price REAL,
                    unit TEXT,

                    norm_unit_price REAL,
                    norm_unit TEXT,
                    norm_note TEXT,

                    item_id INTEGER,
                    mapping_confidence REAL,

                    confidence REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),

                    FOREIGN KEY (flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES flyer_assets(id) ON DELETE SET NULL
                );
                """
            )

            conn.commit()

    # -------------------------
    # Stores helper (UI)
    # -------------------------

    def list_stores(self) -> List[StoreRow]:
        """
        Minimal store listing without depending on stores_repo signatures.
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, name
                FROM stores
                ORDER BY COALESCE(priority, 9999) ASC, COALESCE(is_favorite, 0) DESC, name ASC;
                """
            ).fetchall()

        return [StoreRow(id=int(r[0]), name=str(r[1] or "")) for r in rows]

    # -------------------------
    # Flyer batch
    # -------------------------

    def create_flyer_batch(
        self,
        *,
        store_id: Optional[int],
        valid_from: Optional[str],
        valid_to: Optional[str],
        source_type: str,
        source_ref: Optional[str],
        note: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_batches (store_id, valid_from, valid_to, source_type, source_ref, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    int(store_id) if store_id is not None else None,
                    valid_from,
                    valid_to,
                    source_type,
                    source_ref,
                    note,
                    _now_utc_iso(),
                ),
            )
            fid = int(cur.lastrowid)
            conn.commit()
            return fid

    def add_asset(
        self,
        *,
        flyer_id: int,
        asset_path: str,
        asset_type: str,
        page_index: Optional[int] = None,
        sha256: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_assets (flyer_id, asset_path, asset_type, page_index, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (int(flyer_id), asset_path, asset_type, page_index, sha256, _now_utc_iso()),
            )
            aid = int(cur.lastrowid)
            conn.commit()
            return aid

    def add_raw_json(
        self,
        *,
        flyer_id: int,
        asset_id: int,
        operation_id: str,
        json_path: Optional[str],
        raw_json_dict: Dict[str, Any],
        model_id: str = "prebuilt-layout",
    ) -> int:
        self.ensure_schema()
        raw_text = json.dumps(raw_json_dict, ensure_ascii=False)
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_raw_json (flyer_id, asset_id, operation_id, model_id, json_path, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (int(flyer_id), int(asset_id), operation_id, model_id, json_path, raw_text, _now_utc_iso()),
            )
            rid = int(cur.lastrowid)
            conn.commit()
            return rid

    def add_deal(
        self,
        *,
        flyer_id: int,
        store_id: Optional[int],
        asset_id: Optional[int],
        page_index: Optional[int],

        title: str,
        description: str,
        price_text: Optional[str],

        deal_qty: Optional[float],
        deal_total: Optional[float],
        unit_price: Optional[float],
        unit: Optional[str],

        norm_unit_price: Optional[float],
        norm_unit: Optional[str],
        norm_note: Optional[str],

        item_id: Optional[int],
        mapping_confidence: Optional[float],
        confidence: Optional[float],
    ) -> int:
        self.ensure_schema()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_deals (
                    flyer_id, store_id, asset_id, page_index,
                    title, description, price_text,
                    deal_qty, deal_total, unit_price, unit,
                    norm_unit_price, norm_unit, norm_note,
                    item_id, mapping_confidence, confidence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    int(flyer_id),
                    int(store_id) if store_id is not None else None,
                    int(asset_id) if asset_id is not None else None,
                    int(page_index) if page_index is not None else None,

                    title,
                    description,
                    price_text,

                    float(deal_qty) if deal_qty is not None else None,
                    float(deal_total) if deal_total is not None else None,
                    float(unit_price) if unit_price is not None else None,
                    unit,

                    float(norm_unit_price) if norm_unit_price is not None else None,
                    norm_unit,
                    norm_note,

                    int(item_id) if item_id is not None else None,
                    float(mapping_confidence) if mapping_confidence is not None else None,
                    float(confidence) if confidence is not None else None,

                    _now_utc_iso(),
                ),
            )
            did = int(cur.lastrowid)
            conn.commit()
            return did

    def list_deals_for_flyer(self, flyer_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    d.id, d.flyer_id, d.store_id,
                    COALESCE(s.name, '') as store_name,
                    d.page_index,
                    d.title, d.description, d.price_text,
                    d.deal_qty, d.deal_total, d.unit_price, d.unit,
                    d.norm_unit_price, d.norm_unit, d.norm_note,
                    d.item_id, COALESCE(i.canonical_name, '') as item_name,
                    d.mapping_confidence, d.confidence, d.created_at
                FROM flyer_deals d
                LEFT JOIN stores s ON s.id = d.store_id
                LEFT JOIN items i ON i.id = d.item_id
                WHERE d.flyer_id = ?
                ORDER BY d.id DESC
                LIMIT ?;
                """,
                (int(flyer_id), int(limit)),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "flyer_id": int(r[1]),
                    "store_id": r[2],
                    "store_name": r[3],
                    "page_index": r[4],
                    "title": r[5],
                    "description": r[6],
                    "price_text": r[7],
                    "deal_qty": r[8],
                    "deal_total": r[9],
                    "unit_price": r[10],
                    "unit": r[11],
                    "norm_unit_price": r[12],
                    "norm_unit": r[13],
                    "norm_note": r[14],
                    "item_id": r[15],
                    "item_name": r[16],
                    "mapping_confidence": r[17],
                    "confidence": r[18],
                    "created_at": r[19],
                }
            )
        return out
