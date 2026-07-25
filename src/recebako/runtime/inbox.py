from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recebako.ai import OllamaError
from recebako.config import AppConfig
from recebako.domain import IngestMode, ReceiptStatus
from recebako.imaging import ImagePreprocessError
from recebako.pipeline import process_receipt
from recebako.runtime.errors import (
    FailedMetadataError,
    describe_error,
    failed_metadata,
    move_to_failed,
    write_failed_metadata,
)
from recebako.runtime.files import (
    RuntimeFileError,
    claim_inbox_file,
    move_to_final,
    scan_inbox,
)
from recebako.runtime.layout import RuntimePaths, initialize_runtime
from recebako.runtime.lock import InboxLock
from recebako.storage import (
    ImagePathError,
    MigrationError,
    ReceiptRepository,
    StorageError,
    connect_database,
    image_path_relative_to_root,
)


class InboxItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    receipt_id: int | None
    status: ReceiptStatus
    destination: str
    error_code: str | None = None


class InboxRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int
    processed: int
    confirmed: int
    review: int
    failed: int
    skipped: int
    results: list[InboxItemResult]


def _relative_destination(data_root: Path, path: Path) -> str:
    return image_path_relative_to_root(data_root, path)


def _record_operational_failure(
    *,
    source_name: str,
    work_path: Path,
    data_root: Path,
    paths: RuntimePaths,
    error: BaseException,
) -> InboxItemResult:
    description = describe_error(error)
    destination = work_path
    try:
        destination = move_to_failed(
            work_path,
            paths,
            source_filename=source_name,
        )
        write_failed_metadata(
            destination,
            failed_metadata(error, source_filename=source_name),
        )
    except (FailedMetadataError, OSError):
        if not destination.exists():
            destination = work_path if work_path.exists() else paths.failed
    return InboxItemResult(
        source_name=source_name,
        receipt_id=None,
        status=ReceiptStatus.FAILED,
        destination=_relative_destination(data_root, destination),
        error_code=description.code,
    )


def run_inbox(
    *,
    config: AppConfig,
    mode: IngestMode,
    reference_date: date,
    limit: int | None = None,
) -> InboxRunResult:
    if limit is not None and limit < 1:
        raise ValueError("limitは1以上である必要があります")

    paths, _ = initialize_runtime(config.data.root)
    results: list[InboxItemResult] = []
    with InboxLock(paths):
        scan = scan_inbox(paths, limit=limit)
        for candidate in scan.selected:
            try:
                work_path = claim_inbox_file(candidate, paths)
            except (OSError, RuntimeFileError) as exc:
                results.append(
                    InboxItemResult(
                        source_name=candidate.source_name,
                        receipt_id=None,
                        status=ReceiptStatus.FAILED,
                        destination=f"inbox/{candidate.source_name}",
                        error_code=describe_error(exc).code,
                    )
                )
                continue

            try:
                process_result = process_receipt(
                    work_path,
                    config=config,
                    mode=mode,
                    reference_date=reference_date,
                    storage_image_path=Path("processing") / work_path.name,
                    temporary_root=paths.tmp,
                )
            except (
                ImagePathError,
                ImagePreprocessError,
                MigrationError,
                OSError,
                OllamaError,
                StorageError,
            ) as exc:
                results.append(
                    _record_operational_failure(
                        source_name=candidate.source_name,
                        work_path=work_path,
                        data_root=config.data.root,
                        paths=paths,
                        error=exc,
                    )
                )
                continue

            receipt_id = process_result.receipt_id
            destination = work_path
            try:
                destination = move_to_final(
                    work_path,
                    paths,
                    receipt_id=receipt_id,
                    status=process_result.status,
                    date_value=process_result.date,
                    fallback_date=reference_date,
                    original_name=candidate.source_name,
                )
                relative_destination = _relative_destination(
                    config.data.root,
                    destination,
                )
                with closing(connect_database(config.data.root)) as connection:
                    ReceiptRepository(connection).update_image_path(
                        receipt_id,
                        Path(relative_destination),
                    )
            except (
                ImagePathError,
                OSError,
                RuntimeFileError,
                StorageError,
                sqlite3.Error,
            ) as exc:
                results.append(
                    InboxItemResult(
                        source_name=candidate.source_name,
                        receipt_id=receipt_id,
                        status=ReceiptStatus.FAILED,
                        destination=_relative_destination(
                            config.data.root,
                            destination,
                        ),
                        error_code=describe_error(exc).code,
                    )
                )
                continue

            results.append(
                InboxItemResult(
                    source_name=candidate.source_name,
                    receipt_id=receipt_id,
                    status=process_result.status,
                    destination=relative_destination,
                )
            )

    return InboxRunResult(
        scanned=scan.scanned,
        processed=len(results),
        confirmed=sum(result.status is ReceiptStatus.CONFIRMED for result in results),
        review=sum(result.status is ReceiptStatus.REVIEW for result in results),
        failed=sum(result.status is ReceiptStatus.FAILED for result in results),
        skipped=scan.skipped,
        results=results,
    )
