from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path

from recebako.ai import OllamaError
from recebako.ai.ollama import EXTRACTION_PROMPT
from recebako.config import AppConfig, DataConfig
from recebako.domain import IngestMode, ReceiptExtraction
from recebako.evaluation.dataset import EvaluationCase, discover_cases
from recebako.evaluation.models import (
    AccuracyMetric,
    AccuracyStatus,
    AccuracySummary,
    AccuracyUnknownReason,
    CaseEvaluationResult,
    DateOutcome,
    DuplicateOutcome,
    DurationSummary,
    EvaluationReport,
    EvaluationStatus,
    ModelEvaluationReport,
    ModelEvaluationSummary,
    QualityAssessment,
    QualityAssessmentStatus,
    QualityBaselineReport,
    QualityBaselineSummary,
    QualityModelReport,
    QualityProvenance,
    QualityRateMetric,
    QualityThresholds,
    QualityUnknownReason,
    SchemaOutcome,
    TaxOutcome,
)
from recebako.evaluation.quality import (
    REQUIRED_VERIFIED_CASE_COUNT,
    _QualityAccumulator,
    _QualityCounts,
)
from recebako.evaluation.truth import (
    GroundTruthCase,
    GroundTruthDataset,
    load_ground_truth_csv,
)
from recebako.imaging import ImagePreprocessError
from recebako.pipeline import ProcessResult, process_receipt_with_audit
from recebako.runtime import (
    RUNTIME_DIRECTORY_NAMES,
    RuntimeLayoutError,
    describe_error,
    initialize_runtime,
)
from recebako.storage import (
    ImagePathError,
    MigrationError,
    ReceiptRepository,
    StorageError,
    StoredItem,
    connect_database,
)

DEFAULT_EVALUATION_MODELS = ("qwen3-vl:8b", "qwen3.5:9b")
_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_REPORT_FILENAME = "evaluation-report.json"
_QUALITY_REPORT_FILENAME = "quality-baseline-report.json"


class EvaluationRunErrorCode(str, Enum):
    OUTPUT_PATH_INVALID = "evaluation.output_path_invalid"
    OUTPUT_IN_GIT = "evaluation.output_in_git"
    ROOTS_OVERLAP = "evaluation.roots_overlap"
    SYMLINK_REJECTED = "evaluation.symlink_rejected"
    MODEL_INVALID = "evaluation.model_invalid"
    RUN_ID_INVALID = "evaluation.run_id_invalid"
    RUNTIME_UNAVAILABLE = "evaluation.runtime_unavailable"
    OUTPUT_CHANGED = "evaluation.output_changed"
    SOURCE_CHANGED = "evaluation.source_changed"
    COPY_FAILED = "evaluation.copy_failed"
    STORED_RESULT_MISSING = "evaluation.stored_result_missing"
    REPORT_WRITE_FAILED = "evaluation.report_write_failed"


_RUN_ERROR_MESSAGES = {
    EvaluationRunErrorCode.OUTPUT_PATH_INVALID: (
        "評価出力ディレクトリを安全に利用できません"
    ),
    EvaluationRunErrorCode.OUTPUT_IN_GIT: (
        "評価出力ディレクトリはGitワークツリー外に置いてください"
    ),
    EvaluationRunErrorCode.ROOTS_OVERLAP: (
        "評価入力と評価出力は分離されたディレクトリにしてください"
    ),
    EvaluationRunErrorCode.SYMLINK_REJECTED: ("評価出力ではsymlinkを使用できません"),
    EvaluationRunErrorCode.MODEL_INVALID: "評価対象modelの指定が不正です",
    EvaluationRunErrorCode.RUN_ID_INVALID: "評価run IDの形式が不正です",
    EvaluationRunErrorCode.RUNTIME_UNAVAILABLE: ("評価runtimeを安全に初期化できません"),
    EvaluationRunErrorCode.OUTPUT_CHANGED: (
        "評価出力が検証後に変更されたため処理を中止しました"
    ),
    EvaluationRunErrorCode.SOURCE_CHANGED: (
        "評価入力がscan後に変更されたため処理を中止しました"
    ),
    EvaluationRunErrorCode.COPY_FAILED: (
        "評価入力をmodel別runtimeへ安全にcopyできません"
    ),
    EvaluationRunErrorCode.STORED_RESULT_MISSING: (
        "評価用DBの保存結果を安全に確認できません"
    ),
    EvaluationRunErrorCode.REPORT_WRITE_FAILED: ("評価reportを安全に保存できません"),
}


