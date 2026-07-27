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
    end = source.index("def open_batch_review")
    active_path = source[start:end]

    assert "render_batch_results(" not in active_path
    assert "render_review(" not in active_path


def test_task_center_registers_focusable_external_refresh() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)

    assert "def refresh_and_focus(" in source
    assert "state.task_center_refresh = refresh_and_focus" in source
    assert 'runtime["focus_batch_id"] = str(batch_id)' in source
