from __future__ import annotations

import logging
import threading
from typing import Any

from app.ads import sync_ads_from_config
from app.ai.failover import FailoverRewriter, TitleScorer
from app.db import Database
from app.notify import Notifier
from app.render import TemplateRenderer
from app.services.failures import sanitize_failure_text
from app.wechat import WeChatClient
from app.workflows import (
    DeliverySteps,
    GenerationSteps,
    JobCancelled,
    RenderingStep,
    WorkflowContext,
)


logger = logging.getLogger(__name__)

STEP_ORDER = ["ingest", "rewrite", "title_optimize", "render", "inject"]


class Pipeline:
    """Stable facade that orchestrates independent workflow stage modules.

    UI, CLI, API and batch callers keep using this class. The implementation
    details live in app.workflows so each stage can evolve and be tested alone.
    """

    def __init__(
        self,
        config: dict[str, Any],
        db: Database | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.db = db or Database(
            str(config.get("_db_target") or config["_db_path"])
        )
        self.notifier = Notifier((config.get("notify") or {}).get("webhook_url"))
        self.rewriter = FailoverRewriter(config, db=self.db)
        self.renderer = TemplateRenderer(config)
        self.scorer = TitleScorer()
        self.cancel_event = cancel_event or threading.Event()
        self.context = WorkflowContext(
            config=config,
            db=self.db,
            notifier=self.notifier,
            rewriter=self.rewriter,
            renderer=self.renderer,
            scorer=self.scorer,
            cancel_event=self.cancel_event,
        )
        self.generation = GenerationSteps(self.context)
        self.rendering = RenderingStep(self.context)
        self.delivery = DeliverySteps(self.context, self.rendering)
        sync_ads_from_config(self.db, config.get("ads") or {})

    def create_and_run(
        self,
        *,
        topic: str | None = None,
        url: str | None = None,
        text: str | None = None,
        source: str = "manual",
        mode: str | None = None,
        review: bool = False,
        cover_media_id: str | None = None,
        selected_title_index: int | None = None,
        from_step: str = "ingest",
    ) -> dict[str, Any]:
        mode = mode or (self.config.get("publish") or {}).get("default_mode", "draft")
        job_id = self.db.create_job(
            topic=topic,
            source=source,
            source_url=url,
            raw_content=text,
            mode=mode,
            meta={"review": review, "cover_media_id": cover_media_id},
        )
        return self.run_job(
            job_id,
            review=review,
            cover_media_id=cover_media_id,
            selected_title_index=selected_title_index,
            from_step=from_step,
        )

    def run_job(
        self,
        job_id: int,
        *,
        review: bool = False,
        cover_media_id: str | None = None,
        selected_title_index: int | None = None,
        from_step: str = "ingest",
        publish_now: bool = False,
        attempt_stage_overrides: dict[str, str] | None = None,
        attempt_model_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        job = self._require_job(job_id)
        start_index = STEP_ORDER.index(from_step) if from_step in STEP_ORDER else 0
        stage_overrides = dict(attempt_stage_overrides or {})
        stage_model_ids = dict(attempt_model_ids or {})

        def tracked(stage: str, operation: Any) -> Any:
            return self.context.run_tracked_stage(
                job_id,
                job,
                stage,
                operation,
                stage_overrides=stage_overrides,
                stage_model_ids=stage_model_ids,
            )

        try:
            self._check_cancelled(job_id)
            if start_index <= 0:
                job = tracked("ingest", lambda: self._step_ingest(job))
                self._check_cancelled(job_id)
            if start_index <= 1:
                job = tracked("rewrite", lambda: self._step_rewrite(job))
                self._check_cancelled(job_id)
            if start_index <= 2:
                job = tracked(
                    "title_optimize",
                    lambda: self._step_title_optimize(job),
                )
                self._check_cancelled(job_id)
            if start_index <= 3:
                job = tracked(
                    "render",
                    lambda: self._step_render(
                        job, cover_media_id=cover_media_id
                    ),
                )
                self._check_cancelled(job_id)
            if review and not publish_now:
                # Publish the reviewable state only after ``tracked`` has
                # persisted a terminal attempt. Inbox polling must never see a
                # ready article whose stage attempt is still running.
                self.db.update_job(
                    job_id, status="ready_for_review", step="inject", error=None
                )
                return self._require_job(job_id)
            if start_index <= 4:
                self._check_cancelled(job_id)
                tracked(
                    "inject",
                    lambda: self._step_inject(
                        job,
                        selected_title_index=selected_title_index,
                        cover_media_id=cover_media_id,
                        publish_now=publish_now
                        or job.get("mode") == "publish",
                    ),
                )
            return self._require_job(job_id)
        except JobCancelled:
            self.db.update_job(job_id, status="cancelled", error="用户已终止改写")
            return self._require_job(job_id)
        except Exception as exc:  # noqa: BLE001
            if self.cancel_event.is_set():
                self.db.update_job(job_id, status="cancelled", error="用户已终止改写")
                return self._require_job(job_id)
            safe_error = sanitize_failure_text(exc)
            logger.error("Job %s failed: %s", job_id, safe_error)
            self.db.update_job(job_id, status="failed", error=safe_error)
            self.notifier.send(
                f"Job #{job_id} failed", safe_error, level="error"
            )
            raise

    def review_and_inject(
        self,
        job_id: int,
        *,
        title_index: int | None = None,
        cover_media_id: str | None = None,
        publish_now: bool = False,
    ) -> dict[str, Any]:
        return self.run_job(
            job_id,
            review=False,
            cover_media_id=cover_media_id,
            selected_title_index=title_index,
            from_step="inject",
            publish_now=publish_now,
        )

    def publish_job(self, job_id: int) -> dict[str, Any]:
        return self.delivery.publish_existing(job_id)

    # Compatibility wrappers keep existing internal callers and extensions stable.
    def _step_ingest(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.generation.ingest(job)

    def _step_rewrite(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.generation.rewrite(job)

    def _step_title_optimize(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.generation.optimize_titles(job)

    def _step_render(
        self, job: dict[str, Any], *, cover_media_id: str | None = None
    ) -> dict[str, Any]:
        return self.rendering.render(job, cover_media_id=cover_media_id)

    def _step_inject(
        self,
        job: dict[str, Any],
        *,
        selected_title_index: int | None = None,
        cover_media_id: str | None = None,
        publish_now: bool = False,
    ) -> dict[str, Any]:
        return self.delivery.inject(
            job,
            selected_title_index=selected_title_index,
            cover_media_id=cover_media_id,
            publish_now=publish_now,
        )

    def _check_cancelled(self, job_id: int) -> None:
        self.context.check_cancelled(job_id)

    def _wechat_client(self) -> WeChatClient:
        return self.context.wechat_client()

    def _require_job(self, job_id: int) -> dict[str, Any]:
        return self.context.require_job(job_id)
