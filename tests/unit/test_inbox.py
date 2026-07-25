from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from recebako.ai import OllamaTimeoutError
from recebako.config import AppConfig
from recebako.domain import IngestMode, ReceiptStatus
from recebako.runtime import run_inbox

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
) -> str:
    return json.dumps(
        {
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


def test_run_inbox_confirms_archives_and_saves_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    source = data_root / "inbox" / "receipt.jpg"
    original_bytes = _write_image(source)
    temporary_paths: list[Path] = []

    def fake_request(path: Path, **kwargs: Any) -> str:
        temporary_paths.append(path)
        assert path.is_relative_to(data_root / "tmp")
        assert kwargs["model"] == "qwen3-vl:8b"
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
            "SELECT image_path FROM receipts WHERE id = 1"
        ).fetchone()
    assert image_path == ("archive/2026/07/1_receipt.jpg",)


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
        if call_count == 1:
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
