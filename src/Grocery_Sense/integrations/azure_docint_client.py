"""
Grocery_Sense.integrations.azure_docint_client

Single-file prototype: Azure AI Document Intelligence (prebuilt-receipt) -> JSON -> DB ingest

PART 1 (TOP): Azure call + raw JSON persistence
PART 2 (BOTTOM): Parse receipt fields + line items -> store in SQLite tables immediately

Requires:
  pip install azure-ai-documentintelligence azure-core rapidfuzz

Environment variables (recommended):
  DOCUMENTINTELLIGENCE_ENDPOINT="https://<resource-name>.cognitiveservices.azure.com/"
  DOCUMENTINTELLIGENCE_API_KEY="<your_key>"

Supported file types: JPG/JPEG/PNG/PDF/TIFF
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rapidfuzz import fuzz, process

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories import items_repo as items_repo_module
from Grocery_Sense.data.repositories.item_aliases_repo import ItemAliasesRepo
from Grocery_Sense.data.repositories.stores_repo import create_store, list_stores
from Grocery_Sense.services.ingredient_mapping_service import IngredientMappingService


# =============================================================================
# PART 1: Azure upload/analyze + raw JSON saving
# =============================================================================

@dataclass(frozen=True)
class AzureReceiptResult:
    operation_id: str
    analyze_result: Dict[str, Any]  # JSON-safe dict (AnalyzeResult.as_dict())
    saved_json_path: Path


class AzureReceiptClient:
    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        locale: str = "en-US",
    ) -> None:
        self.endpoint = endpoint or os.environ.get("DOCUMENTINTELLIGENCE_ENDPOINT", "").strip()
        self.api_key = api_key or os.environ.get("DOCUMENTINTELLIGENCE_API_KEY", "").strip()
        self.locale = locale

        if not self.endpoint or not self.api_key:
            raise RuntimeError(
                "Missing Azure Document Intelligence credentials.\n"
                "Set DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY environment variables."
            )

        self.client = DocumentIntelligenceClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.api_key),
        )

    def analyze_receipt_file(self, file_path: str | Path) -> Tuple[str, Dict[str, Any]]:
        """
        Analyze ONE document (image or pdf) with prebuilt-receipt.
        Returns: (operation_id, analyze_result_dict)
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(str(p))

        with p.open("rb") as f:
            poller = self.client.begin_analyze_document(
                "prebuilt-receipt",
                body=f,
                locale=self.locale,
            )
        result = poller.result()
        operation_id = str(poller.details.get("operation_id") or "")
        if not operation_id:
            # Extremely defensive fallback; should usually exist.
            operation_id = f"op_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{p.stem}"

        result_dict = result.as_dict()  # JSON serializable
        return operation_id, result_dict

    def analyze_and_save_json(
        self,
        file_path: str | Path,
        raw_json_dir: str | Path,
    ) -> AzureReceiptResult:
        """
        Analyze and save raw JSON to disk.
        """
        raw_dir = Path(raw_json_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

        operation_id, result_dict = self.analyze_receipt_file(file_path)

        src = Path(file_path)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]+", "_", src.stem)[:80]
        out_path = raw_dir / f"{safe_name}__{operation_id}.json"

        out_path.write_text(
            json.dumps(result_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return AzureReceiptResult(operation_id=operation_id, analyze_result=result_dict, saved_json_path=out_path)


# =============================================================================
# PART 2: Parse receipt JSON + store into Grocery Sense DB
# =============================================================================

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _confidence_to_1_5(conf: Optional[float]) -> Optional[int]:
    if conf is None:
        return None
    try:
        c = float(conf)
    except Exception:
        return None
    if c >= 0.90:
        return 5
    if c >= 0.75:
        return 4
    if c >= 0.60:
        return 3
    if c >= 0.40:
        return 2
    return 1


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    s = s.replace(",", "")
    s = re.sub(r"[^\d\.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _pick_field(fields: Dict[str, Any], names: Iterable[str]) -> Optional[Dict[str, Any]]:
    if not fields:
        return None
    lower = {k.lower(): k for k in fields.keys()}
    for n in names:
        key = lower.get(n.lower())
        if key and isinstance(fields.get(key), dict):
            return fields[key]
    return None


def _field_value(field: Optional[Dict[str, Any]]) -> Tuple[Any, Optional[float]]:
    """
    Returns (value, confidence_float).
    Works against the dict produced by AnalyzeResult.as_dict().
    """
    if not field:
        return None, None

    conf = field.get("confidence")
    # Common value slots from DI JSON:
    for k in (
        "valueString",
        "valueNumber",
        "valueDate",
        "valueTime",
        "valuePhoneNumber",
        "valueCurrency",  # sometimes a dict like {"amount": 1.23, "currencySymbol": "$"}
        "valueInteger",
        "valueBoolean",
    ):
        if k in field:
            return field.get(k), conf

    # Fallback:
    if "content" in field:
        return field.get("content"), conf

    return None, conf


def _currency_amount(v: Any) -> Optional[float]:
    """
    valueCurrency can be {"amount": X, ...} or sometimes already a number/string.
    """
    if v is None:
        return None
    if isinstance(v, dict):
        return _safe_float(v.get("amount"))
    return _safe_float(v)


def _ensure_ingest_tables() -> None:
    """
    Adds two small tables for the ingest pipeline, without changing your existing schema:
      - receipt_raw_json: store full raw JSON response (one row per receipt)
      - receipt_line_items: store parsed line items including discount (since prices table has no discount col)
    """
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_raw_json (
                receipt_id  INTEGER PRIMARY KEY,
                operation_id TEXT,
                json_path    TEXT,
                raw_json     TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_line_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id   INTEGER NOT NULL,
                line_index   INTEGER NOT NULL,
                item_id      INTEGER,
                description  TEXT,
                quantity     REAL,
                unit_price   REAL,
                line_total   REAL,
                discount     REAL,
                confidence   INTEGER,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL
            );
            """
        )
        conn.commit()


def _get_or_create_store_id(merchant_name: str, threshold: int = 85) -> int:
    merchant_name = (merchant_name or "").strip()
    if not merchant_name:
        merchant_name = "Unknown Store"

    stores = list_stores(only_favorites=False, order_by_priority=False)
    if not stores:
        created = create_store(name=merchant_name)
        return int(created.id)

    # Build a list of names for fuzzy match.
    store_names = [s.name for s in stores]
    match = process.extractOne(
        merchant_name,
        store_names,
        scorer=fuzz.token_set_ratio,
    )

    if match:
        best_name, score, _ = match
        if score >= threshold:
            # Return matched store id
            for s in stores:
                if s.name == best_name:
                    return int(s.id)

    # No good match -> create new store
    created = create_store(name=merchant_name)
    return int(created.id)


def _insert_receipt_row(
    store_id: int,
    purchase_date: str,
    subtotal: Optional[float],
    tax: Optional[float],
    total: Optional[float],
    source: str,
    file_path: str,
    image_confidence_1_5: Optional[int],
    azure_request_id: str,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO receipts (
                store_id, purchase_date, subtotal_amount, tax_amount, total_amount,
                source, file_path, image_overall_confidence, keep_image_until,
                azure_request_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                store_id,
                purchase_date,
                subtotal,
                tax,
                total,
                source,
                file_path,
                image_confidence_1_5,
                None,  # keep_image_until (optional; you can set retention later)
                azure_request_id,
                _now_utc_iso(),
            ),
        )
        receipt_id = int(cur.lastrowid)
        conn.commit()
        return receipt_id


def _save_raw_json_row(receipt_id: int, operation_id: str, json_path: Path, raw_json_dict: Dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO receipt_raw_json (receipt_id, operation_id, json_path, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                receipt_id,
                operation_id,
                str(json_path),
                json.dumps(raw_json_dict, ensure_ascii=False),
                _now_utc_iso(),
            ),
        )
        conn.commit()


def _upsert_item_from_mapping(raw_desc: str, mapping: Any) -> Tuple[int, Optional[int]]:
    """
    mapping is MappingResult from IngredientMappingService.
    Returns: (item_id, confidence_1_5)
    """
    # If mapped:
    if getattr(mapping, "item_id", None):
        conf = getattr(mapping, "confidence", None)
        return int(mapping.item_id), _confidence_to_1_5(conf)

    # Otherwise create canonical item immediately (prototype behavior)
    cleaned = (raw_desc or "").strip()
    if not cleaned:
        cleaned = "Unknown Item"

    created = items_repo_module.create_item(canonical_name=cleaned)
    item_id = int(created.id)

    # Also learn alias so it matches next time
    try:
        aliases = ItemAliasesRepo()
        aliases.upsert_alias(alias_text=raw_desc, item_id=item_id, confidence=0.60, source="receipt_auto")
    except Exception:
        # Alias learning is helpful but non-critical
        pass

    return item_id, 2


def _insert_price_point(
    item_id: int,
    store_id: int,
    receipt_id: int,
    date: str,
    unit_price: float,
    unit: str,
    quantity: Optional[float],
    total_price: Optional[float],
    raw_name: str,
    confidence_1_5: Optional[int],
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO prices (
                item_id, store_id, receipt_id, flyer_source_id, source, date,
                unit_price, unit, quantity, total_price, raw_name, confidence, created_at
            )
            VALUES (?, ?, ?, NULL, 'receipt', ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                item_id,
                store_id,
                receipt_id,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence_1_5,
                _now_utc_iso(),
            ),
        )
        conn.commit()


def _insert_receipt_line_item(
    receipt_id: int,
    line_index: int,
    item_id: Optional[int],
    description: str,
    quantity: Optional[float],
    unit_price: Optional[float],
    line_total: Optional[float],
    discount: Optional[float],
    confidence_1_5: Optional[int],
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO receipt_line_items (
                receipt_id, line_index, item_id, description, quantity,
                unit_price, line_total, discount, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                receipt_id,
                line_index,
                item_id,
                description,
                quantity,
                unit_price,
                line_total,
                discount,
                confidence_1_5,
                _now_utc_iso(),
            ),
        )
        conn.commit()


def ingest_analyzed_receipt_into_db(
    *,
    file_path: str | Path,
    operation_id: str,
    analyze_result: Dict[str, Any],
    saved_json_path: Path,
    store_match_threshold: int = 85,
) -> int:
    """
    Takes the raw DI JSON dict and inserts:
      - receipts row
      - receipt_raw_json row (full JSON)
      - receipt_line_items rows (parsed, including discount)
      - prices rows (normalized price history per item)

    Returns receipt_id.
    """
    _ensure_ingest_tables()

    docs = analyze_result.get("documents") or []
    if not docs:
        raise ValueError("No documents found in AnalyzeResult JSON. (Unexpected for prebuilt-receipt)")

    receipt_doc = docs[0]
    fields = receipt_doc.get("fields") or {}

    # Header fields
    merchant_name_val, merchant_conf = _field_value(_pick_field(fields, ["MerchantName", "Merchant"]))
    merchant_name = (merchant_name_val or "").strip() if isinstance(merchant_name_val, str) else str(merchant_name_val or "").strip()
    store_id = _get_or_create_store_id(merchant_name, threshold=store_match_threshold)

    tx_date_val, tx_date_conf = _field_value(_pick_field(fields, ["TransactionDate", "Date"]))
    # purchase_date in your schema is YYYY-MM-DD
    if isinstance(tx_date_val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", tx_date_val.strip()):
        purchase_date = tx_date_val.strip()
    else:
        # If it came through as "2025-12-19" it's already ok;
        # otherwise fall back to today.
        purchase_date = datetime.now().strftime("%Y-%m-%d")

    subtotal_val, subtotal_conf = _field_value(_pick_field(fields, ["Subtotal"]))
    tax_val, tax_conf = _field_value(_pick_field(fields, ["TotalTax", "Tax"]))
    total_val, total_conf = _field_value(_pick_field(fields, ["Total"]))

    subtotal = _currency_amount(subtotal_val)
    tax = _currency_amount(tax_val)
    total = _currency_amount(total_val)

    # overall confidence (rough heuristic)
    overall_conf_float = None
    confs = [c for c in [merchant_conf, tx_date_conf, subtotal_conf, tax_conf, total_conf] if isinstance(c, (int, float))]
    if confs:
        overall_conf_float = sum(float(x) for x in confs) / len(confs)
    overall_conf_1_5 = _confidence_to_1_5(overall_conf_float)

    receipt_id = _insert_receipt_row(
        store_id=store_id,
        purchase_date=purchase_date,
        subtotal=subtotal,
        tax=tax,
        total=total,
        source="receipt",
        file_path=str(file_path),
        image_confidence_1_5=overall_conf_1_5,
        azure_request_id=operation_id,
    )

    _save_raw_json_row(receipt_id, operation_id, saved_json_path, analyze_result)

    # Line items
    items_field = _pick_field(fields, ["Items", "ItemList", "LineItems"])
    items_value, items_conf = _field_value(items_field)

    # Azure DI "Items" is usually a valueArray of objects
    value_array = None
    if isinstance(items_field, dict):
        value_array = items_field.get("valueArray")
    if not isinstance(value_array, list):
        value_array = []

    # Ingredient mapping engine (fuzzy + alias learn)
    mapping_service = IngredientMappingService(
        items_repo=items_repo_module,
        aliases_repo=ItemAliasesRepo(),
        auto_learn=True,
        learn_threshold=0.90,
        accept_threshold=0.75,
    )

    for idx, elem in enumerate(value_array):
        # elem typically has {"valueObject": {...}, "confidence": 0.xx}
        obj = (elem or {}).get("valueObject") if isinstance(elem, dict) else None
        if not isinstance(obj, dict):
            continue

        desc_val, desc_conf = _field_value(_pick_field(obj, ["Description", "Name", "Item"]))
        qty_val, qty_conf = _field_value(_pick_field(obj, ["Quantity", "Qty"]))
        unit_price_val, unit_price_conf = _field_value(_pick_field(obj, ["UnitPrice", "Price"]))
        total_price_val, total_price_conf = _field_value(_pick_field(obj, ["TotalPrice", "LineTotal", "Amount"]))
        discount_val, discount_conf = _field_value(_pick_field(obj, ["Discount", "DiscountAmount"]))

        description = (desc_val or "").strip() if isinstance(desc_val, str) else str(desc_val or "").strip()
        if not description:
            continue

        quantity = _safe_float(qty_val)
        if quantity is None:
            quantity = 1.0  # your rule: treat as 1 if not stated

        unit_price = _currency_amount(unit_price_val)
        line_total = _currency_amount(total_price_val)
        discount = _currency_amount(discount_val)

        # If unit_price missing, try derive from line_total / quantity
        if unit_price is None and line_total is not None and quantity:
            unit_price = float(line_total) / float(quantity)

        # If line_total missing, derive
        if line_total is None and unit_price is not None and quantity:
            line_total = float(unit_price) * float(quantity)

        # Choose a unit for now (prototype). Later you can infer kg/lb from item metadata or text.
        unit = "each"

        # Compute line confidence
        conf_candidates = [
            c for c in [desc_conf, qty_conf, unit_price_conf, total_price_conf, discount_conf]
            if isinstance(c, (int, float))
        ]
        line_conf_float = (sum(float(x) for x in conf_candidates) / len(conf_candidates)) if conf_candidates else None
        line_conf_1_5 = _confidence_to_1_5(line_conf_float)

        # Map to canonical item_id; if not found, auto-create
        mapping = mapping_service.map_to_item(description)
        item_id, map_conf_1_5 = _upsert_item_from_mapping(description, mapping)

        # Store parsed line item (including discount)
        _insert_receipt_line_item(
            receipt_id=receipt_id,
            line_index=idx,
            item_id=item_id,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
            discount=discount,
            confidence=line_conf_1_5 or map_conf_1_5,
        )

        # Store price point (normalized history)
        # NOTE: prices table has no discount column; discount is kept in receipt_line_items + raw JSON.
        if unit_price is not None:
            _insert_price_point(
                item_id=item_id,
                store_id=store_id,
                receipt_id=receipt_id,
                date=purchase_date,
                unit_price=float(unit_price),
                unit=unit,
                quantity=quantity,
                total_price=line_total,
                raw_name=description,
                confidence_1_5=(line_conf_1_5 or map_conf_1_5),
            )

    return receipt_id


# =============================================================================
# Convenience runner: analyze -> save JSON -> ingest, sequentially
# =============================================================================

def ingest_receipt_file(
    file_path: str | Path,
    raw_json_dir: str | Path = "azure_raw_json",
    locale: str = "en-US",
    store_match_threshold: int = 85,
) -> int:
    """
    One-call convenience:
      - analyze 1 receipt file in Azure
      - save raw JSON
      - parse + ingest into DB
    """
    client = AzureReceiptClient(locale=locale)
    az = client.analyze_and_save_json(file_path=file_path, raw_json_dir=raw_json_dir)
    receipt_id = ingest_analyzed_receipt_into_db(
        file_path=file_path,
        operation_id=az.operation_id,
        analyze_result=az.analyze_result,
        saved_json_path=az.saved_json_path,
        store_match_threshold=store_match_threshold,
    )
    return receipt_id


def ingest_receipts_in_folder(
    folder_path: str | Path,
    raw_json_dir: str | Path = "azure_raw_json",
    locale: str = "en-US",
    store_match_threshold: int = 85,
    extensions: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff"),
) -> List[Tuple[str, int]]:
    """
    Sequentially ingest all supported files in a folder (sorted by name).
    Returns list of (filename, receipt_id).
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise NotADirectoryError(str(folder))

    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions]
    files.sort(key=lambda p: p.name.lower())

    results: List[Tuple[str, int]] = []
    for p in files:
        rid = ingest_receipt_file(
            file_path=p,
            raw_json_dir=raw_json_dir,
            locale=locale,
            store_match_threshold=store_match_threshold,
        )
        results.append((p.name, rid))
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Azure prebuilt-receipt ingest -> Grocery Sense DB")
    parser.add_argument("path", help="Receipt file path OR folder path")
    parser.add_argument("--raw-json-dir", default="azure_raw_json", help="Folder to store raw DI JSON outputs")
    parser.add_argument("--locale", default="en-US", help="Locale for receipt analysis (default: en-US)")
    parser.add_argument("--store-threshold", type=int, default=85, help="Fuzzy store match threshold (0-100)")
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_dir():
        out = ingest_receipts_in_folder(
            p,
            raw_json_dir=args.raw_json_dir,
            locale=args.locale,
            store_match_threshold=args.store_threshold,
        )
        for name, rid in out:
            print(f"{name} -> receipt_id={rid}")
    else:
        rid = ingest_receipt_file(
            p,
            raw_json_dir=args.raw_json_dir,
            locale=args.locale,
            store_match_threshold=args.store_threshold,
        )
        print(f"{p.name} -> receipt_id={rid}")
