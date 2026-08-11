from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from app.services.analytics import AnalyticsService
from app.ui.state import AppState


def build_overview_cards(
    state: AppState,
    *,
    on_go_tasks: Callable[[str, bool], Any],
) -> None:
    """Render the compact operational overview inside the writing workbench."""

    @ui.refreshable
    def content() -> None:
        overview = AnalyticsService(state.db).get_overview()
        status_counts = dict(overview.get("status_counts") or {})
        with ui.element("section").classes("ops-metric-grid"):
            for (
                label,
                value,
                unit,
                hint,
                icon,
                tone,
                status_filter,
                today_only,
            ) in (
                (
                    "今日批次",
                    overview.get("today_batches", 0),
                    "个",
                    f"今天生成 {overview.get('today_articles', 0)} 篇文章",
                    "content_copy",
                    "",
                    "",
                    True,
                ),
                (
                    "后台任务",
                    overview.get("processing_articles", 0),
                    "个",
                    "生成、评审和改写持续运行",
                    "sync",
                    "ops-metric-purple",
                    "active",
                    False,
                ),
                (
                    "待审核",
                    overview["pending_review_articles"],
                    "篇",
                    f"{overview.get('review_status_counts', {}).get('needs_changes', 0)} 篇需要修改",
                    "assignment_turned_in",
                    "ops-metric-orange",
                    "ready_for_review",
                    False,
                ),
                (
                    "可写草稿",
                    overview.get("ready_for_draft_articles", 0),
                    "篇",
                    "均需人工确认后写入",
                    "send",
                    "ops-metric-green",
                    "ready_for_draft",
                    False,
                ),
                (
                    "待修复",
                    overview["failed_articles"],
                    "项",
                    "失败阶段可原地恢复",
                    "error_outline",
                    "ops-metric-red",
                    "failed",
                    False,
                ),
            ):
                with ui.element("button").classes(
                    f"ops-metric-item {tone}".strip()
                ).props("type=button").on(
                    "click",
                    lambda _=None, status=status_filter, today=today_only: on_go_tasks(
                        status, today
                    ),
                ):
                    with ui.element("span").classes("ops-metric-icon"):
                        ui.icon(icon, size="19px").classes("ops-semantic-icon")
                    with ui.column().classes("ops-metric-copy"):
                        ui.label(label)
                        ui.label(f"{value} {unit}").classes("ops-metric-value")
                    ui.label(hint).classes("ops-metric-hint")

    content()


__all__ = ["build_overview_cards"]
