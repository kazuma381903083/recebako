from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

CaseId = Annotated[
    str,
    StringConstraints(pattern=r"^case-[0-9]{4,}$"),
]
ModelName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$",
        max_length=128,
    ),
]
RunId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$",
        max_length=128,
    ),
]
SafeCode = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$",
        max_length=128,
    ),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64),
]


class SchemaOutcome(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    VALID = "valid"
    INVALID = "invalid"


class DateOutcome(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    UNCHANGED = "unchanged"
    NORMALIZED = "normalized"
    REJECTED = "rejected"


class TaxOutcome(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    APPLIED = "applied"
    NOT_NEEDED = "not_needed"
    MISSING_EVIDENCE = "missing_evidence"
    INCONSISTENT_INPUT = "inconsistent_input"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    TOTAL_MISMATCH = "total_mismatch"
    SEARCH_LIMIT = "search_limit"
    GROUP_LIMIT = "group_limit"
    ALLOCATION_FAILED = "allocation_failed"


class DuplicateOutcome(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    NONE = "none"
    IDENTITY = "identity"
    PHASH = "phash"


class EvaluationStatus(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    CONFIRMED = "confirmed"
    REVIEW = "review"
    FAILED = "failed"


class AccuracyStatus(str, Enum):
    UNKNOWN = "unknown"
    MEASURED = "measured"


class AccuracyUnknownReason(str, Enum):
    NO_HUMAN_VERIFIED_GROUND_TRUTH = "no_human_verified_ground_truth"


class QualityAssessmentStatus(str, Enum):
    UNKNOWN = "unknown"
    MET = "met"
    NOT_MET = "not_met"


class QualityUnknownReason(str, Enum):
    INCOMPLETE_GOLDEN_SET = "incomplete_golden_set"
    ZERO_DENOMINATOR = "zero_denominator"


class _ReportModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class CaseEvaluationResult(_ReportModel):
    case_id: CaseId
    processing_success: bool
    schema_outcome: SchemaOutcome
    date_outcome: DateOutcome
    tax_outcome: TaxOutcome
    duplicate_outcome: DuplicateOutcome
    status: EvaluationStatus
    elapsed_ms: float = Field(ge=0)
    error_code: SafeCode | None = None
    validation_issue_codes: tuple[SafeCode, ...] = ()


class DurationSummary(_ReportModel):
    sample_count: int = Field(ge=0)
    total_ms: float = Field(ge=0)
    minimum_ms: float | None = Field(default=None, ge=0)
    maximum_ms: float | None = Field(default=None, ge=0)
    mean_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_duration_shape(self) -> DurationSummary:
        aggregates = (self.minimum_ms, self.maximum_ms, self.mean_ms)
        if self.sample_count == 0:
            if self.total_ms != 0 or any(value is not None for value in aggregates):
                raise ValueError(
                    "empty duration summaries must not contain measurements"
                )
            return self
        if any(value is None for value in aggregates):
            raise ValueError("non-empty duration summaries require all aggregates")
        if (
            self.minimum_ms is not None
            and self.maximum_ms is not None
            and self.mean_ms is not None
            and not self.minimum_ms <= self.mean_ms <= self.maximum_ms
        ):
            raise ValueError("duration aggregates are inconsistent")
        return self


class AccuracyMetric(_ReportModel):
    comparable_count: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    accuracy_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_counts_and_rate(self) -> AccuracyMetric:
        if self.correct_count > self.comparable_count:
            raise ValueError("correct_count cannot exceed comparable_count")
        if self.comparable_count == 0:
            if self.correct_count != 0 or self.accuracy_rate is not None:
                raise ValueError("empty accuracy metrics cannot have a rate")
            return self
        if self.accuracy_rate is None:
            raise ValueError("measured accuracy metrics require a rate")
        expected_rate = self.correct_count / self.comparable_count
        if abs(self.accuracy_rate - expected_rate) > 1e-12:
            raise ValueError("accuracy rate is inconsistent with its counts")
        return self


class QualityRateMetric(_ReportModel):
    denominator_count: int = Field(ge=0)
    numerator_count: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_counts_and_rate(self) -> QualityRateMetric:
        if self.numerator_count > self.denominator_count:
            raise ValueError("numerator_count cannot exceed denominator_count")
        if self.denominator_count == 0:
            if self.numerator_count != 0 or self.rate is not None:
                raise ValueError("zero-denominator metrics cannot have a rate")
            return self
        if self.rate is None:
            raise ValueError("non-empty quality metrics require a rate")
        expected_rate = self.numerator_count / self.denominator_count
        if abs(self.rate - expected_rate) > 1e-12:
            raise ValueError("quality rate is inconsistent with its counts")
        return self


class QualityAssessment(_ReportModel):
    status: QualityAssessmentStatus
    reason: QualityUnknownReason | None = None

    @model_validator(mode="after")
    def _validate_status_and_reason(self) -> QualityAssessment:
        if self.status is QualityAssessmentStatus.UNKNOWN:
            if self.reason is None:
                raise ValueError("unknown quality assessment requires a reason")
        elif self.reason is not None:
            raise ValueError("measured quality assessment cannot have a reason")
        return self


class QualityThresholds(_ReportModel):
    q1_total_minimum: float = Field(default=0.98, ge=0, le=1)
    q2_store_minimum: float = Field(default=0.95, ge=0, le=1)
    q2_date_minimum: float = Field(default=0.95, ge=0, le=1)
    q3_items_minimum: float = Field(default=0.80, ge=0, le=1)
    q4_false_confirmation_maximum: float = Field(default=0.02, ge=0, le=1)
    q5_review_maximum: float = Field(default=0.30, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_fixed_quality_v1_thresholds(self) -> QualityThresholds:
        if self.model_dump() != {
            "q1_total_minimum": 0.98,
            "q2_store_minimum": 0.95,
            "q2_date_minimum": 0.95,
            "q3_items_minimum": 0.80,
            "q4_false_confirmation_maximum": 0.02,
            "q5_review_maximum": 0.30,
        }:
            raise ValueError("quality-v1 thresholds are fixed")
        return self


class QualityBaselineSummary(_ReportModel):
    metric_version: Literal["quality-v1"] = "quality-v1"
    required_verified_case_count: Literal[30] = 30
    target_case_count: int = Field(ge=0)
    verified_case_count: int = Field(ge=0)
    golden_set_complete: bool
    total_accuracy: QualityRateMetric
    store_accuracy: QualityRateMetric
    date_accuracy: QualityRateMetric
    item_accuracy: QualityRateMetric
    false_confirmation_rate: QualityRateMetric
    review_rate: QualityRateMetric
    thresholds: QualityThresholds = Field(default_factory=QualityThresholds)
    q1_total: QualityAssessment
    q2_store_and_date: QualityAssessment
    q3_items: QualityAssessment
    q4_false_confirmation: QualityAssessment
    q5_review: QualityAssessment

    @model_validator(mode="after")
    def _validate_quality_baseline(self) -> QualityBaselineSummary:
        if self.verified_case_count > self.target_case_count:
            raise ValueError("verified cases cannot exceed target cases")
        expected_complete = (
            self.target_case_count == self.required_verified_case_count
            and self.verified_case_count == self.required_verified_case_count
        )
        if self.golden_set_complete is not expected_complete:
            raise ValueError("golden_set_complete is inconsistent with case counts")
        case_metrics = (
            self.total_accuracy,
            self.store_accuracy,
            self.date_accuracy,
        )
        if any(
            metric.denominator_count != self.verified_case_count
            for metric in case_metrics
        ):
            raise ValueError("case accuracy denominators must equal verified cases")
        if self.false_confirmation_rate.denominator_count > self.verified_case_count:
            raise ValueError("confirmed denominator cannot exceed verified cases")
        confirmed_count = self.false_confirmation_rate.denominator_count
        false_confirmed_count = self.false_confirmation_rate.numerator_count
        total_correct_count = self.total_accuracy.numerator_count
        minimum_false_confirmed = max(0, confirmed_count - total_correct_count)
        maximum_false_confirmed = min(
            confirmed_count,
            self.verified_case_count - total_correct_count,
        )
        if not (
            minimum_false_confirmed <= false_confirmed_count <= maximum_false_confirmed
        ):
            raise ValueError(
                "false confirmations are inconsistent with confirmed and total counts"
            )
        if self.review_rate.denominator_count != self.target_case_count:
            raise ValueError("review denominator must equal target cases")
        assessments = (
            self.q1_total,
            self.q2_store_and_date,
            self.q3_items,
            self.q4_false_confirmation,
            self.q5_review,
        )
        if not self.golden_set_complete:
            if any(
                assessment.status is not QualityAssessmentStatus.UNKNOWN
                or assessment.reason is not QualityUnknownReason.INCOMPLETE_GOLDEN_SET
                for assessment in assessments
            ):
                raise ValueError(
                    "incomplete golden sets require unknown quality assessments"
                )
            return self

        expected_assessments = (
            _minimum_assessment(
                self.total_accuracy,
                self.thresholds.q1_total_minimum,
            ),
            _combined_minimum_assessment(
                (self.store_accuracy, self.date_accuracy),
                (
                    self.thresholds.q2_store_minimum,
                    self.thresholds.q2_date_minimum,
                ),
            ),
            _minimum_assessment(
                self.item_accuracy,
                self.thresholds.q3_items_minimum,
            ),
            _maximum_assessment(
                self.false_confirmation_rate,
                self.thresholds.q4_false_confirmation_maximum,
            ),
            _maximum_assessment(
                self.review_rate,
                self.thresholds.q5_review_maximum,
            ),
        )
        if any(
            actual != expected
            for actual, expected in zip(
                assessments,
                expected_assessments,
                strict=True,
            )
        ):
            raise ValueError("quality assessments are inconsistent with quality-v1")
        return self


def _minimum_assessment(
    metric: QualityRateMetric,
    threshold: float,
) -> QualityAssessment:
    if metric.rate is None:
        return QualityAssessment(
            status=QualityAssessmentStatus.UNKNOWN,
            reason=QualityUnknownReason.ZERO_DENOMINATOR,
        )
    return QualityAssessment(
        status=(
            QualityAssessmentStatus.MET
            if metric.rate >= threshold
            else QualityAssessmentStatus.NOT_MET
        )
    )


def _maximum_assessment(
    metric: QualityRateMetric,
    threshold: float,
) -> QualityAssessment:
    if metric.rate is None:
        return QualityAssessment(
            status=QualityAssessmentStatus.UNKNOWN,
            reason=QualityUnknownReason.ZERO_DENOMINATOR,
        )
    return QualityAssessment(
        status=(
            QualityAssessmentStatus.MET
            if metric.rate <= threshold
            else QualityAssessmentStatus.NOT_MET
        )
    )


def _combined_minimum_assessment(
    metrics: tuple[QualityRateMetric, ...],
    thresholds: tuple[float, ...],
) -> QualityAssessment:
    if any(metric.rate is None for metric in metrics):
        return QualityAssessment(
            status=QualityAssessmentStatus.UNKNOWN,
            reason=QualityUnknownReason.ZERO_DENOMINATOR,
        )
    return QualityAssessment(
        status=(
            QualityAssessmentStatus.MET
            if all(
                metric.rate is not None and metric.rate >= threshold
                for metric, threshold in zip(metrics, thresholds, strict=True)
            )
            else QualityAssessmentStatus.NOT_MET
        )
    )


def _empty_accuracy_metric() -> AccuracyMetric:
    return AccuracyMetric(
        comparable_count=0,
        correct_count=0,
        accuracy_rate=None,
    )


class AccuracySummary(_ReportModel):
    status: AccuracyStatus
    reason: AccuracyUnknownReason | None
    verified_case_count: int = Field(ge=0)
    store: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)
    date: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)
    total: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)
    receipt_status: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)
    item_name: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)
    item_quantity: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)
    item_price: AccuracyMetric = Field(default_factory=_empty_accuracy_metric)

    @model_validator(mode="after")
    def _validate_status(self) -> AccuracySummary:
        metrics = (
            self.store,
            self.date,
            self.total,
            self.receipt_status,
            self.item_name,
            self.item_quantity,
            self.item_price,
        )
        if self.status is AccuracyStatus.UNKNOWN:
            if self.reason is not AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH:
                raise ValueError("unknown accuracy requires its fixed safe reason")
            if self.verified_case_count != 0 or any(
                metric.comparable_count != 0 for metric in metrics
            ):
                raise ValueError("unknown accuracy cannot contain comparisons")
        else:
            if self.reason is not None:
                raise ValueError("measured accuracy cannot contain an unknown reason")
            if self.verified_case_count == 0:
                raise ValueError("measured accuracy requires verified cases")
        return self


