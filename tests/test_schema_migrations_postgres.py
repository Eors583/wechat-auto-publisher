from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from app.db import _POSTGRES_SCHEMA_INITIALIZED, Database
from app.db_audit import audit_database
from app.schema_migrations import PHASE_ONE_COMPAT, SCHEMA_MIGRATIONS
from app.services.auth import AuthService
from app.services.jizhile_settings import (
    effective_jizhile_settings,
    save_jizhile_settings,
)

POSTGRES_ADMIN_URL = os.environ.get("POSTGRES_SCHEMA_TEST_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_ADMIN_URL,
    reason="set POSTGRES_SCHEMA_TEST_URL to an isolated PostgreSQL server",
)


@pytest.fixture
def postgres_database_url() -> str:
    assert POSTGRES_ADMIN_URL
    parsed = urlsplit(POSTGRES_ADMIN_URL)
    database_name = f"wechat_schema_test_{uuid.uuid4().hex[:16]}"
    database_url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{database_name}",
            parsed.query,
            parsed.fragment,
        )
    )
    with psycopg.connect(POSTGRES_ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        yield database_url
    finally:
        _POSTGRES_SCHEMA_INITIALIZED.discard(database_url)
        with psycopg.connect(POSTGRES_ADMIN_URL, autocommit=True) as conn:
            conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )


def _migration_rows(database_url: str) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as conn:
        return list(
            conn.execute(
                """
                SELECT version, name, checksum, applied_at
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
        )


def _uninitialized_database(database_url: str) -> Database:
    database = object.__new__(Database)
    database.path = database_url
    database.backend = "postgresql"
    database.database_url = database_url
    database.owner_session_id = "migration-concurrency-test"
    database._owner_user_id = ""
    return database


def _rewind_phase_one(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM schema_migrations WHERE version >= '20260824_0002'"
        )
        for table, constraint in (
            (
                "account_creation_plan_defaults",
                "fk_account_creation_plan_defaults_plan",
            ),
            (
                "account_editorial_review_defaults",
                "fk_account_editorial_review_defaults_profile",
            ),
            (
                "official_accounts",
                "fk_official_accounts_default_creation_plan",
            ),
            (
                "official_accounts",
                "fk_official_accounts_default_review_profile",
            ),
            ("jobs", "fk_jobs_batch"),
            ("jobs", "ck_jobs_status"),
            ("jobs", "ck_jobs_step"),
            ("batch_jobs", "ck_batch_jobs_review_status"),
            ("official_accounts", "ck_official_accounts_enabled"),
            ("official_accounts", "ck_official_accounts_owner"),
            ("batches", "ck_batches_owner"),
            ("jobs", "ck_jobs_owner"),
            ("feishu_integrations", "ck_feishu_integrations_enabled"),
            ("feishu_integrations", "ck_feishu_integrations_owner"),
            (
                "feishu_integration_accounts",
                "ck_feishu_integration_accounts_default",
            ),
        ):
            conn.execute(
                sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
                    sql.Identifier(table),
                    sql.Identifier(constraint),
                )
            )
        for table, columns in (
            (
                "official_accounts",
                (
                    "default_creation_plan_id",
                    "default_editorial_review_profile_id",
                    "editorial_review_config_json",
                ),
            ),
            (
                "jobs",
                (
                    "batch_id",
                    "account_id",
                    "account_name_snapshot",
                    "review_status",
                    "viewed_at",
                    "confirmed_at",
                    "source_channel",
                ),
            ),
        ):
            for column in columns:
                conn.execute(
                    sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS {} CASCADE").format(
                        sql.Identifier(table),
                        sql.Identifier(column),
                    )
                )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_deliveries_revision
            ON draft_deliveries(
                job_id, account_id, content_revision, content_fingerprint
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feishu_integrations_owner
            ON feishu_integrations(owner_user_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feishu_integrations_callback
            ON feishu_integrations(callback_key)
            """
        )
    _POSTGRES_SCHEMA_INITIALIZED.discard(database_url)


def test_platform_jizhile_migration_preserves_legacy_default_owner_setting(
    postgres_database_url: str,
) -> None:
    root = Database(postgres_database_url)
    user = AuthService(root).register("legacy-jizhile-owner", "secure-pass-123")
    save_jizhile_settings(root, enabled=True, key="legacy-platform-secret")
    legacy_value = root.get_setting("platform.jizhile_api") or ""
    root.set_setting("migration.customer_data_owner.v1", str(user["id"]))
    root.for_user(str(user["id"])).set_user_setting("jizhile_api", legacy_value)
    with psycopg.connect(postgres_database_url) as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key = 'platform.jizhile_api'"
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = '20260826_0007'"
        )
    _POSTGRES_SCHEMA_INITIALIZED.discard(postgres_database_url)

    upgraded = Database(postgres_database_url)

    assert effective_jizhile_settings(upgraded)["key"] == "legacy-platform-secret"


