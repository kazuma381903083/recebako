from __future__ import annotations

from datetime import date

from recebako.domain import ReceiptStatus, TaxTreatment
from recebako.evaluation.models import EvaluationStatus
from recebako.evaluation.quality import (
    QUALITY_METRIC_VERSION,
    REQUIRED_VERIFIED_CASE_COUNT,
    _item_alignment_counts,
    _normalize_store_name,
    _QualityAccumulator,
)
from recebako.evaluation.truth import GroundTruthCase, GroundTruthItem
from recebako.pipeline import ProcessResult
from recebako.storage import StoredItem


def _truth(
    *,
    human_verified: bool = True,
    store: str = "Expected Store",
    expected_date: date = date(2026, 7, 1),
    total: int = 300,
    items: tuple[GroundTruthItem, ...] = (),
) -> GroundTruthCase:
    return GroundTruthCase(
        case_id="case-0001",
        human_verified=human_verified,
        expected_store=store if human_verified else None,
        expected_date=expected_date if human_verified else None,
        expected_total=total if human_verified else None,
        expected_status=(EvaluationStatus.CONFIRMED if human_verified else None),
        items=items if human_verified else (),
    )


def _truth_item(
    index: int,
    name: str,
    *,
    qty: int = 1,
    price: int = 100,
) -> GroundTruthItem:
    return GroundTruthItem(
        item_index=index,
        expected_item_name=name,
        expected_item_qty=qty,
        expected_item_price=price,
    )


def _result(
    *,
    status: ReceiptStatus = ReceiptStatus.CONFIRMED,
    store: str = "Expected Store",
    receipt_date: str = "2026-07-01",
    total: int = 300,
) -> ProcessResult:
    return ProcessResult(
        receipt_id=1,
        status=status,
        duplicate_of_id=None,
        validation_issues=[],
        store=store,
        date_raw=receipt_date,
        date=receipt_date,
        total=total,
        phash="safe-test-hash",
    )


def _stored_item(
    item_id: int,
    name: str,
    *,
    qty: int = 1,
    price: int = 100,
    price_raw: int | None = None,
) -> StoredItem:
    return StoredItem(
        id=item_id,
        receipt_id=1,
        name=name,
        name_norm=None,
        qty=qty,
        price=price,
        price_raw=price if price_raw is None else price_raw,
        tax_rate=None,
        tax_treatment=TaxTreatment.INCLUDED,
        tax_adjustment=0,
        category=None,
    )


def test_quality_normalizes_store_but_keeps_item_names_raw() -> None:
    truth = _truth(
        store="Ａ\u3000B\u00a0İ",
        items=(_truth_item(0, "ＴＥ\u2003STİ"),),
    )
    accumulator = _QualityAccumulator()

    accumulator.observe(
        truth,
        _result(store="a b i\u0307"),
        (_stored_item(1, "te\tsti\u0307"),),
    )

    counts = accumulator.counts
    assert QUALITY_METRIC_VERSION == "quality-v1"
    assert REQUIRED_VERIFIED_CASE_COUNT == 30
    assert _normalize_store_name("Ａ\u3000B\u00a0İ") == "abi\u0307"
    assert _normalize_store_name(" A-B_Ｃ ") == "a-b_c"
    assert counts.store_correct_count == 1
    assert counts.item_correct_count == 0
    assert counts.item_comparable_count == 1


def test_quality_lcs_keeps_alignment_when_the_first_actual_item_is_missing() -> None:
    truth = _truth(
        items=(
            _truth_item(0, "first", price=10),
            _truth_item(1, "second", price=20),
            _truth_item(2, "third", price=30),
        ),
    )
    accumulator = _QualityAccumulator()

    accumulator.observe(
        truth,
        _result(),
        (
            _stored_item(1, "second", price=20),
            _stored_item(2, "third", price=30),
        ),
    )

    counts = accumulator.counts
    assert counts.item_comparable_count == 3
    assert counts.item_correct_count == 2


def test_quality_item_denominator_uses_the_larger_item_count() -> None:
    truth = _truth(
        items=(
            _truth_item(0, "first", price=10),
            _truth_item(1, "second", price=20),
        ),
    )
    accumulator = _QualityAccumulator()

    accumulator.observe(
        truth,
        _result(),
        (
            _stored_item(1, "first", price=10),
            _stored_item(2, "second", price=20),
            _stored_item(3, "extra", price=30),
        ),
    )

    counts = accumulator.counts
    assert counts.item_comparable_count == 3
    assert counts.item_correct_count == 2


