from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from recebako.domain import (
    NormalizedReceiptItem,
    ReceiptItem,
    ReceiptTaxBreakdown,
    TaxTreatment,
)

TAXABLE_AMOUNT_TOLERANCE_YEN = 2
MAX_SUBSET_SUM_STATES = 20_000
MAX_EXTERNAL_GROUPS = 8
SUPPORTED_EXTERNAL_TAX_RATES = frozenset({8, 10})


class TaxNormalizationReason(str, Enum):
    APPLIED = "applied"
    NOT_NEEDED = "not_needed"
    MISSING_EVIDENCE = "missing_evidence"
    INCONSISTENT_INPUT = "inconsistent_input"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    TOTAL_MISMATCH = "total_mismatch"
    SEARCH_LIMIT = "search_limit"
    GROUP_LIMIT = "group_limit"
    ALLOCATION_FAILED = "allocation_failed"


@dataclass(frozen=True)
class TaxGroupAssignment:
    tax_rate: int
    taxable_amount: int
    tax_amount: int
    item_indexes: tuple[int, ...]


@dataclass(frozen=True)
class TaxNormalizationAudit:
    applied: bool
    reason: TaxNormalizationReason
    assignments: tuple[TaxGroupAssignment, ...]
    search_states: int
    search_limit_reached: bool
    evidence_present: bool


@dataclass(frozen=True)
class TaxNormalizationResult:
    items: list[NormalizedReceiptItem]
    audit: TaxNormalizationAudit


@dataclass(frozen=True)
class _ExternalTaxGroup:
    tax_rate: int
    taxable_amount: int
    tax_amount: int


@dataclass(frozen=True)
class _TaxGroupCandidate:
    tax_rate: int
    taxable_amount: int
    indexes: tuple[int, ...]
    tax_amount: int


@dataclass
class _SearchBudget:
    maximum: int
    states: int = 0
    limit_reached: bool = False

    def consume(self) -> bool:
        if self.states >= self.maximum:
            self.limit_reached = True
            return False
        self.states += 1
        return True


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
    if not raw_prices or taxable_amount <= 0 or tax_amount < 0:
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


def _external_groups(
    breakdowns: list[ReceiptTaxBreakdown],
) -> tuple[list[_ExternalTaxGroup], TaxNormalizationReason | None]:
    external = [
        breakdown
        for breakdown in breakdowns
        if breakdown.tax_treatment is TaxTreatment.EXCLUDED
    ]
    if len(external) > MAX_EXTERNAL_GROUPS:
        return [], TaxNormalizationReason.GROUP_LIMIT

    if any(
        breakdown.tax_rate not in SUPPORTED_EXTERNAL_TAX_RATES
        or breakdown.taxable_amount < 0
        or breakdown.tax_amount < 0
        for breakdown in external
    ):
        return [], TaxNormalizationReason.INCONSISTENT_INPUT

    grouped: dict[int, tuple[int, int]] = {}
    for breakdown in sorted(
        external,
        key=lambda value: (
            value.tax_rate,
            value.taxable_amount,
            value.tax_amount,
        ),
    ):
        taxable_amount, tax_amount = grouped.get(breakdown.tax_rate, (0, 0))
        grouped[breakdown.tax_rate] = (
            taxable_amount + breakdown.taxable_amount,
            tax_amount + breakdown.tax_amount,
        )

    return [
        _ExternalTaxGroup(
            tax_rate=tax_rate,
            taxable_amount=taxable_amount,
            tax_amount=tax_amount,
        )
        for tax_rate, (taxable_amount, tax_amount) in sorted(grouped.items())
    ], None


def _has_allowed_amount_in_range(
    allowed_amounts: list[int],
    minimum: int,
    maximum: int,
) -> bool:
    position = bisect_left(allowed_amounts, minimum)
    return position < len(allowed_amounts) and allowed_amounts[position] <= maximum


def _matching_subsets(
    indexes: list[int],
    items: list[ReceiptItem],
    targets: set[int],
    *,
    budget: _SearchBudget,
) -> set[tuple[int, ...]]:
    allowed_amounts = sorted(
        {
            amount
            for target in targets
            for amount in range(
                target - TAXABLE_AMOUNT_TOLERANCE_YEN,
                target + TAXABLE_AMOUNT_TOLERANCE_YEN + 1,
            )
        }
    )
    if not allowed_amounts:
        return set()

    ordered_indexes = sorted(indexes)
    raw_prices = [_raw_price(items[index]) for index in ordered_indexes]
    suffix_minimum = [0] * (len(raw_prices) + 1)
    suffix_maximum = [0] * (len(raw_prices) + 1)
    for position in range(len(raw_prices) - 1, -1, -1):
        raw_price = raw_prices[position]
        suffix_minimum[position] = suffix_minimum[position + 1] + min(raw_price, 0)
        suffix_maximum[position] = suffix_maximum[position + 1] + max(raw_price, 0)

    matches: set[tuple[int, ...]] = set()

    def search(
        position: int,
        current_sum: int,
        selected: tuple[int, ...],
    ) -> None:
        if budget.limit_reached or not budget.consume():
            return
        if not _has_allowed_amount_in_range(
            allowed_amounts,
            current_sum + suffix_minimum[position],
            current_sum + suffix_maximum[position],
        ):
            return
        if position == len(ordered_indexes):
            if current_sum in allowed_amounts:
                matches.add(selected)
            return

        search(position + 1, current_sum, selected)
        index = ordered_indexes[position]
        search(
            position + 1,
            current_sum + raw_prices[position],
            (*selected, index),
        )

    search(0, 0, ())
    return matches


