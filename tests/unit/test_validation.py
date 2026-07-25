from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

import recebako.normalization.tax as tax_module
from recebako.domain import IngestMode, ReceiptExtraction, ReceiptStatus
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


def test_payload_date_is_normalized_and_raw_value_is_retained() -> None:
    raw_receipt = _receipt(date="2026/7/25")

    receipt, result = validate_receipt_payload(
        raw_receipt.model_dump_json(),
        reference_date=REFERENCE_DATE,
    )

    assert receipt is not None
    assert receipt.date_raw == "2026/7/25"
    assert receipt.date == "2026-07-25"
    assert result.status is ReceiptStatus.CONFIRMED


def test_regular_mode_reviews_date_older_than_365_days() -> None:
    result = validate_receipt(
        _receipt(date="2020/1/1"),
        reference_date=REFERENCE_DATE,
        mode=IngestMode.REGULAR,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {"date.too_old"}


def test_historical_mode_allows_old_date() -> None:
    result = validate_receipt(
        _receipt(date="2020/1/1"),
        reference_date=REFERENCE_DATE,
        mode=IngestMode.HISTORICAL,
    )

    assert result.status is ReceiptStatus.CONFIRMED
    assert result.issues == []


def test_historical_mode_still_reviews_future_date() -> None:
    result = validate_receipt(
        _receipt(date="2026/7/26"),
        reference_date=REFERENCE_DATE,
        mode=IngestMode.HISTORICAL,
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


def test_mixed_external_and_internal_tax_is_confirmed_after_normalization() -> None:
    raw_receipt = _receipt(
        items=[
            {
                "name": "外税商品",
                "price": 140,
                "price_raw": 140,
                "tax_rate": 8,
                "tax_treatment": "excluded",
            },
            {
                "name": "内税商品",
                "price": 570,
                "price_raw": 570,
                "tax_rate": 10,
                "tax_treatment": "included",
            },
        ],
        tax_breakdowns=[
            {
                "tax_rate": 8,
                "taxable_amount": 140,
                "tax_amount": 11,
                "tax_treatment": "excluded",
            },
            {
                "tax_rate": 10,
                "taxable_amount": 570,
                "tax_amount": 51,
                "tax_treatment": "included",
            },
        ],
        total=721,
    )

    receipt, result = validate_receipt_payload(
        raw_receipt.model_dump_json(),
        reference_date=REFERENCE_DATE,
    )

    assert receipt is not None
    assert [item.price for item in receipt.items] == [151, 570]
    assert [item.price_raw for item in receipt.items] == [140, 570]
    assert [item.tax_adjustment for item in receipt.items] == [11, 0]
    assert result.status is ReceiptStatus.CONFIRMED
    assert result.issues == []


def test_missing_external_tax_breakdown_remains_review() -> None:
    result = validate_receipt(
        _receipt(
            items=[
                {
                    "name": "外税商品",
                    "price": 140,
                    "price_raw": 140,
                    "tax_rate": 8,
                    "tax_treatment": "excluded",
                },
                {
                    "name": "内税商品",
                    "price": 570,
                    "price_raw": 570,
                    "tax_rate": 10,
                    "tax_treatment": "included",
                },
            ],
            total=721,
        ),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {
        "tax.normalization.missing_evidence",
        "total.mismatch",
    }


def test_tax_normalization_refusal_reason_is_auditable() -> None:
    result = validate_receipt(
        _receipt(
            items=[
                {
                    "name": "外税商品",
                    "price": 100,
                    "price_raw": 100,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                }
            ],
            tax_breakdowns=[
                {
                    "tax_rate": 8,
                    "taxable_amount": -100,
                    "tax_amount": 8,
                    "tax_treatment": "excluded",
                }
            ],
            total=108,
        ),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {
        "tax.normalization.inconsistent",
        "total.mismatch",
    }


def test_tax_normalization_requires_exact_corrected_total() -> None:
    result = validate_receipt(
        _receipt(
            items=[
                {
                    "name": "外税商品",
                    "price": 100,
                    "price_raw": 100,
                    "tax_rate": 8,
                    "tax_treatment": "excluded",
                }
            ],
            tax_breakdowns=[
                {
                    "tax_rate": 8,
                    "taxable_amount": 100,
                    "tax_amount": 8,
                    "tax_treatment": "excluded",
                }
            ],
            total=109,
        ),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {
        "tax.normalization.total_mismatch",
        "total.mismatch",
    }


def test_tax_rejection_is_review_even_within_business_total_tolerance() -> None:
    result = validate_receipt(
        _receipt(
            items=[
                {
                    "name": "外税商品",
                    "price": 100,
                    "price_raw": 100,
                    "tax_rate": 8,
                    "tax_treatment": "excluded",
                }
            ],
            tax_breakdowns=[
                {
                    "tax_rate": 8,
                    "taxable_amount": 100,
                    "tax_amount": 8,
                    "tax_treatment": "excluded",
                }
            ],
            total=102,
        ),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {
        "tax.normalization.total_mismatch"
    }


def test_tax_search_limit_becomes_review_instead_of_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tax_module, "MAX_SUBSET_SUM_STATES", 2)

    result = validate_receipt(
        _receipt(
            items=[
                {
                    "name": f"外税商品{index}",
                    "price": 50,
                    "price_raw": 50,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                }
                for index in range(3)
            ],
            tax_breakdowns=[
                {
                    "tax_rate": 8,
                    "taxable_amount": 100,
                    "tax_amount": 8,
                    "tax_treatment": "excluded",
                }
            ],
            total=158,
        ),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {
        "tax.normalization.search_limit",
        "total.mismatch",
    }


def test_unique_subtotals_resolve_multiple_external_tax_rates() -> None:
    raw_receipt = _receipt(
        date="2022年05月14日",
        items=[
            {
                "name": "8%商品A",
                "price": 600,
                "price_raw": 600,
                "tax_rate": None,
                "tax_treatment": "excluded",
            },
            {
                "name": "8%商品B",
                "price": 148,
                "price_raw": 148,
                "tax_rate": None,
                "tax_treatment": "excluded",
            },
            {
                "name": "10%内税商品A",
                "price": 5800,
                "price_raw": 5800,
                "tax_rate": None,
                "tax_treatment": "included",
            },
            {
                "name": "10%内税商品B",
                "price": 5800,
                "price_raw": 5800,
                "tax_rate": None,
                "tax_treatment": "included",
            },
            {
                "name": "10%外税商品",
                "price": 3,
                "price_raw": 3,
                "tax_rate": None,
                "tax_treatment": "excluded",
            },
        ],
        tax_breakdowns=[
            {
                "tax_rate": 8,
                "taxable_amount": 748,
                "tax_amount": 59,
                "tax_treatment": "excluded",
            },
            {
                "tax_rate": 10,
                "taxable_amount": 3,
                "tax_amount": 3,
                "tax_treatment": "excluded",
            },
        ],
        total=12410,
    )

    receipt, result = validate_receipt_payload(
        raw_receipt.model_dump_json(),
        reference_date=REFERENCE_DATE,
        mode=IngestMode.HISTORICAL,
    )

    assert receipt is not None
    assert [item.price for item in receipt.items] == [647, 160, 5800, 5800, 3]
    assert [item.tax_rate for item in receipt.items] == [8, 8, None, None, None]
    assert [item.tax_adjustment for item in receipt.items] == [47, 12, 0, 0, 0]
    assert result.status is ReceiptStatus.CONFIRMED
    assert result.issues == []


def test_ambiguous_subtotal_match_remains_review() -> None:
    result = validate_receipt(
        _receipt(
            items=[
                {
                    "name": "同額商品A",
                    "price": 100,
                    "price_raw": 100,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                },
                {
                    "name": "同額商品B",
                    "price": 100,
                    "price_raw": 100,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                },
            ],
            tax_breakdowns=[
                {
                    "tax_rate": 8,
                    "taxable_amount": 100,
                    "tax_amount": 8,
                    "tax_treatment": "excluded",
                }
            ],
            total=208,
        ),
        reference_date=REFERENCE_DATE,
    )

    assert result.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.issues} == {
        "tax.normalization.ambiguous",
        "total.mismatch",
    }


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
