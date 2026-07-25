from __future__ import annotations

import sqlite3
from importlib.resources import files


class MigrationError(RuntimeError):
    """SQLiteマイグレーションを適用できなかったことを表す。"""


def apply_migrations(connection: sqlite3.Connection) -> None:
    migrations = files("recebako.storage.migrations")
    migration_files = sorted(
        (
            migration
            for migration in migrations.iterdir()
            if migration.name.endswith(".sql")
        ),
        key=lambda migration: migration.name,
    )

    for migration in migration_files:
        script = migration.read_text(encoding="utf-8")
        try:
            connection.executescript(f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;")
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise MigrationError("SQLiteマイグレーションに失敗しました") from exc