def _plausible_group_totals(group: _ExternalTaxGroup) -> set[int]:
    if group.tax_amount == 0:
        return set()

    possible_totals = {
        group.taxable_amount,
        group.taxable_amount - group.tax_amount,
    }
    return {
        possible_total
        for possible_total in possible_totals
        if possible_total >= 0
        and abs(Fraction(possible_total * group.tax_rate, 100) - group.tax_amount)
        <= TAXABLE_AMOUNT_TOLERANCE_YEN
    }


def _candidates_for_group(
    group: _ExternalTaxGroup,
    items: list[ReceiptItem],
    unknown_rate_indexes: list[int],
    *,
    budget: _SearchBudget,
) -> list[_TaxGroupCandidate]:
    possible_group_totals = _plausible_group_totals(group)
    if not possible_group_totals:
        return []

    known_rate_indexes = [
        index
        for index, item in enumerate(items)
        if item.tax_treatment is TaxTreatment.EXCLUDED
        and item.tax_rate == group.tax_rate
        and item.price_raw is not None
    ]
    if any(
        item.tax_treatment is TaxTreatment.EXCLUDED
        and item.tax_rate == group.tax_rate
        and item.price_raw is None
        for item in items
    ):
        return []

    known_total = sum(_raw_price(items[index]) for index in known_rate_indexes)
    remaining_targets = {
        group_total - known_total for group_total in possible_group_totals
    }
    unknown_subsets = _matching_subsets(
        unknown_rate_indexes,
        items,
        remaining_targets,
        budget=budget,
    )
    candidates = {
        _TaxGroupCandidate(
            tax_rate=group.tax_rate,
            taxable_amount=group.taxable_amount,
            indexes=tuple(sorted((*known_rate_indexes, *unknown_subset))),
            tax_amount=group.tax_amount,
        )
        for unknown_subset in unknown_subsets
        if known_rate_indexes or unknown_subset
    }
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.tax_rate,
            candidate.indexes,
            candidate.taxable_amount,
            candidate.tax_amount,
        ),
    )


def _select_groups_for_total(
    candidates_by_rate: list[tuple[int, list[_TaxGroupCandidate]]],
    *,
    raw_total: int,
    total: int,
    budget: _SearchBudget,
) -> tuple[tuple[_TaxGroupCandidate, ...], TaxNormalizationReason]:
    best_error: int | None = None
    best_selections: set[tuple[_TaxGroupCandidate, ...]] = set()

    def search(
        position: int,
        selected: tuple[_TaxGroupCandidate, ...],
        used_indexes: frozenset[int],
    ) -> None:
        nonlocal best_error, best_selections
        if budget.limit_reached or not budget.consume():
            return
        if position == len(candidates_by_rate):
            if not selected:
                return
            error = abs(
                raw_total + sum(candidate.tax_amount for candidate in selected) - total
            )
            if best_error is None or error < best_error:
                best_error = error
                best_selections = {selected}
            elif error == best_error:
                best_selections.add(selected)
            return

        search(position + 1, selected, used_indexes)
        _, candidates = candidates_by_rate[position]
        for candidate in candidates:
            candidate_indexes = frozenset(candidate.indexes)
            if used_indexes.isdisjoint(candidate_indexes):
                search(
                    position + 1,
                    (*selected, candidate),
                    used_indexes | candidate_indexes,
                )

    search(0, (), frozenset())
    if budget.limit_reached:
        return (), TaxNormalizationReason.SEARCH_LIMIT
    if best_error is None:
        return (), TaxNormalizationReason.NO_MATCH
    if len(best_selections) != 1:
        return (), TaxNormalizationReason.AMBIGUOUS
    if best_error != 0:
        return (), TaxNormalizationReason.TOTAL_MISMATCH
    return next(iter(best_selections)), TaxNormalizationReason.APPLIED


