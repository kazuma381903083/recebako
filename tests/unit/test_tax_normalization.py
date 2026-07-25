from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, product
from random import Random

import pytest

import recebako.normalization.tax as tax_module
from recebako.domain import ReceiptItem, ReceiptTaxBreakdown
from recebako.normalization import (
    TaxNormalizationReason,
    normalize_item_taxes,
    normalize_item_taxes_with_audit,
)

TOLERANCE_YEN = 2
ORACLE_RANDOM_CASES = 100


def _item(
    price: int,
    *,
    tax_rate: int | None = None,
    tax_treatment: str = "excluded",
) -> ReceiptItem:
    return ReceiptItem(
        name="テスト品目",
        price=price,
        price_raw=price,
        tax_rate=tax_rate,
        tax_treatment=tax_treatment,
    )


def _breakdown(
    tax_rate: int,
    taxable_amount: int,
    tax_amount: int,
    *,
    tax_treatment: str = "excluded",
) -> ReceiptTaxBreakdown:
    return ReceiptTaxBreakdown(
        tax_rate=tax_rate,
        taxable_amount=taxable_amount,
        tax_amount=tax_amount,
        tax_treatment=tax_treatment,
    )


def _prices(
    items: list[ReceiptItem],
    breakdowns: list[ReceiptTaxBreakdown],
    *,
    total: int,
) -> list[int]:
    return [item.price for item in normalize_item_taxes(items, breakdowns, total=total)]


def _powerset_indexes(size: int) -> list[tuple[int, ...]]:
    return [
        indexes
        for count in range(size + 1)
        for indexes in combinations(range(size), count)
    ]


@dataclass(frozen=True)
class _OracleCandidate:
    tax_rate: int
    taxable_amount: int
    tax_amount: int
    indexes: tuple[int, ...]


def _oracle_assignment(
    items: list[ReceiptItem],
    breakdowns: list[ReceiptTaxBreakdown],
    *,
    total: int,
) -> tuple[tuple[int, tuple[int, ...]], ...] | None:
    """小規模テスト専用の、最適化実装から独立した総当たりオラクル。"""
    raw_prices = [
        item.price if item.price_raw is None else item.price_raw for item in items
    ]
    raw_total = sum(raw_prices)
    if raw_total == total:
        return None

    external = [
        breakdown
        for breakdown in breakdowns
        if breakdown.tax_treatment.value == "excluded"
    ]
    if (
        len(external) > 8
        or any(
            breakdown.tax_rate not in {8, 10}
            or breakdown.taxable_amount < 0
            or breakdown.tax_amount < 0
            for breakdown in external
        )
        or not external
    ):
        return None

    grouped: dict[int, tuple[int, int]] = {}
    for breakdown in external:
        taxable_amount, tax_amount = grouped.get(breakdown.tax_rate, (0, 0))
        grouped[breakdown.tax_rate] = (
            taxable_amount + breakdown.taxable_amount,
            tax_amount + breakdown.tax_amount,
        )

    candidates_by_rate: list[list[_OracleCandidate]] = []
    for tax_rate, (taxable_amount, tax_amount) in sorted(grouped.items()):
        if tax_amount == 0:
            candidates_by_rate.append([])
            continue
        possible_totals = {
            possible_total
            for possible_total in {
                taxable_amount,
                taxable_amount - tax_amount,
            }
            if possible_total >= 0
            and abs(Fraction(possible_total * tax_rate, 100) - tax_amount)
            <= TOLERANCE_YEN
        }
        candidates: set[_OracleCandidate] = set()
        for indexes in _powerset_indexes(len(items)):
            if not indexes:
                continue
            if any(
                items[index].tax_treatment.value != "excluded"
                or items[index].tax_rate not in {None, tax_rate}
                or items[index].price_raw is None
                for index in indexes
            ):
                continue
            if any(
                item.tax_treatment.value == "excluded"
                and item.tax_rate == tax_rate
                and index not in indexes
                for index, item in enumerate(items)
            ):
                continue
            subtotal = sum(raw_prices[index] for index in indexes)
            if any(
                abs(subtotal - possible_total) <= TOLERANCE_YEN
                for possible_total in possible_totals
            ):
                candidates.add(
                    _OracleCandidate(
                        tax_rate=tax_rate,
                        taxable_amount=taxable_amount,
                        tax_amount=tax_amount,
                        indexes=indexes,
                    )
                )
        candidates_by_rate.append(
            sorted(
                candidates,
                key=lambda candidate: (
                    candidate.tax_rate,
                    candidate.indexes,
                    candidate.taxable_amount,
                    candidate.tax_amount,
                ),
            )
        )

    best_error: int | None = None
    best: set[tuple[_OracleCandidate, ...]] = set()
    choices = [[None, *candidates] for candidates in candidates_by_rate]
    for selection_with_none in product(*choices):
        selection = tuple(
            candidate for candidate in selection_with_none if candidate is not None
        )
        if not selection:
            continue
        indexes = [index for candidate in selection for index in candidate.indexes]
        if len(indexes) != len(set(indexes)):
            continue
        error = abs(
            raw_total + sum(candidate.tax_amount for candidate in selection) - total
        )
        if best_error is None or error < best_error:
            best_error = error
            best = {selection}
        elif error == best_error:
            best.add(selection)

    if best_error != 0 or len(best) != 1:
        return None
    selected = next(iter(best))
    return tuple((candidate.tax_rate, candidate.indexes) for candidate in selected)


