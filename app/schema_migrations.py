from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaMigration:
    version: str
    name: str
    signature: str
    transactional: bool = True

    @property
    def checksum(self) -> str:
        payload = f"{self.version}\n{self.name}\n{self.signature}"
        return hashlib.sha256(payload.encode()).hexdigest()


BASELINE_SCHEMA = SchemaMigration(
    "20260824_0001",
    "legacy_schema_baseline",
    "37-table schema and compatibility migrations through 2026-08-20",
)
PHASE_ONE_COMPAT = SchemaMigration(
    "20260824_0002",
    "postgres_phase_one_compatibility",
    (
        "account default columns; job batch projection; safe default-reference "
        "foreign keys; status, boolean, and owner checks; scoped setting copy"
    ),
)
DROP_DUPLICATE_INDEXES = SchemaMigration(
    "20260824_0003",
    "drop_exact_duplicate_indexes",
    (
        "drop idx_draft_deliveries_revision, idx_feishu_integrations_owner, "
        "idx_feishu_integrations_callback concurrently on PostgreSQL"
    ),
    transactional=False,
)
SHADOW_BILLING_SCHEMA = SchemaMigration(
    "20260824_0004",
    "shadow_billing_schema",
    (
        "billing plans, subscriptions, model price cards, credit buckets and "
        "ledger, usage operations and AI usage events with seven lookup indexes"
    ),
)
STRICT_TOKEN_METERING = SchemaMigration(
    "20260825_0005",
    "strict_token_metering_status",
    (
        "AI usage token status and provider credits; model metering capability, "
        "strict eligibility, last probe timestamp, sanitized raw usage, and "
        "provider request/response trace-id uniqueness"
    ),
)

SCHEMA_MIGRATIONS = (
    BASELINE_SCHEMA,
    PHASE_ONE_COMPAT,
    DROP_DUPLICATE_INDEXES,
    SHADOW_BILLING_SCHEMA,
    STRICT_TOKEN_METERING,
)


def apply_shadow_billing_schema(conn: Any) -> None:
    """Create the additive token-cost/points shadow schema exactly once."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS billing_plans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            monthly_price_fen INTEGER NOT NULL DEFAULT 0,
            annual_price_fen INTEGER NOT NULL DEFAULT 0,
            monthly_points INTEGER NOT NULL DEFAULT 0,
            entitlements_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL,
            status TEXT NOT NULL,
            current_period_start TEXT,
            current_period_end TEXT,
            next_grant_at TEXT,
            auto_renew INTEGER NOT NULL DEFAULT 0,
            external_subscription_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id) REFERENCES billing_plans(id)
        );

        CREATE TABLE IF NOT EXISTS model_price_cards (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_model TEXT NOT NULL,
            modality TEXT NOT NULL DEFAULT 'text',
            input_micro_cny_per_million INTEGER NOT NULL DEFAULT 0,
            cached_input_micro_cny_per_million INTEGER NOT NULL DEFAULT 0,
            output_micro_cny_per_million INTEGER NOT NULL DEFAULT 0,
            image_micro_cny_each INTEGER NOT NULL DEFAULT 0,
            fixed_request_micro_cny INTEGER NOT NULL DEFAULT 0,
            markup_basis_points INTEGER NOT NULL DEFAULT 10000,
            points_per_cny INTEGER NOT NULL DEFAULT 100,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credit_buckets (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT,
            granted_points INTEGER NOT NULL,
            remaining_points INTEGER NOT NULL CHECK (remaining_points >= 0),
            expires_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS usage_operations (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            scene TEXT NOT NULL,
            source_channel TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'shadow',
            job_id INTEGER,
            estimated_points INTEGER NOT NULL DEFAULT 0,
            reserved_points INTEGER NOT NULL DEFAULT 0,
            charged_points INTEGER NOT NULL DEFAULT 0,
            reservation_expires_at TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
            UNIQUE (owner_user_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS credit_ledger (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            bucket_id TEXT,
            operation_id TEXT,
            amount_points INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_user_id TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (bucket_id) REFERENCES credit_buckets(id),
            FOREIGN KEY (operation_id) REFERENCES usage_operations(id)
        );

        CREATE TABLE IF NOT EXISTS ai_usage_events (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            job_id INTEGER,
            model_id TEXT,
            provider TEXT NOT NULL,
            provider_model TEXT,
            funding_source TEXT NOT NULL,
            modality TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            image_count INTEGER NOT NULL DEFAULT 0,
            fixed_units INTEGER NOT NULL DEFAULT 0,
            usage_source TEXT NOT NULL,
            provider_request_id TEXT,
            provider_response_id TEXT,
            provider_cost_micro_cny INTEGER NOT NULL DEFAULT 0,
            retail_cost_micro_cny INTEGER NOT NULL DEFAULT 0,
            estimated_points INTEGER NOT NULL DEFAULT 0,
            pricing_status TEXT NOT NULL DEFAULT 'price_missing',
            price_snapshot_json TEXT NOT NULL DEFAULT '{}',
            contributes_to_result INTEGER NOT NULL DEFAULT 1,
            billable INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (operation_id) REFERENCES usage_operations(id) ON DELETE CASCADE,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_subscriptions_owner
        ON user_subscriptions(owner_user_id, status);
        CREATE INDEX IF NOT EXISTS idx_price_cards_lookup
        ON model_price_cards(provider, provider_model, modality, effective_from);
        CREATE INDEX IF NOT EXISTS idx_credit_buckets_owner_expiry
        ON credit_buckets(owner_user_id, expires_at, created_at);
        CREATE INDEX IF NOT EXISTS idx_credit_ledger_owner_created
        ON credit_ledger(owner_user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_operations_owner_created
        ON usage_operations(owner_user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_events_operation
        ON ai_usage_events(operation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_events_owner_created
        ON ai_usage_events(owner_user_id, created_at);
        """
    )


