from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.db import Database, JOB_STATUSES, STEPS
from app.schema_migrations import SCHEMA_MIGRATIONS, validate_schema_migrations


def audit_database(database: Database) -> dict[str, Any]:
    """Return aggregate schema/data-integrity findings without customer values."""

    with database.connect() as conn:
        applied_rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        history_valid = True
        try:
            validate_schema_migrations(conn)
        except RuntimeError:
            history_valid = False
        report = {
            "backend": database.backend,
            "schema": {
                "expected_versions": [item.version for item in SCHEMA_MIGRATIONS],
                "applied_versions": [str(row["version"]) for row in applied_rows],
                "history_valid": history_valid,
                "unvalidated_constraints": _unvalidated_constraints(
                    conn, database.backend
                ),
            },
            "orphan_references": {
                "creation_plan_defaults": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM account_creation_plan_defaults d
                    LEFT JOIN creation_plans p ON p.id = d.creation_plan_id
                    WHERE d.creation_plan_id IS NOT NULL AND p.id IS NULL
                    """,
                ),
                "editorial_review_defaults": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM account_editorial_review_defaults d
                    LEFT JOIN editorial_review_profiles p ON p.id = d.profile_id
                    WHERE d.profile_id IS NOT NULL AND p.id IS NULL
                    """,
                ),
                "jobs_batch": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM jobs j
                    LEFT JOIN batches b ON b.id = j.batch_id
                    WHERE j.batch_id IS NOT NULL AND b.id IS NULL
                    """,
                ),
            },
            "owner_mismatches": {
                "creation_plan_defaults": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM official_accounts a
                    JOIN creation_plans p
                      ON p.id = a.default_creation_plan_id
                    WHERE a.owner_user_id <> p.owner_user_id
                    """,
                ),
                "editorial_review_defaults": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM official_accounts a
                    JOIN editorial_review_profiles p
                      ON p.id = a.default_editorial_review_profile_id
                    WHERE a.owner_user_id <> p.owner_user_id
                    """,
                ),
                "jobs_batch": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM jobs j
                    JOIN batches b ON b.id = j.batch_id
                    WHERE j.owner_user_id <> b.owner_user_id
                    """,
                ),
                "jobs_account": _count(
                    conn,
                    """
                    SELECT COUNT(*) AS count
                    FROM jobs j
                    JOIN official_accounts a ON a.id = j.account_id
                    WHERE j.owner_user_id <> a.owner_user_id
                    """,
                ),
            },
            "invalid_values": {
                "jobs_status": _not_in_count(conn, "jobs", "status", JOB_STATUSES),
                "jobs_step": _not_in_count(conn, "jobs", "step", STEPS),
                "batch_jobs_review_status": _not_in_count(
                    conn,
                    "batch_jobs",
                    "review_status",
                    ("unviewed", "viewed", "confirmed", "needs_changes"),
                ),
                "official_accounts_enabled": _not_in_count(
                    conn, "official_accounts", "enabled", (0, 1)
                ),
                "feishu_integrations_enabled": _not_in_count(
                    conn, "feishu_integrations", "enabled", (0, 1)
                ),
                "feishu_integration_accounts_default": _not_in_count(
                    conn, "feishu_integration_accounts", "is_default", (0, 1)
                ),
                "official_accounts_owner": _empty_count(
                    conn, "official_accounts", "owner_user_id"
                ),
                "batches_owner": _empty_count(conn, "batches", "owner_user_id"),
                "jobs_owner": _empty_count(conn, "jobs", "owner_user_id"),
            },
            "duplicate_indexes": _duplicate_indexes(conn, database.backend),
            "compatibility_rows": {
                table: _count(conn, f"SELECT COUNT(*) AS count FROM {table}")
                for table in (
                    "account_creation_plan_defaults",
                    "account_editorial_review_defaults",
                    "batch_jobs",
                    "bot_sessions",
                    "bot_contexts",
                    "processed_events",
                )
            },
        }
    report["ok"] = _report_is_clean(report)
    report["warning_count"] = len(report["duplicate_indexes"])
    return report


def _count(conn: Any, statement: str, params: Iterable[Any] = ()) -> int:
    row = conn.execute(statement, tuple(params)).fetchone()
    return int(row["count"] or 0) if row else 0


def _not_in_count(
    conn: Any,
    table: str,
    column: str,
    accepted: Iterable[Any],
) -> int:
    values = tuple(accepted)
    placeholders = ", ".join("?" for _ in values)
    return _count(
        conn,
        f"""
        SELECT COUNT(*) AS count
        FROM {table}
        WHERE {column} IS NULL OR {column} NOT IN ({placeholders})
        """,
        values,
    )


def _empty_count(conn: Any, table: str, column: str) -> int:
    return _count(
        conn,
        f"SELECT COUNT(*) AS count FROM {table} "
        f"WHERE {column} IS NULL OR {column} = ''",
    )


def _unvalidated_constraints(conn: Any, backend: str) -> int:
    if backend != "postgresql":
        return 0
    return _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace
          AND NOT convalidated
        """,
    )


def _duplicate_indexes(conn: Any, backend: str) -> list[list[str]]:
    if backend == "postgresql":
        rows = conn.execute(
            """
            SELECT ARRAY_AGG(indexname ORDER BY indexname) AS index_names
            FROM (
                SELECT indexname,
                       REGEXP_REPLACE(
                           indexdef,
                           ' INDEX [^ ]+ ON ',
                           ' INDEX ON '
                       ) AS normalized_definition
                FROM pg_indexes
                WHERE schemaname = current_schema()
            ) indexes
            GROUP BY normalized_definition
            HAVING COUNT(*) > 1
            ORDER BY MIN(indexname)
            """
        ).fetchall()
        return [list(row["index_names"] or []) for row in rows]

    duplicate_groups: dict[tuple[Any, ...], list[str]] = {}
    tables = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    for table_row in tables:
        table = str(table_row["name"])
        for index_row in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
            index_name = str(index_row["name"])
            columns = tuple(
                str(item["name"])
                for item in conn.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            )
            signature = (table, int(index_row["unique"] or 0), columns)
            duplicate_groups.setdefault(signature, []).append(index_name)
    return sorted(
        sorted(names)
        for names in duplicate_groups.values()
        if len(names) > 1
    )


def _report_is_clean(report: dict[str, Any]) -> bool:
    schema = report["schema"]
    if (
        not schema["history_valid"]
        or schema["expected_versions"] != schema["applied_versions"]
        or schema["unvalidated_constraints"]
    ):
        return False
    for section in ("orphan_references", "owner_mismatches", "invalid_values"):
        if any(int(value) for value in report[section].values()):
            return False
    return True


__all__ = ["audit_database"]