@pytest.mark.parametrize(
    ("tax_rate", "raw_price", "tax_amount"),
    [
        (8, 100, 8),
        (10, 100, 10),
    ],
)
def test_single_external_tax_rate_is_normalized(
    tax_rate: int,
    raw_price: int,
    tax_amount: int,
) -> None:
    result = normalize_item_taxes_with_audit(
        [_item(raw_price, tax_rate=tax_rate)],
        [_breakdown(tax_rate, raw_price, tax_amount)],
        total=raw_price + tax_amount,
    )

    assert [item.price for item in result.items] == [raw_price + tax_amount]
    assert [item.tax_adjustment for item in result.items] == [tax_amount]
    assert result.audit.applied
    assert result.audit.reason is TaxNormalizationReason.APPLIED
    assert result.audit.assignments[0].item_indexes == (0,)


def test_internal_tax_is_not_added_twice() -> None:
    normalized = normalize_item_taxes(
        [_item(570, tax_rate=10, tax_treatment="included")],
        [_breakdown(10, 570, 51, tax_treatment="included")],
        total=570,
    )

    assert normalized[0].price == 570
    assert normalized[0].tax_adjustment == 0


def test_zero_external_tax_does_not_change_items() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(3)],
        [_breakdown(10, 3, 0)],
        total=3,
    )

    assert [item.price for item in result.items] == [3]
    assert result.audit.reason is TaxNormalizationReason.NOT_NEEDED
    assert not result.audit.applied


def test_small_external_tax_is_not_hidden_by_business_tolerance() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(10, tax_rate=10)],
        [_breakdown(10, 10, 1)],
        total=11,
    )

    assert [item.price for item in result.items] == [11]
    assert result.audit.applied


def test_external_tax_is_distributed_deterministically_with_rounding() -> None:
    items = [_item(33, tax_rate=8), _item(67, tax_rate=8)]
    breakdowns = [_breakdown(8, 100, 8)]

    results = [
        normalize_item_taxes_with_audit(items, breakdowns, total=108) for _ in range(20)
    ]

    assert {
        tuple(item.tax_adjustment for item in result.items) for result in results
    } == {(3, 5)}
    assert {
        tuple(assignment.item_indexes for assignment in result.audit.assignments)
        for result in results
    } == {((0, 1),)}


def test_discount_participates_in_external_tax_allocation() -> None:
    normalized = normalize_item_taxes(
        [_item(1000, tax_rate=10), _item(-100, tax_rate=10)],
        [_breakdown(10, 900, 90)],
        total=990,
    )

    assert [item.tax_adjustment for item in normalized] == [100, -10]
    assert [item.price for item in normalized] == [1100, -110]


