from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from recebako.domain import ReceiptExtraction, ReceiptStatus
from recebako.validation import validate_receipt, validate_receipt_payload

REFERENCE_DATE = date(2026, 7, 25)


def _receipt(**overrides: Any) -> ReceiptExtraction:
    data: dict[str, Any] = {
        "store": "テスト商店",
        "date": "2026-07-25",
        "time": "12:34",
        "items": [{"name": "商品", "qty": 1, "price": 100}],
        "subtotal": 91,
        "tax": 9,
        "total": 100,
        "payment": "cash",
        "confidence": 0.95,
    }
    data.update(overrides)
    return ReceiptExtraction.model_validate(data)


def _issue_codes(receipt: ReceiptExtraction) -> set[str]:
    result = validate_receipt(receipt, reference_date=REFERENCE_DATE)
    return {issue.code for issue in result.issues}


def test_matching_total_is_confirmed() -> None:
    result = validate_receipt(_receipt(), reference_date=REFERENCE_DATE)

    assert result.status is ReceiptStatus.CONFIRMED
    assert result.issues == []


def test_mismatched_total_is_review() -> None:
    result = validate_receipt(
        _receipt(total=104),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {"total.mismatch"}


def test_two_yen_total_difference_is_confirmed() -> None:
    result = validate_receipt(
        _receipt(total=102),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.CONFIRMED
    assert result.issues == []


def test_low_confidence_is_review() -> None:
    result = validate_receipt(
        _receipt(confidence=0.79),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {"confidence.low"}


def test_future_date_is_review() -> None:
    result = validate_receipt(
        _receipt(date="2026-07-26"),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {"date.future"}


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        json.dumps(
            {
                "store": "テスト商店",
                "date": "2026-07-25",
                "items": [],
                "confidence": 0.9,
            }
        ),
    ],
)
def test_invalid_json_or_structure_is_failed(payload: str) -> None:
    receipt, result = validate_receipt_payload(
        payload,
        reference_date=REFERENCE_DATE,
    )

    assert receipt is None
    assert result.status is ReceiptStatus.FAILED
    assert {issue.code for issue in result.issues} == {"structure.invalid"}


def test_discount_line_is_included_as_negative_amount() -> None:
    receipt = _receipt(
        items=[
            {"name": "商品", "qty": 1, "price": 1000},
            {"name": "値引き", "qty": 1, "price": -100},
        ],
        total=900,
    )

    result = validate_receipt(receipt, reference_date=REFERENCE_DATE)

    assert result.status is ReceiptStatus.CONFIRMED
    assert result.issues == []


@pytest.mark.parametrize(
    ("receipt", "expected_code"),
    [
        (
            _receipt(total=0, items=[{"name": "無料品", "price": 0}]),
            "total.non_positive",
        ),
        (_receipt(store=" \t"), "store.empty"),
        (_receipt(date="2026-02-30"), "date.invalid"),
        (_receipt(date="2025-07-24"), "date.too_old"),
        (_receipt(items=[]), "items.empty"),
    ],
)
def test_other_business_rule_violations_are_reported(
    receipt: ReceiptExtraction,
    expected_code: str,
) -> None:
    result = validate_receipt(receipt, reference_date=REFERENCE_DATE)

    assert result.status is ReceiptStatus.REVIEW
    assert expected_code in _issue_codes(receipt)
