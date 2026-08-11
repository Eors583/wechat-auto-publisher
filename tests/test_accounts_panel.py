from __future__ import annotations

from typing import Any

from nicegui import ui

from app.ui import desktop


class _AccountDB:
    def list_jobs(self, _limit: int) -> list[dict[str, Any]]:
        return []


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
            "bound": True,
            "plan_id": "plan-1",
            "plan": {"id": "plan-1", "name": "企业管理深度文章"},
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
            "保存配置",
            "恢复上个版本",
        }.issubset(visible_button_texts)
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
            }
        }
        assert structured_rule_labels == {
            "排版模板",
            "正文配图",
            "封面规则",
            "提示词配置",
            "AI 评审方案",
            "草稿写入规则",
        }
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
