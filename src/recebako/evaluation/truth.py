from __future__ import annotations

import csv
import os
import re
import stat
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

from recebako.evaluation.models import EvaluationStatus

TRUTH_CSV_HEADERS = (
    "case_id",
    "human_verified",
    "expected_store",
    "expected_date",
    "expected_total",
    "expected_status",
    "item_index",
    "expected_item_name",
    "expected_item_qty",
    "expected_item_price",
)


class TruthErrorCode(str, Enum):
    ABSOLUTE_PATH_REQUIRED = "truth.absolute_path_required"
    SOURCE_UNAVAILABLE = "truth.source_unavailable"
    FILE_REQUIRED = "truth.file_required"
    SOURCE_IN_GIT = "truth.source_in_git"
    SYMLINK_REJECTED = "truth.symlink_rejected"
    INVALID_HEADER = "truth.invalid_header"
    INVALID_ROW = "truth.invalid_row"
    UNKNOWN_CASE_ID = "truth.unknown_case_id"
    DUPLICATE_ITEM = "truth.duplicate_item"
    INCONSISTENT_CASE = "truth.inconsistent_case"
    NON_CONTIGUOUS_ITEMS = "truth.non_contiguous_items"
    EMPTY = "truth.empty"


_SAFE_ERROR_MESSAGES = {
    TruthErrorCode.ABSOLUTE_PATH_REQUIRED: (
        "正解CSVはGitワークツリー外の絶対パスで指定してください"
    ),
    TruthErrorCode.SOURCE_UNAVAILABLE: "正解CSVを安全に確認できません",
    TruthErrorCode.FILE_REQUIRED: "正解データには単一のCSVファイルを指定してください",
    TruthErrorCode.SOURCE_IN_GIT: "正解CSVはGitワークツリー外に置いてください",
    TruthErrorCode.SYMLINK_REJECTED: "正解CSVではsymlinkを使用できません",
    TruthErrorCode.INVALID_HEADER: "正解CSVのheaderが契約と一致しません",
    TruthErrorCode.INVALID_ROW: "正解CSVに不正な行があります",
    TruthErrorCode.UNKNOWN_CASE_ID: "正解CSVに未知のcase IDがあります",
    TruthErrorCode.DUPLICATE_ITEM: "正解CSVに重複する品目行があります",
    TruthErrorCode.INCONSISTENT_CASE: "正解CSVのcase反復値が一致しません",
    TruthErrorCode.NON_CONTIGUOUS_ITEMS: (
        "正解CSVのitem_indexは0から連続させてください"
    ),
    TruthErrorCode.EMPTY: "正解CSVにデータ行がありません",
}


class GroundTruthError(ValueError):
    def __init__(self, code: TruthErrorCode) -> None:
        self.code = code
        super().__init__(_SAFE_ERROR_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class GroundTruthItem:
    item_index: int
    expected_item_name: str
    expected_item_qty: int
    expected_item_price: int


@dataclass(frozen=True, slots=True)
class GroundTruthCase:
    case_id: str
    human_verified: bool
    expected_store: str | None
    expected_date: date | None
    expected_total: int | None
    expected_status: EvaluationStatus | None
    items: tuple[GroundTruthItem, ...]


@dataclass(frozen=True, slots=True)
class GroundTruthDataset:
    cases: dict[str, GroundTruthCase]

    def get(self, case_id: str) -> GroundTruthCase | None:
        return self.cases.get(case_id)

    @property
    def verified_cases(self) -> tuple[GroundTruthCase, ...]:
        return tuple(case for case in self.cases.values() if case.human_verified)


def load_ground_truth_csv(
    path: Path,
    known_case_ids: Collection[str],
) -> GroundTruthDataset:
    source = Path(path)
    expected_identity = _validate_truth_path(source)

    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(source, flags)
        if _truth_identity(os.fstat(descriptor)) != expected_identity:
            raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE)
        with os.fdopen(
            descriptor,
            "r",
            encoding="utf-8-sig",
            newline="",
            closefd=True,
        ) as stream:
            descriptor = -1
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != TRUTH_CSV_HEADERS:
                raise GroundTruthError(TruthErrorCode.INVALID_HEADER)
            rows = list(reader)
            if _truth_identity(os.fstat(stream.fileno())) != expected_identity:
                raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE)
        if _truth_identity(source.lstat()) != expected_identity:
            raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE)
    except GroundTruthError:
        raise
    except (OSError, UnicodeError, csv.Error):
        raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not rows:
        raise GroundTruthError(TruthErrorCode.EMPTY)

    known = set(known_case_ids)
    grouped_rows: dict[str, list[dict[str | None, str | list[str] | None]]] = {}
    for row in rows:
        if None in row or any(row.get(header) is None for header in TRUTH_CSV_HEADERS):
            raise GroundTruthError(TruthErrorCode.INVALID_ROW)
        case_id_value = row["case_id"]
        if not isinstance(case_id_value, str) or case_id_value not in known:
            raise GroundTruthError(TruthErrorCode.UNKNOWN_CASE_ID)
        grouped_rows.setdefault(case_id_value, []).append(row)

    cases = {
        case_id: _parse_case_rows(case_id, case_rows)
        for case_id, case_rows in grouped_rows.items()
    }
    return GroundTruthDataset(cases=cases)


