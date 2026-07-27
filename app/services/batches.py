from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from app.accounts import apply_account_selection, public_accounts
from app.ai import (
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    clean_candidate_list,
)
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import build_text_client
from app.config import load_config
from app.cover import invalidate_generated_cover
from app.db import Database
from app.inline_images import (
    invalidate_inline_image_meta,
    regenerate_inline_image_asset,
    remove_inline_image,
)
from app.pipeline import Pipeline
from app.services.batch_progress import BatchProgressMonitor
from app.services.editorial_reviews import (
    EditorialReviewConflict,
    EditorialReviewService,
    article_snapshot,
    snapshot_fingerprint,
)
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
from app.services.url_validation import validate_external_url
from app.services.preflight import preflight_accounts
from app.wechat.material import batch_get_material


_injection_guards: dict[tuple[str, str], threading.Lock] = {}
_injection_guards_lock = threading.Lock()


def _injection_guard(db_path: str, batch_id: str) -> threading.Lock:
    key = (db_path, batch_id)
    with _injection_guards_lock:
        return _injection_guards.setdefault(key, threading.Lock())



class BatchService:
    """Application service shared by the HTTP API and the Feishu bot."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.db = Database(self.config["_db_path"])
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

    def list_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item["id"]),
                "name": str(item["name"]),
                "model_name": str(item.get("model_name") or ""),
            }
            for item in public_accounts(self.db, enabled_only=True)
        ]

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def preflight(
        self, account_ids: list[str], *, deep_model_check: bool = False
    ) -> list[dict[str, Any]]:
        return preflight_accounts(
            self.db, account_ids, deep_model_check=deep_model_check
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
                refreshed = self._batch_job(batch_id, job_id)
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
                    error=str(exc),
                )
                raise

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
        )
        cancel_event = threading.Event()
        task_items: list[dict[str, Any]] = []
        try:
            for account_id in unique_account_ids:
                cfg, account = apply_account_selection(
                    load_config(), self.db, account_id
                )
                pipe = Pipeline(cfg, cancel_event=cancel_event)
                job_id = self.db.create_job(
                    topic=topic,
                    source="feishu" if requested_by else "api",
                    source_url=source_url,
                    raw_content=raw_content,
                    mode="draft",
                    meta={
                        "review": True,
                        "batch_id": batch_id,
                        "official_account_id": account_id,
                        "official_account_name": str(account["name"]),
                        "selected_model_id": str(account["model_id"]),
                        "selected_model_name": str(available[account_id].get("model_name") or ""),
                        "fallback_model_id": str(account["model_id"]),
                        "requested_by": requested_by,
                        "chat_id": chat_id,
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
            self.db.update_batch(batch_id, status="failed", error=str(exc))
            raise

        with self._lock:
            self._cancel_events[batch_id] = cancel_event
        self.db.update_batch(batch_id, status="processing", error="")
        threading.Thread(
            target=self._run_generation,
            args=(batch_id, task_items),
            name=f"batch-generation-{batch_id}",
            daemon=True,
        ).start()
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str, *, include_content: bool = False) -> dict[str, Any]:
        batch = self.db.get_batch(batch_id)
        if not batch:
            raise KeyError(f"批次不存在：{batch_id}")
        jobs = [self._public_job(job, include_content=include_content) for job in batch.pop("jobs", [])]
        batch["jobs"] = jobs
        batch["progress"] = self._progress(jobs)
        batch["status"] = effective_batch_status(jobs, str(batch.get("status") or ""))
        batch["display_id"] = str(batch.get("display_id") or batch_id)
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
        results: list[dict[str, Any]] = []
        for batch in self.db.list_batches(
            limit=limit, include_archived=include_archived
        ):
            batch_id = str(batch["id"])
            results.append(self.get_batch(batch_id, include_content=False))
        return results

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
            if review_status != "viewed":
                raise ValueError("请先打开并查看文章，确认内容无误后再确认")
            if not str(job.get("selected_title") or "").strip():
                raise ValueError("请先选择或填写文章标题")
            self.editorial_reviews.assert_job_may_confirm(job)
            self.db.update_batch_job_review(
                batch_id, job_id, "confirmed"
            )
            return self._public_job(
                self._batch_job(batch_id, job_id), include_content=True
            )

    def request_job_changes(self, batch_id: str, job_id: int) -> dict[str, Any]:
        with self.editorial_reviews.job_operation(job_id):
            job = self._batch_job(batch_id, job_id)
            if job.get("status") != "ready_for_review":
                raise ValueError("只有待审核文章可以标记为需要修改")
            self.db.update_batch_job_review(
                batch_id, job_id, "needs_changes"
            )
            return self._public_job(
                self._batch_job(batch_id, job_id), include_content=True
            )

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

    def rerender_job(self, batch_id: str, job_id: int) -> dict[str, Any]:
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
            raise ValueError("只有待审核文章可以重新排版")
        account_id = str(
            job.get("account_id")
            or (job.get("meta") or {}).get("official_account_id")
            or ""
        )
        cfg, _ = apply_account_selection(load_config(), self.db, account_id)
        Pipeline(cfg).run_job(job_id, review=True, from_step="render")
        self.db.update_batch_job_review(batch_id, job_id, "viewed")
        return self._public_job(
            self._batch_job(batch_id, job_id), include_content=True
        )

    def regenerate_inline_images(
        self, batch_id: str, job_id: int
    ) -> dict[str, Any]:
        """Regenerate all argument images using the account's current image agent."""
        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
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
        self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        try:
            self.db.save_job_version(job_id, reason="重新生成全部正文配图前自动保存")
            self.db.update_job(
                job_id,
                status="rendering",
                step="render",
                error=None,
                html_content="",
                meta_json=invalidate_inline_image_meta(job.get("meta")),
            )
            Pipeline(cfg, db=self.db).run_job(job_id, review=True, from_step="render")
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
            self.db.update_batch_job_review(
                batch_id, job_id, previous_review_status
            )
            raise
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
    ) -> dict[str, Any]:
        """Regenerate one reviewed argument image from an operator instruction."""

        job = self._batch_job(batch_id, job_id)
        if job.get("status") != "ready_for_review":
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
        self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        self.db.update_job(job_id, status="rendering", step="render", error=None)
        try:
            pipeline = Pipeline(cfg, db=self.db)
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
            pipeline.run_job(job_id, review=True, from_step="render")
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
            Pipeline(cfg, db=self.db).run_job(job_id, review=True, from_step="render")
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
        client = build_text_client(self.db, cfg, str(account.get("model_id") or ""))
        previous_review_status = str(job.get("review_status") or "viewed")
        self.db.update_batch_job_review(batch_id, job_id, "needs_changes")
        self.db.update_job(job_id, status="rewriting", step="rewrite", error=None)
        try:
            revision = revise_paragraph(
                client,
                body=str(job.get("body") or ""),
                paragraph_index=paragraph_index,
                instruction=instruction,
                title=str(job.get("selected_title") or ""),
                topic=str(job.get("topic") or ""),
                article_instruction=str((cfg.get("ai") or {}).get("rewrite_prompt") or ""),
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
        except Exception:
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
        self.db.update_batch(batch_id, status="injecting", error="")

        def inject_one(job: dict[str, Any]) -> None:
            try:
                meta = dict(job.get("meta") or {})
                cfg, _ = apply_account_selection(
                    load_config(), self.db, str(meta["official_account_id"]), allow_disabled=True
                )
                # The reviewer has already persisted the exact title (including
                # any manually edited custom title). Remapping it through a
                # candidate index here could overwrite the confirmed value.
                Pipeline(cfg).review_and_inject(int(job["id"]))
            except Exception as exc:  # noqa: BLE001
                self.db.update_job(int(job["id"]), status="failed", error=str(exc))

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

    def _run_generation(self, batch_id: str, task_items: list[dict[str, Any]]) -> None:
        def run_one(item: dict[str, Any]) -> None:
            try:
                item["pipe"].run_job(int(item["job_id"]), review=True, from_step="ingest")
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