def test_followed_article_refresh_upgrade_sets_twenty_point_rate(
    postgres_database_url: str,
) -> None:
    Database(postgres_database_url)
    with psycopg.connect(postgres_database_url) as conn:
        conn.execute(
            """
            UPDATE billing_task_rates
            SET base_points = 10, max_reserve_points = 10, version = 1
            WHERE task_code = 'followed_articles_refresh'
            """
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = '20260826_0008'"
        )
    _POSTGRES_SCHEMA_INITIALIZED.discard(postgres_database_url)

    upgraded = Database(postgres_database_url)
    rate = upgraded.get_billing_task_rate("followed_articles_refresh")

    assert rate is not None
    assert rate["base_points"] == 20
    assert rate["max_reserve_points"] == 20
    assert rate["version"] == 2


def test_fresh_postgres_schema_records_all_versioned_migrations(
    postgres_database_url: str,
) -> None:
    Database(postgres_database_url)

    rows = _migration_rows(postgres_database_url)
    assert [row["version"] for row in rows] == [
        migration.version for migration in SCHEMA_MIGRATIONS
    ]
    assert [row["checksum"] for row in rows] == [
        migration.checksum for migration in SCHEMA_MIGRATIONS
    ]
    with psycopg.connect(postgres_database_url) as conn:
        table_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        ).fetchone()[0]
    assert table_count == 47
    with psycopg.connect(postgres_database_url) as conn:
        billing_tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'billing_plans', 'user_subscriptions',
                      'model_price_cards', 'credit_buckets',
                      'usage_operations', 'credit_ledger', 'ai_usage_events',
                      'billing_pricing_policies', 'billing_task_rates'
                  )
                """
            ).fetchall()
        }
        billing_indexes = {
            row[0]
            for row in conn.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN (
                      'idx_subscriptions_owner', 'idx_price_cards_lookup',
                      'idx_credit_buckets_owner_expiry',
                      'idx_credit_ledger_owner_created',
                      'idx_usage_operations_owner_created',
                      'idx_usage_events_operation',
                      'idx_usage_events_owner_created',
                      'idx_billing_task_rates_enabled',
                      'idx_credit_ledger_operation_bucket_event'
                  )
                """
            ).fetchall()
        }
        strict_usage_columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ai_usage_events'
                  AND column_name IN (
                      'token_usage_status', 'provider_credits', 'raw_usage_json'
                  )
                """
            ).fetchall()
        }
        metering_model_columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ai_models'
                  AND column_name IN (
                      'token_metering_capability', 'strict_token_eligible',
                      'token_metering_checked_at'
                  )
                """
            ).fetchall()
        }
        provider_trace_indexes = {
            row[0]: row[1]
            for row in conn.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN (
                      'idx_usage_events_provider_request_unique',
                      'idx_usage_events_provider_response_unique'
                  )
                """
            ).fetchall()
        }
        commercial_price_columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'model_price_cards'
                  AND column_name IN (
                      'metering_mode', 'reasoning_micro_cny_per_million',
                      'provider_unit_micro_cny_each',
                      'provider_risk_basis_points'
                  )
                """
            ).fetchall()
        }
        commercial_operation_columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'usage_operations'
                  AND column_name IN (
                      'task_code', 'task_base_points', 'resource_points',
                      'pricing_snapshot_json'
                  )
                """
            ).fetchall()
        }
    assert len(billing_tables) == 9
    assert len(billing_indexes) == 9
    assert strict_usage_columns == {
        "token_usage_status",
        "provider_credits",
        "raw_usage_json",
    }
    assert metering_model_columns == {
        "token_metering_capability",
        "strict_token_eligible",
        "token_metering_checked_at",
    }
    assert set(provider_trace_indexes) == {
        "idx_usage_events_provider_request_unique",
        "idx_usage_events_provider_response_unique",
    }
    assert all(
        "UNIQUE INDEX" in value for value in provider_trace_indexes.values()
    )
    assert "owner_user_id, provider, provider_request_id" in (
        provider_trace_indexes["idx_usage_events_provider_request_unique"]
    )
    assert "owner_user_id, provider, provider_response_id" in (
        provider_trace_indexes["idx_usage_events_provider_response_unique"]
    )
    assert commercial_price_columns == {
        "metering_mode",
        "reasoning_micro_cny_per_million",
        "provider_unit_micro_cny_each",
        "provider_risk_basis_points",
    }
    assert commercial_operation_columns == {
        "task_code",
        "task_base_points",
        "resource_points",
        "pricing_snapshot_json",
    }


def test_commercial_point_reservations_are_atomic_on_postgres(
    postgres_database_url: str,
) -> None:
    root = Database(postgres_database_url)
    user = AuthService(root).register("points-owner", "secure-pass-123")
    user_id = str(user["id"])
    database = root.for_user(user_id)
    database.grant_credit_points(points=1_000, source_type="test")
    job_id = database.create_job(topic="PostgreSQL 积分投影")
    operation_ids = [
        database.create_usage_operation(
            {
                "id": f"concurrent-operation-{index}",
                "scene": "article_generation",
                "task_code": "article_standard",
                "source_channel": "test",
                "subject_type": "job",
                "subject_id": str(index),
                "idempotency_key": f"concurrent-reservation-{index}",
                "status": "running",
                "mode": "live",
                "job_id": job_id,
            }
        )
        for index in range(2)
    ]

    def reserve(operation_id: str) -> tuple[str, int | str]:
        try:
            points = root.for_user(user_id).reserve_credit_points(
                operation_id,
                points=600,
                expires_at="2099-01-01T00:00:00+00:00",
            )
        except ValueError as exc:
            return operation_id, str(exc)
        return operation_id, points

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, operation_ids))

    successes = [item for item in results if item[1] == 600]
    failures = [item for item in results if isinstance(item[1], str)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "积分不足" in str(failures[0][1])
    assert database.credit_wallet_summary() == {
        "available": 400,
        "reserved": 600,
        "charged": 0,
    }

    database.settle_credit_operation(
        successes[0][0],
        status="succeeded",
        charged_points=155,
        estimated_points=155,
        task_base_points=60,
        resource_points=95,
        pricing_snapshot_json='{"test":true}',
        completed_at="2026-08-25T12:00:00+00:00",
    )
    assert database.credit_wallet_summary() == {
        "available": 845,
        "reserved": 0,
        "charged": 155,
    }
    article_usage = database.article_generation_token_usage_by_jobs([job_id])
    assert article_usage[0]["estimated_points"] == 155
    assert article_usage[0]["charged_points"] == 155


def test_legacy_postgres_schema_upgrades_without_losing_settings(
    postgres_database_url: str,
) -> None:
    with psycopg.connect(postgres_database_url, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('platform.test', 'preserve-me', '2026-08-24T00:00:00Z')
            """
        )

    database = Database(postgres_database_url)

    assert database.get_setting("platform.test") == "preserve-me"
    assert len(_migration_rows(postgres_database_url)) == len(SCHEMA_MIGRATIONS)


