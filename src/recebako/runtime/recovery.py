from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recebako.config import AppConfig
from recebako.runtime.files import (
    RuntimeFileError,
    collision_free_path,
    final_directory,
    is_final_name_for_receipt,
    is_safe_existing_directory,
    move_to_final,
    original_name_from_work_name,
    preferred_final_name,
)
from recebako.runtime.layout import (
    RuntimeLayoutError,
    RuntimePaths,
    validate_runtime_paths,
)
from recebako.runtime.lock import InboxLock
from recebako.storage import (
    ImagePathError,
    ReceiptRepository,
    StoredReceipt,
    connect_database,
    database_path,
    image_path_relative_to_root,
    validate_image_path,
)


class RecoveryItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    receipt_id: int | None
    source: str
    destination: str | None
    outcome: str
    error_code: str | None = None


class RecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    scanned: int
    recovered: int
    errors: int
    results: list[RecoveryItemResult]


def _relative(paths: RuntimePaths, path: Path) -> str:
    return image_path_relative_to_root(paths.root, path)


def _recovery_error(
    *,
    action: str,
    receipt_id: int | None,
    source: str,
    code: str,
) -> RecoveryItemResult:
    return RecoveryItemResult(
        action=action,
        receipt_id=receipt_id,
        source=source,
        destination=None,
        outcome="error",
        error_code=code,
    )


def _recover_processing_file(
    work_path: Path,
    *,
    paths: RuntimePaths,
    repository: ReceiptRepository,
    fallback_date: date,
    dry_run: bool,
) -> tuple[RecoveryItemResult, int | None]:
    source = _relative(paths, work_path)
    try:
        original_name = original_name_from_work_name(work_path.name)
    except RuntimeFileError:
        return (
            _recovery_error(
                action="inspect_processing",
                receipt_id=None,
                source=source,
                code="recovery.invalid_work_name",
            ),
            None,
        )

    stored = repository.find_by_image_path(Path(source))
    if stored is None:
        destination = collision_free_path(paths.inbox, original_name)
        if not dry_run:
            work_path.rename(destination)
        return (
            RecoveryItemResult(
                action="return_to_inbox",
                receipt_id=None,
                source=source,
                destination=_relative(paths, destination),
                outcome="planned" if dry_run else "recovered",
            ),
            None,
        )

    if dry_run:
        directory = final_directory(
            paths,
            status=stored.status,
            date_value=stored.date,
            fallback_date=fallback_date,
        )
        destination = collision_free_path(
            directory,
            preferred_final_name(stored.id, original_name),
        )
    else:
        destination = move_to_final(
            work_path,
            paths,
            receipt_id=stored.id,
            status=stored.status,
            date_value=stored.date,
            fallback_date=fallback_date,
            original_name=original_name,
        )
        repository.update_image_path(
            stored.id,
            Path(_relative(paths, destination)),
        )
    return (
        RecoveryItemResult(
            action="complete_final_move",
            receipt_id=stored.id,
            source=source,
            destination=_relative(paths, destination),
            outcome="planned" if dry_run else "recovered",
        ),
        stored.id,
    )


def _final_candidates(
    stored: StoredReceipt,
    *,
    paths: RuntimePaths,
    fallback_date: date,
    original_name: str,
) -> list[Path]:
    directory = final_directory(
        paths,
        status=stored.status,
        date_value=stored.date,
        fallback_date=fallback_date,
    )
    if not is_safe_existing_directory(paths.root, directory):
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if not path.is_symlink()
            and path.is_file()
            and is_final_name_for_receipt(
                path.name,
                receipt_id=stored.id,
                original_name=original_name,
            )
        ),
        key=lambda path: path.name,
    )


