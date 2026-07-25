from __future__ import annotations

import pytest

from recebako.normalization import normalize_receipt_date


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-25",
        "2026-7-25",
        "2026/07/25",
        "2026/7/25",
        "2026.07.25",
        "2026.7.25",
        "2026年7月25日",
        "2026年7月25日 (土) 11:42",
        "2026年7月25日（土）11:42",
        "2026年7月25日 (土) 11:42:05",
    ],
)
def test_normalize_supported_date_formats(value: str) -> None:
    result = normalize_receipt_date(value)

    assert result.raw == value
    assert result.normalized == "2026-07-25"


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30",
        "",
        "not-a-date",
        "26-07-25",
        "令和8年7月25日",
        "2026/07-25",
        "2026年7月25日 (日) 11:42",
        "2026年7月25日 (土) 25:00",
        "2026年7月25日 (土) 11:42:60",
        "2026年7月25日 頃",
    ],
)
def test_normalize_rejects_invalid_or_unsupported_dates(value: str) -> None:
    result = normalize_receipt_date(value)

    assert result.raw == value
    assert result.normalized is None
