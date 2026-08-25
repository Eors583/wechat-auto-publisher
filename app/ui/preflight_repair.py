from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from app.services.failures import sanitize_failure_text
from app.ui.navigation import ui_navigation_target, ui_root_url

PREFLIGHT_REPAIR_ACTIONS: dict[str, tuple[str, str]] = {
    "account": ("account", "配置公众号"),
    "model": ("account", "绑定文章模型"),
    "wechat": ("account", "检查公众号凭证"),
    "draft": ("account", "检查草稿权限"),
    "material": ("images", "配置封面素材"),
    "cover": ("images", "选择有效封面"),
    "template": ("template", "打开模板管理"),
    "inline_images": ("images", "配置正文生图"),
}


def preflight_repair_action(check_key: str) -> tuple[str, str]:
    return PREFLIGHT_REPAIR_ACTIONS.get(
        str(check_key or "").strip(),
        ("account", "打开公众号配置"),
    )


def preflight_repair_url(account_id: str, check_key: str) -> str:
    action, _ = preflight_repair_action(check_key)
    return ui_root_url(
        {
            "view": "config",
            "repair": action,
            "account_id": str(account_id or "").strip(),
        }
    )


def preflight_failures(reports: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep every failed preflight reason and its matching repair target."""

    failures: list[dict[str, str]] = []
    for raw_report in reports:
        if not isinstance(raw_report, dict):
            continue
        report = dict(raw_report)
        account_id = str(report.get("account_id") or "").strip()
        account_name = str(report.get("account_name") or "公众号").strip()
        for raw_check in list(report.get("checks") or []):
            if not isinstance(raw_check, dict):
                continue
            check = dict(raw_check)
            if bool(check.get("ok")):
                continue
            check_key = str(check.get("key") or "account").strip()
            check_name = str(
                check.get("name") or check.get("label") or "配置检查"
            ).strip()
            reason = sanitize_failure_text(
                check.get("message")
                or check.get("detail")
                or "系统未返回具体原因，请重新检测后再试。",
                limit=1600,
            )
            _, repair_label = preflight_repair_action(check_key)
            failures.append(
                {
                    "account_id": account_id,
                    "account_name": account_name,
                    "check_key": check_key,
                    "check_name": check_name,
                    "reason": reason,
                    "repair_label": repair_label,
                    "repair_url": preflight_repair_url(account_id, check_key),
                }
            )
    return failures


def render_preflight_failures(
    reports: list[dict[str, Any]],
    on_repair: Callable[[str, str], None],
) -> list[dict[str, str]]:
    failures = preflight_failures(reports)
    with ui.column().classes("w-full gap-2"):
        for failure in failures:
            with ui.element("article").classes("ops-preflight-issue"):
                with ui.column().classes("ops-preflight-issue-copy gap-1"):
                    ui.label(
                        f'{failure["account_name"]} · {failure["check_name"]}'
                    ).classes("text-weight-bold")
                    ui.label(f'阻塞原因：{failure["reason"]}').classes(
                        "ops-preflight-reason"
                    )
                ui.button(
                    failure["repair_label"],
                    on_click=lambda _=None,
                    aid=failure["account_id"],
                    key=failure["check_key"]: on_repair(aid, key),
                ).props("outline dense color=teal-9 no-caps icon=build")
    return failures


def show_preflight_repair_dialog(
    reports: list[dict[str, Any]],
    *,
    title: str = "当前操作被配置问题阻止",
    summary: str = "请修复以下配置后重试；已有文章和任务不会丢失。",
) -> Any | None:
    if not preflight_failures(reports):
        return None

    client = ui.context.client
    with client.content:
        with ui.dialog() as dialog, ui.card().classes(
            "w-full ops-dialog-md ops-dialog-scroll ops-preflight-dialog"
        ):
            ui.label(title).classes("text-h6 text-weight-bold")
            ui.label(summary).classes("muted ops-preflight-reason")

            def open_repair(account_id: str, check_key: str) -> None:
                dialog.close()
                ui.navigate.to(
                    ui_navigation_target(
                        preflight_repair_url(account_id, check_key)
                    )
                )

            render_preflight_failures(reports, open_repair)
            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat no-caps")
        dialog.open()
    return dialog


__all__ = [
    "preflight_failures",
    "preflight_repair_action",
    "preflight_repair_url",
    "render_preflight_failures",
    "show_preflight_repair_dialog",
]
