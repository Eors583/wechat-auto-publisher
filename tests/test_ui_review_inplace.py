from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TASKS_PANEL = ROOT / "app" / "ui" / "panels" / "tasks.py"


def _review_action(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(TASKS_PANEL.read_text(encoding="utf-8"))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one async review action named {name!r}"
    return matches[0]


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(TASKS_PANEL.read_text(encoding="utf-8"))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one function named {name!r}"
    return matches[0]


def _call_name(call: ast.Call) -> str:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        parts = [target.attr]
        value = target.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


@pytest.mark.parametrize(
    "action_name",
    [
        "regenerate_paragraph",
        "regenerate_one_image",
        "remove_one_image",
        "regenerate_inline_images",
        "regenerate_cover",
    ],
)
def test_ai_revision_actions_keep_review_workbench_open(action_name: str) -> None:
    """A successful local revision must update the open workbench in place.

    Closing and immediately rebuilding the dialog discards scroll position,
    expansion state and unfinished review input.  Navigation is only allowed
    for explicit close/save/confirm actions, never for an AI revision action.
    """

    action = _review_action(action_name)
    calls = {_call_name(node) for node in ast.walk(action) if isinstance(node, ast.Call)}

    assert "dialog.close" not in calls
    assert "open_review_workbench" not in calls


def _is_review_open_flag(node: ast.AST, *, variable: str) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == variable
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "review_open"
    )


def test_task_center_timer_does_not_render_while_review_is_open() -> None:
    """Polling active batches must not rebuild the page behind an open review.

    Rebuilding the task center host invalidates the currently open workbench
    and looks like a continuous page refresh without actually completing the
    review.  The timer may still poll while jobs are running, but its render
    call must be guarded by the shared ``review_open`` state.
    """

    refresh = _function("refresh_running_batches")
    guarded_render = False
    for condition in ast.walk(refresh):
        if not isinstance(condition, ast.If):
            continue
        has_closed_review_guard = any(
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and _is_review_open_flag(node.operand, variable="runtime")
            for node in ast.walk(condition.test)
        )
        renders_inside_guard = any(
            isinstance(node, ast.Call) and _call_name(node) == "render"
            for statement in condition.body
            for node in ast.walk(statement)
        )
        guarded_render = guarded_render or (
            has_closed_review_guard and renders_inside_guard
        )

    assert guarded_render, (
        "the task-center polling timer must call render() only when "
        'runtime["review_open"] is false'
    )


def test_task_center_passes_shared_review_state_to_every_review_entry() -> None:
    """All task-center review buttons must participate in the refresh guard."""

    panel = _function("build_tasks_panel")
    batch_card_calls = [
        node
        for node in ast.walk(panel)
        if isinstance(node, ast.Call) and _call_name(node) == "_render_batch_card"
    ]
    assert batch_card_calls
    assert all(
        any(
            keyword.arg == "review_runtime"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "runtime"
            for keyword in call.keywords
        )
        for call in batch_card_calls
    )

    batch_card = _function("_render_batch_card")
    assert "review_runtime" in {
        argument.arg for argument in batch_card.args.args + batch_card.args.kwonlyargs
    }
    review_calls = [
        node
        for node in ast.walk(batch_card)
        if isinstance(node, ast.Call) and _call_name(node) == "open_review_workbench"
    ]
    assert review_calls, "expected task-center buttons to open the review workbench"
    assert all(
        any(
            keyword.arg == "review_runtime"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "review_runtime"
            for keyword in call.keywords
        )
        for call in review_calls
    ), "every review entry must pass the shared review_runtime state"
