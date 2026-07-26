from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import recebako.runtime.inbox as inbox_module
from recebako.ai import OllamaTimeoutError
from recebako.config import AppConfig
from recebako.domain import IngestMode, ReceiptFileState, ReceiptStatus
from recebako.runtime import (
    RuntimeFileError,
    claim_inbox_file,
    initialize_runtime,
    run_inbox,
    scan_inbox,
)
from recebako.storage import (
    ReceiptRepository,
    ReceiptWrite,
    StorageError,
    connect_database,
)
from recebako.validation import validate_receipt_payload

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


def _write_image(path: Path, *, color: str = "white") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (120, 80), color) as image:
        image.save(path)
    return path.read_bytes()


def _payload(
    *,
    receipt_date: str = "2026-07-25",
    confidence: float = 0.99,
    is_receipt: bool = True,
) -> str:
    return json.dumps(
        {
            "is_receipt": is_receipt,
            "store": "テスト商店",
            "date": receipt_date,
            "time": "12:34",
            "items": [{"name": "テスト品", "qty": 1, "price": 100}],
            "subtotal": 100,
            "tax": 0,
            "total": 100,
            "payment": "cash",
            "confidence": confidence,
        },
        ensure_ascii=False,
    )


def test_run_inbox_with_no_files_is_successful(tmp_path: Path) -> None:
    result = run_inbox(
        config=_config(tmp_path / "data"),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.scanned == 0
    assert result.processed == 0
    assert result.results == []


def test_run_inbox_automatically_retries_unregistered_processing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    paths, _ = initialize_runtime(data_root)
    _write_image(paths.inbox / "receipt.jpg")
    claim_inbox_file(
        scan_inbox(paths).selected[0],
        paths,
        token="a" * 32,
    )
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(),
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.confirmed == 1
    assert result.failed == 0
    assert list(paths.processing.iterdir()) == []
    assert (paths.archive / "2026" / "07" / "1_receipt.jpg").is_file()


def test_run_inbox_automatically_finalizes_pending_database_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    paths, _ = initialize_runtime(data_root)
    _write_image(paths.inbox / "receipt.jpg")
    work_path = claim_inbox_file(
        scan_inbox(paths).selected[0],
        paths,
        token="a" * 32,
    )
    extraction, validation = validate_receipt_payload(
        _payload(),
        reference_date=REFERENCE_DATE,
        mode=IngestMode.REGULAR,
    )
    assert extraction is not None
    with closing(connect_database(data_root)) as connection:
        receipt_id = ReceiptRepository(connection).save(
            ReceiptWrite(
                extraction=extraction,
                validation=validation,
                phash="0000000000000000",
                image_path=Path("processing") / work_path.name,
                ingest_mode=IngestMode.REGULAR,
                raw_payload=_payload(),
                file_state=ReceiptFileState.PENDING,
            )
        )
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: pytest.fail("pending record must not be re-extracted"),
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.processed == 0
    with closing(connect_database(data_root)) as connection:
        stored = ReceiptRepository(connection).get(receipt_id)
    assert stored is not None
    assert stored.file_state is ReceiptFileState.FINALIZED
    assert stored.image_path == "archive/2026/07/1_receipt.jpg"
    assert (data_root / stored.image_path).is_file()
    assert list(paths.processing.iterdir()) == []


def test_run_inbox_transition_failure_is_pending_until_next_run_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    paths, _ = initialize_runtime(data_root)
    _write_image(paths.inbox / "receipt.jpg")
    call_count = 0

    def invalid_then_valid(path: Path, **kwargs: Any) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "{not-json"
        return _payload()

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        invalid_then_valid,
    )
    original_move_to_final = inbox_module.move_to_final

    def fail_final_move(*args: Any, **kwargs: Any) -> Path:
        raise RuntimeFileError("forced final move failure")

    monkeypatch.setattr(inbox_module, "move_to_final", fail_final_move)

    failed_run = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert failed_run.failed == 1
    with closing(connect_database(data_root)) as connection:
        pending = ReceiptRepository(connection).get(1)
    assert pending is not None
    assert pending.status is ReceiptStatus.CONFIRMED
    assert pending.file_state is ReceiptFileState.PENDING
    assert pending.image_path.startswith("processing/")
    assert (data_root / pending.image_path).is_file()
    assert call_count == 2

    monkeypatch.setattr(inbox_module, "move_to_final", original_move_to_final)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: pytest.fail("pending record must not be re-extracted"),
    )
    recovered_run = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert recovered_run.processed == 0
    with closing(connect_database(data_root)) as connection:
        finalized = ReceiptRepository(connection).get(1)
    assert finalized is not None
    assert finalized.file_state is ReceiptFileState.FINALIZED
    assert finalized.image_path == "archive/2026/07/1_receipt.jpg"
    assert (data_root / finalized.image_path).is_file()
    assert list(paths.processing.iterdir()) == []
    assert call_count == 2


