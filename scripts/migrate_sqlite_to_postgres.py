from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.db import Database
from app.db_backend import is_postgres_url

BUSINESS_TABLES = (
    "users",
    "app_settings",
    "user_settings",
    "ai_models",
    "prompt_templates",
    "official_accounts",
    "ads",
    "batches",
    "jobs",
    "batch_jobs",
    "bot_sessions",
    "bot_contexts",
    "processed_events",
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

# Access tokens and login sessions are deliberately not copied. They are
# ephemeral authentication material and must be reissued by PostgreSQL-backed
# services after the cut-over.
SKIPPED_EPHEMERAL_TABLES = ("token_cache", "user_sessions")

_ENCRYPTED_FIELD_NAMES = {
    "api_key_encrypted",
    "app_secret_encrypted",
    "cookie_encrypted",
    "token_encrypted",
    "password_encrypted",
}


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


def _rows(
    connection: Any,
    table: str,
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def _table_columns(connection: Any, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def _insert(
    connection: Any,
    table: str,
    row: dict[str, Any],
    target_columns: dict[str, set[str]],
    *,
    serial: bool = False,
) -> int:
    payload = {
        key: value
        for key, value in row.items()
        if key in target_columns[table] and not (serial and key == "id")
    }
    columns = list(payload)
    placeholders = ",".join("?" for _ in columns)
    prefix = "INSERT INTO" if serial else "INSERT OR IGNORE INTO"
    cursor = connection.execute(
        f"{prefix} {table} ({','.join(columns)}) "
        f"VALUES ({placeholders})",
        tuple(payload[column] for column in columns),
    )
    if serial:
        if cursor.lastrowid is None:
            raise RuntimeError(f"迁移 {table} 时没有返回新主键")
        return int(cursor.lastrowid)
    return max(0, int(cursor.rowcount or 0))


def _unique_text_id(
    candidate: str,
    used: set[str],
    *,
    prefix: str,
    fingerprint: str,
) -> str:
    if candidate and candidate not in used:
        used.add(candidate)
        return candidate
    generated = f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, fingerprint + ':' + candidate).hex}"
    counter = 1
    while generated in used:
        generated = f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, fingerprint + ':' + candidate + ':' + str(counter)).hex}"
        counter += 1
    used.add(generated)
    return generated


def _portable_secret(value: Any) -> Any:
    text = str(value or "")
    if not text:
        return value
    if text.startswith("fernet:"):
        # Decrypting once also verifies that the configured server key matches.
        decrypt_api_key(text)
        return text
    return encrypt_api_key(decrypt_api_key(text))