class EvaluationRunError(RuntimeError):
    def __init__(self, code: EvaluationRunErrorCode) -> None:
        self.code = code
        super().__init__(_RUN_ERROR_MESSAGES[code])


@dataclass(slots=True)
class _PinnedDirectory:
    path: Path
    descriptor: int

    def assert_current(self) -> None:
        try:
            descriptor_stat = os.fstat(self.descriptor)
            path_stat = self.path.lstat()
        except OSError:
            raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_CHANGED) from None
        if (
            not stat.S_ISDIR(descriptor_stat.st_mode)
            or not stat.S_ISDIR(path_stat.st_mode)
            or _directory_identity(descriptor_stat) != _directory_identity(path_stat)
        ):
            raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_CHANGED)

    def close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)


def _directory_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _pin_existing_directory(
    path: Path,
    *,
    error_code: EvaluationRunErrorCode,
) -> _PinnedDirectory:
    descriptor = -1
    try:
        descriptor = os.open(path, _directory_open_flags())
        pinned = _PinnedDirectory(path=path, descriptor=descriptor)
        pinned.assert_current()
        return pinned
    except EvaluationRunError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise EvaluationRunError(error_code) from None


def _create_pinned_directory(
    parent: _PinnedDirectory,
    name: str,
    *,
    error_code: EvaluationRunErrorCode,
) -> _PinnedDirectory:
    if Path(name).name != name:
        raise EvaluationRunError(error_code)
    parent.assert_current()
    descriptor = -1
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent.descriptor)
        descriptor = os.open(
            name,
            _directory_open_flags(),
            dir_fd=parent.descriptor,
        )
        pinned = _PinnedDirectory(
            path=parent.path / name,
            descriptor=descriptor,
        )
        os.fchmod(descriptor, 0o700)
        parent.assert_current()
        pinned.assert_current()
        return pinned
    except EvaluationRunError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise EvaluationRunError(error_code) from None


def _pin_child_directory(
    parent: _PinnedDirectory,
    name: str,
    *,
    error_code: EvaluationRunErrorCode,
) -> _PinnedDirectory:
    parent.assert_current()
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            _directory_open_flags(),
            dir_fd=parent.descriptor,
        )
        pinned = _PinnedDirectory(
            path=parent.path / name,
            descriptor=descriptor,
        )
        parent.assert_current()
        pinned.assert_current()
        return pinned
    except EvaluationRunError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise EvaluationRunError(error_code) from None


def _chmod_regular_child(
    parent: _PinnedDirectory,
    name: str,
    mode: int,
) -> None:
    parent.assert_current()
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=parent.descriptor)
        descriptor_stat = os.fstat(descriptor)
        path_stat = (parent.path / name).lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or _directory_identity(descriptor_stat) != _directory_identity(path_stat)
        ):
            raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_CHANGED)
        os.fchmod(descriptor, mode)
        parent.assert_current()
    except EvaluationRunError:
        raise
    except OSError:
        raise EvaluationRunError(EvaluationRunErrorCode.RUNTIME_UNAVAILABLE) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass
