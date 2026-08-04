from __future__ import annotations

import inspect

from app.ui import desktop
from app.ui.panels import tasks


def test_generation_completion_focuses_batch_before_opening_task_center() -> None:
    source = inspect.getsource(desktop._build_wizard)
    refresh = "state.task_center_refresh(active_batch_id)"
    switch = "tabs.set_value(tab_jobs)"

    assert refresh in source
    assert switch in source
    assert source.rfind(refresh) < source.rfind(switch)


def test_active_generation_path_does_not_render_a_second_review_page() -> None:
    source = inspect.getsource(desktop._build_wizard)
    start = source.index("async def start_rewrite")
    active_path = source[start:]

    assert "render_batch_results(" not in active_path
    assert "render_review(" not in active_path
    assert "open_batch_review(" not in active_path


def test_desktop_generation_uses_shared_batch_service_only() -> None:
    source = inspect.getsource(desktop._build_wizard)
    start = source.index("async def start_rewrite")
    active_path = source[start:]

    assert "active_batch_service.create_batch(" in active_path
    assert "Pipeline(" not in active_path
    assert "create_job(" not in active_path
    assert "update_batch_job_review(" not in active_path


def test_desktop_removed_all_legacy_review_implementations() -> None:
    source = inspect.getsource(desktop._build_wizard)

    assert "def open_batch_review" not in source
    assert "def _legacy_render_batch_results" not in source
    assert "def render_batch_results" not in source
    assert "def render_review" not in source


def test_task_center_registers_focusable_external_refresh() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)

    assert "def refresh_and_focus(" in source
    assert "state.task_center_refresh = refresh_and_focus" in source
    assert 'runtime["focus_batch_id"] = str(batch_id)' in source


def test_shared_review_link_routes_directly_to_the_one_workbench() -> None:
    desktop_source = inspect.getsource(desktop.create_desktop_app)
    task_source = inspect.getsource(tasks.build_tasks_panel)

    assert 'query_params.get("view")' in desktop_source
    assert "tab_jobs\n            if open_requested_review" in desktop_source
    assert "else tab_settings\n            if open_requested_config" in desktop_source
    assert "else tab_wizard" in desktop_source
    assert "initial_batch_id=requested_batch_id" in desktop_source
    assert "initial_job_id=requested_job_id" in desktop_source
    assert "if initial_batch_id and initial_job_id:" in task_source
    assert "open_review_workbench(" in task_source
