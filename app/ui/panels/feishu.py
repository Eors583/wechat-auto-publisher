from __future__ import annotations

from typing import Any

from nicegui import run, ui

from app.accounts import public_accounts
from app.services.feishu_integrations import FeishuIntegrationService
from app.services.onboarding import OnboardingService
from app.ui.lifecycle import client_timer
from app.ui.state import AppState, set_button_loading


PERMISSION_CODES = (
    "im:message.p2p_msg:readonly,"
    "im:message:send_as_bot,"
    "im:resource"
)


def _feishu_account_catalog(
    db: Any,
) -> tuple[dict[str, str], list[str], list[str]]:
    options: dict[str, str] = {}
    disabled_names: list[str] = []
    unbound_names: list[str] = []
    for item in public_accounts(db):
        name = str(item.get("name") or item.get("id") or "未命名公众号")
        if not bool(item.get("enabled", True)):
            disabled_names.append(name)
            continue
        if item.get("has_model") is False:
            unbound_names.append(name)
            options[str(item["id"])] = f"{name} · 尚未绑定文章模型"
        else:
            options[str(item["id"])] = name
    return options, disabled_names, unbound_names


def build_feishu_panel(state: AppState) -> None:
    """Render the authenticated user's own isolated Feishu integration."""

    state.reload_config()
    integration_service = FeishuIntegrationService(state.db, state.config)
    onboarding = OnboardingService(state.db, state.config)
    saved = integration_service.public()
    account_options, disabled_accounts, unbound_accounts = _feishu_account_catalog(
        state.db
    )
    model_options = state.model_options(include_default=False)
    saved_model_id = str(saved.get("agent_model_id") or "")
    selected_accounts = [
        str(item)
        for item in saved.get("account_ids") or []
        if str(item) in account_options
    ]
    selected_default = str(saved.get("default_account_id") or "")
    page_state: dict[str, Any] = {"pairing": None}

    with ui.element("div").classes("card w-full feishu-hero"):
        with ui.row().classes("w-full items-center justify-between feishu-heading-row"):
            with ui.column().classes("gap-0 min-w-0"):
                ui.label("我的飞书机器人").classes("text-h5 text-weight-bold")
                ui.label(
                    "每个登录用户独立配置自己的飞书应用、公众号范围和绑定身份。"
                ).classes("muted")
            ui.badge("用户独立 · Webhook").props("color=primary")
        ui.label(
            "你的 App ID、密钥、会话、任务和草稿权限不会与其他系统用户共享。"
        ).classes("text-positive text-weight-bold")

    @ui.refreshable
    def status_card() -> None:
        current = integration_service.public()
        status = str(current.get("status") or "unconfigured")
        runtime = dict(current.get("runtime") or {})
        status_label = {
            "unconfigured": "尚未配置",
            "waiting_pairing": "等待绑定",
            "active": "运行正常",
            "disabled": "已停用",
            "error": "配置异常",
        }.get(status, status)
        color = {
            "active": "positive",
            "waiting_pairing": "warning",
            "error": "negative",
            "disabled": "grey",
        }.get(status, "grey")
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("接入状态").classes("text-h6 text-weight-bold")
                ui.badge(status_label, color=color)
            with ui.element("div").classes("feishu-status-grid"):
                _status_item("飞书凭证", "已保存" if current.get("configured") else "待配置")
                _status_item(
                    "回调验证",
                    "已收到回调"
                    if runtime.get("callback_verified_at")
                    else "等待飞书验证",
                )
                _status_item(
                    "一对一绑定",
                    str(current.get("bound_open_id_masked") or "未绑定"),
                )
                _status_item("最近消息", runtime.get("last_message_at") or "暂无记录")
            if runtime.get("last_error"):
                ui.label(str(runtime["last_error"])).classes(
                    "text-negative feishu-break-anywhere"
                )

    status_card()

    with ui.element("div").classes("card w-full"):
        _section_heading(
            "1. 配置你的飞书自建应用",
            "凭证只属于当前登录用户；留空的已保存密钥不会被覆盖。",
        )
        with ui.element("div").classes("feishu-config-grid"):
            app_id_input = ui.input(
                "App ID",
                value=str(saved.get("app_id") or ""),
                placeholder="cli_xxxxxxxxxx",
            ).classes("w-full").props("outlined stack-label")
            app_secret_input = _secret_input(
                "App Secret", bool(saved.get("has_app_secret"))
            )
            verification_input = _secret_input(
                "Verification Token", bool(saved.get("has_verification_token"))
            )
            encrypt_key_input = _secret_input(
                "Encrypt Key", bool(saved.get("has_encrypt_key"))
            )

        model_input = ui.select(
            options=model_options or {"": "请先在模型管理中添加文本模型"},
            value=saved_model_id if saved_model_id in model_options else None,
            label="机器人理解指令使用的文本模型",
        ).classes("w-full").props("outlined stack-label")
        state.register_model_select(
            model_input,
            purpose="text",
            default_label="请选择文本模型",
        )

        account_input = ui.select(
            options=account_options,
            value=selected_accounts,
            label="机器人允许操作的公众号",
            multiple=True,
        ).classes("w-full").props("outlined stack-label use-chips")
        default_account_input = ui.select(
            options={
                account_id: account_options[account_id]
                for account_id in selected_accounts
                if account_id in account_options
            },
            value=selected_default if selected_default in selected_accounts else None,
            label="唯一默认公众号",
        ).classes("w-full").props("outlined stack-label")

        def sync_default_options() -> None:
            selected = [
                str(item)
                for item in account_input.value or []
                if str(item) in account_options
            ]
            options = {item: account_options[item] for item in selected}
            current = str(default_account_input.value or "")
            default_account_input.set_options(
                options,
                value=current
                if current in options
                else (selected[0] if len(selected) == 1 else None),
            )

        account_input.on_value_change(lambda _event: sync_default_options())
        if not account_options:
            ui.label("尚无可用公众号，请先在公众号配置中添加并启用。 ").classes(
                "text-warning"
            )
        if unbound_accounts:
            ui.label(
                "以下公众号尚未绑定文章模型：" + "、".join(unbound_accounts)
            ).classes("text-warning")
        if disabled_accounts:
            ui.label(
                "已停用公众号不会进入机器人选择：" + "、".join(disabled_accounts)
            ).classes("muted")

        async def save_and_verify() -> None:
            set_button_loading(save_button, True, "正在验证并保存你的飞书机器人…")
            try:
                model_id = str(model_input.value or "").strip()
                account_ids = [str(item) for item in account_input.value or []]
                default_account_id = str(default_account_input.value or "").strip()
                if not model_id:
                    raise ValueError("请选择机器人使用的文本模型")
                await run.io_bound(lambda: onboarding.test_text_model(model_id))
                await run.io_bound(
                    lambda: integration_service.test_credentials(
                        app_id=str(app_id_input.value or ""),
                        app_secret=str(app_secret_input.value or "") or None,
                    )
                )
                result = await run.io_bound(
                    lambda: integration_service.save(
                        app_id=str(app_id_input.value or ""),
                        app_secret=str(app_secret_input.value or "") or None,
                        verification_token=str(verification_input.value or "") or None,
                        encrypt_key=str(encrypt_key_input.value or "") or None,
                        agent_model_id=model_id,
                        account_ids=account_ids,
                        default_account_id=default_account_id,
                        enabled=True,
                    )
                )
                for secret in (app_secret_input, verification_input, encrypt_key_input):
                    secret.value = ""
                callback_input.value = _callback_display(result)
                status_card.refresh()
                pairing_card.refresh()
                ui.notify("你的飞书机器人已验证并独立保存。", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(_friendly_error(exc), type="negative", timeout=15000)
            finally:
                set_button_loading(save_button, False)

        save_button = ui.button(
            "保存并验证我的机器人",
            on_click=save_and_verify,
        ).props("unelevated color=primary no-caps icon=verified_user")

    with ui.element("div").classes("card w-full"):
        _section_heading(
            "2. 配置专属 Webhook",
            "保存后为当前用户生成随机、不可枚举的专属回调地址。",
        )
        callback_input = ui.input(
            "飞书事件回调地址",
            value=_callback_display(saved),
        ).classes("w-full").props("outlined readonly stack-label")
        with ui.row().classes("feishu-actions"):
            ui.button(
                "复制回调地址",
                on_click=lambda: ui.clipboard.write(str(callback_input.value or "")),
            ).props("outline color=primary no-caps icon=content_copy")
            ui.button(
                "刷新接入状态",
                on_click=lambda: (status_card.refresh(), pairing_card.refresh()),
            ).props("outline color=primary no-caps icon=refresh")
        ui.markdown(
            """
在飞书开放平台进入 **事件与回调 → 事件配置**：

1. 选择“将事件发送至开发者服务器”。
2. 填写上面的专属 HTTPS 地址。
3. 添加 `im.message.receive_v1`。
4. 只开通单聊、机器人发消息和资源权限，不开放群聊操作。
            """
        ).classes("w-full feishu-break-anywhere")
        ui.code(PERMISSION_CODES).classes("w-full feishu-break-anywhere")

    with ui.element("div").classes("card w-full"):
        _section_heading(
            "3. 一对一绑定",
            "配对码 10 分钟内有效、只能使用一次，且只能在你的机器人私聊中使用。",
        )

        async def generate_pairing() -> None:
            set_button_loading(pairing_button, True, "正在生成专属配对码…")
            try:
                page_state["pairing"] = await run.io_bound(
                    integration_service.create_pairing_code
                )
                pairing_card.refresh()
            except Exception as exc:  # noqa: BLE001
                ui.notify(_friendly_error(exc), type="negative", timeout=15000)
            finally:
                set_button_loading(pairing_button, False)

        pairing_button = ui.button(
            "生成 10 分钟配对码",
            on_click=generate_pairing,
        ).props("unelevated color=primary no-caps icon=key")

        @ui.refreshable
        def pairing_card() -> None:
            current = integration_service.public()
            pairing = dict(current.get("pairing") or {})
            generated = page_state.get("pairing")
            with ui.element("div").classes("soft-panel w-full"):
                if current.get("bound"):
                    ui.label("已完成一对一绑定").classes(
                        "text-positive text-weight-bold"
                    )
                    ui.label(
                        "绑定身份：" + str(current.get("bound_open_id_masked") or "已绑定")
                    ).classes("muted")
                elif generated:
                    message = str(generated.get("message") or "")
                    ui.label("请私聊自己的飞书机器人发送：").classes("text-weight-bold")
                    ui.code(message).classes("text-h6 w-full")
                    ui.button(
                        "复制配对口令",
                        on_click=lambda value=message: ui.clipboard.write(value),
                    ).props("flat color=primary no-caps icon=content_copy")
                elif pairing.get("status") == "waiting":
                    ui.label("已有配对码等待使用；明文不会再次回显。 ").classes(
                        "text-warning"
                    )
                elif pairing.get("status") == "locked":
                    ui.label("错误次数已达上限，请重新生成配对码。 ").classes(
                        "text-negative"
                    )
                elif pairing.get("status") == "expired":
                    ui.label("上一个配对码已过期，请重新生成。 ").classes("text-warning")
                else:
                    ui.label("尚未生成配对码。 ").classes("muted")

        pairing_card()

        async def unbind() -> None:
            await run.io_bound(integration_service.unbind)
            page_state["pairing"] = None
            pairing_card.refresh()
            status_card.refresh()
            integration_actions.refresh()
            ui.notify("已解除当前用户的飞书身份绑定。", type="positive")

        async def set_integration_enabled(enabled: bool) -> None:
            try:
                await run.io_bound(
                    lambda: integration_service.set_enabled(enabled)
                )
                pairing_card.refresh()
                status_card.refresh()
                integration_actions.refresh()
                ui.notify(
                    "你的飞书机器人已启用。"
                    if enabled
                    else "你的飞书机器人已停用，其他用户不受影响。",
                    type="positive",
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(_friendly_error(exc), type="negative", timeout=15000)

        async def enable() -> None:
            await set_integration_enabled(True)

        async def disable() -> None:
            await set_integration_enabled(False)

        @ui.refreshable
        def integration_actions() -> None:
            current = integration_service.public()
            with ui.row().classes("feishu-actions"):
                if current.get("bound"):
                    ui.button("解除绑定", on_click=unbind).props(
                        "outline color=warning no-caps icon=link_off"
                    )
                if current.get("configured"):
                    if current.get("enabled"):
                        ui.button(
                            "停用我的机器人",
                            on_click=disable,
                        ).props(
                            "outline color=negative no-caps icon=power_settings_new"
                        )
                    else:
                        ui.button(
                            "启用我的机器人",
                            on_click=enable,
                        ).props(
                            "outline color=primary no-caps icon=play_circle"
                        )

        integration_actions()

    client_timer(5.0, lambda: status_card.refresh(), immediate=False)


def _secret_input(label: str, configured: bool) -> Any:
    suffix = "（已加密保存；留空保持不变）" if configured else ""
    return ui.input(
        label + suffix,
        password=True,
        password_toggle_button=True,
    ).classes("w-full").props("outlined stack-label autocomplete=new-password")


def _callback_display(settings: dict[str, Any]) -> str:
    path = str(settings.get("callback_url") or settings.get("callback_path") or "")
    if not path:
        return "保存后自动生成专属回调地址"
    return path if path.startswith("http") else "https://你的系统域名" + path


def _section_heading(title: str, description: str) -> None:
    with ui.column().classes("gap-0"):
        ui.label(title).classes("text-h6 text-weight-bold")
        ui.label(description).classes("muted")


def _status_item(label: str, value: Any) -> None:
    with ui.element("div").classes("feishu-status-item"):
        ui.label(label).classes("muted")
        ui.label(str(value or "暂无记录")).classes(
            "text-weight-medium feishu-break-anywhere"
        )


def _friendly_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    lower = text.casefold()
    if "飞书凭证验证失败" in text or "已被其他系统用户" in text:
        return text
    if any(marker in lower for marker in ("timeout", "timed out", "网络", "connection")):
        return "连接失败：无法访问飞书开放平台，请检查网络后重试。"
    return text or "操作失败，请检查配置后重试。"


__all__ = ["PERMISSION_CODES", "build_feishu_panel"]
