from __future__ import annotations

from datetime import date, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, ValidationError

from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ReceiptExtraction,
    ReceiptStatus,
    ValidationIssue,
    ValidationResult,
)
from recebako.normalization import (
    TaxNormalizationAudit,
    TaxNormalizationReason,
    normalize_item_taxes_with_audit,
    normalize_receipt_date,
)

MINIMUM_CONFIDENCE = 0.8
TOTAL_TOLERANCE_YEN = 2
MAX_RECEIPT_AGE_DAYS = 365


class SchemaOutcome(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    VALID = "valid"
    INVALID = "invalid"


class DateNormalizationOutcome(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    UNCHANGED = "unchanged"
    NORMALIZED = "normalized"
    REJECTED = "rejected"


class ValidationAudit(BaseModel):
    """Private receipt values must never be added to this safe audit model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    schema_outcome: SchemaOutcome
    date_normalization_outcome: DateNormalizationOutcome
    tax_normalization_reason: TaxNormalizationReason | None


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


def normalize_receipt(
    receipt: ReceiptExtraction,
) -> NormalizedReceiptExtraction:
    normalized_receipt, _ = _normalize_receipt_with_tax_audit(receipt)
    return normalized_receipt


def _normalize_receipt_with_tax_audit(
    receipt: ReceiptExtraction,
) -> tuple[NormalizedReceiptExtraction, TaxNormalizationAudit]:
    normalized_receipt, tax_audit, _ = _normalize_receipt_with_audit(receipt)
    return normalized_receipt, tax_audit


def _normalize_receipt_with_audit(
    receipt: ReceiptExtraction,
) -> tuple[
    NormalizedReceiptExtraction,
    TaxNormalizationAudit,
    DateNormalizationOutcome,
]:
    normalization = normalize_receipt_date(receipt.date)
    tax_normalization = normalize_item_taxes_with_audit(
        list(receipt.items),
        receipt.tax_breakdowns,
        total=receipt.total,
    )
    data = receipt.model_dump(mode="python")
    data["items"] = [item.model_dump(mode="python") for item in tax_normalization.items]
    data["date_raw"] = normalization.raw
    data["date"] = normalization.normalized or ""
    if normalization.normalized is None:
        date_outcome = DateNormalizationOutcome.REJECTED
    elif normalization.normalized == normalization.raw:
        date_outcome = DateNormalizationOutcome.UNCHANGED
    else:
        date_outcome = DateNormalizationOutcome.NORMALIZED
    return (
        NormalizedReceiptExtraction.model_validate(data),
        tax_normalization.audit,
        date_outcome,
    )


def _tax_normalization_issue(
    audit: TaxNormalizationAudit,
) -> ValidationIssue | None:
    if (
        audit.applied
        or audit.reason is TaxNormalizationReason.NOT_NEEDED
        or not audit.evidence_present
    ):
        return None

    issue_details = {
        TaxNormalizationReason.MISSING_EVIDENCE: (
            "tax.normalization.missing_evidence",
            "外税補正に必要な明示的な税内訳がありません",
        ),
        TaxNormalizationReason.INCONSISTENT_INPUT: (
            "tax.normalization.inconsistent",
            "税内訳に矛盾する値があるため外税補正を拒否しました",
        ),
        TaxNormalizationReason.NO_MATCH: (
            "tax.normalization.no_match",
            "税対象額と一致する安全な品目割当がありません",
        ),
        TaxNormalizationReason.AMBIGUOUS: (
            "tax.normalization.ambiguous",
            "外税補正の最良候補が一意に決まらないため補正を拒否しました",
        ),
        TaxNormalizationReason.TOTAL_MISMATCH: (
            "tax.normalization.total_mismatch",
            "外税候補を適用してもレシート合計と一致しません",
        ),
        TaxNormalizationReason.SEARCH_LIMIT: (
            "tax.normalization.search_limit",
            "外税補正の探索上限に達したため補正を拒否しました",
        ),
        TaxNormalizationReason.GROUP_LIMIT: (
            "tax.normalization.search_limit",
            "外税グループ数が探索上限を超えたため補正を拒否しました",
        ),
        TaxNormalizationReason.ALLOCATION_FAILED: (
            "tax.normalization.allocation_failed",
            "外税額を安全に配賦できないため補正を拒否しました",
        ),
    }
    details = issue_details.get(audit.reason)
    if details is None:
        return None
    code, message = details
    return _issue(code, message, "tax_breakdowns")


def _validate_normalized_receipt(
    receipt: NormalizedReceiptExtraction,
    *,
    reference_date: date,
    mode: IngestMode,
    tax_audit: TaxNormalizationAudit | None = None,
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
    elif mode is IngestMode.REGULAR and receipt_date < reference_date - timedelta(
        days=MAX_RECEIPT_AGE_DAYS
    ):
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
    if tax_audit is not None:
        tax_issue = _tax_normalization_issue(tax_audit)
        if tax_issue is not None:
            issues.append(tax_issue)
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


def validate_receipt(
    receipt: ReceiptExtraction,
    *,
    reference_date: date,
    mode: IngestMode = IngestMode.REGULAR,
) -> ValidationResult:
    normalized_receipt, tax_audit = _normalize_receipt_with_tax_audit(receipt)
    return _validate_normalized_receipt(
        normalized_receipt,
        reference_date=reference_date,
        mode=mode,
        tax_audit=tax_audit,
    )


def validate_receipt_payload(
    payload: str | bytes | bytearray,
    *,
    reference_date: date,
    mode: IngestMode = IngestMode.REGULAR,
) -> tuple[NormalizedReceiptExtraction | None, ValidationResult]:
    receipt, validation, _ = validate_receipt_payload_with_audit(
        payload,
        reference_date=reference_date,
        mode=mode,
    )
    return receipt, validation


def validate_receipt_payload_with_audit(
    payload: str | bytes | bytearray,
    *,
    reference_date: date,
    mode: IngestMode = IngestMode.REGULAR,
) -> tuple[
    NormalizedReceiptExtraction | None,
    ValidationResult,
    ValidationAudit,
]:
    try:
        receipt = ReceiptExtraction.model_validate_json(payload)
    except ValidationError:
        return (
            None,
            ValidationResult(
                status=ReceiptStatus.FAILED,
                issues=[
                    _issue(
                        "structure.invalid",
                        "Ollama応答をレシートスキーマとして検証できませんでした",
                        "$",
                    )
                ],
            ),
            ValidationAudit(
                schema_outcome=SchemaOutcome.INVALID,
                date_normalization_outcome=(DateNormalizationOutcome.NOT_EVALUATED),
                tax_normalization_reason=None,
            ),
        )

    normalized_receipt, tax_audit, date_outcome = _normalize_receipt_with_audit(receipt)
    return (
        normalized_receipt,
        _validate_normalized_receipt(
            normalized_receipt,
            reference_date=reference_date,
            mode=mode,
            tax_audit=tax_audit,
        ),
        ValidationAudit(
            schema_outcome=SchemaOutcome.VALID,
            date_normalization_outcome=date_outcome,
            tax_normalization_reason=tax_audit.reason,
        ),
    )
