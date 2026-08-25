from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable

from nicegui import run, ui

from app.ai.image_providers import (
    IMAGE_ALIBABA,
    IMAGE_CUSTOM,
    IMAGE_MINIMAX,
    IMAGE_VOLCENGINE,
    IMAGE_ZHIPU,
    get_image_provider_preset,
    image_provider_label,
    image_provider_options,
    infer_image_provider,
    is_image_provider,
)
from app.ai.model_registry import (
    GEMINI,
    LOCAL_OPENAI_COMPATIBLE,
    MANUS,
    OPENAI_COMPATIBLE,
    token_metering_capability_label,
)
from app.db import Database
from app.services.configuration import ConfigurationService
from app.services.onboarding import OnboardingService, TEXT_MODEL_PRESETS
from app.services.onboarding_errors import friendly_model_error
from app.services.local_agents import LocalAgentService
from app.ui.state import AppState, set_button_loading
from app.ui.local_model_bridge import (
    _browser_health_click_handler,
    _browser_health_script,
    local_bridge_result_message,
)


LOCAL_BRIDGE_DOWNLOAD_URL = (
    "https://api.bluebloodlab.cn/downloads/"
    "BlueBloodLab-Cockpit-Bridge-1.4.2.exe"
)


IMAGE_PROVIDER_GUIDES: dict[str, dict[str, str]] = {
    IMAGE_ALIBABA: {
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
        "docs_url": "https://help.aliyun.com/zh/model-studio/get-api-key",
        "key_hint": "进入阿里云百炼控制台创建 API Key。请使用百炼 API Key，不要填写阿里云账号密码。",
        "fields": "只需选择图片模型并填写百炼 API Key；接口地址由系统固定。",
    },
    IMAGE_MINIMAX: {
        "key_url": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
        "docs_url": "https://platform.minimaxi.com/docs/api-reference/image-generation-t2i",
        "key_hint": "进入 MiniMax 开放平台的接口密钥页面创建 API Key。",
        "fields": "只需选择 image-01 系列模型并填写 MiniMax API Key；不需要填写 Group ID。",
    },
    IMAGE_VOLCENGINE: {
        "key_url": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        "docs_url": "https://www.volcengine.com/docs/82379/1541523",
        "key_hint": "进入火山方舟控制台创建 API Key，并确认账号已开通所选 Seedream 模型。",
        "fields": "只需选择 Seedream 模型并填写火山方舟 API Key；接口地址由系统固定。",
    },
    IMAGE_ZHIPU: {
        "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        "docs_url": "https://docs.bigmodel.cn/cn/guide/models/image/glm-image",
        "key_hint": "进入智谱开放平台的 API Key 管理页面创建密钥。",
        "fields": "只需选择 GLM-Image / CogView 模型并填写智谱 API Key；接口地址由系统固定。",
    },
    IMAGE_CUSTOM: {
        "key_url": "",
        "docs_url": "",
        "key_hint": "高级配置仅适用于服务商明确兼容 OpenAI Images API 的情况。",
        "fields": "需要自行填写完整 API Base URL、模型名称和 API Key。",
    },
}


def text_provider_options() -> dict[str, str]:
    """Return the beginner-facing text provider choices in stable order."""

    return {
        preset_id: str(preset["label"])
        for preset_id, preset in TEXT_MODEL_PRESETS.items()
    }


def infer_text_provider_preset(record: dict[str, Any] | None) -> str:
    """Map an editable model record back to a built-in provider preset."""

    if not record:
        return "deepseek"
    provider_type = str(record.get("provider_type") or "")
    if provider_type == GEMINI:
        return "gemini"
    if provider_type == MANUS:
        return "manus"
    if provider_type != OPENAI_COMPATIBLE:
        return "custom"

    configured_base = str(record.get("api_base") or "").strip().rstrip("/").casefold()
    configured_model = str(record.get("model") or "").strip()
    for preset_id, preset in TEXT_MODEL_PRESETS.items():
        if preset_id == "custom" or preset["provider_type"] != OPENAI_COMPATIBLE:
            continue
        preset_base = str(preset.get("api_base") or "").strip().rstrip("/").casefold()
        if configured_base and configured_base == preset_base:
            return preset_id
        if configured_model and configured_model in tuple(preset.get("models") or ()):
            return preset_id
    return "custom"


