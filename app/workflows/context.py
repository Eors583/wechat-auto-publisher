from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from app.ai.failover import FailoverRewriter, TitleScorer
from app.db import Database
from app.notify import Notifier
from app.render import TemplateRenderer
from app.wechat import WeChatAuth, WeChatClient

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
        auth = WeChatAuth(
            app_id=str(wechat_cfg.get("app_id") or ""),
            app_secret=str(wechat_cfg.get("app_secret") or ""),
            db=self.db,
        )
        return WeChatClient(
            get_token=auth.get_access_token,
            refresh_token=lambda: auth.get_access_token(force_refresh=True),
        )
