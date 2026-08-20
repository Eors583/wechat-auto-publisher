from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app" / "ui" / "desktop.py"


def test_desktop_only_mounts_the_initial_workspace_on_first_paint() -> None:
    tree = ast.parse(DESKTOP.read_text(encoding="utf-8"))
    create_app = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_desktop_app"
    )
    mount_calls = [
        node
        for node in ast.walk(create_app)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "mount_tab"
    ]

    initial_mounts = [
        call
        for call in mount_calls
        if call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "initial_tab"
    ]
    assert len(initial_mounts) == 1


def test_inactive_workspaces_are_mounted_from_tab_changes() -> None:
    source = DESKTOP.read_text(encoding="utf-8")

    assert "tabs.on_value_change(lambda event: schedule_tab(event.value))" in source
    assert "scheduled_tabs.add(tab_name)" in source
    schedule_source = source[
        source.index("def schedule_tab(tab: Any) -> None:") :
        source.index("# Only the requested panel contributes")
    ]
    assert "client_timer(" in schedule_source
    assert "lambda selected_tab=tab: mount_tab(selected_tab)" in schedule_source
    assert "mount_tab(tab)" not in schedule_source
    assert 'str(tab_wizard.props["name"]): mount_wizard' in source
    assert 'str(tab_topics.props["name"]): mount_topics' in source
    assert 'str(tab_jobs.props["name"]): mount_jobs' in source
    assert 'str(tab_accounts.props["name"]): mount_accounts' in source
    assert 'str(tab_models.props["name"]): mount_models' in source
    assert 'str(tab_review.props["name"]): mount_review' in source


def test_account_configuration_uses_one_unified_scrollable_panel() -> None:
    source = DESKTOP.read_text(encoding="utf-8")

    assert "config_tabs = ui.toggle(" not in source
    assert "account_config_section =" not in source
    assert '"ops-config-body ops-config-body-unified"' in source
    assert "content_config_section =" in source
    assert "assets_config_section =" in source
    assert "review_config_section =" in source
    assert "sync_config_section" not in source


def test_account_workspace_replaces_the_legacy_settings_tab_stack() -> None:
    source = DESKTOP.read_text(encoding="utf-8")

    assert "def mount_accounts() -> None:" in source
    assert "_build_accounts_panel(" in source
    assert '· 配置中心' in source
    assert 'ui.button("检测连接")' in source
    assert '"保存配置",' in source
    assert "def schedule_settings_tab(tab: Any) -> None:" not in source
