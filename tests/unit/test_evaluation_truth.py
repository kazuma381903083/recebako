from __future__ import annotations

import csv
import os
import traceback
from pathlib import Path

import pytest

import recebako.evaluation.truth as truth_module
from recebako.evaluation.models import EvaluationStatus
from recebako.evaluation.truth import (
    TRUTH_CSV_HEADERS,
    GroundTruthError,
    TruthErrorCode,
    load_ground_truth_csv,
)


def _write_truth_csv(
    path: Path,
    rows: list[dict[str, str]],
    *,
    fieldnames: tuple[str, ...] = TRUTH_CSV_HEADERS,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _verified_row(
    *,
    case_id: str = "case-0001",
    item_index: str = "0",
) -> dict[str, str]:
    return {
        "case_id": case_id,
        "human_verified": "true",
        "expected_store": "expected-store",
        "expected_date": "2026-07-01",
        "expected_total": "300",
        "expected_status": "confirmed",
        "item_index": item_index,
        "expected_item_name": f"expected-item-{item_index}",
        "expected_item_qty": "1",
        "expected_item_price": "150",
    }


def _unverified_row(*, case_id: str = "case-0002") -> dict[str, str]:
    return {
        "case_id": case_id,
        "human_verified": "false",
        "expected_store": "",
        "expected_date": "",
        "expected_total": "",
        "expected_status": "",
        "item_index": "",
        "expected_item_name": "",
        "expected_item_qty": "",
        "expected_item_price": "",
    }


def test_load_ground_truth_csv_parses_verified_items_and_unverified_case(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    first = _verified_row()
    second = _verified_row(item_index="1")
    _write_truth_csv(path, [second, first, _unverified_row()])

    truth = load_ground_truth_csv(path, {"case-0001", "case-0002", "case-0003"})

    verified = truth.get("case-0001")
    assert verified is not None
    assert verified.human_verified is True
    assert verified.expected_store == "expected-store"
    assert verified.expected_date is not None
    assert verified.expected_date.isoformat() == "2026-07-01"
    assert verified.expected_total == 300
    assert verified.expected_status is EvaluationStatus.CONFIRMED
    assert [item.item_index for item in verified.items] == [0, 1]
    assert truth.get("case-0002") is not None
    assert truth.get("case-0002").human_verified is False  # type: ignore[union-attr]
    assert truth.get("case-0002").items == ()  # type: ignore[union-attr]
    assert truth.get("case-0003") is None
    assert truth.verified_cases == (verified,)


@pytest.mark.parametrize(
    "fieldnames",
    [
        TRUTH_CSV_HEADERS[:-1],
        (*TRUTH_CSV_HEADERS, "unexpected"),
        (
            "case_id",
            "case_id",
            *TRUTH_CSV_HEADERS[2:],
        ),
        (
            TRUTH_CSV_HEADERS[1],
            TRUTH_CSV_HEADERS[0],
            *TRUTH_CSV_HEADERS[2:],
        ),
    ],
)
def test_load_ground_truth_csv_rejects_non_contract_headers(
    tmp_path: Path,
    fieldnames: tuple[str, ...],
) -> None:
    path = tmp_path / "truth.csv"
    _write_truth_csv(path, [], fieldnames=fieldnames)

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.INVALID_HEADER


def test_load_ground_truth_csv_rejects_an_unknown_case_without_leaking_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    private_sentinel = "PRIVATE-SENTINEL"
    _write_truth_csv(path, [_verified_row(case_id=private_sentinel)])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.UNKNOWN_CASE_ID
    assert private_sentinel not in str(captured.value)


def test_load_ground_truth_csv_rejects_inconsistent_repeated_receipt_fields_safely(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    private_sentinel = "PRIVATE-SENTINEL"
    first = _verified_row()
    second = _verified_row(item_index="1")
    second["expected_store"] = private_sentinel
    _write_truth_csv(path, [first, second])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.INCONSISTENT_CASE
    assert private_sentinel not in str(captured.value)


def test_load_ground_truth_csv_rejects_duplicate_item_index(tmp_path: Path) -> None:
    path = tmp_path / "truth.csv"
    _write_truth_csv(path, [_verified_row(), _verified_row()])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.DUPLICATE_ITEM


def test_load_ground_truth_csv_rejects_non_contiguous_item_indexes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    _write_truth_csv(path, [_verified_row(item_index="1")])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.NON_CONTIGUOUS_ITEMS


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("human_verified", "yes"),
        ("expected_store", ""),
        ("expected_date", "2026-02-30"),
        ("expected_total", "0"),
        ("expected_total", "0300"),
        ("expected_status", "not_evaluated"),
        ("expected_status", "other"),
        ("item_index", "-1"),
        ("expected_item_name", ""),
        ("expected_item_qty", "0"),
        ("expected_item_price", "1.5"),
    ],
)
def test_load_ground_truth_csv_rejects_invalid_verified_values_without_leakage(
    tmp_path: Path,
    field: str,
    invalid_value: str,
) -> None:
    path = tmp_path / "truth.csv"
    row = _verified_row()
    row[field] = invalid_value
    _write_truth_csv(path, [row])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.INVALID_ROW
    if invalid_value:
        assert invalid_value not in str(captured.value)


def test_load_ground_truth_csv_rejects_multiple_unverified_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    _write_truth_csv(path, [_unverified_row(), _unverified_row()])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0002"})

    assert captured.value.code is TruthErrorCode.DUPLICATE_ITEM


def test_load_ground_truth_csv_rejects_expected_values_on_unverified_case(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    row = _unverified_row()
    row["expected_store"] = "AI-output-must-not-be-ground-truth"
    _write_truth_csv(path, [row])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0002"})

    assert captured.value.code is TruthErrorCode.INVALID_ROW
    assert row["expected_store"] not in str(captured.value)


def test_load_ground_truth_csv_rejects_mixed_verification_flags(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truth.csv"
    first = _verified_row()
    second = _verified_row(item_index="1")
    second["human_verified"] = "false"
    _write_truth_csv(path, [first, second])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.INCONSISTENT_CASE


def test_load_ground_truth_csv_rejects_empty_data(tmp_path: Path) -> None:
    path = tmp_path / "truth.csv"
    _write_truth_csv(path, [])

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.EMPTY


def test_load_ground_truth_csv_requires_absolute_regular_file(tmp_path: Path) -> None:
    with pytest.raises(GroundTruthError) as relative:
        load_ground_truth_csv(Path("truth.csv"), {"case-0001"})
    assert relative.value.code is TruthErrorCode.ABSOLUTE_PATH_REQUIRED

    with pytest.raises(GroundTruthError) as directory:
        load_ground_truth_csv(tmp_path, {"case-0001"})
    assert directory.value.code is TruthErrorCode.FILE_REQUIRED


def test_load_ground_truth_csv_hides_an_unavailable_path(tmp_path: Path) -> None:
    private_sentinel = "PRIVATE-SENTINEL"
    path = tmp_path / f"{private_sentinel}.csv"

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert captured.value.code is TruthErrorCode.SOURCE_UNAVAILABLE
    assert private_sentinel not in rendered


def test_load_ground_truth_csv_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.csv"
    _write_truth_csv(target, [_verified_row()])
    link = tmp_path / "truth.csv"
    link.symlink_to(target)

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(link, {"case-0001"})

    assert captured.value.code is TruthErrorCode.SYMLINK_REJECTED


def test_load_ground_truth_csv_rejects_file_changed_while_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "truth.csv"
    _write_truth_csv(path, [_verified_row()])
    original_identity = truth_module._truth_identity
    identity_calls = 0

    def changing_identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int]:
        nonlocal identity_calls
        identity_calls += 1
        identity = original_identity(value)
        if identity_calls == 3:
            return (*identity[:-1], identity[-1] + 1)
        return identity

    monkeypatch.setattr(truth_module, "_truth_identity", changing_identity)

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(path, {"case-0001"})

    assert captured.value.code is TruthErrorCode.SOURCE_UNAVAILABLE


def test_load_ground_truth_csv_rejects_current_git_worktree_file() -> None:
    tracked_file = Path(__file__).resolve()

    with pytest.raises(GroundTruthError) as captured:
        load_ground_truth_csv(tracked_file, {"case-0001"})

    assert captured.value.code is TruthErrorCode.SOURCE_IN_GIT
