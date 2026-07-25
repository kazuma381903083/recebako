from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from recebako.imaging import (
    InvalidImageError,
    UnsupportedImageFormatError,
    preprocess_image,
)


def _save_image(
    path: Path,
    *,
    size: tuple[int, int] = (120, 80),
    mode: str = "RGB",
    image_format: str | None = None,
) -> None:
    color: tuple[int, ...] = (240, 240, 240, 128) if mode == "RGBA" else (240, 240, 240)
    with Image.new(mode, size, color) as image:
        image.save(path, format=image_format)


def test_preprocess_applies_exif_orientation_and_removes_temporary_file(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(source_path, exif=exif)
    original_bytes = source_path.read_bytes()

    with preprocess_image(source_path) as processed:
        temporary_path = processed.path
        assert temporary_path != source_path
        assert temporary_path.is_file()
        assert (processed.width, processed.height) == (80, 120)
        with Image.open(temporary_path) as image:
            assert image.size == (80, 120)

    assert not temporary_path.exists()
    assert source_path.read_bytes() == original_bytes


def test_preprocess_shrinks_large_image(tmp_path: Path) -> None:
    source_path = tmp_path / "large.png"
    _save_image(source_path, size=(3000, 1000))

    with preprocess_image(source_path) as processed:
        assert (processed.width, processed.height) == (2048, 683)


def test_preprocess_does_not_enlarge_small_image(tmp_path: Path) -> None:
    source_path = tmp_path / "small.jpg"
    _save_image(source_path, size=(800, 400))

    with preprocess_image(source_path) as processed:
        assert (processed.width, processed.height) == (800, 400)


def test_preprocess_converts_to_rgb_and_generates_phash(tmp_path: Path) -> None:
    source_path = tmp_path / "transparent.png"
    _save_image(source_path, mode="RGBA")

    with preprocess_image(source_path) as processed:
        with Image.open(processed.path) as image:
            assert image.mode == "RGB"
        assert len(processed.phash) == 16
        int(processed.phash, 16)


@pytest.mark.parametrize(
    ("suffix", "image_format"),
    [
        (".jpg", "JPEG"),
        (".png", "PNG"),
        (".heic", "HEIF"),
    ],
)
def test_preprocess_accepts_supported_formats(
    tmp_path: Path,
    suffix: str,
    image_format: str,
) -> None:
    source_path = tmp_path / f"supported{suffix}"
    _save_image(source_path, image_format=image_format)

    with preprocess_image(source_path) as processed:
        assert processed.path.is_file()


def test_preprocess_rejects_empty_file(tmp_path: Path) -> None:
    source_path = tmp_path / "empty.jpg"
    source_path.touch()

    expected_error = pytest.raises(InvalidImageError, match="空")
    with expected_error, preprocess_image(source_path):
        pass


def test_preprocess_rejects_corrupt_image(tmp_path: Path) -> None:
    source_path = tmp_path / "corrupt.png"
    source_path.write_bytes(b"this-is-not-an-image")

    expected_error = pytest.raises(InvalidImageError, match="破損")
    with expected_error, preprocess_image(source_path):
        pass


def test_preprocess_rejects_unsupported_format(tmp_path: Path) -> None:
    source_path = tmp_path / "unsupported.gif"
    _save_image(source_path, image_format="GIF")

    expected_error = pytest.raises(
        UnsupportedImageFormatError,
        match="対応していない",
    )
    with expected_error, preprocess_image(source_path):
        pass
