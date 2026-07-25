from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from recebako.imaging.preprocess import SUPPORTED_SUFFIXES

_CASE_ID_PATTERN = r"^case-[0-9]{4,}$"


class DatasetErrorCode(str, Enum):
    ABSOLUTE_PATH_REQUIRED = "dataset.absolute_path_required"
    SOURCE_UNAVAILABLE = "dataset.source_unavailable"
    DIRECTORY_REQUIRED = "dataset.directory_required"
    SOURCE_IN_GIT = "dataset.source_in_git"
    SYMLINK_REJECTED = "dataset.symlink_rejected"
    NESTED_ENTRY_REJECTED = "dataset.nested_entry_rejected"
    UNSUPPORTED_IMAGE = "dataset.unsupported_image"
    NON_ANONYMOUS_NAME = "dataset.non_anonymous_name"
    DUPLICATE_CASE_ID = "dataset.duplicate_case_id"
    EMPTY = "dataset.empty"


_SAFE_ERROR_MESSAGES = {
    DatasetErrorCode.ABSOLUTE_PATH_REQUIRED: (
        "評価入力ディレクトリは絶対パスで指定してください"
    ),
    DatasetErrorCode.SOURCE_UNAVAILABLE: "評価入力ディレクトリを安全に確認できません",
    DatasetErrorCode.DIRECTORY_REQUIRED: "評価入力にはディレクトリを指定してください",
    DatasetErrorCode.SOURCE_IN_GIT: (
        "評価入力ディレクトリはGitワークツリー外に置いてください"
    ),
    DatasetErrorCode.SYMLINK_REJECTED: "評価入力ではsymlinkを使用できません",
    DatasetErrorCode.NESTED_ENTRY_REJECTED: (
        "評価入力ディレクトリには直下の画像だけを置いてください"
    ),
    DatasetErrorCode.UNSUPPORTED_IMAGE: "評価入力に未対応の画像形式があります",
    DatasetErrorCode.NON_ANONYMOUS_NAME: (
        "評価入力の画像名は匿名case ID形式にしてください"
    ),
    DatasetErrorCode.DUPLICATE_CASE_ID: "評価入力に重複するcase IDがあります",
    DatasetErrorCode.EMPTY: "評価入力ディレクトリに画像がありません",
}


class EvaluationDatasetError(ValueError):
    def __init__(self, code: DatasetErrorCode) -> None:
        self.code = code
        super().__init__(_SAFE_ERROR_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    source_path: Path
    st_dev: int
    st_ino: int
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int


def discover_cases(source_root: Path) -> list[EvaluationCase]:
    source = Path(source_root)
    if not source.is_absolute():
        raise EvaluationDatasetError(DatasetErrorCode.ABSOLUTE_PATH_REQUIRED)
    _reject_symlink_components(source)

    try:
        source_stat = source.stat(follow_symlinks=False)
    except OSError:
        raise EvaluationDatasetError(DatasetErrorCode.SOURCE_UNAVAILABLE) from None
    if not stat.S_ISDIR(source_stat.st_mode):
        raise EvaluationDatasetError(DatasetErrorCode.DIRECTORY_REQUIRED)

    try:
        resolved_source = source.resolve(strict=True)
    except OSError:
        raise EvaluationDatasetError(DatasetErrorCode.SOURCE_UNAVAILABLE) from None
    if _has_git_marker_in_ancestors(resolved_source):
        raise EvaluationDatasetError(DatasetErrorCode.SOURCE_IN_GIT)

    entries = list(_safe_scandir(source))
    if not entries:
        raise EvaluationDatasetError(DatasetErrorCode.EMPTY)

    cases: list[EvaluationCase] = []
    case_ids: set[str] = set()
    for entry in sorted(entries, key=lambda candidate: candidate.name):
        try:
            if entry.is_symlink():
                raise EvaluationDatasetError(DatasetErrorCode.SYMLINK_REJECTED)
            entry_stat = entry.stat(follow_symlinks=False)
        except EvaluationDatasetError:
            raise
        except OSError:
            raise EvaluationDatasetError(DatasetErrorCode.SOURCE_UNAVAILABLE) from None
        if not stat.S_ISREG(entry_stat.st_mode):
            raise EvaluationDatasetError(DatasetErrorCode.NESTED_ENTRY_REJECTED)

        entry_path = Path(entry.name)
        suffix = entry_path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise EvaluationDatasetError(DatasetErrorCode.UNSUPPORTED_IMAGE)
        case_id = entry_path.stem
        if not _is_case_id(case_id):
            raise EvaluationDatasetError(DatasetErrorCode.NON_ANONYMOUS_NAME)
        if case_id in case_ids:
            raise EvaluationDatasetError(DatasetErrorCode.DUPLICATE_CASE_ID)
        case_ids.add(case_id)
        cases.append(
            EvaluationCase(
                case_id=case_id,
                source_path=source / entry.name,
                st_dev=entry_stat.st_dev,
                st_ino=entry_stat.st_ino,
                st_size=entry_stat.st_size,
                st_mtime_ns=entry_stat.st_mtime_ns,
                st_ctime_ns=entry_stat.st_ctime_ns,
            )
        )
    return cases


def _is_case_id(value: str) -> bool:
    return re.fullmatch(_CASE_ID_PATTERN, value) is not None


def _safe_scandir(source: Path) -> Iterator[os.DirEntry[str]]:
    try:
        with os.scandir(source) as iterator:
            yield from iterator
    except OSError:
        raise EvaluationDatasetError(DatasetErrorCode.SOURCE_UNAVAILABLE) from None


def _reject_symlink_components(path: Path) -> None:
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor /= component
        try:
            component_stat = cursor.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise EvaluationDatasetError(DatasetErrorCode.SOURCE_UNAVAILABLE) from None
        if stat.S_ISLNK(component_stat.st_mode):
            raise EvaluationDatasetError(DatasetErrorCode.SYMLINK_REJECTED)


def _has_git_marker_in_ancestors(path: Path) -> bool:
    for candidate in (path, *path.parents):
        try:
            (candidate / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise EvaluationDatasetError(DatasetErrorCode.SOURCE_UNAVAILABLE) from None
        return True
    return False
