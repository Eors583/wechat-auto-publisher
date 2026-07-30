from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.db import Database
from app.db_backend import is_postgres_url


TABLE_ORDER = (
    "users",
    "app_settings",
    "ai_models",
    "prompt_templates",
    "official_accounts",
    "ads",
    "token_cache",
    "batches",
    "jobs",
    "batch_jobs",
    "bot_sessions",
    "bot_contexts",
    "processed_events",
    "user_sessions",
    "job_versions",
    "editorial_review_profiles",
    "account_editorial_review_defaults",
    "creation_plans",
    "account_creation_plan_defaults",
    "creation_plan_account_templates",
    "editorial_reviews",
    "editorial_review_applications",
    "topic_sources",
    "topic_items",
    "followed_accounts",
    "followed_articles",
    "job_attempts",
    "draft_deliveries",
    "wechat_connection_health",
)


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def migrate(sqlite_path: Path, database_url: str) -> dict[str, int]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite 数据库不存在：{sqlite_path}")
    if not is_postgres_url(database_url):
        raise ValueError("目标必须是 postgresql:// 或 postgres:// 地址")

    target = Database(database_url)
    source = sqlite3.connect(str(sqlite_path))
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        available = _sqlite_tables(source)
        for table in TABLE_ORDER:
            if table not in available:
                continue
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                counts[table] = 0
                continue
            columns = [str(item) for item in rows[0].keys()]
            placeholders = ",".join("?" for _ in columns)
            column_sql = ",".join(columns)
            sql = (
                f"INSERT OR IGNORE INTO {table} "
                f"({column_sql}) VALUES ({placeholders})"
            )
            copied = 0
            with target.connect() as destination:
                for row in rows:
                    cursor = destination.execute(
                        sql,
                        tuple(row[column] for column in columns),
                    )
                    copied += max(0, int(cursor.rowcount or 0))
            counts[table] = copied

        for table in ("jobs", "job_versions", "job_attempts"):
            if table not in available:
                continue
            with target.connect() as destination:
                destination.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        EXISTS(SELECT 1 FROM {table})
                    )
                    """
                )
    finally:
        source.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将公众号助手的 SQLite 数据一次性迁移到 PostgreSQL"
    )
    parser.add_argument(
        "--sqlite",
        default="data/app.db",
        help="原 SQLite 文件路径，默认 data/app.db",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="PostgreSQL 连接地址；默认读取 DATABASE_URL",
    )
    args = parser.parse_args()
    result = migrate(
        Path(str(args.sqlite)).resolve(),
        str(args.database_url or "").strip(),
    )
    print("迁移完成：")
    for table, count in result.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
