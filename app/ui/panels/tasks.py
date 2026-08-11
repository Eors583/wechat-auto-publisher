from __future__ import annotations

import asyncio
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nicegui import run, ui

from app.ai import clean_candidate_text
from app.config import load_config
from app.render.preview import prepare_preview_html
from app.services.batches import BatchService
from app.services.failures import sanitize_failure_text
from app.time_utils import business_date, format_business_datetime
from app.ui.image_proxy import wechat_image_proxy_url
from app.ui.ip_whitelist_guide import (
    has_ip_whitelist_issue,
    show_ip_whitelist_guide,
)
from app.ui.lifecycle import client_timer
from app.ui.interaction_feedback import (
    attach_interaction_feedback,
    hide_interaction_feedback,
)
from app.ui.panels.review_jury import (
    build_review_jury_panel,
    editorial_review_progress,
)
from app.ui.state import (
    STATUS_LABEL,
    AppState,
    clean_subtitles,
    clean_titles,
    set_button_loading,
)
from app.ui.workflow import next_review_job, render_workflow_guide
from app.wechat.errors import friendly_wechat_error

REVIEW_LABELS = {
    "unviewed": "未查看",
    "viewed": "已查看，未确认",
    "confirmed": "已确认",
    "needs_changes": "需要修改",
    "drafted": "已写入草稿箱",
    "write_failed": "写入失败",
}

REVIEW_COLORS = {
    "unviewed": "orange-8",
    "viewed": "orange-7",
    "confirmed": "teal-7",
    "needs_changes": "deep-orange-7",
    "drafted": "green-7",
    "write_failed": "red-7",
}


def _review_confirmation_gate(review: dict[str, Any] | None) -> tuple[str, int]:
    """Return the frontend confirmation block reason and open blocker count."""

    current = dict(review or {})
    if str(current.get("status") or "") in {"running", "rewriting"}:
        return "AI 评审仍在进行中", 0
    if str(current.get("status") or "") == "candidate_ready":
        return "AI 改写稿已生成，请先选择使用原文还是改写稿", 0
    blocking_count = max(0, int(current.get("blocking_count") or 0))
    if blocking_count:
        return f"AI 评审仍有 {blocking_count} 个阻断项待处理", blocking_count
    return "", 0


INBOX_BUCKETS = {
    "review": {
        "label": "待审核",
        "color": "orange-8",
        "icon": "rate_review",
    },
    "ready_for_draft": {
        "label": "待写入草稿",
        "color": "teal-8",
        "icon": "outbox",
    },
    "write_failed": {
        "label": "写入失败",
        "color": "deep-orange-8",
        "icon": "cloud_off",
    },
    "generation_failed": {
        "label": "生成失败",
        "color": "red-7",
        "icon": "error_outline",
    },
    "today_completed": {
        "label": "今日完成",
        "color": "teal-8",
        "icon": "task_alt",
    },
}


def _format_progress(value: Any) -> tuple[float, str]:
    """Return a safe progress value and its user-facing percentage."""

    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = 0.0
    if not math.isfinite(normalized):
        normalized = 0.0
    normalized = min(1.0, max(0.0, normalized))
    return normalized, f"{round(normalized * 100)}%"


