from __future__ import annotations

import csv
import hashlib
import json
import os
import stat
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import recebako.evaluation.runner as runner_module
from recebako.ai import OllamaTimeoutError
from recebako.ai.ollama import EXTRACTION_PROMPT
from recebako.config import AppConfig, OllamaConfig
from recebako.domain import (
    IngestMode,
    ReceiptExtraction,
    ReceiptStatus,
    TaxTreatment,
    ValidationIssue,
)
from recebako.evaluation import (
    AccuracyStatus,
    AccuracyUnknownReason,
    CaseEvaluationResult,
    DateOutcome,
    DuplicateOutcome,
    EvaluationStatus,
    ModelEvaluationSummary,
    QualityAssessmentStatus,
    QualityBaselineReport,
    QualityUnknownReason,
    SchemaOutcome,
    TaxOutcome,
)
from recebako.evaluation.dataset import discover_cases
from recebako.evaluation.quality import _QualityCounts
from recebako.evaluation.runner import (
    EvaluationRunError,
    EvaluationRunErrorCode,
    run_evaluation,
)
from recebako.evaluation.truth import TRUTH_CSV_HEADERS
from recebako.normalization import TaxNormalizationReason
from recebako.pipeline import (
    DuplicateOutcome as ProcessDuplicateOutcome,
)
from recebako.pipeline import ProcessAudit, ProcessResult
from recebako.storage import StoredItem
from recebako.validation import DateNormalizationOutcome
from recebako.validation import (
    SchemaOutcome as ValidationSchemaOutcome,
)

REFERENCE_DATE = date(2026, 7, 26)
PRIVATE_SENTINEL = "PRIVATE-RECEIPT-CONTENT"


class _StepClock:
    def __init__(self, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def _config(data_root: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "data": {"root": data_root},
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "production-model",
                "temperature": 0,
            },
            "review_ui": {"host": "127.0.0.1", "port": 8765},
        }
    )


def _write_cases(source_root: Path, count: int = 2) -> dict[str, bytes]:
    source_root.mkdir()
    contents: dict[str, bytes] = {}
    for index in range(1, count + 1):
        case_id = f"case-{index:04d}"
        content = f"synthetic-image-{index}".encode()
        source = source_root / f"{case_id}.jpg"
        source.write_bytes(content)
        os.utime(
            source,
            ns=(1_000_000_000 + index, 2_000_000_000 + index),
        )
        contents[case_id] = content
    return contents


def _source_snapshot(path: Path) -> tuple[bytes, int, int, int, int, int]:
    source_stat = path.stat(follow_symlinks=False)
    return (
        path.read_bytes(),
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_mtime_ns,
        stat.S_IMODE(source_stat.st_mode),
        source_stat.st_nlink,
    )


def _process_audit(
    *,
    schema: ValidationSchemaOutcome = ValidationSchemaOutcome.VALID,
    date_outcome: DateNormalizationOutcome = DateNormalizationOutcome.NORMALIZED,
    tax: TaxNormalizationReason | None = TaxNormalizationReason.APPLIED,
    duplicate: ProcessDuplicateOutcome = ProcessDuplicateOutcome.NONE,
) -> ProcessAudit:
    return ProcessAudit(
        schema_outcome=schema,
        date_normalization_outcome=date_outcome,
        tax_normalization_reason=tax,
        duplicate_outcome=duplicate,
    )