class ModelEvaluationSummary(_ReportModel):
    case_count: int = Field(ge=0)
    processing_success_count: int = Field(ge=0)
    processing_success_rate: float | None = Field(default=None, ge=0, le=1)
    schema_success_count: int = Field(ge=0)
    schema_success_rate: float | None = Field(default=None, ge=0, le=1)
    confirmed_rate: float | None = Field(default=None, ge=0, le=1)
    review_rate: float | None = Field(default=None, ge=0, le=1)
    failed_rate: float | None = Field(default=None, ge=0, le=1)
    tax_applied_count: int = Field(ge=0)
    tax_rejected_count: int = Field(ge=0)
    status_counts: dict[EvaluationStatus, int]
    schema_outcome_counts: dict[SchemaOutcome, int]
    date_outcome_counts: dict[DateOutcome, int]
    tax_outcome_counts: dict[TaxOutcome, int]
    duplicate_outcome_counts: dict[DuplicateOutcome, int]
    error_code_counts: dict[SafeCode, int]
    validation_issue_code_counts: dict[SafeCode, int]
    duration: DurationSummary

    @model_validator(mode="after")
    def _validate_summary(self) -> ModelEvaluationSummary:
        if (
            self.processing_success_count > self.case_count
            or self.schema_success_count > self.case_count
        ):
            raise ValueError("summary counts cannot exceed case_count")
        expected_processing_rate = (
            None
            if self.case_count == 0
            else self.processing_success_count / self.case_count
        )
        expected_schema_rate = (
            None
            if self.case_count == 0
            else self.schema_success_count / self.case_count
        )
        if not _rates_match(self.processing_success_rate, expected_processing_rate):
            raise ValueError("processing success rate is inconsistent with its counts")
        if not _rates_match(self.schema_success_rate, expected_schema_rate):
            raise ValueError("schema success rate is inconsistent with its counts")
        expected_status_rates = (
            (
                self.confirmed_rate,
                self.status_counts.get(EvaluationStatus.CONFIRMED, 0),
            ),
            (
                self.review_rate,
                self.status_counts.get(EvaluationStatus.REVIEW, 0),
            ),
            (
                self.failed_rate,
                self.status_counts.get(EvaluationStatus.FAILED, 0),
            ),
        )
        for actual_rate, count in expected_status_rates:
            expected_rate = None if self.case_count == 0 else count / self.case_count
            if not _rates_match(actual_rate, expected_rate):
                raise ValueError("status rate is inconsistent with its count")
        distributions = (
            self.status_counts,
            self.schema_outcome_counts,
            self.date_outcome_counts,
            self.tax_outcome_counts,
            self.duplicate_outcome_counts,
        )
        if any(sum(counts.values()) != self.case_count for counts in distributions):
            raise ValueError("outcome distributions must cover every case")
        if self.duration.sample_count != self.case_count:
            raise ValueError("duration sample count must equal case_count")
        if self.tax_applied_count != self.tax_outcome_counts.get(
            TaxOutcome.APPLIED,
            0,
        ):
            raise ValueError("tax applied count is inconsistent with its distribution")
        non_rejected_tax_outcomes = {
            TaxOutcome.APPLIED,
            TaxOutcome.NOT_NEEDED,
            TaxOutcome.NOT_EVALUATED,
        }
        expected_tax_rejected_count = sum(
            count
            for outcome, count in self.tax_outcome_counts.items()
            if outcome not in non_rejected_tax_outcomes
        )
        if self.tax_rejected_count != expected_tax_rejected_count:
            raise ValueError("tax rejected count is inconsistent with its distribution")
        if any(
            count < 0
            for counts in (
                *distributions,
                self.error_code_counts,
                self.validation_issue_code_counts,
            )
            for count in counts.values()
        ):
            raise ValueError("distribution counts cannot be negative")
        return self