class _AccuracyAccumulator:
    verified_case_count: int = 0
    comparable: Counter[str] = field(default_factory=Counter)
    correct: Counter[str] = field(default_factory=Counter)

    def observe(
        self,
        truth: GroundTruthCase,
        result: ProcessResult | None,
        items: Sequence[StoredItem],
    ) -> None:
        if not truth.human_verified:
            return
        self.verified_case_count += 1

        expected_date = (
            truth.expected_date.isoformat() if truth.expected_date is not None else None
        )
        self._record(
            "store",
            result is not None and result.store == truth.expected_store,
        )
        self._record(
            "date",
            result is not None and result.date == expected_date,
        )
        self._record(
            "total",
            result is not None and result.total == truth.expected_total,
        )
        self._record(
            "receipt_status",
            result is not None
            and truth.expected_status is not None
            and result.status.value == truth.expected_status.value,
        )

        item_count = max(len(truth.items), len(items))
        for index in range(item_count):
            expected = truth.items[index] if index < len(truth.items) else None
            actual = items[index] if index < len(items) else None
            self._record(
                "item_name",
                expected is not None
                and actual is not None
                and actual.name == expected.expected_item_name,
            )
            self._record(
                "item_quantity",
                expected is not None
                and actual is not None
                and actual.qty == expected.expected_item_qty,
            )
            self._record(
                "item_price",
                expected is not None
                and actual is not None
                and actual.price == expected.expected_item_price,
            )

    def _record(self, field_name: str, matches: bool) -> None:
        self.comparable[field_name] += 1
        if matches:
            self.correct[field_name] += 1

    def summary(self) -> AccuracySummary:
        if self.verified_case_count == 0:
            return AccuracySummary(
                status=AccuracyStatus.UNKNOWN,
                reason=(AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH),
                verified_case_count=0,
            )
        return AccuracySummary(
            status=AccuracyStatus.MEASURED,
            reason=None,
            verified_case_count=self.verified_case_count,
            store=self._metric("store"),
            date=self._metric("date"),
            total=self._metric("total"),
            receipt_status=self._metric("receipt_status"),
            item_name=self._metric("item_name"),
            item_quantity=self._metric("item_quantity"),
            item_price=self._metric("item_price"),
        )

    def _metric(self, field_name: str) -> AccuracyMetric:
        comparable_count = self.comparable[field_name]
        correct_count = self.correct[field_name]
        return AccuracyMetric(
            comparable_count=comparable_count,
            correct_count=correct_count,
            accuracy_rate=(
                None if comparable_count == 0 else correct_count / comparable_count
            ),
        )


@dataclass(frozen=True, slots=True)
class _ModelEvaluationArtifacts:
    report: ModelEvaluationReport
    quality: QualityBaselineSummary


def run_evaluation(
    source_root: Path,
    *,
    output_root: Path,
    base_config: AppConfig,
    mode: IngestMode,
    reference_date: date,
    ground_truth_path: Path | None = None,
    models: Sequence[str] = DEFAULT_EVALUATION_MODELS,
    clock: Callable[[], float] = time.perf_counter,
    run_id_factory: Callable[[], str] | None = None,
) -> EvaluationReport:
    cases = discover_cases(source_root)
    model_names = _validate_models(models)
    truth = (
        None
        if ground_truth_path is None
        else load_ground_truth_csv(
            ground_truth_path,
            {case.case_id for case in cases},
        )
    )
    run_id = _default_run_id() if run_id_factory is None else run_id_factory()
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise EvaluationRunError(EvaluationRunErrorCode.RUN_ID_INVALID)
    output = _prepare_output_root(
        source_root,
        output_root,
        production_root=base_config.data.root,
    )
    with closing(output):
        run_root = _create_pinned_directory(
            output,
            run_id,
            error_code=EvaluationRunErrorCode.RUNTIME_UNAVAILABLE,
        )
        with closing(run_root):
            model_artifacts = tuple(
                _run_model(
                    cases,
                    model_name=model_name,
                    model_index=index,
                    run_root=run_root,
                    base_config=base_config,
                    mode=mode,
                    reference_date=reference_date,
                    truth=truth,
                    clock=clock,
                )
                for index, model_name in enumerate(model_names, start=1)
            )
            run_root.assert_current()
            report = EvaluationReport(
                run_id=run_id,
                models=tuple(artifacts.report for artifacts in model_artifacts),
            )
            quality_report = QualityBaselineReport(
                run_id=run_id,
                models=tuple(
                    QualityModelReport(
                        provenance=_quality_provenance(artifacts.report.model_name),
                        summary=artifacts.report.summary,
                        accuracy=artifacts.report.accuracy,
                        quality=artifacts.quality,
                    )
                    for artifacts in model_artifacts
                ),
            )
            _write_report(report, run_root)
            _write_quality_report(quality_report, run_root)
            run_root.assert_current()
            return report


