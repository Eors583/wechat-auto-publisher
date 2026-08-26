from __future__ import annotations

import inspect
from pathlib import Path

from app.ui import desktop
from app.ui.background_activity import build_global_activity_dock
from app.ui.panels import tasks
from app.ui.style_tokens import UI_STYLE_SPEC
from app.ui.styles import APP_CSS

ROOT = Path(__file__).resolve().parents[1]


def test_confirmed_four_entry_information_architecture_and_review_route() -> None:
    source = inspect.getsource(desktop.create_desktop_app)

    for label in ("创作台", "选题雷达", "任务队列", "公众号"):
        assert f'ui.tab("{label}"' in source
    assert 'ui.tab("文章审核", icon="rate_review").classes(' in source
    assert 'ui.tab("飞书机器人", icon="forum").classes(' in source
    assert '"ops-feishu-route-tab"' in source
    assert '"ops-review-route-tab"' in source
    route_css = APP_CSS[APP_CSS.index(".ops-review-route-tab") :]
    assert "display: none !important" in route_css[:300]
    assert "系统就绪" not in source
    assert "公众号配置与后台任务可用" not in source
    for icon in (
        "auto_awesome",
        "radar",
        "format_list_bulleted",
        "campaign",
    ):
        assert f'icon="{icon}"' in source


def test_collapsed_primary_navigation_keeps_accessible_names() -> None:
    source = inspect.getsource(desktop.create_desktop_app)

    for label in ("创作台", "选题雷达", "任务队列", "公众号"):
        assert f'aria-label="{label}" title="{label}"' in source


def test_fullscreen_shell_and_compact_task_rows_use_shared_tokens() -> None:
    layout = UI_STYLE_SPEC["布局"]

    assert layout["layout-sidebar-width"].value == "220px"
    assert layout["layout-topbar-height"].value == "64px"
    assert layout["task-row-height"].value == "68px"
    assert "width: 100vw" in APP_CSS
    assert "height: 100dvh" in APP_CSS
    assert "overflow: hidden" in APP_CSS
    assert "height: var(--ui-task-row-height) !important" in APP_CSS
    assert "align-content: start !important" in APP_CSS
    assert ".ops-task-row-primary-action" in APP_CSS
    assert "width: 84px" in APP_CSS
    assert "flex: 0 0 21px" in APP_CSS


def test_key_business_pages_do_not_use_inline_style_calls() -> None:
    files = (
        "app/ui/desktop.py",
        "app/ui/panels/tasks.py",
        "app/ui/panels/topics.py",
        "app/ui/panels/followed_articles.py",
        "app/ui/panels/settings_hub.py",
        "app/ui/panels/prompts.py",
        "app/ui/panels/review_jury.py",
    )

    for relative in files:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert ".style(" not in source, relative


def test_global_background_activity_is_mounted_once_for_all_pages() -> None:
    desktop_source = inspect.getsource(desktop.create_desktop_app)
    activity_source = inspect.getsource(build_global_activity_dock)

    assert desktop_source.count("build_global_activity_dock(page_state)") == 1
    assert "service.list_batches" in activity_source
    assert "service.list_editorial_reviews" in activity_source
    assert 'ui.label(f"{round(value * 100)}%")' in activity_source
    assert 'ui.link("查看详情"' in activity_source


