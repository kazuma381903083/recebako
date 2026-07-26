from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from recebako.imaging import (
    ImagePreprocessError,
    ImageVariantKind,
    InvalidImageError,
    UnsupportedImageFormatError,
    preprocess_image,
    preprocess_image_variants,
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


def test_preprocess_uses_requested_temporary_root_and_cleans_it(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jpg"
    temporary_root = tmp_path / "runtime-tmp"
    temporary_root.mkdir()
    _save_image(source_path)

    with preprocess_image(
        source_path,
        temporary_root=temporary_root,
    ) as processed:
        assert processed.path.is_relative_to(temporary_root)
        assert processed.path.is_file()

    assert list(temporary_root.iterdir()) == []


def test_preprocess_variants_are_lazy_and_have_fixed_order_and_dimensions(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jpg"
    temporary_root = tmp_path / "runtime-tmp"
    temporary_root.mkdir()
    _save_image(source_path, size=(120, 80))
    original_bytes = source_path.read_bytes()

    with preprocess_image_variants(
        source_path,
        temporary_root=temporary_root,
    ) as variants:
        temporary_directories = list(temporary_root.iterdir())
        assert len(temporary_directories) == 1
        variant_directory = temporary_directories[0]
        assert list(variant_directory.iterdir()) == []

        standard = next(variants)
        assert standard.kind is ImageVariantKind.STANDARD
        assert (standard.width, standard.height) == (120, 80)
        assert [path.name for path in variant_directory.iterdir()] == [
            "variant-1-standard.jpg"
        ]

        rotated = next(variants)
        assert rotated.kind is ImageVariantKind.ROTATED_CLOCKWISE
        assert (rotated.width, rotated.height) == (80, 120)

        upscaled = next(variants)
        assert upscaled.kind is ImageVariantKind.UPSCALED
        assert (upscaled.width, upscaled.height) == (240, 160)
        assert [standard.path.parent, rotated.path.parent, upscaled.path.parent] == [
            variant_directory,
            variant_directory,
            variant_directory,
        ]
        assert standard.phash == rotated.phash == upscaled.phash

        with pytest.raises(StopIteration):
            next(variants)

    assert list(temporary_root.iterdir()) == []
    assert source_path.read_bytes() == original_bytes


def test_preprocess_rotated_variant_is_clockwise_for_asymmetric_image(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "asymmetric.png"
    with Image.new("RGB", (60, 40), "blue") as image:
        for x in range(20):
            for y in range(15):
                image.putpixel((x, y), (255, 0, 0))
        image.save(source_path)

    with preprocess_image_variants(source_path) as variants:
        next(variants)
        rotated = next(variants)
        with Image.open(rotated.path).convert("RGB") as image:
            top_right = image.getpixel((35, 5))
            top_left = image.getpixel((5, 5))

    assert top_right[0] > top_right[2]
    assert top_left[2] > top_left[0]


@pytest.mark.parametrize(
    ("source_size", "expected_standard", "expected_upscaled"),
    [
        ((800, 400), (800, 400), (1600, 800)),
        ((1500, 1000), (1500, 1000), (2048, 1365)),
        ((3000, 1000), (2048, 683), (2048, 683)),
    ],
)
def test_preprocess_upscaled_variant_respects_dimension_limit(
    tmp_path: Path,
    source_size: tuple[int, int],
    expected_standard: tuple[int, int],
    expected_upscaled: tuple[int, int],
) -> None:
    source_path = tmp_path / "source.png"
    _save_image(source_path, size=source_size)

    with preprocess_image_variants(source_path) as variants:
        standard = next(variants)
        next(variants)
        upscaled = next(variants)

    assert (standard.width, standard.height) == expected_standard
    assert (upscaled.width, upscaled.height) == expected_upscaled


def test_preprocess_variants_share_existing_compatible_phash(tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    _save_image(source_path)

    with preprocess_image(source_path) as existing:
        expected_phash = existing.phash
    with preprocess_image_variants(source_path) as variants:
        generated = list(variants)

    assert {variant.phash for variant in generated} == {expected_phash}


def test_preprocess_variants_open_source_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.png"
    _save_image(source_path)
    original_open = Image.open
    opened_paths: list[Path] = []

    def tracked_open(path: Path) -> Image.Image:
        opened_paths.append(path)
        return original_open(path)

    monkeypatch.setattr("recebako.imaging.preprocess.Image.open", tracked_open)

    with preprocess_image_variants(source_path) as variants:
        list(variants)

    assert opened_paths == [source_path]


def test_preprocess_variants_cleanup_after_early_stop_and_exception(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jpg"
    temporary_root = tmp_path / "runtime-tmp"
    temporary_root.mkdir()
    _save_image(source_path)

    with (
        pytest.raises(RuntimeError, match="stop"),
        preprocess_image_variants(
            source_path,
            temporary_root=temporary_root,
        ) as variants,
    ):
        next(variants)
        raise RuntimeError("stop")

    assert list(temporary_root.iterdir()) == []


def test_preprocess_rejects_symlink_component_in_temporary_root(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jpg"
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    symlink_root = tmp_path / "linked-root"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    _save_image(source_path)

    expected_error = pytest.raises(ImagePreprocessError, match="シンボリックリンク")
    with (
        expected_error,
        preprocess_image_variants(
            source_path,
            temporary_root=symlink_root,
        ),
    ):
        pass


@pytest.mark.parametrize("root_kind", ["relative", "missing", "file"])
def test_preprocess_rejects_unsafe_explicit_temporary_root(
    tmp_path: Path,
    root_kind: str,
) -> None:
    source_path = tmp_path / "source.jpg"
    _save_image(source_path)
    if root_kind == "relative":
        temporary_root = Path("relative-root")
    elif root_kind == "missing":
        temporary_root = tmp_path / "missing-root"
    else:
        temporary_root = tmp_path / "root-file"
        temporary_root.write_text("not a directory", encoding="utf-8")

    expected_error = pytest.raises(ImagePreprocessError)
    with (
        expected_error,
        preprocess_image_variants(
            source_path,
            temporary_root=temporary_root,
        ),
    ):
        pass


def test_preprocess_hides_temporary_variant_path_from_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.jpg"
    private_path = tmp_path / "private-variant-path"
    _save_image(source_path)

    def fail_temporary_directory(*args: object, **kwargs: object) -> None:
        raise OSError(f"cannot create {private_path}")

    monkeypatch.setattr(
        "recebako.imaging.preprocess.tempfile.TemporaryDirectory",
        fail_temporary_directory,
    )

    with (
        pytest.raises(ImagePreprocessError) as captured,
        preprocess_image_variants(source_path),
    ):
        pass

    assert str(private_path) not in str(captured.value)


def test_preprocess_preserves_caller_os_error_and_cleans_variants(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jpg"
    temporary_root = tmp_path / "runtime-tmp"
    temporary_root.mkdir()
    _save_image(source_path)
    caller_error = OSError("caller filesystem failure")

    with (
        pytest.raises(OSError) as captured,
        preprocess_image_variants(
            source_path,
            temporary_root=temporary_root,
        ) as variants,
    ):
        next(variants)
        raise caller_error

    assert captured.value is caller_error
    assert list(temporary_root.iterdir()) == []


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
