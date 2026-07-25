from __future__ import annotations

import sqlite3
from importlib.resources import files


class MigrationError(RuntimeError):
    """SQLiteマイグレーションを適用できなかったことを表す。"""


def _is_migration_applied(connection: sqlite3.Connection, version: str) -> bool:
    table_exists = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone()
    if table_exists is None:
        return False
    return (
        connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        is not None
    )


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
        version = migration.name.removesuffix(".sql")
        try:
            if _is_migration_applied(connection, version):
                continue
            script = migration.read_text(encoding="utf-8")
            connection.executescript(f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;")
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise MigrationError("SQLiteマイグレーションに失敗しました") from exc