def test_run_inbox_non_receipt_transition_recovers_without_second_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    paths, _ = initialize_runtime(data_root)
    _write_image(paths.inbox / "image.jpg")
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(
            receipt_date="not-a-date",
            is_receipt=False,
        ),
    )
    original_move_to_final = inbox_module.move_to_final

    def fail_final_move(*args: Any, **kwargs: Any) -> Path:
        raise RuntimeFileError("forced final move failure")

    monkeypatch.setattr(inbox_module, "move_to_final", fail_final_move)

    interrupted = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert interrupted.failed == 1
    assert interrupted.results[0].receipt_id == 1
    assert interrupted.results[0].error_code == "filesystem.transition"
    with closing(connect_database(data_root)) as connection:
        pending = ReceiptRepository(connection).get(1)
    assert pending is not None
    assert pending.status is ReceiptStatus.FAILED
    assert pending.file_state is ReceiptFileState.PENDING
    assert pending.image_path.startswith("processing/")
    assert pending.store == ""
    assert pending.items == []

    monkeypatch.setattr(inbox_module, "move_to_final", original_move_to_final)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: pytest.fail(
            "pending non-receipt must not be re-extracted"
        ),
    )

    recovered = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert recovered.processed == 0
    with closing(connect_database(data_root)) as connection:
        finalized = ReceiptRepository(connection).get(1)
        count = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()
    assert finalized is not None
    assert finalized.status is ReceiptStatus.FAILED
    assert finalized.file_state is ReceiptFileState.FINALIZED
    assert finalized.image_path == "failed/1_image.jpg"
    assert (data_root / finalized.image_path).is_file()
    assert count is not None
    assert count[0] == 1
    assert list(paths.processing.iterdir()) == []


def test_run_inbox_repairs_non_receipt_after_database_finalize_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    paths, _ = initialize_runtime(data_root)
    _write_image(paths.inbox / "image.jpg")
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(is_receipt=False),
    )
    original_finalize = ReceiptRepository.finalize_image_path

    def fail_finalize(
        self: ReceiptRepository,
        receipt_id: int,
        image_path: Path,
    ) -> None:
        raise StorageError("forced database finalize failure")

    monkeypatch.setattr(ReceiptRepository, "finalize_image_path", fail_finalize)

    interrupted = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert interrupted.failed == 1
    assert interrupted.results[0].receipt_id == 1
    assert interrupted.results[0].destination == "failed/1_image.jpg"
    assert interrupted.results[0].error_code == "storage.unavailable"
    with closing(connect_database(data_root)) as connection:
        pending = ReceiptRepository(connection).get(1)
    assert pending is not None
    assert pending.status is ReceiptStatus.FAILED
    assert pending.file_state is ReceiptFileState.PENDING
    assert pending.image_path.startswith("processing/")
    assert not (data_root / pending.image_path).exists()
    assert (paths.failed / "1_image.jpg").is_file()

    monkeypatch.setattr(ReceiptRepository, "finalize_image_path", original_finalize)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: pytest.fail(
            "moved non-receipt must not be re-extracted"
        ),
    )

    recovered = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert recovered.processed == 0
    with closing(connect_database(data_root)) as connection:
        finalized = ReceiptRepository(connection).get(1)
        count = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()
    assert finalized is not None
    assert finalized.status is ReceiptStatus.FAILED
    assert finalized.file_state is ReceiptFileState.FINALIZED
    assert finalized.image_path == "failed/1_image.jpg"
    assert count is not None
    assert count[0] == 1


def test_run_inbox_routes_non_receipt_to_failed_with_safe_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    source = data_root / "inbox" / "image.jpg"
    original_bytes = _write_image(source)
    private_sentinel = "PRIVATE-NON-RECEIPT-CONTENT"
    payload = json.loads(_payload(is_receipt=False))
    payload["store"] = private_sentinel
    payload["items"] = [{"name": private_sentinel, "qty": 1, "price": 100}]
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: json.dumps(payload),
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.failed == 1
    assert result.confirmed == 0
    assert result.review == 0
    item = result.results[0]
    assert item.status is ReceiptStatus.FAILED
    assert item.receipt_id == 1
    assert item.destination == "failed/1_image.jpg"
    assert item.error_code is None
    assert (data_root / item.destination).read_bytes() == original_bytes
    assert private_sentinel not in result.model_dump_json()
    with closing(connect_database(data_root)) as connection:
        stored = ReceiptRepository(connection).get(1)
    assert stored is not None
    assert stored.status is ReceiptStatus.FAILED
    assert stored.file_state is ReceiptFileState.FINALIZED
    assert stored.store == ""
    assert stored.items == []
    assert {issue["code"] for issue in stored.validation_issues} == {
        "receipt.not_receipt"
    }


