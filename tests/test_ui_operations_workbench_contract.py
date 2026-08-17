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
    assert '"ops-review-route-tab"' in source
    route_css = APP_CSS[APP_CSS.index(".ops-review-route-tab") :]
    assert "display: none !important" in route_css[:300]
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
    row_source = inspect.getsource(tasks._render_inbox_article_card)

    for label in ("查看后台运行任务", "查看归档"):
        assert f'"{label}"' in source
    assert 'ui.label("今日处理顺序")' in source
    assert "ops-queue-workspace" in APP_CSS
    assert 'runtime["visible_limit"]' in source
    for label in ("打开审核", "写入草稿", "恢复失败任务", "查看进度"):
        assert f'"{label}"' in row_source
    assert '"active": "生成中"' in source
    assert 'status_in.value = "active"' in source
    assert "archived_in.value = True" in source
    assert '"initial_view": "inbox"' in inspect.getsource(
        desktop.create_desktop_app
    )
    batch_source = inspect.getsource(tasks._render_batch_card)
    assert "_render_batch_detail_content(" in batch_source
    assert 'classes("ops-task-row-card ops-batch-row-card")' in batch_source


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
    assert '"ops-panel ops-create-workflow-panel"' in source
    assert "source_section.move(workflow_panel)" in source
    assert "account_section.move(workflow_panel)" in source

    assert '"workflow priority"' in APP_CSS
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


def test_authenticated_user_can_open_personal_model_settings() -> None:
    source = inspect.getsource(desktop.create_desktop_app)

    assert 'ui.label("我的大模型")' in source
    assert 'ui.menu_item("我的大模型", on_click=open_user_models)' in source
    assert "build_models_panel(page_state, purpose=\"text\")" in source
    assert "配置只属于当前登录账号" in source


def test_custom_prompt_template_manager_is_available_from_account_configuration() -> None:
    source = inspect.getsource(desktop._render_account_config_workspace)

    assert 'ui.label("管理自定义提示词")' in source
    assert "build_prompt_templates_panel(" in source
    assert "on_templates_change=render_prompt_binding" in source
    assert '"保存当前公众号提示词"' in source
