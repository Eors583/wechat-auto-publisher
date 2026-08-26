from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any

from nicegui import ui

from app.ui import desktop
from app.ui.background_activity import _generation_activity, _review_activity
from app.ui.navigation import ui_navigation_target, ui_root_url
from app.ui.panels import tasks


class _FakeDb:
    def list_jobs(self, _limit: int) -> list[dict[str, Any]]:
        return []

    def credit_wallet_summary(self) -> dict[str, int]:
        return {"available": 12_345, "reserved": 0, "charged": 0}


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


def test_primary_navigation_uses_account_embedded_model_settings() -> None:
    labels = _tab_labels(desktop.create_desktop_app)

    assert labels[:7] == [
        "创作台",
        "选题雷达",
        "任务队列",
        "公众号",
        "飞书机器人",
        "积分与用量",
        "文章审核",
    ]
    assert "模型配置" not in labels


def test_sidebar_profile_shows_current_user_credit_balance() -> None:
    assert desktop._sidebar_profile_meta(  # noqa: SLF001
        _FakeDb(), page_is_admin=False
    ) == "运营用户 · 12,345 积分"
    assert desktop._sidebar_profile_meta(  # noqa: SLF001
        _FakeDb(), page_is_admin=True
    ) == "内容运营 · 12,345 积分"


def test_sidebar_profile_contains_long_names_and_balance() -> None:
    from app.ui.styles import APP_CSS

    assert ".ops-sidebar-profile-copy { flex: 1 1 auto; overflow: hidden; }" in APP_CSS
    assert ".ops-sidebar-profile-name," in APP_CSS
    assert "text-overflow: ellipsis" in APP_CSS
    assert ".ops-sidebar-profile > .q-btn { flex: 0 0 auto;" in APP_CSS


def test_public_urls_preserve_the_production_ui_root_path(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("WECHAT_PUBLISHER_UI_ROOT_PATH", "/publisher/")

    assert ui_root_url() == "/publisher/"
    assert ui_root_url({"view": "onboarding"}) == "/publisher/?view=onboarding"
    assert desktop._preflight_repair_url("account-1", "template") == (
        "/publisher/?view=config&repair=template&account_id=account-1"
    )
    assert tasks._settings_action_url("open_account_settings", "account-1") == (
        "/publisher/?view=config&repair=account&account_id=account-1"
    )
    assert _generation_activity(
        {
            "id": "batch-1",
            "jobs": [{"status": "processing"}],
            "progress": {"total": 1, "completed": 0},
        }
    )["url"] == "/publisher/?view=tasks&batch_id=batch-1"
    assert _review_activity(
        {"status": "running", "batch_id": "batch-1", "job_id": 9}
    )["url"] == "/publisher/?view=review&batch_id=batch-1&job_id=9"


def test_nicegui_navigation_target_does_not_duplicate_the_proxy_prefix(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("WECHAT_PUBLISHER_UI_ROOT_PATH", "/publisher/")

    target = ui_navigation_target(ui_root_url({"view": "onboarding"}))
    client_url = "/publisher" + target

    assert target == "/?view=onboarding"
    assert client_url == "/publisher/?view=onboarding"
    assert "/publisher/publisher/" not in client_url
    assert ui_navigation_target("https://example.com") == "https://example.com"


def test_auto_onboarding_redirect_is_admin_root_only() -> None:
    status = {
        "content_ready_account_ids": ["account-1"],
        "repair_step": "wechat",
        "wizard_required": True,
    }

    assert desktop._should_auto_open_onboarding(  # noqa: SLF001
        page_is_admin=True,
        requested_view="",
        status=status,
    )
    assert not desktop._should_auto_open_onboarding(  # noqa: SLF001
        page_is_admin=True,
        requested_view="onboarding",
        status=status,
    )
    assert not desktop._should_auto_open_onboarding(  # noqa: SLF001
        page_is_admin=True,
        requested_view="config",
        status=status,
    )
    assert not desktop._should_auto_open_onboarding(  # noqa: SLF001
        page_is_admin=False,
        requested_view="",
        status=status,
    )


def test_internal_navigation_defaults_to_the_local_root(monkeypatch: Any) -> None:
    monkeypatch.delenv("WECHAT_PUBLISHER_UI_ROOT_PATH", raising=False)

    assert ui_root_url({"view": "tasks"}) == "/?view=tasks"


def test_ui_navigation_callers_use_the_nicegui_navigation_boundary() -> None:
    root = Path(__file__).resolve().parents[1]

    for relative in (
        "app/ui/desktop.py",
        "app/ui/preflight_repair.py",
        "app/ui/panels/onboarding_wizard.py",
        "app/ui/panels/tasks.py",
    ):
        source = "".join(
            (root / relative).read_text(encoding="utf-8").split()
        )
        assert "ui.navigate.to(ui_root_url(" not in source, relative
        assert "ui.navigate.to(preflight_repair_url(" not in source, relative
        assert "ui.navigate.to(_settings_action_url(" not in source, relative


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
