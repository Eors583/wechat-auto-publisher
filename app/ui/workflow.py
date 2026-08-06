from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from nicegui import ui


@dataclass(frozen=True)
class WorkflowStep:
    key: str
    label: str
    hint: str


WORKFLOW_STEPS: tuple[WorkflowStep, ...] = (
    WorkflowStep("content", "准备内容", "选题、链接或正文"),
    WorkflowStep("accounts", "选择公众号", "确认本次生成范围"),
    WorkflowStep("generate", "生成文章", "各公众号并行处理"),
    WorkflowStep("review", "逐篇审核", "标题、正文与排版"),
    WorkflowStep("draft", "写入草稿", "全部确认后批量写入"),
)

CREATION_WORKFLOW_STEPS: tuple[WorkflowStep, ...] = WORKFLOW_STEPS[:3]

_STEP_INDEX = {step.key: index for index, step in enumerate(WORKFLOW_STEPS)}


def normalize_workflow_stage(stage: str) -> str:
    return stage if stage in _STEP_INDEX else WORKFLOW_STEPS[0].key


def next_review_job(
    jobs: Iterable[dict[str, Any]], *, current_job_id: int | None = None
) -> dict[str, Any] | None:
    """Return the next article which still needs an explicit review confirmation.

    The search starts after the current article and wraps once, so the same helper
    can be used by both the content workbench and the task center.
    """

    rows = list(jobs)
    if not rows:
        return None
    start = 0
    if current_job_id is not None:
        for index, row in enumerate(rows):
            if int(row.get("id") or 0) == int(current_job_id):
                start = index + 1
                break
    ordered = rows[start:] + rows[:start]
    for row in ordered:
        if current_job_id is not None and int(row.get("id") or 0) == int(current_job_id):
            continue
        if str(row.get("status") or "") != "ready_for_review":
            continue
        if str(row.get("review_status") or "unviewed") == "confirmed":
            continue
        return row
    return None


def render_workflow_guide(
    active_stage: str,
    *,
    note: str,
    completed: bool = False,
    compact: bool = False,
    steps: tuple[WorkflowStep, ...] = WORKFLOW_STEPS,
) -> None:
    """Render a shared workflow guide for the selected application surface."""

    if not steps:
        return
    step_index = {step.key: index for index, step in enumerate(steps)}
    normalized_stage = normalize_workflow_stage(active_stage)
    active_index = step_index.get(normalized_stage, len(steps) - 1)
    classes = "workflow-guide workflow-guide--compact" if compact else "workflow-guide"
    with ui.element("div").classes(classes):
        with ui.row().classes("workflow-guide__header w-full items-center justify-between"):
            ui.label(
                "开始创作" if len(steps) == len(CREATION_WORKFLOW_STEPS) else "从内容到草稿"
            ).classes("workflow-guide__title")
            ui.label(note).classes("workflow-guide__note")
        with ui.element("div").classes("workflow-steps"):
            for index, step in enumerate(steps):
                state = "done" if completed or index < active_index else (
                    "active" if index == active_index else "pending"
                )
                with ui.element("div").classes(f"workflow-step workflow-step--{state}"):
                    ui.label("✓" if state == "done" else str(index + 1)).classes(
                        "workflow-step__number"
                    )
                    with ui.column().classes("workflow-step__copy gap-0"):
                        ui.label(step.label).classes("workflow-step__label")
                        if not compact:
                            ui.label(step.hint).classes("workflow-step__hint")