def _audit(
    reason: TaxNormalizationReason,
    *,
    budget: _SearchBudget,
    evidence_present: bool,
    assignments: tuple[TaxGroupAssignment, ...] = (),
) -> TaxNormalizationAudit:
    return TaxNormalizationAudit(
        applied=reason is TaxNormalizationReason.APPLIED,
        reason=reason,
        assignments=assignments,
        search_states=budget.states,
        search_limit_reached=(
            budget.limit_reached or reason is TaxNormalizationReason.GROUP_LIMIT
        ),
        evidence_present=evidence_present,
    )


def normalize_item_taxes_with_audit(
    items: list[ReceiptItem],
    breakdowns: list[ReceiptTaxBreakdown],
    *,
    total: int,
) -> TaxNormalizationResult:
    normalized = [_base_normalized_item(item) for item in items]
    raw_total = sum(_raw_price(item) for item in items)
    evidence_present = any(
        item.tax_treatment is TaxTreatment.EXCLUDED for item in items
    ) or any(
        breakdown.tax_treatment is TaxTreatment.EXCLUDED for breakdown in breakdowns
    )
    budget = _SearchBudget(MAX_SUBSET_SUM_STATES)

    if raw_total == total:
        return TaxNormalizationResult(
            items=normalized,
            audit=_audit(
                TaxNormalizationReason.NOT_NEEDED,
                budget=budget,
                evidence_present=evidence_present,
            ),
        )

    external_groups, input_error = _external_groups(breakdowns)
    if input_error is not None:
        return TaxNormalizationResult(
            items=normalized,
            audit=_audit(
                input_error,
                budget=budget,
                evidence_present=evidence_present,
            ),
        )
    if not external_groups:
        return TaxNormalizationResult(
            items=normalized,
            audit=_audit(
                TaxNormalizationReason.MISSING_EVIDENCE,
                budget=budget,
                evidence_present=evidence_present,
            ),
        )

    unknown_rate_indexes = [
        index
        for index, item in enumerate(items)
        if item.tax_treatment is TaxTreatment.EXCLUDED
        and item.tax_rate is None
        and item.price_raw is not None
    ]
    candidates_by_rate = [
        (
            group.tax_rate,
            _candidates_for_group(
                group,
                items,
                unknown_rate_indexes,
                budget=budget,
            ),
        )
        for group in external_groups
    ]
    if budget.limit_reached:
        return TaxNormalizationResult(
            items=normalized,
            audit=_audit(
                TaxNormalizationReason.SEARCH_LIMIT,
                budget=budget,
                evidence_present=evidence_present,
            ),
        )

    selected_groups, reason = _select_groups_for_total(
        candidates_by_rate,
        raw_total=raw_total,
        total=total,
        budget=budget,
    )
    if reason is not TaxNormalizationReason.APPLIED:
        return TaxNormalizationResult(
            items=normalized,
            audit=_audit(
                reason,
                budget=budget,
                evidence_present=evidence_present,
            ),
        )

    assignments: list[TaxGroupAssignment] = []
    for group in selected_groups:
        raw_prices = [_raw_price(items[index]) for index in group.indexes]
        allocations = _allocate_tax(raw_prices, group.tax_amount)
        if allocations is None:
            return TaxNormalizationResult(
                items=[_base_normalized_item(item) for item in items],
                audit=_audit(
                    TaxNormalizationReason.ALLOCATION_FAILED,
                    budget=budget,
                    evidence_present=evidence_present,
                ),
            )

        for index, adjustment in zip(group.indexes, allocations, strict=True):
            item = normalized[index]
            normalized[index] = item.model_copy(
                update={
                    "price": _raw_price(items[index]) + adjustment,
                    "tax_rate": group.tax_rate,
                    "tax_adjustment": adjustment,
                }
            )
        assignments.append(
            TaxGroupAssignment(
                tax_rate=group.tax_rate,
                taxable_amount=group.taxable_amount,
                tax_amount=group.tax_amount,
                item_indexes=group.indexes,
            )
        )

    if sum(item.price for item in normalized) != total:
        return TaxNormalizationResult(
            items=[_base_normalized_item(item) for item in items],
            audit=_audit(
                TaxNormalizationReason.TOTAL_MISMATCH,
                budget=budget,
                evidence_present=evidence_present,
            ),
        )

    return TaxNormalizationResult(
        items=normalized,
        audit=_audit(
            TaxNormalizationReason.APPLIED,
            budget=budget,
            evidence_present=evidence_present,
            assignments=tuple(assignments),
        ),
    )


def normalize_item_taxes(
    items: list[ReceiptItem],
    breakdowns: list[ReceiptTaxBreakdown],
    *,
    total: int,
) -> list[NormalizedReceiptItem]:
    return normalize_item_taxes_with_audit(
        items,
        breakdowns,
        total=total,
    ).items