def _load_review_inbox(
    service: BatchService,
    *,
    bucket: str,
    account_id: str,
    search: str = "",
    limit: int,
    batches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read the review inbox through one UI-facing adapter.

    Newer services expose a first-class inbox query.  The local projection keeps
    the desktop usable with an older service during rolling upgrades.
    """

    loader = getattr(service, "list_review_inbox", None)
    if callable(loader):
        requested_limit = max(1, int(limit))
        items: list[dict[str, Any]] = []
        counts = {key: 0 for key in INBOX_BUCKETS}
        cursor: str | None = None
        next_cursor: str | None = None
        seen_cursors: set[str] = set()
        while len(items) < requested_limit:
            query: dict[str, Any] = {
                "bucket": bucket,
                "account_id": account_id or None,
                "limit": min(100, requested_limit - len(items)),
                "cursor": cursor,
            }
            if str(search or "").strip():
                query["search"] = str(search).strip()
            page = _normalize_inbox_payload(
                loader(**query)
            )
            items.extend(
                list(page.get("items") or [])[
                    : requested_limit - len(items)
                ]
            )
            counts = dict(page.get("counts") or counts)
            next_cursor = page.get("next_cursor")
            if next_cursor is None or len(items) >= requested_limit:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                # A malformed service cursor must not spin or repeat a page.
                next_cursor = None
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return {
            "items": items,
            "counts": counts,
            "next_cursor": next_cursor,
        }
    return _fallback_review_inbox(
        batches or [],
        bucket=bucket,
        account_id=account_id,
        search=search,
        limit=limit,
    )


def _normalize_inbox_payload(payload: Any) -> dict[str, Any]:
    source = dict(payload or {})
    raw_counts = dict(source.get("counts") or {})
    return {
        "items": [
            dict(item)
            for item in list(source.get("items") or [])
            if isinstance(item, dict)
        ],
        "counts": {
            key: int(raw_counts.get(key) or 0)
            for key in INBOX_BUCKETS
        },
        "next_cursor": (
            str(source["next_cursor"])
            if source.get("next_cursor") not in {None, ""}
            else None
        ),
    }


def _fallback_review_inbox(
    batches: list[dict[str, Any]],
    *,
    bucket: str,
    account_id: str = "",
    search: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """Project the stable batch contract into the P0 inbox shape."""

    today = business_date().isoformat()
    grouped: dict[str, list[dict[str, Any]]] = {
        key: [] for key in INBOX_BUCKETS
    }
    for batch in batches:
        for job in list(batch.get("jobs") or []):
            if account_id and str(job.get("account_id") or "") != account_id:
                continue
            status = str(job.get("status") or "")
            step = str(job.get("step") or "")
            review_status = str(job.get("review_status") or "unviewed")
            job_bucket = ""
            if status == "ready_for_review" and review_status != "confirmed":
                job_bucket = "review"
            elif status == "ready_for_review" and review_status == "confirmed":
                job_bucket = "ready_for_draft"
            elif status == "failed" and (
                step == "inject" or review_status == "write_failed"
            ):
                job_bucket = "write_failed"
            elif status == "failed":
                job_bucket = "generation_failed"
            elif status in {"drafted", "published"} and str(
                job.get("updated_at") or ""
            ).startswith(today):
                job_bucket = "today_completed"
            if not job_bucket:
                continue
            failure = str(job.get("error") or "")
            grouped[job_bucket].append(
                {
                    "batch_id": str(batch.get("id") or ""),
                    "batch_display_id": str(
                        batch.get("display_id") or batch.get("id") or ""
                    ),
                    "job_id": int(job["id"]),
                    "status": status,
                    "step": step,
                    "account_id": str(job.get("account_id") or ""),
                    "account_name": str(job.get("account_name") or "公众号"),
                    "title": str(
                        job.get("selected_title") or "尚未选择标题"
                    ),
                    "source": str(
                        batch.get("topic") or batch.get("source_url") or ""
                    ),
                    "source_url": str(batch.get("source_url") or ""),
                    "created_at": job.get("created_at")
                    or batch.get("created_at"),
                    "updated_at": job.get("updated_at")
                    or batch.get("updated_at"),
                    "body_chars": 0,
                    "review_status": review_status,
                    "latest_review_summary": "",
                    "cover_status": "",
                    "blockers": [failure] if failure else [],
                    "priority_reason": (
                        "已标记需要修改"
                        if review_status == "needs_changes"
                        else (
                            "尚未查看"
                            if job_bucket == "review"
                            and review_status == "unviewed"
                            else ""
                        )
                    ),
                    "failure": failure,
                }
            )
    needle = str(search or "").strip().casefold()
    if needle:
        for grouped_bucket, grouped_items in grouped.items():
            grouped[grouped_bucket] = [
                item
                for item in grouped_items
                if needle
                in " ".join(
                    (
                        str(item.get("title") or ""),
                        str(item.get("account_name") or ""),
                        str(item.get("source") or ""),
                    )
                ).casefold()
            ]
    items = grouped.get(bucket, [])
    return {
        "items": items[:limit],
        "counts": {key: len(value) for key, value in grouped.items()},
        "next_cursor": str(limit) if len(items) > limit else None,
    }


def task_center_workflow_stage(batch: dict[str, Any] | None) -> str:
    """Choose the task-center guide stage from the effective batch contract."""

    if not batch:
        return "review"
    progress = dict(batch.get("progress") or {})
    unconfirmed = int(progress.get("unconfirmed") or 0)
    if unconfirmed > 0:
        return "review"
    status = str(batch.get("status") or "")
    if status in {"ready_for_draft", "injecting", "drafted", "published"}:
        return "draft"
    if int(progress.get("ready_for_draft") or 0) > 0:
        return "draft"
    if int(progress.get("drafted") or 0) > 0:
        return "draft"
    return "review"


def build_tasks_panel(
    state: AppState,
    *,
    initial_batch_id: str | None = None,
    initial_job_id: int | None = None,
    initial_entry_mode: str = "activity",
    initial_view: str = "inbox",
    initial_bucket: str = "review",
    initial_status_filter: str = "",
    show_background_activity: bool = True,
    on_open_review: Callable[[str, int], None] | None = None,
) -> None:
    """Review-first task center backed by the shared batch service."""
    service = BatchService(
        load_config(),
        owner_user_id=str(getattr(state, "current_user_id", "") or ""),
        recover_stale_work=False,
    )
    workflow_host = ui.column().classes("ops-hidden-control")
    account_options = {"": "全部公众号", "__refresh__": "刷新任务", **{
        item["id"]: item["name"] for item in service.list_accounts()
    }}
    initial_view_mode = "batches" if initial_view == "batches" else "inbox"
    initial_status = str(initial_status_filter or "")
    initial_segment = (
        initial_bucket
        if initial_view_mode == "inbox" and initial_bucket in {"review", "ready_for_draft"}
        else initial_status
        if initial_status in {"active", "failed"}
        else "batches"
    )
    view_in = ui.toggle(
        {"inbox": "待处理", "batches": "全部批次"},
        value="batches" if initial_view == "batches" else "inbox",
    ).classes("ops-hidden-control")
    status_in = ui.select(
            options={
                "": "全部状态",
                "active": "生成中",
                "attention": "待我审核 / 失败",
                "ready_for_review": "待审核",
                "ready_for_draft": "待写入草稿",
                "drafted": "已写入草稿箱",
                "failed": "失败",
                "cancelled": "已停止",
            },
            value=initial_status,
            label="状态",
    ).props("outlined dense options-dense").classes("ops-hidden-control")
    today_only = ui.switch("只看今天", value=False).classes(
        "ops-hidden-control"
    )
    archived_in = ui.switch("显示已归档", value=False).classes(
        "ops-hidden-control"
    )
    batch_only_filters = ui.row().classes("ops-hidden-control")
    with ui.row().classes("ops-task-page-actions"):
        archive_tasks_btn = ui.button(
            "查看归档",
            icon="archive",
        ).props("outline dense color=primary no-caps")
    queue_segment = ui.toggle(
        {
            "review": "待我处理",
            "active": "生成中",
            "ready_for_draft": "可写草稿",
            "failed": "失败",
            "batches": "全部批次",
        },
        value=initial_segment,
    ).classes("ops-segment ops-task-segment").props(
        "no-caps unelevated toggle-color=white toggle-text-color=dark"
    )
    with ui.row().classes("ops-toolbar ops-task-toolbar"):
        search_in = ui.input(
            placeholder="搜索标题、公众号或批次号"
        ).props("outlined dense clearable debounce=300").classes(
            "ops-search"
        )
        account_in = ui.select(
            options=account_options, value=""
        ).props(
            'outlined dense options-dense hide-bottom-space '
            'display-value="全部公众号"'
        ).classes("ops-filter-account")
        today_filter_btn = ui.button(
            "今天", on_click=lambda: today_only.set_value(True)
        ).props(
            "outline dense color=primary no-caps"
        ).classes("ops-task-today-filter")
        running_tasks_btn = ui.button(
            "查看后台运行任务",
            icon="monitor_heart",
        ).classes("ops-hidden-control")
    with ui.element("div").classes("ops-queue-workspace"):
        with ui.element("section").classes("ops-panel ops-list-panel"):
            with ui.element("div").classes("ops-panel-heading"):
                with ui.column().classes("gap-0"):
                    ui.label("待我处理").classes("ops-panel-title")
                    ui.label("每条任务保持统一行高，按下一步动作排序").classes(
                        "ops-panel-subtitle"
                    )
                queue_count_label = ui.badge("0 条").classes("ops-badge")
            host = ui.column().classes("ops-task-list")
        with ui.element("aside").classes("ops-panel ops-flow-panel"):
            with ui.element("div").classes("ops-panel-heading"):
                with ui.column().classes("gap-0"):
                    ui.label("今日处理顺序").classes("ops-panel-title")
                    ui.label("优先完成阻断项").classes("ops-panel-subtitle")
                ui.badge("建议").classes("ops-badge ops-badge-warm")
            with ui.element("div").classes("ops-flow-list"):
                for order, title, detail in (
                    ("1", "核实事实风险", "完成后释放待审核文章"),
                    ("2", "确认并写入草稿", "已确认文章可批量处理"),
                    ("3", "恢复失败任务", "正文已保留，无需重新生成"),
                ):
                    with ui.row().classes("ops-flow-step"):
                        ui.label(order).classes("ops-flow-number")
                        with ui.column().classes("gap-0 ops-flex-copy"):
                            ui.label(title).classes("ops-task-order-title")
                            ui.label(detail).classes("ops-task-order-detail")
            with ui.element("div").classes("ops-flow-footer"):
                ui.button(
                    "查看后台运行任务",
                    icon="monitor_heart",
                    on_click=lambda: queue_segment.set_value("active"),
                ).classes("w-full").props(
                    "flat dense color=primary no-caps"
                )
    runtime = {
        "has_active_batch": False,
        "review_open": False,
        "focus_batch_id": str(initial_batch_id or ""),
        "completion_batch_id": (
            str(initial_batch_id or "")
            if initial_entry_mode == "completion" and not initial_job_id
            else ""
        ),
        "visible_limit": 4,
        "syncing_controls": False,
        "inbox_bucket": (
            initial_bucket if initial_bucket in INBOX_BUCKETS else "review"
        ),
    }
    if on_open_review is not None:
        runtime["open_review_page"] = on_open_review
    owner_client = ui.context.client
    background_reviews: dict[str, dict[str, Any]] = {}
    background_activity_host = ui.column().classes("background-activity-dock")

    def render_task_center_guide(batch: dict[str, Any] | None = None) -> None:
        workflow_host.clear()
        stage = task_center_workflow_stage(batch)
        if batch is None and str(runtime.get("inbox_bucket") or "") == "ready_for_draft":
            stage = "draft"
        note = (
            "全部文章已确认，可以安全写入公众号草稿箱"
            if stage == "draft"
            else "生成完成后在这里逐篇审核，全部确认后再一次写入草稿箱"
        )
        with workflow_host:
            render_workflow_guide(stage, note=note, compact=True)

    def client_alive() -> bool:
        return not bool(getattr(owner_client, "is_deleted", False))

    def open_activity_detail(batch_id: str, job_id: int | None = None) -> None:
        if job_id:
            ui.navigate.to(
                f"/?view=review&batch_id={batch_id}&job_id={int(job_id)}"
            )
            return
        show_batch(batch_id)

    def render_background_activity() -> None:
        if not client_alive():
            return
        background_activity_host.clear()
        activities: list[dict[str, Any]] = []
        try:
            for batch in service.list_batches(limit=20):
                jobs = list(batch.get("jobs") or [])
                active_jobs = [
                    item
                    for item in jobs
                    if str(item.get("status") or "")
                    not in {
                        "ready_for_review",
                        "drafted",
                        "published",
                        "failed",
                        "cancelled",
                    }
                ]
                if not active_jobs:
                    continue
                progress = dict(batch.get("progress") or {})
                total = max(1, int(progress.get("total") or len(jobs) or 1))
                completed = int(progress.get("completed") or 0)
                activities.append(
                    {
                        "kind": "generation",
                        "title": str(batch.get("topic") or "文章生成"),
                        "status": "后台生成中",
                        "progress": min(0.95, max(0.05, completed / total)),
                        "batch_id": str(batch["id"]),
                        "job_id": None,
                    }
                )
        except Exception:  # noqa: BLE001
            pass

        try:
            running_reviews = [
                review
                for review in service.list_editorial_reviews(limit=30)
                if str(review.get("status") or "")
                in {"running", "rewriting", "candidate_ready"}
            ]
        except Exception:  # noqa: BLE001
            running_reviews = []
        persisted_jobs = {
            int(item.get("job_id") or 0)
            for item in running_reviews
            if str(item.get("status") or "") in {"running", "rewriting"}
        }
        for review in running_reviews:
            review_status = str(review.get("status") or "")
            is_rewrite = review_status == "rewriting"
            candidate_waiting = review_status == "candidate_ready"
            review_progress = editorial_review_progress(review)
            activities.append(
                {
                    "kind": "rewrite" if is_rewrite or candidate_waiting else "review",
                    "title": (
                        f'{review.get("profile_name") or "AI 评审"}'
                        + (
                            " · 整篇优化"
                            if is_rewrite or candidate_waiting
                            else ""
                        )
                    ),
                    "status": str(review_progress["stage"]),
                    "progress": float(review_progress["value"]),
                    "batch_id": str(review.get("batch_id") or ""),
                    "job_id": int(review.get("job_id") or 0),
                }
            )
        for entry in background_reviews.values():
            entry_status = str(entry.get("status") or "")
            if (
                entry_status == "running"
                and int(entry.get("job_id") or 0) in persisted_jobs
            ):
                continue
            rendered = dict(entry)
            rendered["status"] = {
                "running": "AI 正在评审文章",
                "completed": "AI 评审已完成",
                "failed": "AI 评审失败，可查看详情后重试",
            }.get(entry_status, entry_status)
            activities.append(rendered)

        if not activities:
            background_activity_host.set_visibility(False)
            return
        background_activity_host.set_visibility(True)
        with background_activity_host:
            ui.label("后台任务").classes("text-subtitle1 text-weight-bold")
            for activity in activities[:4]:
                progress_value, progress_text = _format_progress(
                    activity.get("progress")
                )
                with ui.card().classes("w-full q-pa-sm background-activity-card"):
                    ui.label(str(activity["title"])).classes(
                        "text-weight-bold ellipsis"
                    )
                    ui.label(str(activity["status"])).classes(
                        "muted text-caption"
                    )
                    with ui.linear_progress(
                        value=progress_value,
                        show_value=False,
                        size="20px",
                    ).props(
                        "color=teal-8 track-color=teal-1 rounded"
                    ).classes("background-activity-progress"):
                        ui.label(progress_text).classes(
                            "absolute-center background-activity-progress-label"
                        )
                    ui.button(
                        "查看详情",
                        on_click=lambda _=None, item=dict(activity): open_activity_detail(
                            str(item.get("batch_id") or ""),
                            int(item.get("job_id") or 0) or None,
                        ),
                    ).props("flat dense color=teal-9 no-caps icon=open_in_new")

    def start_background_review(
        *,
        batch_id: str,
        job_id: int,
        account_name: str,
        operation: Callable[[], dict[str, Any]],
    ) -> bool:
        key = f"review:{batch_id}:{int(job_id)}"
        existing = background_reviews.get(key)
        if existing and str(existing.get("status") or "") == "running":
            ui.notify("这篇文章已经在后台评审中", type="warning")
            return False
        entry: dict[str, Any] = {
            "kind": "review",
            "title": f"{account_name} · AI 评审",
            "status": "running",
            "progress": 0.12,
            "batch_id": str(batch_id),
            "job_id": int(job_id),
        }
        background_reviews[key] = entry
        render_background_activity()

        async def execute() -> None:
            try:
                result = await run.io_bound(operation)
                entry.update(
                    status="completed",
                    progress=1.0,
                    review_id=str(result.get("id") or ""),
                )
                if client_alive():
                    ui.notify(
                        f"{account_name}的 AI 评审已完成",
                        type="positive",
                    )
            except Exception as exc:  # noqa: BLE001
                entry.update(
                    status="failed",
                    progress=1.0,
                    error=sanitize_failure_text(exc),
                )
                if client_alive():
                    ui.notify(
                        f"AI 评审失败：{entry['error']}",
                        type="negative",
                        timeout=12000,
                    )
            finally:
                if client_alive():
                    render_background_activity()
                    render()

        entry["task"] = asyncio.create_task(execute())
        return True

    runtime["start_background_review"] = start_background_review
    if show_background_activity:
        render_background_activity()
        client_timer(2.0, render_background_activity, immediate=False)
    else:
        background_activity_host.set_visibility(False)

    def show_batch(batch_id: str) -> None:
        runtime["completion_batch_id"] = ""
        runtime["focus_batch_id"] = str(batch_id)
        runtime["visible_limit"] = 4
        view_in.value = "batches"
        status_in.set_visibility(True)
        batch_only_filters.set_visibility(True)
        render()

    def select_inbox_bucket(bucket: str) -> None:
        runtime["completion_batch_id"] = ""
        runtime["inbox_bucket"] = bucket
        runtime["visible_limit"] = 4
        render()

    def render() -> None:
        host.clear()
        completion_batch_id = str(runtime.get("completion_batch_id") or "")
        if completion_batch_id:
            for control in (view_in, search_in, account_in):
                control.set_visibility(False)
            status_in.set_visibility(False)
            batch_only_filters.set_visibility(False)
            try:
                completed_batch = service.get_batch(completion_batch_id)
            except KeyError:
                runtime["completion_batch_id"] = ""
            else:
                render_task_center_guide(completed_batch)
                completed_jobs = list(completed_batch.get("jobs") or [])
                completed_progress = dict(completed_batch.get("progress") or {})
                pending_job = next_review_job(completed_jobs)
                failed_count = int(completed_progress.get("failed") or 0)
                with host:
                    with ui.element("section").classes(
                        "card w-full completion-focus-card"
                    ):
                        ui.label("本次任务已生成").classes(
                            "text-h5 text-weight-bold"
                        )
                        ui.label(
                            f'批次 #{completed_batch.get("display_id") or completion_batch_id} · '
                            f'公众号 {len(completed_jobs)} 个 · '
                            f'待审核 {int(completed_progress.get("unconfirmed") or 0)} · '
                            f'待写入 {int(completed_progress.get("ready_for_draft") or 0)} · '
                            f'失败 {failed_count}'
                        ).classes("muted")
                        if failed_count:
                            ui.label(
                                "已生成文章可以先审核；失败公众号可在下方批次卡中单独重试。"
                            ).classes("text-warning")
                        elif pending_job is not None:
                            ui.label(
                                "先审核本次生成的文章，全部确认后再统一写入草稿箱。"
                            ).classes("text-teal-9")
                        else:
                            ui.label(
                                "本次文章均已确认，可以安全进入草稿写入步骤。"
                            ).classes("text-positive")
                        with ui.row().classes("items-center gap-2 q-mt-sm"):
                            if pending_job is not None:
                                ui.button(
                                    "审核第 1 篇",
                                    on_click=lambda: (
                                        runtime["open_review_page"](
                                            completion_batch_id,
                                            int(pending_job["id"]),
                                        )
                                        if callable(runtime.get("open_review_page"))
                                        else open_review_workbench(
                                            state,
                                            service,
                                            completion_batch_id,
                                            int(pending_job["id"]),
                                            render,
                                            review_runtime=runtime,
                                        )
                                    ),
                                ).props(
                                    "unelevated color=teal-9 no-caps icon=rate_review"
                                )
                            ui.button(
                                "返回待处理收件箱",
                                on_click=lambda: (
                                    runtime.__setitem__("completion_batch_id", ""),
                                    runtime.__setitem__("focus_batch_id", ""),
                                    render(),
                                ),
                            ).props(
                                "outline color=teal-9 no-caps icon=arrow_back"
                            )
                    _render_batch_card(
                        state,
                        service,
                        completed_batch,
                        render,
                        review_runtime=runtime,
                        focused=True,
                        auto_expand=True,
                    )
                return
        for control in (view_in, search_in, account_in):
            control.set_visibility(True)
        view_mode = str(view_in.value or "inbox")
        status_in.set_visibility(view_mode == "batches")
        batch_only_filters.set_visibility(view_mode == "batches")
        if view_mode == "inbox":
            has_active_batches = getattr(service, "has_active_batches", None)
            runtime["has_active_batch"] = (
                bool(has_active_batches())
                if callable(has_active_batches)
                else False
            )
            payload = _load_review_inbox(
                service,
                bucket=str(runtime["inbox_bucket"]),
                account_id=str(account_in.value or ""),
                search=str(search_in.value or ""),
                limit=int(runtime["visible_limit"]),
            )
            items = list(payload.get("items") or [])
            queue_count_label.set_text(f"{len(items)} 条")
            with host:
                if not items:
                    current = INBOX_BUCKETS[str(runtime["inbox_bucket"])]["label"]
                    with ui.element("div").classes("card w-full"):
                        ui.label(f"当前没有{current}文章").classes(
                            "text-weight-medium"
                        )
                        ui.label(
                            "新任务完成后会自动出现在这里；历史记录可切换到“全部批次”。"
                        ).classes("muted")
                        if runtime["focus_batch_id"]:
                            ui.button(
                                "查看刚完成的批次",
                                on_click=lambda: show_batch(
                                    str(runtime["focus_batch_id"])
                                ),
                            ).props(
                                "outline color=teal-9 no-caps icon=inventory_2"
                            )
                    return
                focused_card = None
                for item in items:
                    card = _render_inbox_article_card(
                        state,
                        service,
                        item,
                        render,
                        review_runtime=runtime,
                        on_show_batch=show_batch,
                    )
                    if (
                        focused_card is None
                        and str(item.get("batch_id") or "")
                        == str(runtime["focus_batch_id"])
                    ):
                        focused_card = card
                if focused_card is not None:
                    focused_card.run_method(
                        "scrollIntoView",
                        {"behavior": "smooth", "block": "start"},
                    )
                    runtime["focus_batch_id"] = ""
                if payload.get("next_cursor"):
                    ui.button(
                        "加载更多文章",
                        on_click=lambda: (
                            runtime.__setitem__(
                                "visible_limit",
                                int(runtime["visible_limit"]) + 4,
                            ),
                            render(),
                        ),
                    ).props(
                        "outline color=teal-9 no-caps icon=expand_more"
                    ).classes("ops-hidden-control")
            return

        batches = service.list_batches(
            limit=300,
            include_archived=bool(archived_in.value),
        )
        runtime["has_active_batch"] = any(
            str(batch.get("status") or "") in {"pending", "processing", "injecting"}
            for batch in batches
        )
        batches = [batch for batch in batches if _matches_filters(
            batch,
            search=str(search_in.value or ""),
            status=str(status_in.value or ""),
            account_id=str(account_in.value or ""),
            today=bool(today_only.value),
        )]
        filtered_total = len(batches)
        visible_batches = batches[: int(runtime["visible_limit"])]
        queue_count_label.set_text(f"{len(visible_batches)} 条")
        with host:
            if not visible_batches:
                with ui.element("div").classes("card w-full"):
                    ui.label("没有符合条件的批次").classes("text-weight-medium")
                    ui.label("可取消筛选或显示已归档批次。").classes("muted")
                return
            focused_expansion = None
            for batch in visible_batches:
                expansion = _render_batch_card(
                    state,
                    service,
                    batch,
                    render,
                    review_runtime=runtime,
                    focused=(
                        str(batch.get("id") or "")
                        == str(runtime.get("focus_batch_id") or "")
                    ),
                    auto_expand=False,
                )
                if (
                    str(batch.get("id") or "")
                    == str(runtime.get("focus_batch_id") or "")
                ):
                    focused_expansion = expansion
            if focused_expansion is not None:
                focused_expansion.run_method(
                    "scrollIntoView",
                    {"behavior": "smooth", "block": "start"},
                )
                runtime["focus_batch_id"] = ""
            if filtered_total > len(visible_batches):
                remaining = filtered_total - len(visible_batches)
                ui.button(
                    f"加载更多批次（剩余 {remaining} 个）",
                    on_click=lambda: (
                        runtime.__setitem__(
                            "visible_limit",
                            int(runtime["visible_limit"]) + 4,
                        ),
                        render(),
                    ),
                ).props("outline color=teal-9 no-caps icon=expand_more").classes(
                    "self-center q-my-md"
                )

    def refresh_and_focus(
        batch_id: str | None = None,
        *,
        status_filter: str | None = None,
        today: bool | None = None,
        entry_mode: str = "activity",
    ) -> None:
        """Show a newly created batch even when stale filters were active."""
        if batch_id:
            search_in.value = ""
            status_in.value = ""
            account_in.value = ""
            today_only.value = False
            archived_in.value = False
            runtime["focus_batch_id"] = str(batch_id)
            runtime["completion_batch_id"] = (
                str(batch_id) if entry_mode == "completion" else ""
            )
            runtime["inbox_bucket"] = "review"
            runtime["visible_limit"] = 4
            view_in.value = "inbox"
            status_in.set_visibility(False)
            batch_only_filters.set_visibility(False)
        elif status_filter is not None or today is not None:
            runtime["completion_batch_id"] = ""
            search_in.value = ""
            account_in.value = ""
            archived_in.value = False
            today_only.value = bool(today)
            runtime["visible_limit"] = 4
            requested_status = str(status_filter or "")
            if requested_status in {"ready_for_review", "ready_for_draft"}:
                inbox_bucket = (
                    "review"
                    if requested_status == "ready_for_review"
                    else "ready_for_draft"
                )
                runtime["inbox_bucket"] = inbox_bucket
                view_in.value = "inbox"
                queue_segment.set_value(inbox_bucket)
                status_in.value = ""
                status_in.set_visibility(False)
                batch_only_filters.set_visibility(False)
            else:
                view_in.value = "batches"
                status_in.value = requested_status
                queue_segment.set_value(
                    requested_status
                    if requested_status in {"active", "failed"}
                    else "batches"
                )
                status_in.set_visibility(True)
                batch_only_filters.set_visibility(True)
        render()

    def reset_and_render(_: Any = None) -> None:
        if runtime.get("syncing_controls"):
            return
        runtime["visible_limit"] = 4
        render()
        hide_interaction_feedback(owner_client)

    def switch_view(event: Any) -> None:
        if runtime.get("syncing_controls"):
            return
        runtime["completion_batch_id"] = ""
        show_batches = str(event.value or "inbox") == "batches"
        status_in.set_visibility(show_batches)
        batch_only_filters.set_visibility(show_batches)
        runtime["visible_limit"] = 4
        render()
        hide_interaction_feedback(owner_client)

    def show_running_tasks() -> None:
        runtime["completion_batch_id"] = ""
        runtime["syncing_controls"] = True
        try:
            view_in.value = "batches"
            status_in.value = "active"
            archived_in.value = False
            today_only.value = False
            status_in.set_visibility(True)
            batch_only_filters.set_visibility(True)
        finally:
            runtime["syncing_controls"] = False
        runtime["visible_limit"] = 4
        render()
        hide_interaction_feedback(owner_client)

    def show_archived_tasks() -> None:
        runtime["completion_batch_id"] = ""
        runtime["syncing_controls"] = True
        try:
            view_in.value = "batches"
            status_in.value = ""
            archived_in.value = True
            today_only.value = False
            status_in.set_visibility(True)
            batch_only_filters.set_visibility(True)
        finally:
            runtime["syncing_controls"] = False
        runtime["visible_limit"] = 4
        render()
        hide_interaction_feedback(owner_client)

    def switch_queue_segment(event: Any) -> None:
        value = str(event.value or "review")
        runtime["completion_batch_id"] = ""
        runtime["visible_limit"] = 4
        runtime["syncing_controls"] = True
        try:
            archived_in.value = False
            today_only.value = False
            if value in {"review", "ready_for_draft"}:
                view_in.value = "inbox"
                runtime["inbox_bucket"] = value
                status_in.value = ""
            elif value == "active":
                view_in.value = "batches"
                status_in.value = "active"
            elif value == "failed":
                view_in.value = "batches"
                status_in.value = "failed"
            else:
                view_in.value = "batches"
                status_in.value = ""
        finally:
            runtime["syncing_controls"] = False
        render()
        hide_interaction_feedback(owner_client)

    view_in.on_value_change(switch_view)
    queue_segment.on_value_change(switch_queue_segment)
    for element in (search_in, status_in, today_only, archived_in):
        element.on_value_change(reset_and_render)

    def change_account_filter(event: Any) -> None:
        if str(event.value or "") == "__refresh__":
            account_in.set_value("")
            return
        reset_and_render()

    attach_interaction_feedback(
        queue_segment,
        "正在加载任务列表",
        event="update:model-value",
    )
    attach_interaction_feedback(
        account_in,
        "正在筛选公众号任务",
        event="update:model-value",
    )
    attach_interaction_feedback(today_filter_btn, "正在筛选今天的任务")
    attach_interaction_feedback(running_tasks_btn, "正在加载后台任务")
    attach_interaction_feedback(archive_tasks_btn, "正在加载归档任务")
    account_in.on_value_change(change_account_filter)
    running_tasks_btn.on_click(show_running_tasks)
    archive_tasks_btn.on_click(show_archived_tasks)
    render()
    state.task_center_refresh = refresh_and_focus

    if initial_batch_id and initial_job_id:
        def open_requested_review() -> None:
            try:
                open_review_workbench(
                    state,
                    service,
                    str(initial_batch_id),
                    int(initial_job_id),
                    render,
                    review_runtime=runtime,
                )
            except (KeyError, ValueError) as exc:
                ui.notify(
                    f"无法打开指定审核文章：{exc}",
                    type="negative",
                    timeout=10000,
                )

        client_timer(0.15, open_requested_review, once=True)

    # Do not replace interactive queue rows on a fixed timer. Background
    # progress remains live in the shared activity panel, while this queue is
    # refreshed explicitly so a user's click cannot be discarded mid-render.


def _ui_client_alive(owner_client: Any | None) -> bool:
    """Return false once NiceGUI has deleted the page owning an async action."""

    return owner_client is None or not bool(
        getattr(owner_client, "is_deleted", False)
    )


def _set_retry_loading_safely(
    button: Any,
    value: bool,
    *,
    owner_client: Any | None,
) -> None:
    if not _ui_client_alive(owner_client):
        return
    try:
        set_button_loading(button, value)
    except RuntimeError:
        # The client can disappear between the liveness check and element update.
        return


async def _retry_job_with_loading(
    service: BatchService,
    batch_id: str,
    job_id: int,
    button: Any,
    *,
    step: str = "auto",
    model_id: str | None = None,
    source_url: str | None = None,
    raw_content: str | None = None,
    owner_client: Any | None = None,
) -> dict[str, Any]:
    """Submit one recovery request while keeping its trigger state consistent."""

    _set_retry_loading_safely(
        button,
        True,
        owner_client=owner_client,
    )
    try:
        return await run.io_bound(
            lambda: service.retry_job(
                batch_id,
                job_id,
                step=step,
                model_id=model_id,
                source_url=source_url,
                raw_content=raw_content,
            )
        )
    finally:
        _set_retry_loading_safely(
            button,
            False,
            owner_client=owner_client,
        )


def open_retry_job_dialog(
    state: AppState,
    service: BatchService,
    item: dict[str, Any],
    refresh: Callable[[], None],
) -> None:
    """Open explicit recovery controls for one failed inbox article."""

    owner_client = ui.context.client
    nested_job = (
        dict(item.get("job") or {})
        if isinstance(item.get("job"), dict)
        else {}
    )
    batch_id = str(item.get("batch_id") or "")
    job_id = int(item.get("job_id") or nested_job.get("id") or 0)
    if not batch_id or job_id <= 0:
        ui.notify("该失败记录缺少批次或文章标识，暂时无法恢复", type="negative")
        return

    step_options = {
        "auto": "自动：从失败步骤恢复",
        "ingest": "读取来源 / 原文",
        "rewrite": "改写正文",
        "title_optimize": "优化标题",
        "render": "重新排版",
        "images": "处理正文配图",
        "inject": "写入公众号草稿箱",
    }
    model_options = {
        "": "沿用公众号当前文章模型",
        **state.model_options(include_default=False),
    }

    with ui.dialog() as dialog, ui.card().classes("q-pa-lg ops-dialog-md"):
        ui.label("恢复失败文章").classes("text-h6 text-weight-bold")
        ui.label(
            "默认从系统识别的失败步骤继续；也可以指定步骤并临时替换本次恢复使用的输入。"
        ).classes("muted")
        step_in = ui.select(
            options=step_options,
            value="auto",
            label="恢复步骤",
        ).classes("w-full").props("outlined stack-label options-dense")
        model_in = ui.select(
            options=model_options,
            value="",
            label="临时文本模型（可选）",
        ).classes("w-full").props("outlined stack-label options-dense")
        state.register_model_select(
            model_in,
            purpose="text",
            default_label="沿用公众号当前文章模型",
            owner=dialog,
        )
        ui.label(
            "临时模型只影响本次恢复中的正文改写和标题优化，不会修改公众号默认配置。"
        ).classes("muted text-caption")
        source_url_in = ui.input(
            "替换来源链接（可选）",
            value=str(item.get("source_url") or nested_job.get("source_url") or ""),
        ).classes("w-full").props("outlined stack-label")
        raw_content_in = ui.textarea(
            "粘贴替换原文（可选）",
            value="",
        ).classes("w-full").props("outlined stack-label autogrow")

        async def submit_retry() -> None:
            try:
                await _retry_job_with_loading(
                    service,
                    batch_id,
                    job_id,
                    retry_btn,
                    step=str(step_in.value or "auto"),
                    model_id=str(model_in.value or "").strip() or None,
                    source_url=str(source_url_in.value or "").strip() or None,
                    raw_content=str(raw_content_in.value or "").strip() or None,
                    owner_client=owner_client,
                )
            except Exception as exc:  # noqa: BLE001
                if not _ui_client_alive(owner_client):
                    return
                try:
                    ui.notify(
                        f"提交恢复失败：{sanitize_failure_text(exc)}",
                        type="negative",
                        timeout=10000,
                    )
                except RuntimeError:
                    return
                return
            if not _ui_client_alive(owner_client):
                return
            try:
                dialog.close()
                ui.notify(
                    "已提交恢复任务，将从所选步骤继续",
                    type="positive",
                )
                refresh()
            except RuntimeError:
                return

        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("取消", on_click=dialog.close).props(
                "flat color=grey-8 no-caps"
            )
            retry_btn = ui.button(
                "开始恢复",
                on_click=submit_retry,
            ).props("unelevated color=teal-9 no-caps icon=restart_alt")
    dialog.open()


def _failure_action_retry_step(
    action: str,
    failure: dict[str, Any],
    *,
    fallback_step: str = "",
) -> str | None:
    explicit = {
        "retry_ingest": "ingest",
        "retry_rewrite": "rewrite",
        "retry_title": "title_optimize",
        "retry_render": "render",
        "retry_images": "images",
        "retry_inject": "inject",
    }
    if action in explicit:
        return explicit[action]
    if action != "retry_step":
        return None
    stage = str(failure.get("stage") or fallback_step or "").strip().lower()
    return {
        "ingesting": "ingest",
        "rewriting": "rewrite",
        "title": "title_optimize",
        "title_optimizing": "title_optimize",
        "rendering": "render",
        "image": "images",
        "injecting": "inject",
    }.get(stage, stage if stage in {
        "ingest",
        "rewrite",
        "title_optimize",
        "render",
        "images",
        "inject",
    } else "auto")


def _settings_action_message(action: str) -> str:
    return {
        "open_account_settings": (
            "请关闭当前任务弹窗，前往“设置 → 公众号 → 管理 → 基础信息”"
            "更新凭证并测试连接。"
        ),
        "open_template_settings": (
            "请关闭当前任务弹窗，前往“设置 → 公众号 → 管理 → 草稿模板”"
            "重新选择或同步模板。"
        ),
    }.get(action, "")


def _render_inbox_article_card(
    state: AppState,
    service: BatchService,
    item: dict[str, Any],
    refresh: Callable[[], None],
    *,
    review_runtime: dict[str, bool] | None,
    on_show_batch: Callable[[str], None],
) -> Any:
    """Render one decision-oriented inbox row."""

    owner_client = ui.context.client
    nested_job = (
        dict(item.get("job") or {})
        if isinstance(item.get("job"), dict)
        else {}
    )
    batch_id = str(item.get("batch_id") or "")
    job_id = int(item.get("job_id") or nested_job.get("id") or 0)
    status = str(item.get("status") or nested_job.get("status") or "")
    review_status = str(
        item.get("review_status")
        or nested_job.get("review_status")
        or "unviewed"
    )
    title = str(
        item.get("title")
        or nested_job.get("selected_title")
        or "尚未选择标题"
    )
    account_name = str(
        item.get("account_name")
        or nested_job.get("account_name")
        or "公众号"
    )
    failure_value = item.get("failure")
    failure_recommendation = ""
    failure_actions: list[str] = []
    if isinstance(failure_value, dict):
        failure = "：".join(
            part
            for part in (
                str(failure_value.get("title") or "").strip(),
                str(failure_value.get("reason") or "").strip(),
            )
            if part
        )
        failure_recommendation = str(
            failure_value.get("recommendation") or ""
        ).strip()
        failure_actions = list(dict.fromkeys(
            str(action).strip()
            for action in list(failure_value.get("actions") or [])
            if str(action).strip()
        ))
        if (
            not failure_recommendation
            and any(
                action in {"replace_url", "paste_text"}
                for action in failure_actions
            )
        ):
            failure_recommendation = (
                "可在“恢复选项”中替换来源链接或粘贴原文后再恢复。"
            )
    else:
        failure = str(
            failure_value
            or nested_job.get("error")
            or ""
        )
    source = str(
        item.get("source")
        or item.get("batch_topic")
        or ""
    )
    source_url = str(item.get("source_url") or "")
    body_chars = int(item.get("body_chars") or 0)
    priority_reason = str(item.get("priority_reason") or "")
    latest_review_summary = item.get("latest_review_summary")
    if isinstance(latest_review_summary, dict):
        latest_review_summary = (
            latest_review_summary.get("conclusion")
            or latest_review_summary.get("summary")
            or ""
        )
    blocker_texts = [
        str(blocker.get("message") or blocker.get("label") or blocker)
        if isinstance(blocker, dict)
        else str(blocker)
        for blocker in list(item.get("blockers") or [])
        if blocker
    ]
    is_reviewable = (
        status == "ready_for_review"
        and review_status != "confirmed"
        and bool(batch_id)
        and job_id > 0
    )
    badge_text = (
        REVIEW_LABELS.get(review_status, review_status)
        if status == "ready_for_review"
        else (
            "写入失败"
            if str(item.get("step") or nested_job.get("step") or "")
            == "inject"
            else STATUS_LABEL.get(status, status)
        )
    )
    badge_color = (
        REVIEW_COLORS.get(review_status, "orange-8")
        if status == "ready_for_review"
        else _job_color(status)
    )

    async def submit_action_retry(
        step: str,
        button: Any,
        *,
        success_message: str,
    ) -> None:
        if step == "inject":
            account_id = str(
                item.get("account_id")
                or nested_job.get("account_id")
                or ""
            ).strip()
            if account_id:
                try:
                    reports = await run.io_bound(
                        lambda: service.preflight(
                            [account_id],
                            force_wechat_check=True,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    if has_ip_whitelist_issue(exc) and _ui_client_alive(
                        owner_client
                    ):
                        show_ip_whitelist_guide([account_name])
                        return
                else:
                    if has_ip_whitelist_issue(reports) and _ui_client_alive(
                        owner_client
                    ):
                        show_ip_whitelist_guide([account_name])
                        return
        try:
            await _retry_job_with_loading(
                service,
                batch_id,
                job_id,
                button,
                step=step,
                owner_client=owner_client,
            )
        except Exception as exc:  # noqa: BLE001
            if _ui_client_alive(owner_client):
                if has_ip_whitelist_issue(exc):
                    show_ip_whitelist_guide([account_name])
                    return
                ui.notify(
                    f"提交恢复失败：{sanitize_failure_text(exc)}",
                    type="negative",
                    timeout=10000,
                )
            return
        if not _ui_client_alive(owner_client):
            return
        ui.notify(success_message, type="positive")
        refresh()

    async def check_relay_connection(button: Any) -> None:
        _set_retry_loading_safely(
            button,
            True,
            owner_client=owner_client,
        )
        try:
            reports = await run.io_bound(
                lambda: service.preflight(
                    [str(item.get("account_id") or nested_job.get("account_id") or "")],
                    force_wechat_check=True,
                )
            )
            if not _ui_client_alive(owner_client):
                return
            report = dict(reports[0]) if reports else {}
            if has_ip_whitelist_issue(report):
                show_ip_whitelist_guide([account_name])
                return
            failed_checks = [
                str(check.get("message") or check.get("label") or "")
                for check in list(report.get("checks") or [])
                if not bool(check.get("ok"))
            ]
            if bool(report.get("can_write")):
                ui.notify("云端连接与公众号写入接口检测正常", type="positive")
            else:
                ui.notify(
                    "连接检测未通过："
                    + ("；".join(failed_checks) or "请检查公众号或中转配置"),
                    type="warning",
                    timeout=12000,
                )
        except Exception as exc:  # noqa: BLE001
            if _ui_client_alive(owner_client):
                if has_ip_whitelist_issue(exc):
                    show_ip_whitelist_guide([account_name])
                    return
                ui.notify(
                    f"连接检测失败：{sanitize_failure_text(exc)}",
                    type="negative",
                    timeout=10000,
                )
        finally:
            _set_retry_loading_safely(
                button,
                False,
                owner_client=owner_client,
            )

    def bind_retry_action(
        button: Any,
        step: str,
        success_message: str,
    ) -> None:
        async def handle() -> None:
            await submit_action_retry(
                step,
                button,
                success_message=success_message,
            )

        button.on_click(handle)

    def bind_relay_check(button: Any) -> None:
        async def handle() -> None:
            await check_relay_connection(button)

        button.on_click(handle)

    def open_article_review() -> None:
        open_review_page = (
            review_runtime.get("open_review_page")
            if review_runtime is not None
            else None
        )
        if callable(open_review_page):
            if review_runtime is not None:
                review_runtime["review_open"] = True
            open_review_page(batch_id, job_id)
            return
        open_review_workbench(
            state,
            service,
            batch_id,
            job_id,
            refresh,
            review_runtime=review_runtime,
        )

    with ui.dialog() as details_dialog, ui.card().classes(
        "ops-task-detail-dialog"
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("任务详情与恢复操作").classes(
                "text-h6 text-weight-medium"
            )
            ui.button(icon="close", on_click=details_dialog.close).props(
                "flat round dense aria-label=关闭任务详情"
            )
        with ui.row().classes("w-full items-start justify-between"):
            with ui.column().classes("gap-1 ops-flex-copy"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(account_name).classes("text-weight-bold")
                    ui.badge(badge_text).props(f"color={badge_color}")
                ui.label(title).classes("text-subtitle1 text-weight-medium")
                details = [
                    f'批次 #{item.get("batch_display_id") or batch_id}',
                    _format_time(item.get("created_at")),
                ]
                if body_chars:
                    details.append(f"{body_chars} 字")
                ui.label(" · ".join(details)).classes("muted text-caption")
            if priority_reason:
                ui.badge(priority_reason).props(
                    "outline color=deep-orange-7"
                )
        if source:
            ui.label(f"来源：{source}").classes("muted")
        if source_url:
            ui.link("打开来源", source_url, new_tab=True).classes(
                "text-teal-9 text-caption"
            )
        ui.label(
            f"最近 AI 评审：{latest_review_summary or '尚未评审'}"
        ).classes("text-indigo-8 text-caption")
        cover_status = str(item.get("cover_status") or "")
        if cover_status:
            ui.label(
                "封面："
                + {
                    "ready": "已选择",
                    "generated": "AI 封面待确认",
                    "missing": "尚未选择",
                }.get(cover_status, cover_status)
            ).classes("muted text-caption")
        if blocker_texts:
            ui.label("阻塞项：" + "；".join(blocker_texts)).classes(
                "text-warning text-caption"
            )
        if failure:
            ui.label(
                failure
                if isinstance(failure_value, dict)
                else _friendly_error(failure)
            ).classes(
                "text-negative text-caption"
            )
            if failure_recommendation:
                ui.label(f"建议：{failure_recommendation}").classes(
                    "text-warning text-caption"
                )
        recommended_action = str(item.get("recommended_action") or "").strip()
        if recommended_action:
            ui.label(f"推荐下一步：{recommended_action}").classes(
                "text-teal-9 text-caption text-weight-medium"
            )
        if failure_actions:
            known_actions = {
                "replace_url",
                "paste_text",
                "retry_ingest",
                "retry_rewrite",
                "retry_title",
                "retry_render",
                "retry_images",
                "retry_inject",
                "retry_step",
                "open_account_settings",
                "open_template_settings",
                "open_relay_settings",
                "show_ip_whitelist_guide",
                "check_relay",
                "reconcile_draft",
                "change_model",
                "copy_error",
            }
            visible_actions = [
                action for action in failure_actions if action in known_actions
            ]
            with ui.row().classes(
                "w-full items-center gap-2 q-mt-xs failure-action-row"
            ):
                if any(
                    action in {"replace_url", "paste_text"}
                    for action in visible_actions
                ):
                    ui.button(
                        "替换链接 / 粘贴正文",
                        on_click=lambda: open_retry_job_dialog(
                            state,
                            service,
                            item,
                            refresh,
                        ),
                    ).props(
                        "outline dense color=deep-orange-8 no-caps icon=edit_note"
                    )
                if "change_model" in visible_actions:
                    ui.button(
                        "更换模型后恢复",
                        on_click=lambda: open_retry_job_dialog(
                            state,
                            service,
                            item,
                            refresh,
                        ),
                    ).props(
                        "outline dense color=indigo-7 no-caps icon=memory"
                    )
                rendered_retry_steps: set[str] = set()
                for action in visible_actions:
                    retry_step = _failure_action_retry_step(
                        action,
                        dict(failure_value or {})
                        if isinstance(failure_value, dict)
                        else {},
                        fallback_step=str(
                            item.get("step") or nested_job.get("step") or ""
                        ),
                    )
                    if not retry_step or retry_step in rendered_retry_steps:
                        continue
                    rendered_retry_steps.add(retry_step)
                    retry_label = {
                        "ingest": "仅重试抓取",
                        "rewrite": "仅重试正文",
                        "title_optimize": "仅重试标题",
                        "render": "仅重试排版",
                        "images": "仅重试配图",
                        "inject": "仅重试写入",
                        "auto": "从失败步骤重试",
                    }.get(retry_step, "从失败步骤重试")
                    action_button = ui.button(
                        retry_label,
                    ).props(
                        "outline dense color=deep-orange-8 no-caps "
                        "icon=restart_alt"
                    )
                    bind_retry_action(
                        action_button,
                        retry_step,
                        f"已提交{retry_label}任务",
                    )
                for action in (
                    "open_account_settings",
                    "open_template_settings",
                ):
                    if action not in visible_actions:
                        continue
                    setting_label = {
                        "open_account_settings": "查看公众号配置",
                        "open_template_settings": "查看模板配置",
                    }[action]
                    ui.button(
                        setting_label,
                        on_click=lambda _=None, value=action: ui.notify(
                            _settings_action_message(value),
                            type="info",
                            timeout=12000,
                        ),
                    ).props(
                        "flat dense color=teal-9 no-caps icon=settings"
                    )
                if any(
                    action in visible_actions
                    for action in (
                        "open_relay_settings",
                        "show_ip_whitelist_guide",
                    )
                ):
                    ui.button(
                        "查看 IP 白名单配置教程",
                        on_click=lambda: show_ip_whitelist_guide(
                            [account_name]
                        ),
                    ).props(
                        "outline dense color=deep-orange-8 no-caps "
                        "icon=help_outline"
                    )
                if "check_relay" in visible_actions:
                    relay_button = ui.button(
                        "重新检测连接",
                    ).props(
                        "outline dense color=teal-9 no-caps icon=network_check"
                    )
                    bind_relay_check(relay_button)
                if "reconcile_draft" in visible_actions:
                    reconcile_button = ui.button(
                        "安全对账草稿",
                    ).props(
                        "unelevated dense color=deep-orange-8 no-caps "
                        "icon=sync_problem"
                    )
                    bind_retry_action(
                        reconcile_button,
                        "inject",
                        "已提交安全对账；系统不会盲目重复写入草稿",
                    )
                if "copy_error" in visible_actions:
                    copy_text = sanitize_failure_text(
                        (
                            failure_value.get("technical_summary")
                            if isinstance(failure_value, dict)
                            else ""
                        )
                        or nested_job.get("error")
                        or failure
                    )
                    ui.button(
                        "复制错误摘要",
                        on_click=lambda _=None, value=copy_text: (
                            ui.clipboard.write(value),
                            ui.notify("错误摘要已复制", type="positive"),
                        ),
                    ).props(
                        "flat dense color=grey-8 no-caps icon=content_copy"
                    )
        with ui.row().classes("w-full items-center justify-end q-mt-sm"):
            if is_reviewable:
                ui.button(
                    "打开审核",
                    on_click=open_article_review,
                ).props(
                    "unelevated color=teal-9 no-caps icon=rate_review"
                )
            else:
                if status == "failed" and batch_id and job_id > 0:
                    async def retry_from_failed_step() -> None:
                        try:
                            await _retry_job_with_loading(
                                service,
                                batch_id,
                                job_id,
                                retry_btn,
                                step="auto",
                                owner_client=owner_client,
                            )
                        except Exception as exc:  # noqa: BLE001
                            if not _ui_client_alive(owner_client):
                                return
                            try:
                                ui.notify(
                                    "提交恢复失败："
                                    f"{sanitize_failure_text(exc)}",
                                    type="negative",
                                    timeout=10000,
                                )
                            except RuntimeError:
                                return
                            return
                        if not _ui_client_alive(owner_client):
                            return
                        try:
                            ui.notify(
                                "已提交恢复任务，将从失败步骤继续",
                                type="positive",
                            )
                            refresh()
                        except RuntimeError:
                            return

                    retry_btn = ui.button(
                        "从失败步骤重试",
                        on_click=retry_from_failed_step,
                    ).props(
                        "unelevated color=deep-orange-8 no-caps "
                        "icon=restart_alt"
                    )
                    ui.button(
                        "恢复选项",
                        on_click=lambda: open_retry_job_dialog(
                            state,
                            service,
                            item,
                            refresh,
                        ),
                    ).props(
                        "outline color=deep-orange-8 no-caps icon=tune"
                    )
                ui.button(
                    "查看所在批次",
                    on_click=lambda: on_show_batch(batch_id),
                ).props(
                    "outline color=teal-9 no-caps icon=inventory_2"
                )
    primary_label = "查看任务"
    primary_icon = "open_in_new"
    primary_action: Callable[[], None] = lambda: on_show_batch(batch_id)
    if is_reviewable:
        primary_label = "打开审核"
        primary_icon = None
        primary_action = open_article_review
    elif status == "failed":
        primary_label = "恢复失败任务"
        primary_icon = "restart_alt"
        primary_action = details_dialog.open
    elif status in {"pending", "processing", "injecting"}:
        primary_label = "查看进度"
        primary_icon = "monitor_heart"
    elif status == "ready_for_review" and review_status == "confirmed":
        primary_label = "写入草稿"
        primary_icon = "send"

    with ui.card().classes("ops-task-row-card") as card:
        with ui.element("span").classes(
            "ops-task-row-icon ops-icon-blue"
        ):
            ui.icon(
                "rate_review" if is_reviewable else "article",
                size="20px",
            ).classes("ops-semantic-icon")
        with ui.column().classes("ops-task-row-copy"):
            ui.label(title).classes("ops-task-row-title")
            ui.label(
                f"{account_name} · 批次 #{item.get('batch_display_id') or batch_id}"
            ).classes("ops-task-row-meta")
        ui.badge(badge_text).props(f"color={badge_color}").classes(
            "ops-task-row-badge"
        )
        ui.label(
            recommended_action
            or (failure_recommendation if failure else "状态已同步")
        ).classes("ops-task-row-state")
        with ui.row().classes("ops-task-row-actions"):
            ui.button(
                primary_label,
                icon=primary_icon,
                on_click=primary_action,
            ).classes("ops-task-row-primary-action").props(
                "outline dense color=primary no-caps"
            )
            ui.button(icon="more_horiz", on_click=details_dialog.open).props(
                "flat round dense color=grey-7 aria-label=查看任务详情"
            ).tooltip("查看任务详情")
    return card


def build_review_page(
    state: AppState,
    *,
    batch_id: str,
    job_id: int,
    on_back: Callable[[], None],
    on_open_review: Callable[[str, int], None],
) -> None:
    """Build the approved non-modal, full-page article review workspace."""

    service = BatchService(
        load_config(),
        owner_user_id=str(getattr(state, "current_user_id", "") or ""),
        recover_stale_work=False,
    )
    service.mark_job_viewed(batch_id, job_id)
    batch = service.get_batch(batch_id, include_content=True)
    jobs = [dict(item) for item in list(batch.get("jobs") or [])]
    job = next(item for item in jobs if int(item.get("id") or 0) == int(job_id))
    index = next(
        idx for idx, item in enumerate(jobs) if int(item.get("id") or 0) == int(job_id)
    )
    previous_job = jobs[index - 1] if index > 0 else None
    next_job = jobs[index + 1] if index + 1 < len(jobs) else None
    owner_client = ui.context.client
    page_alive = {"value": True}

    try:
        latest_reviews = service.list_editorial_reviews(job_id=job_id, limit=1)
    except Exception:  # noqa: BLE001
        latest_reviews = []
    latest_review = dict(latest_reviews[0]) if latest_reviews else {}
    review_result = dict(latest_review.get("result") or {})
    issues = [
        dict(issue)
        for issue in list(review_result.get("issues") or [])
        if isinstance(issue, dict)
    ]
    selected_issue_ids: set[str] = {
        str(issue.get("id") or "")
        for issue in issues
        if str(issue.get("id") or "")
        and not bool(issue.get("blocks_draft"))
    }

    def alive() -> bool:
        return page_alive["value"] and not bool(
            getattr(owner_client, "is_deleted", False)
        )

    def reopen(target: dict[str, Any] | None = None) -> None:
        target_job = target or job
        page_alive["value"] = False
        on_open_review(batch_id, int(target_job["id"]))

    with ui.element("div").classes("ops-review-bar"):
        with ui.row().classes("ops-review-title"):
            ui.button(icon="arrow_back", on_click=on_back).classes(
                "ops-icon-button"
            ).props("flat round dense aria-label=返回任务")
            with ui.column().classes("gap-0"):
                ui.label("文章审核").classes("ops-review-page-title")
                ui.label(
                    f'{job.get("account_name") or "公众号"} · '
                    f'批次 #{batch.get("display_id") or batch_id} · '
                    f'第 {index + 1} / {len(jobs)} 篇'
                ).classes("ops-panel-subtitle")
        with ui.row().classes("ops-review-controls"):
            previous_btn = ui.button(
                "上一篇",
                icon="chevron_left",
                on_click=lambda: reopen(previous_job),
            ).props("outline dense color=primary no-caps")
            next_btn = ui.button(
                "下一篇",
                icon="chevron_right",
                on_click=lambda: reopen(next_job),
            ).props("outline dense color=primary no-caps icon-right")
            previous_btn.set_enabled(previous_job is not None)
            next_btn.set_enabled(next_job is not None)

    review_tabs = ui.tabs().classes("ops-segment ops-review-mode-tabs").props(
        "dense align=justify indicator-color=transparent active-color=dark"
    )
    with review_tabs:
        preview_tab = ui.tab("成品预览")
        edit_tab = ui.tab("正文编辑")
        assets_tab = ui.tab("标题与图片")
        history_tab = ui.tab("历史版本")

    with ui.element("div").classes("ops-review-layout"):
        with ui.element("article").classes("ops-panel ops-review-document"):
            review_panels = ui.tab_panels(
                review_tabs,
                value=preview_tab,
            ).classes("ops-review-document-panels")
            with review_panels:
                with ui.tab_panel(preview_tab).classes("ops-review-mode-panel"):
                    with ui.element("div").classes("ops-document-tools"):
                        with ui.row().classes(
                            "ops-badge ops-badge-green ops-document-preview-badge"
                        ):
                            ui.icon("smartphone", size="15px").classes(
                                "ops-semantic-icon"
                            )
                            ui.label("微信公众号最终效果")
                        paragraph_count = max(0, str(job.get("body") or "").count("\n") + 1)
                        inline_count = len(
                            list(dict(job.get("meta") or {}).get("inline_images") or [])
                        )
                        ui.badge(
                            f"{paragraph_count} 段 · {inline_count} 张图"
                        ).classes("ops-badge")
                    ui.html(
                        prepare_preview_html(str(job.get("html_content") or "")),
                        sanitize=False,
                    ).classes("ops-document-canvas")

                with ui.tab_panel(edit_tab).classes("ops-review-mode-panel"):
                    with ui.element("div").classes("ops-review-editor-grid"):
                        title_in = ui.input(
                            "文章标题",
                            value=str(job.get("selected_title") or ""),
                        ).props("outlined dense stack-label")
                        subtitle_in = ui.input(
                            "副标题",
                            value=str(job.get("selected_subtitle") or ""),
                        ).props("outlined dense stack-label")
                        digest_in = ui.textarea(
                            "摘要",
                            value=str(job.get("digest") or ""),
                        ).classes("ops-review-digest-editor").props(
                            "outlined rows=3 stack-label"
                        )
                        body_in = ui.textarea(
                            "正文",
                            value=str(job.get("body") or ""),
                        ).classes("ops-review-body-editor").props(
                            "outlined rows=18 stack-label"
                        )

                        async def save_article() -> None:
                            set_button_loading(save_article_btn, True)
                            try:
                                await run.io_bound(
                                    lambda: service.update_job_content(
                                        batch_id,
                                        job_id,
                                        title=str(title_in.value or ""),
                                        subtitle=str(subtitle_in.value or ""),
                                        digest=str(digest_in.value or ""),
                                        body=str(body_in.value or ""),
                                    )
                                )
                                await run.io_bound(
                                    lambda: service.rerender_job(batch_id, job_id)
                                )
                                if alive():
                                    ui.notify("文章修改已保存并重新排版", type="positive")
                                    reopen()
                            except Exception as exc:  # noqa: BLE001
                                if alive():
                                    ui.notify(
                                        f"保存失败：{sanitize_failure_text(exc)}",
                                        type="negative",
                                        timeout=10000,
                                    )
                            finally:
                                if alive():
                                    set_button_loading(save_article_btn, False)

                        save_article_btn = ui.button(
                            "保存文章修改",
                            icon="save",
                            on_click=save_article,
                        ).props("unelevated color=primary no-caps")

                with ui.tab_panel(assets_tab).classes("ops-review-mode-panel"):
                    with ui.element("div").classes("ops-assets-grid"):
                        with ui.element("section").classes("ops-config-section"):
                            ui.label("标题候选").classes("ops-panel-title")
                            title_options = clean_titles(job)
                            selected_title = str(
                                job.get("selected_title") or ""
                            ).strip()
                            if selected_title and selected_title not in title_options:
                                title_options.insert(0, selected_title)
                            title_choice = ui.radio(
                                {title: title for title in title_options},
                                value=(
                                    selected_title
                                    if selected_title in title_options
                                    else None
                                ),
                            ).classes("ops-title-candidates")

                            async def apply_title() -> None:
                                if not title_choice.value:
                                    return
                                await run.io_bound(
                                    lambda: service.update_job_content(
                                        batch_id,
                                        job_id,
                                        title=str(title_choice.value),
                                    )
                                )
                                await run.io_bound(
                                    lambda: service.rerender_job(batch_id, job_id)
                                )
                                if alive():
                                    ui.notify("标题已更新", type="positive")
                                    reopen()

                            ui.button(
                                "使用所选标题",
                                on_click=apply_title,
                            ).props("outline dense color=primary no-caps")
                        with ui.element("section").classes("ops-config-section"):
                            ui.label("封面与正文配图").classes("ops-panel-title")
                            cover = dict(
                                dict(job.get("meta") or {}).get("generated_cover") or {}
                            )
                            cover_url = str(cover.get("url") or "")
                            if cover_url:
                                ui.image(wechat_image_proxy_url(cover_url)).classes(
                                    "ops-review-cover-preview"
                                )
                            else:
                                ui.label("当前未生成 AI 封面").classes(
                                    "ops-panel-subtitle"
                                )

                            async def regenerate_assets(kind: str) -> None:
                                try:
                                    if kind == "cover":
                                        await run.io_bound(
                                            lambda: service.regenerate_cover(
                                                batch_id, job_id
                                            )
                                        )
                                    else:
                                        await run.io_bound(
                                            lambda: service.regenerate_inline_images(
                                                batch_id, job_id
                                            )
                                        )
                                    if alive():
                                        ui.notify("图片任务已提交", type="positive")
                                        reopen()
                                except Exception as exc:  # noqa: BLE001
                                    if alive():
                                        ui.notify(
                                            f"图片处理失败：{sanitize_failure_text(exc)}",
                                            type="negative",
                                        )

                            with ui.row().classes("ops-assets-actions"):
                                ui.button(
                                    "重新生成封面",
                                    on_click=lambda: regenerate_assets("cover"),
                                ).props("outline dense color=primary no-caps")
                                ui.button(
                                    "重新生成正文配图",
                                    on_click=lambda: regenerate_assets("inline"),
                                ).props("outline dense color=primary no-caps")

                with ui.tab_panel(history_tab).classes("ops-review-mode-panel"):
                    versions = service.list_job_versions(batch_id, job_id)
                    if not versions:
                        ui.label("当前还没有可恢复的历史版本").classes(
                            "ops-empty-state"
                        )
                    for version in versions[:8]:
                        with ui.element("div").classes("ops-history-row"):
                            with ui.column().classes("ops-flex-copy gap-0"):
                                ui.label(
                                    str(version.get("reason") or "自动保存")
                                ).classes("ops-panel-title")
                                ui.label(
                                    _format_time(version.get("created_at"))
                                ).classes("ops-panel-subtitle")

                            async def restore_page_version(
                                version_id: int = int(version["id"]),
                            ) -> None:
                                await run.io_bound(
                                    lambda: service.restore_job_version(
                                        batch_id, job_id, version_id
                                    )
                                )
                                if alive():
                                    ui.notify(
                                        "历史版本已恢复，文章需要重新确认",
                                        type="positive",
                                    )
                                    reopen()

                            ui.button(
                                "恢复此版本",
                                on_click=restore_page_version,
                            ).props("outline dense color=primary no-caps")

        with ui.element("aside").classes("ops-review-side"):
            with ui.element("section").classes("ops-panel ops-review-ai-panel"):
                with ui.element("div").classes("ops-panel-heading"):
                    with ui.column().classes("gap-0"):
                        ui.label("AI 评审结论").classes("ops-panel-title")
                        ui.label(
                            str(latest_review.get("profile_name") or "专业深度型")
                        ).classes("ops-panel-subtitle")
                    review_status = str(latest_review.get("status") or "")
                    ui.badge(
                        {
                            "completed": "评审完成",
                            "candidate_ready": "候选稿待选择",
                            "running": "评审中",
                            "rewriting": "改写中",
                            "failed": "评审失败",
                        }.get(review_status, "尚未评审")
                    ).classes(
                        "ops-badge "
                        + (
                            "ops-badge-green"
                            if review_status in {"completed", "applied", "source_kept"}
                            else "ops-badge-warm"
                        )
                    )
                with ui.element("div").classes("ops-panel-body"):
                    score = int(
                        review_result.get("overall_score")
                        or latest_review.get("overall_score")
                        or 0
                    )
                    with ui.element("div").classes("ops-score-line"):
                        ui.label(str(score or "—")).classes("ops-score")
                        with ui.column().classes("gap-0 ops-flex-copy"):
                            ui.label(
                                str(
                                    review_result.get("conclusion")
                                    or review_result.get("summary")
                                    or "等待 AI 评审结论"
                                )
                            ).classes("ops-review-conclusion")
                            ui.label(
                                "AI 建议不会自动覆盖当前文章。"
                            ).classes("ops-review-summary")

                    with ui.element("div").classes("ops-issue-list"):
                        for issue in issues[:3]:
                            issue_id = str(issue.get("id") or "")
                            blocking = bool(issue.get("blocks_draft"))
                            with ui.element("div").classes(
                                "ops-issue ops-issue-risk" if blocking else "ops-issue"
                            ):
                                ui.label(
                                    "阻断项" if blocking else "可优化"
                                ).classes("ops-issue-label")
                                ui.label(
                                    str(
                                        issue.get("message")
                                        or issue.get("description")
                                        or issue.get("title")
                                        or "评审建议"
                                    )
                                )
                                if blocking and issue_id:
                                    async def resolve_issue(
                                        issue_value: str = issue_id,
                                    ) -> None:
                                        await run.io_bound(
                                            lambda: service.resolve_editorial_review_issue(
                                                str(latest_review["id"]),
                                                issue_value,
                                                resolution="resolved",
                                                note="运营人员已在审核页人工核实",
                                                resolved_by="桌面端运营人员",
                                            )
                                        )
                                        if alive():
                                            ui.notify("人工核实结果已保存", type="positive")
                                            reopen()

                                    ui.button(
                                        "已人工核实",
                                        on_click=resolve_issue,
                                    ).props("flat dense color=negative no-caps")
                                elif issue_id:
                                    checkbox = ui.checkbox(
                                        "纳入后台改写",
                                        value=issue_id in selected_issue_ids,
                                    ).props("dense")
                                    checkbox.on_value_change(
                                        lambda event, value=issue_id: (
                                            selected_issue_ids.add(value)
                                            if bool(event.value)
                                            else selected_issue_ids.discard(value)
                                        )
                                    )

                    async def mark_needs_changes() -> None:
                        await run.io_bound(
                            lambda: service.request_job_changes(batch_id, job_id)
                        )
                        if alive():
                            ui.notify("已标记为需要修改", type="warning")
                            reopen()

                    async def confirm_article() -> None:
                        current_reviews = await run.io_bound(
                            lambda: service.list_editorial_reviews(
                                job_id=job_id, limit=1
                            )
                        )
                        reason, _blocking_count = _review_confirmation_gate(
                            dict(current_reviews[0]) if current_reviews else None
                        )
                        if reason:
                            ui.notify(reason, type="warning", timeout=8000)
                            return
                        await run.io_bound(
                            lambda: service.confirm_job(batch_id, job_id)
                        )
                        if alive():
                            ui.notify("文章已确认", type="positive")
                            following = next_review_job(
                                service.get_batch(
                                    batch_id, include_content=False
                                ).get("jobs")
                                or [],
                                current_job_id=job_id,
                            )
                            if following:
                                on_open_review(batch_id, int(following["id"]))
                            else:
                                on_back()

                    with ui.element("div").classes("ops-review-footer-actions"):
                        ui.button(
                            "需要修改",
                            on_click=mark_needs_changes,
                        ).props("outline dense color=primary no-caps")
                        ui.button(
                            "确认此文章",
                            on_click=confirm_article,
                        ).props("unelevated dense color=primary no-caps")

            with ui.element("section").classes("ops-panel ops-review-job-panel"):
                progress = editorial_review_progress(latest_review)
                with ui.element("div").classes("ops-panel-heading"):
                    with ui.column().classes("gap-0"):
                        ui.label("后台任务").classes("ops-panel-title")
                        ui.label("评审和改写可离开页面继续运行").classes(
                            "ops-panel-subtitle"
                        )
                    ui.badge(
                        "1 运行中"
                        if str(latest_review.get("status") or "")
                        in {"running", "rewriting"}
                        else "0 运行中"
                    ).classes("ops-badge")
                with ui.element("div").classes("ops-panel-body"):
                    if str(latest_review.get("status") or "") in {
                        "running",
                        "rewriting",
                    }:
                        ui.label(str(progress.get("stage") or "处理中")).classes(
                            "ops-activity-stage"
                        )
                        ui.linear_progress(
                            value=float(progress.get("value") or 0.05)
                        ).classes("ops-activity-progress").props(
                            "rounded color=primary track-color=blue-1"
                        )
                        ui.label(
                            f'{round(float(progress.get("value") or 0) * 100)}%'
                        ).classes("ops-activity-percent")

                    async def run_review_background() -> None:
                        try:
                            await run.io_bound(
                                lambda: service.run_editorial_review(
                                    batch_id, job_id
                                )
                            )
                            if alive():
                                ui.notify("AI 评审已完成", type="positive")
                                reopen()
                        except Exception as exc:  # noqa: BLE001
                            if alive():
                                ui.notify(
                                    f"AI 评审失败：{sanitize_failure_text(exc)}",
                                    type="negative",
                                    timeout=10000,
                                )

                    def start_review_background() -> None:
                        asyncio.create_task(run_review_background())
                        ui.notify("AI 评审已转入后台，可继续使用其他功能", type="info")

                    async def run_rewrite_background() -> None:
                        try:
                            await run.io_bound(
                                lambda: service.generate_editorial_rewrite_candidate(
                                    batch_id,
                                    job_id,
                                    str(latest_review["id"]),
                                    issue_ids=sorted(selected_issue_ids),
                                    rewrite_mode="selected_issues",
                                )
                            )
                            if alive():
                                ui.notify(
                                    "改写候选稿已生成，请选择使用版本",
                                    type="positive",
                                )
                                reopen()
                        except Exception as exc:  # noqa: BLE001
                            if alive():
                                ui.notify(
                                    f"后台改写失败：{sanitize_failure_text(exc)}",
                                    type="negative",
                                    timeout=10000,
                                )

                    def start_rewrite_background() -> None:
                        if not latest_review:
                            ui.notify("请先完成 AI 评审", type="warning")
                            return
                        if not selected_issue_ids:
                            ui.notify("请至少勾选一条可改写意见", type="warning")
                            return
                        asyncio.create_task(run_rewrite_background())
                        ui.notify("已转入后台改写，可继续使用其他功能", type="info")

                    if not latest_review:
                        ui.button(
                            "后台开始 AI 评审",
                            icon="rate_review",
                            on_click=start_review_background,
                        ).classes("w-full").props(
                            "flat color=primary no-caps"
                        )
                    elif str(latest_review.get("status") or "") not in {
                        "running",
                        "rewriting",
                        "candidate_ready",
                    }:
                        ui.button(
                            "按已选意见后台改写",
                            icon="auto_fix_high",
                            on_click=start_rewrite_background,
                        ).classes("w-full").props(
                            "flat color=primary no-caps"
                        )

                    if str(latest_review.get("status") or "") == "candidate_ready":
                        applications = service.list_editorial_review_applications(
                            str(latest_review["id"]), limit=1
                        )
                        application = dict(applications[0]) if applications else {}
                        application_id = str(application.get("id") or "")
                        source_snapshot = dict(latest_review.get("source_snapshot") or {})
                        candidate_snapshot = dict(
                            latest_review.get("rewritten_snapshot")
                            or application.get("candidate_snapshot")
                            or {}
                        )

                        with ui.dialog() as comparison_dialog, ui.card().classes(
                            "ops-review-comparison-dialog"
                        ):
                            with ui.row().classes("w-full items-center justify-between"):
                                ui.label("改写前后对比").classes("ops-review-page-title")
                                ui.button(
                                    icon="close",
                                    on_click=comparison_dialog.close,
                                ).props("flat round dense aria-label=关闭对比")
                            with ui.element("div").classes("ops-comparison-grid"):
                                for label, snapshot in (
                                    ("改写前", source_snapshot),
                                    ("改写后", candidate_snapshot),
                                ):
                                    with ui.element("section").classes(
                                        "ops-comparison-column"
                                    ):
                                        ui.label(label).classes("ops-panel-title")
                                        ui.label(
                                            str(snapshot.get("title") or "")
                                        ).classes("ops-review-conclusion")
                                        ui.label(
                                            str(snapshot.get("body") or "")
                                        ).classes("ops-comparison-body")

                            async def choose_version(use_rewrite: bool) -> None:
                                comparison_dialog.close()
                                if use_rewrite:
                                    await run.io_bound(
                                        lambda: service.apply_editorial_review_application(
                                            batch_id, job_id, application_id
                                        )
                                    )
                                else:
                                    await run.io_bound(
                                        lambda: service.keep_editorial_review_source(
                                            batch_id, job_id, application_id
                                        )
                                    )
                                if alive():
                                    ui.notify("版本选择已保存", type="positive")
                                    reopen()

                            with ui.row().classes("w-full justify-end"):
                                ui.button(
                                    "使用改写前版本",
                                    on_click=lambda: choose_version(False),
                                ).props("outline color=primary no-caps")
                                ui.button(
                                    "使用改写后版本",
                                    on_click=lambda: choose_version(True),
                                ).props("unelevated color=primary no-caps")
                        ui.button(
                            "查看改写前后对比并选择版本",
                            icon="compare",
                            on_click=comparison_dialog.open,
                        ).classes("w-full").props(
                            "outline color=primary no-caps"
                        )


def open_review_workbench(
    state: AppState,
    service: BatchService,
    batch_id: str,
    job_id: int,
    on_change: Callable[[], None],
    *,
    review_runtime: dict[str, bool] | None = None,
) -> None:
    owner_client = ui.context.client
    workbench_state = {"open": True}

    def ui_alive() -> bool:
        return not bool(getattr(owner_client, "is_deleted", False))

    def workbench_alive() -> bool:
        return ui_alive() and bool(workbench_state["open"])

    async def scroll_to_workbench_result(
        element: Any,
        *,
        block: str = "center",
    ) -> None:
        """Keep the workbench open and bring an async operation's result into view."""

        await asyncio.sleep(0)
        if not workbench_alive():
            return
        try:
            element.run_method(
                "scrollIntoView",
                {"behavior": "smooth", "block": block},
            )
        except RuntimeError:
            return

    service.mark_job_viewed(batch_id, job_id)
    batch = service.get_batch(batch_id, include_content=True)
    job = next(item for item in batch["jobs"] if int(item["id"]) == int(job_id))
    article_index = next(
        (
            index
            for index, item in enumerate(batch["jobs"])
            if int(item["id"]) == int(job_id)
        ),
        0,
    )
    article_position = article_index + 1
    previous_job = (
        dict(batch["jobs"][article_index - 1])
        if article_index > 0
        else None
    )
    next_job = (
        dict(batch["jobs"][article_index + 1])
        if article_index + 1 < len(batch["jobs"])
        else None
    )
    if review_runtime is not None:
        review_runtime["review_open"] = True
    with ui.dialog() as dialog, ui.card().classes(
        "review-workbench w-full"
    ).props("flat"):
        def close_workbench() -> None:
            workbench_state["open"] = False
            if review_runtime is not None:
                review_runtime["review_open"] = False
            dialog.close()
            on_change()

        def open_sibling(target: dict[str, Any] | None) -> None:
            if not target:
                return
            close_workbench()
            client_timer(
                0.05,
                lambda: open_review_workbench(
                    state,
                    service,
                    batch_id,
                    int(target["id"]),
                    on_change,
                    review_runtime=review_runtime,
                ),
                once=True,
            )

        with ui.row().classes(
            "review-workbench__header w-full items-center justify-between no-wrap"
        ):
            with ui.row().classes(
                "review-workbench__title-row items-center no-wrap q-gutter-md"
            ):
                ui.avatar(icon="article", size="44px").classes(
                    "review-workbench__icon"
                )
                with ui.column().classes("gap-0"):
                    ui.label(
                        f'文章审核工作台 · {job["account_name"]}'
                    ).classes("text-h6 text-weight-bold")
                    ui.label(
                        f'批次 #{batch["display_id"]} · '
                        f'第 {article_position}/{len(batch["jobs"])} 篇'
                    ).classes("muted")
            with ui.row().classes("ops-review-nav items-center gap-1"):
                previous_btn = ui.button(
                    "上一篇",
                    icon="chevron_left",
                    on_click=lambda: open_sibling(previous_job),
                ).props("flat dense no-caps color=primary")
                next_btn = ui.button(
                    "下一篇",
                    icon="chevron_right",
                    on_click=lambda: open_sibling(next_job),
                ).props("flat dense no-caps color=primary icon-right")
                previous_btn.set_enabled(previous_job is not None)
                next_btn.set_enabled(next_job is not None)
                ui.button(icon="close", on_click=close_workbench).props(
                    "flat round dense aria-label=关闭文章审核"
                ).tooltip("关闭")

        render_workflow_guide(
            "review",
            note=f'正在审核：{job["account_name"]}',
            compact=True,
        )
        deep_review_controls: list[Any] = []
        deep_review_actions: list[Any] = []
        quick_review_actions: list[Any] = []
        review_jury_actions: dict[str, Any] = {}
        review_gate_state: dict[str, Any] = {"review": None}
        confirm_controls: dict[str, Any] = {}
        quick_summary_host = ui.column().classes("w-full gap-2")

        title_options = clean_titles(job)
        selected_title = clean_candidate_text(
            str(job.get("selected_title") or "")
        ) or (title_options[0] if title_options else "")
        if selected_title and selected_title not in title_options:
            title_options = [selected_title, *title_options[:9]]
        if title_options:
            title_choice = ui.radio(
                {title: title for title in title_options}, value=selected_title
            ).classes("w-full")
        else:
            title_choice = None
        title_in = ui.input("文章标题（可直接修改）", value=selected_title).classes(
            "w-full"
        ).props("outlined stack-label")
        if title_choice:
            title_choice.on_value_change(
                lambda event: setattr(title_in, "value", str(event.value or ""))
            )
        cover_preview_state = {
            "media_id": "",
            "url": "",
            "loading": False,
        }

        async def reveal_deep_review() -> None:
            apply_deep_review_mode()
            reveal_result = review_jury_actions.get("reveal_result")
            if callable(reveal_result):
                await reveal_result()
                return
            client_timer(
                0.05,
                lambda: review_jury_host.run_method(
                    "scrollIntoView",
                    {"behavior": "smooth", "block": "start"},
                ),
                once=True,
            )

        def render_quick_review_summary() -> None:
            """Keep decision-critical context visible in quick review mode."""

            quick_summary_host.clear()
            try:
                reviews = service.list_editorial_reviews(
                    job_id=job_id,
                    limit=1,
                )
            except Exception:  # noqa: BLE001
                reviews = []
            latest_review = dict(reviews[0]) if reviews else {}
            review_gate_state["review"] = latest_review or None
            review_status = str(latest_review.get("status") or "")
            rewritten_snapshot = dict(
                latest_review.get("rewritten_snapshot") or {}
            )
            source_snapshot = dict(latest_review.get("source_snapshot") or {})
            rewrite_matches_editor = bool(rewritten_snapshot) and all(
                str(job.get(job_key) or "").strip()
                == str(rewritten_snapshot.get(snapshot_key) or "").strip()
                for job_key, snapshot_key in (
                    ("selected_title", "title"),
                    ("selected_subtitle", "subtitle"),
                    ("digest", "digest"),
                    ("body", "body"),
                )
            )
            source_matches_editor = bool(source_snapshot) and all(
                str(job.get(job_key) or "").strip()
                == str(source_snapshot.get(snapshot_key) or "").strip()
                for job_key, snapshot_key in (
                    ("selected_title", "title"),
                    ("selected_subtitle", "subtitle"),
                    ("digest", "digest"),
                    ("body", "body"),
                )
            )
            review_result = dict(latest_review.get("result") or {})
            review_summary = str(
                review_result.get("conclusion")
                or review_result.get("summary")
                or ""
            )
            if not review_summary:
                review_summary = {
                    "running": "AI 评审正在进行中",
                    "rewriting": "AI 正在生成修改稿",
                    "failed": "上一次 AI 评审失败，可进入深度编辑重新评审",
                    "stale": "文章已修改，上一次 AI 评审已过期",
                }.get(review_status, "尚未进行 AI 评审")
            blocking_count = int(
                latest_review.get("blocking_count") or 0
            )
            settings_summary = review_jury_actions.get("settings_summary")
            default_settings_summary = (
                str(settings_summary())
                if callable(settings_summary)
                else "使用公众号默认评审设置"
            )
            meta = dict(job.get("meta") or {})
            quality = dict(meta.get("layout_quality") or {})
            blockers = [
                str(message)
                for message in list(quality.get("errors") or [])
                if message
            ]
            reminders = [
                str(message)
                for message in list(quality.get("warnings") or [])
                if message
            ]
            if str(job.get("review_status") or "") == "needs_changes":
                blockers.insert(0, "文章已标记为需要修改")
            if job.get("error"):
                blockers.append(_friendly_error(str(job["error"])))
            cover_meta = dict(meta.get("generated_cover") or {})
            cover_active = bool(
                meta.get("generated_cover_active") and cover_meta
            )
            cover_preview_url = (
                str(cover_meta.get("url") or "")
                if cover_active
                else str(cover_preview_state.get("url") or "")
            )
            generated_cover_path = Path(
                str(cover_meta.get("local_path") or "")
            )
            with quick_summary_host:
                with ui.card().classes(
                    "review-quick-summary w-full q-pa-md"
                ).props("flat"):
                    with ui.row().classes(
                        "w-full items-start justify-between"
                    ):
                        with ui.column().classes("gap-0 ops-flex-copy"):
                            ui.label("AI 评审摘要").classes(
                                "text-weight-bold"
                            )
                            ui.label(
                                review_summary
                            ).classes("muted")
                            if review_status == "candidate_ready":
                                ui.badge(
                                    (
                                        "待选择：保留原文或采用 AI 改写稿"
                                        if source_matches_editor
                                        else "AI 候选稿已过期，请重新评审"
                                    )
                                ).props("outline color=amber-9")
                                ui.label(
                                    (
                                        "当前正文仍是改写前原文，AI 候选稿尚未覆盖正文。"
                                        if source_matches_editor
                                        else "候选稿生成后正文发生了变化，不能再直接采用。"
                                    )
                                ).classes("text-caption text-amber-10")
                            elif review_status == "source_kept":
                                ui.badge("已选择：保留改写前原文").props(
                                    "outline color=blue-grey-7"
                                )
                                ui.label(
                                    "AI 改写稿没有覆盖正文，可随时查看本次前后对比。"
                                ).classes("text-caption text-blue-grey-7")
                            elif review_status == "applied":
                                version_badge = (
                                    "当前版本：AI 改写后"
                                    if rewrite_matches_editor
                                    else "AI 改写后又有人工编辑"
                                )
                                ui.badge(version_badge).props(
                                    "outline color=green-7"
                                )
                                ui.label(
                                    (
                                        "文章已应用本次 AI 修改稿，可查看改写前后的全文对比。"
                                        if rewrite_matches_editor
                                        else "文章应用 AI 修改稿后又发生了编辑；对比区会保留本次 AI 改写记录。"
                                    )
                                ).classes("text-caption text-green-8")
                            ui.label(default_settings_summary).classes(
                                "text-caption text-blue-grey-7"
                            )
                            if blocking_count:
                                ui.label(
                                    f"AI 评审仍有 {blocking_count} 个阻断项"
                                ).classes("text-warning text-caption")
                    ui.separator().classes("q-my-sm")
                    with ui.row().classes("w-full items-center gap-3"):
                        if cover_preview_url or (
                            cover_active and generated_cover_path.is_file()
                        ):
                            preview_source: Any = None
                            if cover_preview_url:
                                preview_source = wechat_image_proxy_url(
                                    cover_preview_url
                                )
                            elif generated_cover_path.is_file():
                                preview_source = generated_cover_path
                            if preview_source is not None:
                                ui.image(preview_source).classes(
                                    "rounded-borders ops-cover-thumb"
                                ).props("fit=cover no-spinner")
                        elif bool(cover_preview_state.get("loading")):
                            with ui.column().classes(
                                "items-center justify-center rounded-borders bg-grey-2 ops-cover-thumb"
                            ):
                                ui.spinner("dots", size="sm", color="teal-9")
                                ui.label("正在读取封面缩略图").classes(
                                    "muted text-caption"
                                )
                        with ui.column().classes("gap-0"):
                            ui.label("当前封面").classes("text-weight-bold")
                            ui.label(
                                (
                                    "AI 封面已选择"
                                    if cover_active
                                    else (
                                        (
                                            "已选择公众号素材"
                                            if cover_preview_url
                                            else "封面已选择，缩略图暂不可用"
                                        )
                                        if job.get("thumb_media_id")
                                        else "尚未选择封面"
                                    )
                                )
                            ).classes(
                                "text-positive"
                                if job.get("thumb_media_id")
                                else "text-warning"
                            )
                    ui.label(
                        (
                            "阻断摘要：无"
                            if not blockers and not blocking_count
                            else "阻断摘要："
                            + "；".join(
                                [
                                    *blockers,
                                    *(
                                        [f"AI 评审 {blocking_count} 项"]
                                        if blocking_count
                                        else []
                                    ),
                                ]
                            )
                        )
                    ).classes(
                        "muted text-caption"
                        if not blockers and not blocking_count
                        else "text-warning text-caption"
                    )
                    ui.label(
                        (
                            "提醒摘要：无"
                            if not reminders
                            else "提醒摘要：" + "；".join(reminders)
                        )
                    ).classes(
                        "muted text-caption"
                        if not reminders
                        else "text-amber-9 text-caption"
                    )

        async def load_selected_cover_preview(media_id: str) -> None:
            preview_url = ""
            try:
                for offset in range(0, 500, 100):
                    page = await run.io_bound(
                        lambda current_offset=offset: service.list_cover_options(
                            batch_id,
                            job_id,
                            limit=100,
                            offset=current_offset,
                        )
                    )
                    matched = next(
                        (
                            item
                            for item in page
                            if str(item.get("media_id") or "") == media_id
                        ),
                        None,
                    )
                    if matched:
                        preview_url = str(matched.get("url") or "")
                        break
                    if len(page) < 100:
                        break
            except Exception:  # noqa: BLE001
                preview_url = ""
            if (
                not workbench_alive()
                or str(cover_preview_state.get("media_id") or "") != media_id
            ):
                return
            cover_preview_state["url"] = preview_url.replace(
                "http://mmbiz.qpic.cn/",
                "https://mmbiz.qpic.cn/",
            )
            cover_preview_state["loading"] = False
            render_quick_review_summary()

        def schedule_selected_cover_preview() -> None:
            media_id = str(job.get("thumb_media_id") or "")
            meta = dict(job.get("meta") or {})
            if bool(meta.get("generated_cover_active")):
                cover_preview_state.update(
                    media_id=media_id,
                    url="",
                    loading=False,
                )
                return
            if not media_id:
                cover_preview_state.update(
                    media_id="",
                    url="",
                    loading=False,
                )
                return
            if (
                str(cover_preview_state.get("media_id") or "") == media_id
                and (
                    bool(cover_preview_state.get("url"))
                    or bool(cover_preview_state.get("loading"))
                )
            ):
                return
            cover_preview_state.update(
                media_id=media_id,
                url="",
                loading=True,
            )
            render_quick_review_summary()
            client_timer(
                0.05,
                lambda: load_selected_cover_preview(media_id),
                once=True,
            )

        subtitle_options = clean_subtitles(job)
        selected_subtitle = clean_candidate_text(
            str(job.get("selected_subtitle") or "")
        )
        if selected_subtitle and selected_subtitle not in subtitle_options:
            subtitle_options = [selected_subtitle, *subtitle_options[:9]]
        with ui.expansion(
            f"更多优化：副标题与摘要（{len(subtitle_options)} 个副标题候选）",
            icon="tune",
            value=False,
        ).classes("w-full") as subtitle_editor:
            if subtitle_options:
                ui.label("副标题候选（单选，也可以在下方直接修改）").classes(
                    "text-weight-medium"
                )
                subtitle_choice = ui.radio(
                    {subtitle: subtitle for subtitle in subtitle_options},
                    value=(
                        selected_subtitle
                        if selected_subtitle in subtitle_options
                        else None
                    ),
                ).classes("w-full")
            else:
                subtitle_choice = None
                ui.label("当前没有可用副标题候选").classes("muted")
            subtitle_in = ui.input(
                "副标题（可留空）", value=selected_subtitle
            ).classes("w-full").props("outlined stack-label")
            if subtitle_choice:
                subtitle_choice.on_value_change(
                    lambda event: setattr(
                        subtitle_in,
                        "value",
                        str(event.value or ""),
                    )
                )
                ui.button(
                    "不使用副标题",
                    on_click=lambda: (
                        setattr(subtitle_choice, "value", None),
                        setattr(subtitle_in, "value", ""),
                    ),
                ).props("flat dense color=grey-8 no-caps icon=close")
            digest_in = ui.textarea(
                "摘要", value=str(job.get("digest") or "")
            ).classes("w-full").props("outlined rows=3 stack-label")
        deep_review_controls.append(subtitle_editor)
        body_in = ui.textarea(
            "正文纯文本", value=str(job.get("body") or "")
        ).classes("review-body-editor w-full").props(
            "outlined rows=18 stack-label"
        )
        deep_review_controls.append(body_in)

        async def scroll_to_updated_article() -> None:
            await scroll_to_workbench_result(body_in, block="start")

        def editor_has_unsaved_changes(*, include_body: bool = True) -> bool:
            current_subtitle = str(job.get("selected_subtitle") or "").strip()
            changed = any(
                (
                    str(title_in.value or "").strip()
                    != str(job.get("selected_title") or "").strip(),
                    str(subtitle_in.value or "").strip() != current_subtitle,
                    str(digest_in.value or "").strip()
                    != str(job.get("digest") or "").strip(),
                )
            )
            if include_body:
                changed = changed or (
                    str(body_in.value or "").strip()
                    != str(job.get("body") or "").strip()
                )
            return changed

        def require_saved_editor() -> bool:
            if not editor_has_unsaved_changes():
                return True
            ui.notify(
                "检测到标题、摘要或正文有尚未保存的修改。请先点击“保存并刷新排版预览”，"
                "再进行 AI 评审或定点改写，避免覆盖人工编辑。",
                type="warning",
                timeout=10000,
            )
            return False

        def handle_review_updated(updated_review: dict[str, Any]) -> None:
            """Refresh both the quick summary and the footer gate in place."""

            review_gate_state["review"] = dict(updated_review)
            render_quick_review_summary()
            sync_confirm_gate(updated_review)

        with ui.column().classes(
            "review-jury-host w-full gap-3"
        ) as review_jury_host:
            background_review = (
                review_runtime.get("start_background_review")
                if review_runtime is not None
                else None
            )
            review_jury_actions.update(build_review_jury_panel(
                service=service,
                batch_id=batch_id,
                job_id=job_id,
                job=job,
                require_saved_editor=require_saved_editor,
                on_job_updated=lambda updated: apply_updated_job(
                    updated,
                    refresh_images=True,
                    refresh_cover=True,
                ),
                on_article_updated=scroll_to_updated_article,
                is_workbench_alive=workbench_alive,
                on_background_review=(
                    background_review if callable(background_review) else None
                ),
                on_enter_background=close_workbench,
                on_review_updated=handle_review_updated,
            ))
        deep_review_controls.append(review_jury_host)
        render_quick_review_summary()
        schedule_selected_cover_preview()

        def current_paragraphs() -> list[str]:
            return [
                item.strip()
                for item in str(body_in.value or "").replace("\r\n", "\n").split("\n\n")
                if item.strip()
            ]

        def paragraph_options() -> dict[int, str]:
            return {
                index: f"第 {index + 1} 段 · {text[:38]}"
                for index, text in enumerate(current_paragraphs())
            }

        with ui.expansion(
            "AI 定点改写（单段）", value=False
        ).classes("w-full") as paragraph_editor:
            ui.label(
                "选择不满意的段落并说明修改要求。系统只替换这一段，其他正文和已审核图片保持不变。"
            ).classes("muted")
            paragraph_in = ui.select(
                options=paragraph_options(), value=0, label="选择段落"
            ).classes("w-full").props("outlined stack-label options-dense")
            selected_paragraph_preview = ui.textarea(
                "当前段落",
                value=(current_paragraphs()[0] if current_paragraphs() else ""),
            ).classes("w-full").props("outlined readonly rows=4 stack-label")
            paragraph_instruction = ui.textarea(
                "你希望怎样修改这段正文",
                placeholder="例如：压缩到 120 字，突出经营风险；语气更克制，并保留原有数据",
            ).classes("w-full").props("outlined rows=3 stack-label counter maxlength=2000")

            def refresh_selected_paragraph() -> None:
                items = current_paragraphs()
                index = int(paragraph_in.value or 0)
                selected_paragraph_preview.value = (
                    items[index] if 0 <= index < len(items) else ""
                )

            paragraph_in.on_value_change(lambda _: refresh_selected_paragraph())

            def apply_paragraphs(items: list[str], selected: int) -> None:
                body_in.value = "\n\n".join(items)
                options = paragraph_options()
                paragraph_in.set_options(
                    options,
                    value=max(0, min(selected, len(options) - 1)) if options else None,
                )
                refresh_selected_paragraph()

            def move_paragraph(offset: int) -> None:
                items = current_paragraphs()
                index = int(paragraph_in.value or 0)
                target = index + offset
                if 0 <= index < len(items) and 0 <= target < len(items):
                    items[index], items[target] = items[target], items[index]
                    apply_paragraphs(items, target)

            def delete_paragraph() -> None:
                items = current_paragraphs()
                index = int(paragraph_in.value or 0)
                if 0 <= index < len(items):
                    items.pop(index)
                    apply_paragraphs(items, max(0, index - 1))

            async def regenerate_paragraph() -> None:
                if not str(paragraph_instruction.value or "").strip():
                    ui.notify("请先填写这段正文的修改要求", type="warning")
                    return
                if not require_saved_editor():
                    return
                set_button_loading(regenerate_btn, True)
                try:
                    updated = await run.io_bound(
                        lambda: service.regenerate_paragraph(
                            batch_id,
                            job_id,
                            int(paragraph_in.value or 0),
                            instruction=str(paragraph_instruction.value or ""),
                        )
                    )
                    if not workbench_alive():
                        return
                    apply_updated_job(updated, refresh_images=False)
                    paragraph_instruction.value = ""
                    ui.notify("所选段落已按要求二次改写，文章需要重新确认", type="positive")
                    await scroll_to_workbench_result(
                        selected_paragraph_preview,
                        block="center",
                    )
                except Exception as exc:  # noqa: BLE001
                    if workbench_alive():
                        ui.notify(
                            "段落重新生成失败："
                            f"{sanitize_failure_text(exc)}",
                            type="negative",
                            timeout=10000,
                        )
                finally:
                    if workbench_alive():
                        set_button_loading(regenerate_btn, False)

            with ui.row().classes("items-center"):
                ui.button("上移", on_click=lambda: move_paragraph(-1)).props("outline dense no-caps")
                ui.button("下移", on_click=lambda: move_paragraph(1)).props("outline dense no-caps")
                ui.button("删除此段", on_click=delete_paragraph).props(
                    "outline dense color=red-7 no-caps"
                )
                regenerate_btn = ui.button(
                    "按要求改写所选段落", on_click=regenerate_paragraph
                ).props("unelevated dense color=indigo-7 no-caps")
            ui.label(
                "模型会同时参考标题、前后文和原段落；修改前版本会自动保存，可在历史版本中恢复。"
            ).classes("muted")
        deep_review_controls.append(paragraph_editor)

        inline_assets = list((job.get("meta") or {}).get("inline_images") or [])
        with ui.expansion(
            f"正文生图 · 已生成 {len(inline_assets)} 张",
            icon="auto_awesome",
            value=False,
        ).classes("w-full") as inline_expansion:
            ui.label(
                "系统按正文小标题识别论点，将图片插在每个论点最后一个段落之后。"
            ).classes("muted")
            inline_content = ui.column().classes("w-full gap-2")
            inline_card_by_index: dict[int, Any] = {}

            def render_inline_assets() -> None:
                assets = list((job.get("meta") or {}).get("inline_images") or [])
                warnings = list(
                    (job.get("meta") or {}).get("inline_image_warnings") or []
                )
                inline_expansion.set_text(f"正文生图 · 已生成 {len(assets)} 张")
                inline_card_by_index.clear()
                inline_content.clear()
                with inline_content:
                    for warning in warnings:
                        ui.label(f"生图提示：{warning}").classes(
                            "text-warning text-caption"
                        )
                    if not assets:
                        ui.label(
                            "尚未生成智能配图。配置生图智能体后，可在这里对当前文章直接测试。"
                        ).classes("text-warning text-caption q-mt-sm")
                        return
                    with ui.grid(columns=3).classes("w-full gap-3 q-mt-sm"):
                        for asset in assets:
                            image_index = int(
                                asset.get("index")
                                or asset.get("image_index")
                                or 0
                            )
                            image_card = ui.card().classes(
                                "w-full q-pa-sm ops-min-width-zero"
                            )
                            inline_card_by_index[image_index] = image_card
                            with image_card:
                                image_url = str(asset.get("url") or "")
                                if image_url:
                                    ui.image(
                                        wechat_image_proxy_url(image_url)
                                    ).classes("w-full rounded-borders").props(
                                        "fit=cover no-spinner"
                                    ).classes("ui-media-preview")
                                ui.label(
                                    f'论点 {asset.get("index")} · '
                                    f'{asset.get("caption") or "正文配图"}'
                                ).classes(
                                    "text-caption ellipsis w-full"
                                ).tooltip(
                                    str(asset.get("caption") or "正文配图")
                                )
                                if asset.get("model_name"):
                                    ui.label(
                                        f'智能体：{asset.get("model_name")}'
                                    ).classes("muted text-caption")
                                if int(asset.get("revision_count") or 0):
                                    ui.label(
                                        f'已定向修改 {int(asset.get("revision_count") or 0)} 次'
                                    ).classes("text-positive text-caption")
                                image_instruction = ui.textarea(
                                    "这张图的修改要求",
                                    placeholder="例如：不要会议室，改成供应链仓库现场，突出库存积压",
                                ).classes("w-full").props(
                                    "outlined dense rows=2 stack-label counter maxlength=2000"
                                )
                                image_revision_btn = ui.button(
                                    "按要求重新生成此图"
                                ).props(
                                    "unelevated dense color=indigo-7 no-caps icon=auto_fix_high"
                                ).classes("w-full")
                                remove_image_btn = ui.button("移除此图").props(
                                    "flat dense color=red-7 no-caps icon=delete_outline"
                                ).classes("w-full")

                                async def regenerate_one_image(
                                    _=None,
                                    *,
                                    selected_index: int = image_index,
                                    request_field: Any = image_instruction,
                                    action_button: Any = image_revision_btn,
                                ) -> None:
                                    request = str(
                                        request_field.value or ""
                                    ).strip()
                                    if not request:
                                        ui.notify(
                                            "请先填写这张图片的修改要求",
                                            type="warning",
                                        )
                                        return
                                    if not require_saved_editor():
                                        return
                                    set_button_loading(
                                        action_button,
                                        True,
                                        "生图智能体正在只重做这张图片并上传，请稍候…",
                                    )
                                    updated_job: dict[str, Any] | None = None
                                    try:
                                        updated_job = await run.io_bound(
                                            lambda: service.regenerate_inline_image(
                                                batch_id,
                                                job_id,
                                                selected_index,
                                                instruction=request,
                                            )
                                        )
                                        if not workbench_alive():
                                            return
                                        ui.notify(
                                            f"正文配图 {selected_index} 已按要求重新生成，其他图片保持不变",
                                            type="positive",
                                            timeout=10000,
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        if workbench_alive():
                                            ui.notify(
                                                "单图重新生成失败，原图片已保留："
                                                f"{sanitize_failure_text(exc)}",
                                                type="negative",
                                                timeout=15000,
                                            )
                                    finally:
                                        if workbench_alive():
                                            set_button_loading(action_button, False)
                                    if updated_job is not None:
                                        apply_updated_job(
                                            updated_job, refresh_images=True
                                        )
                                        await scroll_to_workbench_result(
                                            inline_card_by_index.get(
                                                selected_index,
                                                inline_content,
                                            ),
                                            block="center",
                                        )

                                async def remove_one_image(
                                    _=None,
                                    *,
                                    selected_index: int = image_index,
                                    action_button: Any = remove_image_btn,
                                ) -> None:
                                    if not require_saved_editor():
                                        return
                                    set_button_loading(action_button, True)
                                    updated_job: dict[str, Any] | None = None
                                    try:
                                        updated_job = await run.io_bound(
                                            lambda: service.remove_inline_image(
                                                batch_id, job_id, selected_index
                                            )
                                        )
                                        if not workbench_alive():
                                            return
                                        ui.notify(
                                            "已移除所选正文配图",
                                            type="positive",
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        if workbench_alive():
                                            ui.notify(
                                                "移除失败："
                                                f"{sanitize_failure_text(exc)}",
                                                type="negative",
                                            )
                                    finally:
                                        if workbench_alive():
                                            set_button_loading(action_button, False)
                                    if updated_job is not None:
                                        apply_updated_job(
                                            updated_job, refresh_images=True
                                        )
                                        await scroll_to_workbench_result(
                                            inline_content,
                                            block="start",
                                        )

                                image_revision_btn.on_click(regenerate_one_image)
                                remove_image_btn.on_click(remove_one_image)

            render_inline_assets()

            async def regenerate_inline_images() -> None:
                set_button_loading(
                    inline_image_btn,
                    True,
                    "生图智能体正在按每个论点生成并上传图片，请稍候…",
                )
                try:
                    await run.io_bound(
                        lambda: service.update_job_content(
                            batch_id,
                            job_id,
                            title=str(title_in.value or ""),
                            subtitle=str(subtitle_in.value or ""),
                            digest=str(digest_in.value or ""),
                            body=str(body_in.value or ""),
                        )
                    )
                    updated = await run.io_bound(
                        lambda: service.regenerate_inline_images(batch_id, job_id)
                    )
                    if not workbench_alive():
                        return
                    generated = list((updated.get("meta") or {}).get("inline_images") or [])
                    warnings = list(
                        (updated.get("meta") or {}).get("inline_image_warnings") or []
                    )
                    ui.notify(
                        f"已生成并插入 {len(generated)} 张论点配图"
                        + (f"；{len(warnings)} 项提示请检查" if warnings else ""),
                        type="warning" if warnings else "positive",
                        timeout=12000,
                    )
                    apply_updated_job(
                        updated,
                        refresh_images=True,
                        refresh_cover=True,
                    )
                    await scroll_to_workbench_result(
                        inline_content,
                        block="start",
                    )
                except Exception as exc:  # noqa: BLE001
                    if workbench_alive():
                        ui.notify(
                            f"正文生图失败：{sanitize_failure_text(exc)}",
                            type="negative",
                            timeout=15000,
                        )
                finally:
                    if workbench_alive():
                        set_button_loading(inline_image_btn, False)

            inline_image_btn = ui.button(
                "生成 / 重新生成正文配图",
                on_click=regenerate_inline_images,
            ).props("unelevated color=indigo-7 no-caps icon=auto_awesome").classes(
                "q-mt-sm"
            )
            ui.label(
                "整批重新生成会调用多次生图接口；单张修改只调用一次。这里是按描述重新生成，"
                "不是在原图像素上局部修图。"
            ).classes("muted")
        deep_review_controls.append(inline_expansion)

        with ui.expansion(
            "封面主图",
            icon="panorama",
            value=False,
        ).classes("w-full") as cover_editor:
            ui.label(
                "AI 封面同时参考当前标题、正文主题和核心论点，并作为该公众号的永久图片素材上传。"
            ).classes("muted")
            cover_preview_container = ui.column().classes("w-full gap-2")

            def render_cover_preview() -> None:
                cover_meta = dict(
                    (job.get("meta") or {}).get("generated_cover") or {}
                )
                cover_warning = str(
                    (job.get("meta") or {}).get("cover_image_warning") or ""
                )
                generated_cover_active = bool(
                    (job.get("meta") or {}).get("generated_cover_active")
                    and cover_meta
                )
                cover_preview_container.clear()
                with cover_preview_container:
                    if cover_warning:
                        ui.label(cover_warning).classes(
                            "text-warning text-caption"
                        )
                    if not generated_cover_active:
                        return
                    with ui.card().classes("w-full q-pa-sm ops-dialog-md"):
                        preview_url = str(cover_meta.get("url") or "")
                        local_path = Path(
                            str(cover_meta.get("local_path") or "")
                        )
                        preview_source: Any = None
                        if preview_url:
                            preview_source = wechat_image_proxy_url(preview_url)
                        elif local_path.is_file():
                            preview_source = local_path
                        if preview_source is not None:
                            ui.image(preview_source).classes(
                                "w-full rounded-borders ui-media-preview ops-cover-ratio"
                            ).props("fit=cover no-spinner")
                        ui.label(
                            f'当前 AI 封面 · {cover_meta.get("model_name") or "生图智能体"}'
                        ).classes("text-caption text-weight-medium")

            render_cover_preview()

            cover_instruction = ui.textarea(
                "封面修改要求（可留空）",
                value=str((job.get("meta") or {}).get("cover_revision_instruction") or ""),
                placeholder="例如：改成现代制造现场，主体靠中间，不要会议室",
            ).classes("w-full").props(
                "outlined rows=2 stack-label counter maxlength=2000"
            )

            async def regenerate_cover() -> None:
                if (
                    str(body_in.value or "").strip()
                    != str(job.get("body") or "").strip()
                ):
                    ui.notify(
                        "正文有尚未保存的修改。请先保存并刷新排版预览，再重新生成封面。",
                        type="warning",
                        timeout=10000,
                    )
                    return
                set_button_loading(
                    generate_cover_btn,
                    True,
                    "正在根据标题、正文和核心论点生成封面并上传公众号，请稍候…",
                )
                try:
                    update_kwargs: dict[str, Any] = {
                        "title": str(title_in.value or ""),
                        "subtitle": str(subtitle_in.value or ""),
                        "digest": str(digest_in.value or ""),
                    }
                    await run.io_bound(
                        lambda: service.update_job_content(
                            batch_id,
                            job_id,
                            **update_kwargs,
                        )
                    )
                    updated = await run.io_bound(
                        lambda: service.regenerate_cover(
                            batch_id,
                            job_id,
                            instruction=str(cover_instruction.value or ""),
                        )
                    )
                    if not workbench_alive():
                        return
                    generated = dict((updated.get("meta") or {}).get("generated_cover") or {})
                    if not generated:
                        warning = str((updated.get("meta") or {}).get("cover_image_warning") or "")
                        raise RuntimeError(warning or "生图智能体没有返回可用封面")
                    apply_updated_job(updated, refresh_cover=True)
                    ui.notify("AI 封面已生成并设为当前封面", type="positive", timeout=10000)
                    await scroll_to_workbench_result(
                        cover_preview_container,
                        block="center",
                    )
                except Exception as exc:  # noqa: BLE001
                    if workbench_alive():
                        ui.notify(
                            f"封面生成失败：{sanitize_failure_text(exc)}",
                            type="negative",
                            timeout=15000,
                        )
                finally:
                    if workbench_alive():
                        set_button_loading(generate_cover_btn, False)

            generate_cover_btn = ui.button(
                "生成 / 重新生成封面主图",
                on_click=regenerate_cover,
            ).props("unelevated color=indigo-7 no-caps icon=auto_awesome")
            ui.label(
                "生成会调用一次生图接口并可能产生费用；更换最终标题后建议重新生成。"
            ).classes("muted")
        deep_review_controls.append(cover_editor)

        current_cover = str(job.get("thumb_media_id") or "")
        cover_in = ui.select(
            options=({current_cover: "当前封面"} if current_cover else {}),
            value=current_cover or None,
            label="封面素材",
        ).classes("w-full").props("outlined stack-label options-dense")
        selected_cover_label = ui.label(
            "已选择当前封面" if current_cover else "尚未选择封面"
        ).classes("muted")
        cover_gallery = ui.grid(columns=4).classes("w-full gap-3")
        deep_review_controls.extend(
            (cover_in, selected_cover_label, cover_gallery)
        )
        cover_items: list[dict[str, str]] = []
        cover_card_by_media_id: dict[str, Any] = {}
        cover_page_size = 24

        def select_cover(media_id: str, name: str) -> None:
            cover_in.value = media_id
            selected_cover_label.text = f"已选择：{name}"

        def render_cover_gallery() -> None:
            options = {
                item["media_id"]: item["name"] or f'封面 {index}'
                for index, item in enumerate(cover_items, 1)
            }
            active_cover = str(job.get("thumb_media_id") or "")
            if active_cover and active_cover not in options:
                options = {active_cover: "当前封面", **options}
            cover_in.set_options(
                options,
                value=cover_in.value if cover_in.value in options else None,
            )
            cover_card_by_media_id.clear()
            cover_gallery.clear()
            with cover_gallery:
                for index, item in enumerate(cover_items, 1):
                    media_id = str(item["media_id"])
                    name = str(item.get("name") or f"封面 {index}")
                    image_url = str(item.get("url") or "").replace(
                        "http://mmbiz.qpic.cn/", "https://mmbiz.qpic.cn/"
                    )
                    cover_card = ui.card().classes(
                        "w-full q-pa-sm ops-min-width-zero"
                    )
                    cover_card_by_media_id[media_id] = cover_card
                    with cover_card:
                        if image_url:
                            ui.image(wechat_image_proxy_url(image_url)).classes(
                                "w-full rounded-borders ui-media-option"
                            ).props("fit=cover no-spinner")
                        else:
                            with ui.element("div").classes(
                                "w-full flex items-center justify-center bg-grey-2 rounded-borders ops-cover-placeholder"
                            ):
                                ui.icon("broken_image", size="36px").classes("text-grey-6")
                        ui.label(name).classes("text-caption ellipsis w-full").tooltip(name)
                        ui.button(
                            "选择此封面",
                            on_click=lambda _=None, mid=media_id, label=name: select_cover(
                                mid, label
                            ),
                        ).props("flat dense color=teal-9 no-caps icon=check_circle")

        async def load_covers(*, reset: bool) -> None:
            active_button = cover_btn if reset else more_covers_btn
            set_button_loading(active_button, True)
            try:
                start = 0 if reset else len(cover_items)
                page = await run.io_bound(
                    lambda: service.list_cover_options(
                        batch_id,
                        job_id,
                        limit=cover_page_size,
                        offset=start,
                    )
                )
                if not workbench_alive():
                    return
                if reset:
                    cover_items.clear()
                known = {str(item["media_id"]) for item in cover_items}
                new_items = [
                    item for item in page if str(item["media_id"]) not in known
                ]
                cover_items.extend(new_items)
                render_cover_gallery()
                more_covers_btn.set_visibility(len(page) == cover_page_size)
                ui.notify(
                    f"已显示 {len(cover_items)} 张该公众号封面素材", type="positive"
                )
                await scroll_to_workbench_result(
                    (
                        cover_card_by_media_id.get(
                            str(new_items[0]["media_id"]),
                            cover_gallery,
                        )
                        if not reset and new_items
                        else cover_gallery
                    ),
                    block="start" if reset else "center",
                )
            except Exception as exc:  # noqa: BLE001
                if workbench_alive():
                    ui.notify(
                        f"读取封面失败：{sanitize_failure_text(exc)}",
                        type="negative",
                    )
            finally:
                if workbench_alive():
                    set_button_loading(active_button, False)

        async def reload_covers() -> None:
            await load_covers(reset=True)

        async def load_more_covers() -> None:
            await load_covers(reset=False)

        with ui.row().classes("items-center") as cover_actions:
            cover_btn = ui.button(
                "读取该公众号封面素材", on_click=reload_covers
            ).props("outline dense color=teal-9 no-caps icon=image")
            more_covers_btn = ui.button(
                "加载更多封面", on_click=load_more_covers
            ).props("flat dense color=teal-9 no-caps icon=expand_more")
            more_covers_btn.set_visibility(False)
        deep_review_controls.append(cover_actions)

        with ui.expansion(
            "历史版本", value=False
        ).classes("w-full") as history_editor:
            version_in = ui.select(
                options={},
                value=None,
                label="选择要恢复的版本",
            ).classes("w-full").props("outlined options-dense")

            async def restore_version() -> None:
                if version_in.value is None:
                    ui.notify("当前还没有可恢复的历史版本", type="warning")
                    return
                set_button_loading(restore_btn, True)
                try:
                    updated = await run.io_bound(
                        lambda: service.restore_job_version(
                            batch_id, job_id, int(version_in.value)
                        )
                    )
                    if not workbench_alive():
                        return
                    apply_updated_job(
                        updated,
                        refresh_images=True,
                        refresh_cover=True,
                    )
                    ui.notify("已恢复历史版本，文章需要重新确认", type="positive")
                    await scroll_to_updated_article()
                except Exception as exc:  # noqa: BLE001
                    if workbench_alive():
                        ui.notify(
                            f"恢复失败：{sanitize_failure_text(exc)}",
                            type="negative",
                        )
                finally:
                    if workbench_alive():
                        set_button_loading(restore_btn, False)

            restore_btn = ui.button("恢复此版本", on_click=restore_version).props(
                "outline color=teal-9 no-caps icon=history"
            )

            def refresh_version_options() -> None:
                versions = service.list_job_versions(batch_id, job_id)
                options = {
                    int(item["id"]): (
                        f'{_format_time(item.get("created_at"))}'
                        f' · {item.get("reason") or "自动保存"}'
                    )
                    for item in versions
                }
                version_in.set_options(
                    options,
                    value=int(versions[0]["id"]) if versions else None,
                )
                if versions:
                    restore_btn.enable()
                else:
                    restore_btn.disable()

            refresh_version_options()
        deep_review_controls.append(history_editor)

        with ui.expansion("排版质检与最终 HTML 预览", value=True).classes("w-full"):
            quality_summary = ui.label().classes("muted")
            quality_messages = ui.column().classes("w-full gap-1")
            preview_container = ui.element("div").classes(
                "preview-frame review-phone-preview w-full"
            )

            def render_quality_preview() -> None:
                quality = (job.get("meta") or {}).get("layout_quality") or {}
                quality_summary.set_text(
                    f'段落 {quality.get("paragraph_count", 0)} · '
                    f'图片 {quality.get("image_count", 0)}'
                )
                quality_messages.clear()
                with quality_messages:
                    for message in list(quality.get("errors") or []):
                        ui.label(f"错误：{message}").classes("text-negative")
                    for message in list(quality.get("warnings") or []):
                        ui.label(f"提示：{message}").classes("text-warning")
                preview_container.clear()
                with preview_container:
                    if job.get("html_content"):
                        ui.html(
                            prepare_preview_html(str(job["html_content"])),
                            sanitize=False,
                        )
                    else:
                        ui.label(
                            "正文修改后请点击“保存并刷新排版预览”。"
                        ).classes("muted")

            render_quality_preview()

        def apply_updated_job(
            updated: dict[str, Any],
            *,
            refresh_images: bool = False,
            refresh_cover: bool = False,
        ) -> None:
            selected_paragraph = int(paragraph_in.value or 0)
            job.clear()
            job.update(updated)
            title_in.value = clean_candidate_text(
                str(job.get("selected_title") or "")
            )
            subtitle_in.value = clean_candidate_text(
                str(job.get("selected_subtitle") or "")
            )
            digest_in.value = str(job.get("digest") or "")
            body_in.value = str(job.get("body") or "")
            options = paragraph_options()
            paragraph_in.set_options(
                options,
                value=(
                    max(0, min(selected_paragraph, len(options) - 1))
                    if options
                    else None
                ),
            )
            refresh_selected_paragraph()
            active_cover = str(job.get("thumb_media_id") or "")
            if active_cover:
                cover_in.value = active_cover
                selected_cover_label.set_text("已选择当前封面")
            else:
                cover_in.value = None
                selected_cover_label.set_text("尚未选择封面")
            cover_instruction.value = str(
                (job.get("meta") or {}).get("cover_revision_instruction") or ""
            )
            render_cover_gallery()
            if refresh_images:
                render_inline_assets()
            if refresh_cover:
                render_cover_preview()
            render_quality_preview()
            refresh_version_options()
            render_quick_review_summary()
            schedule_selected_cover_preview()

        async def save_and_render() -> None:
            set_button_loading(save_btn, True)
            try:
                await run.io_bound(
                    lambda: service.update_job_content(
                        batch_id,
                        job_id,
                        title=str(title_in.value or ""),
                        subtitle=str(subtitle_in.value or ""),
                        digest=str(digest_in.value or ""),
                        body=str(body_in.value or ""),
                    )
                )
                if cover_in.value:
                    await run.io_bound(
                        lambda: service.select_job_cover(
                            batch_id, job_id, str(cover_in.value)
                        )
                    )
                updated = await run.io_bound(
                    lambda: service.rerender_job(batch_id, job_id)
                )
                if not workbench_alive():
                    return
                apply_updated_job(
                    updated,
                    refresh_images=True,
                    refresh_cover=True,
                )
                ui.notify("修改已保存并重新排版", type="positive")
                await scroll_to_workbench_result(
                    preview_container,
                    block="start",
                )
            except Exception as exc:  # noqa: BLE001
                if workbench_alive():
                    ui.notify(
                        f"保存失败：{sanitize_failure_text(exc)}",
                        type="negative",
                        timeout=10000,
                    )
            finally:
                if workbench_alive():
                    set_button_loading(save_btn, False)

        def sync_confirm_gate(review: dict[str, Any] | None = None) -> None:
            if review is not None:
                review_gate_state["review"] = dict(review)
            confirm_btn = confirm_controls.get("confirm")
            go_process_btn = confirm_controls.get("go_process")
            if confirm_btn is None or go_process_btn is None:
                return
            reason, blocking_count = _review_confirmation_gate(
                review_gate_state.get("review")
            )
            if blocking_count:
                confirm_btn.set_text(f"先处理 {blocking_count} 个阻断项")
                confirm_btn.disable()
                go_process_btn.set_visibility(True)
            elif reason:
                confirm_btn.set_text("AI 评审中")
                confirm_btn.disable()
                go_process_btn.set_visibility(False)
            else:
                confirm_btn.set_text("确认此文章")
                confirm_btn.enable()
                go_process_btn.set_visibility(False)

        async def confirm() -> None:
            fresh_reviews = await run.io_bound(
                lambda: service.list_editorial_reviews(
                    job_id=job_id,
                    limit=1,
                )
            )
            fresh_review = dict(fresh_reviews[0]) if fresh_reviews else None
            review_gate_state["review"] = fresh_review
            sync_confirm_gate(fresh_review)
            reason, blocking_count = _review_confirmation_gate(fresh_review)
            if reason:
                ui.notify(reason, type="warning", timeout=8000)
                if blocking_count:
                    await reveal_deep_review()
                return

            confirm_btn = confirm_controls["confirm"]
            set_button_loading(confirm_btn, True)
            try:
                await run.io_bound(
                    lambda: service.update_job_content(
                        batch_id,
                        job_id,
                        title=str(title_in.value or ""),
                        subtitle=str(subtitle_in.value or ""),
                        digest=str(digest_in.value or ""),
                        body=str(body_in.value or ""),
                    )
                )
                if cover_in.value:
                    await run.io_bound(
                        lambda: service.select_job_cover(
                            batch_id, job_id, str(cover_in.value)
                        )
                    )
                await run.io_bound(lambda: service.rerender_job(batch_id, job_id))
                await run.io_bound(lambda: service.confirm_job(batch_id, job_id))
                latest = await run.io_bound(
                    lambda: service.get_batch(batch_id, include_content=False)
                )
                if not workbench_alive():
                    return
                following = next_review_job(
                    list(latest.get("jobs") or []), current_job_id=job_id
                )
                if following is None and review_runtime is not None:
                    review_runtime["review_open"] = False
                workbench_state["open"] = False
                dialog.close()
                on_change()
                if following is not None:
                    ui.notify(
                        f'已确认，继续审核 {following.get("account_name") or "下一篇"}',
                        type="positive",
                    )
                    client_timer(
                        0.05,
                        lambda: open_review_workbench(
                            state,
                            service,
                            batch_id,
                            int(following["id"]),
                            on_change,
                            review_runtime=review_runtime,
                        ),
                        once=True,
                    )
                else:
                    ui.notify("全部文章已确认，可以写入草稿箱", type="positive")
            except Exception as exc:  # noqa: BLE001
                if workbench_alive():
                    ui.notify(
                        f"确认失败：{sanitize_failure_text(exc)}",
                        type="negative",
                        timeout=10000,
                    )
            finally:
                if workbench_alive():
                    set_button_loading(confirm_btn, False)
                    sync_confirm_gate()

        def needs_changes() -> None:
            updated = service.request_job_changes(batch_id, job_id)
            job["review_status"] = str(
                updated.get("review_status") or "needs_changes"
            )
            apply_deep_review_mode()
            render_quick_review_summary()
            ui.notify(
                "已标记为需要修改",
                type="warning",
            )

        def apply_deep_review_mode() -> None:
            """Keep the workbench in the full deep-editing experience."""

            for control in deep_review_controls:
                control.set_visibility(True)
            for control in deep_review_actions:
                control.set_visibility(True)
            for control in quick_review_actions:
                control.set_visibility(False)

        ui.element("div").classes("review-action-spacer").props("aria-hidden=true")
        with ui.row().classes("review-action-bar w-full justify-end q-mt-md"):
            save_btn = ui.button("保存文章修改", on_click=save_and_render).props(
                "outline color=teal-9 no-caps"
            )
            needs_changes_btn = ui.button(
                "需要修改",
                on_click=needs_changes,
            ).props(
                "outline color=deep-orange-8 no-caps icon=edit_note"
            )
            go_process_btn = ui.button(
                "去处理",
                on_click=reveal_deep_review,
            ).props(
                "unelevated color=deep-orange-8 no-caps icon=rule"
            )
            confirm_btn = ui.button(
                "确认此文章",
                on_click=confirm,
            ).props(
                "unelevated color=teal-9 no-caps icon=check"
            )
            confirm_controls.update(
                confirm=confirm_btn,
                go_process=go_process_btn,
            )
            sync_confirm_gate()
        deep_review_actions.extend((save_btn, needs_changes_btn))
        apply_deep_review_mode()
    if review_runtime is not None:
        def sync_review_open_state(event: Any) -> None:
            is_open = bool(event.value)
            workbench_state["open"] = is_open
            review_runtime["review_open"] = is_open

        dialog.on_value_change(sync_review_open_state)
    else:
        dialog.on_value_change(
            lambda event: workbench_state.__setitem__(
                "open", bool(event.value)
            )
        )
    dialog.open()


def _render_batch_card(
    state: AppState,
    service: BatchService,
    batch: dict[str, Any],
    refresh: Callable[[], None],
    *,
    review_runtime: dict[str, bool] | None = None,
    focused: bool = False,
    auto_expand: bool = False,
) -> Any:
    """Render a lightweight 68px row and build the rich batch UI on demand."""

    progress = dict(batch.get("progress") or {})
    jobs = [dict(item) for item in list(batch.get("jobs") or [])]
    topic = str(batch.get("topic") or "").strip() or _batch_topic(jobs)
    batch_status = str(batch.get("status") or "")

    def open_details() -> None:
        with ui.dialog() as dialog, ui.card().classes(
            "w-full ops-dialog-xl ops-dialog-scroll ops-task-detail-dialog"
        ):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0 ops-flex-copy"):
                    ui.label(f'批次 #{batch["display_id"]}').classes(
                        "text-h6 text-weight-bold"
                    )
                    ui.label(topic or "未命名批次").classes("muted")
                ui.button(icon="close", on_click=dialog.close).props(
                    "flat round dense aria-label=关闭"
                )
            _render_batch_detail_content(
                state,
                service,
                batch,
                refresh,
                review_runtime=review_runtime,
                focused=True,
                auto_expand=True,
            )
        dialog.open()

    if batch_status in {"pending", "processing", "injecting"}:
        action_label = "查看进度"
        action_icon = "monitor_heart"
    elif batch_status in {"failed", "partial_failed"} or int(
        progress.get("failed") or 0
    ):
        action_label = "恢复失败任务"
        action_icon = "restart_alt"
    elif batch_status == "ready_for_draft":
        action_label = "写入草稿"
        action_icon = "outbox"
    elif batch_status == "ready_for_review":
        action_label = "打开审核"
        action_icon = None
    else:
        action_label = "查看任务"
        action_icon = "visibility"

    row = ui.card().classes("ops-task-row-card ops-batch-row-card")
    with row:
        with ui.element("span").classes("ops-task-row-icon"):
            ui.icon("inventory_2", size="20px").classes("ops-semantic-icon")
        with ui.column().classes("ops-task-row-copy"):
            ui.label(topic or "未命名批次").classes("ops-task-row-title")
            ui.label(
                f'批次 #{batch.get("display_id") or ""} · 公众号 {len(jobs)} 个'
            ).classes("ops-task-row-meta")
        ui.badge(_batch_status_text(batch)).props(
            f'color={_batch_color(batch_status)}'
        )
        ui.label(
            f'已审核 {progress.get("reviewed", 0)}/{progress.get("review_total", 0)}'
            f' · 草稿 {progress.get("drafted", 0)} · 失败 {progress.get("failed", 0)}'
        ).classes("ops-task-row-state")
        with ui.row().classes("ops-task-row-actions"):
            ui.button(
                action_label,
                icon=action_icon,
                on_click=open_details,
            ).classes("ops-task-row-primary-action").props(
                "unelevated dense color=primary no-caps"
            )
            ui.button(icon="more_horiz", on_click=open_details).props(
                "flat round dense color=grey-7 aria-label=查看任务详情"
            )

    if focused:
        ui.timer(0.05, open_details, once=True)
    _ = auto_expand
    return row


def _render_batch_detail_content(
    state: AppState,
    service: BatchService,
    batch: dict[str, Any],
    refresh: Callable[[], None],
    *,
    review_runtime: dict[str, bool] | None = None,
    focused: bool = False,
    auto_expand: bool = False,
) -> Any:
    owner_client = ui.context.client
    progress = batch.get("progress") or {}
    jobs = list(batch.get("jobs") or [])
    topic = str(batch.get("topic") or "").strip() or _batch_topic(jobs)

    async def retry_failed_jobs_in_place(button: Any) -> None:
        failed_jobs = [
            job for job in jobs if str(job.get("status") or "") == "failed"
        ]
        _set_retry_loading_safely(
            button,
            True,
            owner_client=owner_client,
        )
        accepted = 0
        errors: list[str] = []
        try:
            accepted, errors = await run.io_bound(
                lambda: _submit_failed_job_retries(
                    service,
                    str(batch["id"]),
                    failed_jobs,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors = [sanitize_failure_text(exc)]
        finally:
            _set_retry_loading_safely(
                button,
                False,
                owner_client=owner_client,
            )
        if not _ui_client_alive(owner_client):
            return
        if accepted:
            ui.notify(
                f"已按失败步骤原地恢复 {accepted} 篇文章",
                type="positive" if not errors else "warning",
            )
        if errors:
            ui.notify(
                "部分文章未能提交恢复：" + "；".join(errors),
                type="warning",
                timeout=12000,
            )
        if review_runtime is not None:
            review_runtime["focus_batch_id"] = str(batch["id"])
        refresh()

    with ui.expansion(value=focused or auto_expand).classes("card w-full") as expansion:
        with expansion.add_slot("header"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0 ops-flex-copy"):
                    ui.label(f'批次 #{batch["display_id"]}').classes("text-weight-bold")
                    ui.label(topic or "未命名批次").classes("text-weight-medium")
                    ui.label(
                        f'公众号 {len(jobs)} 个 · 已审核 {progress.get("reviewed", 0)}/'
                        f'{progress.get("review_total", 0)} · '
                        f'已写入草稿箱 {progress.get("drafted", 0)} · 失败 {progress.get("failed", 0)}'
                    ).classes("muted")
                ui.badge(_batch_status_text(batch)).props(
                    f'color={_batch_color(str(batch.get("status") or ""))}'
                )

        ui.label(
            f'创建：{_format_time(batch.get("created_at"))} · '
            f'更新：{_format_time(batch.get("updated_at"))} · '
            f'耗时：{_duration(batch.get("created_at"), batch.get("updated_at"))}'
        ).classes("muted")
        if batch.get("source_url"):
            ui.link("查看来源链接", str(batch["source_url"]), new_tab=True).classes("text-teal-9")

        for job in jobs:
            review_status = str(job.get("review_status") or "unviewed")
            with ui.row().classes("w-full items-center justify-between job-row q-pa-sm"):
                with ui.column().classes("gap-0 ops-flex-copy"):
                    ui.label(str(job.get("account_name") or "公众号")).classes("text-weight-medium")
                    ui.label(
                        str(job.get("selected_title") or "尚未选择标题")
                    ).classes("muted")
                    if job.get("error"):
                        with ui.row().classes("items-center gap-1"):
                            ui.label(
                                _friendly_error(str(job["error"]))
                            ).classes("text-negative")
                            ui.button(
                                "复制错误",
                                on_click=lambda _=None, error=str(job["error"]): ui.clipboard.write(
                                    error
                                ),
                            ).props(
                                "flat dense color=red-7 no-caps icon=content_copy"
                            )
                if job.get("status") == "ready_for_review":
                    ui.badge(REVIEW_LABELS.get(review_status, review_status)).props(
                        f'color={REVIEW_COLORS.get(review_status, "grey-7")}'
                    )
                    ui.button(
                        "打开审核",
                        on_click=lambda _=None, jid=int(job["id"]): open_review_workbench(
                            state,
                            service,
                            str(batch["id"]),
                            jid,
                            refresh,
                            review_runtime=review_runtime,
                        ),
                    ).props("outline dense color=teal-9 no-caps")
                else:
                    status = str(job.get("status") or "")
                    ui.badge(STATUS_LABEL.get(status, status)).props(
                        f'color={_job_color(status)}'
                    )

        with ui.row().classes("w-full items-center justify-between q-mt-sm"):
            unconfirmed = int(progress.get("unconfirmed") or 0)
            ready_count = int(progress.get("ready_for_review") or 0)
            failed_count = int(progress.get("failed") or 0)
            if unconfirmed:
                review_message = (
                    f'已审核 {progress.get("reviewed", 0)}/{progress.get("review_total", 0)}，'
                    f"尚有 {unconfirmed} 篇未确认"
                )
                review_class = "text-warning"
            elif ready_count:
                review_message = f"{ready_count} 篇已确认，可以写入草稿箱"
                review_class = "text-positive"
            elif failed_count:
                review_message = "当前没有可写入文章，请先重试失败任务"
                review_class = "text-negative"
            else:
                review_message = "本批次已处理完成"
                review_class = "text-positive"
            ui.label(review_message).classes(review_class)
            with ui.row().classes("items-center"):
                if failed_count > 0:
                    retry_failed_btn = ui.button(
                        "仅重试失败公众号",
                        on_click=lambda: retry_failed_jobs_in_place(
                            retry_failed_btn
                        ),
                    ).props(
                        "outline dense color=orange-8 no-caps icon=restart_alt"
                    )
                ui.button("按原设置重新生成", on_click=lambda: _run_action(
                    lambda: service.copy_batch(str(batch["id"])), refresh, "已复制并开始新批次"
                )).props("flat dense color=teal-9 no-caps")
                if str(batch.get("status")) in {"processing", "pending", "injecting"}:
                    ui.button("停止生成", on_click=lambda: _run_action(
                        lambda: service.cancel_batch(str(batch["id"])), refresh, "已请求停止生成"
                    )).props("flat dense color=grey-8 no-caps")
                pending_job = next_review_job(jobs)
                if unconfirmed and pending_job is not None:
                    ui.button(
                        f"审核下一篇（剩余 {unconfirmed} 篇）",
                        on_click=lambda _=None, jid=int(pending_job["id"]): open_review_workbench(
                            state,
                            service,
                            str(batch["id"]),
                            jid,
                            refresh,
                            review_runtime=review_runtime,
                        ),
                    ).props("unelevated dense color=teal-9 no-caps icon=rate_review")
                else:
                    write_btn = ui.button(
                        f"写入已确认的 {ready_count} 篇",
                        on_click=lambda: confirm_batch_write(service, batch, refresh),
                    ).props("unelevated dense color=teal-9 no-caps")
                    if not ready_count:
                        write_btn.disable()
                ui.button("归档", on_click=lambda: _run_action(
                    lambda: service.archive_batch(str(batch["id"])), refresh, "批次已归档"
                )).props("flat dense color=grey-7 no-caps")
    return expansion


def _submit_failed_job_retries(
    service: BatchService,
    batch_id: str,
    failed_jobs: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    """Claim each failed article in place without creating a compatibility batch."""

    accepted = 0
    errors: list[str] = []
    for failed_job in failed_jobs:
        try:
            service.retry_job(
                batch_id,
                int(failed_job["id"]),
                step="auto",
            )
            accepted += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f'{failed_job.get("account_name") or "公众号"}：'
                f"{sanitize_failure_text(exc)}"
            )
    return accepted, errors


def confirm_batch_write(
    service: BatchService, batch: dict[str, Any], refresh: Callable[[], None]
) -> None:
    targets = [
        dict(job)
        for job in batch.get("jobs") or []
        if job.get("status") == "ready_for_review"
        and job.get("review_status") == "confirmed"
    ]
    names = [str(job.get("account_name") or "") for job in targets]
    account_ids = list(
        dict.fromkeys(
            str(job.get("account_id") or "").strip()
            for job in targets
            if str(job.get("account_id") or "").strip()
        )
    )
    with ui.dialog() as dialog, ui.card():
        ui.label(f"确认写入 {len(names)} 篇文章？").classes("text-h6 text-weight-bold")
        ui.label(f"将写入 {len(names)} 个公众号：{'、'.join(names)}")
        ui.label("仅写入草稿箱，不会直接群发。").classes("text-warning")

        async def submit() -> None:
            set_button_loading(
                button,
                True,
                f"正在同时写入 {len(names)} 个公众号草稿箱，请稍候…",
            )
            try:
                if account_ids:
                    reports = await run.io_bound(
                        lambda: service.preflight(
                            account_ids,
                            force_wechat_check=True,
                        )
                    )
                    if has_ip_whitelist_issue(reports):
                        dialog.close()
                        show_ip_whitelist_guide(names)
                        return
                result = await run.io_bound(
                    lambda: service.inject_batch(str(batch["id"]))
                )
                result_jobs = list(result.get("jobs") or [])
                written = sum(
                    1
                    for job in result_jobs
                    if str(job.get("status") or "") in {"drafted", "published"}
                )
                failed = sum(
                    1 for job in result_jobs if str(job.get("status") or "") == "failed"
                )
                ip_failed_names = [
                    str(job.get("account_name") or "")
                    for job in result_jobs
                    if str(job.get("status") or "") == "failed"
                    and has_ip_whitelist_issue(job)
                ]
                if failed:
                    ui.notify(
                        f"草稿写入完成：成功 {written} 篇，失败 {failed} 篇，可在本批次重试",
                        type="warning",
                        timeout=12000,
                    )
                else:
                    ui.notify(f"已写入 {written} 个公众号草稿箱", type="positive")
                dialog.close()
                if ip_failed_names:
                    show_ip_whitelist_guide(ip_failed_names)
                refresh()
            except Exception as exc:  # noqa: BLE001
                if has_ip_whitelist_issue(exc):
                    dialog.close()
                    show_ip_whitelist_guide(names)
                    return
                ui.notify(
                    f"写入失败：{sanitize_failure_text(exc)}",
                    type="negative",
                    timeout=10000,
                )
            finally:
                set_button_loading(button, False)

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            button = ui.button("确认写入", on_click=submit).props(
                "unelevated color=teal-9 no-caps"
            )
    dialog.open()


def _run_action(action: Callable[[], Any], refresh: Callable[[], None], message: str) -> None:
    try:
        action()
        ui.notify(message, type="positive")
        refresh()
    except Exception as exc:  # noqa: BLE001
        ui.notify(
            sanitize_failure_text(exc),
            type="negative",
            timeout=10000,
        )


def _matches_filters(
    batch: dict[str, Any], *, search: str, status: str, account_id: str, today: bool
) -> bool:
    jobs = list(batch.get("jobs") or [])
    needle = search.strip().casefold()
    haystack = " ".join(
        [str(batch.get("topic") or ""), str(batch.get("source_url") or "")]
        + [
            f'{job.get("account_name", "")} {job.get("selected_title", "")}'
            for job in jobs
        ]
    ).casefold()
    if needle and needle not in haystack:
        return False
    if account_id and not any(str(job.get("account_id")) == account_id for job in jobs):
        return False
    if today:
        created_at = format_business_datetime(batch.get("created_at"))
        if not created_at.startswith(business_date().isoformat()):
            return False
    if status == "attention":
        progress = batch.get("progress") or {}
        return bool(progress.get("unconfirmed") or progress.get("failed"))
    if status == "active":
        return str(batch.get("status") or "") in {"pending", "processing", "injecting"}
    if status and str(batch.get("status") or "") != status:
        return False
    return True


def _batch_topic(jobs: list[dict[str, Any]]) -> str:
    return str(next((job.get("selected_title") or "" for job in jobs if job.get("selected_title")), ""))


def _batch_status_text(batch: dict[str, Any]) -> str:
    status = str(batch.get("status") or "")
    progress = batch.get("progress") or {}
    if progress.get("drafted") and progress.get("failed"):
        return "部分成功"
    return {
        "pending": "等待中",
        "processing": "正在生成",
        "ready_for_review": "待审核",
        "ready_for_draft": "待写入草稿",
        "injecting": "写入中",
        "drafted": "已写入草稿箱",
        "partial_failed": "部分失败",
        "failed": "失败",
        "cancelled": "已停止",
    }.get(status, status)


def _batch_color(status: str) -> str:
    return {
        "pending": "grey-7",
        "processing": "blue-7",
        "ready_for_review": "orange-8",
        "ready_for_draft": "teal-7",
        "injecting": "blue-7",
        "drafted": "green-7",
        "partial_failed": "orange-8",
        "failed": "red-7",
        "cancelled": "grey-7",
    }.get(status, "grey-7")


def _job_color(status: str) -> str:
    return {
        "pending": "grey-7",
        "ingesting": "blue-7",
        "rewriting": "blue-7",
        "title_optimizing": "blue-7",
        "rendering": "blue-7",
        "injecting": "blue-7",
        "drafted": "green-7",
        "published": "green-7",
        "failed": "red-7",
        "cancelled": "grey-7",
    }.get(status, "grey-7")


def _format_time(value: Any) -> str:
    return format_business_datetime(value)


def _duration(start: Any, end: Any) -> str:
    try:
        seconds = max(
            0,
            int((datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()),
        )
        return f"{seconds // 60}分{seconds % 60:02d}秒" if seconds >= 60 else f"{seconds}秒"
    except (TypeError, ValueError):
        return "-"


def _friendly_error(message: str) -> str:
    lower = message.lower()
    if "429" in message or "overload" in lower or "过载" in message:
        return "模型服务繁忙，请稍后重试或更换模型"
    wechat_markers = (
        "wechat api",
        "wechat gateway",
        "40125",
        "40164",
        "invalid appsecret",
        "invalid appid",
        "10054",
    )
    if any(marker in lower for marker in wechat_markers):
        return friendly_wechat_error(message)
    return message