def _rates_match(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= 1e-12


class ModelEvaluationReport(_ReportModel):
    model_name: ModelName
    cases: tuple[CaseEvaluationResult, ...]
    summary: ModelEvaluationSummary
    accuracy: AccuracySummary

    @model_validator(mode="after")
    def _validate_case_count(self) -> ModelEvaluationReport:
        if len(self.cases) != self.summary.case_count:
            raise ValueError("report cases must match summary case_count")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("report case identifiers must be unique")
        return self


class EvaluationReport(_ReportModel):
    schema_version: Literal[1] = 1
    run_id: RunId
    models: tuple[ModelEvaluationReport, ...]

    @model_validator(mode="after")
    def _validate_model_names(self) -> EvaluationReport:
        if len({report.model_name for report in self.models}) != len(self.models):
            raise ValueError("report model names must be unique")
        return self


class QualityProvenance(_ReportModel):
    metric_version: Literal["quality-v1"] = "quality-v1"
    model_name: ModelName
    prompt_sha256: Sha256Digest
    extraction_schema_sha256: Sha256Digest


class QualityModelReport(_ReportModel):
    provenance: QualityProvenance
    summary: ModelEvaluationSummary
    accuracy: AccuracySummary
    quality: QualityBaselineSummary

    @model_validator(mode="after")
    def _validate_aggregate_consistency(self) -> QualityModelReport:
        if self.quality.target_case_count != self.summary.case_count:
            raise ValueError("quality target count must match summary case count")
        if self.quality.verified_case_count != self.accuracy.verified_case_count:
            raise ValueError("quality and accuracy verified counts must match")

        exact_quality_metrics = (
            (self.quality.total_accuracy, self.accuracy.total),
            (self.quality.date_accuracy, self.accuracy.date),
        )
        if any(
            quality_metric.denominator_count != accuracy_metric.comparable_count
            or quality_metric.numerator_count != accuracy_metric.correct_count
            for quality_metric, accuracy_metric in exact_quality_metrics
        ):
            raise ValueError("quality exact metrics must match existing accuracy")
        if (
            self.quality.store_accuracy.denominator_count
            != self.accuracy.store.comparable_count
            or self.quality.store_accuracy.numerator_count
            < self.accuracy.store.correct_count
        ):
            raise ValueError("normalized store accuracy is inconsistent")

        expected_review_count = self.summary.status_counts.get(
            EvaluationStatus.REVIEW,
            0,
        )
        if (
            self.quality.review_rate.denominator_count != self.summary.case_count
            or self.quality.review_rate.numerator_count != expected_review_count
        ):
            raise ValueError("quality review rate must match summary")
        if (
            self.quality.false_confirmation_rate.denominator_count
            > self.summary.status_counts.get(EvaluationStatus.CONFIRMED, 0)
        ):
            raise ValueError("verified confirmations cannot exceed all confirmations")
        return self


class QualityBaselineReport(_ReportModel):
    schema_version: Literal[1] = 1
    run_id: RunId
    models: tuple[QualityModelReport, ...]

    @model_validator(mode="after")
    def _validate_model_names(self) -> QualityBaselineReport:
        model_names = tuple(report.provenance.model_name for report in self.models)
        if len(set(model_names)) != len(model_names):
            raise ValueError("quality report model names must be unique")
        return self
