from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from app.db import Database
from app.ui import desktop
from app.ui.panels import settings_hub


class _SettingsState:
    def __init__(self, root: Path) -> None:
        self.config = {
            "_root": str(root),
            "_db_path": str(root / "settings.db"),
            "ai": {},
        }
        self.db = Database(root / "settings.db")
        self.reload_count = 0

    def reload_config(self) -> dict[str, Any]:
        self.reload_count += 1
        return self.config


def _text_values() -> list[str]:
    values: list[str] = []
    for element in ui.context.client.elements.values():
        text = getattr(element, "text", None)
        if text:
            values.append(str(text))
        label = getattr(element, "_props", {}).get("label")
        if type(element).__name__ == "Tab" and label:
            values.append(str(label))
    return values


def test_settings_entries_do_not_inflate_the_initial_workbench_payload(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        desktop,
        "should_show_onboarding",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(desktop, "_build_wizard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(desktop, "build_topic_center", lambda *_args: None)
    monkeypatch.setattr(desktop, "build_tasks_panel", lambda *_args: None)
    monkeypatch.setattr(
        desktop,
        "_build_accounts_panel",
        lambda *_args, **_kwargs: lambda: None,
    )
    monkeypatch.setattr(
        desktop,
        "build_model_management_panel",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        desktop,
        "build_creation_plans_panel",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(desktop, "build_feishu_panel", lambda *_args: None)
    monkeypatch.setattr(desktop, "_build_help_panel", lambda: None)

    try:
        desktop.create_desktop_app()
        texts = _text_values()
    finally:
        ui.context.client.remove_all_elements()

    for label in ("公众号", "模型管理", "创作方案", "飞书", "系统设置"):
        assert label not in texts
    for obsolete in ("文本模型", "提示词模板", "评审方案"):
        assert obsolete not in texts
    assert "微信公众号云中转" not in texts


def test_model_management_defers_hidden_text_and_image_sections(
    tmp_path,
    monkeypatch: Any,
) -> None:
    state = _SettingsState(tmp_path)
    rendered_purposes: list[str] = []

    def fake_models_panel(_state: Any, *, purpose: str) -> None:
        rendered_purposes.append(purpose)
        ui.label(f"模型面板：{purpose}")

    monkeypatch.setattr(settings_hub, "build_models_panel", fake_models_panel)

    try:
        settings_hub.build_model_management_panel(state)
        texts = _text_values()
    finally:
        ui.context.client.remove_all_elements()

    assert state.reload_count == 1
    assert "全部" in texts
    assert "文章模型" in texts
    assert "图片模型" in texts
    assert rendered_purposes == []


def test_creation_plans_defers_hidden_writing_and_review_sections(
    tmp_path,
    monkeypatch: Any,
) -> None:
    state = _SettingsState(tmp_path)
    rendered: list[tuple[str, Any]] = []

    def on_change() -> None:
        pass

    def fake_prompts(
        _state: Any,
        *,
        on_templates_change: Any = None,
    ) -> None:
        rendered.append(("prompts", on_templates_change))
        ui.label("提示词规则面板")

    def fake_reviews(
        _state: Any,
        *,
        on_profiles_change: Any = None,
    ) -> None:
        rendered.append(("reviews", on_profiles_change))
        ui.label("评审方案面板")

    monkeypatch.setattr(
        settings_hub,
        "build_prompt_templates_panel",
        fake_prompts,
    )
    monkeypatch.setattr(
        settings_hub,
        "build_editorial_review_profiles_panel",
        fake_reviews,
    )

    try:
        settings_hub.build_creation_plans_panel(
            state,
            on_plans_change=on_change,
        )
        texts = _text_values()
    finally:
        ui.context.client.remove_all_elements()

    assert "写作与图片规则" in texts
    assert "AI 评审方案" in texts
    assert "方案管理" in texts
    assert "系统默认方案" in texts
    assert rendered == []
