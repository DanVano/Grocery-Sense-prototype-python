from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from Grocery_Sense.data.connection import get_connection


LB_TO_KG = 0.45359237
KG_TO_LB = 2.2046226218


@dataclass(frozen=True)
class NormalizedPrice:
    norm_unit_price: float
    norm_unit: str
    note: str


class UnitNormalizationService:
    """
    Unit normalization v1

    Stores:
      - items.default_unit (TEXT)
      - prices.norm_unit_price (REAL)
      - prices.norm_unit (TEXT)
      - prices.norm_note (TEXT)

    Rules:
      - If item has no default_unit, set it to the observed unit (if meaningful).
      - If observed unit differs from item default_unit, convert when possible:
          lb <-> kg
          g  <-> kg
      - If unit is unknown, treat as 'each' (no conversion).
    """

    # ----------------------------
    # Schema ensure
    # ----------------------------

    def ensure_schema(self) -> None:
        self._ensure_items_default_unit_column()
        self._ensure_prices_norm_columns()

    def _column_exists(self, table: str, col: str) -> bool:
        with get_connection() as conn:
            rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
        return any((r[1] == col) for r in rows)  # r[1] = column name

    def _ensure_items_default_unit_column(self) -> None:
        if self._column_exists("items", "default_unit"):
            return
        with get_connection() as conn:
            conn.execute("ALTER TABLE items ADD COLUMN default_unit TEXT;")
            conn.commit()

    def _ensure_prices_norm_columns(self) -> None:
        # Add norm columns if missing
        with get_connection() as conn:
            # We must check each one; SQLite doesn't support ALTER COLUMN IF NOT EXISTS
            rows = conn.execute("PRAGMA table_info(prices);").fetchall()
            existing = {r[1] for r in rows}

            if "norm_unit_price" not in existing:
                conn.execute("ALTER TABLE prices ADD COLUMN norm_unit_price REAL;")
            if "norm_unit" not in existing:
                conn.execute("ALTER TABLE prices ADD COLUMN norm_unit TEXT;")
            if "norm_note" not in existing:
                conn.execute("ALTER TABLE prices ADD COLUMN norm_note TEXT;")

            conn.commit()

    # ----------------------------
    # Default unit getters/setters
    # ----------------------------

    def get_item_default_unit(self, item_id: int) -> Optional[str]:
        self.ensure_schema()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT default_unit FROM items WHERE id = ?;",
                (int(item_id),),
            ).fetchone()
        if not row:
            return None
        v = row[0]
        if not v:
            return None
        return str(v).strip().lower() or None

    def set_item_default_unit_if_missing(self, item_id: int, observed_unit: str) -> None:
        """
        If items.default_unit is NULL/empty, set it to observed_unit.
        Only sets meaningful units (each/lb/kg/g).
        """
        self.ensure_schema()
        observed_unit = self._normalize_unit(observed_unit)

        if observed_unit not in ("each", "lb", "kg", "g"):
            return

        cur = self.get_item_default_unit(item_id)
        if cur:
            return

        with get_connection() as conn:
            conn.execute(
                "UPDATE items SET default_unit = ? WHERE id = ?;",
                (observed_unit, int(item_id)),
            )
            conn.commit()

    # ----------------------------
    # Public normalization API
    # ----------------------------

    def normalize(
        self,
        *,
        item_id: int,
        unit_price: float,
        observed_unit: str,
        description: Optional[str] = None,
    ) -> NormalizedPrice:
        """
        Returns normalized price into the item's default unit.
        If no default unit, we set it to observed unit and treat that as default.
        """
        self.ensure_schema()

        obs = self._normalize_unit(observed_unit)
        if obs == "unknown":
            # try infer from description
            guessed = self.guess_unit_from_text(description or "")
            obs = guessed

        # If still unknown -> each
        if obs == "unknown":
            obs = "each"

        # Ensure default exists
        self.set_item_default_unit_if_missing(item_id, obs)
        default_unit = self.get_item_default_unit(item_id) or obs

        # If already matches, no conversion
        if default_unit == obs:
            return NormalizedPrice(
                norm_unit_price=float(unit_price),
                norm_unit=default_unit,
                note="no_conversion",
            )

        # Convert between lb and kg (and g<->kg)
        converted = self._convert(unit_price=float(unit_price), from_unit=obs, to_unit=default_unit)
        if converted is None:
            # Can't convert -> keep observed
            return NormalizedPrice(
                norm_unit_price=float(unit_price),
                norm_unit=obs,
                note=f"no_conversion_possible({obs}->{default_unit})",
            )

        return NormalizedPrice(
            norm_unit_price=float(converted),
            norm_unit=default_unit,
            note=f"converted({obs}->{default_unit})",
        )

    # ----------------------------
    # Unit inference
    # ----------------------------

    def guess_unit_from_text(self, text: str) -> str:
        """
        Basic heuristics from receipt text.
        Examples it catches:
          - "1.25 kg", "KG", "kg @"
          - "2.0 lb", "LB", "lbs"
          - "500 g", "G"
        """
        t = (text or "").lower()

        # kg
        if re.search(r"\bkg\b", t) or re.search(r"\bkilogram(s)?\b", t):
            return "kg"

        # g (avoid matching 'g' in random words by requiring number near it)
        if re.search(r"(\d+(\.\d+)?)\s*g\b", t) or re.search(r"\bgrams?\b", t):
            return "g"

        # lb / lbs / #
        if re.search(r"\blb(s)?\b", t) or re.search(r"\bpound(s)?\b", t) or re.search(r"\b#\b", t):
            return "lb"

        return "unknown"

    # ----------------------------
    # Internals
    # ----------------------------

    def _normalize_unit(self, u: str) -> str:
        if not u:
            return "unknown"
        s = str(u).strip().lower()

        # common normalizations
        if s in ("ea", "each", "unit", "units", "ct", "count"):
            return "each"
        if s in ("lb", "lbs", "#", "pound", "pounds"):
            return "lb"
        if s in ("kg", "kgs", "kilogram", "kilograms"):
            return "kg"
        if s in ("g", "gram", "grams"):
            return "g"

        return "unknown"

    def _convert(self, *, unit_price: float, from_unit: str, to_unit: str) -> Optional[float]:
        """
        Convert a per-unit price between units.

        If price is $/lb and you want $/kg:
          $/kg = $/lb * (lb per kg) = $/lb * 2.20462262

        If price is $/kg and you want $/lb:
          $/lb = $/kg * (kg per lb) = $/kg * 0.45359237
        """
        from_unit = self._normalize_unit(from_unit)
        to_unit = self._normalize_unit(to_unit)

        if from_unit == to_unit:
            return float(unit_price)

        # lb <-> kg
        if from_unit == "lb" and to_unit == "kg":
            return float(unit_price) * KG_TO_LB  # $/lb -> $/kg
        if from_unit == "kg" and to_unit == "lb":
            return float(unit_price) * LB_TO_KG  # $/kg -> $/lb

        # g <-> kg (price per g to price per kg, etc.)
        if from_unit == "g" and to_unit == "kg":
            return float(unit_price) * 1000.0
        if from_unit == "kg" and to_unit == "g":
            return float(unit_price) / 1000.0

        return None
