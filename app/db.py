from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.db_backend import (
    connect_postgres,
    is_postgres_url,
    postgres_integrity_errors,
)
from app.schema_migrations import (
    BASELINE_SCHEMA,
    DROP_DUPLICATE_INDEXES,
    PHASE_ONE_COMPAT,
    SHADOW_BILLING_SCHEMA,
    apply_shadow_billing_schema,
    ensure_schema_migrations,
    migration_applied,
    record_schema_migration,
    validate_schema_migrations,
)
from app.time_utils import business_date, business_day_bounds_utc

JOB_STATUSES = (
    "pending",
    "ingesting",
    "rewriting",
    "title_optimizing",
    "rendering",
    "injecting",
    "ready_for_review",
    "drafted",
    "published",
    "failed",
    "cancelled",
)

STEPS = (
    "ingest",
    "rewrite",
    "title_optimize",
    "render",
    "inject",
    "publish",
)

# Phase-one compatibility contract. New code treats source_channel as the
# canonical persisted source while preserving source for public/API readers.
# The four legacy request columns remain physically present until a later
# observation-backed migration proves they are unused.
JOB_SOURCE_OF_TRUTH = "source_channel"
DEPRECATED_JOB_COLUMNS = (
    "source",
    "source_mode",
    "reference_urls_json",
    "required_facts",
    "rewrite_intensity",
)

_PROCESS_OWNER_SESSION_ID = f"process-{uuid.uuid4().hex}"
_POSTGRES_SCHEMA_INIT_LOCK = threading.RLock()
_POSTGRES_SCHEMA_INITIALIZED: set[str] = set()
_INTEGRITY_ERRORS = (sqlite3.IntegrityError, *postgres_integrity_errors())
_ACTIVE_OWNER_USER_ID: ContextVar[str] = ContextVar(
    "wechat_publisher_owner_user_id",
    default="",
)
_CUSTOMER_SETTING_KEYS = {
    "jizhile_api",
    "onboarding.guide",
    "ui.last_target_account_ids",
    "wechat_backend_search",
}