def _run_model(
    cases: Sequence[EvaluationCase],
    *,
    model_name: str,
    model_index: int,
    run_root: _PinnedDirectory,
    base_config: AppConfig,
    mode: IngestMode,
    reference_date: date,
    truth: GroundTruthDataset | None,
    clock: Callable[[], float],
) -> _ModelEvaluationArtifacts:
    run_root.assert_current()
    model_root = _create_pinned_directory(
        run_root,
        f"model-{model_index:02d}",
        error_code=EvaluationRunErrorCode.RUNTIME_UNAVAILABLE,
    )
    with closing(model_root):
        data_root = model_root.path
        model_config = base_config.model_copy(
            update={
                "data": DataConfig(root=data_root),
                "ollama": base_config.ollama.model_copy(
                    update={"model": model_name},
                ),
            }
        )
        try:
            model_root.assert_current()
            paths, _ = initialize_runtime(data_root)
            model_root.assert_current()
            os.fchmod(model_root.descriptor, 0o700)
            for directory_name in RUNTIME_DIRECTORY_NAMES:
                with closing(
                    _pin_child_directory(
                        model_root,
                        directory_name,
                        error_code=EvaluationRunErrorCode.RUNTIME_UNAVAILABLE,
                    )
                ) as runtime_directory:
                    os.fchmod(runtime_directory.descriptor, 0o700)
                    runtime_directory.assert_current()
            _chmod_regular_child(model_root, "ledger.db", 0o600)
            _chmod_regular_child(
                model_root,
                paths.lock_file.name,
                0o600,
            )
            inputs = _create_pinned_directory(
                model_root,
                "evaluation-inputs",
                error_code=EvaluationRunErrorCode.RUNTIME_UNAVAILABLE,
            )
        except (
            MigrationError,
            OSError,
            RuntimeLayoutError,
            StorageError,
            sqlite3.Error,
        ):
            raise EvaluationRunError(
                EvaluationRunErrorCode.RUNTIME_UNAVAILABLE
            ) from None

        with closing(inputs):
            case_results: list[CaseEvaluationResult] = []
            accuracy = _AccuracyAccumulator()
            quality = _QualityAccumulator()
            for case in cases:
                start = clock()
                process_result: ProcessResult | None = None
                stored_items: Sequence[StoredItem] = ()
                try:
                    run_root.assert_current()
                    model_root.assert_current()
                    inputs.assert_current()
                    image_path = _copy_case(case, inputs)
                    model_root.assert_current()
                    inputs.assert_current()
                    process_result, audit = process_receipt_with_audit(
                        image_path,
                        config=model_config,
                        mode=mode,
                        reference_date=reference_date,
                        storage_image_path=image_path.relative_to(data_root),
                        temporary_root=paths.tmp,
                    )
                    run_root.assert_current()
                    model_root.assert_current()
                    inputs.assert_current()
                    elapsed_ms = max(0.0, (clock() - start) * 1000)
                    case_result = CaseEvaluationResult(
                        case_id=case.case_id,
                        processing_success=True,
                        schema_outcome=SchemaOutcome(audit.schema_outcome.value),
                        date_outcome=DateOutcome(
                            audit.date_normalization_outcome.value
                        ),
                        tax_outcome=(
                            TaxOutcome.NOT_EVALUATED
                            if audit.tax_normalization_reason is None
                            else TaxOutcome(audit.tax_normalization_reason.value)
                        ),
                        duplicate_outcome=DuplicateOutcome(
                            audit.duplicate_outcome.value
                        ),
                        status=EvaluationStatus(process_result.status.value),
                        elapsed_ms=elapsed_ms,
                        error_code=None,
                        validation_issue_codes=tuple(
                            issue.code for issue in process_result.validation_issues
                        ),
                    )
                    if (
                        truth is not None
                        and (truth_case := truth.get(case.case_id)) is not None
                        and truth_case.human_verified
                    ):
                        stored_items = _stored_items(
                            data_root,
                            process_result.receipt_id,
                        )
                        model_root.assert_current()
                except (
                    ImagePathError,
                    ImagePreprocessError,
                    MigrationError,
                    OSError,
                    OllamaError,
                    StorageError,
                    sqlite3.Error,
                ) as error:
                    elapsed_ms = max(0.0, (clock() - start) * 1000)
                    description = describe_error(error)
                    case_result = CaseEvaluationResult(
                        case_id=case.case_id,
                        processing_success=False,
                        schema_outcome=SchemaOutcome.NOT_EVALUATED,
                        date_outcome=DateOutcome.NOT_EVALUATED,
                        tax_outcome=TaxOutcome.NOT_EVALUATED,
                        duplicate_outcome=DuplicateOutcome.NOT_EVALUATED,
                        status=EvaluationStatus.FAILED,
                        elapsed_ms=elapsed_ms,
                        error_code=description.code,
                        validation_issue_codes=(),
                    )
                    process_result = None
                    stored_items = ()
                case_results.append(case_result)
                if (
                    truth is not None
                    and (truth_case := truth.get(case.case_id)) is not None
                ):
                    accuracy.observe(
                        truth_case,
                        process_result,
                        stored_items,
                    )
                    quality.observe(
                        truth_case,
                        process_result,
                        stored_items,
                    )

            model_root.assert_current()
            inputs.assert_current()
            _chmod_regular_child(model_root, "ledger.db", 0o600)
            summary = _summarize(case_results)
            return _ModelEvaluationArtifacts(
                report=ModelEvaluationReport(
                    model_name=model_name,
                    cases=tuple(case_results),
                    summary=summary,
                    accuracy=accuracy.summary(),
                ),
                quality=_quality_baseline(quality.counts, summary),
            )