def apply_strict_token_metering_schema(conn: Any) -> None:
    """Add explicit Token completeness without rewriting the billing baseline."""

    usage_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(ai_usage_events)").fetchall()
    }
    for name, declaration in {
        "token_usage_status": "TEXT NOT NULL DEFAULT 'RECORDED'",
        "provider_credits": "INTEGER",
        "raw_usage_json": "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in usage_columns:
            conn.execute(
                f"ALTER TABLE ai_usage_events ADD COLUMN {name} {declaration}"
            )
    conn.execute(
        """
        UPDATE ai_usage_events
        SET token_usage_status = CASE
            WHEN usage_source = 'provider_actual'
             AND (input_tokens > 0 OR output_tokens > 0 OR total_tokens > 0)
            THEN 'RECORDED'
            ELSE 'UNAVAILABLE'
        END
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_provider_request_unique
        ON ai_usage_events(owner_user_id, provider, provider_request_id)
        WHERE provider_request_id IS NOT NULL AND provider_request_id <> ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_provider_response_unique
        ON ai_usage_events(owner_user_id, provider, provider_response_id)
        WHERE provider_response_id IS NOT NULL AND provider_response_id <> ''
        """
    )

    model_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(ai_models)").fetchall()
    }
    for name, declaration in {
        "token_metering_capability": "TEXT NOT NULL DEFAULT 'unverified'",
        "strict_token_eligible": "INTEGER NOT NULL DEFAULT 0",
        "token_metering_checked_at": "TEXT",
    }.items():
        if name not in model_columns:
            conn.execute(f"ALTER TABLE ai_models ADD COLUMN {name} {declaration}")
    conn.execute(
        """
        UPDATE ai_models
        SET token_metering_capability = CASE
                WHEN provider_type = 'manus' THEN 'no_token_usage'
                WHEN provider_type = 'local_openai_compatible' THEN 'estimated_only'
                WHEN provider_type IN (
                    'image_alibaba', 'image_minimax', 'image_volcengine',
                    'image_zhipu', 'openai_image'
                ) THEN 'not_applicable'
                ELSE COALESCE(NULLIF(token_metering_capability, ''), 'unverified')
            END,
            strict_token_eligible = CASE
                WHEN provider_type IN (
                    'manus', 'local_openai_compatible', 'image_alibaba',
                    'image_minimax', 'image_volcengine', 'image_zhipu',
                    'openai_image'
                ) THEN 0
                ELSE strict_token_eligible
            END
        """
    )


def ensure_schema_migrations(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def validate_schema_migrations(conn: Any) -> None:
    expected = {migration.version: migration for migration in SCHEMA_MIGRATIONS}
    rows = conn.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    for row in rows:
        version = str(row["version"])
        migration = expected.get(version)
        if migration is None:
            raise RuntimeError(
                f"数据库迁移版本 {version} 高于当前应用，拒绝以旧代码启动"
            )
        if str(row["name"]) != migration.name or str(
            row["checksum"]
        ) != migration.checksum:
            raise RuntimeError(f"数据库迁移 {version} checksum 不一致，拒绝启动")


def migration_applied(conn: Any, migration: SchemaMigration) -> bool:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE version = ?",
        (migration.version,),
    ).fetchone()
    if not row:
        return False
    if str(row["checksum"]) != migration.checksum:
        raise RuntimeError(
            f"数据库迁移 {migration.version} checksum 不一致，拒绝启动"
        )
    return True


def record_schema_migration(
    conn: Any,
    migration: SchemaMigration,
    *,
    applied_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO schema_migrations (version, name, checksum, applied_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(version) DO NOTHING
        """,
        (
            migration.version,
            migration.name,
            migration.checksum,
            applied_at,
        ),
    )


__all__ = [
    "BASELINE_SCHEMA",
    "DROP_DUPLICATE_INDEXES",
    "PHASE_ONE_COMPAT",
    "SHADOW_BILLING_SCHEMA",
    "STRICT_TOKEN_METERING",
    "SCHEMA_MIGRATIONS",
    "SchemaMigration",
    "apply_shadow_billing_schema",
    "apply_strict_token_metering_schema",
    "ensure_schema_migrations",
    "migration_applied",
    "record_schema_migration",
    "validate_schema_migrations",
]
