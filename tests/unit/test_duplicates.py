from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ReceiptStatus,
    ValidationResult,
)
from recebako.storage import (
    ReceiptRepository,
    ReceiptWrite,
    apply_migrations,
    connect_database,
    find_duplicate_candidate,
    phash_hamming_distance,
)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    database = connect_database(tmp_path)
    apply_migrations(database)
    try:
        yield database
    finally:
        database.close()


def _extraction(
    *,
    store: str = "テスト商店",
    date: str = "2026-07-25",
    total: int = 100,
) -> NormalizedReceiptExtraction:
    return NormalizedReceiptExtraction(
        store=store,
        date_raw=date,
        date=date,
        time="12:34",
        items=[{"name": "商品", "qty": 1, "price": total}],
        subtotal=total,
        tax=0,
        total=total,
        payment="cash",
        confidence=0.95,
    )


def _save(
    connection: sqlite3.Connection,
    tmp_path: Path,
    extraction: NormalizedReceiptExtraction,
    *,
    phash: str,
    status: ReceiptStatus = ReceiptStatus.CONFIRMED,
    duplicate_of_id: int | None = None,
) -> int:
    return ReceiptRepository(connection).save(
        ReceiptWrite(
            extraction=extraction,
            validation=ValidationResult(status=status, issues=[]),
            phash=phash,
            image_path=tmp_path / "receipt.jpg",
            ingest_mode=IngestMode.REGULAR,
            raw_payload=extraction.model_dump_json(),
            duplicate_of_id=duplicate_of_id,
        )
    )


def test_exact_identity_match_is_detected(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    receipt = _extraction()
    existing_id = _save(
        connection,
        tmp_path,
        receipt,
        phash="ffffffffffffffff",
    )

    candidate = find_duplicate_candidate(
        connection,
        receipt,
        phash="0000000000000000",
    )

    assert candidate is not None
    assert candidate.receipt_id == existing_id
    assert candidate.match_type == "identity"


def test_identical_phash_is_detected(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    existing_id = _save(
        connection,
        tmp_path,
        _extraction(store="別店舗"),
        phash="0123456789abcdef",
    )

    candidate = find_duplicate_candidate(
        connection,
        _extraction(),
        phash="0123456789abcdef",
    )

    assert candidate is not None
    assert candidate.receipt_id == existing_id
    assert candidate.match_type == "phash"
    assert candidate.phash_distance == 0


def test_phash_within_threshold_is_detected(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    existing_id = _save(
        connection,
        tmp_path,
        _extraction(store="別店舗"),
        phash="0000000000000003",
    )

    candidate = find_duplicate_candidate(
        connection,
        _extraction(),
        phash="0000000000000000",
        phash_distance_threshold=2,
    )

    assert (
        phash_hamming_distance(
            "0000000000000000",
            "0000000000000003",
        )
        == 2
    )
    assert candidate is not None
    assert candidate.receipt_id == existing_id


def test_phash_beyond_threshold_is_not_detected(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _save(
        connection,
        tmp_path,
        _extraction(store="別店舗"),
        phash="000000000000003f",
    )

    candidate = find_duplicate_candidate(
        connection,
        _extraction(),
        phash="0000000000000000",
        phash_distance_threshold=5,
    )

    assert candidate is None


def test_candidate_priority_is_identity_then_distance_then_id(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    current = _extraction()
    phash_only_id = _save(
        connection,
        tmp_path,
        _extraction(store="別店舗"),
        phash="0000000000000000",
    )
    far_identity_id = _save(
        connection,
        tmp_path,
        current,
        phash="ffffffffffffffff",
    )
    closest_identity_id = _save(
        connection,
        tmp_path,
        current,
        phash="0000000000000001",
    )
    same_distance_later_id = _save(
        connection,
        tmp_path,
        current,
        phash="0000000000000002",
    )

    candidate = find_duplicate_candidate(
        connection,
        current,
        phash="0000000000000000",
    )

    assert candidate is not None
    assert candidate.receipt_id == closest_identity_id
    assert candidate.receipt_id not in {
        phash_only_id,
        far_identity_id,
        same_distance_later_id,
    }