def _portable_json_secrets(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value

    def convert(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {
                child_key: convert(child_value, str(child_key))
                for child_key, child_value in item.items()
            }
        if isinstance(item, list):
            return [convert(child) for child in item]
        if key in _ENCRYPTED_FIELD_NAMES and isinstance(item, str):
            return _portable_secret(item)
        return item

    return json.dumps(convert(parsed), ensure_ascii=False)


def _rewrite_json_ids(value: Any, replacements: dict[str, str]) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return replacements.get(value, value)

    def replace(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: replace(child) for key, child in item.items()}
        if isinstance(item, list):
            return [replace(child) for child in item]
        if isinstance(item, str):
            return replacements.get(item, item)
        return item

    return json.dumps(replace(parsed), ensure_ascii=False)


def _require_credential_key() -> None:
    if not str(os.getenv("CREDENTIAL_ENCRYPTION_KEY") or "").strip():
        raise RuntimeError(
            "迁移前必须配置 CREDENTIAL_ENCRYPTION_KEY；"
            "该值必须与 PostgreSQL 服务运行环境一致"
        )


def migrate(sqlite_path: Path, database_url: str) -> dict[str, int]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite 数据库不存在：{sqlite_path}")
    if not is_postgres_url(database_url):
        raise ValueError("目标必须是 postgresql:// 或 postgres:// 地址")
    _require_credential_key()

    fingerprint = hashlib.sha256(sqlite_path.read_bytes()).hexdigest()
    marker_key = f"migration.sqlite_to_postgres.v3.{fingerprint[:20]}"
    target = Database(database_url)
    source = sqlite3.connect(str(sqlite_path))
    source.row_factory = sqlite3.Row
    counts: defaultdict[str, int] = defaultdict(int)
    try:
        available = _sqlite_tables(source)
        with target.connect() as destination:
            marker = destination.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (marker_key,),
            ).fetchone()
            if marker:
                return {"already_migrated": 1}

            target_columns = {
                table: _table_columns(destination, table)
                for table in BUSINESS_TABLES
                if table in available
            }

            # Re-encrypt the already-existing PostgreSQL credentials first.
            for row in _rows(destination, "ai_models"):
                encrypted = _portable_secret(row.get("api_key_encrypted"))
                if encrypted != row.get("api_key_encrypted"):
                    destination.execute(
                        "UPDATE ai_models SET api_key_encrypted = ? WHERE id = ?",
                        (encrypted, row["id"]),
                    )
                    counts["postgres_credentials_reencrypted"] += 1
            for row in _rows(destination, "official_accounts"):
                encrypted = _portable_secret(row.get("app_secret_encrypted"))
                if encrypted != row.get("app_secret_encrypted"):
                    destination.execute(
                        "UPDATE official_accounts SET app_secret_encrypted = ? WHERE id = ?",
                        (encrypted, row["id"]),
                    )
                    counts["postgres_credentials_reencrypted"] += 1
            for table, key_columns in (
                ("app_settings", ("key",)),
                ("user_settings", ("user_id", "key")),
            ):
                for row in _rows(destination, table):
                    converted = _portable_json_secrets(row.get("value"))
                    if converted == row.get("value"):
                        continue
                    where = " AND ".join(f"{key} = ?" for key in key_columns)
                    destination.execute(
                        f"UPDATE {table} SET value = ? WHERE {where}",
                        (converted, *(row[key] for key in key_columns)),
                    )
                    counts["postgres_credentials_reencrypted"] += 1

            # Users are canonicalized by username so all customer-owned rows
            # point at the PostgreSQL identity used by login sessions.
            target_users = _rows(destination, "users")
            users_by_name = {
                str(row["username"]).strip().casefold(): row
                for row in target_users
            }
            used_user_ids = {str(row["id"]) for row in target_users}
            user_map: dict[str, str] = {}
            for row in _rows(source, "users"):
                old_id = str(row["id"])
                match = users_by_name.get(
                    str(row["username"]).strip().casefold()
                )
                if match:
                    user_map[old_id] = str(match["id"])
                    counts["users_mapped"] += 1
                    continue
                new_id = _unique_text_id(
                    old_id,
                    used_user_ids,
                    prefix="legacy_user",
                    fingerprint=fingerprint,
                )
                payload = {**row, "id": new_id}
                counts["users"] += _insert(
                    destination,
                    "users",
                    payload,
                    target_columns,
                )
                user_map[old_id] = new_id
                users_by_name[
                    str(row["username"]).strip().casefold()
                ] = payload

            admin_row = next(
                (
                    row
                    for row in _rows(destination, "users")
                    if str(row.get("role") or "") == "admin"
                ),
                None,
            )
            if admin_row is None:
                raise RuntimeError("PostgreSQL 中没有可接收历史数据的管理员")
            default_owner = str(admin_row["id"])

            def owner_id(value: Any) -> str:
                old = str(value or "").strip()
                if not old:
                    return default_owner
                if old not in user_map:
                    raise RuntimeError("历史数据引用了无法映射的用户")
                return user_map[old]

            # Platform models are matched by their actual provider contract.
            target_models = _rows(destination, "ai_models")
            model_identity = lambda row: (
                str(row.get("name") or "").strip().casefold(),
                str(row.get("provider_type") or "").strip(),
                str(row.get("api_base") or "").strip().rstrip("/"),
                str(row.get("model") or "").strip(),
            )
            models_by_identity = {
                model_identity(row): row for row in target_models
            }
            used_model_ids = {str(row["id"]) for row in target_models}
            model_map: dict[str, str] = {}
            for row in _rows(source, "ai_models"):
                old_id = str(row["id"])
                match = models_by_identity.get(model_identity(row))
                if match:
                    model_map[old_id] = str(match["id"])
                    counts["ai_models_mapped"] += 1
                    continue
                new_id = _unique_text_id(
                    old_id,
                    used_model_ids,
                    prefix="legacy_model",
                    fingerprint=fingerprint,
                )
                payload = {
                    **row,
                    "id": new_id,
                    "api_key_encrypted": _portable_secret(
                        row.get("api_key_encrypted")
                    ),
                }
                counts["ai_models"] += _insert(
                    destination,
                    "ai_models",
                    payload,
                    target_columns,
                )
                model_map[old_id] = new_id
                models_by_identity[model_identity(payload)] = payload

            # Accounts are canonicalized by owner + AppID.  The same public
            # account can legitimately have been configured by two customers;
            # merging only by AppID would silently transfer ownership.
            target_accounts = _rows(destination, "official_accounts")
            accounts_by_app_id = {
                (
                    str(row.get("owner_user_id") or ""),
                    str(row.get("app_id") or "").strip(),
                ): row
                for row in target_accounts
                if str(row.get("app_id") or "").strip()
            }
            used_account_ids = {str(row["id"]) for row in target_accounts}
            account_map: dict[str, str] = {}
            for row in _rows(source, "official_accounts"):
                old_id = str(row["id"])
                mapped_owner = owner_id(row.get("owner_user_id"))
                app_id = str(row.get("app_id") or "").strip()
                match = (
                    accounts_by_app_id.get((mapped_owner, app_id))
                    if app_id
                    else None
                )
                if match:
                    account_map[old_id] = str(match["id"])
                    counts["official_accounts_mapped"] += 1
                    continue
                new_id = _unique_text_id(
                    old_id,
                    used_account_ids,
                    prefix="legacy_account",
                    fingerprint=fingerprint,
                )
                payload = {
                    **row,
                    "id": new_id,
                    "owner_user_id": mapped_owner,
                    "model_id": model_map.get(
                        str(row.get("model_id") or ""),
                        str(row.get("model_id") or ""),
                    ),
                    "app_secret_encrypted": _portable_secret(
                        row.get("app_secret_encrypted")
                    ),
                }
                counts["official_accounts"] += _insert(
                    destination,
                    "official_accounts",
                    payload,
                    target_columns,
                )
                account_map[old_id] = new_id
                if app_id:
                    accounts_by_app_id[(mapped_owner, app_id)] = payload

            replacements = {**user_map, **model_map, **account_map}

            def merge_owned_named(
                table: str,
                identity_fields: tuple[str, ...],
                mapping: dict[str, str],
                prefix: str,
            ) -> None:
                target_rows = _rows(destination, table)
                used_ids = {str(row["id"]) for row in target_rows}
                by_identity = {
                    (
                        str(row.get("owner_user_id") or ""),
                        *(str(row.get(field) or "").strip().casefold() for field in identity_fields),
                    ): row
                    for row in target_rows
                }
                for row in _rows(source, table):
                    old_id = str(row["id"])
                    mapped_owner = owner_id(row.get("owner_user_id"))
                    identity = (
                        mapped_owner,
                        *(str(row.get(field) or "").strip().casefold() for field in identity_fields),
                    )
                    match = by_identity.get(identity)
                    if match:
                        mapping[old_id] = str(match["id"])
                        counts[f"{table}_mapped"] += 1
                        continue
                    new_id = _unique_text_id(
                        old_id,
                        used_ids,
                        prefix=prefix,
                        fingerprint=fingerprint,
                    )
                    payload = {
                        **row,
                        "id": new_id,
                        "owner_user_id": mapped_owner,
                    }
                    counts[table] += _insert(
                        destination,
                        table,
                        payload,
                        target_columns,
                    )
                    mapping[old_id] = new_id
                    by_identity[identity] = payload

            prompt_map: dict[str, str] = {}
            profile_map: dict[str, str] = {}
            plan_map: dict[str, str] = {}
            if "prompt_templates" in available:
                merge_owned_named(
                    "prompt_templates",
                    ("purpose", "name"),
                    prompt_map,
                    "legacy_prompt",
                )
            if "editorial_review_profiles" in available:
                merge_owned_named(
                    "editorial_review_profiles",
                    ("name",),
                    profile_map,
                    "legacy_review_profile",
                )
            if "creation_plans" in available:
                merge_owned_named(
                    "creation_plans",
                    ("name",),
                    plan_map,
                    "legacy_creation_plan",
                )
                # Fill template/profile references on newly imported plans.
                for old_id, new_id in plan_map.items():
                    source_plan = next(
                        (
                            row
                            for row in _rows(source, "creation_plans")
                            if str(row["id"]) == old_id
                        ),
                        None,
                    )
                    if not source_plan:
                        continue
                    destination.execute(
                        """
                        UPDATE creation_plans
                        SET article_prompt_template_id = ?,
                            image_prompt_template_id = ?,
                            editorial_review_profile_id = ?
                        WHERE id = ?
                        """,
                        (
                            prompt_map.get(
                                str(source_plan.get("article_prompt_template_id") or ""),
                                source_plan.get("article_prompt_template_id"),
                            ),
                            prompt_map.get(
                                str(source_plan.get("image_prompt_template_id") or ""),
                                source_plan.get("image_prompt_template_id"),
                            ),
                            profile_map.get(
                                str(source_plan.get("editorial_review_profile_id") or ""),
                                source_plan.get("editorial_review_profile_id"),
                            ),
                            new_id,
                        ),
                    )

            for table in ("ads", "processed_events"):
                if table not in available:
                    continue
                for row in _rows(source, table):
                    counts[table] += _insert(
                        destination,
                        table,
                        row,
                        target_columns,
                    )

            # Preserve batch IDs when possible; remap only genuine collisions.
            target_batches = {
                str(row["id"]): row for row in _rows(destination, "batches")
            }
            used_batch_ids = set(target_batches)
            batch_map: dict[str, str] = {}
            source_batches = _rows(source, "batches")
            for row in source_batches:
                old_id = str(row["id"])
                current = target_batches.get(old_id)
                same = bool(
                    current
                    and str(current.get("created_at") or "")
                    == str(row.get("created_at") or "")
                    and str(current.get("topic") or "")
                    == str(row.get("topic") or "")
                )
                batch_map[old_id] = (
                    old_id
                    if not current or same
                    else _unique_text_id(
                        old_id,
                        used_batch_ids,
                        prefix="legacy_batch",
                        fingerprint=fingerprint,
                    )
                )
            for row in source_batches:
                old_id = str(row["id"])
                new_id = batch_map[old_id]
                if old_id in target_batches and new_id == old_id:
                    counts["batches_mapped"] += 1
                    continue
                payload = {
                    **row,
                    "id": new_id,
                    "owner_user_id": owner_id(row.get("owner_user_id")),
                    "parent_batch_id": batch_map.get(
                        str(row.get("parent_batch_id") or ""),
                        row.get("parent_batch_id"),
                    ),
                }
                counts["batches"] += _insert(
                    destination,
                    "batches",
                    payload,
                    target_columns,
                )

            # Every SQLite job receives a fresh PostgreSQL serial ID so a
            # collision can never attach history to an unrelated newer job.
            job_map: dict[str, int] = {}
            for row in sorted(_rows(source, "jobs"), key=lambda item: int(item["id"])):
                old_id = str(row["id"])
                payload = {
                    **row,
                    "owner_user_id": owner_id(row.get("owner_user_id")),
                    "meta_json": _rewrite_json_ids(
                        row.get("meta_json"),
                        {**replacements, **batch_map},
                    ),
                }
                new_id = _insert(
                    destination,
                    "jobs",
                    payload,
                    target_columns,
                    serial=True,
                )
                job_map[old_id] = new_id
                counts["jobs"] += 1

            replacements.update(batch_map)
            replacements.update({key: str(value) for key, value in job_map.items()})

            if "batch_jobs" in available:
                for row in _rows(source, "batch_jobs"):
                    payload = {
                        **row,
                        "batch_id": batch_map[str(row["batch_id"])],
                        "job_id": job_map[str(row["job_id"])],
                        "account_id": account_map.get(
                            str(row.get("account_id") or ""),
                            row.get("account_id"),
                        ),
                    }
                    counts["batch_jobs"] += _insert(
                        destination,
                        "batch_jobs",
                        payload,
                        target_columns,
                    )

            if "bot_sessions" in available:
                for row in _rows(source, "bot_sessions"):
                    payload = {
                        **row,
                        "batch_id": batch_map.get(
                            str(row.get("batch_id") or ""),
                            row.get("batch_id"),
                        ),
                    }
                    counts["bot_sessions"] += _insert(
                        destination,
                        "bot_sessions",
                        payload,
                        target_columns,
                    )
            if "bot_contexts" in available:
                for row in _rows(source, "bot_contexts"):
                    payload = {
                        **row,
                        "context_json": _rewrite_json_ids(
                            row.get("context_json"), replacements
                        ),
                    }
                    counts["bot_contexts"] += _insert(
                        destination,
                        "bot_contexts",
                        payload,
                        target_columns,
                    )

            for row in _rows(source, "job_versions") if "job_versions" in available else []:
                payload = {**row, "job_id": job_map[str(row["job_id"])]}
                _insert(
                    destination,
                    "job_versions",
                    payload,
                    target_columns,
                    serial=True,
                )
                counts["job_versions"] += 1

            def insert_relation(table: str, transforms: dict[str, dict[str, Any]]) -> None:
                if table not in available:
                    return
                for row in _rows(source, table):
                    payload = dict(row)
                    for field, mapping in transforms.items():
                        raw = str(row.get(field) or "")
                        if raw:
                            payload[field] = mapping.get(raw, row.get(field))
                    counts[table] += _insert(
                        destination,
                        table,
                        payload,
                        target_columns,
                    )

            insert_relation(
                "account_editorial_review_defaults",
                {"account_id": account_map, "profile_id": profile_map},
            )
            insert_relation(
                "account_creation_plan_defaults",
                {"account_id": account_map, "creation_plan_id": plan_map},
            )
            insert_relation(
                "creation_plan_account_templates",
                {"account_id": account_map, "creation_plan_id": plan_map},
            )

            review_map: dict[str, str] = {}
            used_review_ids = {
                str(row["id"]) for row in _rows(destination, "editorial_reviews")
            }
            if "editorial_reviews" in available:
                for row in _rows(source, "editorial_reviews"):
                    old_id = str(row["id"])
                    new_id = _unique_text_id(
                        old_id,
                        used_review_ids,
                        prefix="legacy_review",
                        fingerprint=fingerprint,
                    )
                    payload = {
                        **row,
                        "id": new_id,
                        "batch_id": batch_map[str(row["batch_id"])],
                        "job_id": job_map[str(row["job_id"])],
                        "profile_id": profile_map.get(
                            str(row.get("profile_id") or ""),
                            row.get("profile_id"),
                        ),
                        "model_id": model_map.get(
                            str(row.get("model_id") or ""),
                            row.get("model_id"),
                        ),
                    }
                    counts["editorial_reviews"] += _insert(
                        destination,
                        "editorial_reviews",
                        payload,
                        target_columns,
                    )
                    review_map[old_id] = new_id
            insert_relation(
                "editorial_review_applications",
                {"review_id": review_map},
            )

            # Merge tenant topic sources by owner + semantic source_key while
            # keeping all historical topic_items attached through an ID map.
            topic_source_map: dict[str, str] = {}
            target_topic_sources = _rows(destination, "topic_sources")
            target_topic_by_key = {
                (
                    str(row.get("owner_user_id") or ""),
                    str(row.get("source_key") or row["id"]),
                ): row
                for row in target_topic_sources
            }
            used_topic_source_ids = {
                str(row["id"]) for row in target_topic_sources
            }
            if "topic_sources" in available:
                for row in _rows(source, "topic_sources"):
                    old_id = str(row["id"])
                    mapped_owner = owner_id(row.get("owner_user_id"))
                    source_key = str(row.get("source_key") or old_id)
                    match = target_topic_by_key.get((mapped_owner, source_key))
                    if match:
                        topic_source_map[old_id] = str(match["id"])
                        counts["topic_sources_mapped"] += 1
                        continue
                    new_id = _unique_text_id(
                        old_id,
                        used_topic_source_ids,
                        prefix="legacy_topic_source",
                        fingerprint=fingerprint,
                    )
                    payload = {
                        **row,
                        "id": new_id,
                        "owner_user_id": mapped_owner,
                        "source_key": source_key,
                    }
                    counts["topic_sources"] += _insert(
                        destination,
                        "topic_sources",
                        payload,
                        target_columns,
                    )
                    topic_source_map[old_id] = new_id
                    target_topic_by_key[(mapped_owner, source_key)] = payload

            if "topic_items" in available:
                used_topic_item_ids = {
                    str(row["id"]) for row in _rows(destination, "topic_items")
                }
                for row in _rows(source, "topic_items"):
                    old_id = str(row["id"])
                    new_id = _unique_text_id(
                        old_id,
                        used_topic_item_ids,
                        prefix="legacy_topic_item",
                        fingerprint=fingerprint,
                    )
                    payload = {
                        **row,
                        "id": new_id,
                        "source_id": topic_source_map[str(row["source_id"])],
                    }
                    counts["topic_items"] += _insert(
                        destination,
                        "topic_items",
                        payload,
                        target_columns,
                    )

            followed_map: dict[str, str] = {}
            target_followed = _rows(destination, "followed_accounts")
            followed_by_name = {
                (
                    str(row.get("owner_user_id") or ""),
                    str(row.get("name") or "").strip().casefold(),
                ): row
                for row in target_followed
            }
            used_followed_ids = {str(row["id"]) for row in target_followed}
            if "followed_accounts" in available:
                for row in _rows(source, "followed_accounts"):
                    old_id = str(row["id"])
                    mapped_owner = owner_id(row.get("owner_user_id"))
                    identity = (
                        mapped_owner,
                        str(row.get("name") or "").strip().casefold(),
                    )
                    match = followed_by_name.get(identity)
                    if match:
                        followed_map[old_id] = str(match["id"])
                        counts["followed_accounts_mapped"] += 1
                        continue
                    new_id = _unique_text_id(
                        old_id,
                        used_followed_ids,
                        prefix="legacy_followed",
                        fingerprint=fingerprint,
                    )
                    payload = {
                        **row,
                        "id": new_id,
                        "owner_user_id": mapped_owner,
                        "official_account_id": account_map.get(
                            str(row.get("official_account_id") or ""),
                            row.get("official_account_id"),
                        ),
                    }
                    counts["followed_accounts"] += _insert(
                        destination,
                        "followed_accounts",
                        payload,
                        target_columns,
                    )
                    followed_map[old_id] = new_id
                    followed_by_name[identity] = payload

            if "followed_articles" in available:
                used_article_ids = {
                    str(row["id"]) for row in _rows(destination, "followed_articles")
                }
                for row in _rows(source, "followed_articles"):
                    old_id = str(row["id"])
                    mapped_owner = owner_id(row.get("owner_user_id"))
                    old_followed_id = str(
                        row.get("followed_account_id") or ""
                    )
                    mapped_followed_id = followed_map.get(old_followed_id)
                    if not mapped_followed_id:
                        account_match = followed_by_name.get(
                            (
                                mapped_owner,
                                str(row.get("account_name") or "")
                                .strip()
                                .casefold(),
                            )
                        )
                        mapped_followed_id = str(
                            (account_match or {}).get("id") or ""
                        )
                    if not mapped_followed_id:
                        raise RuntimeError(
                            "历史关注文章无法匹配所属公众号"
                        )
                    new_id = _unique_text_id(
                        old_id,
                        used_article_ids,
                        prefix="legacy_followed_article",
                        fingerprint=fingerprint,
                    )
                    payload = {
                        **row,
                        "id": new_id,
                        "owner_user_id": mapped_owner,
                        "followed_account_id": mapped_followed_id,
                        "rewritten_batch_id": batch_map.get(
                            str(row.get("rewritten_batch_id") or ""),
                            row.get("rewritten_batch_id"),
                        ),
                    }
                    counts["followed_articles"] += _insert(
                        destination,
                        "followed_articles",
                        payload,
                        target_columns,
                    )

            if "job_attempts" in available:
                for row in _rows(source, "job_attempts"):
                    payload = {
                        **row,
                        "batch_id": batch_map[str(row["batch_id"])],
                        "job_id": job_map[str(row["job_id"])],
                        "model_id": model_map.get(
                            str(row.get("model_id") or ""),
                            row.get("model_id"),
                        ),
                    }
                    _insert(
                        destination,
                        "job_attempts",
                        payload,
                        target_columns,
                        serial=True,
                    )
                    counts["job_attempts"] += 1

            insert_relation(
                "draft_deliveries",
                {"job_id": job_map, "account_id": account_map},
            )
            insert_relation(
                "wechat_connection_health",
                {"account_id": account_map},
            )

            # Settings are imported last so all embedded IDs can be rewritten.
            if "app_settings" in available:
                for row in _rows(source, "app_settings"):
                    payload = {
                        **row,
                        "value": _rewrite_json_ids(
                            _portable_json_secrets(row.get("value")),
                            replacements,
                        ),
                    }
                    counts["app_settings"] += _insert(
                        destination,
                        "app_settings",
                        payload,
                        target_columns,
                    )
            if "user_settings" in available:
                for row in _rows(source, "user_settings"):
                    payload = {
                        **row,
                        "user_id": owner_id(row.get("user_id")),
                        "value": _rewrite_json_ids(
                            _portable_json_secrets(row.get("value")),
                            replacements,
                        ),
                    }
                    counts["user_settings"] += _insert(
                        destination,
                        "user_settings",
                        payload,
                        target_columns,
                    )

            for table in ("jobs", "job_versions", "job_attempts"):
                destination.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        EXISTS(SELECT 1 FROM {table})
                    )
                    """
                )

            orphan_checks = {
                "batch_jobs_job": "SELECT COUNT(*) AS n FROM batch_jobs bj LEFT JOIN jobs j ON j.id=bj.job_id WHERE j.id IS NULL",
                "batch_jobs_batch": "SELECT COUNT(*) AS n FROM batch_jobs bj LEFT JOIN batches b ON b.id=bj.batch_id WHERE b.id IS NULL",
                "topic_items_source": "SELECT COUNT(*) AS n FROM topic_items ti LEFT JOIN topic_sources ts ON ts.id=ti.source_id WHERE ts.id IS NULL",
                "followed_articles_account": "SELECT COUNT(*) AS n FROM followed_articles fa LEFT JOIN followed_accounts a ON a.id=fa.followed_account_id WHERE a.id IS NULL",
                "customer_owner": "SELECT COUNT(*) AS n FROM official_accounts a LEFT JOIN users u ON u.id=a.owner_user_id WHERE u.id IS NULL",
            }
            for name, sql in orphan_checks.items():
                total = int(destination.execute(sql).fetchone()["n"])
                if total:
                    raise RuntimeError(f"迁移完整性检查失败：{name}={total}")

            audit = {
                "source_sha256": fingerprint,
                "counts": dict(counts),
                "skipped_ephemeral_tables": list(SKIPPED_EPHEMERAL_TABLES),
            }
            destination.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    marker_key,
                    json.dumps(audit, ensure_ascii=False, sort_keys=True),
                    __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ).isoformat(),
                ),
            )
    finally:
        source.close()
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将历史 SQLite 数据事务化合并到 PostgreSQL"
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
    print("迁移完成（仅显示记录数量，不输出凭证）：")
    for table, count in sorted(result.items()):
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
