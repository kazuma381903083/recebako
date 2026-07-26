from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from recebako.ai import (
    OllamaConnectionError,
    OllamaResponseError,
    OllamaTimeoutError,
)
from recebako.domain import IngestMode, ReceiptStatus
from recebako.pipeline.retry import extract_with_variant_retry
from recebako.validation import SchemaOutcome

REFERENCE_DATE = date(2026, 7, 26)
ORIGINAL_PHASH = "0123456789abcdef"


@dataclass(frozen=True)
class _Variant:
    path: Path
    phash: str = ORIGINAL_PHASH


def _variants(count: int = 3) -> list[_Variant]:
    return [
        _Variant(path=Path(f"/synthetic/variant-{index}.jpg"))
        for index in range(1, count + 1)
    ]


def _payload(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "is_receipt": True,
        "store": "テスト商店",
        "date": "2026-07-25",
        "time": "12:34",
        "items": [{"name": "テスト品", "qty": 1, "price": 100}],
        "subtotal": 100,
        "tax": 0,
        "tax_breakdowns": [],
        "total": 100,
        "payment": "cash",
        "confidence": 0.95,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _extract(
    variants: list[_Variant],
    request: Any,
) -> Any:
    return extract_with_variant_retry(
        variants,
        request=request,
        reference_date=REFERENCE_DATE,
        mode=IngestMode.REGULAR,
    )


@pytest.mark.parametrize("successful_attempt", [1, 2, 3])
def test_schema_valid_response_stops_at_expected_variant(
    successful_attempt: int,
) -> None:
    variants = _variants()
    calls: list[Path] = []

    def request(path: Path) -> str:
        calls.append(path)
        if len(calls) < successful_attempt:
            return "{not-json"
        return _payload()

    result = _extract(variants, request)

    assert calls == [variant.path for variant in variants[:successful_attempt]]
    assert result.raw_payload == _payload()
    assert result.extraction is not None
    assert result.validation.status is ReceiptStatus.CONFIRMED
    assert result.validation_audit.schema_outcome is SchemaOutcome.VALID
    assert result.phash == ORIGINAL_PHASH


def test_three_schema_invalid_responses_return_only_third_result_and_stop() -> None:
    variants = _variants(count=4)
    calls: list[Path] = []
    responses = [
        '{"attempt":1}',
        '{"attempt":2}',
        '{"attempt":3}',
        _payload(),
    ]

    def request(path: Path) -> str:
        calls.append(path)
        return responses[len(calls) - 1]

    result = _extract(variants, request)

    assert calls == [variant.path for variant in variants[:3]]
    assert result.raw_payload == responses[2]
    assert result.extraction is None
    assert result.validation.status is ReceiptStatus.FAILED
    assert {issue.code for issue in result.validation.issues} == {"structure.invalid"}
    assert result.validation_audit.schema_outcome is SchemaOutcome.INVALID
    assert result.phash == ORIGINAL_PHASH


def test_timeout_retries_with_next_variant_and_accepts_success() -> None:
    variants = _variants()
    calls: list[Path] = []

    def request(path: Path) -> str:
        calls.append(path)
        if len(calls) == 1:
            raise OllamaTimeoutError("synthetic timeout")
        return _payload()

    result = _extract(variants, request)

    assert calls == [variant.path for variant in variants[:2]]
    assert result.extraction is not None
    assert result.validation.status is ReceiptStatus.CONFIRMED
    assert result.phash == ORIGINAL_PHASH


def test_three_timeouts_reraise_third_timeout() -> None:
    variants = _variants(count=4)
    calls: list[Path] = []
    errors = [
        OllamaTimeoutError(f"synthetic timeout {attempt}") for attempt in range(1, 4)
    ]

    def request(path: Path) -> str:
        calls.append(path)
        raise errors[len(calls) - 1]

    with pytest.raises(OllamaTimeoutError) as captured:
        _extract(variants, request)

    assert captured.value is errors[2]
    assert calls == [variant.path for variant in variants[:3]]


@pytest.mark.parametrize(
    "error",
    [
        OllamaConnectionError("synthetic connection failure"),
        OllamaResponseError("synthetic response failure"),
    ],
)
def test_non_retryable_ollama_error_stops_after_first_variant(
    error: Exception,
) -> None:
    variants = _variants()
    calls: list[Path] = []

    def request(path: Path) -> str:
        calls.append(path)
        raise error

    with pytest.raises(type(error)) as captured:
        _extract(variants, request)

    assert captured.value is error
    assert calls == [variants[0].path]


def test_non_receipt_is_schema_valid_and_stops_after_first_variant() -> None:
    variants = _variants()
    calls: list[Path] = []

    def request(path: Path) -> str:
        calls.append(path)
        return _payload(
            is_receipt=False,
            store="",
            date="",
            time="",
            items=[],
            subtotal=0,
            tax=0,
            total=0,
            payment="unknown",
        )

    result = _extract(variants, request)

    assert calls == [variants[0].path]
    assert result.extraction is None
    assert result.validation.status is ReceiptStatus.FAILED
    assert {issue.code for issue in result.validation.issues} == {"receipt.not_receipt"}
    assert result.validation_audit.schema_outcome is SchemaOutcome.VALID
    assert result.phash == ORIGINAL_PHASH


def test_schema_valid_review_stops_after_first_variant() -> None:
    variants = _variants()
    calls: list[Path] = []

    def request(path: Path) -> str:
        calls.append(path)
        return _payload(confidence=0.5)

    result = _extract(variants, request)

    assert calls == [variants[0].path]
    assert result.extraction is not None
    assert result.validation.status is ReceiptStatus.REVIEW
    assert {issue.code for issue in result.validation.issues} == {"confidence.low"}
    assert result.validation_audit.schema_outcome is SchemaOutcome.VALID
    assert result.phash == ORIGINAL_PHASH


def test_private_invalid_payload_is_hidden_from_repr_and_validation_output() -> None:
    private_sentinel = "PRIVATE-RECEIPT-CONTENT-998877"
    invalid_payload = json.dumps(
        {"store": private_sentinel},
        ensure_ascii=False,
    )

    result = _extract(
        _variants(),
        lambda path: invalid_payload,
    )

    assert result.raw_payload == invalid_payload
    assert result.extraction is None
    assert private_sentinel not in repr(result)
    assert private_sentinel not in result.validation.model_dump_json()
    assert private_sentinel not in result.validation_audit.model_dump_json()


def test_private_accepted_extraction_is_hidden_from_internal_result_repr() -> None:
    private_sentinel = "PRIVATE-ACCEPTED-EXTRACTION-998877"

    result = _extract(
        _variants(),
        lambda path: _payload(
            store=private_sentinel,
            items=[{"name": private_sentinel, "qty": 1, "price": 100}],
        ),
    )

    assert result.extraction is not None
    assert private_sentinel not in repr(result)