def test_missing_external_breakdown_does_not_guess_tax() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(140, tax_rate=8)],
        [],
        total=151,
    )

    assert [item.price for item in result.items] == [140]
    assert result.audit.reason is TaxNormalizationReason.MISSING_EVIDENCE


def test_tax_inclusive_target_is_safe_evidence() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(140), _item(570, tax_treatment="included")],
        [
            _breakdown(8, 151, 11),
            _breakdown(10, 570, 51, tax_treatment="included"),
        ],
        total=721,
    )

    assert [item.price for item in result.items] == [151, 570]
    assert [item.tax_rate for item in result.items] == [8, None]


def test_multiple_rates_are_assigned_exclusively_when_globally_unique() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100), _item(200)],
        [_breakdown(8, 100, 8), _breakdown(10, 200, 20)],
        total=328,
    )

    assert [item.price for item in result.items] == [108, 220]
    assert [
        (assignment.tax_rate, assignment.item_indexes)
        for assignment in result.audit.assignments
    ] == [(8, (0,)), (10, (1,))]


def test_zero_tax_group_does_not_block_other_unique_group() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100), _item(3)],
        [_breakdown(8, 100, 8), _breakdown(10, 3, 0)],
        total=111,
    )

    assert [item.price for item in result.items] == [108, 3]
    assert [
        (assignment.tax_rate, assignment.item_indexes)
        for assignment in result.audit.assignments
    ] == [(8, (0,))]


def test_same_item_is_not_assigned_to_two_tax_rates() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100)],
        [_breakdown(8, 100, 8), _breakdown(10, 100, 10)],
        total=118,
    )

    assert [item.price for item in result.items] == [100]
    assert not result.audit.applied


def test_matching_taxable_amount_but_nonmatching_final_total_is_rejected() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100)],
        [_breakdown(8, 100, 8)],
        total=109,
    )

    assert [item.price for item in result.items] == [100]
    assert result.audit.reason is TaxNormalizationReason.TOTAL_MISMATCH


def test_implausible_tax_candidate_is_excluded() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(600), _item(148), _item(5800, tax_treatment="included"), _item(3)],
        [
            _breakdown(8, 748, 59),
            _breakdown(10, 3, 3),
        ],
        total=6610,
    )

    assert [item.price for item in result.items] == [647, 160, 5800, 3]
    assert [
        (assignment.tax_rate, assignment.item_indexes)
        for assignment in result.audit.assignments
    ] == [(8, (0, 1))]


def test_equal_best_tax_rate_candidates_are_rejected() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100)],
        [_breakdown(8, 100, 9), _breakdown(10, 100, 9)],
        total=109,
    )

    assert [item.price for item in result.items] == [100]
    assert result.audit.reason is TaxNormalizationReason.AMBIGUOUS


def test_breakdown_order_does_not_change_result() -> None:
    items = [_item(100), _item(200)]
    breakdowns = [_breakdown(8, 100, 8), _breakdown(10, 200, 20)]

    forward = normalize_item_taxes_with_audit(items, breakdowns, total=328)
    reverse = normalize_item_taxes_with_audit(
        items,
        list(reversed(breakdowns)),
        total=328,
    )

    assert forward == reverse


def test_equal_price_items_are_distinct_ambiguous_index_combinations() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100), _item(100)],
        [_breakdown(8, 100, 8)],
        total=208,
    )

    assert [item.price for item in result.items] == [100, 100]
    assert result.audit.reason is TaxNormalizationReason.AMBIGUOUS


def test_different_index_combinations_with_same_subtotal_are_ambiguous() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100), _item(50), _item(150)],
        [_breakdown(8, 150, 12)],
        total=312,
    )

    assert [item.price for item in result.items] == [100, 50, 150]
    assert result.audit.reason is TaxNormalizationReason.AMBIGUOUS


def test_globally_ambiguous_rates_are_rejected_even_when_each_has_one_subset() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100)],
        [_breakdown(8, 100, 9), _breakdown(10, 100, 9)],
        total=109,
    )

    assert result.audit.reason is TaxNormalizationReason.AMBIGUOUS
    assert result.audit.assignments == ()