def _validate_truth_path(source: Path) -> tuple[int, int, int, int, int]:
    if not source.is_absolute():
        raise GroundTruthError(TruthErrorCode.ABSOLUTE_PATH_REQUIRED)
    _reject_truth_symlink_components(source)
    try:
        source_stat = source.stat(follow_symlinks=False)
    except OSError:
        raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE) from None
    if not stat.S_ISREG(source_stat.st_mode):
        raise GroundTruthError(TruthErrorCode.FILE_REQUIRED)
    try:
        resolved = source.resolve(strict=True)
    except OSError:
        raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE) from None
    if _truth_is_in_git(resolved):
        raise GroundTruthError(TruthErrorCode.SOURCE_IN_GIT)
    return _truth_identity(source_stat)


def _truth_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _reject_truth_symlink_components(path: Path) -> None:
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor /= component
        try:
            component_stat = cursor.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE) from None
        if stat.S_ISLNK(component_stat.st_mode):
            raise GroundTruthError(TruthErrorCode.SYMLINK_REJECTED)


def _truth_is_in_git(path: Path) -> bool:
    for candidate in (path.parent, *path.parents):
        try:
            (candidate / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise GroundTruthError(TruthErrorCode.SOURCE_UNAVAILABLE) from None
        return True
    return False


def _parse_case_rows(
    case_id: str,
    rows: list[dict[str | None, str | list[str] | None]],
) -> GroundTruthCase:
    verified_values = [_parse_verified(row["human_verified"]) for row in rows]
    if len(set(verified_values)) != 1:
        raise GroundTruthError(TruthErrorCode.INCONSISTENT_CASE)
    human_verified = verified_values[0]
    if not human_verified:
        if len(rows) != 1:
            raise GroundTruthError(TruthErrorCode.DUPLICATE_ITEM)
        if any(
            _required_row_text(rows[0][field]) != ""
            for field in TRUTH_CSV_HEADERS
            if field not in {"case_id", "human_verified"}
        ):
            raise GroundTruthError(TruthErrorCode.INVALID_ROW)
        return GroundTruthCase(
            case_id=case_id,
            human_verified=False,
            expected_store=None,
            expected_date=None,
            expected_total=None,
            expected_status=None,
            items=(),
        )

    receipt_fields = (
        "expected_store",
        "expected_date",
        "expected_total",
        "expected_status",
    )
    baseline = tuple(_required_text(rows[0][field]) for field in receipt_fields)
    for row in rows[1:]:
        if tuple(_required_text(row[field]) for field in receipt_fields) != baseline:
            raise GroundTruthError(TruthErrorCode.INCONSISTENT_CASE)

    expected_store, expected_date_raw, expected_total_raw, expected_status_raw = (
        baseline
    )
    if not expected_store.strip():
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    expected_date = _parse_date(expected_date_raw)
    expected_total = _parse_integer(expected_total_raw, minimum=1)
    expected_status = _parse_status(expected_status_raw)

    parsed_items: dict[int, GroundTruthItem] = {}
    for row in rows:
        item_index = _parse_integer(_required_text(row["item_index"]), minimum=0)
        if item_index in parsed_items:
            raise GroundTruthError(TruthErrorCode.DUPLICATE_ITEM)
        item_name = _required_text(row["expected_item_name"])
        if not item_name.strip():
            raise GroundTruthError(TruthErrorCode.INVALID_ROW)
        parsed_items[item_index] = GroundTruthItem(
            item_index=item_index,
            expected_item_name=item_name,
            expected_item_qty=_parse_integer(
                _required_text(row["expected_item_qty"]),
                minimum=1,
            ),
            expected_item_price=_parse_integer(
                _required_text(row["expected_item_price"]),
            ),
        )
    if sorted(parsed_items) != list(range(len(parsed_items))):
        raise GroundTruthError(TruthErrorCode.NON_CONTIGUOUS_ITEMS)

    return GroundTruthCase(
        case_id=case_id,
        human_verified=True,
        expected_store=expected_store,
        expected_date=expected_date,
        expected_total=expected_total,
        expected_status=expected_status,
        items=tuple(parsed_items[index] for index in range(len(parsed_items))),
    )


def _required_text(value: str | list[str] | None) -> str:
    if not isinstance(value, str) or value == "":
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    return value


def _required_row_text(value: str | list[str] | None) -> str:
    if not isinstance(value, str):
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    return value


def _parse_verified(value: str | list[str] | None) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise GroundTruthError(TruthErrorCode.INVALID_ROW)


def _parse_date(value: str) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    year, month, day = (int(part) for part in value.split("-"))
    try:
        return date(year, month, day)
    except ValueError:
        raise GroundTruthError(TruthErrorCode.INVALID_ROW) from None


def _parse_integer(value: str, *, minimum: int | None = None) -> int:
    if re.fullmatch(r"(?:0|-?[1-9][0-9]*)", value) is None:
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    return parsed


def _parse_status(value: str) -> EvaluationStatus:
    if value not in {
        EvaluationStatus.CONFIRMED.value,
        EvaluationStatus.REVIEW.value,
        EvaluationStatus.FAILED.value,
    }:
        raise GroundTruthError(TruthErrorCode.INVALID_ROW)
    return EvaluationStatus(value)
