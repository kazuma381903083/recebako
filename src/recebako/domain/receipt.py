from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

PaymentMethod = Literal["cash", "credit", "qr", "emoney", "unknown"]


class ReceiptFileState(str, Enum):
    PENDING = "pending"
    FINALIZED = "finalized"


class TaxTreatment(str, Enum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNKNOWN = "unknown"


class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    qty: int = 1
    price: int = Field(description="円・税込の行合計")
    price_raw: int | None = Field(
        description="商品行に印字された税補正前の行合計。読めなければnull",
    )
    tax_rate: int | None = Field(
        ge=0,
        le=100,
        description="商品行へ適用される税率(%)。読めなければnull",
    )
    tax_treatment: TaxTreatment = Field(
        description="印字価格が内税ならincluded、外税ならexcluded、不明ならunknown",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_tax_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            value.setdefault("price_raw", None)
            value.setdefault("tax_rate", None)
            value.setdefault("tax_treatment", TaxTreatment.UNKNOWN)
        return value


class ReceiptTaxBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tax_rate: int = Field(ge=0, le=100, description="税率(%)")
    taxable_amount: int = Field(description="レシート記載の税率別対象金額")
    tax_amount: int = Field(description="レシート記載の税率別消費税額")
    tax_treatment: TaxTreatment = Field(
        description="対象金額が内税ならincluded、外税ならexcluded"
    )


class ReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_receipt: StrictBool = Field(
        description="店舗のレシート画像ならtrue、それ以外ならfalse",
    )
    store: str = Field(description="店名。読めなければ空文字")
    date: str = Field(description="YYYY-MM-DD。読めなければ空文字")
    time: str = ""
    items: Sequence[ReceiptItem]
    subtotal: int = 0
    tax: int = 0
    tax_breakdowns: list[ReceiptTaxBreakdown] = Field(
        description="レシートに明記された税率別の対象金額・税額内訳",
    )
    total: int = Field(description="レシート記載の合計金額")
    payment: PaymentMethod = "unknown"
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="読み取り自信度 0.0-1.0",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_tax_breakdowns(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            value.setdefault("tax_breakdowns", [])
        return value


class NormalizedReceiptItem(ReceiptItem):
    tax_adjustment: int = Field(
        default=0,
        description="price_rawへ決定的に配賦した外税額",
    )


class NormalizedReceiptExtraction(ReceiptExtraction):
    is_receipt: Literal[True] = Field(default=True, exclude=True)
    items: Sequence[NormalizedReceiptItem]
    date_raw: str
