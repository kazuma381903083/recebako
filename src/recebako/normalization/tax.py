from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from recebako.domain import (
    NormalizedReceiptItem,
    ReceiptItem,
    ReceiptTaxBreakdown,
    TaxTreatment,
)

TAXABLE_AMOUNT_TOLERANCE_YEN = 2
MAX_SUBSET_SUM_STATES = 20_000
MAX_EXTERNAL_GROUPS = 8


@dataclass(frozen=True)
class _TaxGroupCandidate:
    tax_rate: int
    indexes: tuple[int, ...]
    tax_amount: int


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


def _unique_subset_for_targets(
    indexes: list[int],
    items: list[ReceiptItem],
    targets: set[int],
) -> tuple[int, ...] | None:
    states: dict[int, tuple[int, ...] | None] = {0: ()}
    missing = object()

    for index in indexes:
        updated = dict(states)
        for current_sum, subset in states.items():
            next_sum = current_sum + _raw_price(items[index])
            candidate = None if subset is None else (*subset, index)
            existing = updated.get(next_sum, missing)
            if existing is missing:
                updated[next_sum] = candidate
            elif existing != candidate:
                updated[next_sum] = None
        if len(updated) > MAX_SUBSET_SUM_STATES:
            return None
        states = updated

    matches: set[tuple[int, ...]] = set()
    for target in targets:
        for amount in range(
            target - TAXABLE_AMOUNT_TOLERANCE_YEN,
            target + TAXABLE_AMOUNT_TOLERANCE_YEN + 1,
        ):
            if amount not in states:
                continue
            subset = states[amount]
            if subset is None:
                return None
            matches.add(subset)

    if len(matches) != 1:
        return None
    return next(iter(matches))


def _candidate_for_breakdown(
    tax_rate: int,
    stated_taxable_amount: int,
    stated_tax_amount: int,
    items: list[ReceiptItem],
    unknown_rate_indexes: list[int],
) -> _TaxGroupCandidate | None:
    known_rate_indexes = [
        index
        for index, item in enumerate(items)
        if item.tax_treatment is TaxTreatment.EXCLUDED
        and item.tax_rate == tax_rate
        and item.price_raw is not None
    ]
    has_incomplete_known_item = any(
        item.tax_treatment is TaxTreatment.EXCLUDED
        and item.tax_rate == tax_rate
        and item.price_raw is None
        for item in items
    )
    if has_incomplete_known_item:
        return None

    known_total = sum(_raw_price(items[index]) for index in known_rate_indexes)
    possible_group_totals = {
        stated_taxable_amount,
        stated_taxable_amount - stated_tax_amount,
    }
    remaining_targets = {
        group_total - known_total for group_total in possible_group_totals
    }
    unknown_subset = _unique_subset_for_targets(
        unknown_rate_indexes,
        items,
        remaining_targets,
    )
    if unknown_subset is None:
        return None

    group_indexes = tuple(sorted((*known_rate_indexes, *unknown_subset)))
    if not group_indexes:
        return None
    return _TaxGroupCandidate(
        tax_rate=tax_rate,
        indexes=group_indexes,
        tax_amount=stated_tax_amount,
    )


def _select_groups_for_total(
    candidates: list[_TaxGroupCandidate],
    *,
    raw_total: int,
    total: int,
) -> tuple[_TaxGroupCandidate, ...]:
    if abs(raw_total - total) <= TAXABLE_AMOUNT_TOLERANCE_YEN:
        return ()
    if len(candidates) > MAX_EXTERNAL_GROUPS:
        return ()

    best_error: int | None = None
    best_selections: list[tuple[_TaxGroupCandidate, ...]] = []
    for selection_size in range(1, len(candidates) + 1):
        for selection in combinations(candidates, selection_size):
            indexes = [index for candidate in selection for index in candidate.indexes]
            if len(indexes) != len(set(indexes)):
                continue

            adjusted_total = raw_total + sum(
                candidate.tax_amount for candidate in selection
            )
            error = abs(adjusted_total - total)
            if error > TAXABLE_AMOUNT_TOLERANCE_YEN:
                continue
            if best_error is None or error < best_error:
                best_error = error
                best_selections = [selection]
            elif error == best_error:
                best_selections.append(selection)

    if len(best_selections) != 1:
        return ()
    return best_selections[0]


def normalize_item_taxes(
    items: list[ReceiptItem],
    breakdowns: list[ReceiptTaxBreakdown],
    *,
    total: int,
) -> list[NormalizedReceiptItem]:
    normalized = [_base_normalized_item(item) for item in items]
    external_breakdowns = _external_breakdowns_by_rate(breakdowns)
    unknown_rate_indexes = [
        index
        for index, item in enumerate(items)
        if item.tax_treatment is TaxTreatment.EXCLUDED
        and item.tax_rate is None
        and item.price_raw is not None
    ]
    candidates = [
        candidate
        for tax_rate, (stated_taxable_amount, stated_tax_amount) in (
            external_breakdowns.items()
        )
        if (
            candidate := _candidate_for_breakdown(
                tax_rate,
                stated_taxable_amount,
                stated_tax_amount,
                items,
                unknown_rate_indexes,
            )
        )
        is not None
    ]
    selected_groups = _select_groups_for_total(
        candidates,
        raw_total=sum(_raw_price(item) for item in items),
        total=total,
    )

    for group in selected_groups:
        raw_prices = [_raw_price(items[index]) for index in group.indexes]
        allocations = _allocate_tax(raw_prices, group.tax_amount)
        if allocations is None:
            continue

        for index, adjustment in zip(group.indexes, allocations, strict=True):
            item = normalized[index]
            normalized[index] = item.model_copy(
                update={
                    "price": _raw_price(items[index]) + adjustment,
                    "tax_rate": group.tax_rate,
                    "tax_adjustment": adjustment,
                }
            )

    return normalized
