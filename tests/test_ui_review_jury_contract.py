from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ui.panels.review_jury import _format_dimension_score

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


def test_review_can_enter_background_without_the_blocking_overlay() -> None:
    start_review = _function("start_review", async_function=True)
    literals = _string_literals(start_review)
    call_names = {
        _call_name(call)
        for call in ast.walk(start_review)
        if isinstance(call, ast.Call)
    }

    assert "AI 评审已转入后台，可继续处理其他文章；右侧可查看进度。" in literals
    assert "on_background_review" in call_names
    assert "on_enter_background" in call_names


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
    assert "result_section.set_visibility" in call_names
    scroll_calls = _calls(scroll_to_result, "scroll_dom_target")
    assert len(scroll_calls) == 1
    assert isinstance(scroll_calls[0].args[0], ast.Name)
    assert scroll_calls[0].args[0].id == "result_anchor_id"
    assert not any(name.endswith(".run_method") for name in call_names)


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


def test_deep_panel_does_not_render_a_duplicate_conclusion_entry() -> None:
    """Quick review owns the only semantic entry into the conclusion."""

    render_summary = _function("render_review_summary")
    conclusion_buttons = [
        call
        for call in _calls(render_summary, "ui.button")
        if call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "查看评审结论"
    ]

    assert not conclusion_buttons


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
        and call.args[0].value == "勾选并交给 AI 改写"
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


def test_review_result_removes_the_no_op_use_original_action() -> None:
    render_controls = _function("render_rewrite_controls")
    literals = _string_literals(render_controls)

    assert "使用原文" not in literals
    assert "按所选建议优化整篇" in literals
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
    assert "comparison_section.set_visibility" in call_names
    scroll_calls = _calls(scroll_to_comparison, "scroll_dom_target")
    assert len(scroll_calls) == 1
    assert isinstance(scroll_calls[0].args[0], ast.Name)
    assert scroll_calls[0].args[0].id == "comparison_anchor_id"
    assert not any(name.endswith(".run_method") for name in call_names)
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


@pytest.mark.parametrize(
    ("dimension", "expected"),
    [
        pytest.param({}, "—", id="missing"),
        pytest.param({"score": None}, "—", id="null"),
        pytest.param({"score": ""}, "—", id="empty"),
        pytest.param({"score": 0}, "0", id="explicit-zero"),
        pytest.param({"score": 83}, "83", id="non-zero"),
    ],
)
def test_dimension_score_has_five_value_contracts(
    dimension: dict[str, object],
    expected: str,
) -> None:
    assert _format_dimension_score(dimension.get("score")) == expected


def test_dimension_score_rejects_unparseable_values() -> None:
    assert _format_dimension_score("not-a-score") == "—"


def test_legacy_placeholder_zero_is_rendered_as_missing() -> None:
    assert _format_dimension_score(
        0,
        summary="本次评审未单独返回该项判断。",
    ) == "—"


def test_explicit_zero_with_a_real_judgement_remains_zero() -> None:
    assert _format_dimension_score(
        0,
        summary="当前点击潜力较弱，需要强化读者收益。",
    ) == "0"


def test_missing_dimension_score_has_an_explicit_explanation() -> None:
    render_result = _function("render_review_result")
    literals = _string_literals(render_result)

    assert "—" in literals
    assert "本次评审未返回该项评分" in literals
    assert _calls(render_result, "_format_dimension_score")


def test_result_and_settings_have_independent_stable_targets() -> None:
    panel = _function("build_review_jury_panel")
    literals = _string_literals(panel)
    scroll_result = _function("scroll_to_review_result", async_function=True)
    scroll_settings = _function("scroll_to_review_settings", async_function=True)

    assert "editorial-review-result-anchor w-full gap-3 q-mt-md" in literals
    assert "editorial-review-settings-anchor w-full gap-1 q-mt-sm" in literals
    assert _calls(scroll_result, "scroll_dom_target")
    settings_scroll_calls = _calls(scroll_settings, "scroll_dom_target")
    assert len(settings_scroll_calls) == 1
    assert isinstance(settings_scroll_calls[0].args[0], ast.Name)
    assert settings_scroll_calls[0].args[0].id == "settings_anchor_id"
    assert not _calls(scroll_result, "result_card.run_method")
    assert not _calls(scroll_settings, "settings_section.run_method")
    assert _calls(scroll_result, "settings_expansion.set_value") == []


