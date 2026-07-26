from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from recebako.ai import OllamaError
from recebako.ai import (
    request_receipt_extraction_with_config as request_receipt_extraction,
)
from recebako.config import (
    AppConfig,
    ConfigError,
    load_config,
    load_ollama_config_for_extract,
)
from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ReceiptFileState,
    ValidationResult,
)
from recebako.evaluation import (
    DEFAULT_EVALUATION_MODELS,
    EvaluationDatasetError,
    EvaluationRunError,
    GroundTruthError,
    run_evaluation,
)
from recebako.imaging import ImagePreprocessError, preprocess_image_variants
from recebako.pipeline import process_receipt
from recebako.pipeline.retry import extract_with_variant_retry
from recebako.runtime import (
    InboxLockError,
    RuntimeFileError,
    RuntimeLayoutError,
    RuntimePaths,
    initialize_runtime,
    move_regular_file_no_overwrite,
    move_to_final,
    recover_runtime,
    run_inbox,
)
from recebako.storage import (
    ImagePathError,
    MigrationError,
    ReceiptRepository,
    StorageError,
    connect_database,
    image_path_relative_to_root,
    initialize_database,
)


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


def _iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "YYYY-MM-DD形式の実在日を指定してください"
        ) from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("YYYY-MM-DD形式の実在日を指定してください")
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

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Git管理外の匿名画像を安全に一括評価します。",
    )
    evaluate_subparsers = evaluate_parser.add_subparsers(
        dest="evaluate_command",
        required=True,
    )
    evaluate_run_parser = evaluate_subparsers.add_parser(
        "run",
        help="modelごとに分離した評価を実行します。",
    )
    evaluate_run_parser.add_argument(
        "source_root",
        type=Path,
        help="case ID形式の匿名画像だけを置いたGit管理外directory",
    )
    evaluate_run_parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="model別DBと安全なreportを保存するGit管理外directory",
    )
    evaluate_run_parser.add_argument(
        "--ground-truth",
        type=Path,
        help="人間が確認した正解CSV（未指定ならaccuracyはunknown）",
    )
    evaluate_run_parser.add_argument(
        "--model",
        dest="models",
        action="append",
        choices=DEFAULT_EVALUATION_MODELS,
        help="比較model。複数回指定できます。",
    )
    evaluate_run_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in IngestMode],
        default=IngestMode.REGULAR.value,
        help="通常取込または過去取込を選択します。",
    )
    evaluate_run_parser.add_argument(
        "--reference-date",
        type=_iso_date,
        help="再現可能な日付検証に使う基準日（YYYY-MM-DD）",
    )
    return parser


def _validate_image_path(image_path: Path) -> bool:
    if not image_path.is_symlink() and image_path.is_file():
        return True
    print(
        f"recebako: error: 画像ファイルが見つかりません: {image_path}",
        file=sys.stderr,
    )
    return False


def _copy_to_processing(image_path: Path, paths: RuntimePaths) -> Path:
    open_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW

    try:
        source_descriptor = os.open(image_path, open_flags)
    except OSError as exc:
        raise RuntimeFileError("画像を安全に読み込めません") from exc

    work_path = paths.processing / f"work-{uuid.uuid4().hex}--{image_path.name}"
    try:
        with (
            os.fdopen(source_descriptor, "rb") as source_file,
            tempfile.TemporaryDirectory(
                prefix="recebako-import-", dir=paths.tmp
            ) as temporary_directory,
        ):
            if not stat.S_ISREG(os.fstat(source_file.fileno()).st_mode):
                raise RuntimeFileError("処理対象が通常ファイルではありません")
            temporary_path = Path(temporary_directory) / "source-copy"
            with temporary_path.open("xb") as temporary_file:
                shutil.copyfileobj(source_file, temporary_file)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            return move_regular_file_no_overwrite(temporary_path, work_path)
    except RuntimeFileError:
        raise
    except OSError as exc:
        raise RuntimeFileError("画像をprocessingへ安全にコピーできません") from exc


def _local_date() -> date:
    return datetime.now(UTC).astimezone().date()


def _run_extract(args: argparse.Namespace) -> int:
    try:
        ollama_config = load_ollama_config_for_extract()
    except ConfigError as exc:
        print(f"recebako: error: {exc}", file=sys.stderr)
        return 1

    image_path: Path = args.image
    if not _validate_image_path(image_path):
        return 2

    try:
        with preprocess_image_variants(image_path) as variants:
            extraction_result = extract_with_variant_retry(
                variants,
                request=lambda variant_path: request_receipt_extraction(
                    variant_path,
                    config=ollama_config,
                ),
                reference_date=_local_date(),
                mode=IngestMode(args.mode),
            )
            output = _output_payload(
                extraction_result.extraction,
                extraction_result.validation,
                phash=extraction_result.phash,
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
        paths, _ = initialize_runtime(config.data.root)
        reference_date = _local_date()
        recovery = recover_runtime(
            config=config,
            fallback_date=reference_date,
            dry_run=False,
        )
        if recovery.errors:
            raise RuntimeFileError("processingの自動回復に失敗しました")
        work_path = _copy_to_processing(image_path, paths)
        result = process_receipt(
            work_path,
            config=config,
            mode=IngestMode(args.mode),
            reference_date=reference_date,
            storage_image_path=Path("processing") / work_path.name,
            file_state=ReceiptFileState.PENDING,
            temporary_root=paths.tmp,
        )
        destination = move_to_final(
            work_path,
            paths,
            receipt_id=result.receipt_id,
            status=result.status,
            date_value=result.date,
            fallback_date=reference_date,
            original_name=image_path.name,
        )
        relative_destination = image_path_relative_to_root(
            config.data.root,
            destination,
        )
        with closing(connect_database(config.data.root)) as connection:
            ReceiptRepository(connection).finalize_image_path(
                result.receipt_id,
                Path(relative_destination),
            )
    except (
        ImagePathError,
        ImagePreprocessError,
        InboxLockError,
        MigrationError,
        OSError,
        OllamaError,
        RuntimeFileError,
        RuntimeLayoutError,
        StorageError,
        sqlite3.Error,
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
        RuntimeFileError,
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
        RuntimeFileError,
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


def _run_evaluate(args: argparse.Namespace) -> int:
    try:
        config = load_config()
        report = run_evaluation(
            args.source_root,
            output_root=args.output_root,
            base_config=config,
            mode=IngestMode(args.mode),
            reference_date=args.reference_date or _local_date(),
            ground_truth_path=args.ground_truth,
            models=(
                DEFAULT_EVALUATION_MODELS if args.models is None else tuple(args.models)
            ),
        )
    except (
        ConfigError,
        EvaluationDatasetError,
        EvaluationRunError,
        GroundTruthError,
    ):
        print(
            "recebako: error: 評価を安全に実行できませんでした",
            file=sys.stderr,
        )
        return 1
    print(report.model_dump_json(indent=2))
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
    if args.command == "evaluate" and args.evaluate_command == "run":
        return _run_evaluate(args)
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
