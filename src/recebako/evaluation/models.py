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
