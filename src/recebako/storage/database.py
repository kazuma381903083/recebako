from __future__ import annotations

from contextlib import closing
from pathlib import Path

from recebako.storage.connection import connect_database, database_path
from recebako.storage.migration import apply_migrations


def initialize_database(data_root: Path) -> Path:
    with closing(connect_database(data_root)) as connection:
        apply_migrations(connection)
    return database_path(data_root)
