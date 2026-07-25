from __future__ import annotations

from pathlib import Path, PurePosixPath


class ImagePathError(ValueError):
    """DBへ保存できない画像パスであることを表す。"""


def validate_image_path(value: str | Path | PurePosixPath) -> str:
    raw_value = str(value)
    path = PurePosixPath(raw_value)
    if not raw_value or path.is_absolute():
        raise ImagePathError("画像パスはdata.rootからの相対パスが必要です")
    if "\\" in raw_value or any(part in {"", ".", ".."} for part in path.parts):
        raise ImagePathError("画像パスに安全でない要素が含まれています")
    return path.as_posix()


def image_path_relative_to_root(data_root: Path, image_path: Path) -> str:
    try:
        relative_path = image_path.resolve(strict=False).relative_to(
            data_root.resolve(strict=True)
        )
    except (FileNotFoundError, ValueError) as exc:
        raise ImagePathError("画像パスがdata.root配下ではありません") from exc
    return validate_image_path(relative_path)