def test_quality_lcs_counts_duplicate_items_by_occurrence() -> None:
    truth = _truth(
        items=(
            _truth_item(0, "same"),
            _truth_item(1, "same"),
            _truth_item(2, "other"),
        ),
    )
    accumulator = _QualityAccumulator()

    accumulator.observe(
        truth,
        _result(),
        (
            _stored_item(1, "same"),
            _stored_item(2, "other"),
        ),
    )

    counts = accumulator.counts
    assert counts.item_comparable_count == 3
    assert counts.item_correct_count == 2


def test_quality_lcs_preserves_item_order() -> None:
    expected = (
        _truth_item(0, "first", price=10),
        _truth_item(1, "second", price=20),
    )
    actual = (
        _stored_item(1, "second", price=20),
        _stored_item(2, "first", price=10),
    )

    assert _item_alignment_counts(expected, actual) == (2, 1)


def test_quality_item_denominator_sums_each_cases_larger_item_count() -> None:
    accumulator = _QualityAccumulator()
    accumulator.observe(
        _truth(
            items=(
                _truth_item(0, "first", price=10),
                _truth_item(1, "second", price=20),
            )
        ),
        _result(),
        (_stored_item(1, "first", price=10),),
    )
    accumulator.observe(
        _truth(items=(_truth_item(0, "third", price=30),)),
        _result(),
        (
            _stored_item(2, "third", price=30),
            _stored_item(3, "extra-1", price=40),
            _stored_item(4, "extra-2", price=50),
        ),
    )

    counts = accumulator.counts
    assert counts.item_comparable_count == 5
    assert counts.item_correct_count == 2


def test_failed_result_counts_as_verified_mismatches_and_empty_actual_items() -> None:
    private_sentinel = "PRIVATE-SENTINEL"
    truth = _truth(
        store=private_sentinel,
        items=(_truth_item(0, private_sentinel),),
    )
    supplied_private_item = _stored_item(1, private_sentinel)
    accumulator = _QualityAccumulator()

    accumulator.observe(truth, None, (supplied_private_item,))

    counts = accumulator.counts
    assert counts.verified_case_count == 1
    assert counts.store_correct_count == 0
    assert counts.date_correct_count == 0
    assert counts.total_correct_count == 0
    assert counts.item_comparable_count == 1
    assert counts.item_correct_count == 0
    assert counts.confirmed_count == 0
    assert private_sentinel not in repr(accumulator)
    assert private_sentinel not in repr(counts)


def test_confirmed_total_mismatch_is_counted_only_for_actual_confirmed_results() -> (
    None
):
    truth = _truth(items=(_truth_item(0, "item"),))
    accumulator = _QualityAccumulator()

    accumulator.observe(
        truth,
        _result(status=ReceiptStatus.CONFIRMED, total=301),
        (_stored_item(1, "item"),),
    )
    accumulator.observe(
        truth,
        _result(status=ReceiptStatus.REVIEW, total=302),
        (_stored_item(2, "item"),),
    )

    counts = accumulator.counts
    assert counts.verified_case_count == 2
    assert counts.total_correct_count == 0
    assert counts.confirmed_count == 1
    assert counts.false_confirmed_count == 1


def test_unverified_cases_do_not_change_quality_counts() -> None:
    accumulator = _QualityAccumulator()

    accumulator.observe(
        _truth(human_verified=False),
        _result(),
        (_stored_item(1, "ignored"),),
    )

    assert accumulator.counts.verified_case_count == 0


def test_item_alignment_requires_raw_name_quantity_and_normalized_price() -> None:
    expected = (
        _truth_item(0, "same", qty=2, price=200),
        _truth_item(1, "same", qty=1, price=100),
        _truth_item(2, "normalized-price", qty=1, price=300),
    )
    actual = (
        _stored_item(1, "same", qty=1, price=200),
        _stored_item(2, "same", qty=1, price=100),
        _stored_item(
            3,
            "normalized-price",
            qty=1,
            price=300,
            price_raw=250,
        ),
    )

    assert _item_alignment_counts(expected, actual) == (3, 2)


def test_item_alignment_does_not_normalize_raw_item_name() -> None:
    expected = (_truth_item(0, "Ａ item", qty=1, price=100),)
    actual = (_stored_item(1, "a item", qty=1, price=100),)

    assert _item_alignment_counts(expected, actual) == (1, 0)