def _repair_missing_processing_path(
    stored: StoredReceipt,
    *,
    paths: RuntimePaths,
    repository: ReceiptRepository,
    fallback_date: date,
    dry_run: bool,
) -> RecoveryItemResult:
    source = stored.image_path
    try:
        original_name = original_name_from_work_name(Path(source).name)
    except RuntimeFileError:
        return _recovery_error(
            action="repair_database_path",
            receipt_id=stored.id,
            source=source,
            code="recovery.invalid_work_name",
        )

    candidates = _final_candidates(
        stored,
        paths=paths,
        fallback_date=fallback_date,
        original_name=original_name,
    )
    if len(candidates) != 1:
        return _recovery_error(
            action="repair_database_path",
            receipt_id=stored.id,
            source=source,
            code=(
                "recovery.final_not_found"
                if not candidates
                else "recovery.final_ambiguous"
            ),
        )

    destination = candidates[0]
    if not dry_run:
        repository.update_image_path(
            stored.id,
            Path(_relative(paths, destination)),
        )
    return RecoveryItemResult(
        action="repair_database_path",
        receipt_id=stored.id,
        source=source,
        destination=_relative(paths, destination),
        outcome="planned" if dry_run else "recovered",
    )


def recover_runtime(
    *,
    config: AppConfig,
    fallback_date: date,
    dry_run: bool = False,
) -> RecoveryResult:
    paths = validate_runtime_paths(config.data.root)
    db_path = database_path(config.data.root)
    if db_path.is_symlink() or not db_path.is_file():
        raise RuntimeLayoutError("ledger.dbが初期化されていません")

    results: list[RecoveryItemResult] = []
    handled_receipt_ids: set[int] = set()
    with InboxLock(paths), closing(connect_database(config.data.root)) as connection:
        repository = ReceiptRepository(connection)
        processing_files = sorted(
            (
                path
                for path in paths.processing.iterdir()
                if not path.is_symlink() and path.is_file()
            ),
            key=lambda path: path.name,
        )
        for work_path in processing_files:
            try:
                result, receipt_id = _recover_processing_file(
                    work_path,
                    paths=paths,
                    repository=repository,
                    fallback_date=fallback_date,
                    dry_run=dry_run,
                )
            except sqlite3.Error:
                result = _recovery_error(
                    action="recover_processing",
                    receipt_id=None,
                    source=_relative(paths, work_path),
                    code="recovery.database",
                )
                receipt_id = None
            except OSError:
                result = _recovery_error(
                    action="recover_processing",
                    receipt_id=None,
                    source=_relative(paths, work_path),
                    code="recovery.filesystem",
                )
                receipt_id = None
            except RuntimeFileError:
                result = _recovery_error(
                    action="recover_processing",
                    receipt_id=None,
                    source=_relative(paths, work_path),
                    code="recovery.invalid_transition",
                )
                receipt_id = None
            results.append(result)
            if receipt_id is not None:
                handled_receipt_ids.add(receipt_id)

        for stored in repository.list_with_image_path_prefix("processing"):
            if stored.id in handled_receipt_ids:
                continue
            try:
                safe_stored_path = validate_image_path(stored.image_path)
            except ImagePathError:
                results.append(
                    _recovery_error(
                        action="repair_database_path",
                        receipt_id=stored.id,
                        source=stored.image_path,
                        code="recovery.invalid_database_path",
                    )
                )
                continue
            expected_processing_path = paths.root / safe_stored_path
            if expected_processing_path.exists():
                continue
            try:
                result = _repair_missing_processing_path(
                    stored,
                    paths=paths,
                    repository=repository,
                    fallback_date=fallback_date,
                    dry_run=dry_run,
                )
            except sqlite3.Error:
                result = _recovery_error(
                    action="repair_database_path",
                    receipt_id=stored.id,
                    source=stored.image_path,
                    code="recovery.database",
                )
            except OSError:
                result = _recovery_error(
                    action="repair_database_path",
                    receipt_id=stored.id,
                    source=stored.image_path,
                    code="recovery.filesystem",
                )
            results.append(result)

    return RecoveryResult(
        dry_run=dry_run,
        scanned=len(results),
        recovered=sum(result.outcome != "error" for result in results),
        errors=sum(result.outcome == "error" for result in results),
        results=results,
    )
