from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recebako.ai import (
    OllamaConnectionError,
    OllamaError,
    OllamaResponseError,
    OllamaTimeoutError,
)
from recebako.imaging import (
    ImagePreprocessError,
    InvalidImageError,
    UnsupportedImageFormatError,
)
from recebako.runtime.files import RuntimeFileError
from recebako.runtime.layout import RuntimePaths
from recebako.storage import ImagePathError, MigrationError, StorageError


class FailedMetadataError(RuntimeError):
    """failed用の安全なエラーメタデータを保存できなかったことを表す。"""


class FailedErrorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    error_type: str
    occurred_at: str
    source_filename: str
    retryable: bool
    message: str


@dataclass(frozen=True)
class _ErrorDescription:
    code: str
    retryable: bool
    message: str


_ExceptionMatch = type[BaseException] | tuple[type[BaseException], ...]

_ERROR_DESCRIPTIONS: tuple[tuple[_ExceptionMatch, _ErrorDescription], ...] = (
    (
        OllamaTimeoutError,
        _ErrorDescription(
            "ollama.timeout",
            True,
            "Ollamaの推論が制限時間内に完了しませんでした",
        ),
    ),
    (
        OllamaConnectionError,
        _ErrorDescription(
            "ollama.unavailable",
            True,
            "ローカルのOllamaへ接続できませんでした",
        ),
    ),
    (
        OllamaResponseError,
        _ErrorDescription(
            "ollama.invalid_response",
            False,
            "Ollamaから処理可能な応答を取得できませんでした",
        ),
    ),
    (
        OllamaError,
        _ErrorDescription(
            "ollama.error",
            False,
            "Ollamaによる抽出処理に失敗しました",
        ),
    ),
    (
        UnsupportedImageFormatError,
        _ErrorDescription(
            "image.unsupported",
            False,
            "対応していない画像形式です",
        ),
    ),
    (
        InvalidImageError,
        _ErrorDescription(
            "image.invalid",
            False,
            "画像が空または破損しています",
        ),
    ),
    (
        ImagePreprocessError,
        _ErrorDescription(
            "image.preprocess",
            False,
            "画像の前処理に失敗しました",
        ),
    ),
    (
        (StorageError, MigrationError, sqlite3.Error),
        _ErrorDescription(
            "storage.unavailable",
            True,
            "SQLiteへの保存処理に失敗しました",
        ),
    ),
    (
        ImagePathError,
        _ErrorDescription(
            "storage.invalid_image_path",
            False,
            "画像の保存パスが安全条件を満たしていません",
        ),
    ),
    (
        RuntimeFileError,
        _ErrorDescription(
            "filesystem.transition",
            True,
            "画像の状態遷移に失敗しました",
        ),
    ),
    (
        OSError,
        _ErrorDescription(
            "filesystem.error",
            True,
            "ローカルファイルの操作に失敗しました",
        ),
    ),
)

_UNEXPECTED_ERROR = _ErrorDescription(
    "processing.unexpected",
    False,
    "予期しない処理エラーが発生しました",
)


def describe_error(error: BaseException) -> _ErrorDescription:
    for exception_types, description in _ERROR_DESCRIPTIONS:
        if isinstance(error, exception_types):
            return description
    return _UNEXPECTED_ERROR


def failed_metadata(
    error: BaseException,
    *,
    source_filename: str,
    occurred_at: datetime | None = None,
) -> FailedErrorMetadata:
    description = describe_error(error)
    timestamp = occurred_at or datetime.now().astimezone()
    return FailedErrorMetadata(
        error_code=description.code,
        error_type=type(error).__name__,
        occurred_at=timestamp.isoformat(),
        source_filename=source_filename,
        retryable=description.retryable,
        message=description.message,
    )


def move_to_failed(
    work_path: Path,
    paths: RuntimePaths,
    *,
    source_filename: str,
) -> Path:
    if Path(source_filename).name != source_filename:
        raise FailedMetadataError("failed移動元のファイル名が不正です")
    if paths.failed.is_symlink() or not paths.failed.is_dir():
        raise FailedMetadataError("failedディレクトリを安全に利用できません")
    suffix = Path(source_filename).suffix
    stem = source_filename[: -len(suffix)] if suffix else source_filename
    counter = 0
    while True:
        candidate_name = (
            source_filename if counter == 0 else f"{stem}.{counter}{suffix}"
        )
        destination = paths.failed / candidate_name
        metadata_path = destination.with_name(f"{destination.name}.error.json")
        if (
            not destination.exists()
            and not destination.is_symlink()
            and not metadata_path.exists()
            and not metadata_path.is_symlink()
        ):
            break
        counter += 1
    try:
        return work_path.rename(destination)
    except OSError as exc:
        raise FailedMetadataError("画像をfailedへ移動できません") from exc


def write_failed_metadata(
    failed_image_path: Path,
    metadata: FailedErrorMetadata,
) -> Path:
    metadata_path = failed_image_path.with_name(f"{failed_image_path.name}.error.json")
    if metadata_path.exists() or metadata_path.is_symlink():
        raise FailedMetadataError("failedメタデータの保存先が衝突しました")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".recebako-error-",
            suffix=".json",
            dir=failed_image_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                metadata.model_dump(mode="json"),
                temporary_file,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.rename(metadata_path)
    except OSError as exc:
        raise FailedMetadataError("failedメタデータを保存できません") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return metadata_path
