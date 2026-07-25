from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ReceiptStatus,
    TaxTreatment,
    ValidationIssue,
    ValidationResult,
)
from recebako.storage.image_paths import validate_image_path


@dataclass(frozen=True)
class ReceiptWrite:
    extraction: NormalizedReceiptExtraction | None
    validation: ValidationResult
    phash: str
    image_path: Path
    ingest_mode: IngestMode
    raw_payload: str
    duplicate_of_id: int | None = None


@dataclass(frozen=True)
class StoredItem:
    id: int
    receipt_id: int
    name: str
    name_norm: str | None
    qty: int
    price: int
    price_raw: int
    tax_rate: int | None
    tax_treatment: TaxTreatment
    tax_adjustment: int
    category: str | None


@dataclass(frozen=True)
class StoredTaxBreakdown:
    id: int
    receipt_id: int
    tax_rate: int
    taxable_amount: int
    tax_amount: int
    tax_treatment: TaxTreatment


@dataclass(frozen=True)
class StoredReceipt:
    id: int
    store: str
    date_raw: str
    date: str
    time: str
    total: int
    subtotal: int
    tax: int
    payment: str
    category: str | None
    status: ReceiptStatus
    confidence: float
    phash: str
    image_path: str
    ingest_mode: IngestMode
    validation_issues: list[dict[str, Any]]
    raw_payload: Any
    duplicate_of_id: int | None
    items: list[StoredItem]
    tax_breakdowns: list[StoredTaxBreakdown]


def _encode_raw_payload(raw_payload: str) -> str:
    try:
        decoded: Any = json.loads(raw_payload)
    except json.JSONDecodeError:
        decoded = raw_payload
    return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))