def test_discount_and_non_discount_matches_are_ambiguous() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100), _item(-20), _item(80)],
        [_breakdown(10, 80, 8)],
        total=168,
    )

    assert [item.price for item in result.items] == [100, -20, 80]
    assert result.audit.reason is TaxNormalizationReason.AMBIGUOUS


@pytest.mark.parametrize(
    ("breakdown", "expected_reason"),
    [
        (_breakdown(8, 500, 40), TaxNormalizationReason.NO_MATCH),
        (_breakdown(8, -100, 8), TaxNormalizationReason.INCONSISTENT_INPUT),
        (_breakdown(8, 100, -8), TaxNormalizationReason.INCONSISTENT_INPUT),
        (_breakdown(5, 100, 5), TaxNormalizationReason.INCONSISTENT_INPUT),
    ],
)
def test_unsafe_tax_metadata_is_rejected(
    breakdown: ReceiptTaxBreakdown,
    expected_reason: TaxNormalizationReason,
) -> None:
    result = normalize_item_taxes_with_audit(
        [_item(100)],
        [breakdown],
        total=108,
    )

    assert [item.price for item in result.items] == [100]
    assert result.audit.reason is expected_reason


def test_empty_tax_breakdown_never_creates_tax_from_total_difference() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(900)],
        [],
        total=1100,
    )

    assert [item.price for item in result.items] == [900]
    assert result.audit.reason is TaxNormalizationReason.MISSING_EVIDENCE


def test_unexplained_total_difference_is_not_absorbed_as_tax() -> None:
    result = normalize_item_taxes_with_audit(
        [_item(900)],
        [_breakdown(10, 800, 80)],
        total=1100,
    )

    assert [item.price for item in result.items] == [900]
    assert result.audit.reason in {
        TaxNormalizationReason.NO_MATCH,
        TaxNormalizationReason.TOTAL_MISMATCH,
    }


def test_search_state_limit_rejects_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tax_module, "MAX_SUBSET_SUM_STATES", 2)

    result = normalize_item_taxes_with_audit(
        [_item(50), _item(50), _item(50)],
        [_breakdown(8, 100, 8)],
        total=158,
    )

    assert [item.price for item in result.items] == [50, 50, 50]
    assert result.audit.reason is TaxNormalizationReason.SEARCH_LIMIT
    assert result.audit.search_limit_reached
    assert result.audit.search_states == 2


def test_external_group_limit_rejects_without_raising() -> None:
    breakdowns = [_breakdown(8 if index % 2 == 0 else 10, 100, 8) for index in range(9)]

    result = normalize_item_taxes_with_audit(
        [_item(100)],
        breakdowns,
        total=108,
    )

    assert [item.price for item in result.items] == [100]
    assert result.audit.reason is TaxNormalizationReason.GROUP_LIMIT
    assert result.audit.search_limit_reached


@pytest.mark.parametrize("item_count", [20, 40])
def test_many_known_rate_items_with_two_rates_are_normalized(
    item_count: int,
) -> None:
    prices = [100 + index * 7 for index in range(item_count)]
    midpoint = item_count // 2
    eight_percent_target = sum(prices[:midpoint])
    ten_percent_target = sum(prices[midpoint:])
    eight_percent_tax = round(eight_percent_target * 8 / 100)
    ten_percent_tax = round(ten_percent_target * 10 / 100)
    result = normalize_item_taxes_with_audit(
        [
            _item(price, tax_rate=8 if index < midpoint else 10)
            for index, price in enumerate(prices)
        ],
        [
            _breakdown(8, eight_percent_target, eight_percent_tax),
            _breakdown(10, ten_percent_target, ten_percent_tax),
        ],
        total=sum(prices) + eight_percent_tax + ten_percent_tax,
    )

    assert result.audit.search_states <= tax_module.MAX_SUBSET_SUM_STATES
    assert result.audit.applied
    assert sum(item.price for item in result.items) == (
        sum(prices) + eight_percent_tax + ten_percent_tax
    )


