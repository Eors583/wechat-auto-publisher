from __future__ import annotations

import threading
import time

from app.feishu.progress import FeishuProgressReporter
from app.services.batch_progress import BatchProgressMonitor


def test_batch_progress_monitor_emits_job_status_changes() -> None:
    state = {
        "status": "processing",
        "job_status": "pending",
        "step": "ingest",
    }
    lock = threading.Lock()
    notifications: list[tuple[str, str]] = []

    def load(_batch_id: str):
        with lock:
            return {
                "id": "b1",
                "status": state["status"],
                "jobs": [
                    {
                        "id": 1,
                        "account_name": "账号A",
                        "status": state["job_status"],
                        "step": state["step"],
                    }
                ],
            }

    def worker():
        for status, step in (
            ("ingesting", "ingest"),
            ("rewriting", "rewrite"),
            ("rendering", "render"),
            ("ready_for_review", "inject"),
        ):
            with lock:
                state["job_status"] = status
                state["step"] = step
            time.sleep(0.14)

    thread = threading.Thread(target=worker)
    thread.start()
    monitor = BatchProgressMonitor(
        load,
        lambda batch: notifications.append(
            (batch["jobs"][0]["status"], batch["jobs"][0]["step"])
        ),
        interval_seconds=0.1,
    )
    monitor.watch("b1", [thread])

    statuses = [item[0] for item in notifications]
    assert "ingesting" in statuses
    assert "rewriting" in statuses
    assert "rendering" in statuses
    assert statuses[-1] == "ready_for_review"


def test_feishu_progress_reporter_deduplicates_and_uses_chinese_labels() -> None:
    reporter = FeishuProgressReporter()
    batch = {
        "id": "b1",
        "status": "processing",
        "jobs": [
            {
                "id": 1,
                "account_name": "蓝血家族办公室",
                "status": "rewriting",
                "step": "rewrite",
            }
        ],
    }
    first = reporter.render_if_changed("chat1", batch)
    second = reporter.render_if_changed("chat1", batch)

    assert first is not None
    assert "蓝血家族办公室：AI 改写中" in first
    assert second is None

    batch["jobs"][0]["status"] = "rendering"
    changed = reporter.render_if_changed("chat1", batch)
    assert changed is not None
    assert "套用排版和模板" in changed