def _stored_items(data_root: Path, receipt_id: int) -> Sequence[StoredItem]:
    try:
        with closing(connect_database(data_root)) as connection:
            stored = ReceiptRepository(connection).get(receipt_id)
    except (OSError, StorageError, ValueError, sqlite3.Error):
        raise EvaluationRunError(EvaluationRunErrorCode.STORED_RESULT_MISSING) from None
    if stored is None:
        raise EvaluationRunError(EvaluationRunErrorCode.STORED_RESULT_MISSING)
    return stored.items


def _quality_provenance(model_name: str) -> QualityProvenance:
    schema_json = json.dumps(
        ReceiptExtraction.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return QualityProvenance(
        model_name=model_name,
        prompt_sha256=hashlib.sha256(EXTRACTION_PROMPT.encode("utf-8")).hexdigest(),
        extraction_schema_sha256=hashlib.sha256(
            schema_json.encode("utf-8")
        ).hexdigest(),
    )


def _quality_rate(numerator: int, denominator: int) -> QualityRateMetric:
    return QualityRateMetric(
        denominator_count=denominator,
        numerator_count=numerator,
        rate=None if denominator == 0 else numerator / denominator,
    )


def _unknown_quality_assessment(
    reason: QualityUnknownReason,
) -> QualityAssessment:
    return QualityAssessment(
        status=QualityAssessmentStatus.UNKNOWN,
        reason=reason,
    )


def _minimum_quality_assessment(
    metric: QualityRateMetric,
    threshold: float,
) -> QualityAssessment:
    if metric.rate is None:
        return _unknown_quality_assessment(QualityUnknownReason.ZERO_DENOMINATOR)
    return QualityAssessment(
        status=(
            QualityAssessmentStatus.MET
            if metric.rate >= threshold
            else QualityAssessmentStatus.NOT_MET
        )
    )


def _maximum_quality_assessment(
    metric: QualityRateMetric,
    threshold: float,
) -> QualityAssessment:
    if metric.rate is None:
        return _unknown_quality_assessment(QualityUnknownReason.ZERO_DENOMINATOR)
    return QualityAssessment(
        status=(
            QualityAssessmentStatus.MET
            if metric.rate <= threshold
            else QualityAssessmentStatus.NOT_MET
        )
    )


def _quality_baseline(
    counts: _QualityCounts,
    summary: ModelEvaluationSummary,
) -> QualityBaselineSummary:
    total_accuracy = _quality_rate(
        counts.total_correct_count,
        counts.verified_case_count,
    )
    store_accuracy = _quality_rate(
        counts.store_correct_count,
        counts.verified_case_count,
    )
    date_accuracy = _quality_rate(
        counts.date_correct_count,
        counts.verified_case_count,
    )
    item_accuracy = _quality_rate(
        counts.item_correct_count,
        counts.item_comparable_count,
    )
    false_confirmation_rate = _quality_rate(
        counts.false_confirmed_count,
        counts.confirmed_count,
    )
    review_rate = _quality_rate(
        summary.status_counts.get(EvaluationStatus.REVIEW, 0),
        summary.case_count,
    )
    thresholds = QualityThresholds()
    golden_set_complete = (
        summary.case_count == REQUIRED_VERIFIED_CASE_COUNT
        and counts.verified_case_count == REQUIRED_VERIFIED_CASE_COUNT
    )
    if not golden_set_complete:
        incomplete = _unknown_quality_assessment(
            QualityUnknownReason.INCOMPLETE_GOLDEN_SET
        )
        assessments = (incomplete,) * 5
    else:
        q2_store = _minimum_quality_assessment(
            store_accuracy,
            thresholds.q2_store_minimum,
        )
        q2_date = _minimum_quality_assessment(
            date_accuracy,
            thresholds.q2_date_minimum,
        )
        if (
            q2_store.status is QualityAssessmentStatus.UNKNOWN
            or q2_date.status is QualityAssessmentStatus.UNKNOWN
        ):
            q2 = _unknown_quality_assessment(QualityUnknownReason.ZERO_DENOMINATOR)
        else:
            q2 = QualityAssessment(
                status=(
                    QualityAssessmentStatus.MET
                    if q2_store.status is QualityAssessmentStatus.MET
                    and q2_date.status is QualityAssessmentStatus.MET
                    else QualityAssessmentStatus.NOT_MET
                )
            )
        assessments = (
            _minimum_quality_assessment(
                total_accuracy,
                thresholds.q1_total_minimum,
            ),
            q2,
            _minimum_quality_assessment(
                item_accuracy,
                thresholds.q3_items_minimum,
            ),
            _maximum_quality_assessment(
                false_confirmation_rate,
                thresholds.q4_false_confirmation_maximum,
            ),
            _maximum_quality_assessment(
                review_rate,
                thresholds.q5_review_maximum,
            ),
        )
    return QualityBaselineSummary(
        target_case_count=summary.case_count,
        verified_case_count=counts.verified_case_count,
        golden_set_complete=golden_set_complete,
        total_accuracy=total_accuracy,
        store_accuracy=store_accuracy,
        date_accuracy=date_accuracy,
        item_accuracy=item_accuracy,
        false_confirmation_rate=false_confirmation_rate,
        review_rate=review_rate,
        thresholds=thresholds,
        q1_total=assessments[0],
        q2_store_and_date=assessments[1],
        q3_items=assessments[2],
        q4_false_confirmation=assessments[3],
        q5_review=assessments[4],
    )


def _summarize(
    cases: Sequence[CaseEvaluationResult],
) -> ModelEvaluationSummary:
    case_count = len(cases)
    processing_success_count = sum(case.processing_success for case in cases)
    schema_success_count = sum(
        case.schema_outcome is SchemaOutcome.VALID for case in cases
    )
    status_counts = Counter(case.status for case in cases)
    tax_outcome_counts = Counter(case.tax_outcome for case in cases)
    elapsed_values = [case.elapsed_ms for case in cases]
    total_ms = sum(elapsed_values)
    return ModelEvaluationSummary(
        case_count=case_count,
        processing_success_count=processing_success_count,
        processing_success_rate=_rate(processing_success_count, case_count),
        schema_success_count=schema_success_count,
        schema_success_rate=_rate(schema_success_count, case_count),
        confirmed_rate=_rate(
            status_counts[EvaluationStatus.CONFIRMED],
            case_count,
        ),
        review_rate=_rate(
            status_counts[EvaluationStatus.REVIEW],
            case_count,
        ),
        failed_rate=_rate(
            status_counts[EvaluationStatus.FAILED],
            case_count,
        ),
        tax_applied_count=tax_outcome_counts[TaxOutcome.APPLIED],
        tax_rejected_count=sum(
            count
            for outcome, count in tax_outcome_counts.items()
            if outcome
            not in {
                TaxOutcome.APPLIED,
                TaxOutcome.NOT_NEEDED,
                TaxOutcome.NOT_EVALUATED,
            }
        ),
        status_counts=dict(status_counts),
        schema_outcome_counts=dict(Counter(case.schema_outcome for case in cases)),
        date_outcome_counts=dict(Counter(case.date_outcome for case in cases)),
        tax_outcome_counts=dict(tax_outcome_counts),
        duplicate_outcome_counts=dict(
            Counter(case.duplicate_outcome for case in cases)
        ),
        error_code_counts=dict(
            Counter(case.error_code for case in cases if case.error_code is not None)
        ),
        validation_issue_code_counts=dict(
            Counter(
                issue_code
                for case in cases
                for issue_code in case.validation_issue_codes
            )
        ),
        duration=DurationSummary(
            sample_count=case_count,
            total_ms=total_ms,
            minimum_ms=min(elapsed_values) if elapsed_values else None,
            maximum_ms=max(elapsed_values) if elapsed_values else None,
            mean_ms=total_ms / case_count if case_count else None,
        ),
    )


def _rate(count: int, total: int) -> float | None:
    return None if total == 0 else count / total


def _copy_case(
    case: EvaluationCase,
    destination_root: Path | _PinnedDirectory,
) -> Path:
    if isinstance(destination_root, _PinnedDirectory):
        return _copy_case_to_pinned_directory(case, destination_root)
    with closing(
        _pin_existing_directory(
            destination_root,
            error_code=EvaluationRunErrorCode.COPY_FAILED,
        )
    ) as pinned_destination:
        return _copy_case_to_pinned_directory(case, pinned_destination)


def _copy_case_to_pinned_directory(
    case: EvaluationCase,
    destination_root: _PinnedDirectory,
) -> Path:
    destination_name = f"{case.case_id}{case.source_path.suffix.lower()}"
    destination = destination_root.path / destination_name
    expected_identity = (
        case.st_dev,
        case.st_ino,
        case.st_size,
        case.st_mtime_ns,
        case.st_ctime_ns,
    )
    source_descriptor = -1
    destination_descriptor = -1
    destination_created = False
    completed = False
    try:
        destination_root.assert_current()
        source_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            source_flags |= os.O_NOFOLLOW
        source_descriptor = os.open(case.source_path, source_flags)
        source_stat = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or _stat_identity(source_stat) != expected_identity
        ):
            raise EvaluationRunError(EvaluationRunErrorCode.SOURCE_CHANGED)

        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            destination_flags |= os.O_NOFOLLOW
        destination_descriptor = os.open(
            destination_name,
            destination_flags,
            0o600,
            dir_fd=destination_root.descriptor,
        )
        destination_created = True
        with (
            os.fdopen(source_descriptor, "rb", closefd=True) as source_stream,
            os.fdopen(
                destination_descriptor,
                "wb",
                closefd=True,
            ) as destination_stream,
        ):
            source_descriptor = -1
            destination_descriptor = -1
            shutil.copyfileobj(source_stream, destination_stream)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
            os.fchmod(destination_stream.fileno(), 0o600)
            if _stat_identity(os.fstat(source_stream.fileno())) != expected_identity:
                raise EvaluationRunError(EvaluationRunErrorCode.SOURCE_CHANGED)

        after_stat = case.source_path.lstat()
        if _stat_identity(after_stat) != expected_identity:
            raise EvaluationRunError(EvaluationRunErrorCode.SOURCE_CHANGED)
        destination_root.assert_current()
        completed = True
        return destination
    except EvaluationRunError:
        raise
    except OSError:
        raise EvaluationRunError(EvaluationRunErrorCode.COPY_FAILED) from None
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if destination_created and not completed:
            try:
                os.unlink(
                    destination_name,
                    dir_fd=destination_root.descriptor,
                )
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _validate_models(models: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(models)
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(_MODEL_NAME_PATTERN.fullmatch(model) is None for model in selected)
    ):
        raise EvaluationRunError(EvaluationRunErrorCode.MODEL_INVALID)
    return selected


