from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_JURY_PANEL = ROOT / "app" / "ui" / "panels" / "review_jury.py"


def _tree() -> ast.Module:
    return ast.parse(REVIEW_JURY_PANEL.read_text(encoding="utf-8"))


def _function(
    name: str,
    *,
    async_function: bool = False,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    expected_type = ast.AsyncFunctionDef if async_function else ast.FunctionDef
    matches = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, expected_type) and node.name == name
    ]
    assert len(matches) == 1, f"expected one function named {name!r}"
    return matches[0]


def _call_name(call: ast.Call) -> str:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id
    if not isinstance(target, ast.Attribute):
        return ""
    parts = [target.attr]
    value = target.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        candidate
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call) and _call_name(candidate) == name
    ]


def _string_literals(node: ast.AST) -> set[str]:
    return {
        candidate.value
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Constant)
        and isinstance(candidate.value, str)
    }


def _keyword(call: ast.Call, name: str) -> ast.expr:
    matches = [
        keyword.value
        for keyword in call.keywords
        if keyword.arg == name
    ]
    assert len(matches) == 1, f"expected one {name!r} keyword"
    return matches[0]


def _is_not_none_guard(node: ast.If, variable: str) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == variable
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def test_completed_review_immediately_scrolls_to_inline_result() -> None:
    """A completed run stays in the workbench and reveals its conclusion."""

    start_review = _function("start_review", async_function=True)
    completion_guards = [
        node
        for node in ast.walk(start_review)
        if isinstance(node, ast.If)
        and _is_not_none_guard(node, "completed_review")
    ]

    assert completion_guards
    assert any(
        _calls(statement, "scroll_to_review_result")
        for guard in completion_guards
        for statement in guard.body
    ), "successful review completion must immediately reveal and scroll to the inline result"


