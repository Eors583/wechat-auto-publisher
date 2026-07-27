from __future__ import annotations

import threading
from typing import Any

from app.services.batch_progress import batch_progress_signature


STATUS_LABELS = {
    "pending": "等待开始",
    "ingesting": "抓取原文",
    "rewriting": "AI 改写中",
    "title_optimizing": "生成标题与副标题",
    "rendering": "套用排版和模板",
    "ready_for_review": "等待审核",
    "injecting": "写入草稿箱",
    "drafted": "已写入草稿箱",
    "failed": "失败",
    "cancelled": "已终止",
}


class FeishuProgressReporter:
    """Deduplicate and render batch progress for Feishu conversations."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._lock = threading.Lock()

    def render_if_changed(
        self,
        chat_id: str,
        batch: dict[str, Any],
    ) -> str | None:
        key = (chat_id, str(batch.get("id") or ""))
        signature = batch_progress_signature(batch)
        with self._lock:
            if self._last.get(key) == signature:
                return None
            self._last[key] = signature
        lines = [f'批次 {batch.get("id")} 实时进度：']
        for job in batch.get("jobs") or []:
            status = str(job.get("status") or "pending")
            label = STATUS_LABELS.get(status, status)
            lines.append(f'• {job.get("account_name") or "公众号"}：{label}')
        return "\n".join(lines)
