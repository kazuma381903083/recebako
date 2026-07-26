from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from recebako.domain import ReceiptStatus
from recebako.evaluation.truth import GroundTruthCase, GroundTruthItem
from recebako.pipeline import ProcessResult
from recebako.storage import StoredItem

QUALITY_METRIC_VERSION = "quality-v1"
REQUIRED_VERIFIED_CASE_COUNT = 30

_ItemIdentity = tuple[str, int, int]


def _normalize_store_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _expected_item_identity(item: GroundTruthItem) -> _ItemIdentity:
    return (
        item.expected_item_name,
        item.expected_item_qty,
        item.expected_item_price,
    )


def _actual_item_identity(item: StoredItem) -> _ItemIdentity:
    return (
        item.name,
        item.qty,
        item.price,
    )


def _lcs_match_count(
    expected: Sequence[_ItemIdentity],
    actual: Sequence[_ItemIdentity],
) -> int:
    if not expected or not actual:
        return 0

    previous = [0] * (len(actual) + 1)
    for expected_item in expected:
        current = [0]
        for actual_index, actual_item in enumerate(actual, start=1):
            if expected_item == actual_item:
                current.append(previous[actual_index - 1] + 1)
            else:
                current.append(max(previous[actual_index], current[-1]))
        previous = current
    return previous[-1]


@dataclass(frozen=True, slots=True)
class _QualityCounts:
    verified_case_count: int
    store_correct_count: int
    date_correct_count: int
    total_correct_count: int
    item_comparable_count: int
    item_correct_count: int
    confirmed_count: int
    false_confirmed_count: int


def _item_alignment_counts(
    expected_items: Sequence[GroundTruthItem],
    actual_items: Sequence[StoredItem],
) -> tuple[int, int]:
    expected_identities = tuple(
        _expected_item_identity(item) for item in expected_items
    )
    actual_identities = tuple(_actual_item_identity(item) for item in actual_items)
    comparable_count = max(len(expected_identities), len(actual_identities))
    correct_count = _lcs_match_count(expected_identities, actual_identities)
    return comparable_count, correct_count


@dataclass(slots=True)
class _QualityAccumulator:
    verified_case_count: int = 0
    store_correct_count: int = 0
    date_correct_count: int = 0
    total_correct_count: int = 0
    item_comparable_count: int = 0
    item_correct_count: int = 0
    confirmed_count: int = 0
    false_confirmed_count: int = 0

    def observe(
        self,
        truth: GroundTruthCase,
        result: ProcessResult | None,
        items: Sequence[StoredItem],
    ) -> None:
        if not truth.human_verified:
            return

        self.verified_case_count += 1
        expected_date = (
            truth.expected_date.isoformat() if truth.expected_date is not None else None
        )
        store_matches = (
            result is not None
            and truth.expected_store is not None
            and _normalize_store_name(result.store)
            == _normalize_store_name(truth.expected_store)
        )
        date_matches = result is not None and result.date == expected_date
        total_matches = result is not None and result.total == truth.expected_total
        self.store_correct_count += int(store_matches)
        self.date_correct_count += int(date_matches)
        self.total_correct_count += int(total_matches)

        actual_items = items if result is not None else ()
        comparable_count, correct_count = _item_alignment_counts(
            truth.items,
            actual_items,
        )
        self.item_comparable_count += comparable_count
        self.item_correct_count += correct_count

        if result is not None and result.status is ReceiptStatus.CONFIRMED:
            self.confirmed_count += 1
            self.false_confirmed_count += int(not total_matches)

    @property
    def counts(self) -> _QualityCounts:
        return _QualityCounts(
            verified_case_count=self.verified_case_count,
            store_correct_count=self.store_correct_count,
            date_correct_count=self.date_correct_count,
            total_correct_count=self.total_correct_count,
            item_comparable_count=self.item_comparable_count,
            item_correct_count=self.item_correct_count,
            confirmed_count=self.confirmed_count,
            false_confirmed_count=self.false_confirmed_count,
        )
