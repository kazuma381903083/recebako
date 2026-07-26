from __future__ import annotations

import stat
import tempfile
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import imagehash
import pillow_heif  # type: ignore[import-untyped]
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_DIMENSION = 2048
SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})
SUPPORTED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "HEIF", "HEIC"})

pillow_heif.register_heif_opener()


class ImagePreprocessError(RuntimeError):
    """画像をOllama向けに前処理できなかったことを表す。"""


class UnsupportedImageFormatError(ImagePreprocessError):
    """入力画像の形式がサポート対象外であることを表す。"""


class InvalidImageError(ImagePreprocessError):
    """入力ファイルが空または画像として破損していることを表す。"""


class ImageVariantKind(str, Enum):
    """抽出再試行で使用する決定的な画像variant。"""

    STANDARD = "standard"
    ROTATED_CLOCKWISE = "rotated_clockwise_90"
    UPSCALED = "upscaled_2x"


@dataclass(frozen=True)
class PreprocessedImage:
    path: Path
    phash: str
    width: int
    height: int


@dataclass(frozen=True)
class PreprocessedImageVariant:
    kind: ImageVariantKind
    path: Path
    phash: str
    width: int
    height: int


def _load_source_image(source_path: Path) -> tuple[Image.Image, str]:
    if source_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise UnsupportedImageFormatError(
            f"対応していない画像形式です: {source_path.suffix or '(拡張子なし)'}"
        )

    try:
        if source_path.stat().st_size == 0:
            raise InvalidImageError("画像ファイルが空です")
    except FileNotFoundError as exc:
        raise InvalidImageError(f"画像ファイルが見つかりません: {source_path}") from exc

    try:
        with Image.open(source_path) as source:
            source.load()
            if source.format not in SUPPORTED_IMAGE_FORMATS:
                raise UnsupportedImageFormatError(
                    f"対応していない画像形式です: {source.format or '不明'}"
                )
            source_phash = str(imagehash.phash(source))
            oriented = ImageOps.exif_transpose(source)
            return oriented.convert("RGB"), source_phash
    except UnsupportedImageFormatError:
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise InvalidImageError(
            "画像ファイルを読み込めません。破損を確認してください"
        ) from exc


def _validate_temporary_root(temporary_root: Path | None) -> Path | None:
    if temporary_root is None:
        return None
    if not temporary_root.is_absolute() or ".." in temporary_root.parts:
        raise ImagePreprocessError(
            "一時ファイルの保存先は安全な絶対パスで指定してください"
        )

    current = Path(temporary_root.anchor)
    try:
        for part in temporary_root.parts[1:]:
            current /= part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ImagePreprocessError(
                    "一時ファイルの保存先にシンボリックリンクがあります"
                )
            if not stat.S_ISDIR(mode):
                raise ImagePreprocessError(
                    "一時ファイルの保存先は実在するディレクトリにしてください"
                )
    except FileNotFoundError as exc:
        raise ImagePreprocessError(
            "一時ファイルの保存先は実在するディレクトリにしてください"
        ) from exc
    except OSError as exc:
        raise ImagePreprocessError(
            "一時ファイルの保存先を安全に検証できませんでした"
        ) from exc
    return temporary_root


def _prepare_standard_image(rgb_image: Image.Image) -> None:
    if max(rgb_image.size) > MAX_IMAGE_DIMENSION:
        rgb_image.thumbnail(
            (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
            Image.Resampling.LANCZOS,
        )


def _upscaled_size(image: Image.Image) -> tuple[int, int]:
    scale = min(2.0, MAX_IMAGE_DIMENSION / max(image.size))
    return (
        min(MAX_IMAGE_DIMENSION, max(1, round(image.width * scale))),
        min(MAX_IMAGE_DIMENSION, max(1, round(image.height * scale))),
    )


def _save_variant(
    image: Image.Image,
    *,
    path: Path,
    kind: ImageVariantKind,
    phash: str,
) -> PreprocessedImageVariant:
    try:
        image.save(path, format="JPEG", quality=90)
    except OSError as exc:
        raise ImagePreprocessError(
            "前処理済み画像の一時ファイルを作成できませんでした"
        ) from exc
    return PreprocessedImageVariant(
        kind=kind,
        path=path,
        phash=phash,
        width=image.width,
        height=image.height,
    )


@contextmanager
def _temporary_variant_directory(
    temporary_root: Path | None,
) -> Iterator[Path]:
    try:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="recebako-",
            dir=temporary_root,
        )
    except OSError as exc:
        raise ImagePreprocessError(
            "抽出用画像variantの一時領域を安全に利用できませんでした"
        ) from exc

    try:
        yield Path(temporary_directory.name)
    except BaseException:
        try:
            temporary_directory.cleanup()
        except OSError:
            pass
        raise
    else:
        try:
            temporary_directory.cleanup()
        except OSError as exc:
            raise ImagePreprocessError(
                "抽出用画像variantの一時領域を安全に利用できませんでした"
            ) from exc


def _iter_variants(
    standard_image: Image.Image,
    *,
    temporary_directory: Path,
    phash: str,
) -> Generator[PreprocessedImageVariant, None, None]:
    yield _save_variant(
        standard_image,
        path=temporary_directory / "variant-1-standard.jpg",
        kind=ImageVariantKind.STANDARD,
        phash=phash,
    )

    with standard_image.transpose(Image.Transpose.ROTATE_270) as rotated:
        rotated_variant = _save_variant(
            rotated,
            path=temporary_directory / "variant-2-rotated-clockwise-90.jpg",
            kind=ImageVariantKind.ROTATED_CLOCKWISE,
            phash=phash,
        )
    yield rotated_variant

    upscaled_size = _upscaled_size(standard_image)
    if upscaled_size == standard_image.size:
        upscaled_variant = _save_variant(
            standard_image,
            path=temporary_directory / "variant-3-upscaled-2x.jpg",
            kind=ImageVariantKind.UPSCALED,
            phash=phash,
        )
    else:
        with standard_image.resize(
            upscaled_size,
            Image.Resampling.LANCZOS,
        ) as upscaled:
            upscaled_variant = _save_variant(
                upscaled,
                path=temporary_directory / "variant-3-upscaled-2x.jpg",
                kind=ImageVariantKind.UPSCALED,
                phash=phash,
            )
    yield upscaled_variant


@contextmanager
def preprocess_image_variants(
    source_path: Path,
    *,
    temporary_root: Path | None = None,
) -> Iterator[Iterator[PreprocessedImageVariant]]:
    """元画像を一度だけ読み込み、抽出用variantを必要な順に生成する。"""

    validated_temporary_root = _validate_temporary_root(temporary_root)
    rgb_image, source_phash = _load_source_image(source_path)

    with rgb_image:
        _prepare_standard_image(rgb_image)
        with _temporary_variant_directory(validated_temporary_root) as temporary_path:
            variants = _iter_variants(
                rgb_image,
                temporary_directory=temporary_path,
                phash=source_phash,
            )
            try:
                yield variants
            finally:
                variants.close()


@contextmanager
def preprocess_image(
    source_path: Path,
    *,
    temporary_root: Path | None = None,
) -> Iterator[PreprocessedImage]:
    with preprocess_image_variants(
        source_path,
        temporary_root=temporary_root,
    ) as variants:
        standard = next(variants)
        yield PreprocessedImage(
            path=standard.path,
            phash=standard.phash,
            width=standard.width,
            height=standard.height,
        )
