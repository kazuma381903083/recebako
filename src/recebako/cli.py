from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recebako.ai import OllamaError, request_receipt_extraction
from recebako.domain import ReceiptExtraction, ValidationResult
from recebako.imaging import ImagePreprocessError, preprocess_image
from recebako.validation import validate_receipt_payload


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
        with preprocess_image(image_path) as preprocessed:
            raw_extraction = request_receipt_extraction(preprocessed.path)
            extraction, validation = validate_receipt_payload(
                raw_extraction,
                reference_date=datetime.now(timezone.utc).astimezone().date(),
            )
            output = _output_payload(
                extraction,
                validation,
                phash=preprocessed.phash,
            )
    except (ImagePreprocessError, OSError, OllamaError) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _output_payload(
    extraction: ReceiptExtraction | None,
    validation: ValidationResult,
    *,
    phash: str,
) -> dict[str, Any]:
    if extraction is None:
        output: dict[str, Any] = {"receipt": None}
    else:
        output = extraction.model_dump(mode="json")

    output.update(
        {
            "status": validation.status.value,
            "validation_issues": [
                issue.model_dump(mode="json") for issue in validation.issues
            ],
            "phash": phash,
        }
    )
    return output


def main() -> None:
    raise SystemExit(run())
