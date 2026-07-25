from __future__ import annotations

from recebako.domain import ReceiptItem, ReceiptTaxBreakdown
from recebako.normalization import normalize_item_taxes


def test_mixed_external_and_internal_tax_is_normalized() -> None:
    items = [
        ReceiptItem(
            name="外税商品",
            price=140,
            price_raw=140,
            tax_rate=8,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="内税商品",
            price=570,
            price_raw=570,
            tax_rate=10,
            tax_treatment="included",
        ),
    ]
    breakdowns = [
        ReceiptTaxBreakdown(
            tax_rate=8,
            taxable_amount=140,
            tax_amount=11,
            tax_treatment="excluded",
        ),
        ReceiptTaxBreakdown(
            tax_rate=10,
            taxable_amount=570,
            tax_amount=51,
            tax_treatment="included",
        ),
    ]

    normalized = normalize_item_taxes(items, breakdowns)

    assert [item.price for item in normalized] == [151, 570]
    assert [item.price_raw for item in normalized] == [140, 570]
    assert [item.tax_adjustment for item in normalized] == [11, 0]


def test_external_tax_is_distributed_deterministically() -> None:
    items = [
        ReceiptItem(
            name="商品A",
            price=33,
            price_raw=33,
            tax_rate=8,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="商品B",
            price=67,
            price_raw=67,
            tax_rate=8,
            tax_treatment="excluded",
        ),
    ]
    breakdowns = [
        ReceiptTaxBreakdown(
            tax_rate=8,
            taxable_amount=100,
            tax_amount=8,
            tax_treatment="excluded",
        )
    ]

    normalized = normalize_item_taxes(items, breakdowns)

    assert [item.tax_adjustment for item in normalized] == [3, 5]
    assert [item.price for item in normalized] == [36, 72]


def test_discount_participates_in_external_tax_allocation() -> None:
    items = [
        ReceiptItem(
            name="商品",
            price=1000,
            price_raw=1000,
            tax_rate=10,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="値引き",
            price=-100,
            price_raw=-100,
            tax_rate=10,
            tax_treatment="excluded",
        ),
    ]
    breakdowns = [
        ReceiptTaxBreakdown(
            tax_rate=10,
            taxable_amount=900,
            tax_amount=90,
            tax_treatment="excluded",
        )
    ]

    normalized = normalize_item_taxes(items, breakdowns)

    assert [item.tax_adjustment for item in normalized] == [100, -10]
    assert [item.price for item in normalized] == [1100, -110]


def test_missing_external_breakdown_does_not_guess_tax() -> None:
    item = ReceiptItem(
        name="外税商品",
        price=140,
        price_raw=140,
        tax_rate=8,
        tax_treatment="excluded",
    )

    normalized = normalize_item_taxes([item], [])

    assert normalized[0].price == 140
    assert normalized[0].tax_adjustment == 0


def test_mismatched_taxable_amount_does_not_normalize() -> None:
    item = ReceiptItem(
        name="外税商品",
        price=140,
        price_raw=140,
        tax_rate=8,
        tax_treatment="excluded",
    )
    breakdown = ReceiptTaxBreakdown(
        tax_rate=8,
        taxable_amount=100,
        tax_amount=8,
        tax_treatment="excluded",
    )

    normalized = normalize_item_taxes([item], [breakdown])

    assert normalized[0].price == 140
    assert normalized[0].tax_adjustment == 0


def test_internal_tax_is_not_added_twice() -> None:
    item = ReceiptItem(
        name="内税商品",
        price=570,
        price_raw=570,
        tax_rate=10,
        tax_treatment="included",
    )
    breakdown = ReceiptTaxBreakdown(
        tax_rate=10,
        taxable_amount=570,
        tax_amount=51,
        tax_treatment="included",
    )

    normalized = normalize_item_taxes([item], [breakdown])

    assert normalized[0].price == 570
    assert normalized[0].tax_adjustment == 0


def test_unique_external_rate_and_tax_inclusive_target_are_safe_evidence() -> None:
    items = [
        ReceiptItem(
            name="外税商品",
            price=140,
            price_raw=140,
            tax_rate=None,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="内税商品",
            price=570,
            price_raw=570,
            tax_rate=None,
            tax_treatment="included",
        ),
    ]
    breakdowns = [
        ReceiptTaxBreakdown(
            tax_rate=8,
            taxable_amount=151,
            tax_amount=11,
            tax_treatment="excluded",
        ),
        ReceiptTaxBreakdown(
            tax_rate=10,
            taxable_amount=570,
            tax_amount=51,
            tax_treatment="included",
        ),
    ]

    normalized = normalize_item_taxes(items, breakdowns)

    assert [item.price for item in normalized] == [151, 570]
    assert [item.tax_rate for item in normalized] == [8, None]
    assert [item.tax_adjustment for item in normalized] == [11, 0]


def test_missing_item_rates_with_multiple_external_rates_are_not_guessed() -> None:
    item = ReceiptItem(
        name="税率不明の外税商品",
        price=140,
        price_raw=140,
        tax_rate=None,
        tax_treatment="excluded",
    )
    breakdowns = [
        ReceiptTaxBreakdown(
            tax_rate=8,
            taxable_amount=151,
            tax_amount=11,
            tax_treatment="excluded",
        ),
        ReceiptTaxBreakdown(
            tax_rate=10,
            taxable_amount=110,
            tax_amount=10,
            tax_treatment="excluded",
        ),
    ]

    normalized = normalize_item_taxes([item], breakdowns)

    assert normalized[0].price == 140
    assert normalized[0].tax_rate is None
    assert normalized[0].tax_adjustment == 0
