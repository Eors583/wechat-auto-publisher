from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from nicegui import ui

from app.services.wechat_relay_settings import (
    DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP,
)


FIXED_EGRESS_IP = DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP
WECHAT_CONSOLE_URL = "https://mp.weixin.qq.com/"

_IP_ERROR_PATTERN = re.compile(
    r"(?:40164|invalid[\s_-]*ip|ip[\s_-]*not[\s_-]*whitelist"
    r"|ip\s*白名单未放行|出口\s*ip\s*未加入|固定出口\s*ip\s*尚未加入"
    r"|当前出口\s*ip\s*未加入)",
    re.IGNORECASE,
)


def has_ip_whitelist_issue(value: Any) -> bool:
    """Return whether an error/report specifically indicates WeChat IP 40164."""

    if isinstance(value, dict):
        code = str(value.get("code") or "").casefold()
        if code in {
            "inject.ip_not_whitelisted",
            "wechat.ip_not_whitelisted",
        }:
            return True
        return any(
            has_ip_whitelist_issue(value.get(key))
            for key in (
                "error",
                "reason",
                "message",
                "title",
                "failure",
                "technical_summary",
                "checks",
                "jobs",
            )
            if key in value
        )
    if isinstance(value, (list, tuple, set)):
        return any(has_ip_whitelist_issue(item) for item in value)
    return bool(_IP_ERROR_PATTERN.search(str(value or "")))


def show_ip_whitelist_guide(
    account_names: Iterable[str] | None = None,
) -> Any:
    """Show the operator tutorial only when a draft check reports IP 40164."""

    names = list(
        dict.fromkeys(
            str(name or "").strip()
            for name in list(account_names or [])
            if str(name or "").strip()
        )
    )
    with ui.dialog() as dialog, ui.card().classes("w-full").style(
        "max-width:680px"
    ):
        with ui.row().classes("w-full items-start justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("微信公众号 IP 白名单未配置").classes(
                    "text-h6 text-weight-bold text-deep-orange-9"
                )
                ui.label(
                    "草稿箱接口已被微信拦截，请先把固定出口 IP 加入目标公众号。"
                ).classes("muted")
            ui.icon("warning_amber", color="deep-orange-8", size="32px")

        if names:
            ui.label("需要配置：" + "、".join(names)).classes(
                "text-weight-medium"
            )

        with ui.element("div").classes(
            "w-full q-pa-md rounded-borders bg-orange-1"
        ):
            ui.label("需要加入白名单的固定 IP").classes(
                "text-caption text-grey-7"
            )
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(FIXED_EGRESS_IP).classes(
                    "text-h5 text-weight-bold text-teal-10"
                )

                def copy_ip() -> None:
                    ui.clipboard.write(FIXED_EGRESS_IP)
                    ui.notify("固定出口 IP 已复制", type="positive")

                ui.button(
                    "复制 IP",
                    icon="content_copy",
                    on_click=copy_ip,
                ).props("outline color=teal-9 no-caps")

        ui.label("配置步骤").classes("text-subtitle1 text-weight-bold")
        for index, text in enumerate(
            (
                "登录微信公众平台，进入需要写入草稿的公众号。",
                "打开“设置与开发”，进入“基本配置”或“开发接口管理”。",
                "找到“IP 白名单”，点击修改。",
                f"添加 {FIXED_EGRESS_IP}，保存并按微信要求扫码确认。",
                "回到本应用，再次点击“写入草稿箱”。",
            ),
            start=1,
        ):
            with ui.row().classes("w-full items-start no-wrap"):
                ui.badge(str(index), color="teal-8")
                ui.label(text).classes("text-body2")

        ui.label(
            "这是草稿箱官方接口的安全要求；只需配置一次。"
            "系统只写入草稿箱，不会自动群发。"
        ).classes("text-caption text-grey-7")
        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("关闭", on_click=dialog.close).props(
                "flat color=grey-7 no-caps"
            )
            ui.link(
                "打开微信公众平台",
                WECHAT_CONSOLE_URL,
                new_tab=True,
            ).classes("q-btn q-btn-item text-teal-9")
    dialog.open()
    return dialog


__all__ = [
    "FIXED_EGRESS_IP",
    "has_ip_whitelist_issue",
    "show_ip_whitelist_guide",
]
