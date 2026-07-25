from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from recebako.ai import OllamaTimeoutError
from recebako.domain import ReceiptStatus
from recebako.runtime import (
    RUNTIME_DIRECTORY_NAMES,
    FailedMetadataError,
    InboxLock,
    InboxLockError,
    RuntimeFileError,
    RuntimeLayoutError,
    claim_inbox_file,
    failed_metadata,
    initialize_runtime,
    managed_path,
    move_regular_file_no_overwrite,
    move_to_failed,
    move_to_final,
    original_name_from_work_name,
    scan_inbox,
    validate_runtime_paths,
    write_failed_metadata,
)

WORK_TOKEN = "a" * 32


def test_runtime_init_creates_directories_and_migrated_database(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"

    paths, result = initialize_runtime(data_root)

    assert result.data_root_initialized
    assert result.database_initialized
    assert result.directories == list(RUNTIME_DIRECTORY_NAMES)
    assert all((data_root / name).is_dir() for name in RUNTIME_DIRECTORY_NAMES)
    assert paths.lock_file.is_file()
    with sqlite3.connect(data_root / "ledger.db") as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert versions == [
        ("001_initial",),
        ("002_tax_normalization",),
        ("003_receipt_file_state",),
    ]


def test_runtime_init_is_repeatable_and_preserves_existing_files(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    paths, _ = initialize_runtime(data_root)
    existing = paths.inbox / "keep.txt"
    existing.write_text("keep", encoding="utf-8")

    initialize_runtime(data_root)

    assert existing.read_text(encoding="utf-8") == "keep"


def test_runtime_init_does_not_overwrite_managed_directory_name_file(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    conflicting_file = data_root / "inbox"
    conflicting_file.write_text("do-not-overwrite", encoding="utf-8")

    with pytest.raises(RuntimeLayoutError, match="同名のファイル"):
        initialize_runtime(data_root)

    assert conflicting_file.read_text(encoding="utf-8") == "do-not-overwrite"


def test_runtime_init_rejects_root_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "data"
    symlink.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeLayoutError, match="シンボリックリンク"):
        initialize_runtime(symlink)


def test_runtime_init_rejects_managed_directory_symlink(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_root / "inbox").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeLayoutError, match="シンボリックリンク"):
        initialize_runtime(data_root)


def test_runtime_init_rejects_git_worktree_before_writing(tmp_path: Path) -> None:
    worktree = tmp_path / "project"
    worktree.mkdir()
    (worktree / ".git").mkdir()
    data_root = worktree / "private-data"

    with pytest.raises(RuntimeLayoutError, match="Gitワークツリー外"):
        initialize_runtime(data_root)

    assert not data_root.exists()


def test_runtime_validation_rejects_existing_root_inside_git_worktree(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    initialize_runtime(data_root)
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")

    with pytest.raises(RuntimeLayoutError, match="Gitワークツリー外"):
        validate_runtime_paths(data_root)


def test_managed_path_rejects_root_escape(tmp_path: Path) -> None:
    with pytest.raises(RuntimeLayoutError, match="data.root外"):
        managed_path(tmp_path, "../outside")


def test_inbox_scan_empty_is_successful(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")

    result = scan_inbox(paths)

    assert result.scanned == 0
    assert result.selected == []
    assert result.skipped == 0


def test_inbox_scan_filters_and_orders_candidates(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    newer = paths.inbox / "b.PNG"
    older_b = paths.inbox / "b.jpg"
    older_a = paths.inbox / "a.HEIC"
    for path in (newer, older_b, older_a):
        path.write_bytes(b"synthetic")
    os.utime(newer, ns=(3_000, 3_000))
    os.utime(older_a, ns=(1_000, 1_000))
    os.utime(older_b, ns=(1_000, 1_000))

    (paths.inbox / ".hidden.jpg").write_bytes(b"ignored")
    (paths.inbox / "unsupported.gif").write_bytes(b"ignored")
    (paths.inbox / "pending.jpg.part").write_bytes(b"ignored")
    (paths.inbox / "pending.png.download").write_bytes(b"ignored")
    (paths.inbox / "directory.jpg").mkdir()
    (paths.inbox / "linked.jpg").symlink_to(newer)

    result = scan_inbox(paths, limit=2)

    assert result.scanned == 3
    assert [candidate.source_name for candidate in result.selected] == [
        "a.HEIC",
        "b.jpg",
    ]
    assert result.skipped == 1


@pytest.mark.parametrize(
    "filename",
    ["a.jpg", "b.JPEG", "c.png", "d.HEIC", "e.heif"],
)
def test_inbox_scan_accepts_all_supported_suffixes(
    tmp_path: Path,
    filename: str,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    (paths.inbox / filename).write_bytes(b"synthetic")

    result = scan_inbox(paths)

    assert [candidate.source_name for candidate in result.selected] == [filename]


def test_claim_work_name_restores_original_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    source = paths.inbox / "original.JPG"
    original_bytes = b"original-image"
    source.write_bytes(original_bytes)
    candidate = scan_inbox(paths).selected[0]

    work_path = claim_inbox_file(candidate, paths, token=WORK_TOKEN)

    assert not source.exists()
    assert work_path.parent == paths.processing
    assert work_path.suffix == ".JPG"
    assert original_name_from_work_name(work_path.name) == "original.JPG"
    assert work_path.read_bytes() == original_bytes


def test_claim_rejects_regular_file_replaced_after_scan(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    source = paths.inbox / "receipt.jpg"
    source.write_bytes(b"original")
    candidate = scan_inbox(paths).selected[0]
    source.unlink()
    source.write_bytes(b"replacement-with-different-size")

    with pytest.raises(RuntimeFileError, match="置き換え"):
        claim_inbox_file(candidate, paths, token=WORK_TOKEN)

    assert source.read_bytes() == b"replacement-with-different-size"
    assert list(paths.processing.iterdir()) == []


def test_claim_rejects_symlink_replaced_after_scan(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    source = paths.inbox / "receipt.jpg"
    source.write_bytes(b"original")
    candidate = scan_inbox(paths).selected[0]
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(RuntimeFileError, match="通常ファイル"):
        claim_inbox_file(candidate, paths, token=WORK_TOKEN)

    assert source.is_symlink()
    assert outside.read_bytes() == b"outside"
    assert list(paths.processing.iterdir()) == []


def test_claim_retries_generated_work_name_collision_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    source = paths.inbox / "receipt.jpg"
    source.write_bytes(b"new")
    candidate = scan_inbox(paths).selected[0]
    first_token = "a" * 32
    second_token = "b" * 32
    existing = paths.processing / f"work-{first_token}--receipt.jpg"
    existing.write_bytes(b"existing")
    tokens = iter((uuid.UUID(hex=first_token), uuid.UUID(hex=second_token)))
    monkeypatch.setattr(
        "recebako.runtime.files.uuid.uuid4",
        lambda: next(tokens),
    )

    work_path = claim_inbox_file(candidate, paths)

    assert work_path.name == f"work-{second_token}--receipt.jpg"
    assert work_path.read_bytes() == b"new"
    assert existing.read_bytes() == b"existing"


def test_final_moves_use_status_date_and_do_not_overwrite(
    tmp_path: Path,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    archive_work = paths.processing / f"work-{WORK_TOKEN}--receipt.jpg"
    archive_work.write_bytes(b"new")
    archive_directory = paths.archive / "2026" / "07"
    archive_directory.mkdir(parents=True)
    existing = archive_directory / "1_receipt.jpg"
    existing.write_bytes(b"existing")

    archived = move_to_final(
        archive_work,
        paths,
        receipt_id=1,
        status=ReceiptStatus.CONFIRMED,
        date_value="2026-07-25",
        fallback_date=date(2026, 8, 1),
        original_name="receipt.jpg",
    )
    review_work = paths.processing / f"work-{'b' * 32}--review.jpg"
    review_work.write_bytes(b"review")
    reviewed = move_to_final(
        review_work,
        paths,
        receipt_id=2,
        status=ReceiptStatus.REVIEW,
        date_value="",
        fallback_date=date(2026, 8, 1),
        original_name="review.jpg",
    )

    assert archived.relative_to(paths.root).as_posix() == (
        "archive/2026/07/1_receipt.1.jpg"
    )
    assert archived.read_bytes() == b"new"
    assert existing.read_bytes() == b"existing"
    assert reviewed.relative_to(paths.root).as_posix() == "review/2_review.jpg"


def test_final_move_retries_collision_created_during_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    work = paths.processing / f"work-{WORK_TOKEN}--receipt.jpg"
    work.write_bytes(b"new")
    original_link = os.link
    collision_created = False

    def racing_link(
        source: Path,
        destination: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        nonlocal collision_created
        if not collision_created:
            Path(destination).write_bytes(b"racer")
            collision_created = True
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("recebako.runtime.files.os.link", racing_link)

    destination = move_to_final(
        work,
        paths,
        receipt_id=1,
        status=ReceiptStatus.REVIEW,
        date_value="",
        fallback_date=date(2026, 8, 1),
        original_name="receipt.jpg",
    )

    assert (paths.review / "1_receipt.jpg").read_bytes() == b"racer"
    assert destination.name == "1_receipt.1.jpg"
    assert destination.read_bytes() == b"new"


def test_final_move_rejects_symlink_source(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    work = paths.processing / f"work-{WORK_TOKEN}--receipt.jpg"
    work.symlink_to(outside)

    with pytest.raises(RuntimeFileError, match="通常ファイル"):
        move_to_final(
            work,
            paths,
            receipt_id=1,
            status=ReceiptStatus.REVIEW,
            date_value="",
            fallback_date=date(2026, 8, 1),
            original_name="receipt.jpg",
        )

    assert work.is_symlink()
    assert outside.read_bytes() == b"outside"
    assert list(paths.review.iterdir()) == []


def test_exclusive_move_preserves_original_link_when_source_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jpg"
    destination = tmp_path / "destination.jpg"
    source.write_bytes(b"original")
    original_link = os.link

    def replacing_link(
        source_path: Path,
        destination_path: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        original_link(
            source_path,
            destination_path,
            follow_symlinks=follow_symlinks,
        )
        Path(source_path).unlink()
        Path(source_path).write_bytes(b"replacement")

    monkeypatch.setattr("recebako.runtime.files.os.link", replacing_link)

    with pytest.raises(RuntimeFileError, match="移動元の画像が置き換え"):
        move_regular_file_no_overwrite(source, destination)

    assert source.read_bytes() == b"replacement"
    assert destination.read_bytes() == b"original"


def test_confirmed_move_uses_fallback_date_when_receipt_date_is_invalid(
    tmp_path: Path,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    work = paths.processing / f"work-{WORK_TOKEN}--unknown-date.jpg"
    work.write_bytes(b"image")

    destination = move_to_final(
        work,
        paths,
        receipt_id=1,
        status=ReceiptStatus.CONFIRMED,
        date_value="",
        fallback_date=date(2026, 8, 1),
        original_name="unknown-date.jpg",
    )

    assert destination.relative_to(paths.root).as_posix() == (
        "archive/2026/08/1_unknown-date.jpg"
    )


def test_inbox_lock_rejects_second_holder_and_can_be_reused(
    tmp_path: Path,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")

    with (
        InboxLock(paths),
        pytest.raises(InboxLockError, match="実行中"),
        InboxLock(paths),
    ):
        pass

    with InboxLock(paths):
        assert paths.lock_file.is_file()


def test_inbox_lock_releases_after_exception(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")

    with pytest.raises(RuntimeError, match="forced"), InboxLock(paths):
        raise RuntimeError("forced")

    with InboxLock(paths):
        pass


def test_failed_metadata_is_safe_json_and_collision_does_not_overwrite(
    tmp_path: Path,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    existing = paths.failed / "receipt.jpg"
    existing.write_bytes(b"existing")
    work = paths.processing / f"work-{WORK_TOKEN}--receipt.jpg"
    work.write_bytes(b"failed-image")
    error = OllamaTimeoutError("秘密の店名 商品123 9999円 /Users/private/raw-response")

    failed_image = move_to_failed(
        work,
        paths,
        source_filename="receipt.jpg",
    )
    metadata = failed_metadata(
        error,
        source_filename="receipt.jpg",
        occurred_at=datetime(2026, 7, 26, 1, 0, tzinfo=UTC),
    )
    metadata_path = write_failed_metadata(failed_image, metadata)
    decoded = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert failed_image.name == "receipt.1.jpg"
    assert failed_image.read_bytes() == b"failed-image"
    assert existing.read_bytes() == b"existing"
    assert decoded == {
        "error_code": "ollama.timeout",
        "error_type": "OllamaTimeoutError",
        "occurred_at": "2026-07-26T01:00:00+00:00",
        "source_filename": "receipt.jpg",
        "retryable": True,
        "message": "Ollamaの推論が制限時間内に完了しませんでした",
    }
    serialized = metadata_path.read_text(encoding="utf-8")
    for forbidden in (
        "秘密の店名",
        "商品123",
        "9999",
        "/Users/",
        "raw-response",
    ):
        assert forbidden not in serialized


def test_failed_move_retries_collision_created_during_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    work = paths.processing / f"work-{WORK_TOKEN}--receipt.jpg"
    work.write_bytes(b"failed-image")
    original_link = os.link
    collision_created = False

    def racing_link(
        source: Path,
        destination: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        nonlocal collision_created
        if not collision_created:
            Path(destination).write_bytes(b"racer")
            collision_created = True
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("recebako.runtime.files.os.link", racing_link)

    destination = move_to_failed(
        work,
        paths,
        source_filename="receipt.jpg",
    )

    assert (paths.failed / "receipt.jpg").read_bytes() == b"racer"
    assert destination.name == "receipt.1.jpg"
    assert destination.read_bytes() == b"failed-image"


def test_failed_move_rejects_symlink_source(tmp_path: Path) -> None:
    paths, _ = initialize_runtime(tmp_path / "data")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    work = paths.processing / f"work-{WORK_TOKEN}--receipt.jpg"
    work.symlink_to(outside)

    with pytest.raises(FailedMetadataError, match="failedへ移動"):
        move_to_failed(
            work,
            paths,
            source_filename="receipt.jpg",
        )

    assert work.is_symlink()
    assert outside.read_bytes() == b"outside"
    assert list(paths.failed.iterdir()) == []
