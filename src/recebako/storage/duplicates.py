from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from recebako.domain import NormalizedReceiptExtraction, ReceiptFileState

# This threshold is intentionally configurable: pHash distance quality depends on
# receipt capture conditions and must be tuned with production observations.
DEFAULT_PHASH_DISTANCE_THRESHOLD = 5


@dataclass(frozen=True)
class DuplicateCandidate:
    receipt_id: int
    match_type: str
    phash_distance: int | None


def phash_hamming_distance(left: str, right: str) -> int | None:
    if len(left) != len(right) or not left:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def find_duplicate_candidate(
    connection: sqlite3.Connection,
    receipt: NormalizedReceiptExtraction,
    *,
    phash: str,
    phash_distance_threshold: int = DEFAULT_PHASH_DISTANCE_THRESHOLD,
) -> DuplicateCandidate | None:
    rows = connection.execute(
        """
        SELECT id, store, date, total, phash
        FROM receipts
        WHERE file_state = ?
        ORDER BY id
        """,
        (ReceiptFileState.FINALIZED.value,),
    ).fetchall()

    exact_candidates: list[DuplicateCandidate] = []
    phash_candidates: list[DuplicateCandidate] = []
    for row in rows:
        distance = phash_hamming_distance(phash, row["phash"])
        is_exact = (
            row["store"] == receipt.store
            and row["date"] == receipt.date
            and row["total"] == receipt.total
        )
        candidate = DuplicateCandidate(
            receipt_id=row["id"],
            match_type="identity" if is_exact else "phash",
            phash_distance=distance,
        )
        if is_exact:
            exact_candidates.append(candidate)
        elif distance is not None and distance <= phash_distance_threshold:
            phash_candidates.append(candidate)

    if exact_candidates:
        return min(
            exact_candidates,
            key=lambda candidate: (
                candidate.phash_distance
                if candidate.phash_distance is not None
                else float("inf"),
                candidate.receipt_id,
            ),
        )
    if phash_candidates:
        return min(
            phash_candidates,
            key=lambda candidate: (
                candidate.phash_distance
                if candidate.phash_distance is not None
                else float("inf"),
                candidate.receipt_id,
            ),
        )
    return None
