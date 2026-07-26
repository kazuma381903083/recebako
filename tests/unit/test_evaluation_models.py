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


def _unknown_quality(target_case_count: int = 1) -> QualityBaselineSummary:
    unknown = QualityAssessment(
        status=QualityAssessmentStatus.UNKNOWN,
        reason=QualityUnknownReason.INCOMPLETE_GOLDEN_SET,
    )
    empty = QualityRateMetric(
        denominator_count=0,
        numerator_count=0,
        rate=None,
    )
    return QualityBaselineSummary(
        target_case_count=target_case_count,
        verified_case_count=0,
        golden_set_complete=False,
        total_accuracy=empty,
        store_accuracy=empty,
        date_accuracy=empty,
        item_accuracy=empty,
        false_confirmation_rate=empty,
        review_rate=QualityRateMetric(
            denominator_count=target_case_count,
            numerator_count=0,
            rate=0 if target_case_count else None,
        ),
        q1_total=unknown,
        q2_store_and_date=unknown,
        q3_items=unknown,
        q4_false_confirmation=unknown,
        q5_review=unknown,
    )


def _provenance() -> QualityProvenance:
    return QualityProvenance(
        model_name="qwen3-vl:8b",
        prompt_sha256="0" * 64,
        extraction_schema_sha256="1" * 64,
    )


def _quality_rate(denominator: int, numerator: int) -> QualityRateMetric:
    return QualityRateMetric(
        denominator_count=denominator,
        numerator_count=numerator,
        rate=None if denominator == 0 else numerator / denominator,
    )


def _quality_assessment(
    status: QualityAssessmentStatus,
    reason: QualityUnknownReason | None = None,
) -> QualityAssessment:
    return QualityAssessment(status=status, reason=reason)


def _complete_quality(
    *,
    total_correct: int = 30,
    store_correct: int = 30,
    date_correct: int = 30,
    item_denominator: int = 100,
    item_correct: int = 80,
    confirmed_count: int = 21,
    false_confirmed_count: int = 0,
    review_count: int = 9,
    q1_status: QualityAssessmentStatus = QualityAssessmentStatus.MET,
    q2_status: QualityAssessmentStatus = QualityAssessmentStatus.MET,
    q3_status: QualityAssessmentStatus = QualityAssessmentStatus.MET,
    q3_reason: QualityUnknownReason | None = None,
    q4_status: QualityAssessmentStatus = QualityAssessmentStatus.MET,
    q4_reason: QualityUnknownReason | None = None,
    q5_status: QualityAssessmentStatus = QualityAssessmentStatus.MET,
) -> QualityBaselineSummary:
    return QualityBaselineSummary(
        target_case_count=30,
        verified_case_count=30,
        golden_set_complete=True,
        total_accuracy=_quality_rate(30, total_correct),
        store_accuracy=_quality_rate(30, store_correct),
        date_accuracy=_quality_rate(30, date_correct),
        item_accuracy=_quality_rate(item_denominator, item_correct),
        false_confirmation_rate=_quality_rate(
            confirmed_count,
            false_confirmed_count,
        ),
        review_rate=_quality_rate(30, review_count),
        q1_total=_quality_assessment(q1_status),
        q2_store_and_date=_quality_assessment(q2_status),
        q3_items=_quality_assessment(q3_status, q3_reason),
        q4_false_confirmation=_quality_assessment(q4_status, q4_reason),
        q5_review=_quality_assessment(q5_status),
    )


def _incomplete_quality() -> QualityBaselineSummary:
    incomplete = _quality_assessment(
        QualityAssessmentStatus.UNKNOWN,
        QualityUnknownReason.INCOMPLETE_GOLDEN_SET,
    )
    return QualityBaselineSummary(
        target_case_count=30,
        verified_case_count=29,
        golden_set_complete=False,
        total_accuracy=_quality_rate(29, 29),
        store_accuracy=_quality_rate(29, 29),
        date_accuracy=_quality_rate(29, 29),
        item_accuracy=_quality_rate(50, 40),
        false_confirmation_rate=_quality_rate(20, 0),
        review_rate=_quality_rate(30, 9),
        q1_total=incomplete,
        q2_store_and_date=incomplete,
        q3_items=incomplete,
        q4_false_confirmation=incomplete,
        q5_review=incomplete,
    )


