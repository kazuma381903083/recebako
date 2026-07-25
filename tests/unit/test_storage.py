from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ReceiptStatus,
    ValidationIssue,
    ValidationResult,
)
from recebako.storage import (
    ReceiptRepository,
    ReceiptWrite,
    apply_migrations,
    connect_database,
    database_path,
)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    database = connect_database(tmp_path)
    apply_migrations(database)
    try:
        yield database
    finally:
        database.close()


def _extraction() -> NormalizedReceiptExtraction:
    return NormalizedReceiptExtraction(
        store="テスト商店",
        date_raw="2026/7/25",
        date="2026-07-25",
        time="12:34",
        items=[
            {
                "name": "外税商品",
                "qty": 1,
                "price": 151,
                "price_raw": 140,
                "tax_rate": 8,
                "tax_treatment": "excluded",
                "tax_adjustment": 11,
            },
            {
                "name": "内税商品",
                "qty": 1,
                "price": 570,
                "price_raw": 570,
                "tax_rate": 10,
                "tax_treatment": "included",
                "tax_adjustment": 0,
            },
        ],
        subtotal=710,
        tax=62,
        tax_breakdowns=[
            {
                "tax_rate": 8,
                "taxable_amount": 140,
                "tax_amount": 11,
                "tax_treatment": "excluded",
            },
            {
                "tax_rate": 10,
                "taxable_amount": 570,
                "tax_amount": 51,
                "tax_treatment": "included",
            },
        ],
        total=721,
        payment="cash",
        confidence=0.95,
    )


def _write(tmp_path: Path) -> ReceiptWrite:
    return ReceiptWrite(
        extraction=_extraction(),
        validation=ValidationResult(
            status=ReceiptStatus.REVIEW,
            issues=[
                ValidationIssue(
                    code="sample.issue",
                    message="テスト用の検証結果です",
                    field="total",
                )
            ],
        ),
        phash="0000000000000000",
        image_path=tmp_path / "receipt.jpg",
        ingest_mode=IngestMode.REGULAR,
        raw_payload='{"source":"ollama"}',
    )


def test_initial_migration_creates_required_tables(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        apply_migrations(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert database_path(tmp_path).is_file()
    assert {
        "schema_migrations",
        "receipts",
        "items",
        "store_master",
        "item_tax_details",
        "receipt_tax_breakdowns",
    } <= tables


def test_migration_can_be_applied_repeatedly(
    connection: sqlite3.Connection,
) -> None:
    apply_migrations(connection)
    apply_migrations(connection)

    versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert [row[0] for row in versions] == [
        "001_initial",
        "002_tax_normalization",
    ]


def test_repository_saves_and_reads_receipt_and_items(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    repository = ReceiptRepository(connection)

    receipt_id = repository.save(_write(tmp_path))
    stored = repository.get(receipt_id)

    assert stored is not None
    assert stored.store == "テスト商店"
    assert stored.date_raw == "2026/7/25"
    assert stored.date == "2026-07-25"
    assert stored.status is ReceiptStatus.REVIEW
    assert [item.name for item in stored.items] == ["外税商品", "内税商品"]
    assert [item.name_norm for item in stored.items] == [None, None]
    assert [item.price for item in stored.items] == [151, 570]
    assert [item.price_raw for item in stored.items] == [140, 570]
    assert [item.tax_rate for item in stored.items] == [8, 10]
    assert [item.tax_treatment.value for item in stored.items] == [
        "excluded",
        "included",
    ]
    assert [item.tax_adjustment for item in stored.items] == [11, 0]
    assert [breakdown.tax_amount for breakdown in stored.tax_breakdowns] == [11, 51]


def test_json_fields_are_saved_and_restored(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    repository = ReceiptRepository(connection)

    stored = repository.get(repository.save(_write(tmp_path)))

    assert stored is not None
    assert stored.validation_issues == [
        {
            "code": "sample.issue",
            "message": "テスト用の検証結果です",
            "field": "total",
        }
    ]
    assert stored.raw_payload == {"source": "ollama"}


def test_item_failure_rolls_back_receipt(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    connection.executescript(
        """
        CREATE TRIGGER fail_item_insert
        BEFORE INSERT ON items
        BEGIN
            SELECT RAISE(ABORT, 'forced item failure');
        END;
        """
    )
    repository = ReceiptRepository(connection)

    with pytest.raises(sqlite3.IntegrityError):
        repository.save(_write(tmp_path))

    receipt_count = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()
    assert receipt_count is not None
    assert receipt_count[0] == 0


def test_item_tax_detail_failure_rolls_back_receipt(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    connection.executescript(
        """
        CREATE TRIGGER fail_item_tax_detail_insert
        BEFORE INSERT ON item_tax_details
        BEGIN
            SELECT RAISE(ABORT, 'forced item tax detail failure');
        END;
        """
    )
    repository = ReceiptRepository(connection)

    with pytest.raises(sqlite3.IntegrityError):
        repository.save(_write(tmp_path))

    receipt_count = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()
    item_count = connection.execute("SELECT COUNT(*) FROM items").fetchone()
    assert receipt_count is not None and receipt_count[0] == 0
    assert item_count is not None and item_count[0] == 0


def test_foreign_key_violations_are_enforced(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO items (
                receipt_id, name, name_norm, qty, price, category
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (999, "存在しない親", None, 1, 100, None),
        )
