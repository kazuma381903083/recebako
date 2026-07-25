from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PaymentMethod = Literal["cash", "credit", "qr", "emoney", "unknown"]


class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    qty: int = 1
    price: int = Field(description="円・税込の行合計")


class ReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: str = Field(description="店名。読めなければ空文字")
    date: str = Field(description="YYYY-MM-DD。読めなければ空文字")
    time: str = ""
    items: list[ReceiptItem]
    subtotal: int = 0
    tax: int = 0
    total: int = Field(description="レシート記載の合計金額")
    payment: PaymentMethod = "unknown"
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="読み取り自信度 0.0-1.0",
    )
