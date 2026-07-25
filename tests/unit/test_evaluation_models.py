from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

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
    SchemaOutcome,
    TaxOutcome,
)


def _case_result() -> CaseEvaluationResult:
    return CaseEvaluationResult(
        case_id="case-0001",
        processing_success=True,
        schema_outcome=SchemaOutcome.VALID,
        date_outcome=DateOutcome.UNCHANGED,
        tax_outcome=TaxOutcome.NOT_NEEDED,
        duplicate_outcome=DuplicateOutcome.NONE,
        status=EvaluationStatus.CONFIRMED,
        elapsed_ms=12.5,
        error_code=None,
        validation_issue_codes=(),
    )


def _summary() -> ModelEvaluationSummary:
    return ModelEvaluationSummary(
        case_count=1,
        processing_success_count=1,
        processing_success_rate=1.0,
        schema_success_count=1,
        schema_success_rate=1.0,
        confirmed_rate=1.0,
        review_rate=0.0,
        failed_rate=0.0,
        tax_applied_count=0,
        tax_rejected_count=0,
        status_counts={EvaluationStatus.CONFIRMED: 1},
        schema_outcome_counts={SchemaOutcome.VALID: 1},
        date_outcome_counts={DateOutcome.UNCHANGED: 1},
        tax_outcome_counts={TaxOutcome.NOT_NEEDED: 1},
        duplicate_outcome_counts={DuplicateOutcome.NONE: 1},
        error_code_counts={},
        validation_issue_code_counts={},
        duration=DurationSummary(
            sample_count=1,
            total_ms=12.5,
            minimum_ms=12.5,
            maximum_ms=12.5,
            mean_ms=12.5,
        ),
    )


def _unknown_accuracy() -> AccuracySummary:
    return AccuracySummary(
        status=AccuracyStatus.UNKNOWN,
        reason=AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH,
        verified_case_count=0,
    )


def test_evaluation_report_serializes_only_safe_allowlisted_metadata() -> None:
    report = EvaluationReport(
        run_id="run-20260726",
        models=(
            ModelEvaluationReport(
                model_name="qwen3-vl:8b",
                cases=(_case_result(),),
                summary=_summary(),
                accuracy=_unknown_accuracy(),
            ),
        ),
    )

    serialized = report.model_dump_json()
    parsed = json.loads(serialized)

    assert parsed["schema_version"] == 1
    assert parsed["run_id"] == "run-20260726"
    assert parsed["models"][0]["cases"][0] == {
        "case_id": "case-0001",
        "processing_success": True,
        "schema_outcome": "valid",
        "date_outcome": "unchanged",
        "tax_outcome": "not_needed",
        "duplicate_outcome": "none",
        "status": "confirmed",
        "elapsed_ms": 12.5,
        "error_code": None,
        "validation_issue_codes": [],
    }
    assert "path" not in serialized
    assert "receipt_id" not in serialized
    assert "phash" not in serialized


@pytest.mark.parametrize(
    ("field", "private_value"),
    [
        ("source_path", "/private/PRIVATE-SENTINEL.jpg"),
        ("receipt_id", "PRIVATE-SENTINEL"),
        ("phash", "PRIVATE-SENTINEL"),
        ("store", "PRIVATE-SENTINEL"),
        ("total", "PRIVATE-SENTINEL"),
        ("raw_value", "PRIVATE-SENTINEL"),
    ],
)
def test_case_report_rejects_private_extra_fields_without_echoing_values(
    field: str,
    private_value: str,
) -> None:
    payload = _case_result().model_dump()
    payload[field] = private_value

    with pytest.raises(ValidationError) as captured:
        CaseEvaluationResult.model_validate(payload)

    assert private_value not in str(captured.value)


def test_case_report_rejects_private_text_in_safe_code_fields() -> None:
    payload = _case_result().model_dump()
    private_sentinel = "PRIVATE SENTINEL"
    payload["error_code"] = private_sentinel

    with pytest.raises(ValidationError) as captured:
        CaseEvaluationResult.model_validate(payload)

    assert private_sentinel not in str(captured.value)


def test_duration_summary_enforces_empty_and_non_empty_shapes() -> None:
    empty = DurationSummary(sample_count=0, total_ms=0)
    assert empty.minimum_ms is None

    with pytest.raises(ValidationError):
        DurationSummary(sample_count=0, total_ms=1)
    with pytest.raises(ValidationError):
        DurationSummary(sample_count=1, total_ms=1)
    with pytest.raises(ValidationError):
        DurationSummary(
            sample_count=1,
            total_ms=1,
            minimum_ms=2,
            maximum_ms=3,
            mean_ms=1,
        )


@pytest.mark.parametrize("elapsed_ms", [float("inf"), float("-inf"), float("nan")])
def test_case_report_rejects_non_finite_durations(elapsed_ms: float) -> None:
    payload = _case_result().model_dump()
    payload["elapsed_ms"] = elapsed_ms

    with pytest.raises(ValidationError):
        CaseEvaluationResult.model_validate(payload)


