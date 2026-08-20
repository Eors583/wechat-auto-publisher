from __future__ import annotations

from typing import Any

from nicegui import ui

from app.services.failures import sanitize_failure_text
from app.services.wechat_commands import WeChatCommandService


def open_wechat_command_dialog(state: Any, account_id: str) -> None:
    """Configure the official-account message callback for one account."""

    service = WeChatCommandService(state.db, state.config)
    settings = service.public_settings(account_id)
    revealed: dict[str, str] = {"token": "", "encoding_aes_key": ""}

    with ui.dialog() as dialog, ui.card().classes("ops-dialog-md ops-dialog-scroll"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            with ui.column().classes("gap-0 ops-flex-copy"):
                ui.label("微信指挥").classes("ops-review-page-title")
                ui.label(
                    f"在微信中直接指挥“{settings['account_name']}”改写、查进度和审核文章。"
                ).classes("ops-panel-subtitle ops-break-anywhere")
            ui.button(icon="close", on_click=dialog.close).props(
                "flat round dense aria-label=关闭"
            )

        with ui.element("section").classes("ops-config-section w-full"):
            ui.label("1. 配置公众号服务器回调").classes("ops-panel-title")
            ui.label(
                "在微信公众平台“设置与开发 → 基本配置 → 服务器配置”中，"
                "粘贴下面三项并选择安全模式（消息加解密）。"
            ).classes("ops-panel-subtitle ops-break-anywhere")

            with ui.row().classes("w-full items-end no-wrap"):
                callback_input = (
                    ui.input(
                        "URL",
                        value=str(settings["callback_url"]),
                    )
                    .classes("w-full ops-break-anywhere")
                    .props("outlined readonly")
                )
                ui.button(
                    icon="content_copy",
                    on_click=lambda: ui.clipboard.write(
                        str(callback_input.value or "")
                    ),
                ).props("flat round color=primary aria-label=复制回调地址")

            secret_host = ui.column().classes("w-full")

            def render_secrets() -> None:
                secret_host.clear()
                with secret_host:
                    if revealed["token"]:
                        for label, key in (
                            ("Token", "token"),
                            ("EncodingAESKey", "encoding_aes_key"),
                        ):
                            with ui.row().classes("w-full items-end no-wrap"):
                                value_input = (
                                    ui.input(
                                        label,
                                        value=revealed[key],
                                    )
                                    .classes("w-full ops-break-anywhere")
                                    .props("outlined readonly")
                                )
                                ui.button(
                                    icon="content_copy",
                                    on_click=lambda _=None, field=value_input: (
                                        ui.clipboard.write(str(field.value or ""))
                                    ),
                                ).props(
                                    f"flat round color=primary aria-label=复制{label}"
                                )
                        ui.label(
                            "这两项只在本次生成后显示；关闭窗口前请复制到微信公众平台。"
                        ).classes("text-warning ops-break-anywhere")
                    elif settings["has_token"]:
                        ui.label(
                            "接入密钥已加密保存。系统不会再次显示明文；如已遗失，请重新生成并同步更新微信后台。"
                        ).classes("ops-panel-subtitle ops-break-anywhere")
                    else:
                        ui.label("尚未生成接入参数。").classes("ops-panel-subtitle")

            render_secrets()

            def provision() -> None:
                try:
                    generated = service.provision(account_id)
                    settings.update(generated)
                    revealed.update(
                        token=str(generated["token"]),
                        encoding_aes_key=str(generated["encoding_aes_key"]),
                    )
                    enable_switch.set_value(True)
                    provision_button.set_text("重新生成接入参数")
                    render_secrets()
                    ui.notify(
                        "接入参数已生成并启用，请复制到微信公众平台", type="positive"
                    )
                except Exception as exc:  # noqa: BLE001
                    ui.notify(sanitize_failure_text(exc), type="negative", timeout=8000)

            provision_button = ui.button(
                "重新生成接入参数" if settings["has_token"] else "生成接入参数",
                icon="key",
                on_click=provision,
            ).props("outline color=primary no-caps")

            def toggle_enabled(event: Any) -> None:
                try:
                    updated = service.set_enabled(account_id, bool(event.value))
                    settings.update(updated)
                    ui.notify(
                        "微信指挥已启用" if event.value else "微信指挥已停用",
                        type="positive",
                    )
                except Exception as exc:  # noqa: BLE001
                    enable_switch.set_value(bool(settings.get("enabled", False)))
                    ui.notify(sanitize_failure_text(exc), type="negative", timeout=8000)

            enable_switch = ui.switch(
                "启用微信指挥",
                value=bool(settings["enabled"]),
            )
            enable_switch.on_value_change(toggle_enabled)

        with ui.element("section").classes("ops-config-section w-full"):
            ui.label("2. 绑定你的微信").classes("ops-panel-title")
            ui.label(
                "服务器配置验证成功后，生成一次性口令，并用你的微信发给这个公众号。"
                "只有已绑定的 OpenID 才能执行指令。"
            ).classes("ops-panel-subtitle ops-break-anywhere")
            pairing_text = ui.label(
                f"当前已绑定 {len(settings['allowed_open_ids'])} 个微信账号"
            ).classes("ops-break-anywhere")

            def create_pairing() -> None:
                try:
                    command = service.create_pairing_code(account_id)
                    pairing_text.set_text(f"请在 30 分钟内发送：{command}")
                    ui.clipboard.write(command)
                    ui.notify("绑定口令已复制", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(sanitize_failure_text(exc), type="negative", timeout=8000)

            ui.button(
                "生成并复制绑定口令",
                icon="link",
                on_click=create_pairing,
            ).props("unelevated color=primary no-caps")

        with ui.element("section").classes("ops-config-section w-full"):
            ui.label("3. 在微信中直接使用").classes("ops-panel-title")
            ui.label(
                "发送一篇公开文章链接即可按当前公众号规则开始改写；也可以发送“帮助”、"
                "“现在进度怎么样”或“确认开始 AI 评审任务 12”。耗时操作会先受理，"
                "执行结果随后通过公众号客服消息返回。"
            ).classes("ops-panel-subtitle ops-break-anywhere")
            ui.link(
                "打开微信公众平台",
                "https://mp.weixin.qq.com/",
                new_tab=True,
            ).classes("text-primary")

    dialog.open()


__all__ = ["open_wechat_command_dialog"]
