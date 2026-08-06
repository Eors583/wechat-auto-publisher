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

    assert "tabs.on_value_change(lambda event: mount_tab(event.value))" in source
    assert 'str(tab_wizard.props["name"]): mount_wizard' in source
    assert 'str(tab_topics.props["name"]): mount_topics' in source
    assert 'str(tab_jobs.props["name"]): mount_jobs' in source
    assert 'str(tab_settings.props["name"]): mount_settings' in source
