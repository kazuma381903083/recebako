from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreprocessedImage:
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


@contextmanager
def preprocess_image(source_path: Path) -> Iterator[PreprocessedImage]:
    rgb_image, source_phash = _load_source_image(source_path)

    with rgb_image:
        if max(rgb_image.size) > MAX_IMAGE_DIMENSION:
            rgb_image.thumbnail(
                (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )

        with tempfile.TemporaryDirectory(prefix="recebako-") as temporary_directory:
            output_path = Path(temporary_directory) / "preprocessed.jpg"
            try:
                rgb_image.save(output_path, format="JPEG", quality=90)
            except OSError as exc:
                raise ImagePreprocessError(
                    "前処理済み画像の一時ファイルを作成できませんでした"
                ) from exc

            yield PreprocessedImage(
                path=output_path,
                phash=source_phash,
                width=rgb_image.width,
                height=rgb_image.height,
            )