def _private_process_result(
    *,
    receipt_id: int = 71,
    status: ReceiptStatus = ReceiptStatus.CONFIRMED,
    store: str = PRIVATE_SENTINEL,
    receipt_date: str = "2026-07-25",
    total: int = 987_654,
) -> ProcessResult:
    return ProcessResult(
        receipt_id=receipt_id,
        status=status,
        duplicate_of_id=999,
        validation_issues=[
            ValidationIssue(
                code="total.mismatch",
                message=f"{PRIVATE_SENTINEL}-message",
                field=f"{PRIVATE_SENTINEL}-field",
            )
        ],
        store=store,
        date_raw=f"{PRIVATE_SENTINEL}-date-raw",
        date=receipt_date,
        total=total,
        phash=f"{PRIVATE_SENTINEL}-phash",
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _assert_safe_report_shape(payload: dict[str, Any]) -> None:
    assert set(payload) == {"schema_version", "run_id", "models"}
    for model in payload["models"]:
        assert set(model) == {"model_name", "cases", "summary", "accuracy"}
        for case in model["cases"]:
            assert set(case) == {
                "case_id",
                "processing_success",
                "schema_outcome",
                "date_outcome",
                "tax_outcome",
                "duplicate_outcome",
                "status",
                "elapsed_ms",
                "error_code",
                "validation_issue_codes",
            }
        _assert_summary_shape(model["summary"])
        _assert_accuracy_shape(model["accuracy"])


def _assert_summary_shape(summary: dict[str, Any]) -> None:
    assert set(summary) == {
        "case_count",
        "processing_success_count",
        "processing_success_rate",
        "schema_success_count",
        "schema_success_rate",
        "confirmed_rate",
        "review_rate",
        "failed_rate",
        "tax_applied_count",
        "tax_rejected_count",
        "status_counts",
        "schema_outcome_counts",
        "date_outcome_counts",
        "tax_outcome_counts",
        "duplicate_outcome_counts",
        "error_code_counts",
        "validation_issue_code_counts",
        "duration",
    }
    assert set(summary["duration"]) == {
        "sample_count",
        "total_ms",
        "minimum_ms",
        "maximum_ms",
        "mean_ms",
    }


def _assert_accuracy_shape(accuracy: dict[str, Any]) -> None:
    assert set(accuracy) == {
        "status",
        "reason",
        "verified_case_count",
        "store",
        "date",
        "total",
        "receipt_status",
        "item_name",
        "item_quantity",
        "item_price",
    }
    for field_name in (
        "store",
        "date",
        "total",
        "receipt_status",
        "item_name",
        "item_quantity",
        "item_price",
    ):
        assert set(accuracy[field_name]) == {
            "comparable_count",
            "correct_count",
            "accuracy_rate",
        }


def _assert_safe_quality_sidecar_shape(payload: dict[str, Any]) -> None:
    assert set(payload) == {"schema_version", "run_id", "models"}
    for model in payload["models"]:
        assert set(model) == {"provenance", "summary", "accuracy", "quality"}
        assert set(model["provenance"]) == {
            "metric_version",
            "model_name",
            "prompt_sha256",
            "extraction_schema_sha256",
        }
        _assert_summary_shape(model["summary"])
        _assert_accuracy_shape(model["accuracy"])
        quality = model["quality"]
        assert set(quality) == {
            "metric_version",
            "required_verified_case_count",
            "target_case_count",
            "verified_case_count",
            "golden_set_complete",
            "total_accuracy",
            "store_accuracy",
            "date_accuracy",
            "item_accuracy",
            "false_confirmation_rate",
            "review_rate",
            "thresholds",
            "q1_total",
            "q2_store_and_date",
            "q3_items",
            "q4_false_confirmation",
            "q5_review",
        }
        for field_name in (
            "total_accuracy",
            "store_accuracy",
            "date_accuracy",
            "item_accuracy",
            "false_confirmation_rate",
            "review_rate",
        ):
            assert set(quality[field_name]) == {
                "denominator_count",
                "numerator_count",
                "rate",
            }
        assert set(quality["thresholds"]) == {
            "q1_total_minimum",
            "q2_store_minimum",
            "q2_date_minimum",
            "q3_items_minimum",
            "q4_false_confirmation_maximum",
            "q5_review_maximum",
        }
        for field_name in (
            "q1_total",
            "q2_store_and_date",
            "q3_items",
            "q4_false_confirmation",
            "q5_review",
        ):
            assert set(quality[field_name]) == {"status", "reason"}


def test_run_evaluation_preserves_sources_separates_models_and_writes_safe_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    expected_contents = _write_cases(source_root)
    source_snapshots = {
        path.name: _source_snapshot(path) for path in source_root.iterdir()
    }
    production_root = tmp_path / "production"
    production_root.mkdir()
    production_marker = production_root / "keep.txt"
    production_marker.write_text("unchanged", encoding="utf-8")
    production_snapshot = _source_snapshot(production_marker)
    output_root = tmp_path / "evaluation-output"
    process_calls: list[tuple[str, str, Path, Path, Path]] = []
    ollama_configs: list[OllamaConfig] = []
    base_config = _config(production_root)
    base_config_snapshot = base_config.model_dump(mode="json")

    def fake_process(
        image_path: Path,
        *,
        config: AppConfig,
        storage_image_path: Path,
        temporary_root: Path,
        **kwargs: Any,
    ) -> tuple[ProcessResult, ProcessAudit]:
        ollama_configs.append(config.ollama)
        process_calls.append(
            (
                config.ollama.model,
                image_path.stem,
                config.data.root,
                storage_image_path,
                temporary_root,
            )
        )
        return _private_process_result(), _process_audit()

    monkeypatch.setattr(
        runner_module,
        "process_receipt_with_audit",
        fake_process,
    )

    report = run_evaluation(
        source_root,
        output_root=output_root,
        base_config=base_config,
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        clock=_StepClock(),
        run_id_factory=lambda: "run-fixed",
    )

    assert [(model_name, case_id) for model_name, case_id, *_ in process_calls] == [
        ("qwen3-vl:8b", "case-0001"),
        ("qwen3-vl:8b", "case-0002"),
        ("qwen3.5:9b", "case-0001"),
        ("qwen3.5:9b", "case-0002"),
    ]
    assert {
        (config.base_url, config.model, config.temperature) for config in ollama_configs
    } == {
        ("http://127.0.0.1:11434", "qwen3-vl:8b", 0),
        ("http://127.0.0.1:11434", "qwen3.5:9b", 0),
    }
    assert base_config.model_dump(mode="json") == base_config_snapshot
    assert base_config.ollama.model == "production-model"
    data_roots = {call[2] for call in process_calls}
    assert data_roots == {
        output_root / "run-fixed" / "model-01",
        output_root / "run-fixed" / "model-02",
    }
    for _, _, data_root, storage_path, temporary_root in process_calls:
        assert storage_path.parts[0] == "evaluation-inputs"
        assert temporary_root == data_root / "tmp"

    for source in source_root.iterdir():
        assert _source_snapshot(source) == source_snapshots[source.name]
    assert _source_snapshot(production_marker) == production_snapshot
    assert not (production_root / "ledger.db").exists()

    run_root = output_root / "run-fixed"
    assert _mode(output_root) == 0o700
    assert _mode(run_root) == 0o700
    ledger_inodes: set[tuple[int, int]] = set()
    for index in (1, 2):
        data_root = run_root / f"model-{index:02d}"
        input_root = data_root / "evaluation-inputs"
        assert _mode(data_root) == 0o700
        assert _mode(input_root) == 0o700
        assert _mode(data_root / "ledger.db") == 0o600
        assert _mode(data_root / ".recebako-inbox.lock") == 0o600
        for directory_name in runner_module.RUNTIME_DIRECTORY_NAMES:
            assert _mode(data_root / directory_name) == 0o700
        ledger_stat = (data_root / "ledger.db").stat()
        ledger_inodes.add((ledger_stat.st_dev, ledger_stat.st_ino))
        for case_id, expected_content in expected_contents.items():
            copied = input_root / f"{case_id}.jpg"
            source = source_root / f"{case_id}.jpg"
            assert copied.read_bytes() == expected_content
            assert _mode(copied) == 0o600
            assert copied.stat().st_ino != source.stat().st_ino
    assert len(ledger_inodes) == 2

    report_path = run_root / "evaluation-report.json"
    assert _mode(report_path) == 0o600
    report_text = report_path.read_text(encoding="utf-8")
    payload = json.loads(report_text)
    assert payload["schema_version"] == 1
    assert payload == report.model_dump(mode="json")
    _assert_safe_report_shape(payload)
    quality_report_path = run_root / "quality-baseline-report.json"
    assert _mode(quality_report_path) == 0o600
    quality_report_text = quality_report_path.read_text(encoding="utf-8")
    quality_payload = json.loads(quality_report_text)
    quality_report = QualityBaselineReport.model_validate(quality_payload)
    assert quality_payload["schema_version"] == 1
    assert quality_payload == quality_report.model_dump(mode="json")
    _assert_safe_quality_sidecar_shape(quality_payload)
    for forbidden in (
        PRIVATE_SENTINEL,
        str(source_root),
        str(output_root),
        str(production_root),
        "receipt_id",
        "duplicate_of_id",
        "phash",
        "date_raw",
        "raw_payload",
        "image_path",
        "source_path",
        "message",
        "field",
        "987654",
    ):
        assert forbidden not in report_text
        assert forbidden not in quality_report_text
    assert "case_id" not in quality_report_text
    assert all(case_id not in quality_report_text for case_id in expected_contents)
    assert all(
        model.accuracy.status is AccuracyStatus.UNKNOWN
        and model.accuracy.reason
        is AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH
        for model in report.models
    )
    assert all(
        model.quality.verified_case_count == 0
        and not model.quality.golden_set_complete
        and model.quality.q1_total.status is QualityAssessmentStatus.UNKNOWN
        and model.quality.q1_total.reason is QualityUnknownReason.INCOMPLETE_GOLDEN_SET
        for model in quality_report.models
    )


def test_run_evaluation_continues_after_private_exception_and_aggregates_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root)
    called: list[str] = []

    def fake_process(
        image_path: Path, **kwargs: Any
    ) -> tuple[ProcessResult, ProcessAudit]:
        called.append(image_path.stem)
        if image_path.stem == "case-0001":
            raise OllamaTimeoutError(f"{PRIVATE_SENTINEL}-exception")
        return (
            _private_process_result(status=ReceiptStatus.REVIEW),
            _process_audit(
                date_outcome=DateNormalizationOutcome.REJECTED,
                tax=TaxNormalizationReason.AMBIGUOUS,
            ),
        )

    monkeypatch.setattr(
        runner_module,
        "process_receipt_with_audit",
        fake_process,
    )

    report = run_evaluation(
        source_root,
        output_root=tmp_path / "output",
        base_config=_config(tmp_path / "production"),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        models=("qwen3-vl:8b",),
        clock=_StepClock(),
        run_id_factory=lambda: "run-partial",
    )

    assert called == ["case-0001", "case-0002"]
    first, second = report.models[0].cases
    assert first.processing_success is False
    assert first.status is EvaluationStatus.FAILED
    assert first.schema_outcome is SchemaOutcome.NOT_EVALUATED
    assert first.date_outcome is DateOutcome.NOT_EVALUATED
    assert first.tax_outcome is TaxOutcome.NOT_EVALUATED
    assert first.duplicate_outcome is DuplicateOutcome.NOT_EVALUATED
    assert first.error_code == "ollama.timeout"
    assert second.processing_success is True
    assert second.status is EvaluationStatus.REVIEW
    assert second.error_code is None
    summary = report.models[0].summary
    assert summary.case_count == 2
    assert summary.processing_success_count == 1
    assert summary.processing_success_rate == 0.5
    assert summary.review_rate == 0.5
    assert summary.failed_rate == 0.5
    assert summary.error_code_counts == {"ollama.timeout": 1}
    report_text = (
        tmp_path / "output" / "run-partial" / "evaluation-report.json"
    ).read_text(encoding="utf-8")
    assert PRIVATE_SENTINEL not in report_text


