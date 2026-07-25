from __future__ import annotations

import csv
import json
import os
import stat
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import recebako.evaluation.runner as runner_module
from recebako.ai import OllamaTimeoutError
from recebako.config import AppConfig
from recebako.domain import (
    IngestMode,
    ReceiptStatus,
    TaxTreatment,
    ValidationIssue,
)
from recebako.evaluation import (
    AccuracyStatus,
    AccuracyUnknownReason,
    DateOutcome,
    DuplicateOutcome,
    EvaluationStatus,
    SchemaOutcome,
    TaxOutcome,
)
from recebako.evaluation.dataset import discover_cases
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
        assert set(model["summary"]) == {
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
        assert set(model["summary"]["duration"]) == {
            "sample_count",
            "total_ms",
            "minimum_ms",
            "maximum_ms",
            "mean_ms",
        }
        assert set(model["accuracy"]) == {
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
            assert set(model["accuracy"][field_name]) == {
                "comparable_count",
                "correct_count",
                "accuracy_rate",
            }


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

    def fake_process(
        image_path: Path,
        *,
        config: AppConfig,
        storage_image_path: Path,
        temporary_root: Path,
        **kwargs: Any,
    ) -> tuple[ProcessResult, ProcessAudit]:
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
        base_config=_config(production_root),
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
    assert payload == report.model_dump(mode="json")
    _assert_safe_report_shape(payload)
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
    assert all(
        model.accuracy.status is AccuracyStatus.UNKNOWN
        and model.accuracy.reason
        is AccuracyUnknownReason.NO_HUMAN_VERIFIED_GROUND_TRUTH
        for model in report.models
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
                store=f"{PRIVATE_SENTINEL}-store",
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
        "correct_count": 1,
        "accuracy_rate": 1.0,
    }
    assert accuracy.date.correct_count == 1
    assert accuracy.total.correct_count == 1
    assert accuracy.receipt_status.correct_count == 1
    assert accuracy.item_name.correct_count == 1
    assert accuracy.item_quantity.correct_count == 1
    assert accuracy.item_price.model_dump() == {
        "comparable_count": 1,
        "correct_count": 0,
        "accuracy_rate": 0.0,
    }
    report_text = (
        tmp_path / "output" / "run-accuracy" / "evaluation-report.json"
    ).read_text(encoding="utf-8")
    for forbidden in (
        PRIVATE_SENTINEL,
        "987654",
        "222",
        "333",
        str(truth_path),
    ):
        assert forbidden not in report_text
