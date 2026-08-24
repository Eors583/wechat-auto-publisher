from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.accounts import (
    apply_account_selection,
    public_accounts,
    require_bound_text_model,
)
from app.ai import (
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    clean_candidate_list,
)
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import apply_model_selection, build_text_client
from app.config import database_target, load_config
from app.cover import invalidate_generated_cover
from app.db import Database, customer_data_scope
from app.inline_images import (
    invalidate_inline_image_meta,
    regenerate_inline_image_asset,
    remove_inline_image,
)
from app.pipeline import Pipeline
from app.render import TemplateRenderer, finalize_article_html
from app.services.article_revisions import (
    append_revision_event,
    preserve_inline_images_after_paragraph_revision,
    revise_paragraph,
)
from app.services.batch_contracts import (
    TERMINAL_STATUSES,
    batch_progress,
    effective_batch_status,
    public_job,
)
from app.services.batch_progress import BatchProgressMonitor
from app.services.billing import BillingService
from app.services.editorial_reviews import (
    EditorialReviewConflict,
    EditorialReviewService,
    article_snapshot,
    snapshot_fingerprint,
)
from app.services.failures import classify_job_failure, sanitize_failure_text
from app.services.job_attempts import retry_backoff_at
from app.services.model_readiness import record_model_auth_failure_for_error
from app.services.preflight import preflight_accounts
from app.services.url_validation import validate_external_url
from app.wechat.material import batch_get_material
from app.wechat.template_snapshot import load_template_snapshot

_injection_guards: dict[tuple[str, str], threading.Lock] = {}
_injection_guards_lock = threading.Lock()
_retry_guards: dict[tuple[str, int], threading.Lock] = {}
_retry_guards_lock = threading.Lock()


def _injection_guard(db_path: str, batch_id: str) -> threading.Lock:
    key = (db_path, batch_id)
    with _injection_guards_lock:
        return _injection_guards.setdefault(key, threading.Lock())


def _retry_guard(db_path: str, job_id: int) -> threading.Lock:
    key = (db_path, int(job_id))
    with _retry_guards_lock:
        return _retry_guards.setdefault(key, threading.Lock())