def _quality_model_summary(
    *,
    confirmed_count: int = 21,
    review_count: int = 9,
    failed_count: int = 0,
) -> ModelEvaluationSummary:
    case_count = confirmed_count + review_count + failed_count
    return ModelEvaluationSummary(
        case_count=case_count,
        processing_success_count=case_count,
        processing_success_rate=1.0,
        schema_success_count=case_count,
        schema_success_rate=1.0,
        confirmed_rate=confirmed_count / case_count,
        review_rate=review_count / case_count,
        failed_rate=failed_count / case_count,
        tax_applied_count=0,
        tax_rejected_count=0,
        status_counts={
            EvaluationStatus.CONFIRMED: confirmed_count,
            EvaluationStatus.REVIEW: review_count,
            EvaluationStatus.FAILED: failed_count,
        },
        schema_outcome_counts={SchemaOutcome.VALID: case_count},
        date_outcome_counts={DateOutcome.UNCHANGED: case_count},
        tax_outcome_counts={TaxOutcome.NOT_NEEDED: case_count},
        duplicate_outcome_counts={DuplicateOutcome.NONE: case_count},
        error_code_counts={},
        validation_issue_code_counts={},
        duration=DurationSummary(
            sample_count=case_count,
            total_ms=case_count * 10,
            minimum_ms=10,
            maximum_ms=10,
            mean_ms=10,
        ),
    )


def _measured_accuracy(
    *,
    verified_case_count: int = 30,
    total_correct: int = 30,
    store_correct: int = 30,
    date_correct: int = 30,
) -> AccuracySummary:
    return AccuracySummary(
        status=AccuracyStatus.MEASURED,
        reason=None,
        verified_case_count=verified_case_count,
        total=AccuracyMetric(
            comparable_count=verified_case_count,
            correct_count=total_correct,
            accuracy_rate=total_correct / verified_case_count,
        ),
        store=AccuracyMetric(
            comparable_count=verified_case_count,
            correct_count=store_correct,
            accuracy_rate=store_correct / verified_case_count,
        ),
        date=AccuracyMetric(
            comparable_count=verified_case_count,
            correct_count=date_correct,
            accuracy_rate=date_correct / verified_case_count,
        ),
    )


