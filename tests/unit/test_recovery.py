from __future__ import annotations

import shutil
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from recebako.config import AppConfig
from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ReceiptStatus,
    ValidationResult,
)
from recebako.runtime import (
    claim_inbox_file,
    initialize_runtime,
    move_to_final,
    recover_runtime,
    scan_inbox,
)
from recebako.storage import (
    ReceiptRepository,
    ReceiptWrite,
    connect_database,
)

REFERENCE_DATE = date(2026, 7, 26)


def _config(data_root: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "data": {"root": data_root},
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "qwen3-vl:8b",
                "temperature": 0,
            },
            "review_ui": {"host": "127.0.0.1", "port": 8765},
        }
    )


def _extraction(receipt_date: str = "2026-07-25") -> NormalizedReceiptExtraction:
    return NormalizedReceiptExtraction.model_validate(
        {
            "store": "テスト商店",
            "date_raw": receipt_date,
            "date": receipt_date,
            "time": "",
            "items": [
                {
                    "name": "テスト品",
                    "price": 100,
                    "price_raw": 100,
                    "tax_rate": None,
                    "tax_treatment": "unknown",
                    "tax_adjustment": 0,
                }
            ],
            "subtotal": 100,
            "tax": 0,
            "tax_breakdowns": [],
            "total": 100,
            "payment": "cash",
            "confidence": 0.99,
        }
    )


def _claim(
    data_root: Path,
    *,
    source_name: str = "receipt.jpg",
    token: str = "a" * 32,
) -> Path:
    paths, _ = initialize_runtime(data_root)
    source = paths.inbox / source_name
    source.write_bytes(b"synthetic-image")
    return claim_inbox_file(
        scan_inbox(paths).selected[0],
        paths,
        token=token,
    )


def _save_processing_record(
    data_root: Path,
    work_path: Path,
    *,
    status: ReceiptStatus,
    receipt_date: str = "2026-07-25",
) -> int:
    with closing(connect_database(data_root)) as connection:
        return ReceiptRepository(connection).save(
            ReceiptWrite(
                extraction=_extraction(receipt_date),
                validation=ValidationResult(status=status, issues=[]),
                phash="0000000000000000",
                image_path=Path("processing") / work_path.name,
                ingest_mode=IngestMode.REGULAR,
                raw_payload="{}",
            )
        )


def _stored_path(data_root: Path, receipt_id: int) -> str:
    with closing(connect_database(data_root)) as connection:
        stored = ReceiptRepository(connection).get(receipt_id)
    assert stored is not None
    return stored.image_path


def test_recover_returns_unregistered_processing_file_to_inbox(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
    )

    assert result.recovered == 1
    assert result.errors == 0
    assert result.results[0].action == "return_to_inbox"
    assert result.results[0].destination == "inbox/receipt.jpg"
    assert (data_root / "inbox" / "receipt.jpg").is_file()
    assert not work_path.exists()


def test_recover_return_to_inbox_collision_does_not_overwrite(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)
    existing = data_root / "inbox" / "receipt.jpg"
    existing.write_bytes(b"existing")

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
    )

    assert result.results[0].destination == "inbox/receipt.1.jpg"
    assert existing.read_bytes() == b"existing"
    assert (data_root / "inbox" / "receipt.1.jpg").read_bytes() == (b"synthetic-image")
    assert not work_path.exists()


@pytest.mark.parametrize(
    ("status", "expected_destination"),
    [
        (ReceiptStatus.CONFIRMED, "archive/2026/07/1_receipt.jpg"),
        (ReceiptStatus.REVIEW, "review/1_receipt.jpg"),
    ],
)
def test_recover_completes_final_move_for_registered_receipt(
    tmp_path: Path,
    status: ReceiptStatus,
    expected_destination: str,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)
    receipt_id = _save_processing_record(
        data_root,
        work_path,
        status=status,
    )

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
    )

    assert result.recovered == 1
    assert result.results[0].receipt_id == receipt_id
    assert result.results[0].destination == expected_destination
    assert (data_root / expected_destination).is_file()
    assert _stored_path(data_root, receipt_id) == expected_destination


def test_recover_repairs_database_after_final_move(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)
    receipt_id = _save_processing_record(
        data_root,
        work_path,
        status=ReceiptStatus.CONFIRMED,
    )
    paths, _ = initialize_runtime(data_root)
    final_path = move_to_final(
        work_path,
        paths,
        receipt_id=receipt_id,
        status=ReceiptStatus.CONFIRMED,
        date_value="2026-07-25",
        fallback_date=REFERENCE_DATE,
        original_name="receipt.jpg",
    )
    assert _stored_path(data_root, receipt_id).startswith("processing/")

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
    )

    assert result.recovered == 1
    assert result.results[0].action == "repair_database_path"
    assert _stored_path(data_root, receipt_id) == (
        final_path.relative_to(data_root).as_posix()
    )


def test_recover_dry_run_changes_neither_file_nor_database(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)
    receipt_id = _save_processing_record(
        data_root,
        work_path,
        status=ReceiptStatus.REVIEW,
    )
    before_lock_bytes = (data_root / ".recebako-inbox.lock").read_bytes()

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
        dry_run=True,
    )

    assert result.dry_run
    assert result.results[0].outcome == "planned"
    assert work_path.is_file()
    assert not (data_root / "review" / "1_receipt.jpg").exists()
    assert _stored_path(data_root, receipt_id).startswith("processing/")
    assert (data_root / ".recebako-inbox.lock").read_bytes() == before_lock_bytes


def test_recover_does_not_change_ambiguous_final_files(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)
    receipt_id = _save_processing_record(
        data_root,
        work_path,
        status=ReceiptStatus.CONFIRMED,
    )
    paths, _ = initialize_runtime(data_root)
    final_path = move_to_final(
        work_path,
        paths,
        receipt_id=receipt_id,
        status=ReceiptStatus.CONFIRMED,
        date_value="2026-07-25",
        fallback_date=REFERENCE_DATE,
        original_name="receipt.jpg",
    )
    second_candidate = final_path.with_name("1_receipt.1.jpg")
    shutil.copyfile(final_path, second_candidate)
    original_db_path = _stored_path(data_root, receipt_id)

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
    )

    assert result.recovered == 0
    assert result.errors == 1
    assert result.results[0].error_code == "recovery.final_ambiguous"
    assert _stored_path(data_root, receipt_id) == original_db_path
    assert final_path.is_file()
    assert second_candidate.is_file()


def test_recover_leaves_missing_final_path_unchanged(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    work_path = _claim(data_root)
    receipt_id = _save_processing_record(
        data_root,
        work_path,
        status=ReceiptStatus.REVIEW,
    )
    work_path.unlink()
    original_db_path = _stored_path(data_root, receipt_id)

    result = recover_runtime(
        config=_config(data_root),
        fallback_date=REFERENCE_DATE,
    )

    assert result.errors == 1
    assert result.results[0].error_code == "recovery.final_not_found"
    assert _stored_path(data_root, receipt_id) == original_db_path
