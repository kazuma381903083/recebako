from enum import Enum


class IngestMode(str, Enum):
    REGULAR = "regular"
    HISTORICAL = "historical"
