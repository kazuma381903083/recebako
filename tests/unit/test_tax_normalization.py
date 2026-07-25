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

    normalized = normalize_item_taxes(items, breakdowns, total=721)

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

    normalized = normalize_item_taxes(items, breakdowns, total=108)

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

    normalized = normalize_item_taxes(items, breakdowns, total=990)

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

    normalized = normalize_item_taxes([item], [], total=151)

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

    normalized = normalize_item_taxes([item], [breakdown], total=151)

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

    normalized = normalize_item_taxes([item], [breakdown], total=570)

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

    normalized = normalize_item_taxes(items, breakdowns, total=721)

    assert [item.price for item in normalized] == [151, 570]
    assert [item.tax_rate for item in normalized] == [8, None]
    assert [item.tax_adjustment for item in normalized] == [11, 0]


def test_ambiguous_tax_rate_assignment_is_not_guessed() -> None:
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
            taxable_amount=151,
            tax_amount=11,
            tax_treatment="excluded",
        ),
    ]

    normalized = normalize_item_taxes([item], breakdowns, total=151)

    assert normalized[0].price == 140
    assert normalized[0].tax_rate is None
    assert normalized[0].tax_adjustment == 0


def test_multiple_rates_are_assigned_by_unique_taxable_subtotals() -> None:
    items = [
        ReceiptItem(
            name="8%商品A",
            price=600,
            price_raw=600,
            tax_rate=None,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="8%商品B",
            price=148,
            price_raw=148,
            tax_rate=None,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="10%内税商品A",
            price=5800,
            price_raw=5800,
            tax_rate=None,
            tax_treatment="included",
        ),
        ReceiptItem(
            name="10%内税商品B",
            price=5800,
            price_raw=5800,
            tax_rate=None,
            tax_treatment="included",
        ),
        ReceiptItem(
            name="10%外税商品",
            price=3,
            price_raw=3,
            tax_rate=None,
            tax_treatment="excluded",
        ),
    ]
    breakdowns = [
        ReceiptTaxBreakdown(
            tax_rate=8,
            taxable_amount=748,
            tax_amount=59,
            tax_treatment="excluded",
        ),
        ReceiptTaxBreakdown(
            tax_rate=10,
            taxable_amount=3,
            tax_amount=3,
            tax_treatment="excluded",
        ),
    ]

    normalized = normalize_item_taxes(items, breakdowns, total=12410)

    assert [item.price for item in normalized] == [647, 160, 5800, 5800, 3]
    assert [item.tax_rate for item in normalized] == [8, 8, None, None, None]
    assert [item.tax_adjustment for item in normalized] == [47, 12, 0, 0, 0]


def test_ambiguous_item_subset_is_not_normalized() -> None:
    items = [
        ReceiptItem(
            name="同額商品A",
            price=100,
            price_raw=100,
            tax_rate=None,
            tax_treatment="excluded",
        ),
        ReceiptItem(
            name="同額商品B",
            price=100,
            price_raw=100,
            tax_rate=None,
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

    normalized = normalize_item_taxes(items, breakdowns, total=208)

    assert [item.price for item in normalized] == [100, 100]
    assert [item.tax_adjustment for item in normalized] == [0, 0]