def test_dom_scroll_uses_stable_ids_and_current_client_javascript() -> None:
    panel = _function("build_review_jury_panel")
    scroll_dom_target = _function("scroll_dom_target")
    call_names = {
        _call_name(call)
        for call in ast.walk(scroll_dom_target)
        if isinstance(call, ast.Call)
    }
    literals = _string_literals(scroll_dom_target)

    assert {
        "editorial-review-result-",
        "editorial-review-settings-",
        "editorial-review-comparison-",
    } <= _string_literals(panel)
    assert "owner_client.run_javascript" in call_names
    javascript = "\n".join(literals)
    assert "document.getElementById" in javascript
    assert "scrollIntoView" in javascript
    assert "behavior:'auto'" in javascript
    assert "requestAnimationFrame" in javascript
    assert "setTimeout" in javascript
    assert "80" in javascript
    assert "180" in javascript
    assert not any(name.endswith(".run_method") for name in call_names)


@pytest.mark.parametrize(
    "helper_name",
    [
        "scroll_to_review_result",
        "scroll_to_review_settings",
        "scroll_to_article_comparison",
    ],
)
def test_dom_scroll_helpers_delegate_directly_without_ui_timer(
    helper_name: str,
) -> None:
    helper = _function(helper_name, async_function=True)
    call_names = {
        _call_name(call)
        for call in ast.walk(helper)
        if isinstance(call, ast.Call)
    }

    assert len(_calls(helper, "scroll_dom_target")) == 1
    assert "client_timer" not in call_names
    assert not any(name.endswith(".run_method") for name in call_names)


def test_completed_review_keeps_settings_collapsed_to_a_summary() -> None:
    panel = _function("build_review_jury_panel")
    source = REVIEW_JURY_PANEL.read_text(encoding="utf-8")
    literals = _string_literals(panel)

    assert "调整本次评审设置" in literals
    assert "settings_summary_label" in source
    assert 'value=False' in source[source.index("settings_expansion ="):]


def test_role_and_style_chip_removals_have_specific_chinese_names() -> None:
    source = REVIEW_JURY_PANEL.read_text(encoding="utf-8")
    helper_start = source.index("def _add_accessible_removal_chips")
    helper_end = source.index("\ndef _review_start_action", helper_start)
    helper_source = source[helper_start:helper_end]

    assert "props.opt.label" in helper_source
    assert "props.label" not in helper_source
    assert ':selected="props.selected"' in helper_source
    assert ':tabindex="props.tabindex"' in helper_source
    assert "remove-aria-label" in helper_source
    assert "移除{item_name}：" in helper_source
    assert 'item_name="评审角色"' in source
    assert 'item_name="目标风格"' in source
    assert "props.removeAtIndex(props.index)" in helper_source


def test_issue_actions_match_the_current_resolution_only() -> None:
    """Open risks can be handled; handled risks expose only the reopen action."""

    render_result = _function("render_review_result")
    resolution_guards = [
        node
        for node in ast.walk(render_result)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "resolution"
        and any(isinstance(operator, ast.Eq) for operator in node.test.ops)
        and any(
            isinstance(value, ast.Constant) and value.value == "open"
            for value in node.test.comparators
        )
    ]

    assert len(resolution_guards) == 1
    guard = resolution_guards[0]
    open_literals = {
        text
        for statement in guard.body
        for text in _string_literals(statement)
    }
    handled_literals = {
        text
        for statement in guard.orelse
        for text in _string_literals(statement)
    }
    assert {"我已核实", "保留原文并接受风险"} <= open_literals
    assert "恢复待核实" not in open_literals
    assert "恢复待核实" in handled_literals
    assert "我已核实" not in handled_literals
    assert "保留原文并接受风险" not in handled_literals
    assert any(text.startswith("处理记录：") for text in handled_literals)


def test_issue_resolution_notifies_parent_gate_before_rerendering() -> None:
    resolve_issue = _function("resolve_issue", async_function=True)
    update_calls = _calls(resolve_issue, "notify_review_updated")
    render_calls = _calls(resolve_issue, "render_review_result")

    assert len(update_calls) == 1
    assert len(render_calls) == 1
    assert update_calls[0].lineno < render_calls[0].lineno


def test_review_start_has_running_and_rerun_states() -> None:
    action = _function("_review_start_action")
    literals = _string_literals(action)

    assert {"开始 AI 评审", "AI 评审中", "重新评审"} <= literals
    assert {"running", "rewriting"} <= literals


def test_canceling_rerun_cannot_start_a_review_or_call_the_model() -> None:
    request_start = _function("request_review_start", async_function=True)
    cancel_buttons = [
        call
        for call in _calls(request_start, "ui.button")
        if call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "取消"
    ]

    assert len(cancel_buttons) == 1
    cancel_handler = _keyword(cancel_buttons[0], "on_click")
    assert isinstance(cancel_handler, ast.Attribute)
    assert cancel_handler.attr == "close"
    assert not _calls(request_start, "service.run_editorial_review")