def test_accuracy_metric_enforces_count_and_rate_consistency() -> None:
    assert (
        AccuracyMetric(
            comparable_count=2,
            correct_count=1,
            accuracy_rate=0.5,
        ).accuracy_rate
        == 0.5
    )

    with pytest.raises(ValidationError):
        AccuracyMetric(comparable_count=1, correct_count=2, accuracy_rate=1)
    with pytest.raises(ValidationError):
        AccuracyMetric(comparable_count=0, correct_count=0, accuracy_rate=0)
    with pytest.raises(ValidationError):
        AccuracyMetric(comparable_count=2, correct_count=1, accuracy_rate=1)


def test_accuracy_unknown_requires_fixed_reason_and_no_comparisons() -> None:
    assert _unknown_accuracy().status is AccuracyStatus.UNKNOWN

    with pytest.raises(ValidationError):
        AccuracySummary(
            status=AccuracyStatus.UNKNOWN,
            reason=None,
            verified_case_count=0,
        )
    with pytest.raises(ValidationError):
        AccuracySummary(
            status=AccuracyStatus.UNKNOWN,
            reason=AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH,
            verified_case_count=1,
        )


def test_measured_accuracy_requires_verified_case_and_omits_unknown_reason() -> None:
    measured = AccuracySummary(
        status=AccuracyStatus.MEASURED,
        reason=None,
        verified_case_count=1,
        store=AccuracyMetric(
            comparable_count=1,
            correct_count=1,
            accuracy_rate=1,
        ),
    )
    assert measured.store.correct_count == 1

    with pytest.raises(ValidationError):
        AccuracySummary(
            status=AccuracyStatus.MEASURED,
            reason=AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH,
            verified_case_count=1,
        )


def test_model_summary_enforces_rates_distributions_and_tax_counts() -> None:
    payload = _summary().model_dump()
    payload["confirmed_rate"] = 0.5
    with pytest.raises(ValidationError):
        ModelEvaluationSummary.model_validate(payload)

    payload = _summary().model_dump()
    payload["status_counts"] = {}
    with pytest.raises(ValidationError):
        ModelEvaluationSummary.model_validate(payload)

    payload = _summary().model_dump()
    payload["tax_applied_count"] = 1
    with pytest.raises(ValidationError):
        ModelEvaluationSummary.model_validate(payload)

    payload = _summary().model_dump()
    payload["tax_outcome_counts"] = {TaxOutcome.AMBIGUOUS: 1}
    with pytest.raises(ValidationError):
        ModelEvaluationSummary.model_validate(payload)


def test_tax_rejected_count_sums_only_rejected_outcomes() -> None:
    summary = ModelEvaluationSummary(
        case_count=3,
        processing_success_count=3,
        processing_success_rate=1.0,
        schema_success_count=3,
        schema_success_rate=1.0,
        confirmed_rate=0.0,
        review_rate=1.0,
        failed_rate=0.0,
        tax_applied_count=1,
        tax_rejected_count=1,
        status_counts={EvaluationStatus.REVIEW: 3},
        schema_outcome_counts={SchemaOutcome.VALID: 3},
        date_outcome_counts={DateOutcome.UNCHANGED: 3},
        tax_outcome_counts={
            TaxOutcome.APPLIED: 1,
            TaxOutcome.AMBIGUOUS: 1,
            TaxOutcome.NOT_EVALUATED: 1,
        },
        duplicate_outcome_counts={DuplicateOutcome.NONE: 3},
        error_code_counts={},
        validation_issue_code_counts={},
        duration=DurationSummary(
            sample_count=3,
            total_ms=30,
            minimum_ms=10,
            maximum_ms=10,
            mean_ms=10,
        ),
    )

    assert summary.tax_rejected_count == 1


def test_model_report_rejects_duplicate_case_ids() -> None:
    with pytest.raises(ValidationError):
        ModelEvaluationReport(
            model_name="qwen3-vl:8b",
            cases=(_case_result(), _case_result()),
            summary=ModelEvaluationSummary(
                case_count=2,
                processing_success_count=2,
                processing_success_rate=1,
                schema_success_count=2,
                schema_success_rate=1,
                confirmed_rate=1,
                review_rate=0,
                failed_rate=0,
                tax_applied_count=0,
                tax_rejected_count=0,
                status_counts={EvaluationStatus.CONFIRMED: 2},
                schema_outcome_counts={SchemaOutcome.VALID: 2},
                date_outcome_counts={DateOutcome.UNCHANGED: 2},
                tax_outcome_counts={TaxOutcome.NOT_NEEDED: 2},
                duplicate_outcome_counts={DuplicateOutcome.NONE: 2},
                error_code_counts={},
                validation_issue_code_counts={},
                duration=DurationSummary(
                    sample_count=2,
                    total_ms=25,
                    minimum_ms=12.5,
                    maximum_ms=12.5,
                    mean_ms=12.5,
                ),
            ),
            accuracy=_unknown_accuracy(),
        )


def test_evaluation_report_rejects_duplicate_models() -> None:
    model_report = ModelEvaluationReport(
        model_name="qwen3-vl:8b",
        cases=(_case_result(),),
        summary=_summary(),
        accuracy=_unknown_accuracy(),
    )

    with pytest.raises(ValidationError):
        EvaluationReport(
            run_id="run-20260726",
            models=(model_report, model_report),
        )
