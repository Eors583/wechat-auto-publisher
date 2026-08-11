from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

from nicegui import ui

from app.ui import desktop


class _FakeDb:
    def list_jobs(self, _limit: int) -> list[dict[str, Any]]:
        return []


class _FakeState:
    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.db = _FakeDb()
        self.selected_topic = ""
        self.topic_source = "manual"
        self.pending_rewrite = None
        self.busy = False
        self.wizard_job_id = None
        self.task_center_refresh = None
        self.account_selects: list[Any] = []
        self.model_selects: list[Any] = []

    def reload_config(self) -> dict[str, Any]:
        return self.config

    def account_options(self) -> dict[str, str]:
        return {}

    def remembered_account_ids(self) -> list[str]:
        return []

    def remember_account_ids(self, _account_ids: list[str]) -> None:
        pass

    def model_options(self, **_kwargs: Any) -> dict[str, str]:
        # The workbench's legacy default-account selects use an empty ID.
        return {"": "配置默认模型"}


class _FakeTabs:
    def set_value(self, _value: object) -> None:
        pass


def _synchronous_refreshable(function: Any) -> Any:
    """Keep this isolated render test independent from NiceGUI's event loop."""

    function.refresh = lambda *args, **kwargs: function(*args, **kwargs)
    return function


def _tab_labels(function: Any) -> list[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    calls = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ui"
            and node.func.attr == "tab"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return [str(node.args[0].value) for node in calls]


def test_primary_navigation_exposes_the_five_confirmed_workbench_entries() -> None:
    labels = _tab_labels(desktop.create_desktop_app)

    assert labels[:5] == ["创作台", "选题雷达", "任务队列", "公众号", "文章审核"]


def test_generation_can_move_to_the_background_task_center() -> None:
    source = inspect.getsource(desktop._build_wizard)  # noqa: SLF001

    assert '"后台开始生成"' in source
    assert '"查看后台任务"' in source
    assert "def open_background_generation()" in source
    assert "tabs.set_value(tab_jobs)" in source
    assert "state.task_center_refresh(active_batch_id)" in source


def test_workbench_contains_compact_entry_points_without_legacy_topic_toggle(
    monkeypatch: Any,
) -> None:
    overview_calls: list[object] = []

    def fake_overview(
        state: Any,
        *,
        on_go_tasks: Any,
    ) -> None:
        overview_calls.append((state, on_go_tasks))
        ui.label("今日运营概览")

    monkeypatch.setattr(desktop, "state", _FakeState())
    monkeypatch.setattr(desktop.ui, "refreshable", _synchronous_refreshable)
    monkeypatch.setattr(desktop.ui, "timer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(desktop, "build_overview_cards", fake_overview)

    try:
        desktop._build_wizard(_FakeTabs(), object(), object())
        elements = list(ui.context.client.elements.values())
    finally:
        ui.context.client.remove_all_elements()

    texts = {
        str(getattr(element, "text", "") or "")
        for element in elements
    }
    field_labels = {
        str(getattr(element, "_props", {}).get("label") or "")
        for element in elements
    }
    legacy_topic_toggles = [
        element
        for element in elements
        if type(element).__name__ == "Toggle"
        and set(getattr(element, "options", {})) == {
            "hot",
            "peer",
            "keyword",
            "manual",
        }
    ]

    assert overview_calls
    assert "今日运营概览" in texts
    assert "从选题库选择" in texts
    assert "文章主题（可选）" in field_labels
    assert len(legacy_topic_toggles) == 1
    assert legacy_topic_toggles[0].visible is False
    assert not any(
        element.visible
        and type(element).__name__ == "Toggle"
        and set(getattr(element, "options", {})) == {
            "hot",
            "peer",
            "keyword",
            "manual",
        }
        for element in elements
    )
