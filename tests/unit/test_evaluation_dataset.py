from __future__ import annotations

import traceback
from pathlib import Path

import pytest

from recebako.evaluation.dataset import (
    DatasetErrorCode,
    EvaluationDatasetError,
    discover_cases,
)


def test_discover_cases_returns_supported_anonymous_images_in_case_order(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "case-0002.HEIC").write_bytes(b"second")
    (source / "case-0001.jpg").write_bytes(b"first")
    (source / "case-0010.png").write_bytes(b"tenth")
    (source / "case-0100.heif").write_bytes(b"hundredth")
    (source / "case-1000.jpeg").write_bytes(b"thousandth")

    cases = discover_cases(source)

    assert [case.case_id for case in cases] == [
        "case-0001",
        "case-0002",
        "case-0010",
        "case-0100",
        "case-1000",
    ]
    assert [case.source_path for case in cases] == [
        source / "case-0001.jpg",
        source / "case-0002.HEIC",
        source / "case-0010.png",
        source / "case-0100.heif",
        source / "case-1000.jpeg",
    ]
    for case in cases:
        source_stat = case.source_path.stat(follow_symlinks=False)
        assert (
            case.st_dev,
            case.st_ino,
            case.st_size,
            case.st_mtime_ns,
            case.st_ctime_ns,
        ) == (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_size,
            source_stat.st_mtime_ns,
            source_stat.st_ctime_ns,
        )


def test_discover_cases_requires_an_absolute_path() -> None:
    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(Path("relative-source"))

    assert captured.value.code is DatasetErrorCode.ABSOLUTE_PATH_REQUIRED
    assert "relative-source" not in str(captured.value)


def test_discover_cases_requires_a_directory(tmp_path: Path) -> None:
    source = tmp_path / "case-0001.jpg"
    source.write_bytes(b"image")

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.DIRECTORY_REQUIRED


def test_discover_cases_hides_an_unavailable_source_path(tmp_path: Path) -> None:
    private_sentinel = "PRIVATE-SENTINEL"
    source = tmp_path / private_sentinel

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert captured.value.code is DatasetErrorCode.SOURCE_UNAVAILABLE
    assert private_sentinel not in rendered


def test_discover_cases_rejects_current_git_worktree_without_scanning_entries() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(repository_root)

    assert captured.value.code is DatasetErrorCode.SOURCE_IN_GIT


def test_discover_cases_rejects_a_symlink_source_component(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "case-0001.jpg").write_bytes(b"image")
    source = tmp_path / "source-link"
    source.symlink_to(target, target_is_directory=True)

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.SYMLINK_REJECTED


def test_discover_cases_rejects_a_symlink_entry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "outside.jpg"
    target.write_bytes(b"image")
    (source / "case-0001.jpg").symlink_to(target)

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.SYMLINK_REJECTED


def test_discover_cases_rejects_nested_entries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    nested = source / "nested"
    nested.mkdir()
    (nested / "case-0001.jpg").write_bytes(b"image")

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.NESTED_ENTRY_REJECTED


def test_discover_cases_rejects_unsupported_images_without_leaking_name(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    private_sentinel = "PRIVATE-SENTINEL"
    (source / f"case-0001-{private_sentinel}.gif").write_bytes(b"image")

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.UNSUPPORTED_IMAGE
    assert private_sentinel not in str(captured.value)


def test_discover_cases_rejects_non_anonymous_names_without_leaking_name(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    private_sentinel = "PRIVATE-SENTINEL"
    (source / f"{private_sentinel}.jpg").write_bytes(b"image")

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.NON_ANONYMOUS_NAME
    assert private_sentinel not in str(captured.value)


def test_discover_cases_rejects_duplicate_case_stems(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "case-0001.jpg").write_bytes(b"first")
    (source / "case-0001.png").write_bytes(b"second")

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.DUPLICATE_CASE_ID


def test_discover_cases_rejects_an_empty_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(EvaluationDatasetError) as captured:
        discover_cases(source)

    assert captured.value.code is DatasetErrorCode.EMPTY
