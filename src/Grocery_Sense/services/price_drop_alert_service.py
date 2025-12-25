from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from Grocery_Sense.data.connection import get_connection


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _percent_drop(avg_price: float, observed: float) -> float:
    if avg_price <= 0:
        return 0.0
    return ((avg_price - observed) / avg_price) * 100.0


@dataclass(frozen=True)
class AlertCreateResult:
    created_count: int
    skipped_count: int


class PriceDropAlertService:
    """
    Creates + lists "price drop" alerts.

    Rules (v1):
      - Only items where items.is_tracked = 1
      - Compare observed receipt unit_price to avg over last N days (default 45)
      - Prefer same-store average if enough samples; otherwise fallback to overall average
      - Require min_samples (default 3)
      - Trigger if percent_drop >= threshold_percent (default 20%)
    """

    def ensure_tables(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_drop_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',

                    receipt_id INTEGER,
                    item_id INTEGER NOT NULL,
                    store_id INTEGER,

                    observed_date TEXT,
                    observed_unit_price REAL NOT NULL,

                    avg_window_days INTEGER NOT NULL,
                    avg_price REAL,
                    sample_count INTEGER,

                    percent_drop REAL,
                    threshold_percent REAL,
                    basis TEXT,           -- 'store' | 'overall'
                    note TEXT,

                    UNIQUE(receipt_id, item_id, store_id) ON CONFLICT IGNORE
                );
                """
            )
            conn.commit()

    # -------------------- Public API --------------------

    def detect_for_receipt(
        self,
        receipt_id: int,
        *,
        threshold_percent: float = 20.0,
        window_days: int = 45,
        min_samples: int = 3,
    ) -> AlertCreateResult:
        """
        Looks at prices created for THIS receipt, creates alerts for tracked items.
        Safe to call repeatedly (unique key prevents duplicates for same receipt/item/store).
        """
        self.ensure_tables()

        # Pull observed prices from this receipt for tracked items only
        observed_rows = self._get_observed_prices_for_receipt(receipt_id)
        created = 0
        skipped = 0

        for obs in observed_rows:
            item_id = int(obs["item_id"])
            store_id = int(obs["store_id"]) if obs["store_id"] is not None else None
            observed_price = obs["unit_price"]
            observed_date = obs["date"]

            if observed_price is None:
                skipped += 1
                continue

            # 1) Try same-store stats
            avg_store, n_store = self._avg_for_item(
                item_id=item_id,
                store_id=store_id,
                window_days=window_days,
                exclude_receipt_id=receipt_id,
            ) if store_id is not None else (None, 0)

            basis = None
            avg_price = None
            sample_count = 0

            if avg_store is not None and n_store >= min_samples:
                basis = "store"
                avg_price = avg_store
                sample_count = n_store
            else:
                # 2) Fallback overall stats (all stores)
                avg_overall, n_overall = self._avg_for_item(
                    item_id=item_id,
                    store_id=None,
                    window_days=window_days,
                    exclude_receipt_id=receipt_id,
                )
                if avg_overall is not None and n_overall >= min_samples:
                    basis = "overall"
                    avg_price = avg_overall
                    sample_count = n_overall

            if avg_price is None or sample_count < min_samples:
                skipped += 1
                continue

            drop = _percent_drop(avg_price, float(observed_price))
            if drop < float(threshold_percent):
                skipped += 1
                continue

            # Insert alert (unique prevents duplicates)
            ok = self._insert_alert(
                receipt_id=receipt_id,
                item_id=item_id,
                store_id=store_id,
                observed_date=observed_date,
                observed_unit_price=float(observed_price),
                window_days=window_days,
                avg_price=float(avg_price),
                sample_count=int(sample_count),
                percent_drop=float(drop),
                threshold_percent=float(threshold_percent),
                basis=basis or "overall",
            )

            if ok:
                created += 1
            else:
                skipped += 1

        return AlertCreateResult(created_count=created, skipped_count=skipped)

    def detect_for_recent_receipts(
        self,
        *,
        receipts_days_back: int = 7,
        threshold_percent: float = 20.0,
        window_days: int = 45,
        min_samples: int = 3,
    ) -> AlertCreateResult:
        """
        Convenience: scan recent receipts and create alerts.
        """
        self.ensure_tables()
        receipt_ids = self._get_recent_receipt_ids(days_back=receipts_days_back)

        total_created = 0
        total_skipped = 0
        for rid in receipt_ids:
            res = self.detect_for_receipt(
                rid,
                threshold_percent=threshold_percent,
                window_days=window_days,
                min_samples=min_samples,
            )
            total_created += res.created_count
            total_skipped += res.skipped_count

        return AlertCreateResult(created_count=total_created, skipped_count=total_skipped)

    def list_open_alerts(self, limit: int = 250) -> List[Dict[str, Any]]:
        self.ensure_tables()
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    a.id,
                    a.created_at,
                    a.receipt_id,
                    a.item_id,
                    COALESCE(i.canonical_name, '') as item_name,
                    a.store_id,
                    COALESCE(s.name, '') as store_name,
                    a.observed_date,
                    a.observed_unit_price,
                    a.avg_price,
                    a.sample_count,
                    a.percent_drop,
                    a.threshold_percent,
                    a.basis
                FROM price_drop_alerts a
                LEFT JOIN items i ON i.id = a.item_id
                LEFT JOIN stores s ON s.id = a.store_id
                WHERE a.status = 'open'
                ORDER BY a.id DESC
                LIMIT ?;
                """,
                (int(limit),),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "created_at": r[1],
                    "receipt_id": r[2],
                    "item_id": r[3],
                    "item_name": r[4],
                    "store_id": r[5],
                    "store_name": r[6],
                    "observed_date": r[7],
                    "observed_unit_price": r[8],
                    "avg_price": r[9],
                    "sample_count": r[10],
                    "percent_drop": r[11],
                    "threshold_percent": r[12],
                    "basis": r[13],
                }
            )
        return out

    def dismiss_alert(self, alert_id: int) -> None:
        self.ensure_tables()
        with get_connection() as conn:
            conn.execute(
                "UPDATE price_drop_alerts SET status = 'dismissed' WHERE id = ?;",
                (int(alert_id),),
            )
            conn.commit()

    def dismiss_all(self) -> None:
        self.ensure_tables()
        with get_connection() as conn:
            conn.execute("UPDATE price_drop_alerts SET status = 'dismissed' WHERE status = 'open';")
            conn.commit()

    # -------------------- Internal helpers --------------------

    def _get_observed_prices_for_receipt(self, receipt_id: int) -> List[Dict[str, Any]]:
        """
        Pull prices from this receipt for tracked items only.
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.item_id,
                    p.store_id,
                    p.unit_price,
                    p.date
                FROM prices p
                JOIN items i ON i.id = p.item_id
                WHERE p.receipt_id = ?
                  AND p.source = 'receipt'
                  AND i.is_tracked = 1
                  AND p.unit_price IS NOT NULL;
                """,
                (int(receipt_id),),
            ).fetchall()

        return [
            {"item_id": r[0], "store_id": r[1], "unit_price": r[2], "date": r[3]}
            for r in rows
        ]

    def _avg_for_item(
        self,
        *,
        item_id: int,
        store_id: Optional[int],
        window_days: int,
        exclude_receipt_id: int,
    ) -> Tuple[Optional[float], int]:
        """
        Average unit_price over last window_days.
        Excludes the current receipt_id so you compare against "recent history".
        """
        with get_connection() as conn:
            if store_id is None:
                rows = conn.execute(
                    """
                    SELECT unit_price
                    FROM prices
                    WHERE item_id = ?
                      AND unit_price IS NOT NULL
                      AND source = 'receipt'
                      AND receipt_id <> ?
                      AND date >= date('now', ?)
                    """,
                    (int(item_id), int(exclude_receipt_id), f"-{int(window_days)} days"),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT unit_price
                    FROM prices
                    WHERE item_id = ?
                      AND store_id = ?
                      AND unit_price IS NOT NULL
                      AND source = 'receipt'
                      AND receipt_id <> ?
                      AND date >= date('now', ?)
                    """,
                    (int(item_id), int(store_id), int(exclude_receipt_id), f"-{int(window_days)} days"),
                ).fetchall()

        vals = [float(r[0]) for r in rows if r and r[0] is not None]
        if not vals:
            return None, 0
        return (sum(vals) / len(vals), len(vals))

    def _insert_alert(
        self,
        *,
        receipt_id: int,
        item_id: int,
        store_id: Optional[int],
        observed_date: Optional[str],
        observed_unit_price: float,
        window_days: int,
        avg_price: float,
        sample_count: int,
        percent_drop: float,
        threshold_percent: float,
        basis: str,
    ) -> bool:
        """
        Returns True if inserted, False if ignored (duplicate).
        """
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO price_drop_alerts (
                    created_at, status,
                    receipt_id, item_id, store_id,
                    observed_date, observed_unit_price,
                    avg_window_days, avg_price, sample_count,
                    percent_drop, threshold_percent, basis, note
                )
                VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    _now_utc_iso(),
                    int(receipt_id),
                    int(item_id),
                    int(store_id) if store_id is not None else None,
                    observed_date,
                    float(observed_unit_price),
                    int(window_days),
                    float(avg_price),
                    int(sample_count),
                    float(percent_drop),
                    float(threshold_percent),
                    basis,
                    None,
                ),
            )
            conn.commit()

        # sqlite: rowcount is 1 when inserted, 0 when ignored
        return getattr(cur, "rowcount", 0) == 1

    def _get_recent_receipt_ids(self, *, days_back: int) -> List[int]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM receipts
                WHERE purchase_date >= date('now', ?)
                ORDER BY id DESC;
                """,
                (f"-{int(days_back)} days",),
            ).fetchall()
        return [int(r[0]) for r in rows]
