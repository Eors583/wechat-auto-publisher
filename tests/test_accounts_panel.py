from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from app.ui import desktop


class _AccountDB:
    def __init__(self) -> None:
        self.account: dict[str, Any] = {}
        self.user_settings: dict[str, str] = {}
        self.settings: dict[str, str] = {}

    def list_jobs(self, _limit: int) -> list[dict[str, Any]]:
        return []

    def get_user_setting(self, key: str) -> str | None:
        return self.user_settings.get(key)

    def set_user_setting(self, key: str, value: str) -> None:
        self.user_settings[key] = value

    def get_setting(self, key: str) -> str | None:
        return self.settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    def get_official_account(self, _account_id: str) -> dict[str, Any]:
        return dict(self.account)


class _AccountState:
    def __init__(self) -> None:
        self.db = _AccountDB()
        self.reload_count = 0
        self.account_refresh_count = 0
        self.model_registrations: list[dict[str, Any]] = []

    def reload_config(self) -> dict[str, Any]:
        self.reload_count += 1
        return {"ai": {}, "wechat": {}}

    def refresh_account_selects(self) -> None:
        self.account_refresh_count += 1

    def model_options(
        self,
        *,
        include_default: bool = True,
        purpose: str = "text",
        **_kwargs: Any,
    ) -> dict[str, str]:
        if purpose == "image":
            return {}
        return {"model-1": "文章模型"}

    def register_model_select(self, select: Any, **kwargs: Any) -> Any:
        self.model_registrations.append({"select": select, **kwargs})
        return select


class _ReviewService:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def get_account_editorial_review_default(
        self,
        _account_id: str,
    ) -> dict[str, str]:
        return {"profile_id": "review-profile"}


class _CreationPlanService:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def list(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "plan-1",
                "name": "企业管理深度文章",
                "available": True,
            }
        ]

    def get_account_default(self, _account_id: str) -> dict[str, Any]:
        return {
            "bound": False,
            "plan_id": "",
            "plan": None,
            "in_sync": True,
        }


def _elements() -> list[Any]:
    return list(ui.context.client.elements.values())


def _direct_parent_visible(element: Any) -> bool:
    parent_slot = element.parent_slot
    parent = getattr(parent_slot, "parent", None)
    return bool(getattr(parent, "visible", True))


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


def test_account_card_is_simple_by_default_but_builds_advanced_controls(
    monkeypatch: Any,
) -> None:
    state = _AccountState()
    account = {
        "id": "account-1",
        "name": "测试公众号",
        "app_id": "wx-test",
        "enabled": True,
        "model_id": "model-1",
        "model_name": "运营文章模型",
        "has_custom_layout": True,
        "layout": {
            "article_prompt": {},
            "inline_images": {},
            "editor_template": {},
        },
    }
    monkeypatch.setattr(desktop, "state", state)
    monkeypatch.setattr(desktop, "BatchService", _ReviewService)
    monkeypatch.setattr(
        desktop,
        "CreationPlanService",
        _CreationPlanService,
    )
    monkeypatch.setattr(
        desktop,
        "public_accounts",
        lambda _db: [dict(account)],
    )
    monkeypatch.setattr(
        desktop,
        "public_prompt_templates",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        desktop,
        "enabled_profile_options",
        lambda _service: {"review-profile": "专业深度型"},
    )
    state.db.account = dict(account)

    try:
        refresh = desktop._build_accounts_panel()
        elements = _elements()
        assert callable(refresh)
        assert state.reload_count == 1

        visible_button_texts = {
            str(element.text)
            for element in elements
            if type(element).__name__ == "Button"
            and getattr(element, "text", None)
            and bool(getattr(element, "visible", True))
        }
        assert {
            "添加公众号",
            "检测连接",
            "恢复上个版本",
        }.issubset(visible_button_texts)
        assert "保存配置" not in visible_button_texts
        assert any(
            type(element).__name__ == "Button"
            and str(getattr(element, "_props", {}).get("icon") or "")
            == "more_horiz"
            and bool(getattr(element, "visible", True))
            for element in elements
        )

        advanced_selects = {
            str(getattr(element, "_props", {}).get("label") or ""): element
            for element in elements
            if type(element).__name__ == "Select"
            and str(getattr(element, "_props", {}).get("label") or "")
            in {
                "内容定位 / 创作方案",
                "默认模型",
                "默认改写强度",
            }
        }
        assert set(advanced_selects) == {
            "内容定位 / 创作方案",
            "默认模型",
            "默认改写强度",
        }
        assert all(
            _direct_parent_visible(element) and element.visible
            for element in advanced_selects.values()
        )
        assert advanced_selects["内容定位 / 创作方案"].value == "plan-1"
        assert all(
            bool(select._change_handlers)
            for select in advanced_selects.values()
        )
        intensity_select = advanced_selects["默认改写强度"]
        intensity_select.value = "strong"
        intensity_select._change_handlers[-1](SimpleNamespace(value="strong"))
        saved_defaults = json.loads(
            state.db.user_settings["ui.account_defaults.account-1"]
        )
        assert saved_defaults["rewrite_intensity"] == "strong"
        assert any(
            "自动保存 · 创作默认值" in value
            for value in state.db.settings.values()
        )

        structured_rule_labels = {
            str(getattr(element, "text", "") or "")
            for element in elements
            if str(getattr(element, "text", "") or "")
            in {
                "排版模板",
                "正文配图",
                "封面规则",
                "提示词配置",
                "AI 评审方案",
                "草稿写入规则",
                "对标公众号",
            }
        }
        assert structured_rule_labels == {
            "排版模板",
            "正文配图",
            "封面规则",
            "提示词配置",
            "AI 评审方案",
            "草稿写入规则",
            "对标公众号",
        }

        benchmark_label = next(
            element
            for element in elements
            if getattr(element, "text", None) == "对标公众号"
        )
        benchmark_entry = benchmark_label.parent_slot.parent.parent_slot.parent
        listener = next(
            item
            for item in benchmark_entry._event_listeners.values()
            if item.type == "click"
        )
        listener.handler()
        dialog_elements = _elements()
        assert any(
            type(element).__name__ == "Select"
            and str(getattr(element, "_props", {}).get("label") or "")
            == "广告标题来源公众号"
            for element in dialog_elements
        )
    finally:
        ui.context.client.remove_all_elements()


