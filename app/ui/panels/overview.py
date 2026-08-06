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
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("今日运营概览").classes("text-subtitle1 text-weight-bold")
                ui.label("点击任一卡片进入任务中心查看明细").classes("muted")
            ui.button("刷新", on_click=content.refresh).props(
                "flat dense color=teal-9 no-caps icon=refresh"
            )
        with ui.grid(columns=4).classes("w-full gap-3"):
            for label, value, hint, color, status_filter, today_only in (
                (
                    "今日生成",
                    overview.get("today_articles", 0),
                    "今天创建的公众号文章",
                    "text-teal-10",
                    "",
                    True,
                ),
                (
                    "待审核",
                    overview["pending_review_articles"],
                    "等待运营确认",
                    "text-orange-9",
                    "ready_for_review",
                    False,
                ),
                (
                    "已入草稿",
                    overview["drafted_or_published_articles"],
                    "历史累计成功",
                    "text-green-8",
                    "drafted",
                    False,
                ),
                (
                    "失败",
                    overview["failed_articles"],
                    "可在任务中心重试",
                    "text-red-8",
                    "failed",
                    False,
                ),
            ):
                with ui.element("div").classes(
                    "card q-pa-md cursor-pointer"
                ).style("min-height:112px").on(
                    "click",
                    lambda _=None, status=status_filter, today=today_only: on_go_tasks(
                        status, today
                    ),
                ):
                    ui.label(label).classes("muted")
                    ui.label(str(value)).classes(
                        f"text-h5 text-weight-bold {color}"
                    )
                    ui.label(hint).classes("muted text-caption")

    content()


__all__ = ["build_overview_cards"]
