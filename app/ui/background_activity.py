from __future__ import annotations

import logging
from typing import Any
from nicegui import run, ui

from app.services.batches import BatchService
from app.ui.lifecycle import client_timer
from app.ui.navigation import ui_root_url
from app.ui.panels.review_jury import editorial_review_progress
from app.ui.state import AppState

logger = logging.getLogger(__name__)


_FINAL_JOB_STATUSES = {
    "ready_for_review",
    "drafted",
    "published",
    "failed",
    "cancelled",
}


def _generation_activity(batch: dict[str, Any]) -> dict[str, Any] | None:
    jobs = [dict(item) for item in list(batch.get("jobs") or [])]
    active_jobs = [
        item
        for item in jobs
        if str(item.get("status") or "") not in _FINAL_JOB_STATUSES
    ]
    if not active_jobs:
        return None
    progress = dict(batch.get("progress") or {})
    total = max(1, int(progress.get("total") or len(jobs) or 1))
    completed = min(total, int(progress.get("completed") or 0))
    value = min(0.95, max(0.05, completed / total))
    current = active_jobs[0]
    stage = str(
        current.get("stage_label")
        or current.get("step_label")
        or current.get("step")
        or "后台生成中"
    )
    return {
        "kind": "generation",
        "title": str(batch.get("topic") or "文章生成任务"),
        "stage": stage,
        "progress": value,
        "detail": f"已完成 {completed}/{total} 篇",
        "url": ui_root_url(
            {"view": "tasks", "batch_id": str(batch.get("id") or "")}
        ),
    }


def _review_activity(review: dict[str, Any]) -> dict[str, Any] | None:
    status = str(review.get("status") or "")
    if status not in {"running", "rewriting", "candidate_ready"}:
        return None
    progress = editorial_review_progress(review)
    batch_id = str(review.get("batch_id") or "")
    job_id = int(review.get("job_id") or 0)
    return {
        "kind": "review",
        "title": str(review.get("account_name") or "文章 AI 评审"),
        "stage": str(progress.get("stage") or "AI 评审处理中"),
        "progress": float(progress.get("value") or 0.05),
        "detail": "候选稿待选择" if status == "candidate_ready" else "可继续使用其他功能",
        "url": ui_root_url(
            {"view": "review", "batch_id": batch_id, "job_id": job_id}
        ),
    }


def build_global_activity_dock(state: AppState) -> None:
    """Keep persisted generation/review progress visible across all pages."""

    service = BatchService(
        dict(getattr(state, "config", {}) or {}),
        owner_user_id=str(getattr(state, "current_user_id", "") or ""),
        recover_stale_work=False,
    )
    owner_client = ui.context.client
    runtime: dict[str, Any] = {
        "visible": False,
        "loading": False,
        "activities": [],
    }

    def load_activities() -> list[dict[str, Any]]:
        """Read persisted activity outside NiceGUI's event-loop thread."""

        activities: list[dict[str, Any]] = []
        try:
            for batch in service.list_batches(limit=20):
                activity = _generation_activity(dict(batch))
                if activity:
                    activities.append(activity)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Unable to list background generation activity: %s", exc)
        try:
            for review in service.list_editorial_reviews(limit=30):
                activity = _review_activity(dict(review))
                if activity:
                    activities.append(activity)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Unable to list background review activity: %s", exc)
        return activities

    @ui.refreshable
    def render() -> None:
        if not runtime["visible"]:
            return
        activities = [
            dict(item) for item in list(runtime.get("activities") or [])
        ]
        with ui.element("aside").classes("ops-global-activity-dock"):
            with ui.row().classes("ops-activity-dock-heading"):
                ui.icon("sync", size="18px").classes("ops-semantic-icon")
                ui.label(f"后台任务 {len(activities)}")
                ui.button(
                    icon="close",
                    on_click=lambda: toggle(False),
                ).props("flat round dense aria-label=关闭后台任务")
            if not activities:
                ui.label("当前没有运行中的后台任务").classes(
                    "ops-activity-stage"
                )
            for activity in activities[:4]:
                value = min(1.0, max(0.0, float(activity["progress"])))
                with ui.element("article").classes("ops-activity-card"):
                    with ui.row().classes("ops-activity-title-row"):
                        ui.label(str(activity["title"]))
                        ui.label(f"{round(value * 100)}%").classes(
                            "ops-activity-percent"
                        )
                    ui.label(str(activity["stage"])).classes(
                        "ops-activity-stage"
                    )
                    ui.linear_progress(value=value).props(
                        "rounded color=primary track-color=blue-1"
                    ).classes("ops-activity-progress")
                    with ui.row().classes("ops-activity-footer"):
                        ui.label(str(activity["detail"]))
                        ui.link("查看详情", str(activity["url"]))

    def toggle(force: bool | None = None) -> None:
        runtime["visible"] = (
            bool(force) if force is not None else not runtime["visible"]
        )
        render.refresh()
        if runtime["visible"]:
            client_timer(0.01, refresh_if_alive, once=True)

    state.activity_dock_toggle = toggle
    render()

    async def refresh_if_alive() -> None:
        if (
            bool(getattr(owner_client, "is_deleted", False))
            or not runtime["visible"]
            or bool(runtime["loading"])
        ):
            return
        runtime["loading"] = True
        try:
            runtime["activities"] = await run.io_bound(load_activities)
        finally:
            runtime["loading"] = False
        if (
            not bool(getattr(owner_client, "is_deleted", False))
            and runtime["visible"]
        ):
            render.refresh()

    client_timer(2.0, refresh_if_alive, immediate=False)


__all__ = ["build_global_activity_dock"]
