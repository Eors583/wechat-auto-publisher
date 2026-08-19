from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from app.ai.image_providers import (
    IMAGE_ALIBABA,
    IMAGE_CUSTOM,
    IMAGE_MINIMAX,
    IMAGE_VOLCENGINE,
    IMAGE_ZHIPU,
)
from app.ai.model_registry import GEMINI, MANUS, OPENAI_COMPATIBLE, save_model
from app.db import Database
from app.ui.panels.models import (
    IMAGE_PROVIDER_GUIDES,
    build_models_panel,
    friendly_model_error,
    infer_text_provider_preset,
    text_provider_options,
)


class _PanelState:
    def __init__(self, root: Path) -> None:
        self.config = {
            "_root": str(root),
            "_db_path": str(root / "panel.db"),
            "ai": {},
        }
        self.db = Database(root / "panel.db")
        self.reload_count = 0
        self.model_refresh_count = 0

    def reload_config(self) -> dict[str, Any]:
        self.reload_count += 1
        return self.config

    def refresh_model_selects(self) -> None:
        self.model_refresh_count += 1


def _rendered_texts() -> list[str]:
    return [
        str(text)
        for element in ui.context.client.elements.values()
        if (text := getattr(element, "text", None))
    ]


def _click_button(label: str) -> None:
    button = next(
        element
        for element in ui.context.client.elements.values()
        if type(element).__name__ == "Button"
        and getattr(element, "text", None) == label
    )
    listener = next(
        item
        for item in button._event_listeners.values()
        if item.type == "click"
    )
    listener.handler(None)


def test_text_provider_options_include_guided_vendors_and_advanced_custom() -> None:
    options = text_provider_options()

    assert list(options) == [
        "deepseek",
        "qwen",
        "moonshot",
        "zhipu",
        "gemini",
        "manus",
        "custom",
    ]
    assert "DeepSeek" in options["deepseek"]
    assert "通义千问" in options["qwen"]
    assert "Kimi" in options["moonshot"]
    assert "智谱" in options["zhipu"]
    assert "高级" in options["custom"]


def test_existing_text_models_are_mapped_back_to_the_correct_vendor_preset() -> None:
    assert infer_text_provider_preset(None) == "deepseek"
    assert (
        infer_text_provider_preset(
            {
                "provider_type": OPENAI_COMPATIBLE,
                "api_base": "https://api.deepseek.com/",
                "model": "deepseek-v4-flash",
            }
        )
        == "deepseek"
    )
    assert (
        infer_text_provider_preset(
            {
                "provider_type": OPENAI_COMPATIBLE,
                "api_base": "https://workspace.example.test/compatible-mode/v1",
                "model": "qwen-plus",
            }
        )
        == "qwen"
    )
    assert (
        infer_text_provider_preset(
            {"provider_type": GEMINI, "model": "gemini-2.5-flash"}
        )
        == "gemini"
    )
    assert (
        infer_text_provider_preset(
            {"provider_type": MANUS, "model": "manus-1.6"}
        )
        == "manus"
    )
    assert (
        infer_text_provider_preset(
            {
                "provider_type": OPENAI_COMPATIBLE,
                "api_base": "https://company.example.test/v1",
                "model": "company-chat",
            }
        )
        == "custom"
    )


def test_every_image_provider_has_in_place_key_and_field_guidance() -> None:
    assert set(IMAGE_PROVIDER_GUIDES) == {
        IMAGE_ALIBABA,
        IMAGE_MINIMAX,
        IMAGE_VOLCENGINE,
        IMAGE_ZHIPU,
        IMAGE_CUSTOM,
    }
    for provider_id, guide in IMAGE_PROVIDER_GUIDES.items():
        assert guide["key_hint"]
        assert guide["fields"]
        if provider_id == IMAGE_CUSTOM:
            assert guide["key_url"] == ""
            assert "API Base URL" in guide["fields"]
        else:
            assert guide["key_url"].startswith("https://")
            assert guide["docs_url"].startswith("https://")
            assert "API Key" in guide["fields"]


def test_model_errors_are_chinese_and_possible_keys_are_redacted() -> None:
    assert (
        friendly_model_error(RuntimeError("HTTP 401 invalid api key sk-secret123"))
        == "文本模型的 API Key 无效或已失效，请从厂商控制台重新复制后再试。"
    )
    assert "余额" in friendly_model_error(
        RuntimeError("insufficient balance"),
        image=True,
    )
    assert "请求过于频繁" in friendly_model_error(
        RuntimeError("HTTP 429 rate limit exceeded")
    )
    assert "连接超时" in friendly_model_error(RuntimeError("request timeout"))

    unknown = friendly_model_error(
        RuntimeError("provider rejected sk-supersecret123 for an unknown reason")
    )
    assert "sk-supersecret123" not in unknown
    assert "••••••••" in unknown