def test_task_queue_keeps_all_required_visible_operations() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)
    row_source = inspect.getsource(tasks._render_batch_card)

    for label in ("查看后台运行任务", "查看归档"):
        assert f'"{label}"' in source
    assert '"退出归档" if archived_only else "查看归档"' in source
    assert 'batches = [batch for batch in batches if batch.get("archived_at")]' in source
    assert 'ui.label("今日处理顺序")' in source
    assert "ops-queue-workspace" in APP_CSS
    assert "container-type: inline-size" in APP_CSS
    assert "@container (max-width: 720px)" in APP_CSS
    assert ".ops-flow-panel { display: none; }" in APP_CSS
    assert 'runtime["visible_limit"]' in source
    for label in ("打开审核", "写入草稿", "恢复失败任务", "查看进度"):
        assert f'"{label}"' in row_source
    assert '"active": "生成中"' in source
    assert 'status_in.value = "active"' in source
    assert "archived_in.value = show_archived" in source
    assert '"initial_view": "batches"' in inspect.getsource(
        desktop.create_desktop_app
    )
    batch_source = inspect.getsource(tasks._render_batch_card)
    assert "_render_batch_detail_content(" in batch_source
    assert "on_archive=archive_and_close" in batch_source
    assert "on_open_review=open_review_and_close" in batch_source
    assert 'review_runtime["focus_batch_id"] = ""' in batch_source
    assert "dialog.close()" in batch_source
    assert 'classes("ops-task-row-card ops-batch-row-card")' in batch_source
    assert ').classes("ops-task-row-badge")' in batch_source
    assert 'batch.get("generation_usage")' in batch_source
    assert "_generation_usage_text(" in batch_source
    assert 'batch.get("generation_token_usage")' in batch_source
    assert 'ui.label(generation_token_text).classes("ops-task-row-token")' in batch_source
    assert 'f" · {generation_token_text}"' not in batch_source


def test_task_queue_width_chain_and_breakpoints_cannot_push_sidebar_offscreen() -> None:
    workspace_css = APP_CSS[APP_CSS.index(".ops-queue-workspace {") :]
    for containment in ("width: 100%", "min-width: 0", "max-width: 100%"):
        assert containment in workspace_css[:500]

    task_list_css = APP_CSS[APP_CSS.rindex(".ops-task-list {") :]
    for containment in ("width: 100%", "min-width: 0", "max-width: 100%"):
        assert containment in task_list_css[:700]
    assert (
        "grid-auto-rows: minmax(var(--ui-task-row-height), auto)"
        in task_list_css[:700]
    )
    assert "overflow-y: auto !important" in task_list_css[:700]
    assert "overflow-x: hidden !important" in task_list_css[:700]

    narrow_css = APP_CSS[APP_CSS.rindex("@media (max-width: 860px)") :]
    assert (
        ".ops-queue-workspace { grid-template-columns: minmax(0, 1fr); }"
        in narrow_css[:700]
    )
    assert ".ops-flow-panel { display: none; }" in narrow_css[:700]
    assert "minmax(0, 1fr) 230px" not in narrow_css[:700]


def test_task_account_filter_only_contains_real_accounts_and_shows_selection() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)

    assert '"": "全部公众号"' in source
    assert '"__refresh__": "刷新任务"' not in source
    assert 'display-value="全部公众号"' not in source
    assert "account_options = load_account_options()" in source
    assert 'account_in.on("popup-show", refresh_account_options)' in source
    assert "account_in.set_options(" in source
    assert "account_in.on_value_change(reset_and_render)" in source


def test_task_rows_expose_direct_batch_archive_actions() -> None:
    inbox_source = inspect.getsource(tasks._render_inbox_article_card)
    batch_source = inspect.getsource(tasks._render_batch_card)
    confirm_source = inspect.getsource(tasks._open_archive_confirmation)

    for source in (inbox_source, batch_source):
        assert '"归档"' in source
        assert '"archive"' in source
        assert "_open_archive_confirmation(" in source
        assert '"ops-task-row-archive-action"' in source
    assert '"取消归档" if archived else "归档"' in batch_source
    assert "service.archive_batch(batch_id, archived=archived)" in confirm_source
    assert '从“全部批次”中隐藏' in confirm_source
    assert ".ops-task-row-archive-action" in APP_CSS
    assert "width: var(--ui-task-archive-action-width)" in APP_CSS
    assert "var(--ui-task-actions-column)" in APP_CSS
    assert "font-size: 0 !important" in APP_CSS


def test_review_workbench_supports_article_navigation_and_background_rewrite() -> None:
    source = inspect.getsource(tasks.open_review_workbench)

    assert '"上一篇"' in source
    assert '"下一篇"' in source
    assert "open_sibling" in source
    assert "start_background_review" in source
    assert "build_review_jury_panel" in source
    assert "历史版本" in source