def _prepare_output_root(
    source_root: Path,
    output_root: Path,
    *,
    production_root: Path,
) -> _PinnedDirectory:
    output = Path(output_root)
    if not output.is_absolute():
        raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_PATH_INVALID)
    _reject_symlink_components(output)
    try:
        source = Path(source_root).resolve(strict=True)
        resolved_output = output.resolve(strict=False)
        production = Path(production_root).resolve(strict=False)
    except (OSError, RuntimeError):
        raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_PATH_INVALID) from None
    if (
        _paths_overlap(source, resolved_output)
        or _paths_overlap(production, resolved_output)
        or _paths_overlap(source, production)
    ):
        raise EvaluationRunError(EvaluationRunErrorCode.ROOTS_OVERLAP)
    if _has_git_marker_in_ancestors(resolved_output):
        raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_IN_GIT)

    try:
        output_existed = output.exists()
        if output_existed:
            if not output.is_dir():
                raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_PATH_INVALID)
        else:
            output.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError:
        raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_CHANGED) from None
    except EvaluationRunError:
        raise
    except OSError:
        raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_PATH_INVALID) from None
    _reject_symlink_components(output)
    pinned = _pin_existing_directory(
        output,
        error_code=EvaluationRunErrorCode.OUTPUT_PATH_INVALID,
    )
    try:
        if not output_existed:
            os.fchmod(pinned.descriptor, 0o700)
        pinned.assert_current()
        final_output = output.resolve(strict=True)
        if (
            final_output != resolved_output
            or _paths_overlap(source, final_output)
            or _paths_overlap(production, final_output)
            or _has_git_marker_in_ancestors(final_output)
        ):
            raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_CHANGED)
    except EvaluationRunError:
        pinned.close()
        raise
    except (OSError, RuntimeError):
        pinned.close()
        raise EvaluationRunError(EvaluationRunErrorCode.OUTPUT_PATH_INVALID) from None
    return pinned


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _reject_symlink_components(path: Path) -> None:
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor /= component
        try:
            component_stat = cursor.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise EvaluationRunError(
                EvaluationRunErrorCode.OUTPUT_PATH_INVALID
            ) from None
        if stat.S_ISLNK(component_stat.st_mode):
            raise EvaluationRunError(EvaluationRunErrorCode.SYMLINK_REJECTED)