def _complete_quality_model() -> QualityModelReport:
    return QualityModelReport(
        provenance=_provenance(),
        summary=_quality_model_summary(),
        accuracy=_measured_accuracy(store_correct=29),
        quality=_complete_quality(store_correct=30),
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
    assert set(parsed) == {"schema_version", "run_id", "models"}
    assert set(parsed["models"][0]) == {
        "model_name",
        "cases",
        "summary",
        "accuracy",
    }
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


def test_quality_sidecar_serializes_only_aggregate_safe_metadata() -> None:
    report = QualityBaselineReport(
        run_id="run-20260726",
        models=(
            QualityModelReport(
                provenance=_provenance(),
                summary=_summary(),
                accuracy=_unknown_accuracy(),
                quality=_unknown_quality(),
            ),
        ),
    )

    parsed = json.loads(report.model_dump_json())

    assert parsed["schema_version"] == 1
    assert parsed["run_id"] == "run-20260726"
    assert set(parsed) == {"schema_version", "run_id", "models"}
    assert set(parsed["models"][0]) == {
        "provenance",
        "summary",
        "accuracy",
        "quality",
    }
    assert parsed["models"][0]["provenance"] == {
        "metric_version": "quality-v1",
        "model_name": "qwen3-vl:8b",
        "prompt_sha256": "0" * 64,
        "extraction_schema_sha256": "1" * 64,
    }
    serialized = report.model_dump_json()
    assert "cases" not in serialized
    assert "path" not in serialized
    assert "receipt_id" not in serialized
    assert "phash" not in serialized
    assert "PRIVATE-SENTINEL" not in serialized


def test_complete_quality_baseline_meets_q1_through_q5_at_fixed_boundaries() -> None:
    quality = _complete_quality(
        store_correct=29,
        date_correct=29,
        item_denominator=100,
        item_correct=80,
        review_count=9,
    )

    assert quality.golden_set_complete is True
    assert quality.item_accuracy.rate == 0.80
    assert quality.review_rate.rate == 0.30
    assert (
        quality.q1_total.status,
        quality.q2_store_and_date.status,
        quality.q3_items.status,
        quality.q4_false_confirmation.status,
        quality.q5_review.status,
    ) == (QualityAssessmentStatus.MET,) * 5


@pytest.mark.parametrize(
    "threshold_case",
    [
        "q1_total",
        "q2_store",
        "q2_date",
        "q3_items",
        "q4_false_confirmation",
        "q5_review",
    ],
)
def test_quality_baseline_marks_the_smallest_count_beyond_each_threshold_not_met(
    threshold_case: str,
) -> None:
    if threshold_case == "q1_total":
        quality = _complete_quality(
            total_correct=29,
            q1_status=QualityAssessmentStatus.NOT_MET,
        )
        assessment = quality.q1_total
    elif threshold_case == "q2_store":
        quality = _complete_quality(
            store_correct=28,
            q2_status=QualityAssessmentStatus.NOT_MET,
        )
        assessment = quality.q2_store_and_date
    elif threshold_case == "q2_date":
        quality = _complete_quality(
            date_correct=28,
            q2_status=QualityAssessmentStatus.NOT_MET,
        )
        assessment = quality.q2_store_and_date
    elif threshold_case == "q3_items":
        quality = _complete_quality(
            item_denominator=100,
            item_correct=79,
            q3_status=QualityAssessmentStatus.NOT_MET,
        )
        assessment = quality.q3_items
    elif threshold_case == "q4_false_confirmation":
        quality = _complete_quality(
            total_correct=29,
            confirmed_count=30,
            false_confirmed_count=1,
            q1_status=QualityAssessmentStatus.NOT_MET,
            q4_status=QualityAssessmentStatus.NOT_MET,
        )
        assessment = quality.q4_false_confirmation
    else:
        quality = _complete_quality(
            review_count=10,
            q5_status=QualityAssessmentStatus.NOT_MET,
        )
        assessment = quality.q5_review

    assert assessment.status is QualityAssessmentStatus.NOT_MET
    assert assessment.reason is None


def test_incomplete_golden_set_keeps_all_quality_assessments_unknown() -> None:
    quality = _incomplete_quality()

    assert quality.golden_set_complete is False
    for assessment in (
        quality.q1_total,
        quality.q2_store_and_date,
        quality.q3_items,
        quality.q4_false_confirmation,
        quality.q5_review,
    ):
        assert assessment.status is QualityAssessmentStatus.UNKNOWN
        assert assessment.reason is QualityUnknownReason.INCOMPLETE_GOLDEN_SET


def test_complete_quality_uses_zero_denominator_reason_for_q3_and_q4() -> None:
    quality = _complete_quality(
        item_denominator=0,
        item_correct=0,
        confirmed_count=0,
        false_confirmed_count=0,
        q3_status=QualityAssessmentStatus.UNKNOWN,
        q3_reason=QualityUnknownReason.ZERO_DENOMINATOR,
        q4_status=QualityAssessmentStatus.UNKNOWN,
        q4_reason=QualityUnknownReason.ZERO_DENOMINATOR,
    )

    assert quality.q3_items == _quality_assessment(
        QualityAssessmentStatus.UNKNOWN,
        QualityUnknownReason.ZERO_DENOMINATOR,
    )
    assert quality.q4_false_confirmation == _quality_assessment(
        QualityAssessmentStatus.UNKNOWN,
        QualityUnknownReason.ZERO_DENOMINATOR,
    )


def test_quality_model_report_accepts_consistent_aggregates_and_store_improvement() -> (
    None
):
    model = _complete_quality_model()

    assert model.quality.store_accuracy.numerator_count == 30
    assert model.accuracy.store.correct_count == 29
    assert model.quality.review_rate.numerator_count == 9
    assert model.quality.false_confirmation_rate.denominator_count == 21


def test_quality_model_report_rejects_inconsistent_aggregate_sources() -> None:
    provenance = _provenance()
    summary = _quality_model_summary()
    accuracy = _measured_accuracy(store_correct=29)

    invalid_inputs = (
        {
            "summary": summary,
            "accuracy": _unknown_accuracy(),
            "quality": _unknown_quality(target_case_count=1),
        },
        {
            "summary": summary,
            "accuracy": _unknown_accuracy(),
            "quality": _complete_quality(),
        },
        {
            "summary": summary,
            "accuracy": accuracy,
            "quality": _complete_quality(
                total_correct=29,
                q1_status=QualityAssessmentStatus.NOT_MET,
            ),
        },
        {
            "summary": summary,
            "accuracy": accuracy,
            "quality": _complete_quality(
                date_correct=29,
            ),
        },
        {
            "summary": summary,
            "accuracy": accuracy,
            "quality": _complete_quality(
                store_correct=28,
                q2_status=QualityAssessmentStatus.NOT_MET,
            ),
        },
        {
            "summary": summary,
            "accuracy": accuracy,
            "quality": _complete_quality(
                review_count=10,
                q5_status=QualityAssessmentStatus.NOT_MET,
            ),
        },
        {
            "summary": summary,
            "accuracy": accuracy,
            "quality": _complete_quality(
                confirmed_count=30,
            ),
        },
    )

    for invalid in invalid_inputs:
        with pytest.raises(ValidationError):
            QualityModelReport(provenance=provenance, **invalid)


def test_quality_baseline_rejects_impossible_false_confirmation_counts() -> None:
    payload = _complete_quality().model_dump()
    payload["total_accuracy"] = _quality_rate(30, 0).model_dump()
    payload["false_confirmation_rate"] = _quality_rate(30, 0).model_dump()
    payload["q1_total"] = _quality_assessment(
        QualityAssessmentStatus.NOT_MET
    ).model_dump()

    with pytest.raises(ValidationError):
        QualityBaselineSummary.model_validate(payload)


def test_quality_provenance_and_sidecar_validation_do_not_echo_private_input() -> None:
    private_sentinel = "PRIVATE-SENTINEL"
    payload = _provenance().model_dump()
    payload["prompt_sha256"] = private_sentinel

    with pytest.raises(ValidationError) as captured:
        QualityProvenance.model_validate(payload)

    assert private_sentinel not in str(captured.value)


def test_quality_sidecar_rejects_duplicate_model_provenance() -> None:
    model = _complete_quality_model()

    with pytest.raises(ValidationError):
        QualityBaselineReport(
            run_id="run-20260726",
            models=(model, model),
        )


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


def test_quality_rate_metric_enforces_count_and_rate_consistency() -> None:
    assert (
        QualityRateMetric(
            denominator_count=4,
            numerator_count=3,
            rate=0.75,
        ).rate
        == 0.75
    )

    with pytest.raises(ValidationError):
        QualityRateMetric(denominator_count=1, numerator_count=2, rate=1)
    with pytest.raises(ValidationError):
        QualityRateMetric(denominator_count=0, numerator_count=0, rate=0)
    with pytest.raises(ValidationError):
        QualityRateMetric(denominator_count=4, numerator_count=3, rate=1)


def test_quality_metric_version_and_thresholds_are_fixed() -> None:
    payload = _unknown_quality().model_dump()
    payload["metric_version"] = "quality-v2"
    with pytest.raises(ValidationError):
        QualityBaselineSummary.model_validate(payload)

    payload = _unknown_quality().model_dump()
    payload["required_verified_case_count"] = 29
    with pytest.raises(ValidationError):
        QualityBaselineSummary.model_validate(payload)

    provenance_payload = _provenance().model_dump()
    provenance_payload["metric_version"] = "quality-v2"
    with pytest.raises(ValidationError):
        QualityProvenance.model_validate(provenance_payload)

    fixed_thresholds = QualityThresholds().model_dump()
    for field_name in fixed_thresholds:
        threshold_payload = fixed_thresholds.copy()
        threshold_payload[field_name] = 0.0
        with pytest.raises(ValidationError):
            QualityThresholds.model_validate(threshold_payload)

    payload = _unknown_quality().model_dump()
    payload["thresholds"]["q1_total_minimum"] = 0.5
    with pytest.raises(ValidationError):
        QualityBaselineSummary.model_validate(payload)


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