def build_models_panel(
    state: AppState,
    *,
    purpose: str = "text",
    db: Database | None = None,
    render_panel: bool = True,
    on_model_saved: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[[str | None], None]:
    """Manage model credentials and teach the complete setup flow in place."""

    state_config = dict(getattr(state, "config", {}) or {})
    loaded = state.reload_config() if render_panel else state_config
    config = loaded if isinstance(loaded, dict) else state_config
    model_db = db or state.db
    image_panel = purpose == "image"
    configuration = ConfigurationService(model_db, config)
    onboarding = OnboardingService(model_db, config)
    local_agents = LocalAgentService(model_db)
    text_presets = {
        str(item["id"]): item for item in onboarding.model_presets()
    }
    host = ui.column().classes("w-full") if render_panel else None

    def image_test_path(model_id: str) -> Path:
        return (
            Path(str(config.get("_root") or "."))
            / "data"
            / "model_tests"
            / f"{model_id}_test.jpg"
        )

    def render_official_help(
        container: Any,
        *,
        key_url: str,
        docs_url: str,
        key_hint: str,
        fields: str,
    ) -> None:
        container.clear()
        with container:
            ui.label(key_hint).classes("text-weight-medium")
            ui.label(fields).classes("muted")
            with ui.row().classes("items-center q-gutter-sm"):
                if key_url:
                    ui.link(
                        "打开官方 API Key 页面",
                        key_url,
                        new_tab=True,
                    ).classes("text-teal-9 text-weight-bold")
                if docs_url:
                    ui.link(
                        "查看官方接入说明",
                        docs_url,
                        new_tab=True,
                    ).classes("text-teal-8")
                if not key_url:
                    ui.label("请向接口服务商索取 API Key 和接入文档。").classes(
                        "text-warning"
                    )

    def show_image_preview(target: str | Path) -> None:
        path = Path(target)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        with ui.dialog() as preview_dialog, ui.card().classes("w-full").style(
            "max-width:820px"
        ):
            ui.label("图片模型接入完成").classes("text-h6 text-weight-bold")
            ui.label("已经使用刚保存的 API Key 真实生成并下载了测试图片。").classes(
                "text-positive"
            )
            ui.image(f"data:image/jpeg;base64,{encoded}").classes(
                "w-full rounded-borders"
            )
            ui.label(f"测试文件：{path}").classes("muted")
            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=preview_dialog.close).props(
                    "unelevated color=teal-9 no-caps"
                )
        preview_dialog.open()

    def open_editor(model_id: str | None = None) -> None:
        owner_client = ui.context.client
        current_model_id = model_id
        record = model_db.get_ai_model(model_id) if model_id else None
        initial_image_provider = (
            infer_image_provider(
                str((record or {}).get("provider_type") or ""),
                str((record or {}).get("api_base") or ""),
            )
            if image_panel and record
            else IMAGE_ALIBABA
        )
        initial_text_provider = (
            infer_text_provider_preset(record) if not image_panel else ""
        )
        initial_connection_type = (
            "local"
            if str((record or {}).get("provider_type") or "")
            == LOCAL_OPENAI_COMPATIBLE
            else "api"
        )

        with ui.dialog() as dialog, ui.card().classes(
            "ops-dialog-model-editor ops-dialog-scroll"
        ):
            ui.label(
                ("编辑图片模型" if record else "添加图片模型")
                if image_panel
                else ("编辑自定义模型" if record else "添加自定义模型")
            ).classes("text-h6 text-weight-bold")
            ui.label(
                "按顺序填写：选择厂商 → 打开官方页面获取 API Key → 保存并生成测试图。"
                if image_panel
                else "选择模型类型并填写连接信息，保存前建议先测试连接。"
            ).classes("muted")

            connection_in = None
            local_provider_in = None
            local_agent_in = None
            if not image_panel:
                ui.label("模型类型").classes("ops-config-field-label")
                connection_in = ui.toggle(
                    {"api": "API 模型", "local": "本地模型"},
                    value=initial_connection_type,
                ).classes("ops-model-kind-toggle w-full").props(
                    "spread no-caps unelevated"
                )
                local_provider_in = ui.select(
                    options={
                        "cockpit": "Cockpit Tools",
                        "ollama": "Ollama",
                        "lm_studio": "LM Studio",
                        "custom": "其他本地 OpenAI 兼容服务",
                    },
                    value=(
                        "cockpit"
                        if any(
                            port in str((record or {}).get("api_base") or "")
                            for port in (":11797", ":11798")
                        )
                        else "lm_studio"
                        if ":1234" in str((record or {}).get("api_base") or "")
                        else "cockpit"
                    ),
                    label="本地服务",
                ).classes("w-full").props("outlined stack-label")
                agent_options = {"": "浏览器临时桥接（兼容模式）"}
                for agent in model_db.list_local_model_agents():
                    if not str(agent.get("revoked_at") or "").strip():
                        agent_options[str(agent["id"])] = str(
                            agent.get("name") or "Windows 本机助手"
                        )
                local_agent_in = ui.select(
                    options=agent_options,
                    value=str((record or {}).get("local_agent_id") or ""),
                    label="执行设备（推荐 Companion）",
                ).classes("w-full").props("outlined stack-label")

            name_in = ui.input(
                "配置名称（可选）",
                value=str((record or {}).get("name") or ""),
                placeholder="留空则自动使用厂商和模型名称",
            ).classes("w-full").props("outlined stack-label")
            probe_mode_in = ui.input(value="skip").classes("hidden").props(
                "aria-hidden=true tabindex=-1"
            )

            if image_panel:
                type_in = ui.select(
                    options=image_provider_options(),
                    value=initial_image_provider,
                    label="第 1 步：选择生图厂商",
                ).classes("w-full").props("outlined stack-label")
            else:
                type_in = ui.select(
                    options=text_provider_options(),
                    value=initial_text_provider,
                    label="第 1 步：选择大模型厂商",
                ).classes("w-full").props("outlined stack-label")

            official_help = ui.column().classes(
                "w-full q-pa-md rounded-borders bg-blue-1 gap-1"
            )
            model_in = ui.input(
                "模型名称（可直接输入）",
                value=str((record or {}).get("model") or ""),
                placeholder=(
                    "请输入厂商接口文档中的准确图片模型名称"
                    if image_panel
                    else "请输入厂商接口文档中的准确文本模型名称"
                ),
            ).classes("w-full").props("outlined stack-label")
            model_note = ui.label("").classes("muted")
            base_in = ui.input(
                "API Base URL",
                value=str((record or {}).get("api_base") or ""),
                placeholder="https://api.example.com/v1",
            ).classes("w-full").props("outlined stack-label")
            endpoint_note = ui.label("").classes("muted")
            key_in = ui.input(
                "第 2 步：粘贴 API Key"
                + ("（留空表示继续使用已保存密钥）" if record else ""),
                password=True,
                password_toggle_button=True,
                placeholder="从上方官方页面复制，不要填写登录密码",
            ).classes("w-full").props(
                "outlined stack-label autocomplete=new-password"
            )
            with ui.column().classes(
                "w-full q-pa-md rounded-borders bg-teal-1 gap-2"
            ) as local_setup:
                ui.label("第 2 步：下载、配置并授权本机助手").classes(
                    "text-weight-bold text-teal-9"
                )
                ui.label(
                    "Cockpit API Key 只保存在这台电脑，不会上传到生产服务器。"
                ).classes("muted")
                ui.link(
                    "1. 直接下载本机桥接器（Windows EXE）",
                    LOCAL_BRIDGE_DOWNLOAD_URL,
                    new_tab=True,
                ).classes("text-teal-9 text-weight-bold ops-break-anywhere")
                ui.label(
                    "2. 双击下载的 EXE，并保持桥接器窗口打开。"
                ).classes("muted ops-break-anywhere")
                ui.link(
                    "3. 打开本机助手设置",
                    "http://127.0.0.1:11798/setup",
                    new_tab=True,
                ).classes("text-teal-9 text-weight-bold")
                local_status = ui.label(
                    "4. 填写 Cockpit 实际地址和 Key；回到这里保存并测试时，浏览器会请求一次本地网络权限。"
                ).classes("muted ops-break-anywhere")
            enabled_in = ui.switch(
                "启用",
                value=bool((record or {}).get("enabled", True)),
            )

            def sync_type(*, reset_model: bool = False) -> None:
                if image_panel:
                    key_in.set_visibility(True)
                    local_setup.set_visibility(False)
                    provider_id = str(type_in.value or IMAGE_CUSTOM)
                    preset = get_image_provider_preset(provider_id)
                    guide = IMAGE_PROVIDER_GUIDES[provider_id]
                    is_custom = provider_id == IMAGE_CUSTOM
                    base_in.set_visibility(is_custom)
                    endpoint_note.text = (
                        f"接口地址已由系统锁定：{preset.endpoint}"
                        if not is_custom
                        else "高级配置：请严格按服务商文档填写完整接口地址。"
                    )
                    key_in.props(f'placeholder="{preset.key_placeholder}"')
                    render_official_help(official_help, **guide)
                    if is_custom:
                        model_note.text = "请按服务商接口文档填写模型名称。"
                        if reset_model:
                            base_in.value = ""
                            model_in.value = ""
                        return
                    base_in.value = preset.endpoint
                    current = (
                        preset.default_model
                        if reset_model
                        else str(
                            model_in.value
                            or (record or {}).get("model")
                            or preset.default_model
                        )
                    )
                    model_in.value = current
                    model_note.text = (
                        "厂商常用模型："
                        + "、".join(str(item) for item in preset.models)
                        + "。也可以输入厂商新发布的模型名称。"
                    )
                    model_in.update()
                    return

                if str(connection_in.value or "api") == "local":
                    is_cockpit = (
                        str(local_provider_in.value or "") == "cockpit"
                    )
                    type_in.set_visibility(False)
                    local_provider_in.set_visibility(True)
                    local_agent_in.set_visibility(is_cockpit)
                    if not is_cockpit and str(local_agent_in.value or ""):
                        local_agent_in.value = ""
                        local_agent_in.update()
                    base_in.set_visibility(True)
                    official_help.clear()
                    with official_help:
                        ui.label(
                            "请求会由当前登录用户打开的网页转发到自己电脑上的本地模型。"
                        ).classes("text-weight-medium")
                        ui.label(
                            "生成期间请保持 HTTPS 页面打开；本地服务必须允许当前网页跨域访问。"
                        ).classes("muted")
                        ui.label(
                            "Cockpit Tools 经本地桥接器使用 11798；Cockpit 自己的地址和端口在本机助手设置页填写。"
                        ).classes("muted")
                        ui.label(
                            "绑定 Companion 后网页可以关闭；未绑定时才使用当前浏览器临时转发。"
                        ).classes("muted")
                        ui.label(
                            "Ollama 常用 11434，LM Studio 常用 1234；不同服务的地址和密钥不能混用。"
                        ).classes("muted")
                    endpoint_note.text = (
                        "这里只允许带端口的 localhost、127.0.0.1 或 ::1 地址；服务器不会直接访问该地址。"
                    )
                    model_note.text = "填写本地服务中已经下载并启用的准确模型名称。"
                    key_in.set_visibility(False)
                    local_setup.set_visibility(is_cockpit)
                    if reset_model:
                        local_defaults = {
                            "cockpit": ("http://127.0.0.1:11798/v1", ""),
                            "ollama": ("http://localhost:11434/v1", "qwen2.5:7b"),
                            "lm_studio": ("http://localhost:1234/v1", ""),
                            "custom": ("", ""),
                        }
                        base_in.value, model_in.value = local_defaults[
                            str(local_provider_in.value or "cockpit")
                        ]
                        base_in.update()
                        model_in.update()
                    return

                type_in.set_visibility(True)
                local_provider_in.set_visibility(False)
                local_agent_in.set_visibility(False)
                local_setup.set_visibility(False)
                key_in.set_visibility(True)
                key_in.props(
                    'label="第 2 步：粘贴 API Key" '
                    'placeholder="从上方官方页面复制，不要填写登录密码"'
                )

                preset_id = str(type_in.value or "custom")
                preset = text_presets[preset_id]
                is_custom = preset_id == "custom"
                base_in.set_visibility(is_custom)
                endpoint = str(preset.get("api_base") or "")
                endpoint_note.text = (
                    f"接口地址已由系统锁定：{endpoint}"
                    if endpoint
                    else (
                        "该厂商由官方 SDK 连接，无需填写接口地址。"
                        if not is_custom
                        else "高级配置：仅用于兼容 OpenAI Chat Completions 的接口。"
                    )
                )
                render_official_help(
                    official_help,
                    key_url=str(preset.get("key_url") or ""),
                    docs_url=str(preset.get("docs_url") or ""),
                    key_hint=str(preset.get("key_hint") or ""),
                    fields=(
                        "只需输入模型名称并填写 API Key；接口地址由系统管理。"
                        if not is_custom
                        else "需要自行填写 API Base URL、模型名称和 API Key。"
                    ),
                )
                if is_custom:
                    model_note.text = "请按服务商接口文档填写模型名称。"
                    if reset_model:
                        base_in.value = ""
                        model_in.value = ""
                    return
                base_in.value = endpoint
                recommended = tuple(preset.get("models") or ())
                current = (
                    str(preset.get("default_model") or "")
                    if reset_model
                    else str(
                        model_in.value
                        or (record or {}).get("model")
                        or preset.get("default_model")
                        or ""
                    )
                )
                model_in.value = current
                model_note.text = (
                    "厂商常用模型："
                    + "、".join(str(item) for item in recommended)
                    + "。也可以输入厂商新发布的模型名称。"
                )
                model_in.update()

            def sync_probe_mode() -> None:
                probe_mode_in.value = (
                    "cockpit_bridge"
                    if not image_panel
                    and str(connection_in.value or "api") == "local"
                    and str(local_provider_in.value or "") == "cockpit"
                    and not str(local_agent_in.value or "").strip()
                    else "skip"
                )
                probe_mode_in.update()

            type_in.on_value_change(lambda _: sync_type(reset_model=True))
            if connection_in is not None:
                connection_in.on_value_change(
                    lambda _: (sync_type(reset_model=True), sync_probe_mode())
                )
                local_provider_in.on_value_change(
                    lambda _: (sync_type(reset_model=True), sync_probe_mode())
                )
                local_agent_in.on_value_change(lambda _: sync_probe_mode())
            sync_type()
            sync_probe_mode()

            def persist_form() -> dict[str, Any]:
                nonlocal current_model_id
                selected_provider = str(type_in.value or "")
                if image_panel:
                    image_preset = get_image_provider_preset(selected_provider)
                    provider_type = selected_provider
                    is_custom = selected_provider == IMAGE_CUSTOM
                    selected_base = (
                        str(base_in.value or "")
                        if is_custom
                        else image_preset.endpoint
                    )
                    selected_model = str(model_in.value or "")
                    provider_label = image_preset.label
                elif str(connection_in.value or "api") == "local":
                    provider_type = LOCAL_OPENAI_COMPATIBLE
                    selected_base = str(base_in.value or "")
                    if str(local_provider_in.value or "") == "cockpit":
                        selected_base = selected_base.replace(
                            "://localhost:", "://127.0.0.1:", 1
                        ).replace(":11797", ":11798", 1)
                    selected_model = str(model_in.value or "")
                    provider_label = "本地模型"
                else:
                    text_preset = text_presets[selected_provider]
                    provider_type = str(text_preset["provider_type"])
                    is_custom = selected_provider == "custom"
                    selected_base = (
                        str(base_in.value or "")
                        if is_custom
                        else str(text_preset.get("api_base") or "")
                    )
                    selected_model = str(model_in.value or "")
                    provider_label = str(text_preset["label"]).split("（", 1)[0]

                display_name = str(name_in.value or "").strip()
                if not display_name:
                    display_name = f"{provider_label} · {selected_model}"
                saved = configuration.save_model(
                    model_id=current_model_id,
                    name=display_name,
                    provider_type=provider_type,
                    api_base=selected_base,
                    model=selected_model,
                    api_key=(
                        None
                        if provider_type == LOCAL_OPENAI_COMPATIBLE
                        else str(key_in.value or "") or None
                    ),
                    local_agent_id=(
                        str(local_agent_in.value or "")
                        if provider_type == LOCAL_OPENAI_COMPATIBLE
                        else None
                    ),
                    enabled=bool(enabled_in.value),
                )
                current_model_id = str(saved["id"])
                if image_panel:
                    # Any edit may change the endpoint, model or credential.
                    # A test image from the previous configuration must not
                    # continue to advertise the new configuration as usable.
                    image_test_path(current_model_id).unlink(
                        missing_ok=True
                    )
                state.refresh_model_selects()
                if on_model_saved is not None:
                    on_model_saved(saved)
                return saved

            async def submit(*, test_after_save: bool, button: Any) -> None:
                set_button_loading(
                    button,
                    True,
                    (
                        "正在保存并真实生成测试图片，请稍候…"
                        if image_panel and test_after_save
                        else (
                            "正在保存并验证 API Key，请稍候…"
                            if test_after_save
                            else "正在保存模型配置…"
                        )
                    ),
                )
                try:
                    saved = persist_form()
                    ui.notify(
                        "模型已同步到所有模型选择器",
                        type="positive",
                        timeout=3500,
                    )
                    if not test_after_save:
                        ui.notify(
                            (
                                "图片模型已保存，但还未验证。请继续点击“生成测试图”。"
                                if image_panel
                                else "自定义模型已保存，但还未验证。请继续点击“测试连接”。"
                            ),
                            type="warning",
                            timeout=7000,
                        )
                        dialog.close()
                        render_models()
                        return

                    if image_panel:
                        result = await run.io_bound(
                            lambda: configuration.generate_model_test_image(
                                str(saved["id"])
                            )
                        )
                        if bool(getattr(owner_client, "is_deleted", False)):
                            return
                        dialog.close()
                        ui.notify("真实测试图生成成功，图片模型接入完成", type="positive")
                        render_models()
                        with owner_client:
                            show_image_preview(str(result["path"]))
                    else:
                        result = await run.io_bound(
                            lambda: onboarding.test_text_model(str(saved["id"]))
                        )
                        if bool(getattr(owner_client, "is_deleted", False)):
                            return
                        dialog.close()
                        ui.notify(
                            str(result.get("message") or "连接成功")
                            + "，自定义模型接入完成",
                            type="positive",
                        )
                        render_models()
                except Exception as exc:  # noqa: BLE001
                    if not bool(getattr(owner_client, "is_deleted", False)):
                        try:
                            with owner_client:
                                ui.notify(
                                    friendly_model_error(
                                        exc,
                                        image=image_panel,
                                    ),
                                    type="negative",
                                    timeout=12000,
                                )
                        except RuntimeError:
                            # The page or dialog can be gone after a slow
                            # provider probe. Never update a deleted UI slot.
                            pass
                finally:
                    set_button_loading(button, False)

            with ui.row().classes("w-full justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                async def save_from_click() -> None:
                    await submit(
                        test_after_save=False,
                        button=save_btn,
                    )

                async def verify_from_click(event: Any) -> None:
                    result = event.args if hasattr(event, "args") else {}
                    if not isinstance(result, dict) or not bool(result.get("ok")):
                        message = local_bridge_result_message(result)
                        local_status.text = message
                        local_status.classes(
                            remove="text-positive muted",
                            add="text-negative text-weight-medium",
                        )
                        local_status.update()
                        ui.notify(message, type="negative", timeout=12000)
                        return
                    if str(result.get("kind") or "") == "ready":
                        if current_model_id:
                            await run.io_bound(
                                lambda: model_db.clear_local_model_credential(
                                    current_model_id
                                )
                            )
                        local_status.text = (
                            "本机助手、Cockpit Tools 和浏览器权限均已就绪，"
                            "正在进行真实模型调用…"
                        )
                        local_status.classes(
                            remove="text-negative muted",
                            add="text-positive text-weight-medium",
                        )
                        local_status.update()
                    await submit(test_after_save=True, button=verify_btn)

                save_btn = ui.button(
                    "仅保存",
                    on_click=save_from_click,
                ).props("outline color=teal-9 no-caps")
                verify_btn = ui.button(
                    "保存并生成测试图"
                    if image_panel
                    else "保存并测试连接",
                    icon="verified",
                ).on(
                    "click",
                    verify_from_click,
                    js_handler=_browser_health_click_handler(
                        base_element_id=int(base_in.id),
                        probe_mode_element_id=int(probe_mode_in.id),
                    ),
                ).props(
                    "unelevated color=teal-9 no-caps"
                )
        dialog.open()

    async def run_connection_test(model_id: str, button: Any) -> None:
        owner_client = ui.context.client
        set_button_loading(button, True, "正在验证模型接口和 API Key…")
        try:
            model = model_db.get_ai_model(model_id)
            browser_cockpit_model = bool(
                model
                and str(model.get("provider_type") or "")
                == LOCAL_OPENAI_COMPATIBLE
                and ":11798" in str(model.get("api_base") or "")
                and not str(model.get("local_agent_id") or "").strip()
            )
            if browser_cockpit_model:
                bridge_result = await owner_client.run_javascript(
                    _browser_health_script(
                        api_base=str(model.get("api_base") or "")
                    ),
                    timeout=12,
                )
                if not isinstance(bridge_result, dict) or not bool(
                    bridge_result.get("ok")
                ):
                    raise RuntimeError(
                        local_bridge_result_message(bridge_result)
                    )
            if image_panel:
                result = await run.io_bound(
                    lambda: configuration.test_model(model_id)
                )
            else:
                result = await run.io_bound(
                    lambda: onboarding.test_text_model(model_id)
                )
            if browser_cockpit_model:
                await run.io_bound(
                    lambda: model_db.clear_local_model_credential(model_id)
                )
            state.refresh_model_selects()
            ui.notify(str(result.get("message") or "连接成功"), type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(
                friendly_model_error(exc, image=image_panel),
                type="negative",
                timeout=10000,
            )
        finally:
            set_button_loading(button, False)

    async def run_generation_test(model_id: str, button: Any) -> None:
        set_button_loading(
            button,
            True,
            "正在真实调用图片模型生成测试图，通常需要几十秒…",
        )
        try:
            result = await run.io_bound(
                lambda: configuration.generate_model_test_image(model_id)
            )
            state.refresh_model_selects()
            render_models()
            show_image_preview(str(result["path"]))
            ui.notify("真实测试图生成成功，图片模型接入完成", type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(
                friendly_model_error(exc, image=True),
                type="negative",
                timeout=15000,
            )
        finally:
            set_button_loading(button, False)

    def confirm_delete(model_id: str, name: str) -> None:
        with ui.dialog() as confirm, ui.card():
            ui.label(f"确定删除“{name}”吗？").classes("text-weight-medium")

            def remove() -> None:
                try:
                    configuration.delete_model(model_id)
                    confirm.close()
                    render_models()
                    state.refresh_model_selects()
                    ui.notify("模型已同步到所有模型选择器", type="positive")
                    ui.notify("模型配置已删除", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="warning")

            with ui.row().classes("justify-end w-full"):
                ui.button("取消", on_click=confirm.close).props("flat no-caps")
                ui.button("删除", on_click=remove).props(
                    "unelevated color=red-7 no-caps"
                )
        confirm.open()

    def set_enabled(model_id: str, enabled: bool) -> None:
        configuration.set_model_enabled(model_id, enabled)
        state.refresh_model_selects()
        ui.notify(
            "模型启用状态已同步到选择器",
            type="positive",
            timeout=1800,
        )

    def rename_local_agent(agent_id: str, current_name: str) -> None:
        with ui.dialog() as rename_dialog, ui.card().classes(
            "ops-dialog-model-editor"
        ):
            ui.label("重命名本机 Companion").classes("text-h6 text-weight-bold")
            name_in = ui.input("设备名称", value=current_name).classes(
                "w-full"
            ).props("outlined stack-label maxlength=100")

            def save_name() -> None:
                try:
                    local_agents.rename_agent(
                        str(model_db.owner_user_id or ""),
                        agent_id,
                        str(name_in.value or ""),
                    )
                    rename_dialog.close()
                    render_models()
                    ui.notify("设备名称已更新", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=rename_dialog.close).props("flat no-caps")
                ui.button("保存", on_click=save_name).props(
                    "unelevated color=teal-9 no-caps"
                )
        rename_dialog.open()

    def revoke_local_agent(agent_id: str, name: str) -> None:
        with ui.dialog() as revoke_dialog, ui.card().classes(
            "ops-dialog-model-editor"
        ):
            ui.label(f"撤销设备“{name}”？").classes("text-h6 text-weight-bold")
            ui.label(
                "撤销后该设备立即失去任务权限，已绑定模型会回到等待绑定状态。"
            ).classes("muted ops-break-anywhere")

            def revoke() -> None:
                try:
                    local_agents.revoke_agent(
                        str(model_db.owner_user_id or ""),
                        agent_id,
                    )
                    revoke_dialog.close()
                    render_models()
                    ui.notify("本机设备已撤销", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=revoke_dialog.close).props("flat no-caps")
                ui.button("确认撤销", on_click=revoke).props(
                    "unelevated color=red-7 no-caps"
                )
        revoke_dialog.open()

    def render_models() -> None:
        if host is None:
            return
        host.clear()
        listed = configuration.list_models(
            purpose="image" if image_panel else "text",
            include_config=False,
        )
        models = [
            item for item in listed if not bool(item.get("is_config_model"))
        ]
        config_items = [
            item for item in listed if bool(item.get("is_config_model"))
        ]
        with host:
            if not image_panel:
                agents = local_agents.list_agents(
                    str(model_db.owner_user_id or "")
                )
                active_agents = [item for item in agents if not item.get("revoked")]
                with ui.element("div").classes("card w-full"):
                    ui.label("本机 Companion").classes("text-h6 text-weight-bold")
                    ui.label(
                        "Companion 主动连接生产 HTTPS；绑定后关闭网页仍可运行本地模型。"
                    ).classes("muted ops-break-anywhere")
                    if not active_agents:
                        ui.label(
                            "尚未配对。请在 Windows 本机助手设置页生成配对码并在本页批准。"
                        ).classes("text-warning ops-break-anywhere")
                    for agent in active_agents:
                        with ui.row().classes(
                            "w-full items-center justify-between q-mt-sm ops-wrap-actions"
                        ):
                            with ui.column().classes("gap-0").style(
                                "min-width:0;flex:1"
                            ):
                                ui.label(str(agent["name"])).classes(
                                    "text-weight-bold ops-break-anywhere"
                                )
                                ui.label(
                                    (
                                        "在线"
                                        if agent.get("online")
                                        else "离线"
                                    )
                                    + " · Cockpit "
                                    + str(agent.get("cockpit_status") or "unknown")
                                ).classes(
                                    "text-positive"
                                    if agent.get("online")
                                    else "muted"
                                )
                            with ui.row().classes("items-center q-gutter-xs"):
                                ui.button(
                                    "重命名",
                                    on_click=lambda _=None, aid=str(agent["id"]), n=str(agent["name"]): rename_local_agent(aid, n),
                                ).props("flat dense no-caps")
                                ui.button(
                                    "撤销",
                                    on_click=lambda _=None, aid=str(agent["id"]), n=str(agent["name"]): revoke_local_agent(aid, n),
                                ).props("flat dense color=red-7 no-caps")
            with ui.element("div").classes("card w-full"):
                ui.label(
                    "图片模型接入：照着 3 步做"
                    if image_panel
                    else "大模型接入：照着 3 步做"
                ).classes("text-h6 text-weight-bold")
                with ui.element("div").classes("w-full q-mt-md").style(
                    "display:grid;"
                    "grid-template-columns:repeat(auto-fit,minmax(220px,1fr));"
                    "gap:12px;"
                    "align-items:stretch"
                ):
                    steps = (
                        (
                            "1. 选择厂商",
                            "选择阿里百炼、MiniMax、火山方舟或智谱；接口地址会自动填写。",
                        ),
                        (
                            "2. 获取并粘贴 Key",
                            "在表单中点官方入口创建 API Key，然后完整复制到输入框。",
                        ),
                        (
                            "3. 真实生成测试图",
                            "保存不等于可用；必须看到测试图片，才说明密钥、余额和模型都正常。",
                        ),
                    ) if image_panel else (
                        (
                            "1. 选择厂商",
                            "选择 DeepSeek、通义、Kimi、智谱、Gemini 或 Manus。",
                        ),
                        (
                            "2. 获取并粘贴 Key",
                            "点表单里的官方入口创建 API Key；接口地址和常用模型名称会自动填写，也可手动修改。",
                        ),
                        (
                            "3. 测试并绑定公众号",
                            "测试连接成功后，到左侧“公众号”把模型绑定给对应公众号。",
                        ),
                    )
                    for title, description in steps:
                        with ui.column().classes(
                            "w-full q-pa-md rounded-borders bg-grey-1 gap-1"
                        ):
                            ui.label(title).classes(
                                "text-weight-bold text-teal-9"
                            )
                            ui.label(description).classes("muted")
                ui.label(
                    "远程 API Key 会在服务器加密保存；本地模型密钥只保存在本机助手。"
                ).classes("text-positive q-mt-sm")

            with ui.element("div").classes("card w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label(
                            "已添加的图片模型"
                            if image_panel
                            else "已添加的文本模型"
                        ).classes("text-h6 text-weight-bold")
                        ui.label(
                            "每个公众号可以选择自己的图片模型。"
                            if image_panel
                            else "每个公众号可以一对一绑定不同文本模型。"
                        ).classes("muted")
                    ui.button(
                        "添加图片模型" if image_panel else "添加自定义模型",
                        on_click=lambda: open_editor(),
                    ).props("unelevated color=teal-9 no-caps icon=add")

            for item in config_items:
                config_model_id = str(item["id"])
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0").style(
                            "min-width:0;flex:1"
                        ):
                            title = str(item["name"])
                            if item.get("is_default"):
                                title += "（当前默认主模型）"
                            ui.label(title).classes("text-weight-bold")
                            ui.label(
                                f'{item["model"]} · 系统默认配置（只读）'
                            ).classes("muted")
                            if item.get("api_base"):
                                ui.label(str(item["api_base"])).classes("muted")
                            ui.label(
                                "API Key：••••••••（环境变量已配置）"
                            ).classes("muted")
                            ui.label(
                                "Token 计量："
                                + token_metering_capability_label(
                                    str(item.get("token_metering_capability") or "")
                                )
                            ).classes(
                                "text-positive"
                                if item.get("strict_token_eligible")
                                else "text-warning"
                            )
                        ui.label("只读配置").classes(
                            "text-positive text-weight-medium"
                        )
                    with ui.row().classes("q-mt-sm items-center"):
                        test_btn = ui.button("测试连接").props(
                            "outline dense color=teal-9 no-caps icon=wifi_tethering"
                        )
                        test_btn.on_click(
                            lambda _=None, mid=config_model_id, btn=test_btn: run_connection_test(
                                mid,
                                btn,
                            )
                        )
                        ui.label(
                            "可直接绑定给公众号；修改密钥请更新 .env，或另行添加一个可编辑模型。"
                        ).classes("muted")

            if not models and not config_items:
                with ui.element("div").classes("card w-full"):
                    ui.label(
                        "还没有图片模型" if image_panel else "还没有文本模型"
                    ).classes("text-weight-medium")
                    ui.label(
                        "点击上方“添加图片模型”，厂商、接口和常用模型名称都已替你准备好，模型名称也可直接输入。"
                        if image_panel
                        else "点击上方“添加自定义模型”，可选择本地模型或 API 模型。"
                    ).classes("muted")
                return

            for item in models:
                editable_model_id = str(item["id"])
                is_editable = bool(item.get("editable"))
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0").style(
                            "min-width:0;flex:1"
                        ):
                            ui.label(str(item["name"])).classes("text-weight-bold")
                            kind = (
                                image_provider_label(
                                    str(item.get("provider_type") or ""),
                                    str(item.get("api_base") or ""),
                                )
                                if is_image_provider(item.get("provider_type"))
                                else (
                                    "本地模型"
                                    if item["provider_type"]
                                    == LOCAL_OPENAI_COMPATIBLE
                                    else (
                                    "Gemini"
                                    if item["provider_type"] == GEMINI
                                    else (
                                        "Manus"
                                        if item["provider_type"] == MANUS
                                        else "OpenAI 兼容"
                                    )
                                    )
                                )
                            )
                            ui.label(f'{kind} · {item["model"]}').classes("muted")
                            if item.get("api_base"):
                                ui.label(str(item["api_base"])).classes("muted")
                            ui.label(
                                "API Key：仅保存在本机助手"
                                if item.get("connection_type") == "local"
                                else (
                                    "API Key：••••••••（已加密保存）"
                                    if item.get("has_api_key")
                                    else "API Key：尚未配置"
                                )
                            ).classes("muted")
                            if not image_panel:
                                ui.label(
                                    "Token 计量："
                                    + token_metering_capability_label(
                                        str(
                                            item.get("token_metering_capability")
                                            or ""
                                        )
                                    )
                                ).classes(
                                    "text-positive"
                                    if item.get("strict_token_eligible")
                                    else "text-warning"
                                )
                            if item.get("connection_type") == "local":
                                agent_id = str(item.get("local_agent_id") or "")
                                agent = (
                                    model_db.get_local_model_agent(agent_id)
                                    if agent_id
                                    else None
                                )
                                ui.label(
                                    (
                                        "由 Companion “"
                                        + str((agent or {}).get("name") or agent_id)
                                        + "”在后台执行；网页可以关闭"
                                    )
                                    if agent_id
                                    else "通过当前浏览器临时连接本机；生成期间请保持网页打开"
                                ).classes(
                                    "text-positive" if agent_id else "text-warning"
                                )
                            if image_panel:
                                tested_path = image_test_path(
                                    editable_model_id
                                )
                                ui.label(
                                    "已真实生成过测试图"
                                    if tested_path.exists()
                                    else "尚未真实生成测试图，保存不代表可用"
                                ).classes(
                                    "text-positive"
                                    if tested_path.exists()
                                    else "text-warning"
                                )
                        if is_editable:
                            enabled_switch = ui.switch(
                                "启用",
                                value=bool(item["enabled"]),
                            )
                            enabled_switch.on_value_change(
                                lambda event, mid=editable_model_id: set_enabled(
                                    mid,
                                    bool(event.value),
                                )
                            )
                        else:
                            ui.badge("平台公共模型").props(
                                "outline color=primary"
                            )
                    with ui.row().classes("q-mt-sm"):
                        if is_editable:
                            test_btn = ui.button(
                                "检查配置" if image_panel else "测试连接"
                            ).props("outline dense color=teal-9 no-caps")
                            test_btn.on_click(
                                lambda _=None, mid=editable_model_id, btn=test_btn: run_connection_test(
                                    mid,
                                    btn,
                                )
                            )
                            if image_panel:
                                generate_btn = ui.button(
                                    "生成测试图",
                                    icon="auto_awesome",
                                ).props(
                                    "unelevated dense color=indigo-7 no-caps"
                                )
                                generate_btn.on_click(
                                    lambda _=None, mid=editable_model_id, btn=generate_btn: run_generation_test(
                                        mid,
                                        btn,
                                    )
                                )
                            ui.button(
                                "编辑",
                                on_click=lambda mid=editable_model_id: open_editor(mid),
                            ).props("flat dense color=teal-9 no-caps")
                            ui.button(
                                "删除",
                                on_click=lambda mid=editable_model_id, name=str(
                                    item["name"]
                                ): confirm_delete(mid, name),
                            ).props("flat dense color=red-7 no-caps")
                        else:
                            ui.label(
                                "由平台管理员维护，可直接绑定到你的公众号。"
                            ).classes("muted")

    render_models()
    return open_editor


__all__ = [
    "IMAGE_PROVIDER_GUIDES",
    "build_models_panel",
    "friendly_model_error",
    "infer_text_provider_preset",
    "text_provider_options",
]
