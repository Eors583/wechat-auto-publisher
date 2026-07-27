from __future__ import annotations

import re
from typing import Any

from nicegui import run, ui

from app.accounts import public_accounts
from app.feishu.runtime import get_runtime
from app.feishu.settings import public_feishu_settings, save_feishu_settings
from app.services.onboarding import OnboardingService
from app.ui.state import AppState, set_button_loading


PERMISSION_CODES = (
    "im:message.p2p_msg:readonly,"
    "im:message.group_at_msg:readonly,"
    "im:message:send_as_bot,"
    "im:resource"
)


def build_feishu_panel(state: AppState) -> None:
    """Render the complete beginner-safe Feishu setup in its real settings page."""

    state.reload_config()
    service = OnboardingService(state.db, getattr(state, "config", None))
    saved = public_feishu_settings(state.db)
    account_options = {
        str(item["id"]): str(item["name"])
        for item in public_accounts(state.db, enabled_only=True)
    }
    agent_model_options = state.model_options(include_default=False)
    default_agent_model = str(saved.get("agent_model_id") or "")
    if default_agent_model not in agent_model_options:
        default_agent_model = (
            "config:moonshot"
            if "config:moonshot" in agent_model_options
            else next(iter(agent_model_options), "")
        )
    if not agent_model_options:
        agent_model_options = {
            "": "请先在“模型管理 → 文章模型”中添加并启用模型"
        }
    selected_accounts = [
        item
        for item in saved.get("default_account_ids") or []
        if item in account_options
    ]
    if not selected_accounts:
        selected_accounts = list(account_options)
    page_state: dict[str, Any] = {"pairing": None}

    with ui.element("div").classes("card w-full"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("飞书机器人接入").classes("text-h5 text-weight-bold")
                ui.label(
                    "不用准备服务器或公网地址。按下面 1—6 步操作，每一步都在实际配置旁边说明。"
                ).classes("muted")
            ui.badge("长连接模式").props("color=teal-7")
        ui.label(
            "默认采用一次性口令绑定，不需要查 Open ID，也不会把机器人开放给所有人。"
        ).classes("text-positive text-weight-bold q-mt-sm")

    @ui.refreshable
    def runtime_card() -> None:
        readiness = service.readiness()
        runtime = get_runtime(state.db)
        runtime_status = str(runtime.get("status") or "stopped")
        if readiness.get("feishu_ready"):
            label, color = "接入完成", "positive"
            explanation = "本次启动后已收到授权用户消息，并已成功回复。"
        elif runtime_status == "error":
            label, color = "连接异常", "negative"
            explanation = "飞书服务启动失败，请查看下方最近错误。"
        elif runtime_status == "running":
            label, color = "已收到消息，等待完成验证", "warning"
            explanation = "已经收到飞书事件，但还没有完成本次授权消息的成功回复。"
        elif runtime_status == "connecting":
            label, color = "服务已启动，等待测试消息", "warning"
            explanation = "本机长连接正在等待飞书消息；收到真实消息前不会显示完成。"
        elif readiness.get("feishu_saved"):
            label, color = "配置已保存，等待重启", "warning"
            explanation = "关闭并重新打开本应用，让飞书服务读取新配置。"
        else:
            label, color = "尚未配置", "grey"
            explanation = "请从第 1 步开始创建飞书企业自建应用。"
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("接入状态").classes("text-h6 text-weight-bold")
                ui.badge(label, color=color)
            ui.label(explanation).classes(
                "text-positive text-weight-bold"
                if readiness.get("feishu_ready")
                else "muted"
            )
            with ui.grid(columns=3).classes("w-full q-mt-sm gap-4"):
                _runtime_item("本次服务启动", runtime.get("started_at"))
                _runtime_item("本次收到消息", runtime.get("last_message_at"))
                _runtime_item("本次成功回复", runtime.get("last_reply_at"))
            if runtime.get("last_error"):
                ui.label(f'最近错误：{runtime["last_error"]}').classes(
                    "text-negative q-mt-sm"
                )
            ui.button(
                "刷新接入状态",
                on_click=lambda: (
                    runtime_card.refresh(),
                    pairing_card.refresh(),
                ),
            ).props("flat color=teal-9 no-caps icon=refresh")

    runtime_card()

    with ui.element("div").classes("card w-full"):
        _step_heading(
            1,
            "创建企业自建应用并复制凭证",
            "必须是“企业自建应用”，不是群聊里的普通自定义机器人。",
        )
        with ui.row().classes("items-center gap-4 q-mb-sm"):
            ui.link(
                "打开飞书开放平台",
                "https://open.feishu.cn/app",
                new_tab=True,
            ).classes("text-teal-9 text-weight-bold")
            ui.link(
                "打开飞书官方机器人教程",
                "https://open.feishu.cn/document/develop-an-echo-bot/introduction",
                new_tab=True,
            ).classes("text-teal-9")
        ui.markdown(
            """
1. 点击“创建企业自建应用”，填写名称和图标。
2. 在“添加应用能力”中添加“机器人”。
3. 打开“凭证与基础信息”，复制 **App ID** 和 **App Secret**。

先不要配置事件订阅。飞书要求本机长连接在线后，才能保存长连接订阅方式。
            """
        ).classes("w-full")

        app_id_in = ui.input(
            "飞书 App ID",
            value=str(saved.get("app_id") or ""),
            placeholder="cli_xxxxxxxxxxxxxxxx",
        ).classes("w-full").props("outlined stack-label")

        def secret_input(label: str, configured: bool, placeholder: str = "") -> Any:
            suffix = "（已保存，留空表示不修改）" if configured else ""
            return ui.input(
                label + suffix,
                password=True,
                password_toggle_button=True,
                placeholder=placeholder,
            ).classes("w-full").props(
                "outlined stack-label autocomplete=new-password"
            )

        app_secret_in = secret_input(
            "App Secret",
            bool(saved.get("has_app_secret")),
            "从飞书“凭证与基础信息”完整复制",
        )

    with ui.element("div").classes("card w-full"):
        _step_heading(
            2,
            "在本页验证并保存",
            "系统会真实调用飞书和智能体模型，只有两项都通过才会保存。",
        )
        agent_model_in = ui.select(
            options=agent_model_options,
            value=default_agent_model,
            label="飞书智能体模型",
        ).classes("w-full").props("outlined stack-label")
        ui.label(
            "它负责理解对话和选择工具；文章仍由各公众号绑定的模型生成。"
        ).classes("muted")
        default_accounts_in = ui.select(
            options=account_options,
            value=selected_accounts,
            label="机器人默认生成到哪些公众号？",
            multiple=True,
        ).classes("w-full").props("outlined stack-label use-chips")
        if not account_options:
            ui.label(
                "尚无可用公众号，请先到“素材与模板”添加并启用公众号。"
            ).classes("text-warning")

        async def test_agent_model() -> None:
            set_button_loading(
                model_test_btn,
                True,
                "正在调用所选文本模型测试连接…",
            )
            try:
                result = await run.io_bound(
                    lambda: service.test_text_model(
                        str(agent_model_in.value or "")
                    )
                )
                runtime_card.refresh()
                ui.notify(
                    str(result.get("message") or "智能体模型连接成功"),
                    type="positive",
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    _friendly_error(exc),
                    type="negative",
                    timeout=15000,
                )
            finally:
                set_button_loading(model_test_btn, False)

        model_test_btn = ui.button(
            "单独测试智能体模型",
            on_click=test_agent_model,
        ).props("outline color=teal-9 no-caps icon=science")

        async def verify_and_save() -> None:
            set_button_loading(
                verify_save_btn,
                True,
                "正在测试智能体模型和飞书凭证…",
            )
            try:
                model_id = str(agent_model_in.value or "").strip()
                account_ids = list(default_accounts_in.value or [])
                if not model_id:
                    raise ValueError("请先选择飞书智能体使用的文本模型")
                if not account_ids:
                    raise ValueError("请至少选择一个机器人默认使用的公众号")
                await run.io_bound(lambda: service.test_text_model(model_id))
                await run.io_bound(
                    lambda: service.test_feishu_credentials(
                        app_id=str(app_id_in.value or ""),
                        app_secret=str(app_secret_in.value or "") or None,
                    )
                )
                current = public_feishu_settings(state.db)
                await run.io_bound(
                    lambda: service.save_feishu(
                        app_id=str(app_id_in.value or ""),
                        app_secret=str(app_secret_in.value or "") or None,
                        agent_model_id=model_id,
                        default_account_ids=account_ids,
                        allow_all=False,
                        allowed_open_ids=list(
                            current.get("allowed_open_ids") or []
                        ),
                        allowed_chat_ids=list(
                            current.get("allowed_chat_ids") or []
                        ),
                    )
                )
                app_secret_in.value = ""
                runtime_card.refresh()
                pairing_card.refresh()
                ui.notify(
                    "模型和飞书凭证验证成功，已按安全模式保存。下一步请重启本应用。",
                    type="positive",
                    timeout=12000,
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    _friendly_error(exc),
                    type="negative",
                    timeout=15000,
                )
            finally:
                set_button_loading(verify_save_btn, False)

        verify_save_btn = ui.button(
            "一键验证并保存",
            on_click=verify_and_save,
        ).props("unelevated color=teal-9 no-caps icon=verified_user")
        ui.label(
            "App Secret 使用 Windows 当前用户加密保存，页面和状态接口都不会回显明文。"
        ).classes("text-positive q-mt-sm")

    with ui.element("div").classes("card w-full"):
        _step_heading(
            3,
            "关闭并重新打开本应用",
            "保存后的新凭证只有重启飞书服务后才会建立长连接。",
        )
        ui.label(
            "重启后先保持本应用开启。状态显示“服务已启动，等待测试消息”即可继续第 4 步；"
            "收到真实飞书消息前不会显示接入完成。"
        ).classes("text-warning text-weight-bold")
        ui.button(
            "我已重启，检查服务状态",
            on_click=lambda: runtime_card.refresh(),
        ).props("outline color=teal-9 no-caps icon=refresh")

    with ui.element("div").classes("card w-full"):
        _step_heading(
            4,
            "开通权限并设置长连接事件",
            "此时本应用必须保持开启，否则飞书可能无法保存长连接订阅方式。",
        )
        ui.label("在飞书“权限管理”中批量开通以下权限：").classes(
            "text-weight-bold"
        )
        with ui.element("div").classes("soft-panel w-full"):
            ui.label("读取用户发给机器人的单聊消息")
            ui.label("接收群聊中 @ 机器人消息事件")
            ui.label("以应用的身份发消息")
            ui.label("获取与上传图片或文件资源（用于发送封面和预览图）")
            ui.code(PERMISSION_CODES).classes("w-full")
            ui.button(
                "复制权限代码",
                on_click=lambda: ui.clipboard.write(PERMISSION_CODES),
            ).props("flat color=teal-9 no-caps icon=content_copy")
        ui.markdown(
            """
然后进入 **事件与回调 → 事件配置**：

1. 订阅方式选择“使用长连接接收事件”。
2. 添加“接收消息 v2.0”，事件代码为 `im.message.receive_v1`。
3. 不要填写公网回调地址。
            """
        ).classes("w-full")

    with ui.element("div").classes("card w-full"):
        _step_heading(
            5,
            "创建版本并发布",
            "权限和事件配置只有进入已发布版本后，正式成员才能使用。",
        )
        ui.markdown(
            """
1. 打开“版本管理与发布”，创建新版本并提交发布。
2. 把测试人员加入应用“可用范围”。
3. 等待企业管理员通过；通过后可在飞书中搜索机器人。
4. 群聊使用时，需要先把机器人加入群。
            """
        ).classes("w-full")

    with ui.element("div").classes("card w-full"):
        _step_heading(
            6,
            "生成并发送一次性绑定口令",
            "口令只在本页显示一次，过期后失效；系统只保存盐化摘要。",
        )

        async def create_pairing() -> None:
            set_button_loading(
                pairing_btn,
                True,
                "正在生成一次性绑定口令…",
            )
            try:
                readiness = await run.io_bound(service.readiness)
                if str(readiness.get("feishu_runtime_status") or "") not in {
                    "connecting",
                    "running",
                }:
                    raise ValueError("请先重启本应用，等待飞书服务启动后再生成绑定口令")
                page_state["pairing"] = await run.io_bound(
                    service.create_feishu_pairing_code
                )
                pairing_card.refresh()
                ui.notify(
                    "绑定口令已生成，请在 30 分钟内发送给飞书机器人。",
                    type="positive",
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    _friendly_error(exc),
                    type="negative",
                    timeout=15000,
                )
            finally:
                set_button_loading(pairing_btn, False)

        pairing_btn = ui.button(
            "生成一次性绑定口令",
            on_click=create_pairing,
        ).props("unelevated color=teal-9 no-caps icon=key")

        async def check_pairing() -> None:
            set_button_loading(
                pairing_check_btn,
                True,
                "正在检查本次消息和回复状态…",
            )
            try:
                await run.io_bound(service.readiness)
                pairing_card.refresh()
                runtime_card.refresh()
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    _friendly_error(exc),
                    type="negative",
                    timeout=15000,
                )
            finally:
                set_button_loading(pairing_check_btn, False)

        pairing_check_btn = ui.button(
            "我已发送绑定口令，检查结果",
            on_click=check_pairing,
        ).props("outline color=teal-9 no-caps icon=refresh")

        @ui.refreshable
        def pairing_card() -> None:
            readiness = service.readiness()
            pairing = page_state.get("pairing")
            status = service.feishu_pairing_status()
            with ui.element("div").classes("soft-panel w-full q-mt-md"):
                if readiness.get("feishu_ready"):
                    ui.label("飞书机器人接入完成").classes(
                        "text-positive text-h6 text-weight-bold"
                    )
                    ui.label(
                        "本次真实授权消息已收到并成功回复。以后可以直接向机器人发送文章链接。"
                    ).classes("text-positive")
                    return
                if pairing:
                    message = str(pairing.get("message") or "")
                    ui.label("请把下面整行发送给飞书机器人：").classes(
                        "text-weight-bold"
                    )
                    ui.code(message).classes("text-h6 w-full")
                    ui.button(
                        "复制绑定口令",
                        on_click=lambda text=message: ui.clipboard.write(text),
                    ).props("flat color=teal-9 no-caps icon=content_copy")
                    ui.label(
                        f'有效期至：{pairing.get("expires_at") or "30 分钟后"}'
                    ).classes("muted")
                    ui.label(
                        "私聊直接发送；群聊中请先 @机器人，再发送该口令。"
                    ).classes("text-warning")
                elif status.get("status") == "waiting":
                    ui.label(
                        "已有绑定口令正在等待使用。出于安全原因不会回显明文，"
                        "如已忘记请重新生成。"
                    ).classes("text-warning")
                elif status.get("status") == "used":
                    ui.label(
                        "绑定口令已使用，但本次成功回复尚未确认。请刷新状态并查看最近错误。"
                    ).classes("text-warning")
                elif status.get("status") == "expired":
                    ui.label("上一个绑定口令已过期，请重新生成。").classes(
                        "text-warning"
                    )
                else:
                    ui.label("尚未生成绑定口令。").classes("muted")

        pairing_card()

    with ui.expansion(
        "高级维护（一般不需要打开）",
        icon="settings",
    ).classes("card w-full"):
        ui.label(
            "以下选项供管理员维护已有配置。新用户请使用上面的安全口令绑定流程。"
        ).classes("muted")
        enabled_in = ui.switch(
            "启用飞书机器人",
            value=bool(saved.get("enabled", False)),
        )
        allow_all_in = ui.switch(
            "允许应用可用范围内的所有成员操作机器人",
            value=bool(saved.get("allow_all", False)),
        )
        ui.label(
            "高风险：开启后，任何能找到机器人的成员都可能生成内容或执行管理操作。"
        ).classes("text-negative text-weight-bold")
        allowed_open_ids_in = ui.textarea(
            "允许的用户 Open ID（高级）",
            value="\n".join(saved.get("allowed_open_ids") or []),
            placeholder="通常不需要手填；安全口令会自动绑定",
        ).classes("w-full").props("outlined rows=3 stack-label")
        allowed_chat_ids_in = ui.textarea(
            "允许的群聊 Chat ID（高级）",
            value="\n".join(saved.get("allowed_chat_ids") or []),
            placeholder="每行一个，例如 oc_xxxxx",
        ).classes("w-full").props("outlined rows=3 stack-label")
        with ui.expansion(
            "Verification Token / Encrypt Key",
            icon="verified_user",
        ).classes("w-full"):
            ui.label(
                "当前使用长连接，这两项不需要填写。它们只用于公网 Webhook；"
                "误填可能导致事件无法解析。"
            ).classes("text-warning")
            verification_in = secret_input(
                "Verification Token",
                bool(saved.get("has_verification_token")),
            )
            encrypt_key_in = secret_input(
                "Encrypt Key",
                bool(saved.get("has_encrypt_key")),
            )
            clear_event_security_in = ui.switch(
                "清除已保存的 Verification Token 和 Encrypt Key",
                value=False,
            )

        async def save_advanced() -> None:
            set_button_loading(
                advanced_save_btn,
                True,
                "正在保存飞书高级配置…",
            )
            try:
                if bool(enabled_in.value) and not str(
                    agent_model_in.value or ""
                ).strip():
                    raise ValueError("启用机器人前请选择飞书智能体模型")
                if bool(enabled_in.value) and not list(
                    default_accounts_in.value or []
                ):
                    raise ValueError("启用机器人前请至少选择一个默认公众号")
                await run.io_bound(
                    lambda: save_feishu_settings(
                        state.db,
                        enabled=bool(enabled_in.value),
                        app_id=str(app_id_in.value or ""),
                        app_secret=str(app_secret_in.value or "") or None,
                        verification_token=str(
                            verification_in.value or ""
                        )
                        or None,
                        encrypt_key=str(encrypt_key_in.value or "") or None,
                        clear_event_security=bool(
                            clear_event_security_in.value
                        ),
                        allow_all=bool(allow_all_in.value),
                        allowed_open_ids=_split_ids(
                            allowed_open_ids_in.value
                        ),
                        allowed_chat_ids=_split_ids(
                            allowed_chat_ids_in.value
                        ),
                        default_account_ids=list(
                            default_accounts_in.value or []
                        ),
                        agent_model_id=str(agent_model_in.value or ""),
                    )
                )
                app_secret_in.value = ""
                verification_in.value = ""
                encrypt_key_in.value = ""
                runtime_card.refresh()
                pairing_card.refresh()
                ui.notify(
                    "飞书高级配置已加密保存，重启应用后生效。",
                    type="positive",
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    _friendly_error(exc),
                    type="negative",
                    timeout=15000,
                )
            finally:
                set_button_loading(advanced_save_btn, False)

        advanced_save_btn = ui.button(
            "保存高级配置",
            on_click=save_advanced,
        ).props("outline color=teal-9 no-caps icon=save")


