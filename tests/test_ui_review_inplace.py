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


def _is_liveness_return_guard(node: ast.AST) -> bool:
    """Return true for ``if not workbench_alive(): return`` guards."""

    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Call)
        and _call_name(node.test.operand) == "workbench_alive"
        and any(isinstance(statement, ast.Return) for statement in node.body)
    )


def _has_guarded_button_restore(node: ast.AST) -> bool:
    """Button reset must only touch controls while the workbench still exists."""

    for try_node in ast.walk(node):
        if not isinstance(try_node, ast.Try):
            continue
        for statement in try_node.finalbody:
            if not isinstance(statement, ast.If):
                continue
            if not (
                isinstance(statement.test, ast.Call)
                and _call_name(statement.test) == "workbench_alive"
            ):
                continue
            if any(
                isinstance(candidate, ast.Call)
                and _call_name(candidate) == "set_button_loading"
                and len(candidate.args) >= 2
                and isinstance(candidate.args[1], ast.Constant)
                and candidate.args[1].value is False
                for candidate in ast.walk(statement)
            ):
                return True
    return False


@pytest.mark.parametrize(
    "action_name",
    [
        "regenerate_paragraph",
        "regenerate_one_image",
        "remove_one_image",
        "regenerate_inline_images",
        "regenerate_cover",
        "restore_version",
        "save_and_render",
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


@pytest.mark.parametrize(
    ("action_name", "scroll_call"),
    [
        ("regenerate_paragraph", "scroll_to_workbench_result"),
        ("regenerate_one_image", "scroll_to_workbench_result"),
        ("remove_one_image", "scroll_to_workbench_result"),
        ("regenerate_inline_images", "scroll_to_workbench_result"),
        ("regenerate_cover", "scroll_to_workbench_result"),
        ("restore_version", "scroll_to_updated_article"),
        ("save_and_render", "scroll_to_workbench_result"),
    ],
)
def test_async_review_results_are_brought_into_view(
    action_name: str,
    scroll_call: str,
) -> None:
    """Every visible async result should remain in place and focus its output."""

    action = _review_action(action_name)
    calls = {
        _call_name(node)
        for node in ast.walk(action)
        if isinstance(node, ast.Call)
    }

    assert scroll_call in calls


def test_ai_jury_rewrite_is_wired_to_the_updated_article_locator() -> None:
    """The jury receives the workbench callback that focuses the rewritten body."""

    workbench = _function("open_review_workbench")
    jury_calls = [
        node
        for node in ast.walk(workbench)
        if isinstance(node, ast.Call)
        and _call_name(node) == "build_review_jury_panel"
    ]
    assert len(jury_calls) == 1
    callback_keywords = [
        keyword.value
        for keyword in jury_calls[0].keywords
        if keyword.arg == "on_article_updated"
    ]
    assert len(callback_keywords) == 1
    assert isinstance(callback_keywords[0], ast.Name)
    assert callback_keywords[0].id == "scroll_to_updated_article"

    locator = _review_action("scroll_to_updated_article")
    scroll_calls = [
        node
        for node in ast.walk(locator)
        if isinstance(node, ast.Call)
        and _call_name(node) == "scroll_to_workbench_result"
    ]
    assert len(scroll_calls) == 1
    assert scroll_calls[0].args
    assert isinstance(scroll_calls[0].args[0], ast.Name)
    assert scroll_calls[0].args[0].id == "body_in"
    block_values = [
        keyword.value
        for keyword in scroll_calls[0].keywords
        if keyword.arg == "block"
    ]
    assert len(block_values) == 1
    assert isinstance(block_values[0], ast.Constant)
    assert block_values[0].value == "start"


def test_ai_jury_receives_the_parent_workbench_liveness_callback() -> None:
    """The nested jury must stop updating after its parent dialog is closed."""

    workbench = _function("open_review_workbench")
    jury_calls = [
        node
        for node in ast.walk(workbench)
        if isinstance(node, ast.Call)
        and _call_name(node) == "build_review_jury_panel"
    ]
    assert len(jury_calls) == 1
    callbacks = [
        keyword.value
        for keyword in jury_calls[0].keywords
        if keyword.arg == "is_workbench_alive"
    ]
    assert len(callbacks) == 1
    assert isinstance(callbacks[0], ast.Name)
    assert callbacks[0].id == "workbench_alive"


@pytest.mark.parametrize(
    "action_name",
    [
        "regenerate_paragraph",
        "regenerate_one_image",
        "remove_one_image",
        "regenerate_inline_images",
        "regenerate_cover",
        "load_covers",
        "restore_version",
        "save_and_render",
        "confirm",
    ],
)
def test_long_running_workbench_actions_guard_ui_after_io_and_button_reset(
    action_name: str,
) -> None:
    """A late service response must not update controls from a closed dialog."""

    action = _review_action(action_name)
    io_calls = [
        node
        for node in ast.walk(action)
        if isinstance(node, ast.Call)
        and _call_name(node) == "run.io_bound"
    ]
    guards = [
        node
        for node in ast.walk(action)
        if _is_liveness_return_guard(node)
    ]

    assert io_calls, f"{action_name} should have a long-running I/O call"
    assert guards, (
        f"{action_name} must return when the workbench was closed "
        "while its I/O call was running"
    )
    assert max(call.lineno for call in io_calls) < min(
        guard.lineno for guard in guards
    ), f"{action_name} must check liveness after its final I/O response"
    assert _has_guarded_button_restore(action), (
        f"{action_name} must not restore its loading button after "
        "the workbench has been deleted"
    )


def test_explicit_close_marks_workbench_closed_before_deleting_dialog() -> None:
    """The close button must invalidate late callbacks before closing NiceGUI."""

    close_workbench = _function("close_workbench")
    close_calls = [
        node
        for node in ast.walk(close_workbench)
        if isinstance(node, ast.Call)
        and _call_name(node) == "dialog.close"
    ]
    state_assignments = [
        node
        for node in ast.walk(close_workbench)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "workbench_state"
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "open"
            for target in node.targets
        )
    ]

    assert len(close_calls) == 1
    assert len(state_assignments) == 1
    assert state_assignments[0].lineno < close_calls[0].lineno


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
