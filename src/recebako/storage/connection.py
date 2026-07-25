from __future__ import annotations

import sqlite3
from pathlib import Path

DATABASE_FILENAME = "ledger.db"


class StorageError(RuntimeError):
    """SQLiteストレージを操作できなかったことを表す。"""


def database_path(data_root: Path) -> Path:
    return data_root / DATABASE_FILENAME


def connect_database(data_root: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path(data_root))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        enabled = connection.execute("PRAGMA foreign_keys").fetchone()
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise StorageError("SQLiteデータベースへ接続できません") from exc

    if enabled is None or enabled[0] != 1:
        connection.close()
        raise StorageError("SQLiteの外部キー制約を有効化できません")
    return connection