def _step_heading(number: int, title: str, description: str) -> None:
    with ui.row().classes("items-start no-wrap"):
        ui.badge(str(number)).props("rounded color=teal-8")
        with ui.column().classes("gap-0"):
            ui.label(title).classes("text-h6 text-weight-bold")
            ui.label(description).classes("muted")


def _split_ids(value: Any) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,，;；\s]+", str(value or ""))
        if item.strip()
    ]


def _runtime_item(label: str, value: Any) -> None:
    with ui.column().classes("gap-0"):
        ui.label(label).classes("muted")
        ui.label(str(value or "暂无记录")).classes("text-weight-medium")


def _friendly_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    lower = text.casefold()
    if "飞书凭证验证失败" in text:
        return text
    if any(marker in lower for marker in ("timeout", "timed out", "网络", "connection")):
        return "连接失败：无法访问服务商，请检查本机网络或代理后重试。"
    if any(marker in lower for marker in ("401", "unauthorized", "invalid api key", "鉴权")):
        return "验证失败：密钥无效，请重新复制完整密钥后重试。"
    if any(marker in lower for marker in ("402", "insufficient", "余额", "欠费")):
        return "验证失败：模型账户余额不足或尚未开通服务。"
    if any(marker in lower for marker in ("429", "rate limit", "限流")):
        return "验证失败：服务商正在限流，请稍后再试。"
    return text or "操作失败，请检查配置后重试。"


__all__ = ["PERMISSION_CODES", "build_feishu_panel"]
