from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.accounts import resolve_account_text_model_id
from app.ai.failover import FailoverRewriter, TitleScorer
from app.db import Database
from app.notify import Notifier
from app.render import TemplateRenderer
from app.services.job_attempts import run_tracked_job_stage
from app.services.model_readiness import record_model_auth_failure_for_error
from app.wechat import WeChatClient
from app.wechat.factory import build_wechat_client

from .errors import JobCancelled


@dataclass
class WorkflowContext:
    """Explicit dependencies shared by workflow stages.

    Stages depend on this small context instead of importing the Pipeline facade,
    which keeps generation, rendering and delivery independently testable.
    """

    config: dict[str, Any]
    db: Database
    notifier: Notifier
    rewriter: FailoverRewriter
    renderer: TemplateRenderer
    scorer: TitleScorer
    cancel_event: threading.Event

    def require_job(self, job_id: int) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")
        return job

    def check_cancelled(self, job_id: int) -> None:
        job = self.db.get_job(job_id) or {}
        if self.cancel_event.is_set() or job.get("status") == "cancelled":
            raise JobCancelled("用户已终止改写")

    def wechat_client(self) -> WeChatClient:
        wechat_cfg = self.config.get("wechat") or {}
        return build_wechat_client(
            self.config,
            self.db,
            app_id=str(wechat_cfg.get("app_id") or ""),
            app_secret=str(wechat_cfg.get("app_secret") or ""),
        )

    def run_tracked_stage(
        self,
        job_id: int,
        job: dict[str, Any],
        stage: str,
        operation: Callable[[], Any],
        *,
        stage_overrides: dict[str, str],
        stage_model_ids: dict[str, str],
    ) -> Any:
        attempt_stage = str(stage_overrides.get(stage) or stage)
        model_id = (
            str(
                stage_model_ids.get(attempt_stage)
                or stage_model_ids.get(stage)
                or self.job_model_id(job)
                or ""
            ).strip()
            or None
        )
        try:
            return run_tracked_job_stage(
                self.db,
                job_id,
                attempt_stage,
                operation,
                model_id=model_id,
            )
        except Exception as exc:
            if stage in {"rewrite", "title_optimize"} and model_id:
                record_model_auth_failure_for_error(
                    self.db,
                    self.config,
                    model_id,
                    exc,
                )
            raise

    def job_model_id(self, job: dict[str, Any]) -> str:
        meta = dict(job.get("meta") or {})
        model_id = str(
            meta.get("selected_model_id")
            or meta.get("model_id")
            or ""
        ).strip()
        if model_id:
            return model_id
        account_id = str(
            meta.get("official_account_id")
            or job.get("account_id")
            or ""
        ).strip()
        if account_id:
            account = self.db.get_official_account(account_id)
            if account:
                return resolve_account_text_model_id(
                    self.db,
                    self.config,
                    account,
                )
        primary = str((self.config.get("ai") or {}).get("primary") or "").strip()
        if not primary:
            return ""
        if self.db.get_ai_model(primary):
            return primary
        return f"config:{primary}"
