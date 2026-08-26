from __future__ import annotations

from typing import Any

from nicegui import run, ui

from app.services.jizhile_settings import (
    clear_jizhile_settings,
    public_jizhile_settings,
    save_jizhile_settings,
    test_saved_jizhile_settings,
)
from app.ui.state import AppState, set_button_loading


def build_admin_jizhile_panel(state: AppState) -> None:
    """Render the platform-owned Topic Radar provider configuration."""

    platform_db = state.db.for_user(None)
    saved = public_jizhile_settings(platform_db)
    configured = bool(saved.get("has_key"))

    with ui.element("div").classes("admin-card w-full"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            with ui.column().classes("gap-1 col"):
                ui.label("极致了文章数据源").classes(
                    "text-h6 text-weight-bold"
                )
                ui.label(
                    "供所有用户的选题雷达统一获取公众号历史文章。"
                    "API Key 只保存在平台配置中，普通用户不可查看或修改。"
                ).classes("muted")
            status = ui.badge(
                "已启用"
                if saved.get("enabled") and configured
                else ("已配置，未启用" if configured else "未配置")
            )
            status.props(
                "color=green-7"
                if saved.get("enabled") and configured
                else ("color=orange-7" if configured else "color=grey-6")
            )

        ui.link(
            "查看极致了历史文章接口文档",
            "https://s.apifox.cn/410674f9-f451-4b4f-957a-5f54f243bc83/199746415e0",
            new_tab=True,
        ).classes("text-teal-9")
        key = ui.input(
            "API Key" + ("（已保存，留空表示不修改）" if configured else ""),
            password=True,
            password_toggle_button=True,
            placeholder="粘贴平台极致了账户提供的 API Key",
        ).classes("w-full").props(
            "outlined stack-label autocomplete=new-password"
        )
        verifycode = ui.input(
            "附加码（可选）"
            + ("（已保存，留空表示不修改）" if saved.get("has_verifycode") else ""),
            password=True,
            password_toggle_button=True,
            placeholder="账户未设置附加码时留空",
        ).classes("w-full").props(
            "outlined stack-label autocomplete=new-password"
        )
        session_label = ui.input(
            "配置备注（可选）",
            value=str(saved.get("session_label") or ""),
            placeholder="例如：运营团队极致了账户",
        ).classes("w-full").props("outlined stack-label")
        enabled = ui.switch(
            "启用为选题雷达公共文章数据源",
            value=bool(saved.get("enabled")) if configured else True,
        )
        ui.label(
            "连接测试只查询账户余额；用户获取文章时才调用历史文章接口。"
            "密钥加密保存，页面不会回显明文。"
        ).classes("muted text-caption")
        details = []
        if saved.get("remain_money") is not None:
            details.append(f'最近余额：{saved.get("remain_money")}')
        if saved.get("checked_at"):
            details.append(f'检测时间：{saved.get("checked_at")}')
        result_label = ui.label(" · ".join(details) or "尚未验证本次输入").classes(
            "muted"
        )

        with ui.row().classes("w-full justify-end gap-2"):
            clear_btn = ui.button("清除配置", icon="delete_outline").props(
                "flat color=red-7 no-caps"
            )
            test_btn = ui.button("仅测试", icon="wifi_tethering").props(
                "outline color=teal-9 no-caps"
            )
            save_btn = ui.button("测试并保存", icon="verified").props(
                "unelevated color=teal-9 no-caps"
            )

        async def test_input() -> None:
            set_button_loading(test_btn, True, "正在验证极致了 API…")
            save_btn.disable()
            try:
                result = await run.io_bound(
                    lambda: test_saved_jizhile_settings(
                        platform_db,
                        key=str(key.value or ""),
                        verifycode=str(verifycode.value or ""),
                    )
                )
                result_label.set_text(
                    f'验证成功，当前余额：{result.get("remain_money", "-")}'
                )
                result_label.classes(replace="text-positive")
                ui.notify("极致了 API 连接正常", type="positive")
            except Exception as exc:  # noqa: BLE001
                result_label.set_text(str(exc))
                result_label.classes(replace="text-negative")
                ui.notify(str(exc), type="negative", timeout=15000)
            finally:
                save_btn.enable()
                set_button_loading(test_btn, False)

        async def test_and_save() -> None:
            set_button_loading(save_btn, True, "正在测试并安全保存…")
            test_btn.disable()
            try:
                key_value = str(key.value or "")
                verifycode_value = str(verifycode.value or "")

                def verify_and_save() -> dict[str, Any]:
                    result = test_saved_jizhile_settings(
                        platform_db,
                        key=key_value,
                        verifycode=verifycode_value,
                    )
                    save_jizhile_settings(
                        platform_db,
                        enabled=bool(enabled.value),
                        key=key_value,
                        verifycode=verifycode_value,
                        session_label=str(session_label.value or ""),
                        remain_money=result.get("remain_money"),
                        checked_at=str(result.get("request_time") or ""),
                    )
                    return result

                result = await run.io_bound(verify_and_save)
                result_label.set_text(
                    f'已保存，当前余额：{result.get("remain_money", "-")}'
                )
                result_label.classes(replace="text-positive")
                status.set_text("已启用" if enabled.value else "已配置，未启用")
                status.props("color=green-7" if enabled.value else "color=orange-7")
                ui.notify("极致了平台配置已保存", type="positive")
            except Exception as exc:  # noqa: BLE001
                result_label.set_text(str(exc))
                result_label.classes(replace="text-negative")
                ui.notify(str(exc), type="negative", timeout=15000)
            finally:
                test_btn.enable()
                set_button_loading(save_btn, False)

        def clear() -> None:
            clear_jizhile_settings(platform_db)
            status.set_text("未配置")
            status.props("color=grey-6")
            result_label.set_text("平台极致了配置已清除")
            result_label.classes(replace="muted")
            ui.notify("极致了平台配置已清除", type="positive")

        test_btn.on_click(test_input)
        save_btn.on_click(test_and_save)
        clear_btn.on_click(clear)
