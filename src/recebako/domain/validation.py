from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ReceiptStatus(str, Enum):
    CONFIRMED = "confirmed"
    REVIEW = "review"
    FAILED = "failed"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReceiptStatus
    issues: list[ValidationIssue]