def test_review_page_contains_long_content_and_exposes_failure_reason() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert 'latest_review.get("error")' in source
    assert 'ui.label("AI 评审失败原因")' in source
    assert "ops-review-failure-status" in source
    assert "aria-label=查看AI评审失败原因" in source
    assert ".ops-review-editor-grid > *" in APP_CSS
    assert ".ops-title-candidates .q-radio__label" in APP_CSS
    assert "overflow-y: auto" in APP_CSS


def test_ai_review_panel_only_appears_in_final_preview() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert 'as review_layout' in source
    assert 'as review_side' in source
    assert 'event.value == "成品预览"' in source
    assert "review_side.set_visibility(show_ai_review)" in source
    assert 'remove="ops-review-layout--single" if show_ai_review else None' in source
    assert 'add=None if show_ai_review else "ops-review-layout--single"' in source
    assert (
        ".ops-review-layout.ops-review-layout--single { "
        "grid-template-columns: minmax(0, 1fr); }"
        in APP_CSS
    )


def test_candidate_comparison_replaces_dialog_and_review_conclusion() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert '"改写前 · 原文"' in source
    assert '"改写后 · AI 候选稿"' in source
    assert '"选择文章版本"' in source
    assert '"使用原文"' in source
    assert '"使用改写后文章"' in source
    assert "_preview_editorial_review_application" in source
    assert "prepare_preview_html(" in source
    assert "html_content," in source
    assert "review_body.set_visibility(not candidate_ready)" in source
    assert "comparison_dialog" not in source
    assert "ops-review-comparison-dialog" not in source
    assert ".ops-inline-comparison" in APP_CSS
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in APP_CSS
    assert ".ops-inline-comparison-canvas iframe" in APP_CSS
    assert ".ops-version-choice-actions .q-btn" in APP_CSS
    assert '"定位下一个改写区域"' not in source
    assert "build_rewrite_regions(" not in source
    assert "rewrite_region_navigation_script(" not in source


def test_failed_ai_review_offers_rerun_instead_of_rewrite() -> None:
    source = inspect.getsource(tasks.build_review_page)

    failed_branch = source.index('current_review_status == "failed"')
    rewrite_action = source.index('"按已选意见后台改写"')
    rewrite_gate = source.rfind(
        "if latest_review and review_status not in", 0, rewrite_action
    )
    assert '"failed",' in source[rewrite_gate:rewrite_action]
    assert '"重新评审"' in source[failed_branch:]
    assert "on_click=start_review_background" in source[failed_branch:]


def test_review_issues_show_problem_and_suggestion_before_selection() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert 'issue.get("problem")' in source
    assert 'issue.get("suggestion")' in source
    assert 'issue.get("category")' in source
    assert 'issue.get("location")' in source
    assert 'issue.get("can_auto_apply")' in source
    assert 'f"问题：{problem}"' in source
    assert 'f"建议：{suggestion}"' in source
    assert 'manual_review = issue_id and not can_auto_apply' in source
    assert 'selected_issue_ids: set[str] = set()' in source
    assert '"AI 联网核实项"' in source
    assert '"按核实结果纳入后台改写"' in source
    assert 'issue.get("evidence_sources")' in source
    assert '"人工核实项"' in source
    assert "此项不能交给 AI 自动改写，请在核实后选择处理结果。" in source
    assert '"已人工核实"' in source
    assert '"保留原文并接受风险"' in source
    assert ".ops-issue-content" in APP_CSS
    assert ".ops-issue-actions" in APP_CSS
    assert ".ops-issue-sources" in APP_CSS
    assert "overflow-wrap: anywhere" in APP_CSS


def test_review_action_is_replaced_by_persisted_progress() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert "show_value=False" in source
    assert 'size="20px"' in source
    assert "absolute-center background-activity-progress-label" in source
    assert 'classes("ops-activity-percent")' not in source
    assert 'stage: str = "正在创建评审任务"' in source
    assert "review_progress_stage.set_text(stage)" in source
    assert "review_action_button.set_visibility(False)" in source
    assert "review_progress_column.set_visibility(True)" in source
    assert "ui.expansion(" in source
    assert '"后台任务 · 运行中"' in source
    assert "review_progress_box.set_visibility(True)" in source
    assert "review_progress_host.set_visibility(True)" in source
    assert "ops-review-background-actions" in source
    assert 'classes("ops-panel ops-review-job-panel")' not in source
    assert "service.list_editorial_reviews" in source
    assert "editorial_review_progress(refreshed)" in source
    assert "review_progress_bar.set_value" in source
    assert "client_timer(" in source
    assert "owner_client.safe_invoke" in source


