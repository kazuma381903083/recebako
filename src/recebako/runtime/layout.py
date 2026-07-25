from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from recebako.storage import initialize_database

RUNTIME_DIRECTORY_NAMES = (
    "inbox",
    "processing",
    "archive",
    "review",
    "failed",
    "reports",
    "logs",
    "tmp",
)


class RuntimeLayoutError(RuntimeError):
    """実行時ディレクトリを安全に利用できないことを表す。"""


class RuntimeInitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_root_initialized: bool
    database_initialized: bool
    directories: list[str]


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    inbox: Path
    processing: Path
    archive: Path
    review: Path
    failed: Path
    reports: Path
    logs: Path
    tmp: Path
    lock_file: Path


def _assert_no_symlink_components(path: Path) -> None:
    current = path
    existing_components: list[Path] = []
    while True:
        if current.exists() or current.is_symlink():
            existing_components.append(current)
        if current == current.parent:
            break
        current = current.parent

    for component in existing_components:
        if component.is_symlink():
            raise RuntimeLayoutError(
                "data.rootまたはその経路にシンボリックリンクがあります"
            )


def _assert_outside_git_worktree(data_root: Path) -> None:
    current = data_root.resolve(strict=False)
    while True:
        git_marker = current / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            raise RuntimeLayoutError("data.rootはGitワークツリー外に配置してください")
        if current == current.parent:
            break
        current = current.parent


def managed_path(data_root: Path, relative_name: str) -> Path:
    relative = PurePosixPath(relative_name)
    if (
        relative.is_absolute()
        or len(relative.parts) != 1
        or relative.parts[0] in {"", ".", ".."}
    ):
        raise RuntimeLayoutError("管理ディレクトリ名がdata.root外を参照しています")
    return data_root / relative.parts[0]


def _paths(data_root: Path) -> RuntimePaths:
    return RuntimePaths(
        root=data_root,
        inbox=managed_path(data_root, "inbox"),
        processing=managed_path(data_root, "processing"),
        archive=managed_path(data_root, "archive"),
        review=managed_path(data_root, "review"),
        failed=managed_path(data_root, "failed"),
        reports=managed_path(data_root, "reports"),
        logs=managed_path(data_root, "logs"),
        tmp=managed_path(data_root, "tmp"),
        lock_file=managed_path(data_root, ".recebako-inbox.lock"),
    )


def _reject_unsafe_existing_path(path: Path, *, directory: bool) -> None:
    if path.is_symlink():
        raise RuntimeLayoutError("管理対象にシンボリックリンクがあります")
    if path.exists() and directory and not path.is_dir():
        raise RuntimeLayoutError("管理ディレクトリと同名のファイルがあります")
    if path.exists() and not directory and not path.is_file():
        raise RuntimeLayoutError("管理ファイルと同名のディレクトリがあります")


def initialize_runtime(data_root: Path) -> tuple[RuntimePaths, RuntimeInitResult]:
    if not data_root.is_absolute():
        raise RuntimeLayoutError("data.rootは絶対パスである必要があります")
    _assert_outside_git_worktree(data_root)
    _assert_no_symlink_components(data_root)
    _reject_unsafe_existing_path(data_root, directory=True)
    data_root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(data_root)

    paths = _paths(data_root)
    for name in RUNTIME_DIRECTORY_NAMES:
        directory = managed_path(data_root, name)
        _reject_unsafe_existing_path(directory, directory=True)
        directory.mkdir(exist_ok=True)

    _reject_unsafe_existing_path(paths.lock_file, directory=False)
    lock_flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    lock_descriptor = os.open(paths.lock_file, lock_flags, 0o600)
    os.close(lock_descriptor)

    database_path = data_root / "ledger.db"
    _reject_unsafe_existing_path(database_path, directory=False)
    initialize_database(data_root)
    return paths, RuntimeInitResult(
        data_root_initialized=True,
        database_initialized=True,
        directories=list(RUNTIME_DIRECTORY_NAMES),
    )


def validate_runtime_paths(data_root: Path) -> RuntimePaths:
    if not data_root.is_absolute():
        raise RuntimeLayoutError("data.rootは絶対パスである必要があります")
    _assert_outside_git_worktree(data_root)
    _assert_no_symlink_components(data_root)
    paths = _paths(data_root)
    if not data_root.is_dir():
        raise RuntimeLayoutError("data.rootが初期化されていません")
    for name in RUNTIME_DIRECTORY_NAMES:
        directory = managed_path(data_root, name)
        _reject_unsafe_existing_path(directory, directory=True)
        if not directory.is_dir():
            raise RuntimeLayoutError("実行時ディレクトリが初期化されていません")
    _reject_unsafe_existing_path(paths.lock_file, directory=False)
    if not paths.lock_file.is_file():
        raise RuntimeLayoutError("inbox排他ロックが初期化されていません")
    return paths
