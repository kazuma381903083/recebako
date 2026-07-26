from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from recebako.ai import OllamaTimeoutError
from recebako.domain import (
    IngestMode,
    NormalizedReceiptExtraction,
    ValidationResult,
)
from recebako.imaging import ImagePreprocessError
from recebako.validation import (
    SchemaOutcome,
    ValidationAudit,
    validate_receipt_payload_with_audit,
)

MAX_EXTRACTION_ATTEMPTS = 3


class PreparedExtractionImage(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def phash(self) -> str: ...


@dataclass(frozen=True)
class VariantExtractionResult:
    """Accepted or final schema-invalid extraction without attempt history."""

    raw_payload: str = field(repr=False)
    extraction: NormalizedReceiptExtraction | None = field(repr=False)
    validation: ValidationResult
    validation_audit: ValidationAudit
    phash: str


class ExtractionVariantError(ImagePreprocessError):
    """The internal image variant sequence did not satisfy its contract."""


def extract_with_variant_retry(
    variants: Iterable[PreparedExtractionImage],
    *,
    request: Callable[[Path], str],
    reference_date: date,
    mode: IngestMode,
) -> VariantExtractionResult:
    """Try at most three deterministic images before accepting schema failure."""

    attempts = 0

    for attempts, variant in enumerate(variants, start=1):
        if attempts > MAX_EXTRACTION_ATTEMPTS:
            break

        try:
            raw_payload = request(variant.path)
        except OllamaTimeoutError:
            if attempts == MAX_EXTRACTION_ATTEMPTS:
                raise
            continue

        extraction, validation, validation_audit = validate_receipt_payload_with_audit(
            raw_payload,
            reference_date=reference_date,
            mode=mode,
        )
        result = VariantExtractionResult(
            raw_payload=raw_payload,
            extraction=extraction,
            validation=validation,
            validation_audit=validation_audit,
            phash=variant.phash,
        )
        if validation_audit.schema_outcome is SchemaOutcome.VALID:
            return result
        if attempts == MAX_EXTRACTION_ATTEMPTS:
            return result

    if attempts < MAX_EXTRACTION_ATTEMPTS:
        raise ExtractionVariantError("抽出画像variantが最大試行数より前に終了しました")
    raise ExtractionVariantError("抽出画像variantが上限を超えました")