def test_background_rewrite_action_precedes_decisions_and_starts_progress() -> None:
    source = inspect.getsource(tasks.build_review_page)

    rewrite = source.index('"按已选意见后台改写"')
    needs_changes = source.index('"退回修改"', rewrite)
    confirm = source.index('"确认通过"', needs_changes)
    assert rewrite < needs_changes < confirm
    assert 'classes("ops-review-rewrite-action")' in source
    assert "start_review_progress(\"正在根据已选意见生成改写候选稿\")" in source
    assert '"当前没有运行中的后台任务"' not in source
    assert ".ops-review-rewrite-action" in APP_CSS


def test_completed_review_can_be_rerun_and_footer_actions_explain_results() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert 'elif current_review_status not in {' in source
    assert '"重新评审"' in source
    assert 'set_button_loading(button, True, "正在确认…")' in source
    assert '"确认文章失败：' in source
    assert '"文章已确认通过，可进入写入草稿流程"' in source
    assert '"将文章标记为需要修改，并保留在待处理列表"' in source
    assert '"ops-review-confirm-hint"' in source
    assert ".ops-review-confirm-hint" in APP_CSS


def test_rerun_hides_stale_result_and_score_ring_uses_real_percentage() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert "review_body.set_visibility(False)" in source
    assert "review_status_indicator.set_visibility(False)" in source
    assert "ui.circular_progress(" in source
    assert "max=100" in source
    assert 'size="58px"' in source
    assert '"track-color=blue-1 thickness=0.18"' in source
    assert "border-top-color" not in APP_CSS


def test_feature_mapping_document_covers_every_confirmed_page() -> None:
    mapping = (ROOT / "docs" / "新版前端功能映射与验收清单.md").read_text(
        encoding="utf-8"
    )

    for heading in (
        "全局框架",
        "创作台",
        "选题雷达",
        "任务队列",
        "公众号与配置中心",
        "文章审核",
    ):
        assert heading in mapping
    assert "原功能" in mapping
    assert "新页面入口" in mapping
    assert "复用接口 / 实现" in mapping
    assert "浏览器验证用例" in mapping


def test_account_center_has_real_capabilities_and_configuration_versions() -> None:
    source = inspect.getsource(desktop._build_accounts_panel)

    for label in ("可生成", "可写草稿", "仅生成", "草稿能力待检测"):
        assert f'"{label}"' in source
    assert "get_wechat_connection_health" in source
    assert "preflight_accounts" in source
    assert '"配置版本"' in source
    assert '"保存当前版本"' in source
    assert '"恢复此版本"' in source
    assert '"恢复前自动备份"' in source


def test_account_configuration_fields_are_vertically_centered() -> None:
    assert ".ops-config-form .q-field__control-container" in APP_CSS
    assert "padding-top: 0 !important" in APP_CSS
    assert "height: var(--ui-control-height-field) !important" in APP_CSS
    assert ".ops-config-form div.q-field__native" in APP_CSS
    assert "align-items: center" in APP_CSS
    assert ".ops-config-field > .q-field" in APP_CSS
    assert ".ops-config-field .q-select .q-field__native > span" in APP_CSS
    assert "text-overflow: ellipsis" in APP_CSS


def test_default_model_select_can_open_the_custom_model_editor() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert '"＋ 添加自定义模型"' in source
    assert "ADD_CUSTOM_MODEL_VALUE" in source
    assert "open_custom_model_editor()" in source
    assert 'render_panel=False' in source
    assert ".ops-dialog-model-editor" in APP_CSS
    assert ".ops-model-kind-toggle" in APP_CSS