def test_review_result_is_inline_and_does_not_create_a_result_dialog() -> None:
    """The conclusion belongs to the current review page, not a nested modal."""

    panel = _function("build_review_jury_panel")
    assigned_names = {
        target.id
        for node in ast.walk(panel)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    call_names = {
        _call_name(call)
        for call in ast.walk(panel)
        if isinstance(call, ast.Call)
    }

    assert "result_dialog" not in assigned_names
    assert "result_dialog.open" not in call_names
    assert "result_dialog.close" not in call_names


def test_scroll_helper_reveals_inline_result_and_uses_browser_scroll() -> None:
    """The inline conclusion must be made visible before smooth scrolling."""

    scroll_to_result = _function(
        "scroll_to_review_result",
        async_function=True,
    )
    call_names = {
        _call_name(call)
        for call in ast.walk(scroll_to_result)
        if isinstance(call, ast.Call)
    }
    literals = _string_literals(scroll_to_result)

    assert "result_section.set_visibility" in call_names
    assert "result_section.run_method" in call_names
    assert "scrollIntoView" in literals
    assert "smooth" in literals


def test_view_conclusion_button_scrolls_to_inline_result() -> None:
    """Opening an existing conclusion uses the same in-page navigation."""

    render_summary = _function("render_review_summary")
    conclusion_buttons = [
        call
        for call in _calls(render_summary, "ui.button")
        if call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "查看评审结论"
    ]

    assert len(conclusion_buttons) == 1
    on_click = _keyword(conclusion_buttons[0], "on_click")
    assert isinstance(on_click, ast.Name)
    assert on_click.id == "scroll_to_review_result"


def test_review_result_is_conclusion_plus_selectable_improvement_items() -> None:
    """The inline result is a decision area, not another prompt form."""

    render_result = _function("render_review_result")
    literals = _string_literals(render_result)

    assert "评审结论" in literals
    assert "整体改进方向（可选择）" in literals
    assert any(text.startswith("发布建议：") for text in literals)
    assert any(text.startswith("评审判断：") for text in literals)
    assert any(text.startswith("整体改进方向：") for text in literals)

    checkboxes = _calls(render_result, "ui.checkbox")
    assert any(
        call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "接受这条改进意见"
        for call in checkboxes
    ), "each safe suggestion must expose an explicit accept checkbox"


def test_review_result_removes_old_manual_rewrite_and_candidate_confirmation_ui() -> None:
    """Operators choose suggestions; they must not write another instruction."""

    source = REVIEW_JURY_PANEL.read_text(encoding="utf-8")
    obsolete_copy = {
        "修改方式",
        "指定段落编号",
        "补充要求",
        "核实备注",
        "预览原稿与修改稿",
        "确认应用修改稿",
        "应用此修改稿",
        "应用所选修改",
    }

    assert not {
        text for text in obsolete_copy if text in source
    }, "the old manual rewrite/candidate confirmation flow must stay removed"


def test_review_result_has_only_the_two_article_decision_actions() -> None:
    render_controls = _function("render_rewrite_controls")
    literals = _string_literals(render_controls)

    assert "使用原文" in literals
    assert "智能修改原文" in literals
    assert "接受这条改进意见" not in literals


def test_smart_rewrite_uses_engagement_optimization_and_immediately_applies() -> None:
    """One click must optimize the whole article, apply, and refresh it."""

    smart_rewrite = _function("smart_rewrite", async_function=True)
    generate_calls = _calls(
        smart_rewrite,
        "service.generate_editorial_rewrite_candidate",
    )
    apply_calls = _calls(
        smart_rewrite,
        "service.apply_editorial_review_application",
    )
    update_calls = _calls(smart_rewrite, "on_job_updated")

    assert len(generate_calls) == 1
    assert len(apply_calls) == 1
    assert len(update_calls) == 1

    generate = generate_calls[0]
    issue_ids = _keyword(generate, "issue_ids")
    rewrite_mode = _keyword(generate, "rewrite_mode")
    paragraph_numbers = _keyword(generate, "paragraph_numbers")
    instruction = _keyword(generate, "instruction")

    assert isinstance(issue_ids, ast.Name) and issue_ids.id == "selected_ids"
    assert (
        isinstance(rewrite_mode, ast.Constant)
        and rewrite_mode.value == "engagement_optimization"
    )
    assert isinstance(paragraph_numbers, ast.List) and not paragraph_numbers.elts
    assert isinstance(instruction, ast.Constant) and instruction.value == ""

    assert generate.lineno < apply_calls[0].lineno < update_calls[0].lineno


def test_smart_rewrite_keeps_parent_review_workbench_open() -> None:
    """Refreshing an inline result must never close/rebuild the workbench."""

    smart_rewrite = _function("smart_rewrite", async_function=True)
    call_names = {
        _call_name(call)
        for call in ast.walk(smart_rewrite)
        if isinstance(call, ast.Call)
    }

    assert "dialog.close" not in call_names
    assert "result_dialog.close" not in call_names
    assert "result_dialog.open" not in call_names
    assert "open_review_workbench" not in call_names
    assert "on_job_updated" in call_names


def test_review_result_displays_ai_estimated_engagement_dimensions() -> None:
    """Forecast scores must be visible and clearly not real backend data."""

    render_result = _function("render_review_result")
    literals = _string_literals(render_result)
    combined = "\n".join(literals)

    assert "AI" in combined and "预估" in combined
    assert "真实公众号后台数据" in combined
    assert _calls(render_result, "dimension.get")


def test_use_original_stays_on_review_page_without_changing_article() -> None:
    """Keeping the original stays in-place and does not change article content."""

    use_original = _function("use_original", async_function=True)
    call_names = {
        _call_name(call)
        for call in ast.walk(use_original)
        if isinstance(call, ast.Call)
    }

    assert not any(name.endswith(".close") for name in call_names)
    assert not any(name.endswith(".open") for name in call_names)
    assert not any(name.startswith("service.") for name in call_names)
    assert "on_job_updated" not in call_names