class BatchService:
    """Application service shared by the HTTP API and the Feishu bot."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        owner_user_id: str | None = None,
        recover_stale_work: bool = True,
    ) -> None:
        self.config = config or load_config()
        self.db = Database(
            database_target(self.config),
            owner_user_id=owner_user_id,
        )
        if recover_stale_work:
            self.db.recover_stale_jobs(older_than_minutes=30)
            self.db.recover_stale_editorial_reviews(older_than_minutes=30)
        self._cancel_events: dict[str, threading.Event] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.RLock()
        self.editorial_reviews = EditorialReviewService(self.config, self.db)
        self._progress_monitor = BatchProgressMonitor(
            self.get_batch,
            self._notify,
            interval_seconds=float(
                ((self.config.get("feishu") or {}).get("progress_interval_seconds") or 1.0)
            ),
        )

    def _for_user(self, user_id: str) -> BatchService:
        return BatchService(
            self.config,
            owner_user_id=str(user_id or "").strip(),
            recover_stale_work=False,
        )

    def list_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item["id"]),
                "name": str(item["name"]),
                "model_name": str(item.get("model_name") or ""),
                "review_priority": int(item.get("review_priority") or 0),
            }
            for item in public_accounts(self.db, enabled_only=True)
        ]

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def preflight(
        self,
        account_ids: list[str],
        *,
        deep_model_check: bool = False,
        force_wechat_check: bool = False,
    ) -> list[dict[str, Any]]:
        return preflight_accounts(
            self.db,
            account_ids,
            deep_model_check=deep_model_check,
            force_wechat_check=force_wechat_check,
        )

    def get_editorial_review_options(self) -> dict[str, Any]:
        return self.editorial_reviews.get_options()

    def list_editorial_review_profiles(
        self, *, include_builtin: bool = True
    ) -> list[dict[str, Any]]:
        return self.editorial_reviews.list_profiles(
            include_builtin=include_builtin
        )

    def save_editorial_review_profile(
        self,
        *,
        name: str,
        config: dict[str, Any],
        profile_id: str | None = None,
        description: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        return self.editorial_reviews.save_profile(
            name=name,
            config=config,
            profile_id=profile_id,
            description=description,
            enabled=enabled,
        )

    def delete_editorial_review_profile(self, profile_id: str) -> None:
        self.editorial_reviews.delete_profile(profile_id)

    def get_account_editorial_review_default(
        self, account_id: str
    ) -> dict[str, Any]:
        return self.editorial_reviews.get_account_default(account_id)

    def set_account_editorial_review_default(
        self,
        account_id: str,
        *,
        profile_id: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.editorial_reviews.set_account_default(
            account_id,
            profile_id=profile_id,
            config=config,
        )

    def run_editorial_review(
        self,
        batch_id: str,
        job_id: int,
        *,
        profile_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        return self.editorial_reviews.run_review(
            batch_id=batch_id,
            job=job,
            profile_id=profile_id,
            config=config,
        )

    def list_editorial_reviews(
        self,
        *,
        job_id: int | None = None,
        batch_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.editorial_reviews.list_reviews(
            job_id=job_id,
            batch_id=batch_id,
            limit=limit,
        )

    def get_editorial_review(self, review_id: str) -> dict[str, Any]:
        return self.editorial_reviews.get_review(review_id)

    def generate_editorial_rewrite_candidate(
        self,
        batch_id: str,
        job_id: int,
        review_id: str,
        *,
        issue_ids: list[str],
        rewrite_mode: str = "selected_issues",
        paragraph_numbers: list[int] | None = None,
        instruction: str = "",
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        return self.editorial_reviews.generate_rewrite_candidate(
            batch_id=batch_id,
            job=job,
            review_id=review_id,
            issue_ids=issue_ids,
            rewrite_mode=rewrite_mode,
            paragraph_numbers=paragraph_numbers,
            instruction=instruction,
        )

    def list_editorial_review_applications(
        self, review_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.editorial_reviews.list_applications(
            review_id, limit=limit
        )

    def get_editorial_review_application(
        self, application_id: str
    ) -> dict[str, Any]:
        return self.editorial_reviews.get_application(application_id)

    def apply_editorial_review_application(
        self,
        batch_id: str,
        job_id: int,
        application_id: str,
    ) -> dict[str, Any]:
        with self.editorial_reviews.job_operation(job_id):
            job = self._batch_job(batch_id, job_id)
            candidate = self.editorial_reviews.candidate_for_apply(
                batch_id=batch_id,
                job=job,
                application_id=application_id,
            )
            if not self.db.claim_ready_job_for_content_update(
                job_id,
                expected_content_revision=int(
                    job.get("content_revision") or 0
                ),
            ):
                raise EditorialReviewConflict(
                    "文章刚刚已被其他操作修改，请刷新审核页并重新生成候选稿"
                )
            previous_review_status = str(
                job.get("review_status") or "viewed"
            )
            source_hash = snapshot_fingerprint(article_snapshot(job))
            candidate_hash = snapshot_fingerprint(candidate)
            try:
                self.db.save_job_version(
                    job_id, reason="应用 AI 评审候选修改稿前自动保存"
                )
                title = str(candidate.get("title") or "").strip()
                body = str(candidate.get("body") or "").strip()
                if not title or not body:
                    raise ValueError("AI 候选修改稿的标题或正文为空")
                title_changed = title != str(job.get("selected_title") or "")
                body_changed = body != str(job.get("body") or "")
                meta = dict(job.get("meta") or {})
                if body_changed:
                    meta = invalidate_inline_image_meta(meta)
                thumb_media_id = job.get("thumb_media_id")
                if title_changed or body_changed:
                    meta, cleared_generated_cover = invalidate_generated_cover(
                        meta
                    )
                    if cleared_generated_cover:
                        thumb_media_id = None
                self.db.update_job(
                    job_id,
                    status="rendering",
                    step="render",
                    error=None,
                    selected_title=title,
                    selected_subtitle=(
                        str(candidate.get("subtitle") or "").strip() or None
                    ),
                    digest=str(candidate.get("digest") or "").strip(),
                    body=body,
                    html_content="",
                    thumb_media_id=thumb_media_id,
                    meta_json=meta,
                )
                account_id = str(
                    job.get("account_id")
                    or (job.get("meta") or {}).get(
                        "official_account_id"
                    )
                    or ""
                )
                cfg, _ = apply_account_selection(
                    deepcopy(self.config), self.db, account_id
                )
                self._rerender_claimed_editorial_job(
                    batch_id, job_id, cfg
                )
                self.editorial_reviews.mark_candidate_applied(application_id)
                self.db.update_batch_job_review(batch_id, job_id, "viewed")
                return self._public_job(
                    self._batch_job(batch_id, job_id),
                    include_content=True,
                )
            except Exception as exc:
                current = self.db.get_job(job_id) or {}
                current_hash = snapshot_fingerprint(article_snapshot(current))
                if current_hash in {source_hash, candidate_hash}:
                    self.db.update_job(
                        job_id,
                        status=str(
                            job.get("status") or "ready_for_review"
                        ),
                        step=str(job.get("step") or "inject"),
                        error=job.get("error"),
                        selected_title=job.get("selected_title"),
                        selected_subtitle=job.get("selected_subtitle"),
                        digest=job.get("digest"),
                        body=job.get("body"),
                        html_content=job.get("html_content"),
                        thumb_media_id=job.get("thumb_media_id"),
                        meta_json=dict(job.get("meta") or {}),
                    )
                    self.db.update_batch_job_review(
                        batch_id, job_id, previous_review_status
                    )
                self._finalize_editorial_application_after_failed_apply(
                    application_id,
                    original_content_revision=int(
                        job.get("content_revision") or 0
                    ),
                    error=sanitize_failure_text(exc),
                )
                raise

    def keep_editorial_review_source(
        self,
        batch_id: str,
        job_id: int,
        application_id: str,
    ) -> dict[str, Any]:
        """Resolve a rewrite choice without replacing the current source article."""

        with self.editorial_reviews.job_operation(job_id):
            job = self._batch_job(batch_id, job_id)
            self.editorial_reviews.keep_source_candidate(
                batch_id=batch_id,
                job=job,
                application_id=application_id,
            )
            self.db.update_batch_job_review(batch_id, job_id, "viewed")
            return self._public_job(job, include_content=True)

    def _preview_editorial_review_application(
        self,
        batch_id: str,
        job_id: int,
        application_id: str,
    ) -> str:
        """Render a candidate with the account's text styles without applying it."""

        job = self._batch_job(batch_id, job_id)
        application = self.editorial_reviews.get_application(application_id)
        review = self.editorial_reviews.get_review(
            str(application["review_id"])
        )
        if int(review["job_id"]) != int(job_id):
            raise ValueError("AI 修改稿不属于当前文章")
        candidate = dict(application.get("candidate_snapshot") or {})
        if not str(candidate.get("body") or "").strip():
            raise ValueError("AI 修改稿正文为空")
        account_id = str(
            job.get("account_id")
            or (job.get("meta") or {}).get("official_account_id")
            or ""
        )
        selected_cfg, _ = apply_account_selection(
            deepcopy(self.config), self.db, account_id
        )
        cfg = selected_cfg or deepcopy(self.config)
        editor_cfg = dict(cfg.get("editor_template") or {})
        editor_cfg["_root"] = cfg.get("_root")
        snapshot = (
            load_template_snapshot(editor_cfg)
            if editor_cfg.get("enabled", False)
            else None
        )
        generated = TemplateRenderer(cfg).render(
            body=str(candidate.get("body") or ""),
            subtitle=str(candidate.get("subtitle") or "") or None,
            show_byline=False if snapshot else None,
        )
        return finalize_article_html(
            generated,
            editor_cfg,
            snapshot=snapshot,
            load_local_snapshot=False,
        ).html

    def _rerender_claimed_editorial_job(
        self,
        batch_id: str,
        job_id: int,
        config: dict[str, Any],
    ) -> None:
        Pipeline(config, db=self.db).run_job(
            job_id, review=True, from_step="render"
        )
        refreshed = self._batch_job(batch_id, job_id)
        if str(refreshed.get("status") or "") != "ready_for_review":
            raise RuntimeError("AI 修改稿重新排版后未回到待审核状态")

    def _finalize_editorial_application_after_failed_apply(
        self,
        application_id: str,
        *,
        original_content_revision: int,
        error: str,
    ) -> None:
        try:
            application = self.editorial_reviews.get_application(
                application_id
            )
            review = self.editorial_reviews.get_review(
                str(application["review_id"])
            )
            current = self.db.get_job(int(review["job_id"])) or {}
            content_unchanged = (
                int(current.get("content_revision") or 0)
                == int(original_content_revision)
                and snapshot_fingerprint(article_snapshot(current))
                == str(application.get("source_hash") or "")
            )
            if content_unchanged:
                self.db.update_editorial_review_application(
                    application_id,
                    status="candidate_ready",
                    applied_at=None,
                    error="",
                )
                self.db.update_editorial_review(
                    str(application["review_id"]),
                    status="candidate_ready",
                    error="",
                )
                return
            message = (
                "应用修改稿失败，原稿已恢复，但内容版本已变化；"
                "为避免覆盖新内容，请重新运行 AI 评审。"
            )
            if str(error or "").strip():
                message += f" 原因：{str(error).strip()[:500]}"
            self.db.update_editorial_review_application(
                application_id,
                status="failed",
                applied_at=None,
                error=message,
            )
            self.db.update_editorial_review(
                str(application["review_id"]),
                status="stale",
                error=message,
            )
        except Exception:
            return

    def resolve_editorial_review_issue(
        self,
        review_id: str,
        issue_id: str,
        *,
        resolution: str,
        note: str = "",
        resolved_by: str = "",
    ) -> dict[str, Any]:
        return self.editorial_reviews.resolve_issue(
            review_id,
            issue_id,
            resolution=resolution,
            note=note,
            resolved_by=resolved_by,
        )

    def create_batch(
        self,
        *,
        source_url: str | None = None,
        raw_content: str | None = None,
        topic: str | None = None,
        source_mode: str | None = None,
        reference_urls: list[str] | None = None,
        required_facts: str | None = None,
        rewrite_intensity: str | None = None,
        account_ids: list[str],
        requested_by: str | None = None,
        chat_id: str | None = None,
        parent_batch_id: str | None = None,
        source_channel: str | None = None,
        source_integration_id: str | None = None,
    ) -> dict[str, Any]:
        source_url = (source_url or "").strip() or None
        raw_content = (raw_content or "").strip() or None
        references = list(
            dict.fromkeys(str(item).strip() for item in (reference_urls or []) if str(item).strip())
        )
        source_mode = str(source_mode or "").strip() or (
            "references" if references else ("link" if source_url else ("text" if raw_content else "topic"))
        )
        if source_mode == "link" and not source_url:
            raise ValueError("链接模式必须填写文章链接")
        if source_mode == "text" and not raw_content:
            raise ValueError("正文模式必须粘贴文章正文")
        if source_mode == "references" and not references:
            raise ValueError("多参考资料模式至少填写一个链接")
        if source_mode == "topic" and not str(topic or "").strip():
            raise ValueError("话题原创模式必须填写话题")
        if source_url:
            validate_external_url(source_url)
        for reference_url in references:
            validate_external_url(reference_url)
        unique_account_ids = list(dict.fromkeys(str(x).strip() for x in account_ids if str(x).strip()))
        if not unique_account_ids:
            raise ValueError("至少选择一个公众号")

        available = {item["id"]: item for item in self.list_accounts()}
        missing = [item for item in unique_account_ids if item not in available]
        if missing:
            raise ValueError("公众号不可用或已停用：" + "、".join(missing))

        batch_id = uuid.uuid4().hex[:16]
        self.db.create_batch(
            batch_id,
            source_url=source_url,
            raw_content=raw_content,
            topic=topic,
            source_mode=source_mode,
            reference_urls=references,
            required_facts=required_facts,
            rewrite_intensity=rewrite_intensity,
            requested_by=requested_by,
            chat_id=chat_id,
            parent_batch_id=parent_batch_id,
            source_integration_id=source_integration_id,
        )
        cancel_event = threading.Event()
        task_items: list[dict[str, Any]] = []
        try:
            for account_id in unique_account_ids:
                cfg, account = apply_account_selection(
                    load_config(), self.db, account_id
                )
                model_id = require_bound_text_model(account)
                pipe = Pipeline(cfg, cancel_event=cancel_event)
                job_id = self.db.create_job(
                    topic=topic,
                    source=(
                        str(source_channel or "").strip()
                        or ("feishu" if requested_by else "api")
                    ),
                    source_url=source_url,
                    raw_content=raw_content,
                    mode="draft",
                    meta={
                        "review": True,
                        "batch_id": batch_id,
                        "official_account_id": account_id,
                        "official_account_name": str(account["name"]),
                        "selected_model_id": model_id,
                        "selected_model_name": str(available[account_id].get("model_name") or ""),
                        "fallback_model_id": "",
                        "requested_by": requested_by,
                        "chat_id": chat_id,
                        "source_integration_id": str(source_integration_id or ""),
                        "source_mode": source_mode,
                        "reference_urls": references,
                        "required_facts": str(required_facts or ""),
                        "rewrite_intensity": str(rewrite_intensity or "standard"),
                    },
                )
                self.db.attach_batch_job(
                    batch_id, job_id, account_id, str(account["name"])
                )
                task_items.append({"pipe": pipe, "job_id": job_id})
        except Exception as exc:
            self.db.update_batch(
                batch_id,
                status="failed",
                error=sanitize_failure_text(exc),
            )
            raise

        with self._lock:
            self._cancel_events[batch_id] = cancel_event
        self.db.update_batch(batch_id, status="processing", error="")
        threading.Thread(
            target=self._run_generation_in_scope,
            args=(self.db.owner_user_id, batch_id, task_items),
            name=f"batch-generation-{batch_id}",
            daemon=True,
        ).start()
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str, *, include_content: bool = False) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        return self._public_batch(batch, include_content=include_content)

    def _public_batch(
        self,
        batch: dict[str, Any],
        *,
        include_content: bool = False,
    ) -> dict[str, Any]:
        batch = dict(batch)
        batch_id = str(batch.get("id") or "")
        jobs = [
            self._public_job(job, include_content=include_content)
            for job in batch.pop("jobs", [])
        ]
        batch["jobs"] = jobs
        batch["progress"] = self._progress(jobs)
        batch["status"] = effective_batch_status(jobs, str(batch.get("status") or ""))
        batch["display_id"] = str(batch.get("display_id") or batch_id)
        batch["error"] = sanitize_failure_text(batch.get("error"))
        if not batch["error"]:
            batch["error"] = None
        try:
            batch["reference_urls"] = json.loads(
                str(batch.get("reference_urls_json") or "[]")
            )
        except json.JSONDecodeError:
            batch["reference_urls"] = []
        return batch

    def list_batches(
        self, *, limit: int = 100, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        return [
            self._public_batch(batch, include_content=False)
            for batch in self.db.list_batches(
                limit=limit,
                include_archived=include_archived,
            )
        ]

    def has_active_batches(self) -> bool:
        """Return whether any non-archived batch still has active article work."""

        return self.db.has_active_batches()

    def list_review_inbox(
        self,
        *,
        bucket: str = "review",
        account_id: str | None = None,
        requested_by: str | None = None,
        chat_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List article-level operator work without loading complete batches."""

        page_size = max(1, min(int(limit), 100))
        try:
            offset = max(0, int(str(cursor or "0")))
        except ValueError as exc:
            raise ValueError("review inbox cursor is invalid") from exc
        rows = self.db.list_review_inbox_rows(
            bucket=bucket,
            account_id=account_id,
            requested_by=requested_by,
            chat_id=chat_id,
            search=search,
            limit=page_size + 1,
            offset=offset,
        )
        has_more = len(rows) > page_size
        items = [
            self._review_inbox_item(row)
            for row in rows[:page_size]
        ]
        return {
            "bucket": str(bucket or "review"),
            "counts": self.db.review_inbox_counts(
                account_id=account_id,
                requested_by=requested_by,
                chat_id=chat_id,
                search=search,
            ),
            "items": items,
            "next_cursor": str(offset + page_size) if has_more else None,
        }

    def _review_inbox_item(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            review_result = json.loads(
                str(row.get("latest_review_result_json") or "{}")
            )
        except json.JSONDecodeError:
            review_result = {}
        if not isinstance(review_result, dict):
            review_result = {}
        failure = public_job(row, include_content=False).get("failure")
        blocking_count = max(
            0, int(row.get("latest_review_blocking_count") or 0)
        )
        blockers: list[str] = []
        if blocking_count:
            blockers.append(f"AI 评审仍有 {blocking_count} 项阻断问题")
        layout_quality = dict((row.get("meta") or {}).get("layout_quality") or {})
        for error in list(layout_quality.get("errors") or []):
            text = str(error or "").strip()
            if text and text not in blockers:
                blockers.append(text)
        if failure:
            blockers.append(str(failure.get("title") or "当前步骤失败"))
        priority_bucket = int(row.get("priority_bucket") or 5)
        priority_reason = {
            1: "今天生成",
            2: "超过24小时未审核",
            3: "计划今天发布",
            4: "高优先级公众号",
            5: "普通历史任务",
        }.get(priority_bucket, "普通历史任务")
        title = str(
            row.get("selected_title")
            or row.get("raw_title")
            or row.get("topic")
            or row.get("batch_topic")
            or "尚未选择标题"
        ).strip()
        thumb_media_id = str(row.get("thumb_media_id") or "").strip()
        meta = dict(row.get("meta") or {})
        generated_cover = dict(meta.get("generated_cover") or {})
        if thumb_media_id:
            cover_status = "ready"
        elif generated_cover.get("url") or generated_cover.get("media_id"):
            cover_status = "generated"
        else:
            cover_status = "missing"
        latest_review_summary = str(
            review_result.get("conclusion")
            or review_result.get("summary")
            or ""
        ).strip()
        job_contract = public_job(row, include_content=False)
        review_status = str(job_contract.get("review_status") or "")
        status = str(row.get("status") or "")
        step = str(row.get("step") or "")
        if status == "ready_for_review" and review_status == "confirmed":
            recommended_action = "打开所在批次并写入公众号草稿箱"
        elif status == "ready_for_review":
            recommended_action = (
                "继续修改并重新确认文章"
                if review_status == "needs_changes"
                else "打开快速审核并确认文章"
            )
        elif status == "failed" and step == "inject":
            recommended_action = "检查公众号连接后，仅重试写入草稿箱"
        elif status == "failed" and step == "ingest":
            recommended_action = "替换真实文章链接或粘贴正文后，仅重试抓取"
        elif status == "failed" and step in {"rewriting", "rewrite"}:
            recommended_action = "更换备用模型或仅重新生成正文"
        elif status == "failed" and step in {"title", "title_optimize"}:
            recommended_action = "保留正文，仅重新生成标题"
        elif status == "failed" and step in {"render", "rendering"}:
            recommended_action = "检查模板后，仅重新排版"
        elif status == "failed" and step in {"images", "image"}:
            recommended_action = "仅重新生成失败图片"
        elif status in {"drafted", "published"}:
            recommended_action = "查看公众号草稿箱中的最终成品"
        elif status == "failed":
            recommended_action = "按处理建议从失败步骤继续重试"
        else:
            recommended_action = "查看文章详情"
        return {
            "batch_id": str(row.get("batch_id") or ""),
            "batch_display_id": str(
                row.get("batch_display_id") or row.get("batch_id") or ""
            ),
            "batch_topic": str(row.get("batch_topic") or ""),
            "source_url": str(row.get("batch_source_url") or row.get("source_url") or ""),
            "source_mode": str(row.get("batch_source_mode") or ""),
            "job_id": int(row["id"]),
            "account_id": str(row.get("account_id") or ""),
            "account_name": str(row.get("account_name") or ""),
            "title": title,
            "source": str(
                row.get("batch_topic")
                or row.get("source")
                or row.get("batch_source_url")
                or ""
            ),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "scheduled_at": row.get("scheduled_at"),
            "body_chars": len(str(row.get("body") or "")),
            "status": status,
            "step": step,
            "review_status": review_status,
            "latest_review_summary": latest_review_summary,
            "review_score": review_result.get("overall_score"),
            "review_blocking_count": blocking_count,
            "cover_status": cover_status,
            "blockers": blockers,
            "priority_reason": priority_reason,
            "recommended_action": recommended_action,
            "review_priority": int(row.get("review_priority") or 0),
            "failure": failure,
            "review_url": job_contract.get("review_url"),
            "job": job_contract,
        }

    def select_job(
        self,
        batch_id: str,
        job_id: int,
        *,
        title_index: int,
        subtitle_index: int | None = None,
    ) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        job = next((item for item in batch["jobs"] if int(item["id"]) == int(job_id)), None)
        if not job:
            raise KeyError(f"任务不属于该批次：{job_id}")
        if job.get("status") != "ready_for_review":
            raise ValueError("任务尚未进入待审核状态")
        titles = clean_candidate_list(
            list(job.get("title_candidates") or job.get("titles") or []),
            limit=TITLE_CANDIDATE_COUNT,
        )
        if not titles:
            raise ValueError("任务没有可选标题")
        if title_index < 0 or title_index >= len(titles):
            raise ValueError(f"title_index 应在 0 到 {len(titles) - 1} 之间")
        subtitles = clean_candidate_list(
            list(job.get("subtitles") or []),
            limit=SUBTITLE_CANDIDATE_COUNT,
        )
        if subtitle_index is not None and (
            subtitle_index < 0 or subtitle_index >= len(subtitles)
        ):
            raise ValueError(f"subtitle_index 应在 0 到 {max(len(subtitles) - 1, 0)} 之间")
        meta, cleared_generated_cover = invalidate_generated_cover(job.get("meta"))
        updates: dict[str, Any] = {
            "selected_title": str(titles[title_index]),
            "selected_subtitle": (
                str(subtitles[subtitle_index]) if subtitle_index is not None else None
            ),
        }
        if cleared_generated_cover:
            updates["thumb_media_id"] = None
            updates["meta_json"] = meta
        self.db.update_job(
            int(job_id),
            **updates,
        )
        self.db.update_batch_job_review(batch_id, int(job_id), "viewed")
        return self._public_job(
            self._batch_job(batch_id, int(job_id)), include_content=True
        )

    def mark_job_viewed(self, batch_id: str, job_id: int) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以进入审核工作台")
        if str(job.get("review_status") or "unviewed") == "unviewed":
            self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def confirm_job(self, batch_id: str, job_id: int) -> dict[str, Any]:
        with self.editorial_reviews.job_operation(job_id):
            job = self._batch_job(batch_id, job_id)
            if job.get("status") != "ready_for_review":
                raise ValueError("只有待审核文章可以确认")
            review_status = str(job.get("review_status") or "unviewed")
            if review_status == "confirmed":
                return self._public_job(job, include_content=True)
            if review_status not in {"viewed", "needs_changes"}:
                raise ValueError("请先打开并查看文章，确认内容无误后再确认")
            if not str(job.get("selected_title") or "").strip():
                raise ValueError("请先选择或填写文章标题")
            self.editorial_reviews.assert_job_may_confirm(job)
            self.db.update_batch_job_review(
                batch_id, job_id, "confirmed"
            )
            result = self._public_job(
                self._batch_job(batch_id, job_id), include_content=True
            )
            self._notify(self.get_batch(batch_id, include_content=True))
            return result

    def request_job_changes(self, batch_id: str, job_id: int) -> dict[str, Any]:
        with self.editorial_reviews.job_operation(job_id):
            job = self._batch_job(batch_id, job_id)
            if job.get("status") != "ready_for_review":
                raise ValueError("只有待审核文章可以标记为需要修改")
            self.db.update_batch_job_review(
                batch_id, job_id, "needs_changes"
            )
            result = self._public_job(
                self._batch_job(batch_id, job_id), include_content=True
            )
            self._notify(self.get_batch(batch_id, include_content=True))
            return result

    def update_job_content(
        self,
        batch_id: str,
        job_id: int,
        *,
        title: str | None = None,
        subtitle: str | None = None,
        body: str | None = None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        with self.editorial_reviews.job_operation(job_id):
            return self._update_job_content_locked(
                batch_id,
                job_id,
                title=title,
                subtitle=subtitle,
                body=body,
                digest=digest,
            )

    def _update_job_content_locked(
        self,
        batch_id: str,
        job_id: int,
        *,
        title: str | None = None,
        subtitle: str | None = None,
        body: str | None = None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以修改")
        updates: dict[str, Any] = {}
        title_changed = False
        body_changed = False
        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                raise ValueError("文章标题不能为空")
            title_changed = clean_title != str(job.get("selected_title") or "")
            if title_changed:
                updates["selected_title"] = clean_title
        if subtitle is not None:
            clean_subtitle = subtitle.strip() or None
            if clean_subtitle != (job.get("selected_subtitle") or None):
                updates["selected_subtitle"] = clean_subtitle
        if digest is not None:
            clean_digest = digest.strip()
            if clean_digest != str(job.get("digest") or ""):
                updates["digest"] = clean_digest
        if body is not None:
            clean_body = body.strip()
            if not clean_body:
                raise ValueError("文章正文不能为空")
            body_changed = clean_body != str(job.get("body") or "")
            if body_changed:
                updates["body"] = clean_body
                updates["html_content"] = ""
                updates["meta_json"] = invalidate_inline_image_meta(job.get("meta"))
        if title_changed or body_changed:
            meta, cleared_generated_cover = invalidate_generated_cover(
                updates.get("meta_json") or job.get("meta")
            )
            updates["meta_json"] = meta
            if cleared_generated_cover:
                updates["thumb_media_id"] = None
        if updates:
            if not self.db.claim_ready_job_for_content_update(
                job_id,
                expected_content_revision=int(
                    job.get("content_revision") or 0
                ),
            ):
                raise ValueError(
                    "文章刚刚已被其他操作修改，请刷新审核页后重试"
                )
            try:
                self.db.save_job_version(
                    job_id, reason="运营编辑前自动保存"
                )
                updates.update(
                    {
                        "status": "ready_for_review",
                        "step": str(job.get("step") or "inject"),
                        "error": job.get("error"),
                    }
                )
                self.db.update_job(job_id, **updates)
            except Exception:
                self.db.update_job(
                    job_id,
                    status=str(
                        job.get("status") or "ready_for_review"
                    ),
                    step=str(job.get("step") or "inject"),
                    error=job.get("error"),
                )
                raise
        self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def move_paragraph(
        self,
        batch_id: str,
        job_id: int,
        paragraph_index: int,
        target_index: int,
    ) -> dict[str, Any]:
        """Move one plain-text paragraph and rebuild the reviewed article."""

        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以移动段落")
        paragraphs = _paragraphs(str(job.get("body") or ""))
        _validate_paragraph_index(paragraphs, paragraph_index)
        _validate_paragraph_index(paragraphs, target_index, label="目标段落")
        if paragraph_index == target_index:
            raise ValueError("原段落和目标位置不能相同")

        paragraph = paragraphs.pop(paragraph_index)
        paragraphs.insert(target_index, paragraph)
        self.update_job_content(
            batch_id,
            job_id,
            body="\n\n".join(paragraphs),
        )
        return self.rerender_job(batch_id, job_id)

    def delete_paragraph(
        self,
        batch_id: str,
        job_id: int,
        paragraph_index: int,
    ) -> dict[str, Any]:
        """Delete one plain-text paragraph and rebuild the reviewed article."""

        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以删除段落")
        paragraphs = _paragraphs(str(job.get("body") or ""))
        _validate_paragraph_index(paragraphs, paragraph_index)
        if len(paragraphs) <= 1:
            raise ValueError("文章至少需要保留一个正文段落")

        paragraphs.pop(paragraph_index)
        self.update_job_content(
            batch_id,
            job_id,
            body="\n\n".join(paragraphs),
        )
        return self.rerender_job(batch_id, job_id)

    def list_job_versions(self, batch_id: str, job_id: int) -> list[dict[str, Any]]:
        self._batch_job(batch_id, job_id)
        versions: list[dict[str, Any]] = []
        for item in self.db.list_job_versions(job_id):
            public = dict(item)
            public["has_visual_snapshot"] = bool(
                public.get("html_content") or public.get("meta_json")
            )
            public.pop("html_content", None)
            public.pop("meta_json", None)
            versions.append(public)
        return versions

    def restore_job_version(
        self, batch_id: str, job_id: int, version_id: int
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        version = self.db.get_job_version(job_id, version_id)
        if not version:
            raise ValueError("文章历史版本不存在")
        self.db.save_job_version(job_id, reason="恢复历史版本前自动保存")
        saved_meta_raw = str(version.get("meta_json") or "").strip()
        if saved_meta_raw:
            try:
                restored_meta = json.loads(saved_meta_raw)
            except json.JSONDecodeError:
                restored_meta = invalidate_inline_image_meta(job.get("meta"))
        else:
            restored_meta = invalidate_inline_image_meta(job.get("meta"))
        if not isinstance(restored_meta, dict):
            restored_meta = invalidate_inline_image_meta(job.get("meta"))
        has_visual_snapshot = bool(saved_meta_raw or version.get("html_content"))
        if has_visual_snapshot:
            restored_thumb = version.get("thumb_media_id")
        else:
            restored_meta, cleared_generated_cover = invalidate_generated_cover(restored_meta)
            restored_thumb = None if cleared_generated_cover else job.get("thumb_media_id")
        self.db.update_job(
            job_id,
            selected_title=version.get("title"),
            selected_subtitle=version.get("subtitle"),
            digest=version.get("digest"),
            body=version.get("body"),
            html_content=str(version.get("html_content") or "") if has_visual_snapshot else "",
            thumb_media_id=restored_thumb,
            meta_json=restored_meta,
        )
        self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def rerender_pending_account_jobs(self, account_id: str) -> dict[str, Any]:
        """Apply current layout to every unconfirmed review job for one account."""

        clean_account_id = str(account_id or "").strip()
        if not clean_account_id:
            raise ValueError("公众号 ID 不能为空")
        jobs: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = self.list_review_inbox(
                bucket="review",
                account_id=clean_account_id,
                limit=100,
                cursor=cursor,
            )
            jobs.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                break

        failures: list[dict[str, Any]] = []
        rerendered = 0
        for job in jobs:
            try:
                self.rerender_job(
                    str(job["batch_id"]),
                    int(job["job_id"]),
                    mark_viewed=False,
                )
                rerendered += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    {
                        "job_id": int(job["job_id"]),
                        "reason": sanitize_failure_text(exc),
                    }
                )
        return {
            "account_id": clean_account_id,
            "requested": len(jobs),
            "rerendered": rerendered,
            "failed": len(failures),
            "failures": failures,
        }

    def rerender_job(
        self,
        batch_id: str,
        job_id: int,
        *,
        mark_viewed: bool = True,
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以重新排版")
        account_id = str(
            job.get("account_id")
            or (job.get("meta") or {}).get("official_account_id")
            or ""
        )
        cfg, _ = apply_account_selection(load_config(), self.db, account_id)
        Pipeline(cfg, db=self.db).run_job(
            job_id,
            review=True,
            from_step="render",
        )
        if mark_viewed:
            self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def regenerate_inline_images(
        self,
        batch_id: str,
        job_id: int,
        *,
        _retry_owned: bool = False,
    ) -> dict[str, Any]:
        """Regenerate all argument images using the account's current image agent."""
        job = self._batch_job(batch_id, job_id)
        if _retry_owned:
            if (
                str(job.get("status") or "") != "rendering"
                or str(job.get("step") or "") != "images"
            ):
                raise ValueError("正文配图恢复任务未持有该文章的执行权。")
        elif job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以生成或重新生成正文配图")
        account_id = str(
            job.get("account_id")
            or (job.get("meta") or {}).get("official_account_id")
            or ""
        )
        cfg, _ = apply_account_selection(load_config(), self.db, account_id)
        settings = dict(cfg.get("inline_images") or {})
        if not settings.get("enabled"):
            raise ValueError("该公众号尚未启用正文生图，请先到公众号管理 → 生图配置中启用")
        model_id = str(settings.get("image_model_id") or "")
        model = self.db.get_ai_model(model_id) if model_id else None
        if (
            settings.get("source_mode") in {"generate", "hybrid"}
            and (
                not model
                or not bool(model.get("enabled"))
                or not is_image_provider(model.get("provider_type"))
            )
        ):
            raise ValueError("该公众号绑定的生图智能体不存在或已停用")
        previous_review_status = str(job.get("review_status") or "viewed")
        if not _retry_owned:
            self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        try:
            self.db.save_job_version(job_id, reason="重新生成全部正文配图前自动保存")
            self.db.update_job(
                job_id,
                status="rendering",
                step="images",
                error=None,
                html_content="",
                meta_json=invalidate_inline_image_meta(job.get("meta")),
            )
            with BillingService(self.db).operation(
                scene="inline_images_regeneration",
                subject_type="job",
                subject_id=str(job_id),
                source_channel="service",
                idempotency_key=f"inline-images:{job_id}:{uuid.uuid4().hex}",
                job_id=job_id,
            ):
                Pipeline(cfg, db=self.db).run_job(
                    job_id,
                    review=True,
                    from_step="render",
                    attempt_stage_overrides={"render": "images"},
                    attempt_model_ids={"images": model_id},
                )
            refreshed = self._batch_job(batch_id, job_id)
            generated = list((refreshed.get("meta") or {}).get("inline_images") or [])
            if not generated:
                warnings = list(
                    (refreshed.get("meta") or {}).get("inline_image_warnings") or []
                )
                raise RuntimeError(
                    "；".join(str(item) for item in warnings if str(item).strip())
                    or "生图智能体没有返回可用的正文配图"
                )
        except Exception:
            self.db.update_job(
                job_id,
                status=str(job.get("status") or "ready_for_review"),
                step=str(job.get("step") or "inject"),
                error=job.get("error"),
                html_content=str(job.get("html_content") or ""),
                thumb_media_id=job.get("thumb_media_id"),
                meta_json=dict(job.get("meta") or {}),
            )
            if not _retry_owned:
                self.db.update_batch_job_review(
                    batch_id, job_id, previous_review_status
                )
            raise
        if not _retry_owned:
            self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def regenerate_inline_image(
        self,
        batch_id: str,
        job_id: int,
        image_index: int,
        *,
        instruction: str,
        _retry_owned: bool = False,
    ) -> dict[str, Any]:
        """Regenerate one reviewed argument image from an operator instruction."""

        job = self._batch_job(batch_id, job_id)
        if _retry_owned:
            if (
                str(job.get("status") or "") != "rendering"
                or str(job.get("step") or "") != "images"
            ):
                raise ValueError("正文配图恢复任务未持有该文章的执行权。")
        elif job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以定向修改正文配图")
        request = str(instruction or "").strip()
        if not request:
            raise ValueError("请先填写这张图片的修改要求")
        assets = list((job.get("meta") or {}).get("inline_images") or [])
        selected_position = next(
            (
                position
                for position, item in enumerate(assets)
                if int(item.get("index") or item.get("image_index") or 0)
                == int(image_index)
            ),
            None,
        )
        if selected_position is None:
            raise ValueError("所选正文配图不存在")

        account_id = str(
            job.get("account_id")
            or (job.get("meta") or {}).get("official_account_id")
            or ""
        )
        cfg, _ = apply_account_selection(load_config(), self.db, account_id)
        settings = dict(cfg.get("inline_images") or {})
        model_id = str(settings.get("image_model_id") or "")
        model = self.db.get_ai_model(model_id) if model_id else None
        if (
            not model
            or not bool(model.get("enabled"))
            or not is_image_provider(model.get("provider_type"))
        ):
            raise ValueError("该公众号绑定的生图智能体不存在、已停用或不是图片模型")

        previous_review_status = str(job.get("review_status") or "viewed")
        if not _retry_owned:
            self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        self.db.update_job(job_id, status="rendering", step="images", error=None)
        try:
            pipeline = Pipeline(cfg, db=self.db)
            with BillingService(self.db).operation(
                scene="inline_image_regeneration",
                subject_type="job",
                subject_id=f"{job_id}:{int(image_index)}",
                source_channel="service",
                idempotency_key=f"inline-image:{job_id}:{int(image_index)}:{uuid.uuid4().hex}",
                job_id=job_id,
            ):
                replacement = regenerate_inline_image_asset(
                    asset=assets[selected_position],
                    instruction=request,
                    article_title=str(job.get("selected_title") or job.get("topic") or ""),
                    model=model,
                    client=pipeline._wechat_client(),
                    root=cfg.get("_root") or ".",
                    job_id=job_id,
                )
            updated_assets = [dict(item) for item in assets]
            updated_assets[selected_position] = replacement
            meta = dict(job.get("meta") or {})
            meta["inline_images"] = updated_assets
            meta["inline_images_resolved"] = True
            meta = append_revision_event(
                meta,
                kind="inline_image",
                instruction=request,
                target=int(image_index),
            )
            self.db.save_job_version(
                job_id,
                reason=f"AI 二次修改正文配图 {int(image_index)} 前自动保存",
            )
            self.db.update_job(job_id, html_content="", meta_json=meta)
            pipeline.run_job(
                job_id,
                review=True,
                from_step="render",
                attempt_stage_overrides={"render": "images"},
                attempt_model_ids={"images": model_id},
            )
        except Exception:
            self.db.update_job(
                job_id,
                status=str(job.get("status") or "ready_for_review"),
                step=str(job.get("step") or "inject"),
                error=job.get("error"),
                html_content=str(job.get("html_content") or ""),
                thumb_media_id=job.get("thumb_media_id"),
                meta_json=dict(job.get("meta") or {}),
            )
            if not _retry_owned:
                self.db.update_batch_job_review(
                    batch_id, job_id, previous_review_status
                )
            raise
        if not _retry_owned:
            self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def remove_inline_image(
        self, batch_id: str, job_id: int, image_index: int
    ) -> dict[str, Any]:
        """Remove one reviewed inline image while preserving all article text."""

        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以删除正文配图")
        html_content = str(job.get("html_content") or "")
        if not html_content:
            raise ValueError("文章尚未完成排版，无法删除正文配图")
        assets = list((job.get("meta") or {}).get("inline_images") or [])
        matching = [
            item
            for item in assets
            if int(item.get("index") or item.get("image_index") or 0)
            == int(image_index)
        ]
        updated_html = remove_inline_image(html_content, int(image_index))
        if updated_html == html_content and not matching:
            raise ValueError("所选正文配图不存在")
        meta = dict(job.get("meta") or {})
        meta["inline_images"] = [
            item
            for item in assets
            if int(item.get("index") or item.get("image_index") or 0)
            != int(image_index)
        ]
        quality = dict(meta.get("layout_quality") or {})
        if quality:
            quality["image_count"] = updated_html.lower().count("<img")
            meta["layout_quality"] = quality
        self.db.save_job_version(
            job_id, reason=f"删除正文配图 {int(image_index)} 前自动保存"
        )
        self.db.update_job(job_id, html_content=updated_html, meta_json=meta)
        self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def regenerate_cover(
        self,
        batch_id: str,
        job_id: int,
        *,
        instruction: str = "",
    ) -> dict[str, Any]:
        """Generate a new article-aware cover using the account image agent."""
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以生成或重新生成封面")
        account_id = str(
            job.get("account_id")
            or (job.get("meta") or {}).get("official_account_id")
            or ""
        )
        cfg, _ = apply_account_selection(load_config(), self.db, account_id)
        settings = dict(cfg.get("inline_images") or {})
        model_id = str(settings.get("image_model_id") or "")
        model = self.db.get_ai_model(model_id) if model_id else None
        if not bool(settings.get("generate_cover", True)):
            raise ValueError("该公众号尚未启用 AI 封面主图")
        if (
            not model
            or not bool(model.get("enabled"))
            or not is_image_provider(model.get("provider_type"))
        ):
            raise ValueError("该公众号绑定的生图智能体不存在或已停用")

        request = str(instruction or "").strip()
        if len(request) > 2000:
            raise ValueError("封面修改要求不能超过 2000 字")
        meta = dict(job.get("meta") or {})
        meta.pop("generated_cover", None)
        meta.pop("cover_image_warning", None)
        meta["generated_cover_active"] = False
        if request:
            meta["cover_revision_instruction"] = request
            meta = append_revision_event(
                meta,
                kind="cover",
                instruction=request,
                target=0,
            )
        else:
            meta.pop("cover_revision_instruction", None)
        previous_review_status = str(job.get("review_status") or "viewed")
        self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        try:
            self.db.save_job_version(job_id, reason="重新生成 AI 封面前自动保存")
            self.db.update_job(
                job_id,
                status="rendering",
                step="render",
                error=None,
                thumb_media_id=None,
                meta_json=meta,
            )
            with BillingService(self.db).operation(
                scene="cover_regeneration",
                subject_type="job",
                subject_id=str(job_id),
                source_channel="service",
                idempotency_key=f"cover:{job_id}:{uuid.uuid4().hex}",
                job_id=job_id,
            ):
                Pipeline(cfg, db=self.db).run_job(
                    job_id, review=True, from_step="render"
                )
            refreshed = self._batch_job(batch_id, job_id)
            refreshed_meta = dict(refreshed.get("meta") or {})
            if not (
                refreshed_meta.get("generated_cover_active")
                and refreshed_meta.get("generated_cover")
            ):
                raise RuntimeError(
                    str(refreshed_meta.get("cover_image_warning") or "")
                    or "生图智能体没有返回可用封面"
                )
        except Exception:
            self.db.update_job(
                job_id,
                status=str(job.get("status") or "ready_for_review"),
                step=str(job.get("step") or "inject"),
                error=job.get("error"),
                html_content=str(job.get("html_content") or ""),
                thumb_media_id=job.get("thumb_media_id"),
                meta_json=dict(job.get("meta") or {}),
            )
            self.db.update_batch_job_review(
                batch_id, job_id, previous_review_status
            )
            raise
        self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def list_cover_options(
        self,
        batch_id: str,
        job_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, str]]:
        job = self._batch_job(batch_id, job_id)
        account_id = str(job.get("account_id") or "")
        cfg, _ = apply_account_selection(
            load_config(), self.db, account_id, allow_disabled=True
        )
        client = Pipeline(cfg, db=self.db)._wechat_client()
        items: list[dict[str, str]] = []
        material_offset = max(0, int(offset))
        while len(items) < max(1, limit):
            data = batch_get_material(
                client,
                material_type="image",
                offset=material_offset,
                count=min(20, limit - len(items)),
            )
            rows = list(data.get("item") or [])
            for row in rows:
                media_id = str(row.get("media_id") or "")
                if media_id:
                    items.append(
                        {
                            "media_id": media_id,
                            "name": str(row.get("name") or f"图片 {len(items) + 1}"),
                            "url": str(row.get("url") or ""),
                        }
                    )
            material_offset += len(rows)
            if not rows or material_offset >= int(data.get("total_count") or 0):
                break
        return items

    def select_job_cover(
        self, batch_id: str, job_id: int, media_id: str
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以修改封面")
        if not media_id.strip():
            raise ValueError("请选择封面素材")
        meta = dict(job.get("meta") or {})
        meta["generated_cover_active"] = False
        meta.pop("cover_image_warning", None)
        self.db.update_job(
            job_id,
            thumb_media_id=media_id.strip(),
            meta_json=meta,
        )
        self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def regenerate_paragraph(
        self,
        batch_id: str,
        job_id: int,
        paragraph_index: int,
        *,
        instruction: str = "",
    ) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以重新生成段落")
        account_id = str(job.get("account_id") or "")
        cfg, account = apply_account_selection(load_config(), self.db, account_id)
        model_id = require_bound_text_model(account)
        client = build_text_client(self.db, cfg, model_id)
        previous_review_status = str(job.get("review_status") or "viewed")
        self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        self.db.update_job(job_id, status="rewriting", step="rewrite", error=None)
        try:
            with BillingService(self.db).operation(
                scene="paragraph_regeneration",
                subject_type="job",
                subject_id=f"{job_id}:{int(paragraph_index)}",
                source_channel="service",
                idempotency_key=f"paragraph:{job_id}:{int(paragraph_index)}:{uuid.uuid4().hex}",
                job_id=job_id,
            ):
                revision = revise_paragraph(
                    client,
                    body=str(job.get("body") or ""),
                    paragraph_index=paragraph_index,
                    instruction=instruction,
                    title=str(job.get("selected_title") or ""),
                    topic=str(job.get("topic") or ""),
                    article_instruction=str(
                        (cfg.get("ai") or {}).get("rewrite_prompt") or ""
                    ),
                )
            meta = preserve_inline_images_after_paragraph_revision(
                job.get("meta"),
                original=revision.original,
                replacement=revision.replacement,
            )
            meta = append_revision_event(
                meta,
                kind="paragraph",
                instruction=instruction,
                target=paragraph_index + 1,
            )
            self.db.save_job_version(
                job_id, reason=f"AI 二次改写第 {paragraph_index + 1} 段前自动保存"
            )
            self.db.update_job(
                job_id,
                status="ready_for_review",
                step="inject",
                body=revision.body,
                html_content="",
                meta_json=meta,
            )
            return self.rerender_job(batch_id, job_id)
        except Exception as exc:
            record_model_auth_failure_for_error(
                self.db,
                cfg,
                model_id,
                exc,
            )
            self.db.update_job(
                job_id,
                status=str(job.get("status") or "ready_for_review"),
                step=str(job.get("step") or "inject"),
                error=job.get("error"),
                body=str(job.get("body") or ""),
                html_content=str(job.get("html_content") or ""),
                thumb_media_id=job.get("thumb_media_id"),
                meta_json=dict(job.get("meta") or {}),
            )
            self.db.update_batch_job_review(
                batch_id, job_id, previous_review_status
            )
            raise

    def inject_batch(self, batch_id: str) -> dict[str, Any]:
        """Write one confirmed batch once, even when multiple UI/API calls race."""

        guard = _injection_guard(str(self.config["_db_path"]), batch_id)
        if not guard.acquire(blocking=False):
            raise ValueError("该批次正在写入草稿箱，请勿重复操作")
        try:
            return self._inject_batch_locked(batch_id)
        finally:
            guard.release()

    def _inject_batch_locked(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        jobs = list(batch["jobs"])
        active_statuses = {
            "pending",
            "ingesting",
            "rewriting",
            "title_optimizing",
            "rendering",
            "injecting",
        }
        active_jobs = [
            job for job in jobs if str(job.get("status") or "") in active_statuses
        ]
        if active_jobs:
            names = "、".join(
                str(job.get("account_name") or "公众号") for job in active_jobs
            )
            raise ValueError(
                f"批次仍有 {len(active_jobs)} 篇文章正在生成、二次修改或写入：{names}。"
                "请等待当前操作完成后再全部写入。"
            )
        ready_jobs = [job for job in jobs if job.get("status") == "ready_for_review"]
        if not ready_jobs:
            raise ValueError("当前批次没有可写入的待审核文章")
        unconfirmed = [
            job
            for job in ready_jobs
            if str(job.get("review_status") or "unviewed") != "confirmed"
        ]
        if unconfirmed:
            names = "、".join(str(job.get("account_name") or "") for job in unconfirmed)
            raise ValueError(
                f"还有 {len(unconfirmed)} 篇文章未显式确认：{names}。"
                "请逐篇点击“确认此文章”后再写入。"
            )
        for job in ready_jobs:
            self.editorial_reviews.assert_job_may_confirm(job)
        account_ids = [
            str(job.get("account_id") or "")
            for job in ready_jobs
            if str(job.get("account_id") or "")
            and self.db.get_official_account(str(job.get("account_id") or ""))
        ]
        jobs_by_account: dict[str, list[dict[str, Any]]] = {}
        for job in ready_jobs:
            account_id = str(job.get("account_id") or "")
            if account_id in account_ids:
                jobs_by_account.setdefault(account_id, []).append(job)
        reports = {
            str(report.get("account_id") or ""): report
            for report in preflight_accounts(
                self.db,
                account_ids,
                deep_model_check=False,
                force_wechat_check=False,
                jobs_by_account=jobs_by_account,
            )
        }
        writable_jobs: list[dict[str, Any]] = []
        for job in ready_jobs:
            account_id = str(job.get("account_id") or "")
            if account_id not in reports:
                # Legacy/config-only jobs have no managed account row and keep
                # using the compatibility delivery path.
                writable_jobs.append(job)
                continue
            report = reports[account_id]
            preflight_attempt = self.db.create_job_attempt(
                batch_id=batch_id,
                job_id=int(job["id"]),
                stage="preflight",
                model_id=str(
                    (job.get("meta") or {}).get("selected_model_id") or ""
                )
                or None,
            )
            if bool(report.get("can_write")):
                self.db.finish_job_attempt(
                    int(preflight_attempt["id"]), status="succeeded"
                )
                writable_jobs.append(job)
                continue
            messages = [
                str(check.get("message") or "").strip()
                for check in list(report.get("checks") or [])
                if str(check.get("key") or "")
                in {"wechat", "draft", "template", "cover"}
                and not bool(check.get("ok"))
            ]
            message = "；".join(item for item in messages if item) or (
                "公众号写入前连接检查未通过"
            )
            failure = classify_job_failure(
                message, step="inject", status="failed"
            )
            next_retry_at = retry_backoff_at(
                failure,
                attempt_no=int(preflight_attempt.get("attempt_no") or 1),
                error=message,
            )
            self.db.finish_job_attempt(
                int(preflight_attempt["id"]),
                status="failed",
                error_code=str((failure or {}).get("code") or ""),
                error=sanitize_failure_text(message),
                next_retry_at=next_retry_at,
            )
            self.db.update_job(
                int(job["id"]),
                status="failed",
                step="inject",
                error=sanitize_failure_text(message),
            )
        ready_jobs = writable_jobs
        if not ready_jobs:
            self.db.update_batch(batch_id, status="partial_failed")
            result = self.get_batch(batch_id, include_content=True)
            self._notify(result)
            return result
        self.db.update_batch(batch_id, status="injecting", error="")
        owner_user_id = self.db.owner_user_id

        def inject_one(job: dict[str, Any]) -> None:
            with customer_data_scope(owner_user_id):
                try:
                    meta = dict(job.get("meta") or {})
                    cfg, _ = apply_account_selection(
                        load_config(),
                        self.db,
                        str(meta["official_account_id"]),
                        allow_disabled=True,
                    )
                    # The reviewer has already persisted the exact title
                    # (including manual edits). Do not remap it here.
                    try:
                        pipeline = Pipeline(cfg, db=self.db)
                    except TypeError as exc:
                        if "db" not in str(exc):
                            raise
                        pipeline = Pipeline(cfg)
                    pipeline.review_and_inject(int(job["id"]))
                except Exception as exc:  # noqa: BLE001
                    self.db.update_job(
                        int(job["id"]),
                        status="failed",
                        step="inject",
                        error=sanitize_failure_text(exc),
                    )

        threads = [
            threading.Thread(target=inject_one, args=(job,), daemon=True)
            for job in ready_jobs
        ]
        for thread in threads:
            thread.start()
        self._progress_monitor.watch(batch_id, threads)
        refreshed = self.db.get_batch(batch_id) or {"jobs": []}
        statuses = [str(job.get("status")) for job in refreshed["jobs"]]
        status = "drafted" if statuses and all(item == "drafted" for item in statuses) else "partial_failed"
        self.db.update_batch(batch_id, status=status)
        result = self.get_batch(batch_id, include_content=True)
        self._notify(result)
        return result

    def list_job_attempts(
        self,
        batch_id: str,
        job_id: int,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self._batch_job(batch_id, job_id)
        attempts = self.db.list_job_attempts(job_id, limit=limit)
        for attempt in attempts:
            attempt["failure"] = classify_job_failure(
                attempt.get("error"),
                step=attempt.get("stage"),
                status=attempt.get("status"),
            )
        return attempts

    def retry_job(
        self,
        batch_id: str,
        job_id: int,
        *,
        step: str = "auto",
        model_id: str | None = None,
        source_url: str | None = None,
        raw_content: str | None = None,
        image_index: int | None = None,
        image_id: str | None = None,
    ) -> dict[str, Any]:
        """Resume one existing job from the failed stage in the background."""

        guard = _retry_guard(str(self.config["_db_path"]), job_id)
        if not guard.acquire(blocking=False):
            raise ValueError("该文章已有恢复任务正在执行，请勿重复提交。")
        claimed = False
        handed_off = False
        requested_step = ""
        target_step = ""
        retry_image_index: int | None = None
        try:
            job = self._batch_job(batch_id, job_id)
            expected_status = str(job.get("status") or "")
            active_statuses = {
                "pending",
                "ingesting",
                "rewriting",
                "title_optimizing",
                "rendering",
                "injecting",
            }
            if expected_status in active_statuses:
                raise ValueError("该文章当前仍在处理中，不能重复发起恢复。")
            if expected_status in {"drafted", "published"}:
                raise ValueError(
                    "已写入草稿箱或已发布的文章不能原地重试，"
                    "如需重新生成请复制原批次。"
                )
            if expected_status not in {"failed", "cancelled"}:
                raise ValueError("只有失败或已停止的文章可以从失败步骤原地重试。")
            requested_step = self._normalize_retry_step(
                step, failed_step=str(job.get("step") or "")
            )
            self._assert_retry_backoff_elapsed(job_id, requested_step)
            if requested_step != "images" and (
                image_index is not None or str(image_id or "").strip()
            ):
                raise ValueError("image_index/image_id 仅可用于正文配图重试。")
            if requested_step == "images":
                retry_image_index = self._resolve_retry_image_index(
                    job,
                    image_index=image_index,
                    image_id=image_id,
                )
            target_status = {
                "ingest": "ingesting",
                "rewrite": "rewriting",
                "title_optimize": "title_optimizing",
                "render": "rendering",
                "images": "rendering",
                "inject": "injecting",
            }[requested_step]
            target_step = requested_step
            account_id = str(
                job.get("account_id")
                or (job.get("meta") or {}).get("official_account_id")
                or ""
            )
            if not account_id:
                raise ValueError("该文章缺少目标公众号，无法恢复。")
            cfg, _account = apply_account_selection(
                deepcopy(self.config),
                self.db,
                account_id,
                allow_disabled=True,
            )
            selected_model_id = str(model_id or "").strip()
            if selected_model_id:
                cfg = apply_model_selection(
                    cfg,
                    self.db,
                    selected_model_id,
                    selected_model_id,
                )
            original_meta = dict(job.get("meta") or {})
            meta = dict(original_meta)
            updates: dict[str, Any] = {}
            if source_url is not None:
                normalized_url = str(source_url or "").strip()
                if not normalized_url:
                    raise ValueError("替换后的原文链接不能为空。")
                validate_external_url(normalized_url)
                updates["source_url"] = normalized_url
                if raw_content is None:
                    # A replacement URL must not be shadowed by stale content
                    # left by the previous failed ingest.
                    updates["raw_content"] = None
                meta["source_mode"] = "link"
                meta["reference_urls"] = []
            if raw_content is not None:
                normalized_content = str(raw_content or "").strip()
                if not normalized_content:
                    raise ValueError("替换后的正文不能为空。")
                updates["raw_content"] = normalized_content
                meta["source_mode"] = "text"
                meta["reference_urls"] = []
            self._assert_retry_prerequisites(
                job,
                step=requested_step,
                source_url=updates.get("source_url"),
                raw_content=updates.get("raw_content"),
            )
            if selected_model_id:
                model = self.db.get_ai_model(selected_model_id)
                meta["selected_model_id"] = selected_model_id
                meta["selected_model_name"] = str(
                    (model or {}).get("name") or selected_model_id
                )
            invalidated, meta = self._retry_downstream_updates(
                job,
                step=requested_step,
                meta=meta,
            )
            updates.update(invalidated)
            if meta != original_meta:
                updates["meta_json"] = meta

            # The process-local guard improves the common desktop case, while
            # this compare-and-set is the authoritative cross-process claim for
            # desktop, API and Feishu callers sharing the same database.
            if not self.db.claim_job_for_retry(
                job_id,
                expected_status=expected_status,
                target_status=target_status,
                target_step=target_step,
                expected_updated_at=str(job.get("updated_at") or "") or None,
            ):
                raise ValueError("文章状态已变化，请刷新后重试。")
            claimed = True
            if updates:
                self.db.update_job(job_id, **updates)
            if requested_step != "inject":
                self.db.update_batch_job_review(
                    batch_id, job_id, "unviewed"
                )
            self.db.update_batch(
                batch_id,
                status="injecting" if requested_step == "inject" else "processing",
                error="",
            )
            thread = threading.Thread(
                target=self._run_job_retry_in_scope,
                args=(
                    self.db.owner_user_id,
                    batch_id,
                    job_id,
                    requested_step,
                    cfg,
                    guard,
                    retry_image_index,
                ),
                name=f"job-retry-{job_id}-{requested_step}",
                daemon=True,
            )
            thread.start()
            handed_off = True
            refreshed = self._batch_job(batch_id, job_id)
            return {
                "batch_id": batch_id,
                "job_id": int(job_id),
                "requested_step": requested_step,
                "image_index": retry_image_index,
                "status": "accepted",
                "job": self._public_job(refreshed, include_content=True),
            }
        except Exception as exc:
            if claimed and not handed_off:
                safe_error = sanitize_failure_text(exc)
                try:
                    self.db.update_job(
                        job_id,
                        status="failed",
                        step=target_step or requested_step or "ingest",
                        error=f"恢复任务启动失败：{safe_error}",
                    )
                    raw_batch = self.db.get_batch(batch_id) or {"jobs": []}
                    self.db.update_batch(
                        batch_id,
                        status=effective_batch_status(
                            list(raw_batch.get("jobs") or []),
                            str(raw_batch.get("status") or ""),
                        ),
                    )
                except Exception:  # noqa: BLE001
                    # Preserve the original launch exception for the caller.
                    pass
            if not handed_off and guard.locked():
                guard.release()
            raise

    def _run_job_retry_in_scope(
        self,
        owner_user_id: str,
        batch_id: str,
        job_id: int,
        step: str,
        cfg: dict[str, Any],
        guard: threading.Lock,
        image_index: int | None = None,
    ) -> None:
        with customer_data_scope(owner_user_id):
            self._run_job_retry(
                batch_id,
                job_id,
                step,
                cfg,
                guard,
                image_index,
            )

    def _run_job_retry(
        self,
        batch_id: str,
        job_id: int,
        step: str,
        cfg: dict[str, Any],
        guard: threading.Lock,
        image_index: int | None = None,
    ) -> None:
        try:
            if step == "images":
                if image_index is not None:
                    self.regenerate_inline_image(
                        batch_id,
                        job_id,
                        image_index,
                        instruction=(
                            "重新生成这张正文配图，保持与对应论点紧密相关；"
                            "画面中不要出现文字、水印、边框或留白。"
                        ),
                        _retry_owned=True,
                    )
                else:
                    self.regenerate_inline_images(
                        batch_id,
                        job_id,
                        _retry_owned=True,
                    )
            elif step == "inject":
                job = self._batch_job(batch_id, job_id)
                account_id = str(job.get("account_id") or "")
                if account_id and self.db.get_official_account(account_id):
                    attempt = self.db.create_job_attempt(
                        batch_id=batch_id,
                        job_id=job_id,
                        stage="preflight",
                        model_id=str(
                            (job.get("meta") or {}).get(
                                "selected_model_id"
                            )
                            or ""
                        )
                        or None,
                    )
                    report = preflight_accounts(
                        self.db,
                        [account_id],
                        deep_model_check=False,
                        force_wechat_check=False,
                        jobs_by_account={account_id: [job]},
                    )[0]
                    if not bool(report.get("can_write")):
                        messages = [
                            str(item.get("message") or "").strip()
                            for item in list(report.get("checks") or [])
                            if str(item.get("key") or "")
                            in {"wechat", "draft", "template", "cover"}
                            and not bool(item.get("ok"))
                        ]
                        message = "；".join(messages) or (
                            "公众号写入前连接检查未通过"
                        )
                        failure = classify_job_failure(
                            message, step="inject", status="failed"
                        )
                        next_retry_at = retry_backoff_at(
                            failure,
                            attempt_no=int(attempt.get("attempt_no") or 1),
                            error=message,
                        )
                        self.db.finish_job_attempt(
                            int(attempt["id"]),
                            status="failed",
                            error_code=str(
                                (failure or {}).get("code") or ""
                            ),
                            error=sanitize_failure_text(message),
                            next_retry_at=next_retry_at,
                        )
                        raise ValueError(message)
                    self.db.finish_job_attempt(
                        int(attempt["id"]), status="succeeded"
                    )
                Pipeline(cfg, db=self.db).review_and_inject(job_id)
            else:
                Pipeline(cfg, db=self.db).run_job(
                    job_id,
                    review=True,
                    from_step=step,
                )
            if step != "inject":
                self.db.update_batch_job_review(
                    batch_id, job_id, "unviewed"
                )
        except Exception as exc:  # noqa: BLE001
            self.db.update_job(
                job_id,
                status="failed",
                step=step,
                error=sanitize_failure_text(exc),
            )
        finally:
            try:
                raw_batch = self.db.get_batch(batch_id) or {"jobs": []}
                jobs = list(raw_batch.get("jobs") or [])
                self.db.update_batch(
                    batch_id,
                    status=effective_batch_status(
                        jobs, str(raw_batch.get("status") or "")
                    ),
                )
                self._notify(self.get_batch(batch_id, include_content=True))
            finally:
                guard.release()

    def _assert_retry_backoff_elapsed(self, job_id: int, step: str) -> None:
        """Reject a retry until the latest failed attempt's cooldown expires."""

        stages = {str(step)}
        if step == "inject":
            stages.add("preflight")
        latest: dict[str, Any] | None = None
        for attempt in self.db.list_job_attempts(job_id, limit=200):
            if str(attempt.get("stage") or "") in stages:
                latest = attempt
                break
        if not latest or str(latest.get("status") or "") != "failed":
            return
        raw_retry_at = str(latest.get("next_retry_at") or "").strip()
        if not raw_retry_at:
            return
        try:
            retry_at = datetime.fromisoformat(raw_retry_at)
        except ValueError:
            return
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        retry_at = retry_at.astimezone(UTC)
        if retry_at <= now:
            return
        remaining_seconds = max(
            1,
            int((retry_at - now).total_seconds()) + 1,
        )
        raise ValueError(
            f"当前步骤仍在服务商冷却期，请约 {remaining_seconds} 秒后重试。"
        )

    @staticmethod
    def _resolve_retry_image_index(
        job: dict[str, Any],
        *,
        image_index: int | None,
        image_id: str | None,
    ) -> int | None:
        """Resolve one requested image, or fall back to failed/all-image retry."""

        assets = [
            dict(item)
            for item in list((job.get("meta") or {}).get("inline_images") or [])
            if isinstance(item, dict)
        ]

        def asset_index(asset: dict[str, Any]) -> int:
            try:
                return int(asset.get("index") or asset.get("image_index") or 0)
            except (TypeError, ValueError):
                return 0

        requested_id = str(image_id or "").strip()
        requested_index: int | None = None
        if image_index is not None:
            try:
                requested_index = int(image_index)
            except (TypeError, ValueError) as exc:
                raise ValueError("image_index 必须是有效的正文配图编号。") from exc
            if requested_index <= 0:
                raise ValueError("image_index 必须大于 0。")

        matched_by_id: int | None = None
        if requested_id:
            for asset in assets:
                candidates = {
                    str(asset.get(key) or "").strip()
                    for key in (
                        "id",
                        "image_id",
                        "media_id",
                        "url",
                        "local_path",
                    )
                }
                index = asset_index(asset)
                candidates.add(str(index) if index > 0 else "")
                if requested_id in candidates and index > 0:
                    matched_by_id = index
                    break
            if matched_by_id is None:
                raise ValueError("image_id 对应的正文配图不存在。")

        if (
            requested_index is not None
            and matched_by_id is not None
            and requested_index != matched_by_id
        ):
            raise ValueError("image_index 与 image_id 指向的正文配图不一致。")
        explicit_index = requested_index or matched_by_id
        if explicit_index is not None:
            if not any(
                asset_index(asset) == explicit_index
                for asset in assets
            ):
                raise ValueError("image_index 对应的正文配图不存在。")
            return explicit_index

        failed_indexes = {
            asset_index(asset)
            for asset in assets
            if (
                str(asset.get("status") or "").strip().casefold()
                in {"failed", "error"}
                or bool(asset.get("error"))
                or not str(asset.get("url") or "").strip()
            )
            and asset_index(asset) > 0
        }
        if len(failed_indexes) == 1:
            return next(iter(failed_indexes))
        # No unique failed image can be identified: retain the compatible
        # behavior and rebuild all argument images atomically.
        return None

    @staticmethod
    def _normalize_retry_step(value: str, *, failed_step: str) -> str:
        requested = str(value or "auto").strip().lower()
        aliases = {
            "title": "title_optimize",
            "title_optimizing": "title_optimize",
            "ingesting": "ingest",
            "rewriting": "rewrite",
            "rendering": "render",
            "injecting": "inject",
        }
        if requested == "auto":
            requested = aliases.get(
                str(failed_step or "").strip().lower(),
                str(failed_step or "ingest").strip().lower(),
            )
        requested = aliases.get(requested, requested)
        allowed = {
            "ingest",
            "rewrite",
            "title_optimize",
            "render",
            "images",
            "inject",
        }
        if requested not in allowed:
            raise ValueError(f"不支持的恢复步骤：{value}")
        return requested

    @staticmethod
    def _retry_downstream_updates(
        job: dict[str, Any],
        *,
        step: str,
        meta: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Invalidate only artifacts downstream from the selected retry step."""

        updates: dict[str, Any] = {}
        refreshed_meta = dict(meta)
        if step in {"ingest", "rewrite"}:
            updates.update(
                {
                    "body": None,
                    "titles_json": [],
                    "subtitles_json": [],
                    "title_candidates_json": [],
                    "selected_title": None,
                    "selected_subtitle": None,
                    "html_content": "",
                    "digest": None,
                    "draft_media_id": None,
                }
            )
            refreshed_meta = invalidate_inline_image_meta(refreshed_meta)
            if step == "ingest":
                refreshed_meta.pop("source_images", None)
            refreshed_meta, cleared_generated_cover = (
                invalidate_generated_cover(refreshed_meta)
            )
            if cleared_generated_cover:
                updates["thumb_media_id"] = None
        elif step == "title_optimize":
            # Rewrite-stage title/subtitle lists are upstream inputs for title
            # optimization, so keep them while discarding the optimized choice.
            updates.update(
                {
                    "title_candidates_json": [],
                    "selected_title": None,
                    "selected_subtitle": None,
                    "html_content": "",
                }
            )
            refreshed_meta, cleared_generated_cover = (
                invalidate_generated_cover(refreshed_meta)
            )
            if cleared_generated_cover:
                updates["thumb_media_id"] = None
        elif step == "render":
            updates["html_content"] = ""
        return updates, refreshed_meta

    @staticmethod
    def _assert_retry_prerequisites(
        job: dict[str, Any],
        *,
        step: str,
        source_url: str | None,
        raw_content: str | None,
    ) -> None:
        effective_raw = str(raw_content or job.get("raw_content") or "").strip()
        effective_url = str(source_url or job.get("source_url") or "").strip()
        if step == "ingest" and not (effective_raw or effective_url or job.get("topic")):
            raise ValueError("重新抓取前请提供文章链接、正文或话题。")
        if step in {"rewrite", "title_optimize", "render", "images"} and not (
            effective_raw or str(job.get("body") or "").strip()
        ):
            raise ValueError("该步骤缺少上游正文，请改为从抓取原文开始恢复。")
        if step in {"title_optimize", "render", "images"} and not str(
            job.get("body") or ""
        ).strip():
            raise ValueError("该步骤缺少已生成正文，请改为重新生成正文。")
        if step == "inject":
            if str(job.get("review_status") or "") != "confirmed":
                raise ValueError("文章尚未确认，不能仅重试写入草稿箱。")
            if not str(job.get("html_content") or "").strip():
                raise ValueError("文章尚未完成排版，不能直接重试写入。")

    def retry_failed(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        failed_account_ids = [
            str(job.get("account_id") or (job.get("meta") or {}).get("official_account_id") or "")
            for job in batch["jobs"]
            if job.get("status") in {"failed", "cancelled"}
        ]
        failed_account_ids = [item for item in failed_account_ids if item]
        if not failed_account_ids:
            raise ValueError("当前批次没有失败或已终止的公众号任务")
        return self.create_batch(
            source_url=batch.get("source_url"),
            raw_content=batch.get("raw_content"),
            topic=batch.get("topic"),
            source_mode=batch.get("source_mode"),
            reference_urls=_json_list(batch.get("reference_urls_json")),
            required_facts=batch.get("required_facts"),
            rewrite_intensity=batch.get("rewrite_intensity"),
            account_ids=failed_account_ids,
            requested_by=batch.get("requested_by"),
            chat_id=batch.get("chat_id"),
            parent_batch_id=batch_id,
        )

    def copy_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        account_ids = [str(job.get("account_id") or "") for job in batch["jobs"]]
        return self.create_batch(
            source_url=batch.get("source_url"),
            raw_content=batch.get("raw_content"),
            topic=batch.get("topic"),
            source_mode=batch.get("source_mode"),
            reference_urls=_json_list(batch.get("reference_urls_json")),
            required_facts=batch.get("required_facts"),
            rewrite_intensity=batch.get("rewrite_intensity"),
            account_ids=[item for item in account_ids if item],
            requested_by=batch.get("requested_by"),
            chat_id=batch.get("chat_id"),
            parent_batch_id=batch_id,
        )

    def archive_batch(self, batch_id: str, *, archived: bool = True) -> dict[str, Any]:
        self.db.archive_batch(batch_id, archived=archived)
        return self.get_batch(batch_id)

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        with self._lock:
            event = self._cancel_events.get(batch_id)
            if event:
                event.set()
        for job in batch["jobs"]:
            if str(job.get("status")) not in TERMINAL_STATUSES:
                self.db.update_job(
                    int(job["id"]), status="cancelled", error="用户已终止改写"
                )
        self.db.update_batch(batch_id, status="cancelled")
        result = self.get_batch(batch_id)
        self._notify(result)
        return result

    def _run_generation_in_scope(
        self,
        owner_user_id: str,
        batch_id: str,
        task_items: list[dict[str, Any]],
    ) -> None:
        with customer_data_scope(owner_user_id):
            self._run_generation(owner_user_id, batch_id, task_items)

    def _run_generation(
        self,
        owner_user_id: str,
        batch_id: str,
        task_items: list[dict[str, Any]],
    ) -> None:
        def run_one(item: dict[str, Any]) -> None:
            with customer_data_scope(owner_user_id):
                try:
                    job_id = int(item["job_id"])
                    attempts = self.db.list_job_attempts(job_id, limit=500)
                    job = self.db.get_job(job_id) or {}
                    with BillingService(self.db).operation(
                        scene="article_generation",
                        subject_type="job",
                        subject_id=str(job_id),
                        source_channel=str(job.get("source") or "system"),
                        idempotency_key=(
                            f"article-generation:{job_id}:{len(attempts)}"
                        ),
                        job_id=job_id,
                    ):
                        item["pipe"].run_job(
                            job_id,
                            review=True,
                            from_step="ingest",
                        )
                except Exception:  # noqa: BLE001
                    return

        threads = [
            threading.Thread(target=run_one, args=(item,), daemon=True)
            for item in task_items
        ]
        for thread in threads:
            thread.start()
        self._progress_monitor.watch(batch_id, threads)
        batch = self.db.get_batch(batch_id) or {"jobs": []}
        statuses = [str(job.get("status")) for job in batch["jobs"]]
        if statuses and all(item == "ready_for_review" for item in statuses):
            status = "ready_for_review"
        elif statuses and all(item == "cancelled" for item in statuses):
            status = "cancelled"
        else:
            status = "partial_failed"
        self.db.update_batch(batch_id, status=status)
        with self._lock:
            self._cancel_events.pop(batch_id, None)
        self._notify(self.get_batch(batch_id, include_content=True))

    def _notify(self, batch: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(batch)
            except Exception:  # noqa: BLE001
                continue

    def _batch_job(self, batch_id: str, job_id: int) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        job = next(
            (item for item in batch["jobs"] if int(item["id"]) == int(job_id)),
            None,
        )
        if not job:
            raise KeyError(f"任务不属于该批次：{job_id}")
        return job

    @staticmethod
    def _progress(jobs: list[dict[str, Any]]) -> dict[str, int]:
        return batch_progress(jobs)

    @staticmethod
    def _public_job(job: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
        return public_job(job, include_content=include_content)


_services: dict[str, BatchService] = {}
_services_lock = threading.Lock()


def get_batch_service(config: dict[str, Any] | None = None) -> BatchService:
    cfg = config or load_config()
    key = str(cfg["_db_path"])
    with _services_lock:
        if key not in _services:
            _services[key] = BatchService(cfg)
        return _services[key]


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _paragraphs(body: str) -> list[str]:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    return [item.strip() for item in normalized.split("\n\n") if item.strip()]


def _validate_paragraph_index(
    paragraphs: list[str], paragraph_index: int, *, label: str = "所选段落"
) -> None:
    if (
        isinstance(paragraph_index, bool)
        or not isinstance(paragraph_index, int)
        or paragraph_index < 0
        or paragraph_index >= len(paragraphs)
    ):
        raise ValueError(f"{label}不存在")
