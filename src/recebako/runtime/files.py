from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from recebako.domain import ReceiptStatus
from recebako.imaging.preprocess import SUPPORTED_SUFFIXES
from recebako.runtime.layout import RuntimePaths

TRANSFER_SUFFIXES = frozenset({".tmp", ".part", ".download"})
WORK_NAME_SEPARATOR = "--"
_WORK_NAME_PATTERN = re.compile(r"^work-[0-9a-f]{32}--(?P<original>.+)$")


class RuntimeFileError(RuntimeError):
    """実行時ファイルを安全に状態遷移できなかったことを表す。"""


@dataclass(frozen=True)
class InboxCandidate:
    path: Path
    source_name: str
    modified_at_ns: int


@dataclass(frozen=True)
class InboxScan:
    scanned: int
    selected: list[InboxCandidate]
    skipped: int


def scan_inbox(paths: RuntimePaths, *, limit: int | None = None) -> InboxScan:
    candidates: list[InboxCandidate] = []
    for path in paths.inbox.iterdir():
        try:
            if path.name.startswith(".") or path.is_symlink():
                continue
            if path.suffix.lower() in TRANSFER_SUFFIXES:
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES or not path.is_file():
                continue
            stat_result = path.stat(follow_symlinks=False)
        except OSError:
            continue
        candidates.append(
            InboxCandidate(
                path=path,
                source_name=path.name,
                modified_at_ns=stat_result.st_mtime_ns,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.modified_at_ns,
            candidate.source_name,
        )
    )
    selected = candidates if limit is None else candidates[:limit]
    return InboxScan(
        scanned=len(candidates),
        selected=selected,
        skipped=len(candidates) - len(selected),
    )


def claim_inbox_file(
    candidate: InboxCandidate,
    paths: RuntimePaths,
    *,
    token: str | None = None,
) -> Path:
    work_token = uuid.uuid4().hex if token is None else token
    if not re.fullmatch(r"[0-9a-f]{32}", work_token):
        raise RuntimeFileError("processing作業名の識別子が不正です")
    destination = paths.processing / (
        f"work-{work_token}{WORK_NAME_SEPARATOR}{candidate.source_name}"
    )
    if destination.exists() or destination.is_symlink():
        raise RuntimeFileError("processing作業名が衝突しました")
    try:
        return candidate.path.rename(destination)
    except OSError as exc:
        raise RuntimeFileError("画像をprocessingへ移動できません") from exc


def original_name_from_work_name(work_name: str) -> str:
    match = _WORK_NAME_PATTERN.fullmatch(work_name)
    if match is None:
        raise RuntimeFileError("processing作業名から元ファイル名を復元できません")
    original_name = match.group("original")
    if Path(original_name).name != original_name:
        raise RuntimeFileError("processing作業名に安全でない元ファイル名があります")
    return original_name


def collision_free_path(directory: Path, preferred_name: str) -> Path:
    if Path(preferred_name).name != preferred_name:
        raise RuntimeFileError("移動先ファイル名が不正です")
    preferred = directory / preferred_name
    if not preferred.exists() and not preferred.is_symlink():
        return preferred

    suffix = Path(preferred_name).suffix
    stem = preferred_name[: -len(suffix)] if suffix else preferred_name
    counter = 1
    while True:
        candidate = directory / f"{stem}.{counter}{suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        counter += 1


def _archive_parts(date_value: str, fallback_date: date) -> tuple[str, str]:
    try:
        receipt_date = date.fromisoformat(date_value)
    except ValueError:
        receipt_date = fallback_date
    return f"{receipt_date.year:04d}", f"{receipt_date.month:02d}"


def final_directory(
    paths: RuntimePaths,
    *,
    status: ReceiptStatus,
    date_value: str,
    fallback_date: date,
) -> Path:
    if status is ReceiptStatus.CONFIRMED:
        year, month = _archive_parts(date_value, fallback_date)
        return paths.archive / year / month
    if status is ReceiptStatus.REVIEW:
        return paths.review
    return paths.failed


def ensure_safe_directory(data_root: Path, directory: Path) -> None:
    try:
        relative = directory.relative_to(data_root)
    except ValueError as exc:
        raise RuntimeFileError("移動先がdata.root外です") from exc

    current = data_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeFileError("移動先にシンボリックリンクがあります")
        if current.exists() and not current.is_dir():
            raise RuntimeFileError("移動先ディレクトリを作成できません")
        current.mkdir(exist_ok=True)


def is_safe_existing_directory(data_root: Path, directory: Path) -> bool:
    try:
        relative = directory.relative_to(data_root)
    except ValueError:
        return False
    current = data_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            return False
    return directory.is_dir()


def preferred_final_name(receipt_id: int, original_name: str) -> str:
    if receipt_id <= 0 or Path(original_name).name != original_name:
        raise RuntimeFileError("最終ファイル名を決定できません")
    return f"{receipt_id}_{original_name}"


def move_to_final(
    work_path: Path,
    paths: RuntimePaths,
    *,
    receipt_id: int,
    status: ReceiptStatus,
    date_value: str,
    fallback_date: date,
    original_name: str,
) -> Path:
    directory = final_directory(
        paths,
        status=status,
        date_value=date_value,
        fallback_date=fallback_date,
    )
    ensure_safe_directory(paths.root, directory)
    destination = collision_free_path(
        directory,
        preferred_final_name(receipt_id, original_name),
    )
    try:
        return work_path.rename(destination)
    except OSError as exc:
        raise RuntimeFileError("画像を最終フォルダへ移動できません") from exc


def is_final_name_for_receipt(
    filename: str,
    *,
    receipt_id: int,
    original_name: str,
) -> bool:
    preferred_name = preferred_final_name(receipt_id, original_name)
    if filename == preferred_name:
        return True
    suffix = Path(preferred_name).suffix
    stem = preferred_name[: -len(suffix)] if suffix else preferred_name
    pattern = re.compile(rf"^{re.escape(stem)}\.\d+{re.escape(suffix)}$")
    return pattern.fullmatch(filename) is not None