def _has_git_marker_in_ancestors(path: Path) -> bool:
    for candidate in (path, *path.parents):
        try:
            (candidate / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise EvaluationRunError(
                EvaluationRunErrorCode.OUTPUT_PATH_INVALID
            ) from None
        return True
    return False


def _default_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    return f"run-{timestamp}-{uuid.uuid4().hex[:12]}"


def _write_report(
    report: EvaluationReport,
    run_root: _PinnedDirectory,
) -> None:
    _write_json_report(
        report,
        run_root,
        filename=_REPORT_FILENAME,
        temporary_stem="evaluation-report",
    )


def _write_quality_report(
    report: QualityBaselineReport,
    run_root: _PinnedDirectory,
) -> None:
    _write_json_report(
        report,
        run_root,
        filename=_QUALITY_REPORT_FILENAME,
        temporary_stem="quality-baseline-report",
    )


def _write_json_report(
    report: EvaluationReport | QualityBaselineReport,
    run_root: _PinnedDirectory,
    *,
    filename: str,
    temporary_stem: str,
) -> None:
    temporary_name = f".{temporary_stem}-{uuid.uuid4().hex}.tmp"
    descriptor = -1
    temporary_created = False
    linked = False
    try:
        run_root.assert_current()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=run_root.descriptor,
        )
        temporary_created = True
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            descriptor = -1
            json.dump(
                report.model_dump(mode="json"),
                stream,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o600)
        os.link(
            temporary_name,
            filename,
            src_dir_fd=run_root.descriptor,
            dst_dir_fd=run_root.descriptor,
            follow_symlinks=False,
        )
        linked = True
        run_root.assert_current()
    except EvaluationRunError:
        raise
    except OSError:
        raise EvaluationRunError(EvaluationRunErrorCode.REPORT_WRITE_FAILED) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_created:
            try:
                os.unlink(
                    temporary_name,
                    dir_fd=run_root.descriptor,
                )
            except FileNotFoundError:
                pass
            except OSError:
                if not linked:
                    pass
