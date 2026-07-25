from __future__ import annotations

from datetime import date, timedelta

from pydantic import ValidationError

from recebako.domain import (
    ReceiptExtraction,
    ReceiptStatus,
    ValidationIssue,
    ValidationResult,
)

MINIMUM_CONFIDENCE = 0.8
TOTAL_TOLERANCE_YEN = 2
MAX_RECEIPT_AGE_DAYS = 365


def _issue(code: str, message: str, field: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, field=field)


def _parse_receipt_date(value: str) -> date | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def validate_receipt(
    receipt: ReceiptExtraction,
    *,
    reference_date: date,
) -> ValidationResult:
    issues: list[ValidationIssue] = []

    if receipt.total <= 0:
        issues.append(
            _issue(
                "total.non_positive",
                "合計金額は0円より大きい必要があります",
                "total",
            )
        )

    if not receipt.store.strip():
        issues.append(
            _issue(
                "store.empty",
                "店名が空です",
                "store",
            )
        )

    receipt_date = _parse_receipt_date(receipt.date)
    if receipt_date is None:
        issues.append(
            _issue(
                "date.invalid",
                "日付が実在するYYYY-MM-DD形式ではありません",
                "date",
            )
        )
    elif receipt_date > reference_date:
        issues.append(
            _issue(
                "date.future",
                "日付が未来です",
                "date",
            )
        )
    elif receipt_date < reference_date - timedelta(days=MAX_RECEIPT_AGE_DAYS):
        issues.append(
            _issue(
                "date.too_old",
                "日付が実行日から過去1年の範囲外です",
                "date",
            )
        )

    if receipt.confidence < MINIMUM_CONFIDENCE:
        issues.append(
            _issue(
                "confidence.low",
                "読み取り自信度が0.8未満です",
                "confidence",
            )
        )

    if not receipt.items:
        issues.append(
            _issue(
                "items.empty",
                "品目が1件もありません",
                "items",
            )
        )

    item_total = sum(item.price for item in receipt.items)
    if abs(item_total - receipt.total) > TOTAL_TOLERANCE_YEN:
        issues.append(
            _issue(
                "total.mismatch",
                "品目金額の合計とレシート合計の差が2円を超えています",
                "total",
            )
        )

    status = ReceiptStatus.REVIEW if issues else ReceiptStatus.CONFIRMED
    return ValidationResult(status=status, issues=issues)


def validate_receipt_payload(
    payload: str | bytes | bytearray,
    *,
    reference_date: date,
) -> tuple[ReceiptExtraction | None, ValidationResult]:
    try:
        receipt = ReceiptExtraction.model_validate_json(payload)
    except ValidationError:
        return None, ValidationResult(
            status=ReceiptStatus.FAILED,
            issues=[
                _issue(
                    "structure.invalid",
                    "Ollama応答をレシートスキーマとして検証できませんでした",
                    "$",
                )
            ],
        )

    return receipt, validate_receipt(receipt, reference_date=reference_date)