def test_friendly_model_error_keeps_local_browser_repair_details() -> None:
    detail = (
        "无法从当前浏览器访问本地模型：Failed to fetch。"
        "请允许当前网页访问本地网络。"
    )

    assert friendly_model_error(RuntimeError(detail)) == detail


def test_text_model_panel_renders_three_steps_inside_existing_page(tmp_path) -> None:
    state = _PanelState(tmp_path)

    try:
        build_models_panel(state, purpose="text")
        texts = _rendered_texts()
    finally:
        ui.context.client.remove_all_elements()

    assert state.reload_count == 1
    assert "大模型接入：照着 3 步做" in texts
    assert "1. 选择厂商" in texts
    assert "2. 获取并粘贴 Key" in texts
    assert "3. 测试并绑定公众号" in texts
    assert "添加自定义模型" in texts
    assert all("独立向导" not in text for text in texts)


def test_image_model_panel_requires_real_test_image_in_existing_page(
    tmp_path,
) -> None:
    state = _PanelState(tmp_path)

    try:
        build_models_panel(state, purpose="image")
        texts = _rendered_texts()
    finally:
        ui.context.client.remove_all_elements()

    assert state.reload_count == 1
    assert "图片模型接入：照着 3 步做" in texts
    assert "3. 真实生成测试图" in texts
    assert any(
        "保存不等于可用" in text and "测试图片" in text
        for text in texts
    )
    assert "添加图片模型" in texts


def test_model_editor_uses_free_text_model_name_instead_of_select(
    tmp_path,
) -> None:
    state = _PanelState(tmp_path)

    try:
        build_models_panel(state, purpose="text")
        _click_button("添加自定义模型")
        elements = list(ui.context.client.elements.values())
    finally:
        ui.context.client.remove_all_elements()

    assert any(
        type(element).__name__ == "Input"
        and str(getattr(element, "_props", {}).get("label") or "")
        == "模型名称（可直接输入）"
        for element in elements
    )
    assert not any(
        type(element).__name__ == "Select"
        and str(getattr(element, "_props", {}).get("label") or "")
        == "推荐模型"
        for element in elements
    )


def test_model_editor_defines_local_probe_listener_with_string_icon(
    tmp_path,
) -> None:
    state = _PanelState(tmp_path)

    try:
        build_models_panel(state, purpose="text")
        _click_button("添加自定义模型")
        verify = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Button"
            and getattr(element, "text", None) == "保存并测试连接"
        )
        definition = verify._to_dict()
    finally:
        ui.context.client.remove_all_elements()

    assert definition["props"]["icon"] == "verified"
    assert isinstance(definition["props"]["icon"], str)
    click_events = [
        event for event in definition["events"] if event["type"] == "click"
    ]
    assert len(click_events) == 1
    assert "loopback-network" in click_events[0]["js_handler"]


def test_model_editor_guides_users_to_download_and_run_portable_bridge(
    tmp_path,
) -> None:
    state = _PanelState(tmp_path)

    try:
        build_models_panel(state, purpose="text")
        _click_button("添加自定义模型")
        links = [
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Link"
        ]
        texts = _rendered_texts()
    finally:
        ui.context.client.remove_all_elements()

    download = next(
        item
        for item in links
        if getattr(item, "text", None) == "1. 直接下载本机桥接器（Windows EXE）"
    )
    assert str(download._props.get("href") or "").endswith(
        "/downloads/BlueBloodLab-Cockpit-Bridge-1.4.1.exe"
    )
    assert "2. 双击下载的 EXE，并保持桥接器窗口打开。" in texts
    assert any("填写 Cockpit 实际地址和 Key" in text for text in texts)


def test_user_model_panel_marks_platform_models_read_only(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-user-model-panel-key",
    )
    state = _PanelState(tmp_path)
    platform_db = state.db.for_user("")
    state.db = state.db.for_user("current-user")
    save_model(
        platform_db,
        name="平台公共模型",
        provider_type=GEMINI,
        api_base="",
        model="gemini-platform",
        api_key="platform-key",
    )
    save_model(
        state.db,
        name="我的私有模型",
        provider_type=GEMINI,
        api_base="",
        model="gemini-private",
        api_key="private-key",
    )

    try:
        build_models_panel(state, purpose="text")
        texts = _rendered_texts()
        buttons = [
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Button"
        ]
    finally:
        ui.context.client.remove_all_elements()

    assert "平台公共模型" in texts
    assert "我的私有模型" in texts
    assert "由平台管理员维护，可直接绑定到你的公众号。" in texts
    assert sum(getattr(item, "text", None) == "编辑" for item in buttons) == 1
    assert sum(getattr(item, "text", None) == "删除" for item in buttons) == 1
