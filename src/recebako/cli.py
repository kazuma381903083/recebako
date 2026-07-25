from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from recebako.ai import OllamaError, request_receipt_extraction
from recebako.config import AppConfig, ConfigError, load_config
from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ValidationResult,
)
from recebako.imaging import ImagePreprocessError, preprocess_image
from recebako.pipeline import process_receipt
from recebako.runtime import (
    InboxLockError,
    RuntimeLayoutError,
    initialize_runtime,
    recover_runtime,
    run_inbox,
)
from recebako.storage import (
    MigrationError,
    StorageError,
    initialize_database,
)
from recebako.validation import validate_receipt_payload


def _add_image_and_mode_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("image", type=Path, help="レシート画像のパス")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in IngestMode],
        default=IngestMode.REGULAR.value,
        help="通常取込または過去取込を選択します。",
    )


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("1以上の整数を指定してください") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("1以上の整数を指定してください")
    return parsed


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
    _add_image_and_mode_arguments(extract_parser)

    process_parser = subparsers.add_parser(
        "process",
        help="レシート画像を抽出・検証してSQLiteへ保存します。",
    )
    _add_image_and_mode_arguments(process_parser)

    db_parser = subparsers.add_parser("db", help="SQLiteを管理します。")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("init", help="SQLiteを初期化します。")

    runtime_parser = subparsers.add_parser(
        "runtime",
        help="実行時ディレクトリを管理します。",
    )
    runtime_subparsers = runtime_parser.add_subparsers(
        dest="runtime_command",
        required=True,
    )
    runtime_subparsers.add_parser(
        "init",
        help="実行時ディレクトリとSQLiteを初期化します。",
    )
    recover_parser = runtime_subparsers.add_parser(
        "recover",
        help="中断されたファイル状態遷移を回復します。",
    )
    recover_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ファイルとDBを変更せず回復予定だけを表示します。",
    )

    inbox_parser = subparsers.add_parser(
        "inbox",
        help="inboxの画像を処理します。",
    )
    inbox_subparsers = inbox_parser.add_subparsers(
        dest="inbox_command",
        required=True,
    )
    inbox_run_parser = inbox_subparsers.add_parser(
        "run",
        help="inbox直下の対象画像を一括処理します。",
    )
    inbox_run_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in IngestMode],
        default=IngestMode.REGULAR.value,
        help="通常取込または過去取込を選択します。",
    )
    inbox_run_parser.add_argument(
        "--limit",
        type=_positive_integer,
        help="今回処理する最大件数です。",
    )
    return parser


def _validate_image_path(image_path: Path) -> bool:
    if image_path.is_file():
        return True
    print(
        f"recebako: error: 画像ファイルが見つかりません: {image_path}",
        file=sys.stderr,
    )
    return False


def _local_date() -> date:
    return datetime.now(UTC).astimezone().date()


def _run_extract(args: argparse.Namespace) -> int:
    image_path: Path = args.image
    if not _validate_image_path(image_path):
        return 2

    try:
        with preprocess_image(image_path) as preprocessed:
            raw_extraction = request_receipt_extraction(preprocessed.path)
            extraction, validation = validate_receipt_payload(
                raw_extraction,
                reference_date=_local_date(),
                mode=IngestMode(args.mode),
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


def _run_db_init() -> int:
    try:
        config = load_config()
        initialize_database(config.data.root)
    except (ConfigError, MigrationError, StorageError) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"database": "initialized"}, ensure_ascii=False))
    return 0


def _run_process(args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1

    image_path: Path = args.image
    if not _validate_image_path(image_path):
        return 2

    try:
        result = process_receipt(
            image_path,
            config=config,
            mode=IngestMode(args.mode),
            reference_date=_local_date(),
        )
    except (
        ImagePreprocessError,
        MigrationError,
        OSError,
        OllamaError,
        StorageError,
    ) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2))
    return 0


def _load_runtime_config() -> AppConfig:
    return load_config()


def _run_runtime_init() -> int:
    try:
        config = _load_runtime_config()
        _, result = initialize_runtime(config.data.root)
    except (
        ConfigError,
        MigrationError,
        OSError,
        RuntimeLayoutError,
        StorageError,
    ) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1
    print(result.model_dump_json(indent=2))
    return 0


def _run_inbox(args: argparse.Namespace) -> int:
    try:
        config = _load_runtime_config()
        result = run_inbox(
            config=config,
            mode=IngestMode(args.mode),
            reference_date=_local_date(),
            limit=args.limit,
        )
    except (
        ConfigError,
        InboxLockError,
        MigrationError,
        OSError,
        RuntimeLayoutError,
        StorageError,
        ValueError,
    ) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1
    if result.failed:
        print(
            f"recebako: warning: {result.failed}件の処理に失敗しました",
            file=sys.stderr,
        )
    print(result.model_dump_json(indent=2))
    return 0


def _run_runtime_recover(args: argparse.Namespace) -> int:
    try:
        config = _load_runtime_config()
        result = recover_runtime(
            config=config,
            fallback_date=_local_date(),
            dry_run=bool(args.dry_run),
        )
    except (
        ConfigError,
        InboxLockError,
        OSError,
        RuntimeLayoutError,
        StorageError,
    ) as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1
    if result.errors:
        print(
            f"recebako: warning: {result.errors}件を自動回復できませんでした",
            file=sys.stderr,
        )
    print(result.model_dump_json(indent=2))
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "extract":
        return _run_extract(args)
    if args.command == "process":
        return _run_process(args)
    if args.command == "db" and args.db_command == "init":
        return _run_db_init()
    if args.command == "runtime" and args.runtime_command == "init":
        return _run_runtime_init()
    if args.command == "runtime" and args.runtime_command == "recover":
        return _run_runtime_recover(args)
    if args.command == "inbox" and args.inbox_command == "run":
        return _run_inbox(args)
    raise AssertionError("到達不能なCLIコマンドです")


def _output_payload(
    extraction: NormalizedReceiptExtraction | None,
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