def test_run_inbox_confirms_archives_and_saves_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    source = data_root / "inbox" / "receipt.jpg"
    original_bytes = _write_image(source)
    temporary_paths: list[Path] = []
    app_config = _config(data_root)

    def fake_request(path: Path, **kwargs: Any) -> str:
        temporary_paths.append(path)
        assert path.is_relative_to(data_root / "tmp")
        assert kwargs == {"config": app_config.ollama}
        assert kwargs["config"] is app_config.ollama
        return _payload()

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        fake_request,
    )

    result = run_inbox(
        config=app_config,
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.model_dump(mode="json") == {
        "scanned": 1,
        "processed": 1,
        "confirmed": 1,
        "review": 0,
        "failed": 0,
        "skipped": 0,
        "results": [
            {
                "source_name": "receipt.jpg",
                "receipt_id": 1,
                "status": "confirmed",
                "destination": "archive/2026/07/1_receipt.jpg",
                "error_code": None,
            }
        ],
    }
    archived = data_root / result.results[0].destination
    assert archived.read_bytes() == original_bytes
    assert not source.exists()
    assert list((data_root / "processing").iterdir()) == []
    assert list((data_root / "tmp").iterdir()) == []
    assert temporary_paths and all(not path.exists() for path in temporary_paths)
    with sqlite3.connect(data_root / "ledger.db") as connection:
        image_path = connection.execute(
            "SELECT image_path, file_state FROM receipts WHERE id = 1"
        ).fetchone()
    assert image_path == ("archive/2026/07/1_receipt.jpg", "finalized")


def test_run_inbox_routes_duplicate_to_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    _write_image(data_root / "inbox" / "a.jpg")
    _write_image(data_root / "inbox" / "b.jpg")
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(),
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.confirmed == 1
    assert result.review == 1
    assert result.results[1].status is ReceiptStatus.REVIEW
    assert result.results[1].destination == "review/2_b.jpg"
    assert (data_root / "review" / "2_b.jpg").is_file()
    with sqlite3.connect(data_root / "ledger.db") as connection:
        duplicate = connection.execute(
            "SELECT duplicate_of_id, image_path FROM receipts WHERE id = 2"
        ).fetchone()
    assert duplicate == (1, "review/2_b.jpg")


def test_run_inbox_failure_writes_safe_metadata_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    _write_image(data_root / "inbox" / "a.jpg")
    _write_image(data_root / "inbox" / "b.jpg", color="black")
    call_count = 0

    def fake_request(path: Path, **kwargs: Any) -> str:
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise OllamaTimeoutError("private raw response 9999円")
        return _payload()

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        fake_request,
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
    )

    assert result.processed == 2
    assert result.failed == 1
    assert result.confirmed == 1
    failed_result = result.results[0]
    assert failed_result.receipt_id is None
    assert failed_result.error_code == "ollama.timeout"
    failed_image = data_root / failed_result.destination
    metadata_path = failed_image.with_name(f"{failed_image.name}.error.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["error_code"] == "ollama.timeout"
    assert metadata["retryable"] is True
    assert "private raw response" not in metadata_path.read_text(encoding="utf-8")
    with sqlite3.connect(data_root / "ledger.db") as connection:
        count = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()
    assert count == (1,)
    assert call_count == 4


def test_run_inbox_historical_mode_uses_old_receipt_archive_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    _write_image(data_root / "inbox" / "historical.png")
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(receipt_date="2020/1/1"),
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.HISTORICAL,
        reference_date=REFERENCE_DATE,
    )

    assert result.confirmed == 1
    assert result.results[0].destination == ("archive/2020/01/1_historical.png")


def test_run_inbox_limit_leaves_unselected_file_in_inbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    _write_image(data_root / "inbox" / "a.jpg")
    _write_image(data_root / "inbox" / "b.jpg")
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(),
    )

    result = run_inbox(
        config=_config(data_root),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        limit=1,
    )

    assert result.scanned == 2
    assert result.processed == 1
    assert result.skipped == 1
    assert (data_root / "inbox" / "b.jpg").is_file()
