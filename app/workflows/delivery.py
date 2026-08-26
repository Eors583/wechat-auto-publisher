from __future__ import annotations

import logging
from typing import Any

from app.ai import TITLE_CANDIDATE_COUNT, clean_candidate_list
from app.benchmark import fetch_latest_benchmark_record, sync_secondary_titles
from app.cover import invalidate_generated_cover
from app.layout import compose_articles, select_secondary_articles
from app.services.failures import sanitize_failure_text
from app.services.wechat_delivery import deliver_draft_once
from app.wechat import (
    build_article_from_job,
    submit_publish,
)

from .context import WorkflowContext
from .rendering import RenderingStep

logger = logging.getLogger(__name__)


def resolve_secondary_articles(
    client: Any,
    config: dict[str, Any],
    db: Any,
    job: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve the secondary articles exactly as the delivery path will."""

    secondaries = select_secondary_articles(
        client,
        config.get("layout") or {},
        exclude_titles=[str(job.get("selected_title") or "")],
    )
    benchmark_cfg = config.get("benchmark") or {}
    if not benchmark_cfg.get("enabled", False) or not secondaries:
        return secondaries
    try:
        record = fetch_latest_benchmark_record(config, db)
        matched = sync_secondary_titles(
            secondaries,
            record,
            threshold=float(benchmark_cfg.get("image_match_threshold") or 0.90),
            matched_only=bool(benchmark_cfg.get("matched_only", False)),
            follow_source_order=bool(benchmark_cfg.get("follow_source_order", True)),
            deduplicate_by_image=bool(
                benchmark_cfg.get("deduplicate_by_image", True)
            ),
        )
        return matched or secondaries
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "benchmark ad-title sync failed; using default draft ads: %s",
            sanitize_failure_text(exc),
        )
        return secondaries


class DeliverySteps:
    """Draft composition and WeChat delivery, independent from AI generation."""

    def __init__(self, context: WorkflowContext, rendering: RenderingStep) -> None:
        self.context = context
        self.rendering = rendering

    def inject(
        self,
        job: dict[str, Any],
        *,
        selected_title_index: int | None = None,
        cover_media_id: str | None = None,
        publish_now: bool = False,
    ) -> dict[str, Any]:
        job_id = int(job["id"])
        db = self.context.db
        body_chars = len("".join(str(job.get("body") or "").split()))
        if body_chars < 2000:
            raise ValueError(
                f"写入已停止：正文不足 2000 字，当前只有 {body_chars} 字，请重新生成。"
            )
        db.update_job(job_id, status="injecting", step="inject", error=None)
        job = self._apply_review_choices(
            job,
            selected_title_index=selected_title_index,
            cover_media_id=cover_media_id,
        )
        job = self._ensure_rendered(job, cover_media_id)
        self._validate_layout(job)
        self.context.check_cancelled(job_id)

        client = self.context.wechat_client()
        article = self._build_article(job)
        secondaries = self._secondary_articles(client, job)
        articles = compose_articles(article, secondaries)
        self.context.check_cancelled(job_id)
        meta = _delivery_meta(job, secondaries, len(articles))

        draft_id = deliver_draft_once(
            db,
            client,
            job_id=job_id,
            account_id=_delivery_account_id(job),
            articles=articles,
            # Secondary articles come from a moving draft library. The primary
            # article is the stable identity for resuming this job safely.
            fingerprint_articles=[article],
        )
        if publish_now:
            return self._publish_created_draft(job, client, draft_id, meta)
        db.update_job(
            job_id,
            draft_media_id=draft_id,
            status="drafted",
            step="inject",
            meta_json=meta,
            error=None,
        )
        self.context.notifier.send(
            f"Job #{job_id} drafted",
            f"title={job.get('selected_title')} media_id={draft_id} "
            f"articles={len(articles)} secondaries={meta.get('secondary_titles')}",
        )
        return self.context.require_job(job_id)

    def publish_existing(self, job_id: int) -> dict[str, Any]:
        job = self.context.require_job(job_id)
        client = self.context.wechat_client()
        try:
            if job.get("draft_media_id"):
                draft_id = str(job["draft_media_id"])
            else:
                if not job.get("html_content"):
                    job = self.rendering.render(job)
                article = self._build_article(job)
                draft_id = deliver_draft_once(
                    self.context.db,
                    client,
                    job_id=job_id,
                    account_id=_delivery_account_id(job),
                    articles=[article],
                    fingerprint_articles=[article],
                )
                # Persist the accepted/reconciled draft before the separate
                # publish mutation. A publish failure must never trigger a
                # second untracked draft creation.
                self.context.db.update_job(
                    job_id,
                    draft_media_id=draft_id,
                    status="drafted",
                    step="inject",
                    error=None,
                )
                job = self.context.require_job(job_id)
            publish_id = submit_publish(client, draft_id)
            self.context.db.update_job(
                job_id,
                draft_media_id=draft_id,
                publish_id=publish_id,
                status="published",
                step="publish",
                error=None,
            )
            self.context.notifier.send(
                f"Job #{job_id} published",
                f"draft={draft_id} publish_id={publish_id}",
            )
            return self.context.require_job(job_id)
        except Exception as exc:  # noqa: BLE001
            safe_error = sanitize_failure_text(exc)
            self.context.notifier.send(
                f"Job #{job_id} publish failed", safe_error, level="error"
            )
            self.context.db.update_job(
                job_id,
                status="failed",
                error=safe_error,
            )
            raise

    def _apply_review_choices(
        self,
        job: dict[str, Any],
        *,
        selected_title_index: int | None,
        cover_media_id: str | None,
    ) -> dict[str, Any]:
        db = self.context.db
        job_id = int(job["id"])
        candidates = clean_candidate_list(
            list(job.get("title_candidates") or job.get("titles") or []),
            limit=TITLE_CANDIDATE_COUNT,
        )
        if selected_title_index is not None and candidates:
            index = max(0, min(selected_title_index, len(candidates) - 1))
            selected_title = str(candidates[index])
            updates: dict[str, Any] = {"selected_title": selected_title}
            if selected_title != str(job.get("selected_title") or ""):
                meta, cleared_generated_cover = invalidate_generated_cover(
                    job.get("meta")
                )
                if cleared_generated_cover:
                    updates["thumb_media_id"] = None
                    updates["meta_json"] = meta
            db.update_job(job_id, **updates)
            job = self.context.require_job(job_id)
        if cover_media_id:
            meta = dict(job.get("meta") or {})
            meta["generated_cover_active"] = False
            db.update_job(
                job_id,
                thumb_media_id=cover_media_id,
                meta_json=meta,
            )
        return self.context.require_job(job_id)

    def _ensure_rendered(
        self, job: dict[str, Any], cover_media_id: str | None
    ) -> dict[str, Any]:
        editor_cfg = self.context.config.get("editor_template") or {}
        if editor_cfg.get("enabled", False):
            job = self.rendering.render(job, cover_media_id=cover_media_id)
            if editor_cfg.get("required", True) and not bool(
                (job.get("meta") or {}).get("editor_template_applied")
            ):
                raise RuntimeError(
                    "写入已停止：尚未同步‘蓝血经营管理系统模版’。"
                    "请先在公众号编辑器插入该模板，保留‘蓝血经营管理系统正文’，"
                    "保存一次临时文章后再写入。"
                )
        elif not job.get("html_content") or not job.get("thumb_media_id"):
            job = self.rendering.render(job, cover_media_id=cover_media_id)
        return job

    @staticmethod
    def _validate_layout(job: dict[str, Any]) -> None:
        quality = (job.get("meta") or {}).get("layout_quality") or {}
        errors = list(quality.get("errors") or [])
        if errors:
            raise ValueError("写入已停止：最终排版检查未通过：" + "；".join(errors))

    def _build_article(self, job: dict[str, Any]) -> dict[str, Any]:
        wechat_cfg = self.context.config.get("wechat") or {}
        return build_article_from_job(
            job,
            author=str(wechat_cfg.get("author") or ""),
            need_open_comment=int(wechat_cfg.get("need_open_comment") or 0),
            only_fans_can_comment=int(wechat_cfg.get("only_fans_can_comment") or 0),
        )

    def _secondary_articles(
        self, client: Any, job: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            return resolve_secondary_articles(
                client,
                self.context.config,
                self.context.db,
                job,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "select secondary articles failed: %s",
                sanitize_failure_text(exc),
            )
            return []

    def _publish_created_draft(
        self,
        job: dict[str, Any],
        client: Any,
        draft_id: str,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = int(job["id"])
        try:
            publish_id = submit_publish(client, draft_id)
        except Exception as exc:
            logger.error(
                "Publish failed; draft kept media_id=%s error=%s",
                draft_id,
                sanitize_failure_text(exc),
            )
            self.context.db.update_job(
                job_id,
                draft_media_id=draft_id,
                status="drafted",
                meta_json=meta,
                error="Publish failed, multi-article draft saved",
            )
            raise
        self.context.db.update_job(
            job_id,
            draft_media_id=draft_id,
            publish_id=publish_id,
            status="published",
            step="publish",
            meta_json=meta,
            error=None,
        )
        self.context.notifier.send(
            f"Job #{job_id} published",
            f"title={job.get('selected_title')} draft={draft_id}",
        )
        return self.context.require_job(job_id)


def _delivery_meta(
    job: dict[str, Any], secondaries: list[dict[str, Any]], article_count: int
) -> dict[str, Any]:
    meta = dict(job.get("meta") or {})
    meta["secondary_titles"] = [item.get("title") for item in secondaries]
    meta["secondary_media_ids"] = [item.get("_from_media_id") for item in secondaries]
    meta["benchmark_matches"] = [
        {
            "title": item.get("title"),
            "original_title": item.get("_original_title"),
            "score": item.get("_benchmark_image_score"),
            "source": item.get("_benchmark_source"),
            "published_at": item.get("_benchmark_published_at"),
        }
        for item in secondaries
        if item.get("_benchmark_title")
    ]
    meta["article_count"] = article_count
    return meta


def _delivery_account_id(job: dict[str, Any]) -> str:
    meta = job.get("meta")
    if isinstance(meta, dict):
        account_id = str(meta.get("official_account_id") or "").strip()
        if account_id:
            return account_id
    # Legacy and direct CLI jobs use the imported config account.
    return "account_config_default"