def test_account_can_be_added_before_a_model_is_configured(
    monkeypatch: Any,
) -> None:
    state = _AccountState()
    state.model_options = lambda **_kwargs: {}
    monkeypatch.setattr(desktop, "state", state)
    monkeypatch.setattr(desktop, "BatchService", _ReviewService)
    monkeypatch.setattr(
        desktop,
        "CreationPlanService",
        _CreationPlanService,
    )
    monkeypatch.setattr(desktop, "public_accounts", lambda _db: [])
    monkeypatch.setattr(
        desktop,
        "public_prompt_templates",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        desktop,
        "enabled_profile_options",
        lambda _service: {"review-profile": "专业深度型"},
    )

    try:
        desktop._build_accounts_panel()
        _click_button("添加公众号")
        elements = _elements()
        model_select = next(
            element
            for element in elements
            if type(element).__name__ == "Select"
            and str(getattr(element, "_props", {}).get("label") or "")
            == "该公众号使用的文章模型（可选）"
        )
        save_button = next(
            element
            for element in elements
            if type(element).__name__ == "Button"
            and getattr(element, "text", None) == "保存"
        )
    finally:
        ui.context.client.remove_all_elements()

    assert model_select.value == ""
    assert getattr(model_select, "options", {}).get("") == "暂不绑定模型（可稍后选择）"
    assert not bool(getattr(save_button, "_props", {}).get("disable"))
    assert state.model_registrations == [
        {
            "select": model_select,
            "purpose": "text",
            "default_label": "暂不绑定模型（可稍后选择）",
            "owner": next(
                element
                for element in elements
                if type(element).__name__ == "Dialog"
            ),
        }
    ]


def test_layout_editor_distinguishes_indent_from_padding_and_refreshes_reviews() -> None:
    source = inspect.getsource(desktop._build_accounts_panel)

    assert "首行缩进（0em = 不缩进）" in source
    assert "正文左右留白（0px = 不留白）" in source
    assert "首行缩进只影响每段第一行" in source
    assert "rerender_pending_account_jobs" in source


def test_account_configuration_exposes_dynamic_benchmark_ad_settings() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert 'ui.label("广告栏同步")' in source
    assert 'label="广告标题来源公众号"' in source
    assert 'ui.label("对标公众号")' in source
    assert '"未选择，不处理广告标题"' in source
    assert '"enabled": bool(source_account_id)' in source
    assert '"启用广告标题同步"' not in source
    assert '"图片相似度（%）"' not in source
    assert '"仅写入图片匹配成功的广告"' not in source
    assert '"测试获取最新广告栏"' in source
    assert "on_benchmark_preview" in source
    assert '"获取失败："' in source
    assert "on_benchmark(" in source
    assert "ops-config-entry-grid-single" in source
    assert "ops-wrap-anywhere" in source
