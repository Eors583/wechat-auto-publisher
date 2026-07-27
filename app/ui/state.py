from __future__ import annotations

import json
import logging
from typing import Any

from app.accounts import (
    DEFAULT_ACCOUNT_ID,
    ensure_account_layouts_initialized,
    ensure_config_accounts_imported,
    public_accounts,
)
from app.ai import (
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    clean_candidate_text,
)
from app.ai.model_registry import configured_models, public_models
from app.ai.openai_compat import is_junk_title_or_subtitle
from app.config import load_config
from app.db import Database
from app.pipeline import Pipeline
from app.ui.loading import DEFAULT_REQUEST_MESSAGE, get_request_loading


logger = logging.getLogger(__name__)

STATUS_LABEL = {
    "pending": "等待中",
    "ingesting": "抓取原文",
    "rewriting": "AI 改写中",
    "title_optimizing": "优化标题",
    "rendering": "排版渲染",
    "injecting": "写入草稿",
    "ready_for_review": "待选标题/预览",
    "drafted": "已写入草稿箱",
    "published": "已发布",
    "failed": "失败",
    "cancelled": "已停止",
}


def set_button_loading(
    button: Any,
    loading: bool,
    message: str = DEFAULT_REQUEST_MESSAGE,
) -> None:
    """Show button feedback and a blocking overlay for an API request."""
    if loading:
        button.props(add="loading")
        button.disable()
        get_request_loading(button, message).show(message)
    else:
        button.props(remove="loading")
        button.enable()
        overlay = getattr(button, "_request_loading_overlay", None)
        if overlay is not None:
            overlay.hide()


class AppState:
    """UI state and option providers, separate from NiceGUI view composition."""

    def __init__(self) -> None:
        self.config = load_config()
        self.db = Database(self.config["_db_path"])
        recovered = self.db.recover_stale_jobs(older_than_minutes=30)
        if recovered:
            logger.warning("Recovered %s stale in-progress jobs", recovered)
        recovered_reviews = self.db.recover_stale_editorial_reviews(
            older_than_minutes=30
        )
        if recovered_reviews:
            logger.warning(
                "Recovered %s stale editorial review operations",
                recovered_reviews,
            )
        ensure_config_accounts_imported(self.db, self.config)
        ensure_account_layouts_initialized(self.db, self.config)
        self.busy = False
        self.wizard_job_id: int | None = None
        self.selected_topic = ""
        self.topic_source = "manual"
        self.pending_rewrite: dict[str, Any] | None = None
        self.model_selects: list[Any] = []
        self.account_selects: list[Any] = []
        self.task_center_refresh: Any | None = None

    def remembered_account_ids(self) -> list[str]:
        """Return the last desktop target selection, filtered by current accounts."""
        try:
            saved = json.loads(
                self.db.get_setting("ui.last_target_account_ids") or "[]"
            )
        except (TypeError, ValueError):
            saved = []
        available = set(self.account_options())
        return [
            str(account_id)
            for account_id in saved
            if str(account_id) in available
        ]

    def remember_account_ids(self, account_ids: list[str]) -> None:
        self.db.set_setting(
            "ui.last_target_account_ids",
            json.dumps([str(item) for item in account_ids], ensure_ascii=False),
        )

    def reload_config(self) -> dict[str, Any]:
        self.config = load_config()
        self.db = Database(self.config["_db_path"])
        ensure_config_accounts_imported(self.db, self.config)
        ensure_account_layouts_initialized(self.db, self.config)
        return self.config

    def pipeline(self) -> Pipeline:
        return Pipeline(load_config())

    def model_options(self, *, include_default: bool = True) -> dict[str, str]:
        config_options = {
            str(item["id"]): (
                f'{item["name"]} · {item["model"]}'
                + ("（当前默认）" if item.get("is_default") else "（配置模型）")
            )
            for item in configured_models(self.config)
        }
        options = {
            str(item["id"]): f'{item["name"]} · {item["model"]}'
            for item in public_models(self.db, enabled_only=True, purpose="text")
        }
        options = {**config_options, **options}
        return {"": "使用系统默认模型", **options} if include_default else options

    def refresh_model_selects(self) -> None:
        for select, include_default in list(self.model_selects):
            try:
                options = self.model_options(include_default=include_default)
                select.set_options(options)
                if select.value not in options:
                    select.value = ""
            except Exception:  # noqa: BLE001
                logger.exception("refresh model selector failed")

    def account_options(self, *, include_default: bool = True) -> dict[str, str]:
        accounts = public_accounts(self.db, enabled_only=True)
        options = {
            str(item["id"]): f'{item["name"]} · {item["model_name"]}'
            for item in accounts
        }
        if include_default:
            configured_app_id = str((self.config.get("wechat") or {}).get("app_id") or "")
            imported = any(
                str(item.get("app_id") or "") == configured_app_id
                for item in accounts
            )
            if configured_app_id and not imported:
                return {DEFAULT_ACCOUNT_ID: "系统默认公众号", **options}
        return options

    def refresh_account_selects(self) -> None:
        for select, include_default in list(self.account_selects):
            try:
                options = self.account_options(include_default=include_default)
                current = [value for value in (select.value or []) if value in options]
                select.set_options(options, value=current)
            except Exception:  # noqa: BLE001
                logger.exception("refresh account selector failed")


def clean_titles(job: dict[str, Any]) -> list[str]:
    raw = list(job.get("title_candidates") or []) + list(job.get("titles") or [])
    return _clean_candidate_options(
        raw,
        limit=TITLE_CANDIDATE_COUNT,
        min_length=6,
    )


def clean_subtitles(job: dict[str, Any]) -> list[str]:
    return _clean_candidate_options(
        list(job.get("subtitles") or []),
        limit=SUBTITLE_CANDIDATE_COUNT,
        min_length=2,
    )


def _clean_candidate_options(
    raw: list[Any],
    *,
    limit: int,
    min_length: int,
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        title = clean_candidate_text(item)
        if len(title) < min_length or is_junk_title_or_subtitle(title):
            continue
        if title in seen:
            continue
        seen.add(title)
        output.append(title)
        if len(output) >= limit:
            break
    return output