def _encode_validation_issues(issues: list[ValidationIssue]) -> str:
    return json.dumps(
        [issue.model_dump(mode="json") for issue in issues],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class ReceiptRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, record: ReceiptWrite) -> int:
        extraction = record.extraction
        receipt_values = (
            (
                extraction.store,
                extraction.date_raw,
                extraction.date,
                extraction.time,
                extraction.total,
                extraction.subtotal,
                extraction.tax,
                extraction.payment,
                extraction.confidence,
            )
            if extraction is not None
            else ("", "", "", "", 0, 0, 0, "unknown", 0.0)
        )

        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO receipts (
                    store, date_raw, date, time, total, subtotal, tax, payment,
                    category, status, confidence, phash, image_path, ingest_mode,
                    validation_issues_json, raw_payload_json, duplicate_of_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *receipt_values[:8],
                    None,
                    record.validation.status.value,
                    receipt_values[8],
                    record.phash,
                    validate_image_path(record.image_path),
                    record.ingest_mode.value,
                    _encode_validation_issues(record.validation.issues),
                    _encode_raw_payload(record.raw_payload),
                    record.duplicate_of_id,
                ),
            )
            receipt_id = cursor.lastrowid
            if receipt_id is None:
                raise sqlite3.DatabaseError("receipt id was not generated")

            if extraction is not None:
                for item in extraction.items:
                    item_cursor = self._connection.execute(
                        """
                        INSERT INTO items (
                            receipt_id, name, name_norm, qty, price, category
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            receipt_id,
                            item.name,
                            None,
                            item.qty,
                            item.price,
                            None,
                        ),
                    )
                    item_id = item_cursor.lastrowid
                    if item_id is None:
                        raise sqlite3.DatabaseError("item id was not generated")
                    self._connection.execute(
                        """
                        INSERT INTO item_tax_details (
                            item_id, price_raw, tax_rate, tax_treatment,
                            tax_adjustment
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            item.price if item.price_raw is None else item.price_raw,
                            item.tax_rate,
                            item.tax_treatment.value,
                            item.tax_adjustment,
                        ),
                    )

                self._connection.executemany(
                    """
                    INSERT INTO receipt_tax_breakdowns (
                        receipt_id, tax_rate, taxable_amount, tax_amount,
                        tax_treatment
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            receipt_id,
                            breakdown.tax_rate,
                            breakdown.taxable_amount,
                            breakdown.tax_amount,
                            breakdown.tax_treatment.value,
                        )
                        for breakdown in extraction.tax_breakdowns
                    ],
                )

        return int(receipt_id)

    def update_image_path(self, receipt_id: int, image_path: Path) -> None:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE receipts
                SET image_path = ?
                WHERE id = ?
                """,
                (validate_image_path(image_path), receipt_id),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("更新対象のreceiptが見つかりません")

    def find_by_image_path(self, image_path: Path) -> StoredReceipt | None:
        rows = self._connection.execute(
            """
            SELECT id
            FROM receipts
            WHERE image_path = ?
            ORDER BY id
            """,
            (validate_image_path(image_path),),
        ).fetchall()
        if len(rows) > 1:
            raise sqlite3.DatabaseError("同じprocessingパスのreceiptが複数あります")
        return None if not rows else self.get(int(rows[0]["id"]))

    def list_with_image_path_prefix(self, prefix: str) -> list[StoredReceipt]:
        safe_prefix = validate_image_path(prefix)
        rows = self._connection.execute(
            """
            SELECT id
            FROM receipts
            WHERE image_path LIKE ?
            ORDER BY id
            """,
            (f"{safe_prefix}/%",),
        ).fetchall()
        return [
            receipt for row in rows if (receipt := self.get(int(row["id"]))) is not None
        ]

    def get(self, receipt_id: int) -> StoredReceipt | None:
        row = self._connection.execute(
            """
            SELECT *
            FROM receipts
            WHERE id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if row is None:
            return None

        item_rows = self._connection.execute(
            """
            SELECT
                i.id,
                i.receipt_id,
                i.name,
                i.name_norm,
                i.qty,
                i.price,
                i.category,
                COALESCE(t.price_raw, i.price) AS price_raw,
                t.tax_rate,
                COALESCE(t.tax_treatment, 'unknown') AS tax_treatment,
                COALESCE(t.tax_adjustment, 0) AS tax_adjustment
            FROM items AS i
            LEFT JOIN item_tax_details AS t ON t.item_id = i.id
            WHERE i.receipt_id = ?
            ORDER BY i.id
            """,
            (receipt_id,),
        ).fetchall()
        items = [
            StoredItem(
                id=item["id"],
                receipt_id=item["receipt_id"],
                name=item["name"],
                name_norm=item["name_norm"],
                qty=item["qty"],
                price=item["price"],
                price_raw=item["price_raw"],
                tax_rate=item["tax_rate"],
                tax_treatment=TaxTreatment(item["tax_treatment"]),
                tax_adjustment=item["tax_adjustment"],
                category=item["category"],
            )
            for item in item_rows
        ]
        tax_breakdown_rows = self._connection.execute(
            """
            SELECT
                id,
                receipt_id,
                tax_rate,
                taxable_amount,
                tax_amount,
                tax_treatment
            FROM receipt_tax_breakdowns
            WHERE receipt_id = ?
            ORDER BY id
            """,
            (receipt_id,),
        ).fetchall()
        tax_breakdowns = [
            StoredTaxBreakdown(
                id=breakdown["id"],
                receipt_id=breakdown["receipt_id"],
                tax_rate=breakdown["tax_rate"],
                taxable_amount=breakdown["taxable_amount"],
                tax_amount=breakdown["tax_amount"],
                tax_treatment=TaxTreatment(breakdown["tax_treatment"]),
            )
            for breakdown in tax_breakdown_rows
        ]
        return StoredReceipt(
            id=row["id"],
            store=row["store"],
            date_raw=row["date_raw"],
            date=row["date"],
            time=row["time"],
            total=row["total"],
            subtotal=row["subtotal"],
            tax=row["tax"],
            payment=row["payment"],
            category=row["category"],
            status=ReceiptStatus(row["status"]),
            confidence=row["confidence"],
            phash=row["phash"],
            image_path=row["image_path"],
            ingest_mode=IngestMode(row["ingest_mode"]),
            validation_issues=json.loads(row["validation_issues_json"]),
            raw_payload=json.loads(row["raw_payload_json"]),
            duplicate_of_id=row["duplicate_of_id"],
            items=items,
            tax_breakdowns=tax_breakdowns,
        )