def test_many_unknown_rate_items_are_rejected_at_search_limit() -> None:
    prices = [100 + index * 7 for index in range(40)]
    midpoint = len(prices) // 2
    eight_percent_target = sum(prices[:midpoint])
    ten_percent_target = sum(prices[midpoint:])
    eight_percent_tax = round(eight_percent_target * 8 / 100)
    ten_percent_tax = round(ten_percent_target * 10 / 100)

    result = normalize_item_taxes_with_audit(
        [_item(price) for price in prices],
        [
            _breakdown(8, eight_percent_target, eight_percent_tax),
            _breakdown(10, ten_percent_target, ten_percent_tax),
        ],
        total=sum(prices) + eight_percent_tax + ten_percent_tax,
    )

    assert result.audit.reason is TaxNormalizationReason.SEARCH_LIMIT
    assert result.audit.search_limit_reached
    assert result.audit.search_states == tax_module.MAX_SUBSET_SUM_STATES
    assert not result.audit.applied


@pytest.mark.parametrize(
    ("prices", "breakdowns", "total"),
    [
        ([100, 250], [_breakdown(8, 150, 12)], 362),
        ([100, 100], [_breakdown(8, 100, 8)], 208),
        (
            [100, 200],
            [_breakdown(8, 100, 8), _breakdown(10, 200, 20)],
            328,
        ),
        (
            [100],
            [_breakdown(8, 100, 8), _breakdown(10, 100, 10)],
            118,
        ),
        ([100, 100, 50], [_breakdown(8, 150, 12)], 262),
        ([100, -20, 80], [_breakdown(10, 80, 8)], 168),
    ],
)
def test_optimized_search_matches_exhaustive_oracle_for_named_cases(
    prices: list[int],
    breakdowns: list[ReceiptTaxBreakdown],
    total: int,
) -> None:
    items = [_item(price) for price in prices]
    expected = _oracle_assignment(items, breakdowns, total=total)

    result = normalize_item_taxes_with_audit(items, breakdowns, total=total)
    actual = (
        tuple(
            (assignment.tax_rate, assignment.item_indexes)
            for assignment in result.audit.assignments
        )
        if result.audit.applied
        else None
    )

    assert actual == expected


def test_optimized_search_matches_seeded_exhaustive_oracle() -> None:
    random = Random(20260726)

    for _ in range(ORACLE_RANDOM_CASES):
        item_count = random.randint(2, 7)
        prices = [
            random.choice(
                [
                    -random.randint(1, 30),
                    random.randint(20, 250),
                    random.randint(20, 250),
                ]
            )
            for _ in range(item_count)
        ]
        if sum(prices) <= 0:
            prices[0] += abs(sum(prices)) + 100
        items = [_item(price) for price in prices]

        rates = [8] if random.random() < 0.5 else [8, 10]
        breakdowns: list[ReceiptTaxBreakdown] = []
        for tax_rate in rates:
            selected = [index for index in range(item_count) if random.random() < 0.45]
            if not selected:
                selected = [random.randrange(item_count)]
            taxable_amount = sum(prices[index] for index in selected)
            if taxable_amount <= 0:
                taxable_amount = random.randint(40, 300)
            if random.random() < 0.2:
                taxable_amount += 7
            tax_amount = round(taxable_amount * tax_rate / 100)
            breakdowns.append(_breakdown(tax_rate, taxable_amount, tax_amount))

        raw_total = sum(prices)
        declared_tax = sum(breakdown.tax_amount for breakdown in breakdowns)
        total = random.choice(
            [
                raw_total,
                raw_total + declared_tax,
                raw_total
                + random.choice([breakdown.tax_amount for breakdown in breakdowns]),
                raw_total + declared_tax + 3,
            ]
        )
        expected = _oracle_assignment(items, breakdowns, total=total)

        result = normalize_item_taxes_with_audit(
            items,
            breakdowns,
            total=total,
        )
        actual = (
            tuple(
                (assignment.tax_rate, assignment.item_indexes)
                for assignment in result.audit.assignments
            )
            if result.audit.applied
            else None
        )

        assert actual == expected