def test_phase_one_upgrade_repairs_defaults_and_backfills_new_sources_of_truth(
    postgres_database_url: str,
) -> None:
    Database(postgres_database_url)
    _rewind_phase_one(postgres_database_url)
    with psycopg.connect(postgres_database_url, autocommit=True) as conn:
        now = "2026-08-24T00:00:00Z"
        conn.execute(
            """
            INSERT INTO users (
                id, username, password_hash, role, enabled, created_at, updated_at
            ) VALUES ('user-a', 'owner-a', 'hash', 'user', 1, %s, %s)
            """,
            (now, now),
        )
        for account_id in ("account-valid", "account-orphan"):
            conn.execute(
                """
                INSERT INTO official_accounts (
                    id, owner_user_id, name, app_id, app_secret_encrypted,
                    model_id, layout_json, review_priority, enabled,
                    created_at, updated_at
                ) VALUES (%s, 'user-a', %s, %s, 'encrypted', 'model-a', '{}', 0, 1, %s, %s)
                """,
                (account_id, account_id, f"wx-{account_id}", now, now),
            )
        conn.execute(
            """
            INSERT INTO creation_plans (
                id, owner_user_id, name, description, layout_json,
                image_settings_json, enabled, created_at, updated_at
            ) VALUES ('plan-valid', 'user-a', '方案', '', '{}', '{}', 1, %s, %s)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO editorial_review_profiles (
                id, owner_user_id, name, description, config_json,
                enabled, created_at, updated_at
            ) VALUES ('profile-valid', 'user-a', '评审', '', '{}', 1, %s, %s)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO account_creation_plan_defaults (
                account_id, creation_plan_id, created_at, updated_at
            ) VALUES
                ('account-valid', 'plan-valid', %s, %s),
                ('account-orphan', 'plan-missing', %s, %s)
            """,
            (now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO account_editorial_review_defaults (
                account_id, profile_id, config_json, created_at, updated_at
            ) VALUES
                ('account-valid', 'profile-valid', '{"mode":"strict"}', %s, %s),
                ('account-orphan', 'profile-missing', '{"secret":"not-a-credential"}', %s, %s)
            """,
            (now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO batches (
                id, owner_user_id, status, topic, created_at, updated_at
            ) VALUES ('batch-a', 'user-a', 'pending', '主题', %s, %s)
            """,
            (now, now),
        )
        job_id = conn.execute(
            """
            INSERT INTO jobs (
                owner_user_id, status, step, topic, source, meta_json,
                created_at, updated_at
            ) VALUES (
                'user-a', 'ready_for_review', 'render', '主题', 'manual', '{}', %s, %s
            ) RETURNING id
            """,
            (now, now),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO batch_jobs (
                batch_id, job_id, account_id, account_name,
                review_status, viewed_at, confirmed_at
            ) VALUES ('batch-a', %s, 'account-valid', '公众号快照', 'viewed', %s, NULL)
            """,
            (job_id, now),
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES
                ('migration.customer_data_owner.v1', 'user-a', %s),
                ('onboarding.guide', '{"step":2}', %s),
                ('wechat_backend_search', '{"token":"private-value"}', %s)
            ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
            """,
            (now, now, now),
        )
        before = {
            table: conn.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in ("official_accounts", "jobs", "batches", "batch_jobs")
        }
        before_owner_counts = {
            table: [
                (str(row[0]), int(row[1]))
                for row in conn.execute(
                    sql.SQL(
                        "SELECT owner_user_id, COUNT(*) AS count FROM {} "
                        "GROUP BY owner_user_id ORDER BY owner_user_id"
                    ).format(sql.Identifier(table))
                ).fetchall()
            ]
            for table in ("official_accounts", "jobs", "batches")
        }

    database = Database(postgres_database_url).for_user("user-a")

    valid = database.get_official_account("account-valid")
    orphan = database.get_official_account("account-orphan")
    job = database.get_job(int(job_id))
    assert valid["default_creation_plan_id"] == "plan-valid"
    assert valid["default_editorial_review_profile_id"] == "profile-valid"
    assert valid["editorial_review_config_json"] == '{"mode":"strict"}'
    assert orphan["default_creation_plan_id"] is None
    assert orphan["default_editorial_review_profile_id"] is None
    assert job["batch_id"] == "batch-a"
    assert job["account_id"] == "account-valid"
    assert job["account_name_snapshot"] == "公众号快照"
    assert job["review_status"] == "viewed"
    assert job["source_channel"] == "manual"
    assert database.get_user_setting("onboarding.guide") == '{"step":2}'
    assert database.get_user_setting("wechat_backend_search") == (
        '{"token":"private-value"}'
    )
    with database.connect() as conn:
        after = {
            table: conn.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            for table in ("official_accounts", "jobs", "batches", "batch_jobs")
        }
        after_owner_counts = {
            table: [
                (str(row["owner_user_id"]), int(row["count"]))
                for row in conn.execute(
                    f"""
                    SELECT owner_user_id, COUNT(*) AS count
                    FROM {table}
                    GROUP BY owner_user_id
                    ORDER BY owner_user_id
                    """
                ).fetchall()
            ]
            for table in ("official_accounts", "jobs", "batches")
        }
        duplicate_indexes = conn.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND indexname IN (
                  'idx_draft_deliveries_revision',
                  'idx_feishu_integrations_owner',
                  'idx_feishu_integrations_callback'
              )
            """
        ).fetchall()
        unvalidated = conn.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
              AND conname LIKE ANY(ARRAY['fk_%', 'ck_%'])
              AND NOT convalidated
            """
        ).fetchall()
    assert after == before
    assert after_owner_counts == before_owner_counts
    assert duplicate_indexes == []
    assert unvalidated == []