@pytest.mark.parametrize(
    ("replacement_kind", "expected_code"),
    [
        ("symlink", EvaluationRunErrorCode.COPY_FAILED),
        ("regular", EvaluationRunErrorCode.SOURCE_CHANGED),
    ],
)
def test_run_evaluation_rejects_source_replaced_after_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
    expected_code: EvaluationRunErrorCode,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    original_discover = runner_module.discover_cases
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")

    def replacing_discover(path: Path) -> list[runner_module.EvaluationCase]:
        cases = original_discover(path)
        source = cases[0].source_path
        source.unlink()
        if replacement_kind == "symlink":
            source.symlink_to(outside)
        else:
            source.write_bytes(b"replacement-with-different-identity")
        return cases

    monkeypatch.setattr(runner_module, "discover_cases", replacing_discover)
    monkeypatch.setattr(
        runner_module,
        "process_receipt_with_audit",
        lambda *args, **kwargs: pytest.fail("replaced source must not be processed"),
    )

    with pytest.raises(EvaluationRunError) as error:
        run_evaluation(
            source_root,
            output_root=tmp_path / "output",
            base_config=_config(tmp_path / "production"),
            mode=IngestMode.REGULAR,
            reference_date=REFERENCE_DATE,
            models=("qwen3-vl:8b",),
            clock=_StepClock(),
            run_id_factory=lambda: f"run-{replacement_kind}",
        )

    assert error.value.code is expected_code
    assert PRIVATE_SENTINEL not in str(error.value)
    assert (
        list(
            (
                tmp_path
                / "output"
                / f"run-{replacement_kind}"
                / "model-01"
                / "evaluation-inputs"
            ).iterdir()
        )
        == []
    )
    assert not (
        tmp_path / "output" / f"run-{replacement_kind}" / "evaluation-report.json"
    ).exists()
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("overlap_target", ["source", "production"])
def test_run_evaluation_rejects_overlapping_output_before_writes(
    tmp_path: Path,
    overlap_target: str,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    production_root = tmp_path / "production"
    production_root.mkdir()
    marker = production_root / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    output_root = (
        source_root / "evaluation"
        if overlap_target == "source"
        else production_root / "evaluation"
    )

    with pytest.raises(EvaluationRunError) as error:
        run_evaluation(
            source_root,
            output_root=output_root,
            base_config=_config(production_root),
            mode=IngestMode.REGULAR,
            reference_date=REFERENCE_DATE,
            models=("qwen3-vl:8b",),
            run_id_factory=lambda: "run-overlap",
        )

    assert error.value.code is EvaluationRunErrorCode.ROOTS_OVERLAP
    assert not output_root.exists()
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (production_root / "ledger.db").exists()


def test_run_evaluation_rejects_source_inside_production_root_before_writes(
    tmp_path: Path,
) -> None:
    production_root = tmp_path / "production"
    production_root.mkdir()
    marker = production_root / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    source_root = production_root / "evaluation-source"
    _write_cases(source_root, count=1)
    output_root = tmp_path / "output"

    with pytest.raises(EvaluationRunError) as error:
        run_evaluation(
            source_root,
            output_root=output_root,
            base_config=_config(production_root),
            mode=IngestMode.REGULAR,
            reference_date=REFERENCE_DATE,
            models=("qwen3-vl:8b",),
            run_id_factory=lambda: "run-production-source-overlap",
        )

    assert error.value.code is EvaluationRunErrorCode.ROOTS_OVERLAP
    assert not output_root.exists()
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (production_root / "ledger.db").exists()


def test_run_evaluation_preserves_existing_output_root_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    output_root = tmp_path / "shared-output"
    output_root.mkdir()
    output_root.chmod(0o1777)
    original_mode = _mode(output_root)
    monkeypatch.setattr(
        runner_module,
        "process_receipt_with_audit",
        lambda *args, **kwargs: (
            _private_process_result(),
            _process_audit(),
        ),
    )

    run_evaluation(
        source_root,
        output_root=output_root,
        base_config=_config(tmp_path / "production"),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        models=("qwen3-vl:8b",),
        run_id_factory=lambda: "run-existing-output",
    )

    assert original_mode == 0o1777
    assert _mode(output_root) == original_mode
    assert _mode(output_root / "run-existing-output") == 0o700


def test_run_evaluation_rejects_output_root_replaced_after_pinning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    production_root = tmp_path / "production"
    production_root.mkdir()
    marker = production_root / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    output_root = tmp_path / "output"
    detached_output = tmp_path / "detached-output"
    original_prepare = runner_module._prepare_output_root

    def replacing_prepare(*args: Any, **kwargs: Any) -> Any:
        pinned = original_prepare(*args, **kwargs)
        pinned.path.rename(detached_output)
        pinned.path.symlink_to(production_root, target_is_directory=True)
        return pinned

    monkeypatch.setattr(
        runner_module,
        "_prepare_output_root",
        replacing_prepare,
    )

    with pytest.raises(EvaluationRunError) as error:
        run_evaluation(
            source_root,
            output_root=output_root,
            base_config=_config(production_root),
            mode=IngestMode.REGULAR,
            reference_date=REFERENCE_DATE,
            models=("qwen3-vl:8b",),
            run_id_factory=lambda: "run-output-replaced",
        )

    assert error.value.code is EvaluationRunErrorCode.OUTPUT_CHANGED
    assert list(detached_output.iterdir()) == []
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (production_root / "run-output-replaced").exists()
    assert not (production_root / "ledger.db").exists()


def test_run_evaluation_rejects_run_root_replaced_before_model_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    production_root = tmp_path / "production"
    production_root.mkdir()
    marker = production_root / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    output_root = tmp_path / "output"
    detached_run_root = output_root / "detached-run"
    original_run_model = runner_module._run_model

    def replacing_run_model(*args: Any, **kwargs: Any) -> Any:
        pinned_run_root = kwargs["run_root"]
        pinned_run_root.path.rename(detached_run_root)
        pinned_run_root.path.symlink_to(
            production_root,
            target_is_directory=True,
        )
        return original_run_model(*args, **kwargs)

    monkeypatch.setattr(
        runner_module,
        "_run_model",
        replacing_run_model,
    )

    with pytest.raises(EvaluationRunError) as error:
        run_evaluation(
            source_root,
            output_root=output_root,
            base_config=_config(production_root),
            mode=IngestMode.REGULAR,
            reference_date=REFERENCE_DATE,
            models=("qwen3-vl:8b",),
            run_id_factory=lambda: "run-root-replaced",
        )

    assert error.value.code is EvaluationRunErrorCode.OUTPUT_CHANGED
    assert list(detached_run_root.iterdir()) == []
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (production_root / "model-01").exists()
    assert not (production_root / "ledger.db").exists()


def test_copy_case_collision_preserves_existing_destination_and_source(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    case = discover_cases(source_root)[0]
    source_snapshot = _source_snapshot(case.source_path)
    destination_root = tmp_path / "inputs"
    destination_root.mkdir()
    existing = destination_root / "case-0001.jpg"
    existing.write_bytes(b"existing")

    with pytest.raises(EvaluationRunError) as error:
        runner_module._copy_case(case, destination_root)

    assert error.value.code is EvaluationRunErrorCode.COPY_FAILED
    assert existing.read_bytes() == b"existing"
    assert _source_snapshot(case.source_path) == source_snapshot


def test_quality_report_temp_collision_preserves_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root_path = tmp_path / "run"
    run_root_path.mkdir()
    existing = run_root_path / ".quality-baseline-report-fixed.tmp"
    existing.write_bytes(b"existing")
    monkeypatch.setattr(
        runner_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )

    with (
        closing(
            runner_module._pin_existing_directory(
                run_root_path,
                error_code=EvaluationRunErrorCode.RUNTIME_UNAVAILABLE,
            )
        ) as run_root,
        pytest.raises(EvaluationRunError) as error,
    ):
        runner_module._write_quality_report(
            QualityBaselineReport(run_id="run-test", models=()),
            run_root,
        )

    assert error.value.code is EvaluationRunErrorCode.REPORT_WRITE_FAILED
    assert existing.read_bytes() == b"existing"
    assert not (run_root_path / "quality-baseline-report.json").exists()


def test_quality_report_final_collision_does_not_overwrite_or_leave_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root_path = tmp_path / "run"
    run_root_path.mkdir()
    existing = run_root_path / "quality-baseline-report.json"
    existing.write_bytes(b"existing")
    monkeypatch.setattr(
        runner_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )

    with (
        closing(
            runner_module._pin_existing_directory(
                run_root_path,
                error_code=EvaluationRunErrorCode.RUNTIME_UNAVAILABLE,
            )
        ) as run_root,
        pytest.raises(EvaluationRunError) as error,
    ):
        runner_module._write_quality_report(
            QualityBaselineReport(run_id="run-test", models=()),
            run_root,
        )

    assert error.value.code is EvaluationRunErrorCode.REPORT_WRITE_FAILED
    assert existing.read_bytes() == b"existing"
    assert not (run_root_path / ".quality-baseline-report-fixed.tmp").exists()


def _write_verified_truth(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TRUTH_CSV_HEADERS)
        writer.writeheader()
        writer.writerow(
            {
                "case_id": "case-0001",
                "human_verified": "true",
                "expected_store": f"{PRIVATE_SENTINEL}-store",
                "expected_date": "2026-07-25",
                "expected_total": "987654",
                "expected_status": "confirmed",
                "item_index": "0",
                "expected_item_name": f"{PRIVATE_SENTINEL}-item",
                "expected_item_qty": "2",
                "expected_item_price": "222",
            }
        )


def test_run_evaluation_reports_only_aggregate_ground_truth_accuracy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=1)
    truth_path = tmp_path / "truth.csv"
    _write_verified_truth(truth_path)

    monkeypatch.setattr(
        runner_module,
        "process_receipt_with_audit",
        lambda *args, **kwargs: (
            _private_process_result(
                store=f" {PRIVATE_SENTINEL}-STORE ",
                receipt_date="2026-07-25",
                total=987_654,
            ),
            _process_audit(),
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "_stored_items",
        lambda *args, **kwargs: (
            StoredItem(
                id=501,
                receipt_id=71,
                name=f"{PRIVATE_SENTINEL}-item",
                name_norm=None,
                qty=2,
                price=333,
                price_raw=333,
                tax_rate=None,
                tax_treatment=TaxTreatment.UNKNOWN,
                tax_adjustment=0,
                category=None,
            ),
            StoredItem(
                id=502,
                receipt_id=71,
                name=f"{PRIVATE_SENTINEL}-extra-item",
                name_norm=None,
                qty=1,
                price=444,
                price_raw=444,
                tax_rate=None,
                tax_treatment=TaxTreatment.UNKNOWN,
                tax_adjustment=0,
                category=None,
            ),
        ),
    )

    report = run_evaluation(
        source_root,
        output_root=tmp_path / "output",
        base_config=_config(tmp_path / "production"),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        ground_truth_path=truth_path,
        models=("qwen3-vl:8b",),
        clock=_StepClock(),
        run_id_factory=lambda: "run-accuracy",
    )

    accuracy = report.models[0].accuracy
    assert accuracy.status is AccuracyStatus.MEASURED
    assert accuracy.reason is None
    assert accuracy.verified_case_count == 1
    assert accuracy.store.model_dump() == {
        "comparable_count": 1,
        "correct_count": 0,
        "accuracy_rate": 0.0,
    }
    assert accuracy.date.correct_count == 1
    assert accuracy.total.correct_count == 1
    assert accuracy.receipt_status.correct_count == 1
    assert accuracy.item_name.correct_count == 1
    assert accuracy.item_name.comparable_count == 2
    assert accuracy.item_quantity.correct_count == 1
    assert accuracy.item_quantity.comparable_count == 2
    assert accuracy.item_price.model_dump() == {
        "comparable_count": 2,
        "correct_count": 0,
        "accuracy_rate": 0.0,
    }
    run_root = tmp_path / "output" / "run-accuracy"
    quality_report_path = run_root / "quality-baseline-report.json"
    quality_report_text = quality_report_path.read_text(encoding="utf-8")
    quality_report = QualityBaselineReport.model_validate_json(quality_report_text)
    quality_model = quality_report.models[0]
    quality = quality_model.quality
    assert quality_report.schema_version == 1
    assert quality_report.run_id == report.run_id
    assert quality_model.provenance.metric_version == "quality-v1"
    assert quality_model.provenance.model_name == "qwen3-vl:8b"
    assert quality.metric_version == "quality-v1"
    assert quality.verified_case_count == 1
    assert not quality.golden_set_complete
    assert quality.total_accuracy.rate == 1.0
    assert quality.store_accuracy.rate == 1.0
    assert quality.date_accuracy.rate == 1.0
    assert quality.item_accuracy.rate == 0.0
    assert quality.item_accuracy.denominator_count == 2
    assert quality.false_confirmation_rate.rate == 0.0
    assert quality.review_rate.rate == 0.0
    assert quality.q1_total.status is QualityAssessmentStatus.UNKNOWN
    assert quality.q1_total.reason is QualityUnknownReason.INCOMPLETE_GOLDEN_SET
    expected_schema_json = json.dumps(
        ReceiptExtraction.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert (
        quality_model.provenance.prompt_sha256
        == hashlib.sha256(EXTRACTION_PROMPT.encode("utf-8")).hexdigest()
    )
    assert (
        quality_model.provenance.extraction_schema_sha256
        == hashlib.sha256(expected_schema_json.encode("utf-8")).hexdigest()
    )
    report_text = (run_root / "evaluation-report.json").read_text(encoding="utf-8")
    report_payload = json.loads(report_text)
    assert report_payload["schema_version"] == 1
    _assert_safe_report_shape(report_payload)
    quality_payload = json.loads(quality_report_text)
    _assert_safe_quality_sidecar_shape(quality_payload)
    for forbidden in (
        PRIVATE_SENTINEL,
        "987654",
        "222",
        "333",
        "444",
        str(truth_path),
        str(source_root),
    ):
        assert forbidden not in report_text
        assert forbidden not in quality_report_text
    assert "case_id" not in quality_report_text
    assert "case-0001" not in quality_report_text


@pytest.mark.parametrize("unavailable_truth", ["missing", "unverified"])
def test_quality_sidecar_keeps_incomplete_30_case_truth_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unavailable_truth: str,
) -> None:
    source_root = tmp_path / "source"
    _write_cases(source_root, count=30)
    truth_path = tmp_path / "truth.csv"
    with truth_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TRUTH_CSV_HEADERS)
        writer.writeheader()
        for index in range(1, 31):
            if index == 30 and unavailable_truth == "missing":
                continue
            row = {field_name: "" for field_name in TRUTH_CSV_HEADERS}
            row["case_id"] = f"case-{index:04d}"
            if index == 30:
                row["human_verified"] = "false"
            else:
                row.update(
                    {
                        "human_verified": "true",
                        "expected_store": PRIVATE_SENTINEL,
                        "expected_date": "2026-07-25",
                        "expected_total": "987654",
                        "expected_status": "confirmed",
                        "item_index": "0",
                        "expected_item_name": f"{PRIVATE_SENTINEL}-item",
                        "expected_item_qty": "1",
                        "expected_item_price": "100",
                    }
                )
            writer.writerow(row)

    monkeypatch.setattr(
        runner_module,
        "process_receipt_with_audit",
        lambda *args, **kwargs: (_private_process_result(), _process_audit()),
    )
    monkeypatch.setattr(
        runner_module,
        "_stored_items",
        lambda *args, **kwargs: (
            StoredItem(
                id=501,
                receipt_id=71,
                name=f"{PRIVATE_SENTINEL}-item",
                name_norm=None,
                qty=1,
                price=100,
                price_raw=100,
                tax_rate=None,
                tax_treatment=TaxTreatment.UNKNOWN,
                tax_adjustment=0,
                category=None,
            ),
        ),
    )

    report = run_evaluation(
        source_root,
        output_root=tmp_path / "output",
        base_config=_config(tmp_path / "production"),
        mode=IngestMode.REGULAR,
        reference_date=REFERENCE_DATE,
        ground_truth_path=truth_path,
        models=("qwen3-vl:8b",),
        clock=_StepClock(),
        run_id_factory=lambda: f"run-{unavailable_truth}",
    )
    quality_report = QualityBaselineReport.model_validate_json(
        (
            tmp_path
            / "output"
            / f"run-{unavailable_truth}"
            / "quality-baseline-report.json"
        ).read_text(encoding="utf-8")
    )
    quality = quality_report.models[0].quality

    assert report.models[0].accuracy.verified_case_count == 29
    assert quality.target_case_count == 30
    assert quality.verified_case_count == 29
    assert not quality.golden_set_complete
    assert all(
        assessment.status is QualityAssessmentStatus.UNKNOWN
        and assessment.reason is QualityUnknownReason.INCOMPLETE_GOLDEN_SET
        for assessment in (
            quality.q1_total,
            quality.q2_store_and_date,
            quality.q3_items,
            quality.q4_false_confirmation,
            quality.q5_review,
        )
    )


def _quality_counts(
    *,
    verified: int = 30,
    total_correct: int = 30,
    store_correct: int = 30,
    date_correct: int = 30,
    item_comparable: int = 100,
    item_correct: int = 100,
    confirmed: int = 30,
    false_confirmed: int = 0,
) -> _QualityCounts:
    return _QualityCounts(
        verified_case_count=verified,
        store_correct_count=store_correct,
        date_correct_count=date_correct,
        total_correct_count=total_correct,
        item_comparable_count=item_comparable,
        item_correct_count=item_correct,
        confirmed_count=confirmed,
        false_confirmed_count=false_confirmed,
    )


def _quality_operational_summary(
    *,
    case_count: int = 30,
    review_count: int = 0,
    failed_count: int = 0,
) -> ModelEvaluationSummary:
    cases = tuple(
        CaseEvaluationResult(
            case_id=f"case-{index:04d}",
            processing_success=True,
            schema_outcome=SchemaOutcome.VALID,
            date_outcome=DateOutcome.UNCHANGED,
            tax_outcome=TaxOutcome.NOT_NEEDED,
            duplicate_outcome=DuplicateOutcome.NONE,
            status=(
                EvaluationStatus.REVIEW
                if index <= review_count
                else (
                    EvaluationStatus.FAILED
                    if index <= review_count + failed_count
                    else EvaluationStatus.CONFIRMED
                )
            ),
            elapsed_ms=1,
        )
        for index in range(1, case_count + 1)
    )
    return runner_module._summarize(cases)


def test_quality_v1_marks_all_targets_met_at_their_boundaries() -> None:
    quality = runner_module._quality_baseline(
        _quality_counts(
            total_correct=30,
            store_correct=29,
            date_correct=29,
            item_correct=80,
            confirmed=21,
            false_confirmed=0,
        ),
        _quality_operational_summary(review_count=9),
    )

    assert quality.golden_set_complete
    assert quality.q1_total.status is QualityAssessmentStatus.MET
    assert quality.q2_store_and_date.status is QualityAssessmentStatus.MET
    assert quality.q3_items.status is QualityAssessmentStatus.MET
    assert quality.q4_false_confirmation.status is QualityAssessmentStatus.MET
    assert quality.q5_review.status is QualityAssessmentStatus.MET


@pytest.mark.parametrize(
    ("counts", "review_count", "field_name"),
    [
        (
            _quality_counts(total_correct=29, false_confirmed=1),
            0,
            "q1_total",
        ),
        (_quality_counts(store_correct=28), 0, "q2_store_and_date"),
        (_quality_counts(date_correct=28), 0, "q2_store_and_date"),
        (_quality_counts(item_correct=79), 0, "q3_items"),
        (
            _quality_counts(total_correct=29, confirmed=30, false_confirmed=1),
            0,
            "q4_false_confirmation",
        ),
        (_quality_counts(confirmed=20), 10, "q5_review"),
    ],
)
def test_quality_v1_marks_each_threshold_miss_not_met(
    counts: _QualityCounts,
    review_count: int,
    field_name: str,
) -> None:
    quality = runner_module._quality_baseline(
        counts,
        _quality_operational_summary(review_count=review_count),
    )

    assert getattr(quality, field_name).status is QualityAssessmentStatus.NOT_MET


def test_quality_v1_keeps_incomplete_truth_and_zero_confirmed_unknown() -> None:
    incomplete = runner_module._quality_baseline(
        _quality_counts(
            verified=29,
            total_correct=29,
            store_correct=29,
            date_correct=29,
            confirmed=29,
        ),
        _quality_operational_summary(),
    )
    assert not incomplete.golden_set_complete
    assert all(
        assessment.status is QualityAssessmentStatus.UNKNOWN
        and assessment.reason is QualityUnknownReason.INCOMPLETE_GOLDEN_SET
        for assessment in (
            incomplete.q1_total,
            incomplete.q2_store_and_date,
            incomplete.q3_items,
            incomplete.q4_false_confirmation,
            incomplete.q5_review,
        )
    )

    no_confirmed = runner_module._quality_baseline(
        _quality_counts(confirmed=0),
        _quality_operational_summary(review_count=0, failed_count=30),
    )
    assert no_confirmed.false_confirmation_rate.denominator_count == 0
    assert no_confirmed.false_confirmation_rate.rate is None
    assert no_confirmed.q4_false_confirmation.status is QualityAssessmentStatus.UNKNOWN
    assert (
        no_confirmed.q4_false_confirmation.reason
        is QualityUnknownReason.ZERO_DENOMINATOR
    )
    assert no_confirmed.review_rate.numerator_count == 0
    assert no_confirmed.review_rate.rate == 0.0
    assert no_confirmed.q5_review.status is QualityAssessmentStatus.MET
