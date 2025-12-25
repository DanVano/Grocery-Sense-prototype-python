from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DealAdjusted:
    quantity: float
    unit_price: Optional[float]
    line_total: Optional[float]
    deal_note: str


class MultiBuyDealService:
    """
    Normalize common multi-buy deal formats into an "effective" unit price.

    Handles (v1):
      - "2/$5", "2 / $5.00", "2/5"
      - "3 for 10", "3 for $10.00"
      - "2 @ 4.00" (interpreted as 2 units at $4 each)
      - "Buy 1 get 1", "BOGO", "buy one get one"

    Strategy:
      - Prefer actual receipt amounts when available (line_total and/or discount),
        because some receipts already apply the promo price.
      - If promo pattern indicates bundle qty but receipt qty is missing/wrong,
        adjust quantity (conservatively) only when it matches totals.
    """

    # Regex patterns
    _re_slash = re.compile(r"\b(\d+)\s*/\s*\$?\s*(\d+(?:\.\d+)?)\b")          # 2/$5
    _re_for = re.compile(r"\b(\d+)\s*for\s*\$?\s*(\d+(?:\.\d+)?)\b")          # 3 for 10
    _re_at = re.compile(r"\b(\d+)\s*@\s*\$?\s*(\d+(?:\.\d+)?)\b")             # 2 @ 4.00
    _re_bogo = re.compile(r"\b(bogo|buy\s*1\s*get\s*1|buy\s*one\s*get\s*one)\b", re.IGNORECASE)

    def adjust(
        self,
        *,
        description: str,
        quantity: Optional[float],
        unit_price: Optional[float],
        line_total: Optional[float],
        discount: Optional[float],
    ) -> DealAdjusted:
        desc = (description or "").strip()
        q = float(quantity) if quantity and quantity > 0 else 1.0
        up = float(unit_price) if unit_price is not None else None
        lt = float(line_total) if line_total is not None else None
        disc = float(discount) if discount is not None else 0.0

        # 1) BOGO-like promotions: compute effective unit price using net total if possible
        if self._re_bogo.search(desc):
            # Use receipt net total if possible:
            # - some receipts show line_total as gross and discount as promo
            # - some show net already (discount 0)
            base_total = lt if lt is not None else (up * q if up is not None else None)
            if base_total is not None and q >= 2:
                net_total = base_total - (disc or 0.0)
                if net_total > 0:
                    eff = net_total / q
                    return DealAdjusted(quantity=q, unit_price=eff, line_total=net_total, deal_note="bogo_effective_price")
            return DealAdjusted(quantity=q, unit_price=up, line_total=lt, deal_note="bogo_detected_no_adjust")

        # 2) Bundle price patterns: 2/$5, 3 for 10
        bundle = self._parse_bundle_price(desc)
        if bundle is not None:
            bundle_qty, bundle_total = bundle

            # If receipt provides a line total, trust it (but reconcile qty if it's obviously a bundle line)
            if lt is not None:
                # If quantity looks wrong and line_total matches bundle_total, fix q
                if (q < bundle_qty) and self._close(lt, bundle_total):
                    q2 = float(bundle_qty)
                    net_total = lt - (disc or 0.0)
                    eff = net_total / q2 if q2 > 0 else None
                    return DealAdjusted(quantity=q2, unit_price=eff, line_total=net_total, deal_note=f"bundle({bundle_qty}/${bundle_total})_qty_fix")

                # If qty is multiple of bundle qty and totals align, still compute effective from totals
                net_total = lt - (disc or 0.0)
                if q > 0:
                    eff = net_total / q
                    return DealAdjusted(quantity=q, unit_price=eff, line_total=net_total, deal_note=f"bundle({bundle_qty}/${bundle_total})_from_total")

            # No line total: fall back to stated promo math
            eff = bundle_total / float(bundle_qty)
            # If quantity is a multiple of bundle qty, keep q and compute implied line total
            implied_total = eff * q
            implied_total = implied_total - (disc or 0.0)
            return DealAdjusted(quantity=q, unit_price=eff, line_total=implied_total, deal_note=f"bundle({bundle_qty}/${bundle_total})_from_text")

        # 3) "2 @ 4.00" means 2 units at $4 each
        at = self._parse_at_price(desc)
        if at is not None:
            at_qty, each_price = at

            # If unit_price missing, set it
            if up is None or up <= 0:
                up2 = each_price
            else:
                up2 = up

            # If receipt qty is missing/wrong and at_qty makes sense, bump it
            q2 = q
            if q < at_qty:
                # Only bump if totals match or totals missing
                if lt is None:
                    q2 = float(at_qty)
                else:
                    # If line total looks like at_qty * each_price, bump
                    if self._close(lt, float(at_qty) * float(each_price)):
                        q2 = float(at_qty)

            # If line_total missing, compute it
            lt2 = lt
            if lt2 is None and up2 is not None:
                lt2 = (up2 * q2) - (disc or 0.0)

            # If line_total present, compute effective using net total / qty
            if lt2 is not None and q2 > 0:
                net_total = lt2 - (disc or 0.0) if lt is not None else lt2
                eff = net_total / q2
                return DealAdjusted(quantity=q2, unit_price=eff, line_total=net_total, deal_note=f"at({at_qty}@{each_price})")

            return DealAdjusted(quantity=q2, unit_price=up2, line_total=lt2, deal_note=f"at({at_qty}@{each_price})_no_total")

        # 4) No deal detected: optionally compute unit_price if missing from totals
        if (up is None or up <= 0) and (lt is not None) and q > 0:
            net_total = lt - (disc or 0.0)
            eff = net_total / q
            return DealAdjusted(quantity=q, unit_price=eff, line_total=net_total, deal_note="unit_from_total")

        return DealAdjusted(quantity=q, unit_price=up, line_total=lt, deal_note="no_deal")

    # -----------------------------
    # Parsers
    # -----------------------------

    def _parse_bundle_price(self, text: str) -> Optional[Tuple[int, float]]:
        t = (text or "").lower()

        m = self._re_slash.search(t)
        if m:
            qty = int(m.group(1))
            total = float(m.group(2))
            if qty > 0 and total > 0:
                return qty, total

        m = self._re_for.search(t)
        if m:
            qty = int(m.group(1))
            total = float(m.group(2))
            if qty > 0 and total > 0:
                return qty, total

        return None

    def _parse_at_price(self, text: str) -> Optional[Tuple[int, float]]:
        t = (text or "").lower()
        m = self._re_at.search(t)
        if not m:
            return None
        qty = int(m.group(1))
        each = float(m.group(2))
        if qty > 0 and each > 0:
            return qty, each
        return None

    # -----------------------------
    # Utils
    # -----------------------------

    def _close(self, a: float, b: float, tol: float = 0.02) -> bool:
        # currency tolerance
        return abs(float(a) - float(b)) <= tol