def test_default_model_select_marks_source_and_only_custom_models_are_deletable() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert "ops-model-option-badge-official" in source
    assert "ops-model-option-badge-custom" in source
    assert "v-if=\"props.opt.label.startsWith('自定义 · ')\"" in source
    assert "ops-model-option-edit" in source
    assert "ops-model-option-delete" in source
    assert source.index("ops-model-option-edit") < source.index(
        "ops-model-option-delete"
    )
    assert 'with model_select.add_slot("append")' not in source
    assert "$root.$refs.r0.$emit" in source
    assert "open_custom_model_editor(edit_model_id)" in source
    assert "owned_custom_model_record(option)" in source
    assert "官方模型由后台维护，不能在这里删除" in source
    assert "state.db.delete_ai_model(delete_model_id)" in source
    assert ".ops-model-option-actions" in APP_CSS
    assert ".ops-model-option-edit" in APP_CSS
    assert ".ops-model-option-delete" in APP_CSS


def test_account_layout_editor_can_import_a_public_wechat_article() -> None:
    source = inspect.getsource(desktop._build_accounts_panel)

    assert '"从微信文章获取排版"' in source
    assert "fetch_wechat_article_layout(" in source
    assert "parse_wechat_article_layout(" in source
    assert '"文章 HTML（可选）"' in source
    assert "登录态不是公开文章解析的必填项。" in source
    assert '"原文排版还原"' in source
    assert '"应用后的生成效果"' in source
    assert '"导入微信排版前备份"' in source
    assert '"应用到当前公众号"' in source
    assert '"title.bold": "一级标题加粗"' in source
    assert '"title.spacing_before": "一级标题前间距"' in source
    assert '"argument.bold": "论点加粗"' in source
    assert '"quote.spacing_after": "引用后间距"' in source
    assert '"list.marker_color": "列表标记颜色"' in source
    assert ".wechat-layout-import-previews" in APP_CSS
    assert ".wechat-layout-html-input textarea.q-field__native" in APP_CSS


def test_account_actions_menu_belongs_to_each_directory_item() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert source.count('icon="more_horiz"') == 1
    assert '"ops-account-directory-more"' in source
    assert "f\"flat round dense aria-label={item_name}更多操作\"" in source
    assert source.index('"ops-account-directory-more"') < source.index(
        '"ops-panel ops-account-config"'
    )
    assert ".ops-account-directory-more" in APP_CSS


def test_account_directory_badges_stay_inside_variable_height_cards() -> None:
    assert "grid-template-rows: minmax(38px, auto) auto" in APP_CSS
    assert ".ops-account-directory-item .ops-panel-subtitle" in APP_CSS
    assert "text-overflow: ellipsis" in APP_CSS
    assert ".ops-account-directory-status" in APP_CSS
    assert "flex-wrap: wrap" in APP_CSS
    assert ".ops-account-directory-status .ops-badge" in APP_CSS


def test_account_and_creation_directories_show_every_account_with_inner_scroll() -> None:
    account_source = inspect.getsource(desktop._render_account_config_workspace)
    create_source = inspect.getsource(desktop._build_wizard)

    assert "visible_accounts = all_accounts" in account_source
    assert "ops-account-pagination" not in account_source
    assert "for account_id, account_label in account_items:" in create_source
    assert "account_items[offset : offset + 2]" not in create_source
    assert "上一组目标公众号" not in create_source
    assert ".ops-account-directory-list" in APP_CSS
    assert ".ops-create-account-list" in APP_CSS
    assert "overflow-y: auto" in APP_CSS
    assert "flex: 1 1 auto" in APP_CSS
    assert "max-height: none" in APP_CSS
    assert "grid-auto-rows: max-content" in APP_CSS
    assert "align-content: start" in APP_CSS


