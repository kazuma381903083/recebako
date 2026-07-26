from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from recebako.config import AppConfig
from recebako.domain import IngestMode, ReceiptStatus
from recebako.normalization import TaxNormalizationReason
from recebako.pipeline import (
    DuplicateOutcome,
    ProcessAudit,
    process_receipt,
    process_receipt_with_audit,
)
from recebako.validation import DateNormalizationOutcome, SchemaOutcome

REFERENCE_DATE = date(2026, 7, 25)


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


def _image(tmp_path: Path) -> Path:
    image_path = tmp_path / "case.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    return image_path


def _payload(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "is_receipt": True,
        "store": "監査テスト店",
        "date": "2026-07-25",
        "time": "12:34",
        "items": [{"name": "監査テスト品", "qty": 1, "price": 100}],
        "subtotal": 100,
        "tax": 0,
        "total": 100,
        "payment": "cash",
        "confidence": 0.95,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _run_with_audit(
    image_path: Path,
    *,
    config: AppConfig,
) -> tuple[Any, Any]:
    return process_receipt_with_audit(
        image_path,
        config=config,
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        storage_image_path=Path("archive/case.png"),
    )


def test_process_audit_contains_only_safe_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(),
    )

    result, audit = _run_with_audit(
        image_path,
        config=_config(tmp_path / "data"),
    )

    assert result.status is ReceiptStatus.CONFIRMED
    assert audit.schema_outcome is SchemaOutcome.VALID
    assert audit.date_normalization_outcome is DateNormalizationOutcome.UNCHANGED
    assert audit.tax_normalization_reason is TaxNormalizationReason.NOT_NEEDED
    assert audit.duplicate_outcome is DuplicateOutcome.NONE
    assert set(audit.model_dump()) == {
        "schema_outcome",
        "date_normalization_outcome",
        "tax_normalization_reason",
        "duplicate_outcome",
    }
    assert "監査テスト店" not in audit.model_dump_json()
    assert "監査テスト品" not in audit.model_dump_json()
    assert result.phash not in audit.model_dump_json()


def test_process_audit_validation_error_hides_private_extra_values() -> None:
    private_sentinel = "PRIVATE-RECEIPT-CONTENT"
    payload = {
        "schema_outcome": SchemaOutcome.VALID,
        "date_normalization_outcome": DateNormalizationOutcome.UNCHANGED,
        "tax_normalization_reason": TaxNormalizationReason.NOT_NEEDED,
        "duplicate_outcome": DuplicateOutcome.NONE,
        "store": private_sentinel,
    }

    with pytest.raises(ValidationError) as captured:
        ProcessAudit.model_validate(payload)

    assert private_sentinel not in str(captured.value)


def test_process_audit_marks_invalid_structure_and_skipped_duplicate_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: "{not-json",
    )

    result, audit = _run_with_audit(
        image_path,
        config=_config(tmp_path / "data"),
    )

    assert result.status is ReceiptStatus.FAILED
    assert audit.schema_outcome is SchemaOutcome.INVALID
    assert audit.date_normalization_outcome is DateNormalizationOutcome.NOT_EVALUATED
    assert audit.tax_normalization_reason is None
    assert audit.duplicate_outcome is DuplicateOutcome.NOT_EVALUATED


def test_process_non_receipt_is_failed_without_private_result_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    data_root = tmp_path / "data"
    private_sentinel = "PRIVATE-NON-RECEIPT-CONTENT"
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(
            is_receipt=False,
            store=private_sentinel,
            items=[{"name": private_sentinel, "qty": 1, "price": 100}],
        ),
    )

    result, audit = _run_with_audit(
        image_path,
        config=_config(data_root),
    )

    assert result.status is ReceiptStatus.FAILED
    assert result.store == ""
    assert result.date_raw == ""
    assert result.date == ""
    assert result.total == 0
    assert result.duplicate_of_id is None
    assert {issue.code for issue in result.validation_issues} == {"receipt.not_receipt"}
    assert audit.schema_outcome is SchemaOutcome.VALID
    assert audit.date_normalization_outcome is DateNormalizationOutcome.NOT_EVALUATED
    assert audit.tax_normalization_reason is None
    assert audit.duplicate_outcome is DuplicateOutcome.NOT_EVALUATED
    assert private_sentinel not in result.model_dump_json()
    assert private_sentinel not in audit.model_dump_json()

    with sqlite3.connect(data_root / "ledger.db") as connection:
        stored = connection.execute(
            """
            SELECT status, store, date_raw, date, total, duplicate_of_id
            FROM receipts
            WHERE id = ?
            """,
            (result.receipt_id,),
        ).fetchone()
        item_count = connection.execute(
            "SELECT COUNT(*) FROM items WHERE receipt_id = ?",
            (result.receipt_id,),
        ).fetchone()
    assert stored == ("failed", "", "", "", 0, None)
    assert item_count == (0,)


