from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time

_SEPARATOR_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})(?P<separator>[-/.])"
    r"(?P<month>\d{1,2})(?P=separator)(?P<day>\d{1,2})"
)
_JAPANESE_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
    r"(?:\s*[（(](?P<weekday>[月火水木金土日])[）)])?"
    r"(?:\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)
_JAPANESE_WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")


@dataclass(frozen=True)
class DateNormalization:
    raw: str
    normalized: str | None


def normalize_receipt_date(value: str) -> DateNormalization:
    candidate = value.strip()
    match = _SEPARATOR_DATE_PATTERN.fullmatch(candidate)
    if match is None:
        match = _JAPANESE_DATE_PATTERN.fullmatch(candidate)
    if match is None:
        return DateNormalization(raw=value, normalized=None)

    try:
        parsed = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return DateNormalization(raw=value, normalized=None)

    weekday = match.groupdict().get("weekday")
    if weekday is not None and weekday != _JAPANESE_WEEKDAYS[parsed.weekday()]:
        return DateNormalization(raw=value, normalized=None)

    hour = match.groupdict().get("hour")
    minute = match.groupdict().get("minute")
    second = match.groupdict().get("second")
    if hour is not None and minute is not None:
        try:
            time(int(hour), int(minute), int(second) if second is not None else 0)
        except ValueError:
            return DateNormalization(raw=value, normalized=None)

    return DateNormalization(raw=value, normalized=parsed.isoformat())
