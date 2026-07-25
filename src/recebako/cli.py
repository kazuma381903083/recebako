from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from recebako.ai import OllamaError, extract_receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recebako",
        description="ローカルのOllamaでレシート画像を読み取ります。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract",
        help="レシート画像を構造化JSONへ変換します。",
    )
    extract_parser.add_argument("image", type=Path, help="レシート画像のパス")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    image_path: Path = args.image

    if not image_path.is_file():
        print(
            f"recebako: error: 画像ファイルが見つかりません: {image_path}",
            file=sys.stderr,
        )
        return 2

    try:
        extraction = extract_receipt(image_path)
    except (OSError, OllamaError) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1

    print(extraction.model_dump_json(indent=2))
    return 0


def main() -> None:
    raise SystemExit(run())
