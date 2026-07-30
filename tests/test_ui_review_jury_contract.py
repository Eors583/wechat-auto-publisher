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


def test_jury_ui_liveness_honors_the_parent_workbench_callback() -> None:
    """A live browser client is insufficient once its review dialog is closed."""

    ui_alive = _function("ui_alive")
    call_names = {
        _call_name(call)
        for call in ast.walk(ui_alive)
        if isinstance(call, ast.Call)
    }

    assert "is_workbench_alive" in call_names
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "is_workbench_alive"
        and any(
            isinstance(operator, ast.Is)
            for operator in node.test.ops
        )
        for node in ast.walk(ui_alive)
    ), "the callback must remain optional for non-workbench reuse"


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


def test_article_comparison_uses_persisted_review_snapshots() -> None:
    """Before/after previews must survive closing and reopening the workbench.

    The review source and generated candidate are persisted with the editorial
    review records.  Reading mutable ``job`` state here would lose the original
    article as soon as ``on_job_updated`` refreshes it.
    """

    render_comparison = _function("render_article_comparison")
    literals = _string_literals(render_comparison)

    assert "source_snapshot" in literals
    assert "candidate_snapshot" in literals
    assert "改写前原文" in literals
    assert "智能修改后" in literals


def test_article_comparison_is_a_responsive_two_column_inline_preview() -> None:
    """Wide screens compare side by side; narrow screens may stack naturally."""

    render_comparison = _function("render_article_comparison")
    layout_calls = [
        call
        for call in ast.walk(render_comparison)
        if isinstance(call, ast.Call)
        and _call_name(call) in {"ui.row", "ui.grid"}
    ]
    assert layout_calls, "the two versions need one shared comparison layout"

    layout_literals = "\n".join(_string_literals(render_comparison))
    assert (
        "grid-cols-2" in layout_literals
        or "lg:" in layout_literals
        or "md:" in layout_literals
        or "col-md-6" in layout_literals
    ), "the comparison must become two columns on desktop widths"
    assert (
        "w-full" in layout_literals
    ), "the comparison must remain usable at narrow workbench widths"
    assert "gap:0" in layout_literals, (
        "NiceGUI rows add their own flex gap; it must be removed so two "
        "50% Quasar columns do not wrap on desktop"
    )


def test_smart_rewrite_renders_and_scrolls_to_before_after_comparison() -> None:
    """Successful AI rewriting focuses the comparison, not only the new body."""

    smart_rewrite = _function("smart_rewrite", async_function=True)
    render_calls = _calls(smart_rewrite, "render_article_comparison")
    scroll_calls = _calls(smart_rewrite, "scroll_to_article_comparison")
    update_calls = _calls(smart_rewrite, "on_job_updated")

    assert len(render_calls) == 1
    assert len(scroll_calls) == 1
    assert len(update_calls) == 1
    assert update_calls[0].lineno < render_calls[0].lineno < scroll_calls[0].lineno


def test_article_comparison_scroll_helper_keeps_current_workbench_open() -> None:
    """Displaying the generated comparison is an in-page navigation action."""

    scroll_to_comparison = _function(
        "scroll_to_article_comparison",
        async_function=True,
    )
    call_names = {
        _call_name(call)
        for call in ast.walk(scroll_to_comparison)
        if isinstance(call, ast.Call)
    }
    literals = _string_literals(scroll_to_comparison)

    assert "comparison_section.set_visibility" in call_names
    assert "comparison_section.run_method" in call_names
    assert "scrollIntoView" in literals
    assert "smooth" in literals
    assert not any(name.endswith(".close") for name in call_names)
    assert "open_review_workbench" not in call_names


def test_existing_application_restores_the_persisted_comparison() -> None:
    """Reopening a reviewed article must still show its before/after result."""

    panel = _function("build_review_jury_panel")
    render_result = _function("render_review_result")
    direct_calls = _calls(panel, "render_article_comparison")
    delegated_calls = _calls(render_result, "render_article_comparison")

    assert direct_calls or delegated_calls, (
        "the initial persisted application must be rendered as a comparison "
        "when the review workbench is reopened"
    )


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