def test_create_workbench_contains_long_text_and_dynamic_progress_in_flow() -> None:
    source = inspect.getsource(desktop._build_wizard)
    app_source = inspect.getsource(desktop.create_desktop_app)
    assert '"ops-panel ops-create-workflow-panel"' in source
    assert "source_section.move(workflow_panel)" in source
    assert "account_section.move(workflow_panel)" in source
    assert "build_overview_cards" not in source
    assert "今天先处理这些" not in source
    assert "最近任务" not in source
    assert "今天准备做什么内容" not in app_source
    assert "topbar.set_visibility" in app_source

    assert 'grid-template-areas: "workflow"' in APP_CSS
    assert '"workflow priority"' not in APP_CSS
    assert '"source priority"' not in APP_CSS
    assert '"account priority"' not in APP_CSS
    assert "margin: 295px 0 0 !important" not in APP_CSS
    assert "margin-top: 236px !important" not in APP_CSS

    workflow_css = APP_CSS[APP_CSS.index(".ops-create-workflow-panel {") :]
    assert "grid-area: workflow" in workflow_css[:500]
    assert "overflow-y: auto" in workflow_css[:500]
    assert "grid-template-rows: max-content max-content" in workflow_css[:500]

    source_body_css = APP_CSS[APP_CSS.index(".ops-create-form-body {") :]
    account_list_css = APP_CSS[APP_CSS.index(".ops-create-account-list {") :]
    assert "overflow: visible" in source_body_css[:400]
    assert "height: auto" in account_list_css[:500]
    assert "max-height: none" in account_list_css[:500]
    assert "overflow: visible" in account_list_css[:500]

    textarea_css = APP_CSS[
        APP_CSS.index(
            ".ops-create-source-section .article-body-input textarea.q-field__native"
        ) :
    ]
    assert "height: 100% !important" in textarea_css[:400]
    assert "max-height: 132px" in textarea_css[:400]
    assert "overflow-y: auto !important" in textarea_css[:400]

    status_css = APP_CSS[APP_CSS.index(".ops-create-status-row {") :]
    action_css = APP_CSS[APP_CSS.index(".ops-create-action-row {") :]
    assert "position: static" in status_css[:300]
    assert "position: static" in action_css[:400]
    assert "flex-wrap: wrap" in action_css[:400]


def test_create_log_keeps_multiline_height_and_cannot_overlap_actions() -> None:
    fixed_field_css = (
        ".ops-create-account-section "
        ".q-field:not(.ops-create-log-area) .q-field__control"
    )
    assert fixed_field_css in APP_CSS

    log_css = APP_CSS[APP_CSS.index(".ops-create-log-area {") :]
    assert ".ops-create-log-area .q-field__control" in log_css[:1200]
    assert "height: auto !important" in log_css[:1200]
    assert "min-height: calc(5 * 1.5em) !important" in log_css[:1600]
    assert "overflow-y: auto !important" in log_css[:1600]
    assert "overflow-wrap: anywhere" in log_css[:1600]
    assert "white-space: pre-wrap" in log_css[:1600]

    containment_css = APP_CSS[APP_CSS.index(".ops-workbench-shell :is(") :]
    for generated_part in (
        ".q-field__control-container",
        ".q-field__native",
        ".q-item__label",
        ".q-btn__content",
        ".q-chip__content",
    ):
        assert generated_part in containment_css[:800]
    assert "min-width: 0" in containment_css[:800]
    assert "max-width: 100%" in containment_css[:800]


def test_personal_model_settings_live_in_account_configuration_only() -> None:
    app_source = inspect.getsource(desktop.create_desktop_app)
    account_source = inspect.getsource(desktop._render_account_config_workspace)

    assert 'tab_accounts = ui.tab("公众号"' in app_source
    assert 'ui.tab("模型配置"' not in app_source
    assert "tab_models" not in app_source
    assert "mount_models" not in app_source
    assert 'ui.label("默认模型")' in account_source
    assert "ADD_CUSTOM_MODEL_VALUE" in account_source
    assert "build_models_panel(" in account_source
    assert "render_panel=False" in account_source
    assert 'edit=request_model_edit' in account_source
    assert 'delete=request_model_delete' in account_source


def test_custom_prompt_template_manager_is_available_from_account_configuration() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert 'ui.label("管理自定义提示词")' in source
    assert "build_prompt_templates_panel(" in source
    assert "on_templates_change=render_prompt_binding" in source
    assert '"保存当前公众号提示词"' in source


def test_account_configuration_exposes_wechat_command_entry() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert '"微信指挥"' in source
    assert '"在微信中发送链接和改写指令"' in source
    assert "open_wechat_command_dialog(state, account_id)" in source
