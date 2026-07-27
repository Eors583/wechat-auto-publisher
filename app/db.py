from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    name TEXT NOT NULL,
                    provider_type TEXT NOT NULL DEFAULT 'openai_compatible',
                    api_base TEXT,
                    model TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT 'image',
                    content TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS official_accounts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    app_secret_encrypted TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    layout_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
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

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    creation_plan_id TEXT NOT NULL,
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
                    followed_account_id TEXT,
                    account_name TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
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
                    FOREIGN KEY (followed_account_id) REFERENCES followed_accounts(id) ON DELETE SET NULL
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
                """
            )
            # Lightweight migration for databases created before per-account layouts.
            account_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(official_accounts)").fetchall()
            }
            if "layout_json" not in account_columns:
                conn.execute("ALTER TABLE official_accounts ADD COLUMN layout_json TEXT")
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
                    status, step, topic, source, source_url, raw_content, mode,
                    meta_json, created_at, updated_at
                ) VALUES (?, 'ingest', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pending",
                    topic,
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
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def recover_stale_jobs(self, *, older_than_minutes: int = 30) -> int:
        """Mark orphaned in-progress jobs left behind by a previous process."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=max(1, older_than_minutes))
        ).replace(microsecond=0).isoformat()
        active = ("pending", "ingesting", "rewriting", "title_optimizing", "rendering", "injecting")
        placeholders = ",".join("?" for _ in active)
        now = _utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET status = 'cancelled',
                    error = '应用重启后检测到历史任务已中断，请重新发起改写',
                    updated_at = ?
                WHERE status IN ({placeholders}) AND updated_at < ?
                """,
                (now, *active, cutoff),
            )
            return int(cursor.rowcount or 0)

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
            if key in {
                "selected_title",
                "selected_subtitle",
                "digest",
                "body",
            }:
                content_compare.append((key, value))
        if content_compare:
            updates.append(
                "content_revision = content_revision + CASE WHEN ("
                + " OR ".join(
                    f"{key} IS NOT ?" for key, _value in content_compare
                )
                + ") THEN 1 ELSE 0 END"
            )
            values.extend(value for _key, value in content_compare)
        updates.append("updated_at = ?")
        values.append(_utc_now())
        values.append(job_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
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
    ) -> None:
        now = _utc_now()
        with self.connect() as conn:
            day = now[:10].replace("-", "")
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM batches WHERE substr(created_at, 1, 10) = ?",
                    (now[:10],),
                ).fetchone()[0]
                or 0
            )
            display_id = f"{day}-{count + 1:02d}"
            conn.execute(
                """
                INSERT INTO batches (
                    id, display_id, status, topic, source_mode,
                    reference_urls_json, required_facts, rewrite_intensity,
                    source_url, raw_content,
                    requested_by, chat_id, error, parent_batch_id,
                    archived_at, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)
                """,
                (
                    batch_id,
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
            conn.execute(
                """
                INSERT INTO batch_jobs (batch_id, job_id, account_id, account_name)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, job_id, account_id, account_name),
            )

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
        with self.connect() as conn:
            conn.execute(
                f"UPDATE batches SET {', '.join(updates)} WHERE id = ?", values
            )

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if not row:
                return None
            batch = dict(row)
            jobs = conn.execute(
                """
                SELECT bj.account_id, bj.account_name,
                       bj.review_status, bj.viewed_at, bj.confirmed_at, j.*
                FROM batch_jobs bj
                JOIN jobs j ON j.id = bj.job_id
                WHERE bj.batch_id = ?
                ORDER BY j.id
                """,
                (batch_id,),
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
            where = "" if include_archived else "WHERE archived_at IS NULL"
            rows = conn.execute(
                f"SELECT id FROM batches {where} ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [batch for row in rows if (batch := self.get_batch(str(row["id"])))]

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
            cursor = conn.execute(
                """
                UPDATE batch_jobs
                SET review_status = ?,
                    viewed_at = COALESCE(viewed_at, ?),
                    confirmed_at = ?
                WHERE batch_id = ? AND job_id = ?
                """,
                (review_status, viewed_at, confirmed_at, batch_id, int(job_id)),
            )
            if not cursor.rowcount:
                raise KeyError(f"任务不属于该批次：{job_id}")

    def archive_batch(self, batch_id: str, *, archived: bool = True) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE batches SET archived_at = ?, updated_at = ? WHERE id = ?",
                (_utc_now() if archived else None, _utc_now(), batch_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"批次不存在：{batch_id}")

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
        except sqlite3.IntegrityError:
            return False

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
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
            sql = "SELECT * FROM ai_models"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql).fetchall()]

    def get_ai_model(self, model_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_models WHERE id = ?", (model_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_ai_model(self, model: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_models (
                    id, name, provider_type, api_base, model,
                    api_key_encrypted, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    provider_type=excluded.provider_type,
                    api_base=excluded.api_base,
                    model=excluded.model,
                    api_key_encrypted=excluded.api_key_encrypted,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    model["id"],
                    model["name"],
                    model.get("provider_type", "openai_compatible"),
                    model.get("api_base"),
                    model["model"],
                    model["api_key_encrypted"],
                    1 if model.get("enabled", True) else 0,
                    model.get("created_at") or now,
                    now,
                ),
            )

    def delete_ai_model(self, model_id: str) -> None:
        with self.connect() as conn:
            used = conn.execute(
                "SELECT name FROM official_accounts WHERE model_id = ? LIMIT 1",
                (model_id,),
            ).fetchone()
            if used:
                raise ValueError(f"该模型正被公众号“{used['name']}”使用，请先修改公众号绑定")
            conn.execute("DELETE FROM ai_models WHERE id = ?", (model_id,))

    def list_prompt_templates(
        self,
        *,
        purpose: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            clauses: list[str] = []
            params: list[Any] = []
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
            row = conn.execute(
                "SELECT * FROM prompt_templates WHERE id = ?", (template_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_prompt_template(self, template: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO prompt_templates (
                    id, name, purpose, content, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    purpose=excluded.purpose,
                    content=excluded.content,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    template["id"],
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
            conn.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))

    def list_creation_plans(
        self, *, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM creation_plans"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql).fetchall()]

    def get_creation_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM creation_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_creation_plan(self, plan: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO creation_plans (
                    id, name, description, article_prompt_template_id,
                    image_prompt_template_id, editorial_review_profile_id,
                    layout_json, image_settings_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.execute("DELETE FROM creation_plans WHERE id = ?", (plan_id,))

    def list_account_creation_plan_defaults(
        self, *, plan_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if plan_id is None:
                rows = conn.execute(
                    "SELECT * FROM account_creation_plan_defaults"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM account_creation_plan_defaults
                    WHERE creation_plan_id = ?
                    """,
                    (plan_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_account_creation_plan_default(
        self, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM account_creation_plan_defaults
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_account_creation_plan_default(
        self, account_id: str, creation_plan_id: str
    ) -> None:
        now = _utc_now()
        with self.connect() as conn:
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
            clauses.append("creation_plan_id = ?")
            params.append(creation_plan_id)
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        sql = "SELECT * FROM creation_plan_account_templates"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, account_id"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_creation_plan_account_template(
        self, creation_plan_id: str, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM creation_plan_account_templates
                WHERE creation_plan_id = ? AND account_id = ?
                """,
                (creation_plan_id, account_id),
            ).fetchone()
            return dict(row) if row else None

    def upsert_creation_plan_account_template(
        self, binding: dict[str, Any]
    ) -> None:
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
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql).fetchall()]

    def get_official_account(self, account_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM official_accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_official_account(self, account: dict[str, Any]) -> None:
        now = _utc_now()
        layout = account.get("layout")
        if layout is None:
            try:
                layout = json.loads(str(account.get("layout_json") or "{}"))
            except json.JSONDecodeError:
                layout = {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO official_accounts (
                    id, name, app_id, app_secret_encrypted, model_id, layout_json,
                    enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    app_id=excluded.app_id,
                    app_secret_encrypted=excluded.app_secret_encrypted,
                    model_id=excluded.model_id,
                    layout_json=excluded.layout_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    account["id"],
                    account["name"],
                    account["app_id"],
                    account["app_secret_encrypted"],
                    account["model_id"],
                    json.dumps(layout or {}, ensure_ascii=False),
                    1 if account.get("enabled", True) else 0,
                    account.get("created_at") or now,
                    now,
                ),
            )

    def delete_official_account(self, account_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM official_accounts WHERE id = ?", (account_id,))

    def list_editorial_review_profiles(
        self, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM editorial_review_profiles"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at, name"
            return [dict(row) for row in conn.execute(sql).fetchall()]

    def get_editorial_review_profile(
        self, profile_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM editorial_review_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_editorial_review_profile(self, profile: dict[str, Any]) -> None:
        now = _utc_now()
        config = profile.get("config")
        if config is None:
            config = _loads_json(profile.get("config_json"), {})
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO editorial_review_profiles (
                    id, name, description, config_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    config_json=excluded.config_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    profile["id"],
                    profile["name"],
                    profile.get("description") or "",
                    json.dumps(config or {}, ensure_ascii=False),
                    1 if profile.get("enabled", True) else 0,
                    profile.get("created_at") or now,
                    now,
                ),
            )

    def delete_editorial_review_profile(self, profile_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM editorial_review_profiles WHERE id = ?",
                (profile_id,),
            )
            conn.execute(
                """
                UPDATE account_editorial_review_defaults
                SET profile_id = NULL, config_json = '{}', updated_at = ?
                WHERE profile_id = ?
                """,
                (_utc_now(), profile_id),
            )

    def get_account_editorial_review_default(
        self, account_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM account_editorial_review_defaults
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_account_editorial_review_default(
        self,
        account_id: str,
        *,
        profile_id: str | None,
        config: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_now()
        with self.connect() as conn:
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
                    json.dumps(config or {}, ensure_ascii=False),
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
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
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
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at, name"
            rows = conn.execute(sql).fetchall()
        return [self._topic_source_row(row) for row in rows]

    def get_topic_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_sources WHERE id = ?", (source_id,)
            ).fetchone()
        return self._topic_source_row(row) if row else None

    def upsert_topic_source(self, source: dict[str, Any]) -> None:
        now = _utc_now()
        config = source.get("config")
        if config is None:
            config = _loads_json(source.get("config_json"), {})
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO topic_sources (
                    id, name, source_type, config_json, enabled,
                    last_synced_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    source_type=excluded.source_type,
                    config_json=excluded.config_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    source["id"],
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
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE topic_sources
                SET last_synced_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, error, now, source_id),
            )

    def delete_topic_source(self, source_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM topic_sources WHERE id = ?", (source_id,))

    def upsert_topic_item(self, item: dict[str, Any]) -> None:
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
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_ids:
            clauses.append("ti.source_id IN (" + ",".join("?" for _ in source_ids) + ")")
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
        sql = """
            SELECT ti.*, ts.name AS source_name, ts.source_type
            FROM topic_items ti
            JOIN topic_sources ts ON ts.id = ti.source_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(ti.published_at, ti.created_at) DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._topic_item_row(row) for row in rows]

    def get_topic_item(self, item_id: str) -> dict[str, Any] | None:
        """Return one topic item together with its source metadata."""

        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT ti.*, ts.name AS source_name, ts.source_type
                FROM topic_items ti
                JOIN topic_sources ts ON ts.id = ti.source_id
                WHERE ti.id = ?
                """,
                (item_id,),
            ).fetchone()
        return self._topic_item_row(row) if row else None

    def update_topic_item_flags(
        self,
        item_id: str,
        *,
        favorite: bool | None = None,
        used: bool | None = None,
    ) -> None:
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
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY name"
            rows = conn.execute(sql).fetchall()
        return [self._followed_account_row(row) for row in rows]

    def get_followed_account(self, account_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM followed_accounts WHERE id = ?", (account_id,)
            ).fetchone()
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
            conn.execute(
                """
                INSERT INTO followed_accounts (
                    id, name, wechat_id, official_account_id, category, tags_json, fetch_method,
                    sample_url, source_url, keywords_json, is_owned, enabled,
                    refresh_hours, last_synced_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    account["id"], account["name"], account.get("wechat_id") or "",
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
            conn.execute("DELETE FROM followed_accounts WHERE id = ?", (account_id,))

    def upsert_followed_article(self, article: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO followed_articles (
                    id, followed_account_id, account_name, title, url,
                    published_at, discovered_at, cover_url, summary,
                    source_channel, external_key, is_read, is_favorite,
                    is_ignored, rewritten_batch_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
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
                    article["id"], article.get("followed_account_id"),
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
            row = conn.execute(
                "SELECT * FROM followed_articles WHERE id = ?", (article_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_followed_article_by_url(self, url: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM followed_articles WHERE url = ?", (url,)
            ).fetchone()
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
                row = conn.execute(
                    """
                    SELECT * FROM followed_articles
                    WHERE followed_account_id = ? AND external_key = ?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (followed_account_id, external_key),
                ).fetchone()
            if row is None and followed_account_id and title and published_at:
                row = conn.execute(
                    """
                    SELECT * FROM followed_articles
                    WHERE followed_account_id = ? AND title = ? AND published_at = ?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (followed_account_id, title, published_at),
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

    @staticmethod
    def _topic_source_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
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
