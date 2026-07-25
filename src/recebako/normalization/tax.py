from __future__ import annotations

from fractions import Fraction

from recebako.domain import (
    NormalizedReceiptItem,
    ReceiptItem,
    ReceiptTaxBreakdown,
    TaxTreatment,
)

TAXABLE_AMOUNT_TOLERANCE_YEN = 2


def _raw_price(item: ReceiptItem) -> int:
    return item.price if item.price_raw is None else item.price_raw


def _base_normalized_item(item: ReceiptItem) -> NormalizedReceiptItem:
    data = item.model_dump(mode="python")
    data["price_raw"] = _raw_price(item)
    data["price"] = _raw_price(item)
    data["tax_adjustment"] = 0
    return NormalizedReceiptItem.model_validate(data)


def _allocate_tax(raw_prices: list[int], tax_amount: int) -> list[int] | None:
    taxable_amount = sum(raw_prices)
    if not raw_prices or taxable_amount == 0:
        return None

    exact_allocations = [
        Fraction(raw_price * tax_amount, taxable_amount) for raw_price in raw_prices
    ]
    allocations = [int(exact) for exact in exact_allocations]
    remainder = tax_amount - sum(allocations)
    if remainder == 0:
        return allocations

    fractional_parts = [
        exact - allocated
        for exact, allocated in zip(exact_allocations, allocations, strict=True)
    ]
    if remainder > 0:
        order = sorted(
            range(len(raw_prices)),
            key=lambda index: (-fractional_parts[index], index),
        )
        increment = 1
    else:
        order = sorted(
            range(len(raw_prices)),
            key=lambda index: (fractional_parts[index], index),
        )
        increment = -1

    for offset in range(abs(remainder)):
        allocations[order[offset % len(order)]] += increment
    return allocations


def _external_breakdowns_by_rate(
    breakdowns: list[ReceiptTaxBreakdown],
) -> dict[int, tuple[int, int]]:
    grouped: dict[int, tuple[int, int]] = {}
    for breakdown in breakdowns:
        if breakdown.tax_treatment is not TaxTreatment.EXCLUDED:
            continue
        taxable_amount, tax_amount = grouped.get(breakdown.tax_rate, (0, 0))
        grouped[breakdown.tax_rate] = (
            taxable_amount + breakdown.taxable_amount,
            tax_amount + breakdown.tax_amount,
        )
    return grouped


def normalize_item_taxes(
    items: list[ReceiptItem],
    breakdowns: list[ReceiptTaxBreakdown],
) -> list[NormalizedReceiptItem]:
    normalized = [_base_normalized_item(item) for item in items]
    external_breakdowns = _external_breakdowns_by_rate(breakdowns)

    excluded_indexes_by_rate: dict[int, list[int]] = {}
    excluded_indexes_without_rate: list[int] = []
    for index, item in enumerate(items):
        if item.tax_treatment is not TaxTreatment.EXCLUDED or item.price_raw is None:
            continue
        if item.tax_rate is None:
            excluded_indexes_without_rate.append(index)
        else:
            excluded_indexes_by_rate.setdefault(item.tax_rate, []).append(index)

    if len(external_breakdowns) == 1:
        only_external_rate = next(iter(external_breakdowns))
        excluded_indexes_by_rate.setdefault(only_external_rate, []).extend(
            excluded_indexes_without_rate
        )
        for index in excluded_indexes_without_rate:
            normalized[index] = normalized[index].model_copy(
                update={"tax_rate": only_external_rate}
            )

    for tax_rate, indexes in excluded_indexes_by_rate.items():
        breakdown = external_breakdowns.get(tax_rate)
        if breakdown is None:
            continue

        stated_taxable_amount, stated_tax_amount = breakdown
        raw_prices = [_raw_price(items[index]) for index in indexes]
        raw_total = sum(raw_prices)
        matches_pretax_amount = (
            abs(raw_total - stated_taxable_amount) <= TAXABLE_AMOUNT_TOLERANCE_YEN
        )
        matches_tax_inclusive_amount = (
            abs(raw_total + stated_tax_amount - stated_taxable_amount)
            <= TAXABLE_AMOUNT_TOLERANCE_YEN
        )
        if not matches_pretax_amount and not matches_tax_inclusive_amount:
            continue

        allocations = _allocate_tax(raw_prices, stated_tax_amount)
        if allocations is None:
            continue

        for index, adjustment in zip(indexes, allocations, strict=True):
            item = normalized[index]
            normalized[index] = item.model_copy(
                update={
                    "price": _raw_price(items[index]) + adjustment,
                    "tax_adjustment": adjustment,
                }
            )

    return normalized