def _sqlite_test_mode_enabled() -> bool:
    return str(
        os.getenv("WECHAT_PUBLISHER_ALLOW_SQLITE_FOR_TESTS") or ""
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _is_customer_setting(key: str) -> bool:
    clean = str(key or "").strip()
    return clean in _CUSTOMER_SETTING_KEYS or clean.startswith("ui.")


@contextmanager
def customer_data_scope(user_id: str | None) -> Iterator[None]:
    """Scope shared API service objects to one authenticated customer."""

    token = _ACTIVE_OWNER_USER_ID.set(str(user_id or "").strip())
    try:
        yield
    finally:
        _ACTIVE_OWNER_USER_ID.reset(token)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Database:
    def __init__(
        self,
        path: str | Path,
        *,
        owner_session_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> None:
        self.path = str(path)
        self.backend = "postgresql" if is_postgres_url(self.path) else "sqlite"
        if self.backend == "sqlite" and not _sqlite_test_mode_enabled():
            raise RuntimeError(
                "应用已切换为 PostgreSQL-only；请配置 DATABASE_URL。"
                "SQLite 仅允许在隔离自动化测试中使用。"
            )
        self.database_url = self.path if self.backend == "postgresql" else ""
        self.owner_session_id = (
            str(owner_session_id or "").strip()
            or str(
                os.getenv("WECHAT_PUBLISHER_LAUNCH_SESSION_ID") or ""
            ).strip()
            or _PROCESS_OWNER_SESSION_ID
        )
        # ``None`` inherits the current request scope. An explicit empty value
        # is the platform scope used by administrator tooling.
        self._owner_user_id = (
            None
            if owner_user_id is None
            else str(owner_user_id or "").strip()
        )
        if self.backend == "sqlite":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # PostgreSQL schema setup executes the full migration script under an
        # advisory lock. A page creates several user-scoped handles, so doing
        # this for every handle adds seconds to normal navigation. A process
        # restart still performs the check once. SQLite keeps its per-handle
        # initialization because isolated tests replace temporary databases.
        if self.backend == "postgresql":
            with _POSTGRES_SCHEMA_INIT_LOCK:
                if self.database_url not in _POSTGRES_SCHEMA_INITIALIZED:
                    self._init_schema()
                    _POSTGRES_SCHEMA_INITIALIZED.add(self.database_url)
        else:
            self._init_schema()

    def for_user(self, user_id: str | None) -> Database:
        """Return an independent database handle scoped to one login account."""

        return Database(
            self.path,
            owner_session_id=self.owner_session_id,
            owner_user_id=str(user_id or "").strip(),
        )

    def set_owner_user(self, user_id: str | None) -> None:
        self._owner_user_id = str(user_id or "").strip()

    @property
    def owner_user_id(self) -> str:
        if self._owner_user_id is not None:
            return self._owner_user_id
        return _ACTIVE_OWNER_USER_ID.get()

    def _owner_clause(
        self,
        column: str = "owner_user_id",
        *,
        prefix: str = "WHERE",
    ) -> tuple[str, list[Any]]:
        if not self.owner_user_id:
            return "", []
        return f"{prefix} {column} = ?", [self.owner_user_id]

    def _assert_write_owner(
        self,
        conn: Any,
        table_name: str,
        record_id: str,
    ) -> None:
        if not self.owner_user_id:
            return
        row = conn.execute(
            f"SELECT owner_user_id FROM {table_name} WHERE id = ?",
            (str(record_id),),
        ).fetchone()
        if row and str(row["owner_user_id"] or "") != self.owner_user_id:
            raise ValueError("该配置不属于当前登录账号")

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.backend == "postgresql":
            conn = connect_postgres(self.database_url)
        else:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            if self.backend == "postgresql":
                conn.execute(
                    "SELECT pg_advisory_xact_lock(?)",
                    (8_104_721_907_351,),
                )
            ensure_schema_migrations(conn)
            validate_schema_migrations(conn)
            baseline_applied = migration_applied(conn, BASELINE_SCHEMA)
        if baseline_applied:
            self._run_post_baseline_migrations()
            return

        with self.connect() as conn:
            if self.backend == "postgresql":
                # Web, API and the standalone admin console can initialize at
                # the same time. Serialize DDL/migrations per database to avoid
                # PostgreSQL relation-lock deadlocks during concurrent starts.
                conn.execute(
                    "SELECT pg_advisory_xact_lock(?)",
                    (8_104_721_907_351,),
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    step TEXT NOT NULL DEFAULT 'ingest',
                    topic TEXT,
                    source_mode TEXT,
                    reference_urls_json TEXT,
                    required_facts TEXT,
                    rewrite_intensity TEXT,
                    source TEXT,
                    source_url TEXT,
                    raw_content TEXT,
                    raw_title TEXT,
                    body TEXT,
                    titles_json TEXT,
                    subtitles_json TEXT,
                    title_candidates_json TEXT,
                    selected_title TEXT,
                    selected_subtitle TEXT,
                    html_content TEXT,
                    digest TEXT,
                    thumb_media_id TEXT,
                    ad_id TEXT,
                    draft_media_id TEXT,
                    publish_id TEXT,
                    mode TEXT DEFAULT 'draft',
                    error TEXT,
                    meta_json TEXT,
                    scheduled_at TEXT,
                    content_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT,
                    description TEXT,
                    course_start_at TEXT,
                    priority INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    expires_at TEXT,
                    last_used_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS token_cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_models (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    provider_type TEXT NOT NULL DEFAULT 'openai_compatible',
                    api_base TEXT,
                    model TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    local_agent_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_model_requests (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    request_json TEXT NOT NULL,
                    response_text TEXT,
                    error TEXT,
                    result_error_code TEXT NOT NULL DEFAULT '',
                    claimed_by TEXT,
                    agent_id TEXT,
                    operation TEXT NOT NULL DEFAULT 'chat.completions',
                    attempt_id TEXT,
                    nonce TEXT,
                    deadline_at TEXT,
                    lease_until TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (model_id) REFERENCES ai_models(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS prompt_templates (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT 'image',
                    content TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS official_accounts (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    app_secret_encrypted TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    layout_json TEXT,
                    review_priority INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    display_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    topic TEXT,
                    source_url TEXT,
                    raw_content TEXT,
                    requested_by TEXT,
                    chat_id TEXT,
                    error TEXT,
                    parent_batch_id TEXT,
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS batch_jobs (
                    batch_id TEXT NOT NULL,
                    job_id INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'unviewed',
                    viewed_at TEXT,
                    confirmed_at TEXT,
                    PRIMARY KEY (batch_id, job_id),
                    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS bot_sessions (
                    scope_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_contexts (
                    scope_id TEXT PRIMARY KEY,
                    context_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feishu_integrations (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL UNIQUE,
                    app_id TEXT NOT NULL UNIQUE,
                    app_secret_encrypted TEXT NOT NULL,
                    verification_token_encrypted TEXT NOT NULL,
                    encrypt_key_encrypted TEXT NOT NULL,
                    callback_key TEXT NOT NULL UNIQUE,
                    bound_open_id TEXT,
                    bound_chat_id TEXT,
                    agent_model_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'waiting_pairing',
                    pairing_salt TEXT,
                    pairing_code_hash TEXT,
                    pairing_iterations INTEGER,
                    pairing_expires_at TEXT,
                    pairing_used_at TEXT,
                    pairing_failed_attempts INTEGER NOT NULL DEFAULT 0,
                    runtime_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feishu_integration_accounts (
                    integration_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (integration_id, account_id),
                    FOREIGN KEY (integration_id) REFERENCES feishu_integrations(id) ON DELETE CASCADE,
                    FOREIGN KEY (account_id) REFERENCES official_accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS feishu_sessions (
                    integration_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    batch_id TEXT,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (integration_id, chat_id),
                    FOREIGN KEY (integration_id) REFERENCES feishu_integrations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS feishu_processed_events (
                    integration_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (integration_id, event_id),
                    FOREIGN KEY (integration_id) REFERENCES feishu_integrations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS local_model_agents (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    last_seen_at TEXT,
                    cockpit_status TEXT,
                    last_error_code TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS local_agent_pairings (
                    id TEXT PRIMARY KEY,
                    device_code_hash TEXT NOT NULL UNIQUE,
                    user_code_salt TEXT NOT NULL,
                    user_code_hash TEXT NOT NULL,
                    hash_iterations INTEGER NOT NULL,
                    device_name TEXT NOT NULL,
                    owner_user_id TEXT,
                    agent_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT NOT NULL,
                    approved_at TEXT,
                    consumed_at TEXT,
                    last_polled_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS job_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    title TEXT,
                    subtitle TEXT,
                    digest TEXT,
                    body TEXT,
                    html_content TEXT,
                    thumb_media_id TEXT,
                    meta_json TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS editorial_review_profiles (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    description TEXT,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_editorial_review_defaults (
                    account_id TEXT PRIMARY KEY,
                    profile_id TEXT,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES official_accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS creation_plans (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    article_prompt_template_id TEXT,
                    image_prompt_template_id TEXT,
                    editorial_review_profile_id TEXT,
                    layout_json TEXT NOT NULL DEFAULT '{}',
                    image_settings_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_creation_plan_defaults (
                    account_id TEXT PRIMARY KEY,
                    creation_plan_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES official_accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS creation_plan_account_templates (
                    creation_plan_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    source_app_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    capture_title TEXT,
                    placeholder TEXT,
                    selected_media_id TEXT,
                    selected_article_index INTEGER NOT NULL DEFAULT 0,
                    selected_title TEXT,
                    snapshot_html TEXT,
                    snapshot_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (creation_plan_id, account_id),
                    FOREIGN KEY (creation_plan_id) REFERENCES creation_plans(id) ON DELETE CASCADE,
                    FOREIGN KEY (account_id) REFERENCES official_accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS editorial_reviews (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    job_id INTEGER NOT NULL,
                    profile_id TEXT,
                    profile_name TEXT,
                    model_id TEXT,
                    model_name TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    source_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    selected_issue_ids_json TEXT NOT NULL DEFAULT '[]',
                    rewrite_mode TEXT,
                    rewritten_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    blocking_count INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS editorial_review_applications (
                    id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'generating',
                    rewrite_mode TEXT NOT NULL,
                    selected_issue_ids_json TEXT NOT NULL DEFAULT '[]',
                    paragraph_numbers_json TEXT NOT NULL DEFAULT '[]',
                    instruction TEXT,
                    source_hash TEXT NOT NULL,
                    candidate_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    applied_at TEXT,
                    FOREIGN KEY (review_id) REFERENCES editorial_reviews(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS topic_sources (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    source_key TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_synced_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topic_items (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    published_at TEXT,
                    summary TEXT,
                    category TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    used INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_id, title, url),
                    FOREIGN KEY (source_id) REFERENCES topic_sources(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS followed_accounts (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    wechat_id TEXT,
                    official_account_id TEXT,
                    category TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    fetch_method TEXT NOT NULL DEFAULT 'public_search',
                    sample_url TEXT,
                    source_url TEXT,
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    is_owned INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    refresh_hours INTEGER NOT NULL DEFAULT 12,
                    last_synced_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS followed_articles (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    followed_account_id TEXT,
                    account_name TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    discovered_at TEXT NOT NULL,
                    cover_url TEXT,
                    summary TEXT,
                    source_channel TEXT NOT NULL,
                    external_key TEXT,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    is_ignored INTEGER NOT NULL DEFAULT 0,
                    rewritten_batch_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, url),
                    FOREIGN KEY (followed_account_id) REFERENCES followed_accounts(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS job_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    job_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    model_id TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    error_code TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    owner_session_id TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT,
                    completed_at TEXT,
                    next_retry_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS draft_deliveries (
                    idempotency_key TEXT PRIMARY KEY,
                    job_id INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    content_revision INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    draft_media_id TEXT,
                    last_error_code TEXT,
                    error TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    reconciled_at TEXT,
                    next_retry_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, account_id, content_revision, content_fingerprint),
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS wechat_connection_health (
                    account_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'direct',
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    latency_ms INTEGER,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    last_error_code TEXT,
                    error TEXT,
                    last_successful_write_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_topic_items_published
                    ON topic_items(published_at DESC);
                CREATE INDEX IF NOT EXISTS idx_followed_articles_account
                    ON followed_articles(followed_account_id, published_at DESC);
                CREATE INDEX IF NOT EXISTS idx_followed_articles_discovered
                    ON followed_articles(discovered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_editorial_reviews_job
                    ON editorial_reviews(job_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_editorial_reviews_batch
                    ON editorial_reviews(batch_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_editorial_reviews_active_job
                    ON editorial_reviews(job_id)
                    WHERE status IN ('running', 'rewriting');
                CREATE INDEX IF NOT EXISTS idx_editorial_review_applications_review
                    ON editorial_review_applications(review_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_creation_plans_enabled
                    ON creation_plans(enabled, created_at);
                CREATE INDEX IF NOT EXISTS idx_account_creation_plan_defaults_plan
                    ON account_creation_plan_defaults(creation_plan_id);
                CREATE INDEX IF NOT EXISTS idx_creation_plan_account_templates_account
                    ON creation_plan_account_templates(account_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_review_inbox
                    ON jobs(status, step, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_job_attempts_job
                    ON job_attempts(job_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_job_attempts_status
                    ON job_attempts(status, started_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_job_attempts_running
                    ON job_attempts(job_id)
                    WHERE status = 'running';
                CREATE INDEX IF NOT EXISTS idx_draft_deliveries_status
                    ON draft_deliveries(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_user_sessions_user
                    ON user_sessions(user_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_local_model_requests_owner_status
                    ON local_model_requests(owner_user_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_local_model_agents_owner
                    ON local_model_agents(owner_user_id, revoked_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_local_agent_pairings_status
                    ON local_agent_pairings(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_local_agent_pairings_owner
                    ON local_agent_pairings(owner_user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_feishu_integrations_owner
                    ON feishu_integrations(owner_user_id);
                CREATE INDEX IF NOT EXISTS idx_feishu_integrations_callback
                    ON feishu_integrations(callback_key);
                CREATE INDEX IF NOT EXISTS idx_feishu_integration_accounts_owner
                    ON feishu_integration_accounts(owner_user_id, account_id);
                CREATE INDEX IF NOT EXISTS idx_feishu_sessions_owner
                    ON feishu_sessions(owner_user_id, updated_at);
                """
            )
            # Customer-owned records are scoped to the login account. Existing
            # installations receive an empty owner first; the default
            # administrator claims those rows after authentication is seeded.
            for table_name in (
                "ai_models",
                "official_accounts",
                "jobs",
                "prompt_templates",
                "creation_plans",
                "editorial_review_profiles",
                "batches",
                "topic_sources",
                "followed_accounts",
                "followed_articles",
            ):
                columns = {
                    str(row["name"])
                    for row in conn.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }
                if "owner_user_id" not in columns:
                    conn.execute(
                        f"ALTER TABLE {table_name} "
                        "ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT ''"
                    )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table_name}_owner "
                    f"ON {table_name}(owner_user_id)"
                )
            ai_model_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(ai_models)").fetchall()
            }
            pairing_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(local_agent_pairings)"
                ).fetchall()
            }
            if "last_polled_at" not in pairing_columns:
                conn.execute(
                    "ALTER TABLE local_agent_pairings ADD COLUMN last_polled_at TEXT"
                )
            if "local_agent_id" not in ai_model_columns:
                conn.execute("ALTER TABLE ai_models ADD COLUMN local_agent_id TEXT")
            local_request_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(local_model_requests)"
                ).fetchall()
            }
            for name, declaration in {
                "agent_id": "TEXT",
                "operation": "TEXT NOT NULL DEFAULT 'chat.completions'",
                "attempt_id": "TEXT",
                "nonce": "TEXT",
                "deadline_at": "TEXT",
                "lease_until": "TEXT",
                "completed_at": "TEXT",
                "result_error_code": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in local_request_columns:
                    conn.execute(
                        f"ALTER TABLE local_model_requests "
                        f"ADD COLUMN {name} {declaration}"
                    )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_models_local_agent
                ON ai_models(local_agent_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_model_requests_agent_status
                ON local_model_requests(agent_id, status, created_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_model_requests_agent_lease
                ON local_model_requests(agent_id, lease_until)
                """
            )
            if self.backend == "postgresql":
                conn.execute(
                    """
                    ALTER TABLE followed_articles
                    DROP CONSTRAINT IF EXISTS followed_articles_url_key
                    """
                )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_followed_articles_owner_url
                ON followed_articles(owner_user_id, url)
                """
            )
            topic_source_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(topic_sources)"
                ).fetchall()
            }
            if "source_key" not in topic_source_columns:
                conn.execute(
                    """
                    ALTER TABLE topic_sources
                    ADD COLUMN source_key TEXT NOT NULL DEFAULT ''
                    """
                )
            conn.execute(
                """
                UPDATE topic_sources
                SET source_key = id
                WHERE source_key IS NULL OR source_key = ''
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_topic_sources_owner_source_key
                ON topic_sources(owner_user_id, source_key)
                """
            )
            # Lightweight migration for databases created before per-account layouts.
            account_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(official_accounts)").fetchall()
            }
            if "layout_json" not in account_columns:
                conn.execute("ALTER TABLE official_accounts ADD COLUMN layout_json TEXT")
            if "review_priority" not in account_columns:
                conn.execute(
                    """
                    ALTER TABLE official_accounts
                    ADD COLUMN review_priority INTEGER NOT NULL DEFAULT 0
                    """
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_official_accounts_review_priority
                ON official_accounts(review_priority DESC)
                """
            )
            attempt_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(job_attempts)"
                ).fetchall()
            }
            if "owner_session_id" not in attempt_columns:
                conn.execute(
                    """
                    ALTER TABLE job_attempts
                    ADD COLUMN owner_session_id TEXT NOT NULL DEFAULT ''
                    """
                )
            if "heartbeat_at" not in attempt_columns:
                conn.execute(
                    "ALTER TABLE job_attempts ADD COLUMN heartbeat_at TEXT"
                )
            conn.execute(
                """
                UPDATE job_attempts
                SET heartbeat_at = started_at
                WHERE heartbeat_at IS NULL OR heartbeat_at = ''
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_job_attempts_owner_lease
                ON job_attempts(status, owner_session_id, heartbeat_at)
                """
            )
            delivery_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(draft_deliveries)"
                ).fetchall()
            }
            for name, declaration in {
                "content_revision": "INTEGER NOT NULL DEFAULT 0",
                "last_error_code": "TEXT",
                "next_retry_at": "TEXT",
            }.items():
                if name not in delivery_columns:
                    conn.execute(
                        f"ALTER TABLE draft_deliveries ADD COLUMN {name} {declaration}"
                    )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_deliveries_revision
                ON draft_deliveries(
                    job_id, account_id, content_revision, content_fingerprint
                )
                """
            )
            health_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(wechat_connection_health)"
                ).fetchall()
            }
            for name, declaration in {
                "mode": "TEXT NOT NULL DEFAULT 'direct'",
                "latency_ms": "INTEGER",
                "last_error_code": "TEXT",
            }.items():
                if name not in health_columns:
                    conn.execute(
                        "ALTER TABLE wechat_connection_health "
                        f"ADD COLUMN {name} {declaration}"
                    )
            job_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "content_revision" not in job_columns:
                conn.execute(
                    """
                    ALTER TABLE jobs
                    ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "scheduled_at" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN scheduled_at TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_scheduled_at
                ON jobs(scheduled_at)
                """
            )
            review_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(editorial_reviews)"
                ).fetchall()
            }
            if "revision" not in review_columns:
                conn.execute(
                    """
                    ALTER TABLE editorial_reviews
                    ADD COLUMN revision INTEGER NOT NULL DEFAULT 0
                    """
                )
            batch_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(batches)").fetchall()
            }
            for name, declaration in {
                "display_id": "TEXT",
                "topic": "TEXT",
                "source_mode": "TEXT",
                "reference_urls_json": "TEXT",
                "required_facts": "TEXT",
                "rewrite_intensity": "TEXT",
                "parent_batch_id": "TEXT",
                "archived_at": "TEXT",
                "source_integration_id": "TEXT",
            }.items():
                if name not in batch_columns:
                    conn.execute(f"ALTER TABLE batches ADD COLUMN {name} {declaration}")
            batch_job_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(batch_jobs)").fetchall()
            }
            for name, declaration in {
                "review_status": "TEXT NOT NULL DEFAULT 'unviewed'",
                "viewed_at": "TEXT",
                "confirmed_at": "TEXT",
            }.items():
                if name not in batch_job_columns:
                    conn.execute(f"ALTER TABLE batch_jobs ADD COLUMN {name} {declaration}")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_batch_jobs_review_status
                ON batch_jobs(review_status, job_id)
                """
            )
            followed_account_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(followed_accounts)").fetchall()
            }
            if "official_account_id" not in followed_account_columns:
                conn.execute("ALTER TABLE followed_accounts ADD COLUMN official_account_id TEXT")
            creation_plan_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(creation_plans)").fetchall()
            }
            for name, declaration in {
                "layout_json": "TEXT NOT NULL DEFAULT '{}'",
                "image_settings_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in creation_plan_columns:
                    conn.execute(
                        f"ALTER TABLE creation_plans ADD COLUMN {name} {declaration}"
                    )
            creation_plan_template_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(creation_plan_account_templates)"
                ).fetchall()
            }
            for name, declaration in {
                "source_app_id": "TEXT NOT NULL DEFAULT ''",
                "snapshot_sha256": "TEXT",
            }.items():
                if name not in creation_plan_template_columns:
                    conn.execute(
                        "ALTER TABLE creation_plan_account_templates "
                        f"ADD COLUMN {name} {declaration}"
                    )
            version_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(job_versions)").fetchall()
            }
            for name, declaration in {
                "html_content": "TEXT",
                "thumb_media_id": "TEXT",
                "meta_json": "TEXT",
            }.items():
                if name not in version_columns:
                    conn.execute(f"ALTER TABLE job_versions ADD COLUMN {name} {declaration}")
            conn.execute(
                """
                DELETE FROM followed_articles AS old
                WHERE old.url LIKE 'http://mp.weixin.qq.com/%'
                  AND EXISTS (
                      SELECT 1 FROM followed_articles AS current
                      WHERE current.url = 'https://' || substr(old.url, 8)
                  )
                """
            )
            conn.execute(
                """
                UPDATE followed_articles
                SET url = 'https://' || substr(url, 8)
                WHERE url LIKE 'http://mp.weixin.qq.com/%'
                """
            )
            self._migrate_legacy_jobs_to_batches(conn)
            record_schema_migration(
                conn,
                BASELINE_SCHEMA,
                applied_at=_utc_now(),
            )
        self._run_post_baseline_migrations()

    def _run_post_baseline_migrations(self) -> None:
        with self.connect() as conn:
            if self.backend == "postgresql":
                conn.execute(
                    "SELECT pg_advisory_xact_lock(?)",
                    (8_104_721_907_351,),
                )
            ensure_schema_migrations(conn)
            validate_schema_migrations(conn)
            if not migration_applied(conn, PHASE_ONE_COMPAT):
                self._migrate_phase_one_compatibility(conn)
                record_schema_migration(
                    conn,
                    PHASE_ONE_COMPAT,
                    applied_at=_utc_now(),
                )
        self._run_nontransactional_schema_migrations()
        self._run_shadow_billing_schema_migration()

    def _run_shadow_billing_schema_migration(self) -> None:
        with self.connect() as conn:
            if self.backend == "postgresql":
                conn.execute(
                    "SELECT pg_advisory_xact_lock(?)",
                    (8_104_721_907_351,),
                )
            ensure_schema_migrations(conn)
            validate_schema_migrations(conn)
            if not migration_applied(conn, SHADOW_BILLING_SCHEMA):
                apply_shadow_billing_schema(conn)
                record_schema_migration(
                    conn,
                    SHADOW_BILLING_SCHEMA,
                    applied_at=_utc_now(),
                )

    def _run_nontransactional_schema_migrations(self) -> None:
        if self.backend != "postgresql":
            with self.connect() as conn:
                if not migration_applied(conn, DROP_DUPLICATE_INDEXES):
                    self._drop_duplicate_indexes(conn, concurrently=False)
                    record_schema_migration(
                        conn,
                        DROP_DUPLICATE_INDEXES,
                        applied_at=_utc_now(),
                    )
            return
        conn = connect_postgres(self.database_url, autocommit=True)
        try:
            conn.execute(
                "SELECT pg_advisory_lock(?)",
                (8_104_721_907_351,),
            )
            ensure_schema_migrations(conn)
            validate_schema_migrations(conn)
            if not migration_applied(conn, DROP_DUPLICATE_INDEXES):
                self._drop_duplicate_indexes(conn, concurrently=True)
                record_schema_migration(
                    conn,
                    DROP_DUPLICATE_INDEXES,
                    applied_at=_utc_now(),
                )
        finally:
            try:
                conn.execute(
                    "SELECT pg_advisory_unlock(?)",
                    (8_104_721_907_351,),
                )
            finally:
                conn.close()

    @staticmethod
    def _drop_duplicate_indexes(conn: Any, *, concurrently: bool) -> None:
        modifier = " CONCURRENTLY" if concurrently else ""
        for index_name in (
            "idx_draft_deliveries_revision",
            "idx_feishu_integrations_owner",
            "idx_feishu_integrations_callback",
        ):
            conn.execute(f"DROP INDEX{modifier} IF EXISTS {index_name}")

    def _migrate_phase_one_compatibility(self, conn: Any) -> None:
        """Apply the first additive PostgreSQL optimization release."""

        account_columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(official_accounts)"
            ).fetchall()
        }
        for name, declaration in {
            "default_creation_plan_id": "TEXT",
            "default_editorial_review_profile_id": "TEXT",
            "editorial_review_config_json": "TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if name not in account_columns:
                conn.execute(
                    f"ALTER TABLE official_accounts ADD COLUMN {name} {declaration}"
                )

        job_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for name, declaration in {
            "batch_id": "TEXT",
            "account_id": "TEXT",
            "account_name_snapshot": "TEXT",
            "review_status": "TEXT NOT NULL DEFAULT 'unviewed'",
            "viewed_at": "TEXT",
            "confirmed_at": "TEXT",
            "source_channel": "TEXT",
        }.items():
            if name not in job_columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")

        duplicate_job = conn.execute(
            """
            SELECT job_id, COUNT(*) AS link_count
            FROM batch_jobs
            GROUP BY job_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate_job:
            raise RuntimeError(
                "检测到一个文章任务属于多个批次，已停止 jobs 结构回填以避免数据歧义"
            )

        if self.backend == "postgresql":
            conn.execute(
                """
                ALTER TABLE account_creation_plan_defaults
                ALTER COLUMN creation_plan_id DROP NOT NULL
                """
            )
            conn.execute(
                """
                UPDATE account_creation_plan_defaults AS defaults
                SET creation_plan_id = NULL, updated_at = ?
                WHERE creation_plan_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM official_accounts AS account
                      JOIN creation_plans AS plan
                        ON plan.id = defaults.creation_plan_id
                       AND plan.owner_user_id = account.owner_user_id
                      WHERE account.id = defaults.account_id
                  )
                """,
                (_utc_now(),),
            )
        else:
            conn.execute(
                """
                DELETE FROM account_creation_plan_defaults
                WHERE creation_plan_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM official_accounts AS account
                      JOIN creation_plans AS plan
                        ON plan.id = account_creation_plan_defaults.creation_plan_id
                       AND plan.owner_user_id = account.owner_user_id
                      WHERE account.id = account_creation_plan_defaults.account_id
                  )
                """
            )
        conn.execute(
            """
            UPDATE account_editorial_review_defaults AS defaults
            SET profile_id = NULL, config_json = '{}', updated_at = ?
            WHERE profile_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM official_accounts AS account
                  JOIN editorial_review_profiles AS profile
                    ON profile.id = defaults.profile_id
                   AND profile.owner_user_id = account.owner_user_id
                  WHERE account.id = defaults.account_id
              )
            """,
            (_utc_now(),),
        )
        conn.execute(
            """
            UPDATE official_accounts AS account
            SET default_creation_plan_id = (
                    SELECT defaults.creation_plan_id
                    FROM account_creation_plan_defaults AS defaults
                    WHERE defaults.account_id = account.id
                ),
                default_editorial_review_profile_id = (
                    SELECT defaults.profile_id
                    FROM account_editorial_review_defaults AS defaults
                    WHERE defaults.account_id = account.id
                ),
                editorial_review_config_json = COALESCE((
                    SELECT defaults.config_json
                    FROM account_editorial_review_defaults AS defaults
                    WHERE defaults.account_id = account.id
                ), '{}')
            """
        )
        conn.execute(
            """
            UPDATE jobs
            SET batch_id = (
                    SELECT batch_id FROM batch_jobs WHERE job_id = jobs.id
                ),
                account_id = (
                    SELECT account_id FROM batch_jobs WHERE job_id = jobs.id
                ),
                account_name_snapshot = (
                    SELECT account_name FROM batch_jobs WHERE job_id = jobs.id
                ),
                review_status = COALESCE((
                    SELECT review_status FROM batch_jobs WHERE job_id = jobs.id
                ), review_status, 'unviewed'),
                viewed_at = (
                    SELECT viewed_at FROM batch_jobs WHERE job_id = jobs.id
                ),
                confirmed_at = (
                    SELECT confirmed_at FROM batch_jobs WHERE job_id = jobs.id
                )
            WHERE EXISTS (SELECT 1 FROM batch_jobs WHERE job_id = jobs.id)
            """
        )
        conn.execute(
            """
            UPDATE jobs
            SET source_channel = source
            WHERE source_channel IS NULL OR source_channel = ''
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_account ON jobs(account_id, id)"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_review_status
            ON jobs(review_status, status, created_at)
            """
        )

        self._migrate_scoped_customer_settings(conn)
        if self.backend == "postgresql":
            self._add_phase_one_postgres_constraints(conn)

    @staticmethod
    def _migrate_scoped_customer_settings(conn: Any) -> None:
        owner_row = conn.execute(
            """
            SELECT value
            FROM app_settings
            WHERE key = 'migration.customer_data_owner.v1'
            """
        ).fetchone()
        owner_user_id = str(owner_row["value"] if owner_row else "").strip()
        if not owner_user_id:
            return
        user_exists = conn.execute(
            "SELECT 1 FROM users WHERE id = ?",
            (owner_user_id,),
        ).fetchone()
        if not user_exists:
            return
        now = _utc_now()
        for row in conn.execute(
            """
            SELECT key, value
            FROM app_settings
            WHERE key IN (
                'jizhile_api',
                'onboarding.guide',
                'ui.last_target_account_ids',
                'wechat_backend_search'
            ) OR key LIKE 'ui.%'
            """
        ).fetchall():
            conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO NOTHING
                """,
                (owner_user_id, str(row["key"]), str(row["value"]), now),
            )

    @staticmethod
    def _postgres_constraint_exists(conn: Any, name: str) -> bool:
        return bool(
            conn.execute(
                """
                SELECT 1
                FROM pg_constraint
                WHERE connamespace = current_schema()::regnamespace
                  AND conname = ?
                """,
                (name,),
            ).fetchone()
        )

    @classmethod
    def _add_postgres_foreign_key(
        cls,
        conn: Any,
        *,
        table: str,
        name: str,
        column: str,
        referenced_table: str,
    ) -> None:
        if not cls._postgres_constraint_exists(conn, name):
            conn.execute(
                f"""
                ALTER TABLE {table}
                ADD CONSTRAINT {name}
                FOREIGN KEY ({column}) REFERENCES {referenced_table}(id)
                ON DELETE SET NULL NOT VALID
                """
            )
        conn.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

    @classmethod
    def _add_postgres_check(
        cls,
        conn: Any,
        *,
        table: str,
        name: str,
        expression: str,
        invalid_where: str,
    ) -> None:
        if not cls._postgres_constraint_exists(conn, name):
            conn.execute(
                f"""
                ALTER TABLE {table}
                ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID
                """
            )
        invalid = conn.execute(
            f"SELECT 1 FROM {table} WHERE {invalid_where} LIMIT 1"
        ).fetchone()
        if not invalid:
            conn.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

    @classmethod
    def _add_phase_one_postgres_constraints(cls, conn: Any) -> None:
        for table, name, column, referenced_table in (
            (
                "account_creation_plan_defaults",
                "fk_account_creation_plan_defaults_plan",
                "creation_plan_id",
                "creation_plans",
            ),
            (
                "account_editorial_review_defaults",
                "fk_account_editorial_review_defaults_profile",
                "profile_id",
                "editorial_review_profiles",
            ),
            (
                "official_accounts",
                "fk_official_accounts_default_creation_plan",
                "default_creation_plan_id",
                "creation_plans",
            ),
            (
                "official_accounts",
                "fk_official_accounts_default_review_profile",
                "default_editorial_review_profile_id",
                "editorial_review_profiles",
            ),
            (
                "jobs",
                "fk_jobs_batch",
                "batch_id",
                "batches",
            ),
        ):
            cls._add_postgres_foreign_key(
                conn,
                table=table,
                name=name,
                column=column,
                referenced_table=referenced_table,
            )
        for table, name, expression, invalid_where in (
            (
                "jobs",
                "ck_jobs_status",
                "status IN ("
                + ", ".join(f"'{status}'" for status in JOB_STATUSES)
                + ")",
                "status NOT IN ("
                + ", ".join(f"'{status}'" for status in JOB_STATUSES)
                + ") OR status IS NULL",
            ),
            (
                "jobs",
                "ck_jobs_step",
                "step IN (" + ", ".join(f"'{step}'" for step in STEPS) + ")",
                "step NOT IN ("
                + ", ".join(f"'{step}'" for step in STEPS)
                + ") OR step IS NULL",
            ),
            (
                "batch_jobs",
                "ck_batch_jobs_review_status",
                "review_status IN ('unviewed', 'viewed', 'confirmed', 'needs_changes')",
                "review_status NOT IN ('unviewed', 'viewed', 'confirmed', 'needs_changes') OR review_status IS NULL",
            ),
            (
                "official_accounts",
                "ck_official_accounts_enabled",
                "enabled IN (0, 1)",
                "enabled NOT IN (0, 1) OR enabled IS NULL",
            ),
            (
                "official_accounts",
                "ck_official_accounts_owner",
                "owner_user_id <> ''",
                "owner_user_id IS NULL OR owner_user_id = ''",
            ),
            (
                "batches",
                "ck_batches_owner",
                "owner_user_id <> ''",
                "owner_user_id IS NULL OR owner_user_id = ''",
            ),
            (
                "jobs",
                "ck_jobs_owner",
                "owner_user_id <> ''",
                "owner_user_id IS NULL OR owner_user_id = ''",
            ),
            (
                "feishu_integrations",
                "ck_feishu_integrations_enabled",
                "enabled IN (0, 1)",
                "enabled NOT IN (0, 1) OR enabled IS NULL",
            ),
            (
                "feishu_integrations",
                "ck_feishu_integrations_owner",
                "owner_user_id <> ''",
                "owner_user_id IS NULL OR owner_user_id = ''",
            ),
            (
                "feishu_integration_accounts",
                "ck_feishu_integration_accounts_default",
                "is_default IN (0, 1)",
                "is_default NOT IN (0, 1) OR is_default IS NULL",
            ),
        ):
            cls._add_postgres_check(
                conn,
                table=table,
                name=name,
                expression=expression,
                invalid_where=invalid_where,
            )


    @staticmethod
    def _migrate_legacy_jobs_to_batches(conn: sqlite3.Connection) -> None:
        """Make pre-batch jobs visible in the new task center exactly once."""
        conn.execute(
            """
            UPDATE batch_jobs
            SET account_id = 'account_config_default',
                account_name = CASE
                    WHEN account_name = '历史默认公众号' THEN '默认公众号'
                    ELSE account_name
                END
            WHERE account_id = 'legacy-default'
            """
        )
        rows = conn.execute(
            """
            SELECT j.*
            FROM jobs j
            LEFT JOIN batch_jobs bj ON bj.job_id = j.id
            WHERE bj.job_id IS NULL
            ORDER BY j.created_at, j.id
            """
        ).fetchall()
        if not rows:
            return

        groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        preferred_ids: dict[tuple[str, str, str], str] = {}
        for row in rows:
            try:
                meta = json.loads(str(row["meta_json"] or "{}"))
            except json.JSONDecodeError:
                meta = {}
            preferred = str(meta.get("batch_id") or "").strip()
            if preferred:
                key = ("batch", preferred, "")
                preferred_ids[key] = preferred
            else:
                # Concurrent legacy jobs were inserted in the same second with
                # the same source. This reconstructs those batches without
                # guessing across unrelated operations.
                key = (
                    str(row["created_at"] or "")[:19],
                    str(row["source_url"] or ""),
                    str(row["topic"] or ""),
                )
            groups.setdefault(key, []).append(row)

        day_counts: dict[str, int] = {}
        for existing in conn.execute(
            "SELECT created_at FROM batches ORDER BY created_at"
        ).fetchall():
            day = str(existing["created_at"] or "")[:10]
            day_counts[day] = day_counts.get(day, 0) + 1

        for key, jobs in groups.items():
            first = jobs[0]
            batch_id = preferred_ids.get(key) or f'legacy-{int(first["id"]):08d}'
            exists = conn.execute(
                "SELECT 1 FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if not exists:
                created_at = str(first["created_at"])
                day = created_at[:10]
                day_counts[day] = day_counts.get(day, 0) + 1
                display_id = f'{day.replace("-", "")}-{day_counts[day]:02d}'
                statuses = {str(job["status"] or "") for job in jobs}
                if statuses and statuses <= {"drafted", "published"}:
                    batch_status = "drafted"
                elif statuses == {"ready_for_review"}:
                    batch_status = "ready_for_review"
                elif statuses == {"cancelled"}:
                    batch_status = "cancelled"
                elif statuses & {
                    "pending", "ingesting", "rewriting", "title_optimizing",
                    "rendering", "injecting",
                }:
                    batch_status = "processing"
                else:
                    batch_status = "partial_failed"
                conn.execute(
                    """
                    INSERT INTO batches (
                        id, display_id, status, topic, source_url, raw_content,
                        requested_by, chat_id, error, parent_batch_id,
                        archived_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        batch_id,
                        display_id,
                        batch_status,
                        first["topic"],
                        first["source_url"],
                        first["raw_content"],
                        created_at,
                        max(str(job["updated_at"]) for job in jobs),
                    ),
                )
            for job in jobs:
                try:
                    meta = json.loads(str(job["meta_json"] or "{}"))
                except json.JSONDecodeError:
                    meta = {}
                account_id = str(
                    meta.get("official_account_id") or "account_config_default"
                )
                account_name = str(meta.get("official_account_name") or "历史默认公众号")
                status = str(job["status"] or "")
                review_status = (
                    "confirmed" if status in {"drafted", "published"} else "unviewed"
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO batch_jobs (
                        batch_id, job_id, account_id, account_name,
                        review_status, viewed_at, confirmed_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        batch_id,
                        int(job["id"]),
                        account_id,
                        account_name,
                        review_status,
                        str(job["updated_at"]) if review_status == "confirmed" else None,
                    ),
                )

    def create_job(
        self,
        *,
        topic: str | None = None,
        source_mode: str | None = None,
        reference_urls: list[str] | None = None,
        required_facts: str | None = None,
        rewrite_intensity: str | None = None,
        source: str = "manual",
        source_url: str | None = None,
        raw_content: str | None = None,
        mode: str = "draft",
        meta: dict[str, Any] | None = None,
    ) -> int:
        now = _utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    owner_user_id, status, step, topic, source, source_channel,
                    source_url, raw_content, mode,
                    meta_json, created_at, updated_at
                ) VALUES (?, ?, 'ingest', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.owner_user_id,
                    "pending",
                    topic,
                    source,
                    source,
                    source_url,
                    raw_content,
                    mode,
                    json.dumps(meta or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM jobs WHERE id = ?"
            params: list[Any] = [job_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return self._row_to_job(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            owner_clause = "WHERE owner_user_id = ?" if self.owner_user_id else ""
            params: list[Any] = []
            if self.owner_user_id:
                params.append(self.owner_user_id)
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM jobs {owner_clause} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def recover_stale_jobs(
        self,
        *,
        older_than_minutes: int = 30,
        owner_session_id: str | None = None,
    ) -> int:
        """Recover orphaned work using persisted launch-session leases.

        Attempts owned by a different launcher session are interrupted
        immediately. Attempts from the current session are left alone until
        their heartbeat lease expires. Legacy active jobs without a running
        attempt retain the previous timestamp-based recovery behavior.
        """

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(minutes=max(1, older_than_minutes))
        ).replace(microsecond=0).isoformat()
        active = (
            "pending",
            "ingesting",
            "rewriting",
            "title_optimizing",
            "rendering",
            "injecting",
        )
        active_placeholders = ",".join("?" for _ in active)
        current_owner = (
            str(owner_session_id or "").strip() or self.owner_session_id
        )
        now = _utc_now()
        recovered_job_ids: set[int] = set()
        with self.connect() as conn:
            stale_attempts = conn.execute(
                """
                SELECT id, job_id
                FROM job_attempts
                WHERE status = 'running'
                  AND (
                      COALESCE(owner_session_id, '') <> ?
                      OR COALESCE(heartbeat_at, started_at) < ?
                  )
                """,
                (current_owner, cutoff),
            ).fetchall()
            for row in stale_attempts:
                cursor = conn.execute(
                    """
                    UPDATE job_attempts
                    SET status = 'cancelled',
                        error_code = 'job.interrupted',
                        error = '应用重启后检测到历史执行已中断',
                        completed_at = ?
                    WHERE id = ? AND status = 'running'
                      AND (
                          COALESCE(owner_session_id, '') <> ?
                          OR COALESCE(heartbeat_at, started_at) < ?
                      )
                    """,
                    (
                        now,
                        int(row["id"]),
                        current_owner,
                        cutoff,
                    ),
                )
                if cursor.rowcount:
                    recovered_job_ids.add(int(row["job_id"]))
            if recovered_job_ids:
                job_placeholders = ",".join(
                    "?" for _ in recovered_job_ids
                )
                conn.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'cancelled',
                        error = '应用重启后检测到历史任务已中断，请重新发起改写',
                        updated_at = ?
                    WHERE id IN ({job_placeholders})
                      AND status IN ({active_placeholders})
                    """,
                    (now, *recovered_job_ids, *active),
                )

            orphaned_rows = conn.execute(
                f"""
                SELECT j.id
                FROM jobs j
                WHERE j.status IN ({active_placeholders})
                  AND j.updated_at < ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM job_attempts a
                      WHERE a.job_id = j.id AND a.status = 'running'
                  )
                """,
                (*active, cutoff),
            ).fetchall()
            orphaned_job_ids = [
                int(row["id"]) for row in orphaned_rows
            ]
            if orphaned_job_ids:
                recovered_orphans: list[int] = []
                for orphaned_job_id in orphaned_job_ids:
                    cursor = conn.execute(
                        f"""
                    UPDATE jobs
                    SET status = 'cancelled',
                        error = '应用重启后检测到历史任务已中断，请重新发起改写',
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ({active_placeholders})
                      AND updated_at < ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM job_attempts a
                          WHERE a.job_id = jobs.id
                            AND a.status = 'running'
                      )
                    """,
                        (
                            now,
                            orphaned_job_id,
                            *active,
                            cutoff,
                        ),
                    )
                    if cursor.rowcount:
                        recovered_orphans.append(orphaned_job_id)
                recovered_job_ids.update(recovered_orphans)

            affected_batch_ids: list[str] = []
            if recovered_job_ids:
                recovered_placeholders = ",".join(
                    "?" for _ in recovered_job_ids
                )
                affected_batch_ids = [
                    str(row["batch_id"])
                    for row in conn.execute(
                        f"""
                        SELECT DISTINCT batch_id
                        FROM jobs
                        WHERE id IN ({recovered_placeholders})
                          AND batch_id IS NOT NULL
                        """,
                        tuple(recovered_job_ids),
                    ).fetchall()
                ]
            self._recompute_batch_statuses_locked(
                conn, affected_batch_ids, updated_at=now
            )
        return len(recovered_job_ids)

    @staticmethod
    def _recompute_batch_statuses_locked(
        conn: sqlite3.Connection,
        batch_ids: list[str],
        *,
        updated_at: str,
    ) -> None:
        for batch_id in batch_ids:
            statuses = {
                str(row["status"] or "")
                for row in conn.execute(
                    """
                    SELECT status
                    FROM jobs
                    WHERE batch_id = ?
                    """,
                    (batch_id,),
                ).fetchall()
            }
            if not statuses:
                continue
            active = {
                "pending",
                "ingesting",
                "rewriting",
                "title_optimizing",
                "rendering",
            }
            if statuses & active:
                status = "processing"
            elif "injecting" in statuses:
                status = "injecting"
            elif statuses <= {"drafted", "published"}:
                status = "drafted"
            elif statuses == {"ready_for_review"}:
                status = "ready_for_review"
            elif statuses == {"cancelled"}:
                status = "cancelled"
            elif statuses == {"failed"}:
                status = "failed"
            else:
                status = "partial_failed"
            conn.execute(
                """
                UPDATE batches
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, updated_at, batch_id),
            )

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "step",
            "topic",
            "source",
            "source_url",
            "raw_content",
            "raw_title",
            "body",
            "titles_json",
            "subtitles_json",
            "title_candidates_json",
            "selected_title",
            "selected_subtitle",
            "html_content",
            "digest",
            "thumb_media_id",
            "ad_id",
            "draft_media_id",
            "publish_id",
            "mode",
            "error",
            "meta_json",
            "scheduled_at",
        }
        updates: list[str] = []
        values: list[Any] = []
        content_compare: list[tuple[str, Any]] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key.endswith("_json") and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            if key == "meta_json" and isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            updates.append(f"{key} = ?")
            values.append(value)
            if key == "source":
                updates.append("source_channel = ?")
                values.append(value)
            if key in {
                "selected_title",
                "selected_subtitle",
                "digest",
                "body",
            }:
                content_compare.append((key, value))
        if content_compare:
            # SQLite accepts ``column IS NOT ?`` as a null-safe inequality,
            # while PostgreSQL rejects the translated ``IS NOT $n`` syntax.
            # Use PostgreSQL's null-safe comparison without changing the
            # existing SQLite behavior.
            difference_operator = (
                "IS DISTINCT FROM" if self.backend == "postgresql" else "IS NOT"
            )
            updates.append(
                "content_revision = content_revision + CASE WHEN ("
                + " OR ".join(
                    f"{key} {difference_operator} ?"
                    for key, _value in content_compare
                )
                + ") THEN 1 ELSE 0 END"
            )
            values.extend(value for _key, value in content_compare)
        updates.append("updated_at = ?")
        values.append(_utc_now())
        values.append(job_id)
        owner_clause = ""
        if self.owner_user_id:
            owner_clause = " AND owner_user_id = ?"
            values.append(self.owner_user_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?{owner_clause}",
                values,
            )

    def claim_ready_job_for_content_update(
        self,
        job_id: int,
        *,
        expected_content_revision: int,
        operation_status: str = "rewriting",
    ) -> bool:
        """Atomically reserve one unchanged review job for a content mutation."""

        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, step = 'rewrite', error = NULL, updated_at = ?
                WHERE id = ?
                  AND status = 'ready_for_review'
                  AND content_revision = ?
                """,
                (
                    operation_status,
                    now,
                    int(job_id),
                    max(0, int(expected_content_revision or 0)),
                ),
            )
            return int(cursor.rowcount or 0) == 1

    def claim_job_for_retry(
        self,
        job_id: int,
        *,
        expected_status: str,
        target_status: str,
        target_step: str,
        expected_updated_at: str | None = None,
    ) -> bool:
        """Atomically reserve a job for retry across processes.

        ``expected_updated_at`` prevents an ABA race where another process
        claims a failed job, finishes quickly, and changes it back to
        ``failed`` before this caller reaches the compare-and-set.
        """

        with self.connect() as conn:
            if expected_updated_at is None:
                cursor = conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, step = ?, error = NULL, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        str(target_status),
                        str(target_step),
                        _utc_now(),
                        int(job_id),
                        str(expected_status),
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, step = ?, error = NULL, updated_at = ?
                    WHERE id = ? AND status = ? AND updated_at = ?
                    """,
                    (
                        str(target_status),
                        str(target_step),
                        _utc_now(),
                        int(job_id),
                        str(expected_status),
                        str(expected_updated_at),
                    ),
                )
            return int(cursor.rowcount or 0) == 1

    def save_job_version(self, job_id: int, *, reason: str) -> int:
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO job_versions (
                    job_id, title, subtitle, digest, body, html_content,
                    thumb_media_id, meta_json, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job.get("selected_title"),
                    job.get("selected_subtitle"),
                    job.get("digest"),
                    job.get("body"),
                    job.get("html_content"),
                    job.get("thumb_media_id"),
                    json.dumps(job.get("meta") or {}, ensure_ascii=False),
                    reason,
                    _utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_job_versions(self, job_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_versions WHERE job_id = ? ORDER BY id DESC LIMIT ?",
                (job_id, max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_job_version(self, job_id: int, version_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_versions WHERE job_id = ? AND id = ?",
                (job_id, version_id),
            ).fetchone()
            return dict(row) if row else None

    def create_batch(
        self,
        batch_id: str,
        *,
        source_url: str | None = None,
        raw_content: str | None = None,
        topic: str | None = None,
        source_mode: str | None = None,
        reference_urls: list[str] | None = None,
        required_facts: str | None = None,
        rewrite_intensity: str | None = None,
        requested_by: str | None = None,
        chat_id: str | None = None,
        parent_batch_id: str | None = None,
        source_integration_id: str | None = None,
    ) -> None:
        now = _utc_now()
        local_day = business_date()
        day_start, day_end = business_day_bounds_utc(local_day)
        with self.connect() as conn:
            day = local_day.strftime("%Y%m%d")
            count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS value
                    FROM batches
                    WHERE created_at >= ? AND created_at < ?
                      AND (? = '' OR owner_user_id = ?)
                    """,
                    (
                        day_start.isoformat(timespec="microseconds"),
                        day_end.isoformat(timespec="microseconds"),
                        self.owner_user_id,
                        self.owner_user_id,
                    ),
                ).fetchone()["value"]
                or 0
            )
            display_id = f"{day}-{count + 1:02d}"
            conn.execute(
                """
                INSERT INTO batches (
                    id, owner_user_id, display_id, status, topic, source_mode,
                    reference_urls_json, required_facts, rewrite_intensity,
                    source_url, raw_content,
                    requested_by, chat_id, error, parent_batch_id,
                    source_integration_id, archived_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?)
                """,
                (
                    batch_id,
                    self.owner_user_id,
                    display_id,
                    topic,
                    source_mode,
                    json.dumps(reference_urls or [], ensure_ascii=False),
                    required_facts,
                    rewrite_intensity,
                    source_url,
                    raw_content,
                    requested_by,
                    chat_id,
                    parent_batch_id,
                    source_integration_id,
                    now,
                    now,
                ),
            )

    def attach_batch_job(
        self,
        batch_id: str,
        job_id: int,
        account_id: str,
        account_name: str,
    ) -> None:
        with self.connect() as conn:
            job = conn.execute(
                "SELECT owner_user_id, batch_id FROM jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
            if job and job["batch_id"] and str(job["batch_id"]) != batch_id:
                raise ValueError("文章任务已经属于其他批次")
            if self.owner_user_id:
                batch = conn.execute(
                    "SELECT 1 FROM batches WHERE id = ? AND owner_user_id = ?",
                    (batch_id, self.owner_user_id),
                ).fetchone()
                account = conn.execute(
                    "SELECT owner_user_id FROM official_accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                if (
                    not batch
                    or not job
                    or str(job["owner_user_id"] or "") != self.owner_user_id
                    or (
                        account
                        and str(account["owner_user_id"] or "")
                        != self.owner_user_id
                    )
                ):
                    raise ValueError("批次、任务或公众号不存在")
            conn.execute(
                """
                INSERT INTO batch_jobs (batch_id, job_id, account_id, account_name)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, job_id, account_id, account_name),
            )
            cursor = conn.execute(
                """
                UPDATE jobs
                SET batch_id = ?, account_id = ?, account_name_snapshot = ?,
                    updated_at = ?
                WHERE id = ? AND (batch_id IS NULL OR batch_id = ?)
                """,
                (
                    batch_id,
                    account_id,
                    account_name,
                    _utc_now(),
                    int(job_id),
                    batch_id,
                ),
            )
            if not cursor.rowcount:
                raise ValueError("文章任务已经属于其他批次")

    def update_batch(
        self,
        batch_id: str,
        *,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        updates = ["updated_at = ?"]
        values: list[Any] = [_utc_now()]
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if error is not None:
            updates.append("error = ?")
            values.append(error)
        values.append(batch_id)
        if self.owner_user_id:
            values.append(self.owner_user_id)
        with self.connect() as conn:
            where = "id = ?"
            if self.owner_user_id:
                where += " AND owner_user_id = ?"
            conn.execute(
                f"UPDATE batches SET {', '.join(updates)} WHERE {where}", values
            )

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM batches WHERE id = ?"
            params: list[Any] = [batch_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            if not row:
                return None
            batch = dict(row)
            jobs = conn.execute(
                f"""
                SELECT j.*, j.account_name_snapshot AS account_name
                FROM jobs j
                WHERE j.batch_id = ?
                {"AND j.owner_user_id = ?" if self.owner_user_id else ""}
                ORDER BY j.id
                """,
                (batch_id, self.owner_user_id)
                if self.owner_user_id
                else (batch_id,),
            ).fetchall()
            batch["jobs"] = [self._row_to_job(item) for item in jobs]
            return batch

    def list_batches(
        self,
        *,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if not include_archived:
                clauses.append("archived_at IS NULL")
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            params.append(max(1, int(limit)))
            rows = conn.execute(
                f"SELECT * FROM batches {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            batches = [dict(row) for row in rows]
            batch_ids = [str(batch["id"]) for batch in batches]
            jobs_by_batch: dict[str, list[dict[str, Any]]] = {
                batch_id: [] for batch_id in batch_ids
            }
            if batch_ids:
                placeholders = ", ".join("?" for _ in batch_ids)
                job_rows = conn.execute(
                    f"""
                    SELECT j.batch_id AS linked_batch_id, j.*,
                           j.account_name_snapshot AS account_name
                    FROM jobs j
                    WHERE j.batch_id IN ({placeholders})
                    {"AND j.owner_user_id = ?" if self.owner_user_id else ""}
                    ORDER BY j.batch_id, j.id
                    """,
                    [*batch_ids, self.owner_user_id]
                    if self.owner_user_id
                    else batch_ids,
                ).fetchall()
                for item in job_rows:
                    linked_batch_id = str(item["linked_batch_id"])
                    jobs_by_batch.setdefault(linked_batch_id, []).append(
                        self._row_to_job(item)
                    )
            for batch in batches:
                batch["jobs"] = jobs_by_batch.get(str(batch["id"]), [])
            return batches

    def update_batch_job_review(
        self,
        batch_id: str,
        job_id: int,
        review_status: str,
    ) -> None:
        now = _utc_now()
        viewed_at = now if review_status in {"viewed", "confirmed", "needs_changes"} else None
        confirmed_at = now if review_status == "confirmed" else None
        with self.connect() as conn:
            owner_clause = ""
            params: list[Any] = [
                review_status,
                viewed_at,
                confirmed_at,
                batch_id,
                int(job_id),
            ]
            if self.owner_user_id:
                owner_clause = (
                    " AND EXISTS (SELECT 1 FROM batches b "
                    "WHERE b.id = batch_jobs.batch_id AND b.owner_user_id = ?)"
                )
                params.append(self.owner_user_id)
            cursor = conn.execute(
                f"""
                UPDATE batch_jobs
                SET review_status = ?,
                    viewed_at = COALESCE(viewed_at, ?),
                    confirmed_at = ?
                WHERE batch_id = ? AND job_id = ?
                {owner_clause}
                """,
                params,
            )
            if not cursor.rowcount:
                raise KeyError(f"任务不属于该批次：{job_id}")
            job_params: list[Any] = [
                review_status,
                viewed_at,
                confirmed_at,
                int(job_id),
                batch_id,
            ]
            job_owner_clause = ""
            if self.owner_user_id:
                job_owner_clause = " AND owner_user_id = ?"
                job_params.append(self.owner_user_id)
            job_cursor = conn.execute(
                f"""
                UPDATE jobs
                SET review_status = ?,
                    viewed_at = COALESCE(viewed_at, ?),
                    confirmed_at = ?
                WHERE id = ? AND batch_id = ?{job_owner_clause}
                """,
                job_params,
            )
            if not job_cursor.rowcount:
                raise KeyError(f"任务不属于该批次：{job_id}")

    def review_inbox_counts(
        self,
        *,
        account_id: str | None = None,
        requested_by: str | None = None,
        chat_id: str | None = None,
        search: str | None = None,
    ) -> dict[str, int]:
        """Return queue counters without loading every batch into memory."""

        day_start_value, day_end_value = business_day_bounds_utc()
        day_start = day_start_value.isoformat(timespec="microseconds")
        day_end = day_end_value.isoformat(timespec="microseconds")
        account_clause = " AND j.account_id = ?" if account_id else ""
        account_params: list[Any] = [str(account_id)] if account_id else []
        search_clause = ""
        search_params: list[Any] = []
        search_value = str(search or "").strip()
        if search_value:
            search_clause = """
                AND (
                    COALESCE(j.selected_title, '') LIKE ?
                    OR COALESCE(j.raw_title, '') LIKE ?
                    OR COALESCE(j.topic, '') LIKE ?
                    OR COALESCE(j.source_channel, j.source, '') LIKE ?
                    OR COALESCE(j.source_url, '') LIKE ?
                    OR COALESCE(b.topic, '') LIKE ?
                    OR COALESCE(b.source_url, '') LIKE ?
                    OR COALESCE(j.account_name_snapshot, '') LIKE ?
                )
            """
            pattern = f"%{search_value}%"
            search_params = [pattern] * 8
        scope_parts: list[str] = []
        scope_params: list[Any] = []
        if requested_by:
            scope_parts.append("b.requested_by = ?")
            scope_params.append(str(requested_by))
        if chat_id:
            scope_parts.append("b.chat_id = ?")
            scope_params.append(str(chat_id))
        scope_clause = (
            " AND (" + " OR ".join(scope_parts) + ")"
            if scope_parts
            else ""
        )
        owner_clause = (
            " AND b.owner_user_id = ? AND j.owner_user_id = ?"
            if self.owner_user_id
            else ""
        )
        owner_params: list[Any] = (
            [self.owner_user_id, self.owner_user_id]
            if self.owner_user_id
            else []
        )
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    SUM(CASE
                        WHEN j.status = 'ready_for_review'
                         AND COALESCE(j.review_status, 'unviewed') != 'confirmed'
                        THEN 1 ELSE 0 END
                    ) AS review,
                    SUM(CASE
                        WHEN j.status = 'ready_for_review'
                         AND COALESCE(j.review_status, 'unviewed') = 'confirmed'
                        THEN 1 ELSE 0 END
                    ) AS ready_for_draft,
                    SUM(CASE
                        WHEN j.status = 'failed' AND j.step = 'inject'
                        THEN 1 ELSE 0 END
                    ) AS write_failed,
                    SUM(CASE
                        WHEN j.status = 'failed' AND j.step != 'inject'
                        THEN 1 ELSE 0 END
                    ) AS generation_failed,
                    SUM(CASE
                        WHEN j.status IN ('drafted', 'published')
                         AND j.updated_at >= ? AND j.updated_at < ?
                        THEN 1 ELSE 0 END
                    ) AS today_completed
                FROM jobs j
                JOIN batches b ON b.id = j.batch_id
                WHERE b.archived_at IS NULL
                {owner_clause}
                {account_clause}
                {search_clause}
                {scope_clause}
                """,
                (
                    day_start,
                    day_end,
                    *owner_params,
                    *account_params,
                    *search_params,
                    *scope_params,
                ),
            ).fetchone()
        values = dict(row) if row else {}
        return {
            key: int(values.get(key) or 0)
            for key in (
                "review",
                "ready_for_draft",
                "write_failed",
                "generation_failed",
                "today_completed",
            )
        }

    def list_review_inbox_rows(
        self,
        *,
        bucket: str = "review",
        account_id: str | None = None,
        requested_by: str | None = None,
        chat_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return one article-level page ordered by operator urgency."""

        normalized_bucket = str(bucket or "review").strip().lower()
        day_start_value, day_end_value = business_day_bounds_utc()
        day_start = day_start_value.isoformat(timespec="microseconds")
        day_end = day_end_value.isoformat(timespec="microseconds")
        overdue = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat(timespec="microseconds")
        scheduled_day = business_date().isoformat()
        clauses = {
            "review": (
                "j.status = 'ready_for_review' "
                "AND COALESCE(j.review_status, 'unviewed') != 'confirmed'"
            ),
            "ready_for_draft": (
                "j.status = 'ready_for_review' "
                "AND COALESCE(j.review_status, 'unviewed') = 'confirmed'"
            ),
            "write_failed": "j.status = 'failed' AND j.step = 'inject'",
            "generation_failed": "j.status = 'failed' AND j.step != 'inject'",
            "today_completed": (
                "j.status IN ('drafted', 'published') "
                "AND j.updated_at >= ? AND j.updated_at < ?"
            ),
        }
        if normalized_bucket not in clauses:
            raise ValueError(f"Unsupported review inbox bucket: {bucket}")
        filter_params: list[Any] = []
        if normalized_bucket == "today_completed":
            filter_params.extend((day_start, day_end))
        account_clause = ""
        if account_id:
            account_clause = " AND j.account_id = ?"
            filter_params.append(str(account_id))
        search_clause = ""
        search_value = str(search or "").strip()
        if search_value:
            search_clause = """
                AND (
                    COALESCE(j.selected_title, '') LIKE ?
                    OR COALESCE(j.raw_title, '') LIKE ?
                    OR COALESCE(j.topic, '') LIKE ?
                    OR COALESCE(j.source_channel, j.source, '') LIKE ?
                    OR COALESCE(j.source_url, '') LIKE ?
                    OR COALESCE(b.topic, '') LIKE ?
                    OR COALESCE(b.source_url, '') LIKE ?
                    OR COALESCE(j.account_name_snapshot, '') LIKE ?
                )
            """
            pattern = f"%{search_value}%"
            filter_params.extend([pattern] * 8)
        scope_parts: list[str] = []
        if requested_by:
            scope_parts.append("b.requested_by = ?")
            filter_params.append(str(requested_by))
        if chat_id:
            scope_parts.append("b.chat_id = ?")
            filter_params.append(str(chat_id))
        scope_clause = (
            " AND (" + " OR ".join(scope_parts) + ")"
            if scope_parts
            else ""
        )
        owner_clause = (
            " AND b.owner_user_id = ? AND j.owner_user_id = ?"
            if self.owner_user_id
            else ""
        )
        owner_params: list[Any] = (
            [self.owner_user_id, self.owner_user_id]
            if self.owner_user_id
            else []
        )
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    b.id AS batch_id,
                    b.display_id AS batch_display_id,
                    b.topic AS batch_topic,
                    b.source_url AS batch_source_url,
                    b.source_mode AS batch_source_mode,
                    j.account_name_snapshot AS account_name,
                    COALESCE(oa.review_priority, 0) AS review_priority,
                    er.id AS latest_review_id,
                    er.status AS latest_review_status,
                    er.result_json AS latest_review_result_json,
                    er.blocking_count AS latest_review_blocking_count,
                    j.*,
                    CASE
                        WHEN j.created_at >= ? AND j.created_at < ? THEN 1
                        WHEN j.status = 'ready_for_review'
                         AND COALESCE(j.review_status, 'unviewed') != 'confirmed'
                         AND j.created_at < ? THEN 2
                        WHEN substr(COALESCE(j.scheduled_at, ''), 1, 10) = ? THEN 3
                        WHEN COALESCE(oa.review_priority, 0) > 0 THEN 4
                        ELSE 5
                    END AS priority_bucket
                FROM jobs j
                JOIN batches b ON b.id = j.batch_id
                LEFT JOIN official_accounts oa ON oa.id = j.account_id
                LEFT JOIN editorial_reviews er ON er.id = (
                    SELECT er2.id
                    FROM editorial_reviews er2
                    WHERE er2.job_id = j.id
                    ORDER BY er2.created_at DESC, er2.id DESC
                    LIMIT 1
                )
                WHERE b.archived_at IS NULL
                  {owner_clause}
                  AND {clauses[normalized_bucket]}
                  {account_clause}
                  {search_clause}
                  {scope_clause}
                ORDER BY
                    priority_bucket ASC,
                    COALESCE(oa.review_priority, 0) DESC,
                    CASE
                        WHEN j.status = 'ready_for_review'
                         AND COALESCE(j.review_status, 'unviewed') != 'confirmed'
                         AND j.created_at < ?
                        THEN j.created_at
                    END ASC,
                    j.created_at DESC,
                    j.id DESC
                LIMIT ? OFFSET ?
                """,
                (
                    day_start,
                    day_end,
                    overdue,
                    scheduled_day,
                    *owner_params,
                    *filter_params,
                    overdue,
                    max(1, min(int(limit), 200)),
                    max(0, int(offset)),
                ),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def has_active_batches(self) -> bool:
        """Return whether any non-archived batch still has active article work."""

        with self.connect() as conn:
            owner_clause = ""
            params: list[Any] = []
            if self.owner_user_id:
                owner_clause = (
                    " AND b.owner_user_id = ? AND j.owner_user_id = ?"
                )
                params.extend((self.owner_user_id, self.owner_user_id))
            row = conn.execute(
                f"""
                SELECT 1
                FROM jobs j
                JOIN batches b ON b.id = j.batch_id
                WHERE b.archived_at IS NULL
                  {owner_clause}
                  AND j.status IN (
                      'pending',
                      'ingesting',
                      'rewriting',
                      'title_optimizing',
                      'rendering',
                      'injecting'
                )
                LIMIT 1
                """,
                params,
            ).fetchone()
        return row is not None

    def create_job_attempt(
        self,
        *,
        batch_id: str,
        job_id: int,
        stage: str,
        model_id: str | None = None,
        next_retry_at: str | None = None,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically create the only active execution attempt for a job."""

        now = _utc_now()
        owner = (
            str(owner_session_id or "").strip() or self.owner_session_id
        )
        try:
            with self.connect() as conn:
                attempt_no = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) + 1 AS value
                        FROM job_attempts
                        WHERE job_id = ? AND stage = ?
                        """,
                        (int(job_id), str(stage)),
                    ).fetchone()["value"]
                    or 1
                )
                cursor = conn.execute(
                    """
                    INSERT INTO job_attempts (
                        batch_id, job_id, stage, attempt_no, model_id,
                        status, started_at, owner_session_id, heartbeat_at,
                        next_retry_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                    """,
                    (
                        str(batch_id),
                        int(job_id),
                        str(stage),
                        attempt_no,
                        model_id or None,
                        now,
                        owner,
                        now,
                        next_retry_at,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE jobs SET updated_at = ? WHERE id = ?",
                    (now, int(job_id)),
                )
                row = conn.execute(
                    "SELECT * FROM job_attempts WHERE id = ?",
                    (int(cursor.lastrowid),),
                ).fetchone()
        except _INTEGRITY_ERRORS as exc:
            raise ValueError("该文章已有操作正在执行，请勿重复提交。") from exc
        if not row:
            raise RuntimeError("Unable to create job attempt")
        return dict(row)

    def heartbeat_job_attempt(
        self,
        attempt_id: int,
        *,
        owner_session_id: str | None = None,
    ) -> bool:
        """Renew a running attempt lease owned by this launcher session."""

        owner = (
            str(owner_session_id or "").strip() or self.owner_session_id
        )
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT job_id
                FROM job_attempts
                WHERE id = ? AND status = 'running'
                  AND owner_session_id = ?
                """,
                (int(attempt_id), owner),
            ).fetchone()
            if not row:
                return False
            cursor = conn.execute(
                """
                UPDATE job_attempts
                SET heartbeat_at = ?
                WHERE id = ? AND status = 'running'
                  AND owner_session_id = ?
                """,
                (now, int(attempt_id), owner),
            )
            if not cursor.rowcount:
                return False
            conn.execute(
                """
                UPDATE jobs
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, int(row["job_id"])),
            )
        return True

    def finish_job_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        error_code: str | None = None,
        error: str | None = None,
        next_retry_at: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE job_attempts
                SET status = ?, error_code = ?, error = ?,
                    completed_at = ?, next_retry_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    str(status),
                    error_code or None,
                    error or None,
                    now,
                    next_retry_at,
                    int(attempt_id),
                ),
            )
            if not cursor.rowcount:
                raise KeyError(f"Job attempt is not running: {attempt_id}")
            row = conn.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (int(attempt_id),)
            ).fetchone()
        return dict(row)

    def list_job_attempts(
        self, job_id: int, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_attempts
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(job_id), max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_wechat_connection_health(
        self, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM wechat_connection_health
                WHERE account_id = ?
                """,
                (str(account_id),),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["details"] = _loads_json(result.get("details_json"), {})
        return result

    def upsert_wechat_connection_health(
        self,
        account_id: str,
        *,
        status: str,
        checked_at: str,
        expires_at: str,
        details: dict[str, Any] | None = None,
        error: str | None = None,
        mode: str = "direct",
        latency_ms: int | None = None,
        last_error_code: str | None = None,
        last_successful_write_at: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO wechat_connection_health (
                    account_id, mode, status, checked_at, expires_at,
                    latency_ms, details_json, last_error_code, error,
                    last_successful_write_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    mode=excluded.mode,
                    status=excluded.status,
                    checked_at=excluded.checked_at,
                    expires_at=excluded.expires_at,
                    latency_ms=excluded.latency_ms,
                    details_json=excluded.details_json,
                    last_error_code=excluded.last_error_code,
                    error=excluded.error,
                    last_successful_write_at=COALESCE(
                        excluded.last_successful_write_at,
                        wechat_connection_health.last_successful_write_at
                    ),
                    updated_at=excluded.updated_at
                """,
                (
                    str(account_id),
                    str(mode or "direct"),
                    str(status),
                    str(checked_at),
                    str(expires_at),
                    latency_ms,
                    json.dumps(details or {}, ensure_ascii=False),
                    last_error_code or None,
                    error or None,
                    last_successful_write_at or None,
                    now,
                ),
            )
        result = self.get_wechat_connection_health(account_id)
        if not result:
            raise RuntimeError("Unable to save WeChat connection health")
        return result

    def invalidate_wechat_connection_health(self, account_id: str) -> None:
        """Expire a cached probe while preserving last successful write data."""

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE wechat_connection_health
                SET status = 'stale', expires_at = '', updated_at = ?
                WHERE account_id = ?
                """,
                (_utc_now(), str(account_id)),
            )

    def invalidate_all_wechat_connection_health(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE wechat_connection_health
                SET status = 'stale', expires_at = '', updated_at = ?
                """,
                (_utc_now(),),
            )

    def mark_wechat_connection_write_success(
        self,
        account_id: str,
        *,
        written_at: str | None = None,
    ) -> None:
        now = written_at or _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO wechat_connection_health (
                    account_id, mode, status, checked_at, expires_at,
                    details_json, last_successful_write_at, updated_at
                ) VALUES (?, 'direct', 'unknown', ?, ?, '{}', ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_successful_write_at=excluded.last_successful_write_at,
                    updated_at=excluded.updated_at
                """,
                (str(account_id), now, now, now, now),
            )

    def claim_draft_delivery(
        self,
        *,
        idempotency_key: str,
        job_id: int,
        account_id: str,
        content_fingerprint: str,
        content_revision: int = 0,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO draft_deliveries (
                    idempotency_key, job_id, account_id, content_fingerprint,
                    content_revision, status, attempts, details_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, '{}', ?, ?)
                """,
                (
                    str(idempotency_key),
                    int(job_id),
                    str(account_id),
                    str(content_fingerprint),
                    max(0, int(content_revision or 0)),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM draft_deliveries
                WHERE job_id = ? AND account_id = ?
                  AND content_revision = ? AND content_fingerprint = ?
                """,
                (
                    int(job_id),
                    str(account_id),
                    max(0, int(content_revision or 0)),
                    str(content_fingerprint),
                ),
            ).fetchone()
        if not row:
            raise RuntimeError("Unable to claim draft delivery")
        result = self._draft_delivery_row(row)
        result["claimed_new"] = bool(cursor.rowcount)
        return result

    def get_draft_delivery(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM draft_deliveries
                WHERE idempotency_key = ?
                """,
                (str(idempotency_key),),
            ).fetchone()
        return self._draft_delivery_row(row) if row else None

    def transition_draft_delivery(
        self,
        idempotency_key: str,
        *,
        from_statuses: list[str] | tuple[str, ...] | set[str],
        status: str,
        draft_media_id: str | None = None,
        error: str | None = None,
        details: dict[str, Any] | None = None,
        reconciled_at: str | None = None,
        next_retry_at: str | None = None,
        last_error_code: str | None = None,
    ) -> dict[str, Any] | None:
        allowed = tuple(str(item) for item in from_statuses if str(item))
        if not allowed:
            return None
        placeholders = ", ".join("?" for _ in allowed)
        now = _utc_now()
        attempts_sql = (
            "attempts + 1" if str(status) == "running" else "attempts"
        )
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE draft_deliveries
                SET status = ?,
                    attempts = {attempts_sql},
                    draft_media_id = COALESCE(?, draft_media_id),
                    last_error_code = ?,
                    error = ?,
                    details_json = COALESCE(?, details_json),
                    reconciled_at = COALESCE(?, reconciled_at),
                    next_retry_at = ?,
                    updated_at = ?
                WHERE idempotency_key = ?
                  AND status IN ({placeholders})
                """,
                (
                    str(status),
                    draft_media_id or None,
                    last_error_code or None,
                    error or None,
                    (
                        json.dumps(details, ensure_ascii=False)
                        if details is not None
                        else None
                    ),
                    reconciled_at or None,
                    next_retry_at or None,
                    now,
                    str(idempotency_key),
                    *allowed,
                ),
            )
            if not cursor.rowcount:
                return None
            row = conn.execute(
                """
                SELECT * FROM draft_deliveries
                WHERE idempotency_key = ?
                """,
                (str(idempotency_key),),
            ).fetchone()
        return self._draft_delivery_row(row) if row else None

    @staticmethod
    def _draft_delivery_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["details"] = _loads_json(result.get("details_json"), {})
        return result

    def archive_batch(self, batch_id: str, *, archived: bool = True) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE batches SET archived_at = ?, updated_at = ? WHERE id = ?",
                (_utc_now() if archived else None, _utc_now(), batch_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"批次不存在：{batch_id}")

    # ------------------------------------------------------------------
    # Per-user Feishu integrations
    # ------------------------------------------------------------------
    def get_feishu_integration(self) -> dict[str, Any] | None:
        """Return the current authenticated user's own Feishu integration."""

        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feishu_integrations WHERE owner_user_id = ?",
                (owner_user_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_feishu_integration_by_callback_key(
        self, callback_key: str
    ) -> dict[str, Any] | None:
        """Resolve the tenant before authentication of a Feishu webhook.

        This is intentionally the only unscoped integration lookup. The random
        callback key selects the credential set; signature/token verification
        still happens before an event is trusted.
        """

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feishu_integrations WHERE callback_key = ?",
                (str(callback_key),),
            ).fetchone()
        return dict(row) if row else None

    def feishu_integration_health(self) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
                FROM feishu_integrations
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0) if row else 0,
            "enabled": int(row["enabled"] or 0) if row else 0,
            "errors": int(row["errors"] or 0) if row else 0,
        }

    def save_feishu_integration(
        self,
        *,
        app_id: str,
        app_secret_encrypted: str,
        verification_token_encrypted: str,
        encrypt_key_encrypted: str,
        callback_key: str,
        agent_model_id: str,
        account_ids: list[str],
        default_account_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise PermissionError("请先登录后再配置飞书机器人")
        clean_account_ids = list(
            dict.fromkeys(str(item).strip() for item in account_ids if str(item).strip())
        )
        if not clean_account_ids:
            raise ValueError("请至少选择一个机器人可操作的公众号")
        if default_account_id not in clean_account_ids:
            raise ValueError("默认公众号必须包含在机器人可操作的公众号中")
        now = _utc_now()
        existing = self.get_feishu_integration()
        integration_id = str(
            (existing or {}).get("id") or f"feishu_{uuid.uuid4().hex}"
        )
        with self.connect() as conn:
            placeholders = ",".join("?" for _ in clean_account_ids)
            rows = conn.execute(
                f"""
                SELECT id FROM official_accounts
                WHERE owner_user_id = ? AND enabled = 1
                  AND id IN ({placeholders})
                """,
                (owner_user_id, *clean_account_ids),
            ).fetchall()
            available_ids = {str(row["id"]) for row in rows}
            if available_ids != set(clean_account_ids):
                raise ValueError("所选公众号不属于当前用户、已停用或不存在")
            conn.execute(
                """
                INSERT INTO feishu_integrations (
                    id, owner_user_id, app_id, app_secret_encrypted,
                    verification_token_encrypted, encrypt_key_encrypted,
                    callback_key, agent_model_id, enabled, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting_pairing', ?, ?)
                ON CONFLICT(owner_user_id) DO UPDATE SET
                    app_id=excluded.app_id,
                    app_secret_encrypted=excluded.app_secret_encrypted,
                    verification_token_encrypted=excluded.verification_token_encrypted,
                    encrypt_key_encrypted=excluded.encrypt_key_encrypted,
                    callback_key=excluded.callback_key,
                    agent_model_id=excluded.agent_model_id,
                    enabled=excluded.enabled,
                    status=CASE
                        WHEN feishu_integrations.bound_open_id IS NOT NULL
                        THEN 'active' ELSE 'waiting_pairing' END,
                    updated_at=excluded.updated_at
                """,
                (
                    integration_id,
                    owner_user_id,
                    str(app_id),
                    str(app_secret_encrypted),
                    str(verification_token_encrypted),
                    str(encrypt_key_encrypted),
                    str(callback_key),
                    str(agent_model_id),
                    int(bool(enabled)),
                    now,
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM feishu_integration_accounts WHERE integration_id = ?",
                (integration_id,),
            )
            for account_id in clean_account_ids:
                conn.execute(
                    """
                    INSERT INTO feishu_integration_accounts (
                        integration_id, owner_user_id, account_id,
                        is_default, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        integration_id,
                        owner_user_id,
                        account_id,
                        int(account_id == default_account_id),
                        now,
                    ),
                )
        integration = self.get_feishu_integration()
        if not integration:
            raise RuntimeError("飞书机器人配置保存失败")
        return integration

    def list_feishu_integration_accounts(
        self, integration_id: str
    ) -> list[dict[str, Any]]:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT fia.account_id, fia.is_default, oa.name, oa.enabled
                FROM feishu_integration_accounts fia
                JOIN official_accounts oa ON oa.id = fia.account_id
                WHERE fia.integration_id = ?
                  AND fia.owner_user_id = ?
                  AND oa.owner_user_id = ?
                ORDER BY fia.is_default DESC, oa.name
                """,
                (str(integration_id), owner_user_id, owner_user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_feishu_pairing(
        self,
        integration_id: str,
        *,
        salt: str,
        code_hash: str,
        iterations: int,
        expires_at: str,
    ) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise PermissionError("请先登录后再生成配对码")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE feishu_integrations
                SET pairing_salt = ?, pairing_code_hash = ?,
                    pairing_iterations = ?, pairing_expires_at = ?,
                    pairing_used_at = NULL, pairing_failed_attempts = 0,
                    status = 'waiting_pairing', updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    salt,
                    code_hash,
                    int(iterations),
                    expires_at,
                    _utc_now(),
                    str(integration_id),
                    owner_user_id,
                ),
            )
            if not cursor.rowcount:
                raise KeyError("飞书机器人配置不存在")

    def consume_feishu_pairing(
        self,
        integration_id: str,
        *,
        expected_code_hash: str,
        open_id: str,
        chat_id: str,
    ) -> bool:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return False
        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE feishu_integrations
                SET bound_open_id = ?, bound_chat_id = ?, pairing_used_at = ?,
                    status = 'active', updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND enabled = 1
                  AND pairing_code_hash = ?
                  AND pairing_used_at IS NULL
                  AND pairing_failed_attempts < 5
                  AND pairing_expires_at > ?
                """,
                (
                    str(open_id),
                    str(chat_id),
                    now,
                    now,
                    str(integration_id),
                    owner_user_id,
                    str(expected_code_hash),
                    now,
                ),
            )
            return bool(cursor.rowcount)

    def fail_feishu_pairing(self, integration_id: str) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE feishu_integrations
                SET pairing_failed_attempts = pairing_failed_attempts + 1,
                    updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                  AND pairing_used_at IS NULL
                  AND pairing_failed_attempts < 5
                """,
                (_utc_now(), str(integration_id), owner_user_id),
            )

    def unbind_feishu_integration(self) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise PermissionError("请先登录后再解除绑定")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE feishu_integrations
                SET bound_open_id = NULL, bound_chat_id = NULL,
                    pairing_salt = NULL, pairing_code_hash = NULL,
                    pairing_expires_at = NULL, pairing_used_at = NULL,
                    pairing_failed_attempts = 0,
                    status = 'waiting_pairing', updated_at = ?
                WHERE owner_user_id = ?
                """,
                (_utc_now(), owner_user_id),
            )

    def set_feishu_integration_enabled(self, enabled: bool) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise PermissionError("请先登录后再停用机器人")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE feishu_integrations
                SET enabled = ?, status = CASE
                    WHEN ? = 0 THEN 'disabled'
                    WHEN bound_open_id IS NOT NULL THEN 'active'
                    ELSE 'waiting_pairing' END,
                    updated_at = ?
                WHERE owner_user_id = ?
                """,
                (int(bool(enabled)), int(bool(enabled)), _utc_now(), owner_user_id),
            )

    def update_feishu_runtime(
        self, integration_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return {}
        integration = self.get_feishu_integration()
        if not integration or str(integration.get("id")) != str(integration_id):
            return {}
        try:
            runtime = json.loads(str(integration.get("runtime_json") or "{}"))
        except json.JSONDecodeError:
            runtime = {}
        runtime.update(dict(changes))
        runtime["updated_at"] = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE feishu_integrations
                SET runtime_json = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    json.dumps(runtime, ensure_ascii=False),
                    _utc_now(),
                    str(integration_id),
                    owner_user_id,
                ),
            )
        return runtime

    def claim_feishu_event(self, integration_id: str, event_id: str) -> bool:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return False
        integration = self.get_feishu_integration()
        if not integration or str(integration.get("id")) != str(integration_id):
            return False
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO feishu_processed_events (
                        integration_id, owner_user_id, event_id, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (str(integration_id), owner_user_id, str(event_id), _utc_now()),
                )
            return True
        except _INTEGRITY_ERRORS:
            return False

    def set_feishu_session(
        self,
        integration_id: str,
        chat_id: str,
        *,
        batch_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise PermissionError("飞书会话缺少用户作用域")
        integration = self.get_feishu_integration()
        if not integration or str(integration.get("id")) != str(integration_id):
            raise PermissionError("飞书会话不属于当前用户的机器人")
        current = self.get_feishu_session(integration_id, chat_id)
        effective_batch_id = batch_id if batch_id is not None else current.get("batch_id")
        effective_context = context if context is not None else current.get("context", {})
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feishu_sessions (
                    integration_id, owner_user_id, chat_id, batch_id,
                    context_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(integration_id, chat_id) DO UPDATE SET
                    owner_user_id=excluded.owner_user_id,
                    batch_id=excluded.batch_id,
                    context_json=excluded.context_json,
                    updated_at=excluded.updated_at
                """,
                (
                    str(integration_id),
                    owner_user_id,
                    str(chat_id),
                    effective_batch_id,
                    json.dumps(effective_context or {}, ensure_ascii=False),
                    _utc_now(),
                ),
            )

    def get_feishu_session(
        self, integration_id: str, chat_id: str
    ) -> dict[str, Any]:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return {"batch_id": None, "context": {}}
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT batch_id, context_json
                FROM feishu_sessions
                WHERE integration_id = ? AND chat_id = ? AND owner_user_id = ?
                """,
                (str(integration_id), str(chat_id), owner_user_id),
            ).fetchone()
        if not row:
            return {"batch_id": None, "context": {}}
        try:
            context = json.loads(str(row["context_json"] or "{}"))
        except json.JSONDecodeError:
            context = {}
        return {
            "batch_id": str(row["batch_id"]) if row["batch_id"] else None,
            "context": context if isinstance(context, dict) else {},
        }

    def set_bot_session(self, scope_id: str, batch_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_sessions (scope_id, batch_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scope_id) DO UPDATE SET
                    batch_id=excluded.batch_id,
                    updated_at=excluded.updated_at
                """,
                (scope_id, batch_id, _utc_now()),
            )

    def get_bot_session(self, scope_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT batch_id FROM bot_sessions WHERE scope_id = ?", (scope_id,)
            ).fetchone()
            return str(row["batch_id"]) if row else None

    def set_bot_context(self, scope_id: str, context: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_contexts (scope_id, context_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scope_id) DO UPDATE SET
                    context_json=excluded.context_json,
                    updated_at=excluded.updated_at
                """,
                (scope_id, json.dumps(context or {}, ensure_ascii=False), _utc_now()),
            )

    def get_bot_context(self, scope_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT context_json FROM bot_contexts WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
        if not row:
            return {}
        try:
            value = json.loads(str(row["context_json"] or "{}"))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def claim_event(self, event_id: str) -> bool:
        """Return True once for each external event id."""
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO processed_events (event_id, created_at) VALUES (?, ?)",
                    (event_id, _utc_now()),
                )
            return True
        except _INTEGRITY_ERRORS:
            return False

    def get_setting(self, key: str) -> str | None:
        if self.owner_user_id and _is_customer_setting(key):
            scoped_value = self.get_user_setting(key)
            if scoped_value is not None:
                return scoped_value
            # One compatibility cycle: installations upgraded before the
            # user-settings split may still have only the historical value.
            # Only the explicitly claimed legacy owner may see that row; other
            # users must never inherit the same customer setting.
            with self.connect() as conn:
                row = conn.execute(
                    """
                    SELECT setting.value
                    FROM app_settings AS setting
                    JOIN app_settings AS claim
                      ON claim.key = 'migration.customer_data_owner.v1'
                     AND claim.value = ?
                    WHERE setting.key = ?
                    """,
                    (self.owner_user_id, key),
                ).fetchone()
            return str(row["value"]) if row else None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        if self.owner_user_id and _is_customer_setting(key):
            self.set_user_setting(key, value)
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, value, _utc_now()),
            )

    def get_user_setting(self, key: str) -> str | None:
        """Read a setting belonging to the current login account."""

        if not self.owner_user_id:
            return self.get_setting(key)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT value
                FROM user_settings
                WHERE user_id = ? AND key = ?
                """,
                (self.owner_user_id, str(key)),
            ).fetchone()
        return str(row["value"]) if row else None

    def set_user_setting(self, key: str, value: str) -> None:
        """Persist a setting for the current login account."""

        if not self.owner_user_id:
            self.set_setting(key, value)
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (self.owner_user_id, str(key), str(value), _utc_now()),
            )

    def claim_legacy_customer_data(self, user_id: str) -> None:
        """Assign pre-login customer data to the default administrator.

        Business records and customer settings use separate migration markers.
        This keeps upgrades idempotent while allowing installations which have
        already claimed business rows to still migrate legacy user settings.
        """

        clean_user_id = str(user_id or "").strip()
        if not clean_user_id:
            return
        with self.connect() as conn:
            if self.backend == "postgresql":
                conn.execute(
                    "SELECT pg_advisory_xact_lock(?)",
                    (8_104_721_907_352,),
                )
            owner_marker_key = "migration.customer_data_owner.v1"
            owner_migrated = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (owner_marker_key,),
            ).fetchone()
            if not owner_migrated:
                for table_name in (
                    "official_accounts",
                    "jobs",
                    "prompt_templates",
                    "creation_plans",
                    "editorial_review_profiles",
                    "batches",
                    "topic_sources",
                    "followed_accounts",
                    "followed_articles",
                ):
                    conn.execute(
                        f"UPDATE {table_name} SET owner_user_id = ? "
                        "WHERE owner_user_id IS NULL OR owner_user_id = ''",
                        (clean_user_id,),
                    )
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (owner_marker_key, clean_user_id, _utc_now()),
                )

            settings_marker_key = "migration.customer_settings_owner.v1"
            settings_migrated = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (settings_marker_key,),
            ).fetchone()
            if not settings_migrated:
                now = _utc_now()
                legacy_settings = conn.execute(
                    "SELECT key, value FROM app_settings"
                ).fetchall()
                for legacy in legacy_settings:
                    key = str(legacy["key"])
                    if not _is_customer_setting(key):
                        continue
                    conn.execute(
                        """
                        INSERT INTO user_settings (
                            user_id, key, value, updated_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, key) DO NOTHING
                        """,
                        (clean_user_id, key, str(legacy["value"]), now),
                    )
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (settings_marker_key, clean_user_id, now),
                )

    # ------------------------------------------------------------------
    # Users and login sessions
    # ------------------------------------------------------------------
    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        enabled: bool = True,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        new_id = str(user_id or f"user_{uuid.uuid4().hex}")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, role, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    str(username),
                    str(password_hash),
                    str(role),
                    int(bool(enabled)),
                    now,
                    now,
                ),
            )
        user = self.get_user(new_id)
        if not user:
            raise RuntimeError("Unable to create user")
        return user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (str(user_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM users
                WHERE lower(username) = lower(?)
                """,
                (str(username),),
            ).fetchone()
        return dict(row) if row else None

    def list_users(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM users"
        params: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled = ?"
            params = (1,)
        sql += " ORDER BY created_at ASC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def set_user_enabled(self, user_id: str, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(bool(enabled)), _utc_now(), str(user_id)),
            )

    def create_user_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        expires_at: str,
    ) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_sessions (
                    token_hash, user_id, expires_at, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(token_hash) DO UPDATE SET
                    user_id=excluded.user_id,
                    expires_at=excluded.expires_at,
                    last_seen_at=excluded.last_seen_at
                """,
                (str(token_hash), str(user_id), str(expires_at), now, now),
            )

    def get_user_session(self, token_hash: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    s.token_hash,
                    s.expires_at,
                    s.created_at AS session_created_at,
                    s.last_seen_at,
                    u.id,
                    u.username,
                    u.password_hash,
                    u.role,
                    u.enabled,
                    u.created_at,
                    u.updated_at
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                  AND s.expires_at > ?
                  AND u.enabled = 1
                """,
                (str(token_hash), now),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE user_sessions
                    SET last_seen_at = ?
                    WHERE token_hash = ?
                    """,
                    (now, str(token_hash)),
                )
        return dict(row) if row else None

    def delete_user_session(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM user_sessions WHERE token_hash = ?",
                (str(token_hash),),
            )

    def purge_expired_user_sessions(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?",
                (_utc_now(),),
            )
        return int(cursor.rowcount or 0)

    def upsert_ad(self, ad: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ads (
                    id, title, url, description, course_start_at, priority,
                    enabled, expires_at, last_used_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    url=excluded.url,
                    description=excluded.description,
                    course_start_at=excluded.course_start_at,
                    priority=excluded.priority,
                    enabled=excluded.enabled,
                    expires_at=excluded.expires_at
                """,
                (
                    ad["id"],
                    ad.get("title", ""),
                    ad.get("url"),
                    ad.get("description"),
                    ad.get("course_start_at"),
                    int(ad.get("priority", 0)),
                    1 if ad.get("enabled", True) else 0,
                    ad.get("expires_at"),
                    ad.get("last_used_at"),
                    now,
                ),
            )

    def list_ads(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM ads WHERE enabled = 1"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM ads").fetchall()
            return [dict(r) for r in rows]

    def mark_ad_used(self, ad_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE ads SET last_used_at = ? WHERE id = ?",
                (_utc_now(), ad_id),
            )

    def get_token(self, key: str = "access_token") -> tuple[str, str] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM token_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            return row["value"], row["expires_at"]

    def set_token(self, value: str, expires_at: str, key: str = "access_token") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO token_cache (key, value, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at
                """,
                (key, value, expires_at),
            )

    def list_ai_models(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            owner_user_id = self.owner_user_id
            clauses = ["owner_user_id IN ('', ?)"] if owner_user_id else ["owner_user_id = ''"]
            params: list[Any] = [owner_user_id] if owner_user_id else []
            if enabled_only:
                clauses.append("enabled = 1")
            sql = (
                "SELECT * FROM ai_models WHERE "
                + " AND ".join(clauses)
                + " ORDER BY owner_user_id, created_at, name"
            )
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_ai_model(self, model_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            owner_user_id = self.owner_user_id
            owner_clause = (
                "owner_user_id IN ('', ?)" if owner_user_id else "owner_user_id = ''"
            )
            params: tuple[Any, ...] = (
                (model_id, owner_user_id) if owner_user_id else (model_id,)
            )
            row = conn.execute(
                f"SELECT * FROM ai_models WHERE id = ? AND {owner_clause}",
                params,
            ).fetchone()
            return dict(row) if row else None

    def upsert_ai_model(self, model: dict[str, Any]) -> None:
        now = _utc_now()
        provider_type = str(
            model.get("provider_type") or "openai_compatible"
        )
        with self.connect() as conn:
            owner_user_id = self.owner_user_id
            existing = conn.execute(
                "SELECT owner_user_id "
                "FROM ai_models WHERE id = ?",
                (str(model["id"]),),
            ).fetchone()
            if existing and str(existing["owner_user_id"] or "") != owner_user_id:
                raise ValueError("该模型不属于当前登录账号")
            # New local credentials are never accepted from a caller. The
            # conflict clause below atomically preserves only the database's
            # current local-to-local legacy value until verified cleanup.
            api_key_encrypted = (
                ""
                if provider_type == "local_openai_compatible"
                else str(model.get("api_key_encrypted") or "")
            )
            conn.execute(
                """
                INSERT INTO ai_models (
                    id, owner_user_id, name, provider_type, api_base, model,
                    api_key_encrypted, local_agent_id, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    provider_type=excluded.provider_type,
                    api_base=excluded.api_base,
                    model=excluded.model,
                    api_key_encrypted=CASE
                        WHEN excluded.provider_type = 'local_openai_compatible'
                         AND ai_models.provider_type = 'local_openai_compatible'
                        THEN ai_models.api_key_encrypted
                        ELSE excluded.api_key_encrypted
                    END,
                    local_agent_id=excluded.local_agent_id,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    model["id"],
                    owner_user_id,
                    model["name"],
                    provider_type,
                    model.get("api_base"),
                    model["model"],
                    api_key_encrypted,
                    str(model.get("local_agent_id") or "") or None,
                    1 if model.get("enabled", True) else 0,
                    model.get("created_at") or now,
                    now,
                ),
            )

    def delete_ai_model(self, model_id: str) -> None:
        with self.connect() as conn:
            owner_user_id = self.owner_user_id
            model = conn.execute(
                "SELECT owner_user_id FROM ai_models WHERE id = ?",
                (model_id,),
            ).fetchone()
            if not model or str(model["owner_user_id"] or "") != owner_user_id:
                raise ValueError("该模型不属于当前登录账号")
            account_sql = "SELECT name FROM official_accounts WHERE model_id = ?"
            account_params: list[Any] = [model_id]
            if owner_user_id:
                account_sql += " AND owner_user_id = ?"
                account_params.append(owner_user_id)
            account_sql += " LIMIT 1"
            used = conn.execute(
                account_sql,
                account_params,
            ).fetchone()
            if used:
                raise ValueError(f"该模型正被公众号“{used['name']}”使用，请先修改公众号绑定")
            conn.execute(
                "DELETE FROM ai_models WHERE id = ? AND owner_user_id = ?",
                (model_id, owner_user_id),
            )

    def clear_local_model_credential(self, model_id: str) -> bool:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return False
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE ai_models
                SET api_key_encrypted = '', updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                  AND provider_type = 'local_openai_compatible'
                """,
                (_utc_now(), str(model_id), owner_user_id),
            )
        return updated.rowcount == 1

    def clear_local_model_credentials_for_agent(self, agent_id: str) -> int:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return 0
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE ai_models
                SET api_key_encrypted = '', updated_at = ?
                WHERE owner_user_id = ? AND local_agent_id = ?
                  AND provider_type = 'local_openai_compatible'
                """,
                (_utc_now(), owner_user_id, str(agent_id)),
            )
        return int(updated.rowcount)

    # ------------------------------------------------------------------
    # Local model companions and one-time pairing
    # ------------------------------------------------------------------
    def create_local_agent_pairing(self, pairing: dict[str, Any]) -> bool:
        now = _utc_now()
        retention_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(timespec="microseconds")
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM local_agent_pairings
                WHERE updated_at < ?
                  AND (status IN ('consumed', 'locked', 'expired') OR expires_at < ?)
                """,
                (retention_cutoff, retention_cutoff),
            )
            active = conn.execute(
                """
                SELECT COUNT(*) AS count FROM local_agent_pairings
                WHERE status IN ('pending', 'approved') AND expires_at > ?
                """,
                (now,),
            ).fetchone()
            if int((active or {"count": 0})["count"]) >= 100:
                return False
            conn.execute(
                """
                INSERT INTO local_agent_pairings (
                    id, device_code_hash, user_code_salt, user_code_hash,
                    hash_iterations, device_name, owner_user_id, agent_id,
                    status, failed_attempts, expires_at, approved_at,
                    consumed_at, last_polled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pending', 0, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    str(pairing["id"]),
                    str(pairing["device_code_hash"]),
                    str(pairing["user_code_salt"]),
                    str(pairing["user_code_hash"]),
                    int(pairing["hash_iterations"]),
                    str(pairing["device_name"]),
                    str(pairing["expires_at"]),
                    now,
                    now,
                ),
            )
        return True

    def get_local_agent_pairing(
        self,
        pairing_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM local_agent_pairings WHERE id = ?",
                (str(pairing_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_local_agent_pairing_by_device_hash(
        self,
        device_code_hash: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM local_agent_pairings WHERE device_code_hash = ?",
                (str(device_code_hash),),
            ).fetchone()
        return dict(row) if row else None

    def record_local_agent_pairing_failure(
        self,
        pairing_id: str,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE local_agent_pairings
                SET failed_attempts = failed_attempts + 1,
                    status = CASE
                        WHEN failed_attempts + 1 >= 5 THEN 'locked'
                        ELSE status
                    END,
                    updated_at = ?
                WHERE id = ? AND status = 'pending' AND expires_at > ?
                """,
                (now, str(pairing_id), now),
            )
            row = conn.execute(
                "SELECT * FROM local_agent_pairings WHERE id = ?",
                (str(pairing_id),),
            ).fetchone()
        return dict(row) if row else None

    def approve_local_agent_pairing(
        self,
        pairing_id: str,
    ) -> dict[str, Any] | None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise ValueError("批准本机设备必须绑定登录用户")
        now = _utc_now()
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE local_agent_pairings
                SET status = 'approved', owner_user_id = ?, approved_at = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                  AND failed_attempts < 5 AND expires_at > ?
                """,
                (owner_user_id, now, now, str(pairing_id), now),
            )
            if updated.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM local_agent_pairings WHERE id = ?",
                (str(pairing_id),),
            ).fetchone()
        return dict(row) if row else None

    def exchange_local_agent_pairing(
        self,
        *,
        device_code_hash: str,
        agent_id: str,
        token_hash: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM local_agent_pairings WHERE device_code_hash = ?",
                (str(device_code_hash),),
            ).fetchone()
            if row is None:
                return {"state": "invalid"}
            pairing = dict(row)
            if str(pairing.get("expires_at") or "") <= now:
                conn.execute(
                    """
                    UPDATE local_agent_pairings
                    SET status = 'expired', updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'approved')
                    """,
                    (now, str(pairing["id"])),
                )
                return {"state": "expired"}
            state = str(pairing.get("status") or "")
            if state == "pending":
                last_polled_at = str(pairing.get("last_polled_at") or "")
                min_poll_at = (
                    datetime.now(timezone.utc) - timedelta(seconds=3)
                ).isoformat(timespec="microseconds")
                if last_polled_at and last_polled_at > min_poll_at:
                    return {"state": "rate_limited"}
                conn.execute(
                    """
                    UPDATE local_agent_pairings
                    SET last_polled_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, now, str(pairing["id"])),
                )
            if state != "approved":
                return {"state": state or "invalid"}
            owner_user_id = str(pairing.get("owner_user_id") or "")
            if not owner_user_id:
                return {"state": "invalid"}
            consumed = conn.execute(
                """
                UPDATE local_agent_pairings
                SET status = 'consumed', agent_id = ?, consumed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'approved' AND consumed_at IS NULL
                """,
                (str(agent_id), now, now, str(pairing["id"])),
            )
            if consumed.rowcount != 1:
                return {"state": "consumed"}
            conn.execute(
                """
                INSERT INTO local_model_agents (
                    id, owner_user_id, name, token_hash, last_seen_at,
                    cockpit_status, last_error_code, revoked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 'unknown', '', NULL, ?, ?)
                """,
                (
                    str(agent_id),
                    owner_user_id,
                    str(pairing.get("device_name") or "Windows 本机助手"),
                    str(token_hash),
                    now,
                    now,
                ),
            )
            agent = conn.execute(
                "SELECT * FROM local_model_agents WHERE id = ?",
                (str(agent_id),),
            ).fetchone()
        return {"state": "consumed", "agent": dict(agent) if agent else {}}

    def find_local_model_agent_by_token_hash(
        self,
        value: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.*
                FROM local_model_agents a
                JOIN users u ON u.id = a.owner_user_id
                WHERE a.token_hash = ? AND a.revoked_at IS NULL AND u.enabled = 1
                """,
                (str(value),),
            ).fetchone()
        return dict(row) if row else None

    def list_local_model_agents(self) -> list[dict[str, Any]]:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM local_model_agents
                WHERE owner_user_id = ?
                ORDER BY revoked_at IS NOT NULL, created_at DESC
                """,
                (owner_user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_local_model_agent(self, agent_id: str) -> dict[str, Any] | None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM local_model_agents
                WHERE id = ? AND owner_user_id = ?
                """,
                (str(agent_id), owner_user_id),
            ).fetchone()
        return dict(row) if row else None

    def rename_local_model_agent(
        self,
        agent_id: str,
        name: str,
    ) -> dict[str, Any] | None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return None
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE local_model_agents SET name = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND revoked_at IS NULL
                """,
                (str(name), _utc_now(), str(agent_id), owner_user_id),
            )
        if updated.rowcount != 1:
            return None
        return self.get_local_model_agent(agent_id)

    def heartbeat_local_model_agent(
        self,
        agent_id: str,
        *,
        cockpit_status: str,
        last_error_code: str,
    ) -> bool:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return False
        now = _utc_now()
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE local_model_agents
                SET last_seen_at = ?, cockpit_status = ?, last_error_code = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND revoked_at IS NULL
                """,
                (
                    now,
                    str(cockpit_status or "unknown")[:40],
                    str(last_error_code or "")[:100],
                    now,
                    str(agent_id),
                    owner_user_id,
                ),
            )
        return updated.rowcount == 1

    def revoke_local_model_agent(self, agent_id: str) -> bool:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return False
        now = _utc_now()
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE local_model_agents
                SET revoked_at = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND revoked_at IS NULL
                """,
                (now, now, str(agent_id), owner_user_id),
            )
            if updated.rowcount != 1:
                return False
            conn.execute(
                """
                UPDATE ai_models SET local_agent_id = NULL, updated_at = ?
                WHERE owner_user_id = ? AND local_agent_id = ?
                """,
                (now, owner_user_id, str(agent_id)),
            )
            conn.execute(
                """
                UPDATE local_model_requests
                SET status = 'failed', error = '本机 Companion 已撤销',
                    result_error_code = 'agent.revoked',
                    completed_at = ?, updated_at = ?
                WHERE owner_user_id = ? AND agent_id = ?
                  AND status IN ('pending', 'running')
                """,
                (now, now, owner_user_id, str(agent_id)),
            )
        return True

    def create_local_model_request(
        self,
        model_id: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float = 620.0,
    ) -> str:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            raise ValueError("本地模型请求必须绑定登录用户")
        model = self.get_ai_model(model_id)
        if (
            not model
            or str(model.get("provider_type") or "")
            != "local_openai_compatible"
        ):
            raise ValueError("本地模型不存在或不属于当前登录账号")
        agent_id = str(model.get("local_agent_id") or "").strip()
        if agent_id:
            agent = self.get_local_model_agent(agent_id)
            if not agent or str(agent.get("revoked_at") or "").strip():
                raise ValueError("绑定的本机 Companion 不存在或已撤销")
        if not isinstance(payload, dict):
            raise ValueError("本地模型任务载荷无效")
        allowed_keys = {"model", "messages", "temperature", "max_tokens", "stream"}
        if set(payload).difference(allowed_keys):
            raise ValueError("本地模型任务包含不允许的参数")
        if payload.get("stream") not in {None, False}:
            raise ValueError("本机 Companion 第一版不支持流式任务")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("本地模型任务 messages 必须是数组")
        clean_messages: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("本地模型任务消息格式无效")
            role = str(message.get("role") or "")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(
                content,
                str,
            ):
                raise ValueError("本地模型任务消息格式无效")
            clean_messages.append({"role": role, "content": content})
        clean_payload: dict[str, Any] = {
            "model": str(model.get("model") or "").strip(),
            "messages": clean_messages,
        }
        if "temperature" in payload:
            clean_payload["temperature"] = payload["temperature"]
        if "max_tokens" in payload:
            clean_payload["max_tokens"] = payload["max_tokens"]
        request_json = json.dumps(clean_payload, ensure_ascii=False)
        if len(request_json.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("本地模型任务超过 16 MiB 限制")
        request_id = uuid.uuid4().hex
        now = _utc_now()
        deadline_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(1.0, float(timeout_seconds)))
        ).isoformat(timespec="microseconds")
        expired_before = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(timespec="microseconds")
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM local_model_requests
                WHERE owner_user_id = ? AND updated_at < ?
                """,
                (owner_user_id, expired_before),
            )
            conn.execute(
                """
                INSERT INTO local_model_requests (
                    id, owner_user_id, model_id, status, request_json,
                    response_text, error, claimed_by, agent_id, operation,
                    attempt_id, nonce, deadline_at, lease_until, completed_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, '', '', '', ?,
                          'chat.completions', '', '', ?, NULL, NULL, ?, ?)
                """,
                (
                    request_id,
                    owner_user_id,
                    model_id,
                    request_json,
                    agent_id or None,
                    deadline_at,
                    now,
                    now,
                ),
            )
        return request_id

    def claim_local_model_request(
        self,
        claimed_by: str,
    ) -> dict[str, Any] | None:
        owner_user_id = self.owner_user_id
        if not owner_user_id or not str(claimed_by or "").strip():
            return None
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM local_model_requests
                WHERE owner_user_id = ? AND status = 'pending'
                  AND agent_id IS NULL
                ORDER BY created_at ASC LIMIT 1
                """,
                (owner_user_id,),
            ).fetchone()
            if row is None:
                return None
            request_id = str(row["id"])
            updated = conn.execute(
                """
                UPDATE local_model_requests
                SET status = 'running', claimed_by = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND status = 'pending'
                  AND agent_id IS NULL
                """,
                (str(claimed_by), now, request_id, owner_user_id),
            )
            if updated.rowcount != 1:
                return None
            claimed = conn.execute(
                """
                SELECT * FROM local_model_requests
                WHERE id = ? AND owner_user_id = ?
                """,
                (request_id, owner_user_id),
            ).fetchone()
        if claimed is None:
            return None
        result = dict(claimed)
        result["request"] = _loads_json(result.get("request_json"), {})
        return result

    def complete_local_model_request(
        self,
        request_id: str,
        claimed_by: str,
        *,
        response_text: str = "",
        error: str = "",
    ) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return
        status = "failed" if str(error or "").strip() else "completed"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE local_model_requests
                SET status = ?, response_text = ?, error = ?, result_error_code = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND claimed_by = ?
                  AND agent_id IS NULL
                """,
                (
                    status,
                    str(response_text or ""),
                    str(error or "")[:2000],
                    "browser.failed" if status == "failed" else "",
                    _utc_now(),
                    _utc_now(),
                    str(request_id),
                    owner_user_id,
                    str(claimed_by),
                ),
            )

    def fail_local_model_request(
        self,
        request_id: str,
        error: str,
        *,
        error_code: str = "browser.timeout",
    ) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE local_model_requests
                SET status = 'failed', error = ?, result_error_code = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ?
                  AND status IN ('pending', 'running')
                """,
                (
                    str(error or "")[:2000],
                    str(error_code or "browser.timeout")[:100],
                    _utc_now(),
                    _utc_now(),
                    str(request_id),
                    owner_user_id,
                ),
            )

    def get_local_model_request(self, request_id: str) -> dict[str, Any] | None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM local_model_requests
                WHERE id = ? AND owner_user_id = ?
                """,
                (str(request_id), owner_user_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_local_model_request(self, request_id: str) -> None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM local_model_requests WHERE id = ? AND owner_user_id = ?",
                (str(request_id), owner_user_id),
            )

    def claim_local_agent_request(
        self,
        agent_id: str,
        *,
        lease_seconds: float = 60.0,
    ) -> dict[str, Any] | None:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return None
        agent = self.get_local_model_agent(agent_id)
        if not agent or str(agent.get("revoked_at") or "").strip():
            return None
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="microseconds")
        lease_until = (
            now_dt + timedelta(seconds=max(1.0, float(lease_seconds)))
        ).isoformat(timespec="microseconds")
        attempt_id = uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE local_model_requests
                SET status = 'failed', error = '本地模型任务已超过截止时间',
                    result_error_code = 'agent.deadline_exceeded',
                    completed_at = ?, updated_at = ?
                WHERE owner_user_id = ? AND agent_id = ?
                  AND status IN ('pending', 'running')
                  AND deadline_at IS NOT NULL AND deadline_at <= ?
                """,
                (now, now, owner_user_id, str(agent_id), now),
            )
            row = conn.execute(
                """
                SELECT id FROM local_model_requests
                WHERE owner_user_id = ? AND agent_id = ?
                  AND operation = 'chat.completions'
                  AND deadline_at > ?
                  AND (
                      status = 'pending'
                      OR (status = 'running' AND lease_until <= ?)
                  )
                ORDER BY created_at ASC LIMIT 1
                """,
                (owner_user_id, str(agent_id), now, now),
            ).fetchone()
            if row is None:
                return None
            request_id = str(row["id"])
            updated = conn.execute(
                """
                UPDATE local_model_requests
                SET status = 'running', claimed_by = ?, attempt_id = ?,
                    nonce = ?, lease_until = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND agent_id = ?
                  AND deadline_at > ?
                  AND (
                      status = 'pending'
                      OR (status = 'running' AND lease_until <= ?)
                  )
                """,
                (
                    f"agent:{agent_id}",
                    attempt_id,
                    nonce,
                    lease_until,
                    now,
                    request_id,
                    owner_user_id,
                    str(agent_id),
                    now,
                    now,
                ),
            )
            if updated.rowcount != 1:
                return None
            claimed = conn.execute(
                """
                SELECT * FROM local_model_requests
                WHERE id = ? AND owner_user_id = ? AND agent_id = ?
                  AND attempt_id = ? AND nonce = ?
                """,
                (
                    request_id,
                    owner_user_id,
                    str(agent_id),
                    attempt_id,
                    nonce,
                ),
            ).fetchone()
        if claimed is None:
            return None
        item = dict(claimed)
        return {
            "request_id": str(item["id"]),
            "attempt_id": str(item["attempt_id"]),
            "nonce": str(item["nonce"]),
            "operation": "chat.completions",
            "deadline_at": str(item.get("deadline_at") or ""),
            "lease_until": str(item.get("lease_until") or ""),
            "payload": _loads_json(item.get("request_json"), {}),
        }

    def renew_local_agent_request(
        self,
        request_id: str,
        agent_id: str,
        attempt_id: str,
        nonce: str,
        *,
        lease_seconds: float = 60.0,
    ) -> bool:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return False
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="microseconds")
        lease_until = (
            now_dt + timedelta(seconds=max(1.0, float(lease_seconds)))
        ).isoformat(timespec="microseconds")
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE local_model_requests
                SET lease_until = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND agent_id = ?
                  AND attempt_id = ? AND nonce = ? AND status = 'running'
                  AND lease_until > ? AND deadline_at > ?
                """,
                (
                    lease_until,
                    now,
                    str(request_id),
                    owner_user_id,
                    str(agent_id),
                    str(attempt_id),
                    str(nonce),
                    now,
                    now,
                ),
            )
            if updated.rowcount == 1:
                conn.execute(
                    """
                    UPDATE local_model_agents
                    SET last_seen_at = ?, updated_at = ?
                    WHERE id = ? AND owner_user_id = ? AND revoked_at IS NULL
                    """,
                    (now, now, str(agent_id), owner_user_id),
                )
        return updated.rowcount == 1

    def complete_local_agent_request(
        self,
        request_id: str,
        agent_id: str,
        attempt_id: str,
        nonce: str,
        *,
        status: str,
        response_text: str = "",
        error: str = "",
        error_code: str = "",
    ) -> str:
        owner_user_id = self.owner_user_id
        if not owner_user_id:
            return "missing"
        if status not in {"completed", "failed"}:
            raise ValueError("本机任务结果状态无效")
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM local_model_requests
                WHERE id = ? AND owner_user_id = ? AND agent_id = ?
                """,
                (str(request_id), owner_user_id, str(agent_id)),
            ).fetchone()
            if row is None:
                return "missing"
            item = dict(row)
            same_attempt = (
                str(item.get("attempt_id") or "") == str(attempt_id)
                and str(item.get("nonce") or "") == str(nonce)
            )
            if str(item.get("status") or "") in {"completed", "failed"}:
                return "duplicate" if same_attempt else "stale"
            if (
                not same_attempt
                or str(item.get("status") or "") != "running"
                or str(item.get("lease_until") or "") <= now
                or str(item.get("deadline_at") or "") <= now
            ):
                return "stale"
            updated = conn.execute(
                """
                UPDATE local_model_requests
                SET status = ?, response_text = ?, error = ?, result_error_code = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND owner_user_id = ? AND agent_id = ?
                  AND attempt_id = ? AND nonce = ? AND status = 'running'
                  AND lease_until > ? AND deadline_at > ?
                """,
                (
                    status,
                    str(response_text or ""),
                    str(error or "")[:2000],
                    str(error_code or "")[:100],
                    now,
                    now,
                    str(request_id),
                    owner_user_id,
                    str(agent_id),
                    str(attempt_id),
                    str(nonce),
                    now,
                    now,
                ),
            )
        return "accepted" if updated.rowcount == 1 else "stale"

    def list_prompt_templates(
        self,
        *,
        purpose: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            if purpose:
                clauses.append("purpose = ?")
                params.append(purpose)
            if enabled_only:
                clauses.append("enabled = 1")
            sql = "SELECT * FROM prompt_templates"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_prompt_template(self, template_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM prompt_templates WHERE id = ?"
            params: list[Any] = [template_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def upsert_prompt_template(self, template: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            self._assert_write_owner(
                conn, "prompt_templates", str(template["id"])
            )
            conn.execute(
                """
                INSERT INTO prompt_templates (
                    id, owner_user_id, name, purpose, content, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    purpose=excluded.purpose,
                    content=excluded.content,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    template["id"],
                    self.owner_user_id
                    or str(template.get("owner_user_id") or "").strip(),
                    template["name"],
                    template.get("purpose", "image"),
                    template["content"],
                    1 if template.get("enabled", True) else 0,
                    template.get("created_at") or now,
                    now,
                ),
            )

    def delete_prompt_template(self, template_id: str) -> None:
        with self.connect() as conn:
            sql = "DELETE FROM prompt_templates WHERE id = ?"
            params: list[Any] = [template_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            conn.execute(sql, params)

    def list_creation_plans(
        self, *, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM creation_plans"
            clauses: list[str] = []
            params: list[Any] = []
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            if enabled_only:
                clauses.append("enabled = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_creation_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM creation_plans WHERE id = ?"
            params: list[Any] = [plan_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def upsert_creation_plan(self, plan: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            self._assert_write_owner(conn, "creation_plans", str(plan["id"]))
            conn.execute(
                """
                INSERT INTO creation_plans (
                    id, owner_user_id, name, description, article_prompt_template_id,
                    image_prompt_template_id, editorial_review_profile_id,
                    layout_json, image_settings_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    article_prompt_template_id=excluded.article_prompt_template_id,
                    image_prompt_template_id=excluded.image_prompt_template_id,
                    editorial_review_profile_id=excluded.editorial_review_profile_id,
                    layout_json=excluded.layout_json,
                    image_settings_json=excluded.image_settings_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    plan["id"],
                    self.owner_user_id
                    or str(plan.get("owner_user_id") or "").strip(),
                    plan["name"],
                    plan.get("description") or "",
                    plan.get("article_prompt_template_id") or None,
                    plan.get("image_prompt_template_id") or None,
                    plan.get("editorial_review_profile_id") or None,
                    json.dumps(
                        plan.get("layout")
                        if isinstance(plan.get("layout"), dict)
                        else _loads_json(plan.get("layout_json"), {}),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        plan.get("image_settings")
                        if isinstance(plan.get("image_settings"), dict)
                        else _loads_json(plan.get("image_settings_json"), {}),
                        ensure_ascii=False,
                    ),
                    1 if plan.get("enabled", True) else 0,
                    plan.get("created_at") or now,
                    now,
                ),
            )

    def delete_creation_plan(self, plan_id: str) -> None:
        with self.connect() as conn:
            clear_sql = (
                """
                UPDATE official_accounts
                SET default_creation_plan_id = NULL, updated_at = ?
                WHERE default_creation_plan_id = ?
                """
            )
            clear_params: list[Any] = [_utc_now(), plan_id]
            if self.owner_user_id:
                clear_sql += " AND owner_user_id = ?"
                clear_params.append(self.owner_user_id)
            conn.execute(clear_sql, clear_params)
            sql = "DELETE FROM creation_plans WHERE id = ?"
            params: list[Any] = [plan_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            conn.execute(sql, params)

    def list_account_creation_plan_defaults(
        self, *, plan_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if plan_id is not None:
                clauses.append(
                    "COALESCE(a.default_creation_plan_id, d.creation_plan_id) = ?"
                )
                params.append(plan_id)
            if self.owner_user_id:
                clauses.append("a.owner_user_id = ?")
                params.append(self.owner_user_id)
            clauses.append(
                "COALESCE(a.default_creation_plan_id, d.creation_plan_id) IS NOT NULL"
            )
            sql = """
                SELECT a.id AS account_id,
                       COALESCE(
                           a.default_creation_plan_id, d.creation_plan_id
                       ) AS creation_plan_id,
                       COALESCE(d.created_at, a.created_at) AS created_at,
                       COALESCE(d.updated_at, a.updated_at) AS updated_at
                FROM official_accounts a
                LEFT JOIN account_creation_plan_defaults d
                  ON d.account_id = a.id
            """
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def get_account_creation_plan_default(
        self, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = """
                SELECT a.id AS account_id,
                       COALESCE(
                           a.default_creation_plan_id, d.creation_plan_id
                       ) AS creation_plan_id,
                       COALESCE(d.created_at, a.created_at) AS created_at,
                       COALESCE(d.updated_at, a.updated_at) AS updated_at
                FROM official_accounts a
                LEFT JOIN account_creation_plan_defaults d
                  ON d.account_id = a.id
                WHERE a.id = ?
                  AND COALESCE(
                      a.default_creation_plan_id, d.creation_plan_id
                  ) IS NOT NULL
            """
            params: list[Any] = [account_id]
            if self.owner_user_id:
                sql += " AND a.owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def set_account_creation_plan_default(
        self, account_id: str, creation_plan_id: str
    ) -> None:
        if self.owner_user_id:
            if not self.get_official_account(account_id):
                raise ValueError("公众号不存在")
            if not self.get_creation_plan(creation_plan_id):
                raise ValueError("创作方案不存在")
        now = _utc_now()
        with self.connect() as conn:
            account_sql = """
                UPDATE official_accounts
                SET default_creation_plan_id = ?, updated_at = ?
                WHERE id = ?
            """
            account_params: list[Any] = [creation_plan_id, now, account_id]
            if self.owner_user_id:
                account_sql += " AND owner_user_id = ?"
                account_params.append(self.owner_user_id)
            cursor = conn.execute(account_sql, account_params)
            if not cursor.rowcount:
                raise ValueError("公众号不存在")
            conn.execute(
                """
                INSERT INTO account_creation_plan_defaults (
                    account_id, creation_plan_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    creation_plan_id=excluded.creation_plan_id,
                    updated_at=excluded.updated_at
                """,
                (account_id, creation_plan_id, now, now),
            )

    def list_creation_plan_account_templates(
        self,
        *,
        creation_plan_id: str | None = None,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if creation_plan_id is not None:
            clauses.append("t.creation_plan_id = ?")
            params.append(creation_plan_id)
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        sql = """
            SELECT t.*
            FROM creation_plan_account_templates t
            JOIN official_accounts a ON a.id = t.account_id
            JOIN creation_plans p ON p.id = t.creation_plan_id
        """
        if self.owner_user_id:
            clauses.append("a.owner_user_id = ?")
            params.append(self.owner_user_id)
            clauses.append("p.owner_user_id = ?")
            params.append(self.owner_user_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, account_id"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_creation_plan_account_template(
        self, creation_plan_id: str, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = """
                SELECT t.*
                FROM creation_plan_account_templates t
                JOIN official_accounts a ON a.id = t.account_id
                JOIN creation_plans p ON p.id = t.creation_plan_id
                WHERE t.creation_plan_id = ? AND t.account_id = ?
            """
            params: list[Any] = [creation_plan_id, account_id]
            if self.owner_user_id:
                sql += " AND a.owner_user_id = ? AND p.owner_user_id = ?"
                params.extend((self.owner_user_id, self.owner_user_id))
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def upsert_creation_plan_account_template(
        self, binding: dict[str, Any]
    ) -> None:
        if self.owner_user_id:
            if not self.get_official_account(str(binding["account_id"])):
                raise ValueError("公众号不存在")
            if not self.get_creation_plan(str(binding["creation_plan_id"])):
                raise ValueError("创作方案不存在")
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO creation_plan_account_templates (
                    creation_plan_id, account_id, source_app_id, enabled, capture_title,
                    placeholder, selected_media_id, selected_article_index,
                    selected_title, snapshot_html, snapshot_sha256,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(creation_plan_id, account_id) DO UPDATE SET
                    source_app_id=excluded.source_app_id,
                    enabled=excluded.enabled,
                    capture_title=excluded.capture_title,
                    placeholder=excluded.placeholder,
                    selected_media_id=excluded.selected_media_id,
                    selected_article_index=excluded.selected_article_index,
                    selected_title=excluded.selected_title,
                    snapshot_html=excluded.snapshot_html,
                    snapshot_sha256=excluded.snapshot_sha256,
                    updated_at=excluded.updated_at
                """,
                (
                    binding["creation_plan_id"],
                    binding["account_id"],
                    binding.get("source_app_id") or "",
                    1 if binding.get("enabled") else 0,
                    binding.get("capture_title") or None,
                    binding.get("placeholder") or None,
                    binding.get("selected_media_id") or None,
                    int(binding.get("selected_article_index") or 0),
                    binding.get("selected_title") or None,
                    binding.get("snapshot_html") or None,
                    binding.get("snapshot_sha256") or None,
                    binding.get("created_at") or now,
                    now,
                ),
            )

    def delete_creation_plan_account_template(
        self, creation_plan_id: str, account_id: str
    ) -> None:
        if self.owner_user_id and not self.get_creation_plan_account_template(
            creation_plan_id, account_id
        ):
            return
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM creation_plan_account_templates
                WHERE creation_plan_id = ? AND account_id = ?
                """,
                (creation_plan_id, account_id),
            )

    def list_official_accounts(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM official_accounts"
            clauses: list[str] = []
            params: list[Any] = []
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            if enabled_only:
                clauses.append("enabled = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_official_account(self, account_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM official_accounts WHERE id = ?"
            params: list[Any] = [account_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def upsert_official_account(self, account: dict[str, Any]) -> None:
        now = _utc_now()
        previous = self.get_official_account(str(account["id"]))
        layout = account.get("layout")
        if layout is None:
            try:
                layout = json.loads(str(account.get("layout_json") or "{}"))
            except json.JSONDecodeError:
                layout = {}
        review_priority = int(
            account.get(
                "review_priority",
                (previous or {}).get("review_priority") or 0,
            )
            or 0
        )
        with self.connect() as conn:
            self._assert_write_owner(
                conn, "official_accounts", str(account["id"])
            )
            conn.execute(
                """
                INSERT INTO official_accounts (
                    id, owner_user_id, name, app_id, app_secret_encrypted, model_id, layout_json,
                    review_priority, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    app_id=excluded.app_id,
                    app_secret_encrypted=excluded.app_secret_encrypted,
                    model_id=excluded.model_id,
                    layout_json=excluded.layout_json,
                    review_priority=excluded.review_priority,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    account["id"],
                    self.owner_user_id
                    or str(account.get("owner_user_id") or "").strip(),
                    account["name"],
                    account["app_id"],
                    account["app_secret_encrypted"],
                    account["model_id"],
                    json.dumps(layout or {}, ensure_ascii=False),
                    review_priority,
                    1 if account.get("enabled", True) else 0,
                    account.get("created_at") or now,
                    now,
                ),
            )
        connection_changed = not previous or any(
            (
                bool((previous or {}).get("enabled"))
                != bool(account.get("enabled", (previous or {}).get("enabled")))
            )
            if key == "enabled"
            else (
                str((previous or {}).get(key) or "")
                != str(account.get(key, (previous or {}).get(key)) or "")
            )
            for key in ("app_id", "app_secret_encrypted", "enabled")
        )
        if connection_changed:
            self.invalidate_wechat_connection_health(str(account["id"]))

    def delete_official_account(self, account_id: str) -> None:
        if self.owner_user_id and not self.get_official_account(account_id):
            return
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM wechat_connection_health WHERE account_id = ?",
                (account_id,),
            )
            sql = "DELETE FROM official_accounts WHERE id = ?"
            params: list[Any] = [account_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            conn.execute(sql, params)

    def list_editorial_review_profiles(
        self, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM editorial_review_profiles"
            clauses: list[str] = []
            params: list[Any] = []
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            if enabled_only:
                clauses.append("enabled = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_editorial_review_profile(
        self, profile_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM editorial_review_profiles WHERE id = ?"
            params: list[Any] = [profile_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def upsert_editorial_review_profile(self, profile: dict[str, Any]) -> None:
        now = _utc_now()
        config = profile.get("config")
        if config is None:
            config = _loads_json(profile.get("config_json"), {})
        with self.connect() as conn:
            self._assert_write_owner(
                conn, "editorial_review_profiles", str(profile["id"])
            )
            conn.execute(
                """
                INSERT INTO editorial_review_profiles (
                    id, owner_user_id, name, description, config_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    config_json=excluded.config_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    profile["id"],
                    self.owner_user_id
                    or str(profile.get("owner_user_id") or "").strip(),
                    profile["name"],
                    profile.get("description") or "",
                    json.dumps(config or {}, ensure_ascii=False),
                    1 if profile.get("enabled", True) else 0,
                    profile.get("created_at") or now,
                    now,
                ),
            )

    def delete_editorial_review_profile(self, profile_id: str) -> None:
        if self.owner_user_id and not self.get_editorial_review_profile(profile_id):
            return
        with self.connect() as conn:
            account_sql = """
                UPDATE official_accounts
                SET default_editorial_review_profile_id = NULL,
                    editorial_review_config_json = '{}', updated_at = ?
                WHERE default_editorial_review_profile_id = ?
            """
            account_params: list[Any] = [_utc_now(), profile_id]
            if self.owner_user_id:
                account_sql += " AND owner_user_id = ?"
                account_params.append(self.owner_user_id)
            conn.execute(account_sql, account_params)
            conn.execute(
                """
                UPDATE account_editorial_review_defaults
                SET profile_id = NULL, config_json = '{}', updated_at = ?
                WHERE profile_id = ?
                """,
                (_utc_now(), profile_id),
            )
            conn.execute(
                "DELETE FROM editorial_review_profiles WHERE id = ?",
                (profile_id,),
            )

    def get_account_editorial_review_default(
        self, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = """
                SELECT a.id AS account_id,
                       COALESCE(
                           a.default_editorial_review_profile_id, d.profile_id
                       ) AS profile_id,
                       COALESCE(
                           NULLIF(a.editorial_review_config_json, ''),
                           d.config_json,
                           '{}'
                       ) AS config_json,
                       COALESCE(d.created_at, a.created_at) AS created_at,
                       COALESCE(d.updated_at, a.updated_at) AS updated_at
                FROM official_accounts a
                LEFT JOIN account_editorial_review_defaults d
                  ON d.account_id = a.id
                WHERE a.id = ?
                  AND (
                      a.default_editorial_review_profile_id IS NOT NULL
                      OR d.account_id IS NOT NULL
                      OR COALESCE(a.editorial_review_config_json, '{}') <> '{}'
                  )
            """
            params: list[Any] = [account_id]
            if self.owner_user_id:
                sql += " AND a.owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def set_account_editorial_review_default(
        self,
        account_id: str,
        *,
        profile_id: str | None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if self.owner_user_id:
            if not self.get_official_account(account_id):
                raise ValueError("公众号不存在")
            if profile_id and not self.get_editorial_review_profile(profile_id):
                raise ValueError("评审方案不存在")
        now = _utc_now()
        config_json = json.dumps(config or {}, ensure_ascii=False)
        with self.connect() as conn:
            account_sql = """
                UPDATE official_accounts
                SET default_editorial_review_profile_id = ?,
                    editorial_review_config_json = ?, updated_at = ?
                WHERE id = ?
            """
            account_params: list[Any] = [
                profile_id or None,
                config_json,
                now,
                account_id,
            ]
            if self.owner_user_id:
                account_sql += " AND owner_user_id = ?"
                account_params.append(self.owner_user_id)
            cursor = conn.execute(account_sql, account_params)
            if not cursor.rowcount:
                raise ValueError("公众号不存在")
            conn.execute(
                """
                INSERT INTO account_editorial_review_defaults (
                    account_id, profile_id, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    profile_id=excluded.profile_id,
                    config_json=excluded.config_json,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    profile_id or None,
                    config_json,
                    now,
                    now,
                ),
            )

    def create_editorial_review(self, review: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO editorial_reviews (
                    id, batch_id, job_id, profile_id, profile_name,
                    model_id, model_name, status, config_json,
                    source_snapshot_json, result_json,
                    selected_issue_ids_json, rewrite_mode,
                    rewritten_snapshot_json, blocking_count, error,
                    completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review["id"],
                    review["batch_id"],
                    int(review["job_id"]),
                    review.get("profile_id"),
                    review.get("profile_name") or "",
                    review.get("model_id") or "",
                    review.get("model_name") or "",
                    review.get("status") or "running",
                    json.dumps(review.get("config") or {}, ensure_ascii=False),
                    json.dumps(
                        review.get("source_snapshot") or {}, ensure_ascii=False
                    ),
                    json.dumps(review.get("result") or {}, ensure_ascii=False),
                    json.dumps(
                        review.get("selected_issue_ids") or [], ensure_ascii=False
                    ),
                    review.get("rewrite_mode") or "",
                    json.dumps(
                        review.get("rewritten_snapshot") or {},
                        ensure_ascii=False,
                    ),
                    max(0, int(review.get("blocking_count") or 0)),
                    review.get("error") or "",
                    review.get("completed_at"),
                    review.get("created_at") or now,
                    now,
                ),
            )

    def update_editorial_review(
        self, review_id: str, **updates: Any
    ) -> None:
        allowed = {
            "status",
            "result_json",
            "selected_issue_ids_json",
            "rewrite_mode",
            "rewritten_snapshot_json",
            "blocking_count",
            "error",
            "completed_at",
        }
        serialized = {
            "result_json",
            "selected_issue_ids_json",
            "rewritten_snapshot_json",
        }
        fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            fields.append(f"{key} = ?")
            if key in serialized and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            if key == "blocking_count":
                value = max(0, int(value or 0))
            values.append(value)
        if not fields:
            return
        fields.append("revision = revision + 1")
        fields.append("updated_at = ?")
        values.append(_utc_now())
        values.append(review_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE editorial_reviews SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def update_editorial_review_result_if_unchanged(
        self,
        review_id: str,
        *,
        expected_revision: int,
        result: dict[str, Any],
        blocking_count: int,
    ) -> bool:
        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE editorial_reviews
                SET result_json = ?,
                    blocking_count = ?,
                    revision = revision + 1,
                    updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    json.dumps(result, ensure_ascii=False),
                    max(0, int(blocking_count or 0)),
                    now,
                    review_id,
                    max(0, int(expected_revision or 0)),
                ),
            )
            return int(cursor.rowcount or 0) == 1

    def get_editorial_review(self, review_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM editorial_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_editorial_reviews(
        self,
        *,
        job_id: int | None = None,
        batch_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if job_id is not None:
            clauses.append("job_id = ?")
            values.append(int(job_id))
        if batch_id:
            clauses.append("batch_id = ?")
            values.append(str(batch_id))
        sql = "SELECT * FROM editorial_reviews"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        values.append(max(1, int(limit)))
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, values).fetchall()]

    def create_editorial_review_application(
        self, application: dict[str, Any]
    ) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO editorial_review_applications (
                    id, review_id, status, rewrite_mode,
                    selected_issue_ids_json, paragraph_numbers_json,
                    instruction, source_hash, candidate_snapshot_json,
                    error, created_at, updated_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application["id"],
                    application["review_id"],
                    application.get("status") or "generating",
                    application.get("rewrite_mode") or "selected_issues",
                    json.dumps(
                        application.get("selected_issue_ids") or [],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        application.get("paragraph_numbers") or [],
                        ensure_ascii=False,
                    ),
                    application.get("instruction") or "",
                    application.get("source_hash") or "",
                    json.dumps(
                        application.get("candidate_snapshot") or {},
                        ensure_ascii=False,
                    ),
                    application.get("error") or "",
                    application.get("created_at") or now,
                    now,
                    application.get("applied_at"),
                ),
            )

    def update_editorial_review_application(
        self, application_id: str, **updates: Any
    ) -> None:
        allowed = {
            "status",
            "candidate_snapshot_json",
            "error",
            "applied_at",
        }
        fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            fields.append(f"{key} = ?")
            if key == "candidate_snapshot_json" and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(_utc_now())
        values.append(application_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE editorial_review_applications
                SET {', '.join(fields)}
                WHERE id = ?
                """,
                values,
            )

    def get_editorial_review_application(
        self, application_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM editorial_review_applications
                WHERE id = ?
                """,
                (application_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_editorial_review_applications(
        self, review_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM editorial_review_applications
                WHERE review_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (review_id, max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]

    def recover_stale_editorial_reviews(
        self, *, older_than_minutes: int = 30
    ) -> int:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(minutes=max(1, older_than_minutes))
        ).replace(microsecond=0).isoformat()
        now = _utc_now()
        with self.connect() as conn:
            review_cursor = conn.execute(
                """
                UPDATE editorial_reviews
                SET status = 'failed',
                    error = '应用重启后检测到 AI 评审已中断，请重新发起',
                    completed_at = ?,
                    updated_at = ?
                WHERE status IN ('running', 'rewriting')
                  AND updated_at < ?
                """,
                (now, now, cutoff),
            )
            application_cursor = conn.execute(
                """
                UPDATE editorial_review_applications
                SET status = 'failed',
                    error = '应用重启后检测到候选稿生成已中断，请重新生成',
                    updated_at = ?
                WHERE status = 'generating' AND updated_at < ?
                """,
                (now, cutoff),
            )
            return int(review_cursor.rowcount or 0) + int(
                application_cursor.rowcount or 0
            )

    def list_topic_sources(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM topic_sources"
            clauses: list[str] = []
            params: list[Any] = []
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            if enabled_only:
                clauses.append("enabled = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at, name"
            rows = conn.execute(sql, params).fetchall()
        return [self._topic_source_row(row) for row in rows]

    def get_topic_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM topic_sources WHERE id = ?"
            params: list[Any] = [str(source_id)]
            if self.owner_user_id:
                sql = (
                    "SELECT * FROM topic_sources "
                    "WHERE owner_user_id = ? AND (id = ? OR source_key = ?)"
                )
                params = [
                    self.owner_user_id,
                    str(source_id),
                    str(source_id),
                ]
            row = conn.execute(sql, params).fetchone()
        return self._topic_source_row(row) if row else None

    def upsert_topic_source(self, source: dict[str, Any]) -> None:
        now = _utc_now()
        requested_id = str(source["id"])
        source_key = str(source.get("source_key") or requested_id).strip()
        if not source_key:
            raise ValueError("选题来源标识不能为空")
        config = source.get("config")
        if config is None:
            config = _loads_json(source.get("config_json"), {})
        with self.connect() as conn:
            storage_id = requested_id
            if self.owner_user_id:
                existing = conn.execute(
                    """
                    SELECT id, source_key
                    FROM topic_sources
                    WHERE owner_user_id = ?
                      AND (id = ? OR source_key = ?)
                    """,
                    (self.owner_user_id, requested_id, source_key),
                ).fetchone()
                if existing:
                    storage_id = str(existing["id"])
                    source_key = str(existing["source_key"] or source_key)
                else:
                    storage_id = "ts_" + uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"wechat-publisher:topic-source:"
                        f"{self.owner_user_id}:{source_key}",
                    ).hex
            self._assert_write_owner(conn, "topic_sources", storage_id)
            conn.execute(
                """
                INSERT INTO topic_sources (
                    id, owner_user_id, source_key, name, source_type,
                    config_json, enabled, last_synced_at, last_error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, source_key) DO UPDATE SET
                    name=excluded.name,
                    source_type=excluded.source_type,
                    config_json=excluded.config_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    storage_id,
                    self.owner_user_id
                    or str(source.get("owner_user_id") or "").strip(),
                    source_key,
                    source["name"],
                    source["source_type"],
                    json.dumps(config or {}, ensure_ascii=False),
                    1 if source.get("enabled", True) else 0,
                    source.get("last_synced_at"),
                    source.get("last_error"),
                    source.get("created_at") or now,
                    now,
                ),
            )

    def update_topic_source_sync(
        self,
        source_id: str,
        *,
        error: str = "",
        synced_at: str | None = None,
    ) -> None:
        now = synced_at or _utc_now()
        storage_id = str(source_id)
        if self.owner_user_id:
            source = self.get_topic_source(source_id)
            if not source:
                return
            storage_id = str(source["id"])
        with self.connect() as conn:
            sql = """
                UPDATE topic_sources
                SET last_synced_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
            """
            params: list[Any] = [now, error, now, storage_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            conn.execute(sql, params)

    def delete_topic_source(self, source_id: str) -> None:
        storage_id = str(source_id)
        if self.owner_user_id:
            source = self.get_topic_source(source_id)
            if not source:
                return
            storage_id = str(source["id"])
        with self.connect() as conn:
            sql = "DELETE FROM topic_sources WHERE id = ?"
            params: list[Any] = [storage_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            conn.execute(sql, params)

    def upsert_topic_item(self, item: dict[str, Any]) -> None:
        if self.owner_user_id and not self.get_topic_source(str(item["source_id"])):
            raise ValueError("选题来源不存在")
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO topic_items (
                    id, source_id, title, url, published_at, summary, category,
                    favorite, used, raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, title, url) DO UPDATE SET
                    published_at=COALESCE(excluded.published_at, topic_items.published_at),
                    summary=COALESCE(NULLIF(excluded.summary, ''), topic_items.summary),
                    category=COALESCE(NULLIF(excluded.category, ''), topic_items.category),
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    item["id"],
                    item["source_id"],
                    item["title"],
                    item.get("url") or "",
                    item.get("published_at"),
                    item.get("summary") or "",
                    item.get("category") or "",
                    1 if item.get("favorite") else 0,
                    1 if item.get("used") else 0,
                    json.dumps(item.get("raw") or {}, ensure_ascii=False),
                    item.get("created_at") or now,
                    now,
                ),
            )

    def list_topic_items(
        self,
        *,
        source_ids: list[str] | None = None,
        since: str | None = None,
        keyword: str = "",
        favorite_only: bool = False,
        unused_only: bool = False,
        used_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if self.owner_user_id:
            clauses.append("ts.owner_user_id = ?")
            params.append(self.owner_user_id)
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            clauses.append(
                f"(ti.source_id IN ({placeholders}) "
                f"OR ts.source_key IN ({placeholders}))"
            )
            params.extend(source_ids)
            params.extend(source_ids)
        if since:
            clauses.append("COALESCE(ti.published_at, ti.created_at) >= ?")
            params.append(since)
        if keyword.strip():
            clauses.append("(ti.title LIKE ? OR ti.summary LIKE ?)")
            token = f"%{keyword.strip()}%"
            params.extend((token, token))
        if favorite_only:
            clauses.append("ti.favorite = 1")
        if unused_only:
            clauses.append("ti.used = 0")
        if used_only:
            clauses.append("ti.used = 1")
        sql = """
            SELECT ti.*, ts.name AS source_name, ts.source_type
            FROM topic_items ti
            JOIN topic_sources ts ON ts.id = ti.source_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += (
            " ORDER BY COALESCE(ti.published_at, ti.created_at) DESC, ti.id DESC "
            "LIMIT ? OFFSET ?"
        )
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._topic_item_row(row) for row in rows]

    def count_topic_items(
        self,
        *,
        source_ids: list[str] | None = None,
        since: str | None = None,
        keyword: str = "",
        favorite_only: bool = False,
        unused_only: bool = False,
        used_only: bool = False,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if self.owner_user_id:
            clauses.append("ts.owner_user_id = ?")
            params.append(self.owner_user_id)
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            clauses.append(
                f"(ti.source_id IN ({placeholders}) "
                f"OR ts.source_key IN ({placeholders}))"
            )
            params.extend(source_ids)
            params.extend(source_ids)
        if since:
            clauses.append("COALESCE(ti.published_at, ti.created_at) >= ?")
            params.append(since)
        if keyword.strip():
            clauses.append("(ti.title LIKE ? OR ti.summary LIKE ?)")
            token = f"%{keyword.strip()}%"
            params.extend((token, token))
        if favorite_only:
            clauses.append("ti.favorite = 1")
        if unused_only:
            clauses.append("ti.used = 0")
        if used_only:
            clauses.append("ti.used = 1")
        sql = """
            SELECT COUNT(*) AS total
            FROM topic_items ti
            JOIN topic_sources ts ON ts.id = ti.source_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total"] if row else 0)

    def get_topic_item(self, item_id: str) -> dict[str, Any] | None:
        """Return one topic item together with its source metadata."""

        with self.connect() as conn:
            owner_clause = " AND ts.owner_user_id = ?" if self.owner_user_id else ""
            row = conn.execute(
                f"""
                SELECT ti.*, ts.name AS source_name, ts.source_type
                FROM topic_items ti
                JOIN topic_sources ts ON ts.id = ti.source_id
                WHERE ti.id = ?
                {owner_clause}
                """,
                (
                    item_id,
                    *([self.owner_user_id] if self.owner_user_id else []),
                ),
            ).fetchone()
        return self._topic_item_row(row) if row else None

    def update_topic_item_flags(
        self,
        item_id: str,
        *,
        favorite: bool | None = None,
        used: bool | None = None,
    ) -> None:
        if self.owner_user_id and not self.get_topic_item(item_id):
            return
        updates = ["updated_at = ?"]
        values: list[Any] = [_utc_now()]
        if favorite is not None:
            updates.append("favorite = ?")
            values.append(1 if favorite else 0)
        if used is not None:
            updates.append("used = ?")
            values.append(1 if used else 0)
        values.append(item_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE topic_items SET {', '.join(updates)} WHERE id = ?", values
            )

    def list_followed_accounts(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM followed_accounts"
            clauses: list[str] = []
            params: list[Any] = []
            if self.owner_user_id:
                clauses.append("owner_user_id = ?")
                params.append(self.owner_user_id)
            if enabled_only:
                clauses.append("enabled = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY name"
            rows = conn.execute(sql, params).fetchall()
        return [self._followed_account_row(row) for row in rows]

    def get_followed_account(self, account_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM followed_accounts WHERE id = ?"
            params: list[Any] = [account_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
        return self._followed_account_row(row) if row else None

    def upsert_followed_account(self, account: dict[str, Any]) -> None:
        now = _utc_now()
        tags = account.get("tags")
        if tags is None:
            tags = _loads_json(account.get("tags_json"), [])
        keywords = account.get("keywords")
        if keywords is None:
            keywords = _loads_json(account.get("keywords_json"), [])
        with self.connect() as conn:
            self._assert_write_owner(
                conn, "followed_accounts", str(account["id"])
            )
            conn.execute(
                """
                INSERT INTO followed_accounts (
                    id, owner_user_id, name, wechat_id, official_account_id, category, tags_json, fetch_method,
                    sample_url, source_url, keywords_json, is_owned, enabled,
                    refresh_hours, last_synced_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    wechat_id=excluded.wechat_id,
                    official_account_id=excluded.official_account_id,
                    category=excluded.category,
                    tags_json=excluded.tags_json,
                    fetch_method=excluded.fetch_method,
                    sample_url=excluded.sample_url,
                    source_url=excluded.source_url,
                    keywords_json=excluded.keywords_json,
                    is_owned=excluded.is_owned,
                    enabled=excluded.enabled,
                    refresh_hours=excluded.refresh_hours,
                    updated_at=excluded.updated_at
                """,
                (
                    account["id"],
                    self.owner_user_id
                    or str(account.get("owner_user_id") or "").strip(),
                    account["name"], account.get("wechat_id") or "",
                    account.get("official_account_id") or "",
                    account.get("category") or "",
                    json.dumps(tags or [], ensure_ascii=False),
                    account.get("fetch_method") or "public_search",
                    account.get("sample_url") or "", account.get("source_url") or "",
                    json.dumps(keywords or [], ensure_ascii=False),
                    1 if account.get("is_owned") else 0,
                    1 if account.get("enabled", True) else 0,
                    max(1, int(account.get("refresh_hours") or 12)),
                    account.get("last_synced_at"), account.get("last_error") or "",
                    account.get("created_at") or now, now,
                ),
            )

    def merge_followed_accounts(self, keep_id: str, duplicate_ids: list[str]) -> None:
        duplicate_ids = [item for item in duplicate_ids if item and item != keep_id]
        if not duplicate_ids:
            return
        if self.owner_user_id:
            if not self.get_followed_account(keep_id):
                raise ValueError("关注公众号不存在")
            duplicate_ids = [
                item for item in duplicate_ids if self.get_followed_account(item)
            ]
            if not duplicate_ids:
                return
        placeholders = ",".join("?" for _ in duplicate_ids)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE followed_articles SET followed_account_id = ? "
                f"WHERE followed_account_id IN ({placeholders})",
                [keep_id, *duplicate_ids],
            )
            conn.execute(
                f"DELETE FROM followed_accounts WHERE id IN ({placeholders})",
                duplicate_ids,
            )

    def prune_invalid_followed_articles(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM followed_articles
                WHERE url LIKE '%weixin.sogou.com/antispider%'
                   OR url LIKE '%weixin.sogou.com/websearch/%'
                   OR (title = '搜狗搜索' AND url NOT LIKE '%mp.weixin.qq.com%')
                """
            )
            return int(cursor.rowcount or 0)

    def update_followed_account_sync(
        self, account_id: str, *, error: str = "", synced_at: str | None = None
    ) -> None:
        if self.owner_user_id and not self.get_followed_account(account_id):
            return
        now = synced_at or _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE followed_accounts
                SET last_synced_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, error, now, account_id),
            )

    def delete_followed_account(self, account_id: str) -> None:
        with self.connect() as conn:
            sql = "DELETE FROM followed_accounts WHERE id = ?"
            params: list[Any] = [account_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            conn.execute(sql, params)

    def upsert_followed_article(self, article: dict[str, Any]) -> None:
        followed_account_id = str(article.get("followed_account_id") or "").strip()
        if (
            self.owner_user_id
            and followed_account_id
            and not self.get_followed_account(followed_account_id)
        ):
            raise ValueError("关注公众号不存在")
        stored_id = str(article["id"])
        if self.owner_user_id:
            stored_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{self.owner_user_id}|{stored_id}",
            ).hex[:24]
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO followed_articles (
                    id, owner_user_id, followed_account_id, account_name, title, url,
                    published_at, discovered_at, cover_url, summary,
                    source_channel, external_key, is_read, is_favorite,
                    is_ignored, rewritten_batch_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    followed_account_id=COALESCE(excluded.followed_account_id, followed_articles.followed_account_id),
                    account_name=COALESCE(NULLIF(excluded.account_name, ''), followed_articles.account_name),
                    title=COALESCE(NULLIF(excluded.title, ''), followed_articles.title),
                    url=excluded.url,
                    published_at=COALESCE(excluded.published_at, followed_articles.published_at),
                    cover_url=COALESCE(NULLIF(excluded.cover_url, ''), followed_articles.cover_url),
                    summary=COALESCE(NULLIF(excluded.summary, ''), followed_articles.summary),
                    source_channel=excluded.source_channel,
                    external_key=COALESCE(NULLIF(excluded.external_key, ''), followed_articles.external_key),
                    updated_at=excluded.updated_at
                """,
                (
                    stored_id,
                    self.owner_user_id
                    or str(article.get("owner_user_id") or "").strip(),
                    article.get("followed_account_id"),
                    article.get("account_name") or "", article["title"], article["url"],
                    article.get("published_at"), article.get("discovered_at") or now,
                    article.get("cover_url") or "", article.get("summary") or "",
                    article.get("source_channel") or "manual", article.get("external_key") or "",
                    1 if article.get("is_read") else 0,
                    1 if article.get("is_favorite") else 0,
                    1 if article.get("is_ignored") else 0,
                    article.get("rewritten_batch_id"), article.get("created_at") or now, now,
                ),
            )

    def get_followed_article(self, article_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM followed_articles WHERE id = ?"
            params: list[Any] = [article_id]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_followed_article_by_url(self, url: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            sql = "SELECT * FROM followed_articles WHERE url = ?"
            params: list[Any] = [url]
            if self.owner_user_id:
                sql += " AND owner_user_id = ?"
                params.append(self.owner_user_id)
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_followed_article_by_identity(
        self,
        *,
        followed_account_id: str | None,
        external_key: str,
        title: str,
        published_at: str | None,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = None
            if followed_account_id and external_key:
                owner_clause = (
                    " AND owner_user_id = ?" if self.owner_user_id else ""
                )
                params: list[Any] = [followed_account_id, external_key]
                if self.owner_user_id:
                    params.append(self.owner_user_id)
                row = conn.execute(
                    f"""
                    SELECT * FROM followed_articles
                    WHERE followed_account_id = ? AND external_key = ?
                    {owner_clause}
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    params,
                ).fetchone()
            if row is None and followed_account_id and title and published_at:
                owner_clause = (
                    " AND owner_user_id = ?" if self.owner_user_id else ""
                )
                params = [followed_account_id, title, published_at]
                if self.owner_user_id:
                    params.append(self.owner_user_id)
                row = conn.execute(
                    f"""
                    SELECT * FROM followed_articles
                    WHERE followed_account_id = ? AND title = ? AND published_at = ?
                    {owner_clause}
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    params,
                ).fetchone()
        return dict(row) if row else None

    def list_followed_articles(
        self,
        *,
        account_ids: list[str] | None = None,
        since: str | None = None,
        keyword: str = "",
        unread_only: bool = False,
        favorite_only: bool = False,
        unrewritten_only: bool = False,
        include_ignored: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if self.owner_user_id:
            clauses.append("owner_user_id = ?")
            params.append(self.owner_user_id)
        if account_ids:
            clauses.append("followed_account_id IN (" + ",".join("?" for _ in account_ids) + ")")
            params.extend(account_ids)
        if since:
            clauses.append("COALESCE(published_at, discovered_at) >= ?")
            params.append(since)
        if keyword.strip():
            clauses.append("(title LIKE ? OR summary LIKE ? OR account_name LIKE ?)")
            token = f"%{keyword.strip()}%"
            params.extend((token, token, token))
        if unread_only:
            clauses.append("is_read = 0")
        if favorite_only:
            clauses.append("is_favorite = 1")
        if unrewritten_only:
            clauses.append("rewritten_batch_id IS NULL")
        if not include_ignored:
            clauses.append("is_ignored = 0")
        sql = "SELECT * FROM followed_articles"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += (
            " ORDER BY COALESCE(published_at, discovered_at) DESC, "
            "discovered_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def update_followed_article(
        self,
        article_id: str,
        *,
        is_read: bool | None = None,
        is_favorite: bool | None = None,
        is_ignored: bool | None = None,
        rewritten_batch_id: str | None = None,
    ) -> None:
        if self.owner_user_id and not self.get_followed_article(article_id):
            return
        updates = ["updated_at = ?"]
        values: list[Any] = [_utc_now()]
        for column, value in (
            ("is_read", is_read),
            ("is_favorite", is_favorite),
            ("is_ignored", is_ignored),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                values.append(1 if value else 0)
        if rewritten_batch_id is not None:
            updates.append("rewritten_batch_id = ?")
            values.append(rewritten_batch_id or None)
        values.append(article_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE followed_articles SET {', '.join(updates)} WHERE id = ?", values
            )

    # Shadow billing -----------------------------------------------------

    def create_usage_operation(self, operation: dict[str, Any]) -> str:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            raise ValueError("用量操作必须绑定当前登录账号")
        supplied_owner = str(operation.get("owner_user_id") or owner_user_id)
        if supplied_owner != owner_user_id:
            raise ValueError("用量操作不属于当前登录账号")
        operation_id = str(operation.get("id") or uuid.uuid4().hex)
        idempotency_key = str(operation.get("idempotency_key") or operation_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_operations (
                    id, owner_user_id, scene, source_channel, subject_type,
                    subject_id, idempotency_key, status, mode, job_id,
                    estimated_points, reserved_points, charged_points,
                    reservation_expires_at, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL, ?, NULL)
                ON CONFLICT(owner_user_id, idempotency_key) DO NOTHING
                """,
                (
                    operation_id,
                    owner_user_id,
                    str(operation.get("scene") or "unknown"),
                    str(operation.get("source_channel") or "system"),
                    str(operation.get("subject_type") or "operation"),
                    str(operation.get("subject_id") or ""),
                    idempotency_key,
                    str(operation.get("status") or "running"),
                    str(operation.get("mode") or "shadow"),
                    operation.get("job_id"),
                    str(operation.get("created_at") or _utc_now()),
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM usage_operations
                WHERE owner_user_id = ? AND idempotency_key = ?
                """,
                (owner_user_id, idempotency_key),
            ).fetchone()
        if not row:
            raise RuntimeError("影子用量操作创建失败")
        return str(row["id"])

    def finish_usage_operation(
        self,
        operation_id: str,
        *,
        status: str,
        estimated_points: int,
        charged_points: int,
        completed_at: str,
    ) -> None:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            raise ValueError("用量操作必须绑定当前登录账号")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE usage_operations
                SET status = ?, estimated_points = ?, charged_points = ?,
                    completed_at = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    str(status),
                    max(0, int(estimated_points)),
                    0 if str(status) else max(0, int(charged_points)),
                    str(completed_at),
                    str(operation_id),
                    owner_user_id,
                ),
            )

    def insert_ai_usage_event(self, event: dict[str, Any]) -> None:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            raise ValueError("AI 用量必须绑定当前登录账号")
        if str(event.get("owner_user_id") or owner_user_id) != owner_user_id:
            raise ValueError("AI 用量不属于当前登录账号")
        operation_id = str(event.get("operation_id") or "")
        with self.connect() as conn:
            owned_operation = conn.execute(
                """
                SELECT 1 FROM usage_operations
                WHERE id = ? AND owner_user_id = ?
                """,
                (operation_id, owner_user_id),
            ).fetchone()
            if not owned_operation:
                raise ValueError("用量操作不存在或不属于当前登录账号")
            conn.execute(
                """
                INSERT INTO ai_usage_events (
                    id, owner_user_id, operation_id, job_id, model_id,
                    provider, provider_model, funding_source, modality,
                    input_tokens, cached_input_tokens, output_tokens,
                    reasoning_tokens, total_tokens, image_count, fixed_units,
                    usage_source, provider_request_id, provider_response_id,
                    provider_cost_micro_cny, retail_cost_micro_cny,
                    estimated_points, pricing_status, price_snapshot_json,
                    contributes_to_result, billable, status, error_code, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    str(event.get("id") or uuid.uuid4().hex),
                    owner_user_id,
                    operation_id,
                    event.get("job_id"),
                    str(event.get("model_id") or ""),
                    str(event.get("provider") or "unknown"),
                    str(event.get("provider_model") or ""),
                    str(event.get("funding_source") or "platform"),
                    str(event.get("modality") or "text"),
                    max(0, int(event.get("input_tokens") or 0)),
                    max(0, int(event.get("cached_input_tokens") or 0)),
                    max(0, int(event.get("output_tokens") or 0)),
                    max(0, int(event.get("reasoning_tokens") or 0)),
                    max(0, int(event.get("total_tokens") or 0)),
                    max(0, int(event.get("image_count") or 0)),
                    max(0, int(event.get("fixed_units") or 0)),
                    str(event.get("usage_source") or "unknown"),
                    str(event.get("provider_request_id") or ""),
                    str(event.get("provider_response_id") or ""),
                    max(0, int(event.get("provider_cost_micro_cny") or 0)),
                    max(0, int(event.get("retail_cost_micro_cny") or 0)),
                    max(0, int(event.get("estimated_points") or 0)),
                    str(event.get("pricing_status") or "price_missing"),
                    str(event.get("price_snapshot_json") or "{}"),
                    1 if event.get("contributes_to_result", True) else 0,
                    1 if event.get("billable", False) else 0,
                    str(event.get("status") or "unknown"),
                    str(event.get("error_code") or ""),
                    str(event.get("created_at") or _utc_now()),
                ),
            )

    def usage_operation_totals(self, operation_id: str) -> dict[str, int]:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            return {}
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS event_count,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(image_count), 0) AS image_count,
                       COALESCE(SUM(CASE WHEN contributes_to_result = 1
                           THEN estimated_points ELSE 0 END), 0) AS estimated_points
                FROM ai_usage_events
                WHERE operation_id = ? AND owner_user_id = ?
                """,
                (str(operation_id), owner_user_id),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row or {}).items()}

    def get_effective_model_price_card(
        self,
        *,
        provider: str,
        provider_model: str,
        modality: str,
        at: str | None = None,
    ) -> dict[str, Any] | None:
        effective_at = str(at or _utc_now())
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM model_price_cards
                WHERE enabled = 1
                  AND provider = ?
                  AND provider_model IN (?, '*')
                  AND modality = ?
                  AND effective_from <= ?
                  AND (effective_to IS NULL OR effective_to > ?)
                ORDER BY CASE WHEN provider_model = ? THEN 0 ELSE 1 END,
                         effective_from DESC
                LIMIT 1
                """,
                (
                    str(provider),
                    str(provider_model),
                    str(modality),
                    effective_at,
                    effective_at,
                    str(provider_model),
                ),
            ).fetchone()
        return dict(row) if row else None

    def upsert_model_price_card(self, card: dict[str, Any]) -> str:
        if self.owner_user_id:
            raise ValueError("模型价格卡只能由平台管理员维护")
        card_id = str(card.get("id") or uuid.uuid4().hex)
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO model_price_cards (
                    id, provider, provider_model, modality,
                    input_micro_cny_per_million,
                    cached_input_micro_cny_per_million,
                    output_micro_cny_per_million, image_micro_cny_each,
                    fixed_request_micro_cny, markup_basis_points, points_per_cny,
                    effective_from, effective_to, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider = excluded.provider,
                    provider_model = excluded.provider_model,
                    modality = excluded.modality,
                    input_micro_cny_per_million = excluded.input_micro_cny_per_million,
                    cached_input_micro_cny_per_million = excluded.cached_input_micro_cny_per_million,
                    output_micro_cny_per_million = excluded.output_micro_cny_per_million,
                    image_micro_cny_each = excluded.image_micro_cny_each,
                    fixed_request_micro_cny = excluded.fixed_request_micro_cny,
                    markup_basis_points = excluded.markup_basis_points,
                    points_per_cny = excluded.points_per_cny,
                    effective_from = excluded.effective_from,
                    effective_to = excluded.effective_to,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    card_id,
                    str(card.get("provider") or "unknown"),
                    str(card.get("provider_model") or "*"),
                    str(card.get("modality") or "text"),
                    max(0, int(card.get("input_micro_cny_per_million") or 0)),
                    max(
                        0,
                        int(card.get("cached_input_micro_cny_per_million") or 0),
                    ),
                    max(0, int(card.get("output_micro_cny_per_million") or 0)),
                    max(0, int(card.get("image_micro_cny_each") or 0)),
                    max(0, int(card.get("fixed_request_micro_cny") or 0)),
                    max(0, int(card.get("markup_basis_points") or 10_000)),
                    max(0, int(card.get("points_per_cny") or 100)),
                    str(card.get("effective_from") or now),
                    card.get("effective_to") or None,
                    1 if card.get("enabled", True) else 0,
                    str(card.get("created_at") or now),
                    now,
                ),
            )
        return card_id

    def list_model_price_cards(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM model_price_cards"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY provider, provider_model, modality, effective_from DESC"
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def billing_usage_summary(self) -> dict[str, int]:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            return {}
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
            timespec="microseconds"
        )
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT o.id) AS operations,
                       COALESCE(SUM(e.input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(e.cached_input_tokens), 0) AS cached_input_tokens,
                       COALESCE(SUM(e.output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(e.image_count), 0) AS image_count,
                       COALESCE(SUM(CASE WHEN e.contributes_to_result = 1
                           THEN e.estimated_points ELSE 0 END), 0) AS estimated_points
                FROM usage_operations AS o
                LEFT JOIN ai_usage_events AS e ON e.operation_id = o.id
                WHERE o.owner_user_id = ? AND o.created_at >= ?
                """,
                (owner_user_id, since),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row or {}).items()}

    def list_usage_operations(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.scene, o.source_channel, o.subject_type,
                       o.subject_id, o.status, o.mode, o.job_id,
                       o.estimated_points, o.charged_points,
                       o.created_at, o.completed_at,
                       COUNT(e.id) AS event_count,
                       COALESCE(SUM(e.input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(e.cached_input_tokens), 0) AS cached_input_tokens,
                       COALESCE(SUM(e.output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(e.image_count), 0) AS image_count,
                       COALESCE(SUM(
                           CASE
                               WHEN e.pricing_status = 'price_missing'
                                AND e.funding_source = 'platform'
                                AND e.status = 'succeeded'
                                AND e.contributes_to_result = 1
                               THEN 1 ELSE 0
                           END
                       ), 0) AS price_missing_events
                FROM usage_operations AS o
                LEFT JOIN ai_usage_events AS e ON e.operation_id = o.id
                WHERE o.owner_user_id = ?
                GROUP BY o.id, o.scene, o.source_channel, o.subject_type,
                         o.subject_id, o.status, o.mode, o.job_id,
                         o.estimated_points, o.charged_points,
                         o.created_at, o.completed_at
                ORDER BY o.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (owner_user_id, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        return [dict(row) for row in rows]

    def admin_billing_usage_summary(self) -> dict[str, int]:
        if self.owner_user_id:
            raise ValueError("平台成本汇总仅管理员可读")
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
            timespec="microseconds"
        )
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT operation_id) AS operations,
                       COUNT(*) AS events,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(image_count), 0) AS image_count,
                       COALESCE(SUM(provider_cost_micro_cny), 0) AS provider_cost_micro_cny,
                       COALESCE(SUM(retail_cost_micro_cny), 0) AS retail_cost_micro_cny,
                       COALESCE(SUM(estimated_points), 0) AS estimated_points,
                       COALESCE(SUM(CASE WHEN pricing_status = 'price_missing'
                           THEN 1 ELSE 0 END), 0) AS price_missing_events
                FROM ai_usage_events
                WHERE created_at >= ?
                """,
                (since,),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row or {}).items()}

    def admin_list_ai_usage_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if self.owner_user_id:
            raise ValueError("平台成本明细仅管理员可读")
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, owner_user_id, operation_id, job_id, model_id,
                       provider, provider_model, funding_source, modality,
                       input_tokens, cached_input_tokens, output_tokens,
                       reasoning_tokens, total_tokens, image_count, fixed_units,
                       usage_source, provider_cost_micro_cny,
                       retail_cost_micro_cny, estimated_points, pricing_status,
                       contributes_to_result, billable, status, error_code, created_at
                FROM ai_usage_events
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        return [dict(row) for row in rows]

    def grant_credit_points(
        self,
        *,
        points: int,
        source_type: str,
        source_id: str = "",
        expires_at: str | None = None,
        actor_user_id: str = "",
        reason: str = "",
    ) -> str:
        owner_user_id = str(self.owner_user_id or "").strip()
        amount = int(points)
        if not owner_user_id:
            raise ValueError("积分必须绑定当前登录账号")
        if amount <= 0:
            raise ValueError("发放积分必须大于 0")
        bucket_id = uuid.uuid4().hex
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO credit_buckets (
                    id, owner_user_id, source_type, source_id,
                    granted_points, remaining_points, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bucket_id,
                    owner_user_id,
                    str(source_type),
                    str(source_id or ""),
                    amount,
                    amount,
                    expires_at,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO credit_ledger (
                    id, owner_user_id, bucket_id, operation_id,
                    amount_points, event_type, actor_user_id, reason, created_at
                ) VALUES (?, ?, ?, NULL, ?, 'grant', ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    owner_user_id,
                    bucket_id,
                    amount,
                    str(actor_user_id or ""),
                    str(reason or ""),
                    now,
                ),
            )
        return bucket_id

    def credit_wallet_summary(self) -> dict[str, int]:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            return {"available": 0}
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(remaining_points), 0) AS available
                FROM credit_buckets
                WHERE owner_user_id = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (owner_user_id, now),
            ).fetchone()
        return {"available": int((row or {})["available"] or 0)}

    def list_credit_ledger(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        owner_user_id = str(self.owner_user_id or "").strip()
        if not owner_user_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, bucket_id, operation_id, amount_points,
                       event_type, reason, created_at
                FROM credit_ledger
                WHERE owner_user_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (owner_user_id, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _topic_source_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["source_key"] = str(data.get("source_key") or data.get("id") or "")
        data["config"] = _loads_json(data.get("config_json"), {})
        return data

    @staticmethod
    def _topic_item_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["raw"] = _loads_json(data.get("raw_json"), {})
        return data

    @staticmethod
    def _followed_account_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["tags"] = _loads_json(data.get("tags_json"), [])
        data["keywords"] = _loads_json(data.get("keywords_json"), [])
        return data

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        source_channel = str(
            data.get(JOB_SOURCE_OF_TRUTH) or data.get("source") or "manual"
        )
        data[JOB_SOURCE_OF_TRUTH] = source_channel
        # Public contracts keep the historical key during the compatibility
        # window, but its value is projected from the canonical field.
        data["source"] = source_channel

        def loads(key: str, default: Any) -> Any:
            raw = data.get(key)
            if not raw:
                return default
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return default

        data["titles"] = loads("titles_json", [])
        data["subtitles"] = loads("subtitles_json", [])
        data["title_candidates"] = loads("title_candidates_json", [])
        data["meta"] = loads("meta_json", {})
        return data


def _loads_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default
