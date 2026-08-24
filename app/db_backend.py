from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any


_QMARK_PATTERN = re.compile(r"\?")
_INSERT_OR_IGNORE_PATTERN = re.compile(
    r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+",
    re.IGNORECASE,
)
_SERIAL_INSERT_PATTERN = re.compile(
    r"^\s*INSERT\s+INTO\s+(jobs|job_versions|job_attempts)\b",
    re.IGNORECASE,
)
_PRAGMA_TABLE_INFO_PATTERN = re.compile(
    r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$",
    re.IGNORECASE,
)


def is_postgres_url(value: str) -> bool:
    normalized = str(value or "").strip().casefold()
    return normalized.startswith(("postgresql://", "postgres://"))


def postgres_schema_sql(sql: str) -> str:
    """Translate the deliberately portable SQLite schema to PostgreSQL."""

    return re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )


def postgres_statement(sql: str) -> tuple[str, bool]:
    """Return PostgreSQL SQL and whether the cursor should expose lastrowid."""

    # psycopg parses percent signs whenever a parameters sequence is supplied,
    # including an empty sequence. Escape literal SQL percent signs before
    # translating SQLite qmark placeholders to PostgreSQL ``%s`` placeholders.
    statement = str(sql).replace("%", "%%")
    insert_or_ignore = bool(_INSERT_OR_IGNORE_PATTERN.search(statement))
    if insert_or_ignore:
        statement = _INSERT_OR_IGNORE_PATTERN.sub("INSERT INTO ", statement, count=1)
        statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    wants_lastrowid = bool(_SERIAL_INSERT_PATTERN.search(statement))
    if wants_lastrowid and " returning " not in statement.casefold():
        statement = statement.rstrip().rstrip(";") + " RETURNING id"
    return _QMARK_PATTERN.sub("%s", statement), wants_lastrowid


class PostgresCursor:
    """Small DB-API compatibility wrapper used by the existing repository."""

    def __init__(
        self,
        cursor: Any,
        *,
        lastrowid: int | None = None,
        prefetched: list[Any] | None = None,
    ) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid
        self._prefetched = list(prefetched or [])

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount or 0)

    def fetchone(self) -> Any | None:
        if self._prefetched:
            return self._prefetched.pop(0)
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        rows = list(self._prefetched)
        self._prefetched.clear()
        rows.extend(self._cursor.fetchall())
        return rows

    def __iter__(self) -> Iterator[Any]:
        while self._prefetched:
            yield self._prefetched.pop(0)
        yield from self._cursor


class PostgresConnection:
    """Expose the subset of sqlite3.Connection used by ``app.db``."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> PostgresCursor:
        pragma = _PRAGMA_TABLE_INFO_PATTERN.match(str(sql))
        if pragma:
            table_name = pragma.group(1).strip().strip("\"'")
            cursor = self._connection.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            return PostgresCursor(cursor)
        statement, wants_lastrowid = postgres_statement(sql)
        cursor = self._connection.execute(statement, tuple(params or ()))
        if not wants_lastrowid:
            return PostgresCursor(cursor)
        returned = cursor.fetchone()
        if returned is None:
            return PostgresCursor(cursor)
        if isinstance(returned, dict):
            generated_id = int(returned["id"])
        else:
            generated_id = int(returned[0])
        return PostgresCursor(cursor, lastrowid=generated_id)

    def executescript(self, sql: str) -> None:
        translated = postgres_schema_sql(sql)
        for statement in translated.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect_postgres(
    database_url: str,
    *,
    autocommit: bool = False,
) -> PostgresConnection:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - exercised in packaged runtime
        raise RuntimeError(
            "已配置 PostgreSQL，但缺少 psycopg 驱动；请重新安装完整依赖。"
        ) from exc
    raw = psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=10,
        autocommit=autocommit,
    )
    return PostgresConnection(raw)


def postgres_integrity_errors() -> tuple[type[BaseException], ...]:
    try:
        import psycopg
    except ImportError:
        return ()
    return (psycopg.IntegrityError,)


__all__ = [
    "PostgresConnection",
    "connect_postgres",
    "is_postgres_url",
    "postgres_integrity_errors",
    "postgres_schema_sql",
    "postgres_statement",
]
