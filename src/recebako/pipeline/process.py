from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recebako.ai import request_receipt_extraction
from recebako.config import AppConfig
from recebako.domain import (
    IngestMode,
    ReceiptStatus,
    ValidationIssue,
    ValidationResult,
)
from recebako.imaging import preprocess_image
from recebako.storage import (
    MigrationError,
    ReceiptRepository,
    ReceiptWrite,
    StorageError,
    connect_database,
    find_duplicate_candidate,
    initialize_database,
)
from recebako.validation import validate_receipt_payload


class ProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: int
    status: ReceiptStatus
    duplicate_of_id: int | None
    validation_issues: list[ValidationIssue]
    store: str
    date_raw: str
    date: str
    total: int
    phash: str


def _with_duplicate_issue(validation: ValidationResult) -> ValidationResult:
    issues = list(validation.issues)
    issues.append(
        ValidationIssue(
            code="duplicate.suspected",
            message="既存レシートとの重複候補です",
            field="duplicate_of_id",
        )
    )
    return ValidationResult(status=ReceiptStatus.REVIEW, issues=issues)


def process_receipt(
    image_path: Path,
    *,
    config: AppConfig,
    mode: IngestMode,
    reference_date: date,
) -> ProcessResult:
    with preprocess_image(image_path) as preprocessed:
        raw_payload = request_receipt_extraction(
            preprocessed.path,
            base_url=config.ollama.base_url,
            model=config.ollama.model,
            temperature=config.ollama.temperature,
        )
        extraction, validation = validate_receipt_payload(
            raw_payload,
            reference_date=reference_date,
            mode=mode,
        )
        phash = preprocessed.phash

    try:
        initialize_database(config.data.root)
        with closing(connect_database(config.data.root)) as connection:
            duplicate = (
                find_duplicate_candidate(
                    connection,
                    extraction,
                    phash=phash,
                    phash_distance_threshold=(
                        config.deduplication.phash_distance_threshold
                    ),
                )
                if extraction is not None
                else None
            )
            if duplicate is not None:
                validation = _with_duplicate_issue(validation)

            repository = ReceiptRepository(connection)
            receipt_id = repository.save(
                ReceiptWrite(
                    extraction=extraction,
                    validation=validation,
                    phash=phash,
                    image_path=image_path,
                    ingest_mode=mode,
                    raw_payload=raw_payload,
                    duplicate_of_id=(
                        duplicate.receipt_id if duplicate is not None else None
                    ),
                )
            )
    except (MigrationError, StorageError):
        raise
    except sqlite3.Error as exc:
        raise StorageError("SQLiteへの保存に失敗しました") from exc

    return ProcessResult(
        receipt_id=receipt_id,
        status=validation.status,
        duplicate_of_id=duplicate.receipt_id if duplicate is not None else None,
        validation_issues=validation.issues,
        store=extraction.store if extraction is not None else "",
        date_raw=extraction.date_raw if extraction is not None else "",
        date=extraction.date if extraction is not None else "",
        total=extraction.total if extraction is not None else 0,
        phash=phash,
    )
