from __future__ import annotations

import inspect

from app.ui import desktop
from app.ui.panels import tasks
from app.ui.styles import APP_CSS


def test_generation_completion_focuses_batch_before_opening_task_center() -> None:
    source = inspect.getsource(desktop._build_wizard)
    refresh = "state.task_center_refresh(active_batch_id)"
    switch = "tabs.set_value(tab_jobs)"

    assert refresh in source
    assert switch in source
    assert source.rfind(refresh) < source.rfind(switch)
    assert 'entry_mode="completion"' in source
    assert "state.pending_task_center_entry" in source


def test_lazy_task_center_consumes_pending_completion_entry() -> None:
    desktop_source = inspect.getsource(desktop.create_desktop_app)
    task_source = inspect.getsource(tasks.build_tasks_panel)

    assert "if page_state.pending_task_center_entry:" in desktop_source
    assert 'pending_entry.get("entry_mode") or "activity"' in desktop_source
    assert "page_state.pending_task_center_entry = None" in desktop_source
    assert 'initial_entry_mode == "completion"' in task_source
    assert 'runtime.get("completion_batch_id")' in task_source
    assert '"本次任务已生成"' in task_source
    assert '"审核第 1 篇"' in task_source
    assert '"返回待处理收件箱"' in task_source


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


def test_task_center_does_not_reference_removed_refresh_button() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)

    assert "refresh_btn" not in source
    assert 'requested_status in {"ready_for_review", "ready_for_draft"}' in source
    assert 'runtime["inbox_bucket"] = inbox_bucket' in source


def test_task_review_action_keeps_a_text_only_single_line_button() -> None:
    source = inspect.getsource(tasks._render_inbox_article_card)

    assert 'primary_label = "打开审核"' in source
    assert "primary_icon = None" in source
    assert 'classes("ops-task-row-primary-action")' in source
    assert ".ops-task-row-primary-action .q-icon { display: none !important; }" in APP_CSS


def test_task_center_workflow_guide_follows_effective_batch_state() -> None:
    cases = [
        ({"status": "ready_for_review", "progress": {"unconfirmed": 1}}, "review"),
        ({"status": "ready_for_draft", "progress": {"unconfirmed": 0}}, "draft"),
        ({"status": "injecting", "progress": {"unconfirmed": 0}}, "draft"),
        ({"status": "drafted", "progress": {"unconfirmed": 0}}, "draft"),
        (
            {
                "status": "partial_failed",
                "progress": {"unconfirmed": 1, "ready_for_draft": 1},
            },
            "review",
        ),
        (
            {
                "status": "partial_failed",
                "progress": {"unconfirmed": 0, "ready_for_draft": 1},
            },
            "draft",
        ),
    ]

    for batch, expected in cases:
        assert tasks.task_center_workflow_stage(batch) == expected

    source = inspect.getsource(tasks.build_tasks_panel)
    assert "render_task_center_guide(completed_batch)" in source
    assert "render_workflow_guide(stage, note=note" in source
    assert "全部文章已确认，可以安全写入公众号草稿箱" in source


def test_shared_review_link_routes_directly_to_the_one_workbench() -> None:
    desktop_source = inspect.getsource(desktop.create_desktop_app)

    assert 'query_params.get("view")' in desktop_source
    assert "tab_review\n            if open_requested_review" in desktop_source
    assert "else tab_accounts\n            if open_requested_config" in desktop_source
    assert "else tab_wizard" in desktop_source
    assert "def open_review_page(" in desktop_source
    assert "build_review_page(" in desktop_source
    assert "tabs.set_value(tab_review)" in desktop_source
    assert "on_open_review=open_review_page" in desktop_source
    assert 'ui.tab("文章审核", icon="rate_review").classes(' in desktop_source
    assert '"ops-review-route-tab"' in desktop_source