def test_non_receipt_is_not_downgraded_to_review_by_duplicate_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    payloads = iter([_payload(), _payload(is_receipt=False)])
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: next(payloads),
    )
    config = _config(tmp_path / "data")

    first, _ = _run_with_audit(image_path, config=config)
    second, audit = _run_with_audit(image_path, config=config)

    assert first.status is ReceiptStatus.CONFIRMED
    assert second.status is ReceiptStatus.FAILED
    assert second.duplicate_of_id is None
    assert {issue.code for issue in second.validation_issues} == {"receipt.not_receipt"}
    assert audit.duplicate_outcome is DuplicateOutcome.NOT_EVALUATED


@pytest.mark.parametrize(
    ("raw_date", "expected"),
    [
        ("2026/7/25", DateNormalizationOutcome.NORMALIZED),
        ("not-a-date", DateNormalizationOutcome.REJECTED),
    ],
)
def test_process_audit_reports_date_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_date: str,
    expected: DateNormalizationOutcome,
) -> None:
    image_path = _image(tmp_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(date=raw_date),
    )

    _, audit = _run_with_audit(
        image_path,
        config=_config(tmp_path / "data"),
    )

    assert audit.date_normalization_outcome is expected


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (108, TaxNormalizationReason.APPLIED),
        (109, TaxNormalizationReason.TOTAL_MISMATCH),
    ],
)
def test_process_audit_reports_tax_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    total: int,
    expected: TaxNormalizationReason,
) -> None:
    image_path = _image(tmp_path)
    payload = _payload(
        items=[
            {
                "name": "監査テスト外税品",
                "price": 100,
                "price_raw": 100,
                "tax_rate": 8,
                "tax_treatment": "excluded",
            }
        ],
        tax_breakdowns=[
            {
                "tax_rate": 8,
                "taxable_amount": 100,
                "tax_amount": 8,
                "tax_treatment": "excluded",
            }
        ],
        total=total,
    )
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: payload,
    )

    _, audit = _run_with_audit(
        image_path,
        config=_config(tmp_path / "data"),
    )

    assert audit.tax_normalization_reason is expected


def test_process_audit_reports_identity_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(),
    )
    config = _config(tmp_path / "data")
    _run_with_audit(image_path, config=config)

    result, audit = _run_with_audit(image_path, config=config)

    assert result.status is ReceiptStatus.REVIEW
    assert result.duplicate_of_id is not None
    assert audit.duplicate_outcome is DuplicateOutcome.IDENTITY
    assert str(result.duplicate_of_id) not in audit.model_dump_json()


def test_process_audit_reports_phash_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    payloads = iter([_payload(store="監査テスト店A"), _payload(store="監査テスト店B")])
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: next(payloads),
    )
    config = _config(tmp_path / "data")
    _run_with_audit(image_path, config=config)

    result, audit = _run_with_audit(image_path, config=config)

    assert result.status is ReceiptStatus.REVIEW
    assert result.duplicate_of_id is not None
    assert audit.duplicate_outcome is DuplicateOutcome.PHASH
    assert result.phash not in audit.model_dump_json()


def test_process_wrapper_preserves_existing_result_serialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _image(tmp_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _payload(date="2026/7/25"),
    )

    wrapped = process_receipt(
        image_path,
        config=_config(tmp_path / "wrapped-data"),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        storage_image_path=Path("archive/case.png"),
    )
    audited, _ = _run_with_audit(
        image_path,
        config=_config(tmp_path / "audited-data"),
    )

    assert wrapped.model_dump(mode="json") == audited.model_dump(mode="json")