def test_phase_one_refuses_ambiguous_multi_batch_jobs(
    postgres_database_url: str,
) -> None:
    Database(postgres_database_url)
    _rewind_phase_one(postgres_database_url)
    with psycopg.connect(postgres_database_url, autocommit=True) as conn:
        now = "2026-08-24T00:00:00Z"
        job_id = conn.execute(
            """
            INSERT INTO jobs (
                owner_user_id, status, step, source, meta_json,
                created_at, updated_at
            ) VALUES ('owner-a', 'pending', 'ingest', 'manual', '{}', %s, %s)
            RETURNING id
            """,
            (now, now),
        ).fetchone()[0]
        for batch_id in ("batch-a", "batch-b"):
            conn.execute(
                """
                INSERT INTO batches (
                    id, owner_user_id, status, created_at, updated_at
                ) VALUES (%s, 'owner-a', 'pending', %s, %s)
                """,
                (batch_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO batch_jobs (
                    batch_id, job_id, account_id, account_name,
                    review_status
                ) VALUES (%s, %s, 'account-a', '公众号', 'unviewed')
                """,
                (batch_id, job_id),
            )

    with pytest.raises(RuntimeError, match="属于多个批次"):
        Database(postgres_database_url)
    with psycopg.connect(postgres_database_url) as conn:
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = %s",
            (PHASE_ONE_COMPAT.version,),
        ).fetchone()
    assert applied is None


def test_phase_one_runtime_dual_writes_and_reads_new_canonical_fields(
    postgres_database_url: str,
) -> None:
    root = Database(postgres_database_url)
    now = "2026-08-24T00:00:00Z"
    with root.connect() as conn:
        conn.execute(
            """
            INSERT INTO users (
                id, username, password_hash, role, enabled, created_at, updated_at
            ) VALUES ('owner-a', 'owner-a', 'hash', 'user', 1, ?, ?)
            """,
            (now, now),
        )
    database = root.for_user("owner-a")
    database.upsert_official_account(
        {
            "id": "account-a",
            "name": "公众号A",
            "app_id": "wx-account-a",
            "app_secret_encrypted": "encrypted-secret",
            "model_id": "model-a",
            "enabled": True,
        }
    )
    database.upsert_creation_plan(
        {"id": "plan-a", "name": "方案A", "enabled": True}
    )
    database.upsert_editorial_review_profile(
        {"id": "profile-a", "name": "评审A", "enabled": True}
    )
    database.set_account_creation_plan_default("account-a", "plan-a")
    database.set_account_editorial_review_default(
        "account-a", profile_id="profile-a", config={"mode": "strict"}
    )
    database.create_batch("batch-a", topic="主题")
    job_id = database.create_job(source="manual", topic="主题")
    database.attach_batch_job("batch-a", job_id, "account-a", "公众号快照")
    database.update_job(job_id, source="feishu", status="ready_for_review")
    database.update_batch_job_review("batch-a", job_id, "confirmed")

    with database.connect() as conn:
        account = conn.execute(
            """
            SELECT default_creation_plan_id,
                   default_editorial_review_profile_id,
                   editorial_review_config_json
            FROM official_accounts WHERE id = 'account-a'
            """
        ).fetchone()
        legacy_plan = conn.execute(
            """
            SELECT creation_plan_id FROM account_creation_plan_defaults
            WHERE account_id = 'account-a'
            """
        ).fetchone()
        legacy_review = conn.execute(
            """
            SELECT profile_id, config_json
            FROM account_editorial_review_defaults
            WHERE account_id = 'account-a'
            """
        ).fetchone()
        job = conn.execute(
            """
            SELECT batch_id, account_id, account_name_snapshot,
                   review_status, source, source_channel
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        legacy_job = conn.execute(
            """
            SELECT review_status FROM batch_jobs
            WHERE batch_id = 'batch-a' AND job_id = ?
            """,
            (job_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE batch_jobs SET review_status = 'unviewed'
            WHERE batch_id = 'batch-a' AND job_id = ?
            """,
            (job_id,),
        )
    assert account["default_creation_plan_id"] == "plan-a"
    assert account["default_editorial_review_profile_id"] == "profile-a"
    assert json.loads(account["editorial_review_config_json"]) == {
        "mode": "strict"
    }
    assert legacy_plan["creation_plan_id"] == "plan-a"
    assert legacy_review["profile_id"] == "profile-a"
    assert job["batch_id"] == "batch-a"
    assert job["account_id"] == "account-a"
    assert job["account_name_snapshot"] == "公众号快照"
    assert job["review_status"] == legacy_job["review_status"] == "confirmed"
    assert job["source"] == job["source_channel"] == "feishu"
    assert database.review_inbox_counts()["ready_for_draft"] == 1
    assert database.get_batch("batch-a")["jobs"][0]["review_status"] == (
        "confirmed"
    )
    database.create_batch("batch-b", topic="另一主题")
    with pytest.raises(ValueError, match="已经属于其他批次"):
        database.attach_batch_job(
            "batch-b", job_id, "account-a", "公众号快照"
        )
    database.delete_editorial_review_profile("profile-a")
    cleared = database.get_account_editorial_review_default("account-a")
    assert cleared is not None
    assert cleared["profile_id"] is None
    assert json.loads(cleared["config_json"]) == {}
    with database.connect() as conn:
        account_after_delete = conn.execute(
            """
            SELECT default_editorial_review_profile_id,
                   editorial_review_config_json
            FROM official_accounts WHERE id = 'account-a'
            """
        ).fetchone()
        legacy_after_delete = conn.execute(
            """
            SELECT profile_id, config_json
            FROM account_editorial_review_defaults
            WHERE account_id = 'account-a'
            """
        ).fetchone()
    assert account_after_delete["default_editorial_review_profile_id"] is None
    assert json.loads(account_after_delete["editorial_review_config_json"]) == {}
    assert legacy_after_delete["profile_id"] is None
    assert json.loads(legacy_after_delete["config_json"]) == {}


def test_database_audit_reports_only_aggregate_findings(
    postgres_database_url: str,
) -> None:
    root = Database(postgres_database_url)
    with root.connect() as conn:
        now = "2026-08-24T00:00:00Z"
        conn.execute(
            """
            INSERT INTO users (
                id, username, password_hash, role, enabled, created_at, updated_at
            ) VALUES ('owner-a', 'owner-a', 'hash', 'user', 1, ?, ?)
            """,
            (now, now),
        )
    root.for_user("owner-a").set_user_setting(
        "wechat_backend_search", "credential-must-not-appear"
    )

    report = audit_database(root)

    # The audit deliberately surfaces the pre-existing unique-constraint plus
    # explicit-index pair for followed articles. Phase one does not drop this
    # extra index without a production query-plan observation window.
    assert report["ok"] is True
    assert report["warning_count"] == 1
    assert report["duplicate_indexes"] == [[
        "followed_articles_owner_user_id_url_key",
        "idx_followed_articles_owner_url",
    ]]
    assert all(value == 0 for value in report["orphan_references"].values())
    assert all(value == 0 for value in report["owner_mismatches"].values())
    assert "credential-must-not-appear" not in json.dumps(report)


def test_postgres_schema_restart_is_idempotent(
    postgres_database_url: str,
) -> None:
    Database(postgres_database_url)
    before = _migration_rows(postgres_database_url)
    _POSTGRES_SCHEMA_INITIALIZED.discard(postgres_database_url)

    Database(postgres_database_url)

    assert _migration_rows(postgres_database_url) == before


def test_postgres_schema_rejects_checksum_tampering(
    postgres_database_url: str,
) -> None:
    Database(postgres_database_url)
    version = SCHEMA_MIGRATIONS[0].version
    with psycopg.connect(postgres_database_url) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = %s",
            (version,),
        )
        conn.commit()
    _POSTGRES_SCHEMA_INITIALIZED.discard(postgres_database_url)

    with pytest.raises(RuntimeError, match="checksum"):
        Database(postgres_database_url)


def test_postgres_advisory_lock_serializes_concurrent_initialization(
    postgres_database_url: str,
) -> None:
    def initialize(_index: int) -> None:
        _uninitialized_database(postgres_database_url)._init_schema()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(initialize, range(4)))

    rows = _migration_rows(postgres_database_url)
    assert len(rows) == len(SCHEMA_MIGRATIONS)
    assert len({row["version"] for row in rows}) == len(rows)
