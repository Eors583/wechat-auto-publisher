from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from app.services import FollowedContentService, TopicSourceService
from app.services.followed_content import (
    FETCH_METHODS,
    FOLLOWED_PUBLICATION_METHODS,
)
from app.services.topic_sources import SOURCE_TYPES
from app.ui.interaction_feedback import (
    attach_interaction_feedback,
    hide_interaction_feedback,
)
from app.ui.panels.followed_articles import open_followed_articles_dialog
from app.ui.state import AppState, set_button_loading

TOPIC_CENTER_TABS = ("我的关注", "选题内容", "来源管理")


def build_topic_center(
    state: AppState,
    workspace_tabs: Any,
    tab_wizard: Any,
    *,
    initial_action: str = "",
) -> None:
    """Topic discovery UI; all mutations go through independent services."""

    topic_service = TopicSourceService(state.db, state.config)
    follow_service = FollowedContentService(state.db, state.config)
    inner_tabs = ui.tabs().classes(
        "workspace-tabs w-full ops-topic-secondary-tabs"
    ).props(
        "dense align=left indicator-color=teal-9 active-color=teal-10"
    )
    with inner_tabs:
        accounts_tab = ui.tab(TOPIC_CENTER_TABS[0]).classes(
            "ops-topic-primary-tab"
        )
        hot_tab = ui.tab(TOPIC_CENTER_TABS[1])
        sources_tab = ui.tab(TOPIC_CENTER_TABS[2])
    initial_tab = accounts_tab
    with ui.tab_panels(inner_tabs, value=initial_tab).classes(
        "w-full bg-transparent ops-topic-secondary-panels"
    ):
        with ui.tab_panel(accounts_tab):
            accounts_host = ui.column().classes("w-full ops-topic-detail-view")
        with ui.tab_panel(hot_tab):
            hot_host = ui.column().classes("w-full ops-topic-primary-view")
        with ui.tab_panel(sources_tab):
            sources_host = ui.column().classes("w-full ops-topic-detail-view")

    inner_mounts = {
        str(hot_tab.props["name"]): (
            hot_host,
            lambda: _build_hot_topics(
                state,
                topic_service,
                workspace_tabs,
                tab_wizard,
            ),
        ),
        str(accounts_tab.props["name"]): (
            accounts_host,
            lambda: _build_followed_accounts(
                state,
                follow_service,
                workspace_tabs,
                tab_wizard,
                open_backend_config=initial_action == "wechat_backend",
            ),
        ),
        str(sources_tab.props["name"]): (
            sources_host,
            lambda: _build_sources(topic_service),
        ),
    }
    mounted_inner_tabs: set[str] = set()
    scheduled_inner_tabs: set[str] = set()

    def mount_inner_tab(tab: Any) -> None:
        tab_name = str(tab.props["name"] if hasattr(tab, "props") else tab)
        if tab_name in mounted_inner_tabs:
            hide_interaction_feedback()
            return
        host, builder = inner_mounts[tab_name]
        host.clear()
        with host:
            builder()
        mounted_inner_tabs.add(tab_name)
        scheduled_inner_tabs.discard(tab_name)
        hide_interaction_feedback()

    def schedule_inner_tab(tab: Any) -> None:
        tab_name = str(tab.props["name"] if hasattr(tab, "props") else tab)
        if tab_name in mounted_inner_tabs or tab_name in scheduled_inner_tabs:
            return
        scheduled_inner_tabs.add(tab_name)
        mount_inner_tab(tab)

    schedule_inner_tab(initial_tab)
    attach_interaction_feedback(
        inner_tabs,
        "正在切换选题页面",
        event="update:model-value",
    )
    inner_tabs.on_value_change(lambda event: schedule_inner_tab(event.value))


def _build_hot_topics(
    state: AppState,
    service: TopicSourceService,
    workspace_tabs: Any,
    tab_wizard: Any,
) -> None:
    sources = service.list_sources(enabled_only=True)
    source_options = {str(item["id"]): str(item["name"]) for item in sources}
    hot_source_ids = [
        str(item["id"])
        for item in sources
        if str(item.get("source_type") or "")
        not in {"manual", "followed_accounts"}
    ]
    manual_source_ids = [
        str(item["id"])
        for item in sources
        if str(item.get("source_type") or "") == "manual"
    ]
    view_mode = ui.toggle(
        {
            "hot": "热点",
            "favorite": "收藏选题",
            "manual": "手动选题",
            "history": "历史文章",
        },
        value="hot",
    ).classes("ops-segment ops-topic-view-segment").props(
        "dense no-caps unelevated toggle-color=white toggle-text-color=dark"
    )
    with ui.row().classes("w-full items-end gap-3 ops-topic-toolbar"):
        source_filter = ui.select(
            source_options,
            value=hot_source_ids,
            multiple=True,
        ).classes("col ops-topic-source-filter").props(
            'outlined dense options-dense hide-bottom-space '
            'display-value="全部来源"'
        )
        days = ui.select(
            {1: "今天", 3: "最近3天", 7: "最近7天", 30: "最近30天"},
            value=7,
        ).classes("w-40 ops-topic-days-filter").props(
            "outlined dense options-dense hide-bottom-space"
        )
        keyword = ui.input(
            placeholder="搜索标题、摘要或关键词",
        ).classes("w-80 ops-topic-search").props(
            "outlined dense clearable hide-bottom-space"
        )
        unused = ui.switch(value=False).classes("ops-hidden-control")
        unused_btn = ui.button(
            "仅未使用",
            on_click=lambda: unused.set_value(not bool(unused.value)),
        ).classes("ops-topic-unused-filter").props(
            "outline dense no-caps aria-pressed=false"
        )
    with ui.row().classes("w-full items-center gap-2 ops-topic-actions"):
        refresh_btn = ui.button(
            "刷新所选来源", icon="refresh"
        ).classes("ops-topic-heading-action ops-topic-heading-refresh").props(
            "unelevated color=primary no-caps"
        )
        query_btn = ui.button(
            "搜索热点", icon="search"
        ).classes("ops-topic-heading-action ops-topic-search-action").props(
            "unelevated color=primary no-caps"
        )
        manual_btn = ui.button(
            "添加手动选题", icon="add"
        ).classes("ops-topic-heading-action ops-topic-manual-action").props(
            "unelevated color=primary no-caps"
        )
    ui.label(
        "新闻搜索会直接使用该关键词；RSS、微博和百度热榜会拉取后筛选；自定义 API 可配置 {keyword} 或关键词参数。"
    ).classes("muted ops-topic-helper")
    result_host = ui.column().classes("w-full ops-topic-results")
    result_runtime = {"page": 1, "page_size": 6}
    source_operation = {"busy": False}

    def sync_heading_action() -> None:
        mode = str(view_mode.value or "hot")
        has_keyword = bool(str(keyword.value or "").strip())
        manual_btn.set_visibility(mode == "manual")
        query_btn.set_visibility(mode != "manual" and has_keyword)
        refresh_btn.set_visibility(mode != "manual" and not has_keyword)

    def render() -> None:
        mode = str(view_mode.value or "hot")
        sync_heading_action()
        selected_source_ids = list(source_filter.value or [])
        query_days = int(days.value or 7)
        favorite_only = False
        if mode == "favorite":
            selected_source_ids = list(source_options)
            query_days = 365
            favorite_only = True
        elif mode == "manual":
            selected_source_ids = manual_source_ids
            query_days = 365
        elif mode == "history":
            selected_source_ids = list(source_options)
            query_days = 365
        page_result = service.paginate_topics(
            source_ids=selected_source_ids,
            days=query_days,
            keyword=str(keyword.value or ""),
            favorite_only=favorite_only,
            unused_only=bool(unused.value) if mode == "hot" else False,
            used_only=mode == "history",
            page=int(result_runtime["page"]),
            page_size=int(result_runtime["page_size"]),
        )
        items = list(page_result["items"])
        total = int(page_result["total"])
        current_page = int(page_result["page"])
        page_count = int(page_result["page_count"])
        result_runtime["page"] = current_page
        account_options = state.account_options()
        target_account_ids = list(account_options)
        target_account_count = len(target_account_ids)
        result_host.clear()
        with result_host:
            mode_labels = {
                "hot": "热点",
                "favorite": "收藏",
                "manual": "手动选题",
                "history": "历史使用记录",
            }
            ui.label(
                f'{mode_labels.get(mode, "选题")} · 共 {total} 条'
            ).classes("muted ops-topic-result-count")
            if not total:
                empty_messages = {
                    "favorite": "还没有收藏选题；可返回热点列表收藏感兴趣的内容。",
                    "manual": "还没有手动选题；点击右上角“添加手动选题”创建。",
                    "history": "还没有已使用的选题；从选题卡片带去创作后会保留记录。",
                }
                ui.label(
                    empty_messages.get(
                        mode,
                        "没有符合条件的选题；点击右上角“刷新所选来源”获取最新内容。",
                    )
                ).classes("muted ops-topic-empty")
                return
            with ui.element("div").classes("ops-topic-table"):
                with ui.element("div").classes("ops-topic-table-head"):
                    ui.label("来源与状态")
                    ui.label("选题标题")
                    ui.label("摘要与时间")
                    ui.label("操作")
                with ui.element("div").classes("ops-topic-table-body"):
                    for item in items:
                        with ui.element("article").classes("ops-topic-card"):
                            _render_topic_card(
                                item,
                                state=state,
                                service=service,
                                workspace_tabs=workspace_tabs,
                                tab_wizard=tab_wizard,
                                render=render,
                                target_account_count=target_account_count,
                                target_account_ids=target_account_ids,
                            )
            with ui.row().classes("ops-topic-pagination"):
                ui.label(
                    f"第 {current_page} / {page_count} 页 · 每页 {result_runtime['page_size']} 条"
                ).classes("ops-topic-page-summary")
                pagination = ui.pagination(
                    min=1,
                    max=page_count,
                    value=current_page,
                    direction_links=True,
                ).props(
                    "color=primary active-color=primary max-pages=7"
                    + (" boundary-links" if page_count > 7 else "")
                )

                def change_page(event: Any) -> None:
                    result_runtime["page"] = int(event.value or 1)
                    render()
                    hide_interaction_feedback()

                attach_interaction_feedback(
                    pagination,
                    "正在加载选题列表",
                    event="update:model-value",
                )
                pagination.on_value_change(change_page)

    async def refresh_selected() -> None:
        if source_operation["busy"]:
            ui.notify("选题来源正在处理中，请稍候", type="info")
            return
        selected = list(source_filter.value or [])
        if not selected:
            ui.notify("请至少选择一个来源", type="warning")
            return
        source_operation["busy"] = True
        query_btn.disable()
        set_button_loading(refresh_btn, True, "正在刷新选题来源…")
        try:
            report = await run.io_bound(lambda: service.refresh(selected))
            failures = [row for row in report["sources"] if row.get("error")]
            if failures:
                ui.notify(
                    f'刷新完成，获取 {report["total"]} 条内容；以下来源暂时不可用：'
                    + "；".join(f'{row["name"]}：{row["error"]}' for row in failures),
                    type="warning",
                    timeout=12000,
                )
            elif report["total"]:
                ui.notify(f'刷新完成，获取 {report["total"]} 条内容', type="positive")
            else:
                ui.notify("刷新完成，暂时没有获取到新选题", type="info")
            result_runtime["page"] = 1
            render()
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"刷新失败：{exc}", type="negative")
        finally:
            set_button_loading(refresh_btn, False)
            query_btn.enable()
            source_operation["busy"] = False

    async def search_selected() -> None:
        if source_operation["busy"]:
            ui.notify("选题来源正在处理中，请稍候", type="info")
            return
        selected = list(source_filter.value or [])
        search_keyword = str(keyword.value or "").strip()
        if not search_keyword:
            ui.notify("请先输入热点关键词", type="warning")
            return
        if not selected:
            ui.notify("请至少选择一个来源", type="warning")
            return
        source_operation["busy"] = True
        refresh_btn.disable()
        set_button_loading(query_btn, True, f"正在多来源搜索“{search_keyword}”…")
        try:
            report = await run.io_bound(
                lambda: service.search(
                    search_keyword,
                    selected,
                    days=int(days.value or 7),
                )
            )
            failures = [row for row in report["sources"] if row.get("error")]
            detail = "；".join(
                f'{row["name"]} {row["count"]}条'
                for row in report["sources"]
                if not row.get("error")
            )
            if failures:
                ui.notify(
                    f'搜索完成，共找到 {report["total"]} 条；以下来源暂时不可用：'
                    + "；".join(f'{row["name"]}：{row["error"]}' for row in failures),
                    type="warning",
                    timeout=12000,
                )
            elif report["total"]:
                ui.notify(
                    f'搜索完成，共找到 {report["total"]} 条。{detail}',
                    type="positive",
                    timeout=8000,
                )
            else:
                ui.notify(
                    "搜索完成，当前来源暂未找到匹配选题，可以更换关键词或扩大日期范围",
                    type="info",
                    timeout=8000,
                )
            result_runtime["page"] = 1
            render()
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"关键词搜索失败：{exc}", type="negative")
        finally:
            set_button_loading(query_btn, False)
            refresh_btn.enable()
            source_operation["busy"] = False

    def add_manual() -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full ops-dialog-md"):
            ui.label("添加手动选题").classes("text-h6 text-weight-bold")
            title = ui.input("选题标题").classes("w-full").props("outlined stack-label")
            url = ui.input("参考链接（可选）").classes("w-full").props("outlined stack-label")
            summary = ui.textarea("补充说明（可选）").classes("w-full").props("outlined rows=3")

            def save() -> None:
                try:
                    service.add_manual_topic(str(title.value or ""), url=str(url.value or ""), summary=str(summary.value or ""))
                    dialog.close()
                    render()
                    ui.notify("手动选题已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("保存", on_click=save).props("unelevated color=teal-9 no-caps")
        dialog.open()

    refresh_btn.on_click(refresh_selected)
    query_btn.on_click(search_selected)
    keyword.on("keydown.enter", search_selected)
    manual_btn.on_click(add_manual)
    def change_view(_: Any = None) -> None:
        result_runtime["page"] = 1
        render()
        hide_interaction_feedback()

    attach_interaction_feedback(
        view_mode,
        "正在加载选题列表",
        event="update:model-value",
    )
    attach_interaction_feedback(
        days,
        "正在按日期筛选选题",
        event="update:model-value",
    )
    attach_interaction_feedback(
        source_filter,
        "正在按来源筛选选题",
        event="update:model-value",
    )
    attach_interaction_feedback(unused_btn, "正在筛选未使用选题")
    view_mode.on_value_change(change_view)
    days.on_value_change(change_view)
    source_filter.on_value_change(change_view)
    keyword.on_value_change(lambda _event: sync_heading_action())

    def change_unused(event: Any) -> None:
        unused_btn.props(
            f"aria-pressed={'true' if bool(event.value) else 'false'}"
        )
        change_view()

    unused.on_value_change(change_unused)
    render()


def _render_topic_card(
    item: dict[str, Any],
    *,
    state: AppState,
    service: TopicSourceService,
    workspace_tabs: Any,
    tab_wizard: Any,
    render: Callable[[], None],
    target_account_count: int,
    target_account_ids: list[str],
) -> None:
    """Render one topic using the approved compact radar-card hierarchy."""

    published = str(item.get("published_at") or "")[:10]
    source_name = str(item.get("source_name") or "选题来源")
    source_type = SOURCE_TYPES.get(str(item.get("source_type") or ""), "")
    with ui.row().classes("ops-topic-card-meta"):
        ui.badge(source_name[:12]).classes("ops-badge ops-badge-green")
        ui.badge("已使用" if item.get("used") else "未使用").classes(
            "ops-badge" if item.get("used") else "ops-badge ops-badge-warm"
        )
    ui.label(str(item.get("title") or "未命名选题")).classes(
        "ops-topic-card-title"
    )
    detail = " · ".join(value for value in (source_name, published, source_type) if value)
    summary = str(item.get("summary") or "").strip()
    ui.label(
        f"{detail}。{summary[:110]}" if summary else detail
    ).classes("ops-topic-card-summary")
    with ui.row().classes("ops-topic-card-actions"):
        ui.button(
            "取消收藏" if item.get("favorite") else "收藏",
            on_click=lambda _=None, row=dict(item): (
                service.update_topic_state(
                    str(row["id"]),
                    favorite=not bool(row.get("favorite")),
                ),
                render(),
            ),
        ).props("flat dense color=primary no-caps")
        if item.get("url"):
            ui.link("原文", str(item["url"]), new_tab=True).classes(
                "ops-topic-source-link"
            ).props("aria-label=查看原文")
        ui.space()
        ui.button(
            "去创作",
            on_click=lambda _=None, row=dict(item): _queue_for_wizard(
                state, workspace_tabs, tab_wizard, row
            ),
        ).props("unelevated dense color=primary no-caps aria-label=带去创作")
        with ui.button(icon="more_horiz").props(
            "flat round dense color=grey-8 aria-label=更多选题操作"
        ):
            with ui.menu():
                ui.menu_item(
                    f"直接生成（{target_account_count} 个公众号）",
                    on_click=lambda _=None,
                    row=dict(item),
                    account_ids=list(target_account_ids): _queue_for_wizard(
                        state,
                        workspace_tabs,
                        tab_wizard,
                        row,
                        auto_start=True,
                        account_ids=account_ids,
                        topic_item_id=str(row["id"]),
                    ),
                )


def _build_followed_accounts(
    state: AppState,
    service: FollowedContentService,
    workspace_tabs: Any,
    tab_wizard: Any,
    *,
    open_backend_config: bool = False,
) -> None:
    official_options = {
        str(item["id"]): str(item["name"])
        for item in service.db.list_official_accounts()
    }
    article_refresh_points = service.article_refresh_points()
    refresh_price = f"{article_refresh_points}积分"
    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-0"):
            ui.label("关注公众号").classes("text-h5 text-weight-bold")
            ui.label(
                "平台极致了数据源或你的公众号后台登录态可用于获取微信原文；"
                f"每次获取一个公众号消耗 {refresh_price}。"
            ).classes("muted")
        with ui.row().classes("items-center gap-2"):
            refresh_all_btn = ui.button(
                f"刷新全部 · 每个公众号{refresh_price}", icon="sync"
            ).props("outline color=teal-9 no-caps")
            import_owned_btn = ui.button("导入自有公众号", icon="cloud_download").props("outline color=teal-9 no-caps")
            add_btn = ui.button("添加关注公众号", icon="add").props("unelevated color=teal-9 no-caps")

    with ui.expansion(
        "更多设置：文章获取数据源",
        icon="admin_panel_settings",
        value=False,
    ).classes("w-full"):
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-start justify-between gap-4"):
                with ui.column().classes("gap-1 col"):
                    ui.label("公众号后台搜索").classes("text-subtitle1 text-weight-bold")
                    ui.label(
                        "使用你自己管理的公众号后台登录态，按公众号名称精确查找其已公开文章。"
                        "该方式不是个人微信 Token，也不是 AppID / AppSecret。"
                    ).classes("muted")
                    backend_status_label = ui.label("").classes("text-caption")
                backend_status_badge = ui.badge("")
            with ui.row().classes("items-center gap-2 q-mt-sm"):
                backend_config_btn = ui.button("配置登录态", icon="key").props("outline dense color=teal-9 no-caps")
                backend_test_btn = ui.button("测试连接", icon="wifi_tethering").props("flat dense color=teal-9 no-caps")
                backend_clear_btn = ui.button("清除登录态", icon="delete_outline").props("flat dense color=red-7 no-caps")
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-start justify-between gap-4"):
                with ui.column().classes("gap-1 col"):
                    ui.label("极致了 API").classes("text-subtitle1 text-weight-bold")
                    ui.label(
                        "由平台管理员统一配置并加密保管，用于选题雷达获取近期公开文章；"
                        "普通用户无需也不能填写 API Key。"
                    ).classes("muted")
                ui.badge("平台统一管理", color="teal-8")

    host = ui.column().classes("w-full gap-3")

    def queue_article(article: dict[str, Any], auto_start: bool) -> None:
        account_ids = list(state.account_options())
        _queue_for_wizard(
            state,
            workspace_tabs,
            tab_wizard,
            article,
            auto_start=auto_start,
            account_ids=account_ids if auto_start else None,
            followed_article_id=str(article.get("id") or ""),
        )

    def open_articles(account_id: str) -> None:
        open_followed_articles_dialog(
            service,
            account_id,
            target_account_count=len(state.account_options()),
            on_queue=queue_article,
            on_configure_backend=backend_config_dialog,
        )

    def refresh_backend_status() -> None:
        settings = service.get_backend_search_settings()
        configured = bool(settings.get("has_token") and settings.get("has_cookie"))
        enabled = bool(settings.get("enabled") and configured)
        backend_status_badge.set_text("已启用" if enabled else ("已配置，未启用" if configured else "未配置"))
        backend_status_badge.props(
            "color=green-7" if enabled else ("color=orange-7" if configured else "color=grey-6")
        )
        session_label = str(settings.get("session_label") or "").strip()
        backend_status_label.set_text(
            (f"会话备注：{session_label}。" if session_label else "")
            + "Token 和 Cookie 使用 Windows 当前用户加密保存，界面不会回显明文。"
        )

    def backend_config_dialog() -> None:
        saved = service.get_backend_search_settings()
        configured = bool(saved.get("has_token") and saved.get("has_cookie"))
        with ui.dialog() as dialog, ui.card().classes(
            "w-full ops-dialog-lg ops-dialog-scroll"
        ):
            ui.label("配置公众号后台搜索").classes("text-h6 text-weight-bold")
            ui.label(
                "不用填写 AppID、AppSecret，也不是个人微信 Token。按下面 4 步操作即可。"
            ).classes("muted q-mb-sm")

            with ui.element("div").classes(
                "w-full q-pa-md rounded-borders bg-teal-1"
            ):
                _backend_login_step(
                    1,
                    "登录微信公众号后台",
                    "用你有权管理公众号的账号登录；进入后台首页后不要退出。",
                )
                ui.link(
                    "打开微信公众平台（mp.weixin.qq.com）",
                    "https://mp.weixin.qq.com/",
                    new_tab=True,
                ).classes("text-teal-9 q-ml-lg")

            _backend_login_step(
                2,
                "复制 Token",
                "登录后复制浏览器地址栏的完整链接，直接粘贴到下面即可；"
                "程序会自动提取 token= 后面的内容。也可以只复制 token 的值。",
            )
            token = ui.input(
                "后台 Token" + ("（已保存，留空表示不修改）" if saved.get("has_token") else ""),
                password=True,
                password_toggle_button=True,
                placeholder="可粘贴完整后台链接，或只粘贴 token= 后面的内容",
            ).classes("w-full").props("outlined stack-label autocomplete=new-password")

            _backend_login_step(
                3,
                "复制 Cookie",
                "按 F12（或 Ctrl+Shift+I）打开开发者工具，选择 Network，刷新后台页面；"
                "点开任意 mp.weixin.qq.com/cgi-bin/ 请求，在 Headers → Request Headers "
                "里找到 Cookie。可复制 Cookie 后面的值，也可复制整段请求头。",
            )
            cookie = ui.input(
                "后台 Cookie" + ("（已保存，留空表示不修改）" if saved.get("has_cookie") else ""),
                password=True,
                password_toggle_button=True,
                placeholder="例如：wxuin=...; data_bizuin=...; bizuin=...; ...",
            ).classes("w-full").props("outlined stack-label autocomplete=new-password")

            with ui.expansion(
                "Network 里找不到 Cookie？点这里查看",
                icon="help_outline",
                value=False,
            ).classes("w-full bg-grey-1 rounded-borders"):
                ui.label(
                    "先确认后台页面已登录，再打开 Network 并刷新页面。筛选栏输入 "
                    "cgi-bin，选择任意请求；如果 Request Headers 旁显示“Provisional "
                    "headers”，请换一个返回 200 的请求。Cookie 通常是一整行，"
                    "不要复制 Response Headers 里的 Set-Cookie。"
                ).classes("muted q-pa-sm")

            _backend_login_step(
                4,
                "测试并保存",
                "点击“测试并保存”。只有微信后台真实验证成功后才会保存并启用；"
                "以后搜索提示登录态失效时，重新执行前 3 步即可。",
            )
            session_label = ui.input(
                "会话备注（可选）",
                value=str(saved.get("session_label") or ""),
                placeholder="例如：运营主账号 / 2026-07-21",
            ).classes("w-full").props("outlined stack-label")
            enabled = ui.switch(
                "验证成功后启用公众号后台搜索",
                value=bool(saved.get("enabled")) if configured else True,
            )
            ui.label(
                "安全提醒：Token 和 Cookie 等同于临时后台登录凭证。不要发到聊天、群聊或截图中。"
                "程序仅使用 Windows 当前用户加密保存，不会在页面回显明文。"
            ).classes("text-negative text-caption")
            ui.label(
                "修改密码、退出后台、触发安全验证或登录一段时间后，凭证可能失效；"
                "此时重新登录并更新即可。留空输入框会保留已保存内容。"
            ).classes("text-warning text-caption")
            result_label = ui.label(
                "尚未验证本次输入" if not configured else "已有登录态；可留空后重新测试"
            ).classes("muted")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                test_input_btn = ui.button(
                    "仅测试",
                    icon="wifi_tethering",
                ).props("outline color=teal-9 no-caps")
                save_btn = ui.button(
                    "测试并保存",
                    icon="verified",
                ).props("unelevated color=teal-9 no-caps")

            async def test_input() -> None:
                set_button_loading(test_input_btn, True, "正在验证后台会话…")
                save_btn.disable()
                try:
                    token_value = str(token.value or "")
                    cookie_value = str(cookie.value or "")
                    await run.io_bound(
                        lambda: service.test_backend_search_settings(
                            token=token_value,
                            cookie=cookie_value,
                        )
                    )
                    result_label.set_text("验证成功：当前登录态可以搜索公众号文章")
                    result_label.classes(replace="text-positive")
                    ui.notify("连接成功，可以搜索公众号文章", type="positive")
                except Exception as exc:  # noqa: BLE001
                    message = _friendly_backend_error(exc)
                    result_label.set_text(message)
                    result_label.classes(replace="text-negative")
                    ui.notify(message, type="negative", timeout=15000)
                finally:
                    save_btn.enable()
                    set_button_loading(test_input_btn, False)

            async def test_and_save() -> None:
                set_button_loading(save_btn, True, "正在测试并安全保存…")
                test_input_btn.disable()
                try:
                    token_value = str(token.value or "")
                    cookie_value = str(cookie.value or "")
                    session_value = str(session_label.value or "")
                    should_enable = bool(enabled.value)

                    def verify_and_save() -> None:
                        service.test_backend_search_settings(
                            token=token_value,
                            cookie=cookie_value,
                        )
                        service.save_backend_search_settings(
                            enabled=should_enable,
                            token=token_value,
                            cookie=cookie_value,
                            session_label=session_value,
                        )

                    await run.io_bound(verify_and_save)
                    result_label.set_text("验证成功，登录态已加密保存")
                    result_label.classes(replace="text-positive")
                    ui.notify(
                        "连接成功，公众号后台登录态已加密保存",
                        type="positive",
                    )
                    dialog.close()
                    refresh_backend_status()
                    render()
                except Exception as exc:  # noqa: BLE001
                    message = _friendly_backend_error(exc)
                    result_label.set_text(message)
                    result_label.classes(replace="text-negative")
                    ui.notify(message, type="negative", timeout=15000)
                finally:
                    test_input_btn.enable()
                    set_button_loading(save_btn, False)

            test_input_btn.on_click(test_input)
            save_btn.on_click(test_and_save)
        dialog.open()

    async def test_saved_backend() -> None:
        set_button_loading(backend_test_btn, True, "正在测试连接…")
        try:
            await run.io_bound(service.test_backend_search_settings)
            ui.notify("公众号后台搜索连接正常", type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(
                _friendly_backend_error(exc),
                type="negative",
                timeout=15000,
            )
        finally:
            set_button_loading(backend_test_btn, False)

    def clear_backend() -> None:
        service.clear_backend_search_settings()
        refresh_backend_status()
        ui.notify("后台登录态已清除", type="positive")

    backend_config_btn.on_click(backend_config_dialog)
    backend_test_btn.on_click(test_saved_backend)
    backend_clear_btn.on_click(clear_backend)
    refresh_backend_status()
    if open_backend_config:
        backend_config_dialog()
    def edit_dialog(existing: dict[str, Any] | None = None) -> None:
        row = dict(existing or {})
        with ui.dialog() as dialog, ui.card().classes("w-full ops-dialog-md"):
            ui.label("编辑关注公众号" if row else "添加关注公众号").classes("text-h6 text-weight-bold")
            name = ui.input("公众号名称", value=str(row.get("name") or "")).classes("w-full").props("outlined stack-label")
            wechat_id = ui.input("微信号（可选）", value=str(row.get("wechat_id") or "")).classes("w-full").props("outlined stack-label")
            with ui.row().classes("w-full gap-3"):
                category = ui.input("分类", value=str(row.get("category") or "")).classes("col").props("outlined stack-label")
                default_method = "backend_search"
                current_method = str(row.get("fetch_method") or default_method)
                if current_method not in FOLLOWED_PUBLICATION_METHODS:
                    current_method = "backend_search"
                method = ui.select(FOLLOWED_PUBLICATION_METHODS, value=current_method, label="获取方式").classes("col").props("outlined stack-label")
                refresh_hours = ui.number("刷新间隔（小时）", value=int(row.get("refresh_hours") or 12), min=1, max=720).classes("w-48").props("outlined stack-label")
            tags = ui.input("标签（逗号分隔）", value="，".join(row.get("tags") or [])).classes("w-full").props("outlined stack-label")
            keywords = ui.input("关键词限制（逗号分隔）", value="，".join(row.get("keywords") or [])).classes("w-full").props("outlined stack-label")
            sample_url = ui.input("示例文章链接（可选）", value=str(row.get("sample_url") or "")).classes("w-full").props("outlined stack-label")
            official_account = ui.select(
                official_options,
                value=str(row.get("official_account_id") or "") or None,
                label="绑定自有公众号",
            ).classes("w-full").props("outlined stack-label clearable")
            source_url = ui.input("官网 / RSS / 第三方 API 地址", value=str(row.get("source_url") or "")).classes("w-full").props("outlined stack-label")
            with ui.row().classes("items-center gap-4"):
                is_owned = ui.switch("自有公众号", value=bool(row.get("is_owned")))
                enabled = ui.switch("启用关注", value=bool(row.get("enabled", 1)))

            def sync_method() -> None:
                selected_method = str(method.value or default_method)
                official_account.set_visibility(selected_method == "official")
                source_url.set_visibility(selected_method in {"rss", "third_party"})
                is_owned.value = selected_method == "official"
                is_owned.disable() if selected_method == "official" else is_owned.enable()

            method.on_value_change(lambda _: sync_method())
            sync_method()

            def save() -> None:
                try:
                    service.save_account(
                        {
                            **row,
                            "name": str(name.value or ""),
                            "wechat_id": str(wechat_id.value or ""),
                            "category": str(category.value or ""),
                            "fetch_method": str(method.value or default_method),
                            "official_account_id": str(official_account.value or ""),
                            "refresh_hours": int(refresh_hours.value or 12),
                            "tags": str(tags.value or ""),
                            "keywords": str(keywords.value or ""),
                            "sample_url": str(sample_url.value or ""),
                            "source_url": str(source_url.value or ""),
                            "is_owned": bool(is_owned.value),
                            "enabled": bool(enabled.value),
                        }
                    )
                    dialog.close()
                    render()
                    ui.notify("关注公众号已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("保存", on_click=save).props("unelevated color=teal-9 no-caps")
        dialog.open()

    def render() -> None:
        accounts = service.list_accounts()
        host.clear()
        with host:
            if not accounts:
                ui.label("尚未添加关注公众号").classes("muted")
            for account in accounts:
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-start justify-between"):
                        with ui.column().classes("gap-1"):
                            ui.label(str(account["name"])).classes("text-subtitle1 text-weight-bold")
                            meta = " · ".join(
                                value
                                for value in (
                                    str(account.get("wechat_id") or ""),
                                    str(account.get("category") or ""),
                                    FETCH_METHODS.get(str(account.get("fetch_method") or ""), ""),
                                )
                                if value
                            )
                            ui.label(meta).classes("muted")
                            ui.label(f'最近同步：{str(account.get("last_synced_at") or "尚未同步")[:19]}').classes("muted")
                            if account.get("last_error"):
                                ui.label(f'最近错误：{account["last_error"]}').classes("text-negative text-caption")
                        ui.badge("已启用" if account.get("enabled") else "已停用").props("color=green-7" if account.get("enabled") else "color=grey-6")
                    with ui.row().classes("items-center gap-2 q-mt-sm"):
                        refresh_btn = ui.button(
                            f"同步官方发布记录 · {refresh_price}"
                            if str(account.get("fetch_method") or "") == "official"
                            else f"获取近期文章 · {refresh_price}",
                            icon="sync" if str(account.get("fetch_method") or "") == "official" else "search",
                        ).props("outline dense color=teal-9 no-caps")

                        async def refresh_one(
                            account_id: str = str(account["id"]),
                            button: Any = refresh_btn,
                        ) -> None:
                            row = service.get_account(account_id) or {}
                            loading_text = (
                                "正在同步官方发布记录…"
                                if str(row.get("fetch_method") or "") == "official"
                                else "正在选择可用数据源获取文章…"
                            )
                            set_button_loading(button, True, loading_text)
                            should_open = False
                            try:
                                report = await run.io_bound(lambda: service.discover_account(account_id))
                                if report.get("error"):
                                    ui.notify(str(report["error"]), type="warning", timeout=10000)
                                else:
                                    source_label = str(report.get("source_label") or "可用数据源")
                                    ui.notify(
                                        f'已通过{source_label}发现并同步 {report["added"]} 篇文章，'
                                        f'本次 {report.get("points", article_refresh_points)} 积分',
                                        type="positive",
                                    )
                                    if report.get("warning"):
                                        ui.notify(str(report["warning"]), type="info", timeout=10000)
                                    should_open = True
                            except Exception as exc:  # noqa: BLE001
                                ui.notify(f"发现失败：{exc}", type="negative")
                            finally:
                                set_button_loading(button, False)
                            if should_open:
                                render()
                                open_articles(account_id)

                        refresh_btn.on_click(refresh_one)
                        ui.button(
                            "查看近期文章",
                            icon="article",
                            on_click=lambda _=None, account_id=str(account["id"]): open_articles(account_id),
                        ).props("flat dense color=teal-9 no-caps")
                        ui.button("编辑", on_click=lambda _=None, row=dict(account): edit_dialog(row)).props("flat dense color=teal-9 no-caps")
                        ui.button(
                            "删除",
                            on_click=lambda _=None, account_id=str(account["id"]): _delete_followed_account(service, account_id, render),
                        ).props("flat dense color=red-7 no-caps")

    async def refresh_all() -> None:
        set_button_loading(refresh_all_btn, True, "正在刷新全部关注公众号…")
        try:
            report = await run.io_bound(service.discover_all)
            ui.notify(
                f'刷新完成，共发现并同步 {report["added"]} 篇文章，'
                f'本次 {report.get("points", 0)} 积分',
                type="positive",
            )
            render()
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"刷新失败：{exc}", type="negative")
        finally:
            set_button_loading(refresh_all_btn, False)

    add_btn.on_click(lambda: edit_dialog())
    def import_owned() -> None:
        count = service.import_owned_official_accounts()
        render()
        ui.notify(f"已导入 {count} 个启用中的自有公众号，可直接同步官方发布记录", type="positive")

    import_owned_btn.on_click(import_owned)
    refresh_all_btn.on_click(refresh_all)
    render()


def _build_sources(service: TopicSourceService) -> None:
    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-0"):
            ui.label("选题来源管理").classes("text-h5 text-weight-bold")
            ui.label("每个来源独立启停、刷新并显示最近错误；热点页可以多选来源。").classes("muted")
        add_btn = ui.button("添加来源", icon="add").props("unelevated color=teal-9 no-caps")
    host = ui.column().classes("w-full gap-3")

    def source_dialog(existing: dict[str, Any] | None = None) -> None:
        row = dict(existing or {})
        config = dict(row.get("config") or {})
        with ui.dialog() as dialog, ui.card().classes("w-full ops-dialog-md"):
            ui.label("编辑选题来源" if row else "添加选题来源").classes("text-h6 text-weight-bold")
            name = ui.input("来源名称", value=str(row.get("name") or "")).classes("w-full").props("outlined stack-label")
            source_type = ui.select(SOURCE_TYPES, value=str(row.get("source_type") or "rss"), label="来源类型").classes("w-full").props("outlined stack-label")
            url = ui.input("RSS / API 地址", value=str(config.get("url") or "")).classes("w-full").props("outlined stack-label")
            query_param = ui.input(
                "关键词参数名（可选）",
                value=str(config.get("query_param") or ""),
                placeholder="例如 q、keyword；也可在地址中写 {keyword}",
            ).classes("w-full").props("outlined stack-label")
            queries = ui.textarea("新闻搜索关键词（每行一个）", value="\n".join(config.get("queries") or [])).classes("w-full").props("outlined rows=5")
            enabled = ui.switch("启用来源", value=bool(row.get("enabled", 1)))

            def sync_fields() -> None:
                kind = str(source_type.value or "")
                url.set_visibility(kind in {"rss", "hot_api"})
                query_param.set_visibility(kind in {"rss", "hot_api"})
                queries.set_visibility(kind == "news_search")

            source_type.on_value_change(lambda _: sync_fields())
            sync_fields()

            def save() -> None:
                try:
                    service.save_source(
                        {
                            **row,
                            "name": str(name.value or ""),
                            "source_type": str(source_type.value or "rss"),
                            "enabled": bool(enabled.value),
                            "config": {
                                "url": str(url.value or "").strip(),
                                "query_param": str(query_param.value or "").strip(),
                                "queries": [line.strip() for line in str(queries.value or "").splitlines() if line.strip()],
                            },
                        }
                    )
                    dialog.close()
                    render()
                    ui.notify("选题来源已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("保存", on_click=save).props("unelevated color=teal-9 no-caps")
        dialog.open()

    def render() -> None:
        sources = service.list_sources()
        host.clear()
        with host:
            for source in sources:
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-start justify-between"):
                        with ui.column().classes("gap-1"):
                            ui.label(str(source["name"])).classes("text-subtitle1 text-weight-bold")
                            ui.label(SOURCE_TYPES.get(str(source["source_type"]), str(source["source_type"]))).classes("muted")
                            ui.label(f'最近刷新：{str(source.get("last_synced_at") or "尚未刷新")[:19]}').classes("muted")
                            if source.get("last_error"):
                                ui.label(f'最近错误：{source["last_error"]}').classes("text-negative text-caption")
                        enabled = ui.switch("启用", value=bool(source.get("enabled")))

                        def toggle(value: bool, row: dict[str, Any] = dict(source)) -> None:
                            service.save_source({**row, "enabled": value})

                        enabled.on_value_change(lambda event, fn=toggle: fn(bool(event.value)))
                    with ui.row().classes("items-center gap-2 q-mt-sm"):
                        refresh_btn = ui.button("单独刷新", icon="refresh").props("outline dense color=teal-9 no-caps")

                        async def refresh_one(source_id: str = str(source["id"]), button: Any = refresh_btn) -> None:
                            set_button_loading(button, True, "正在刷新该选题来源…")
                            try:
                                report = await run.io_bound(lambda: service.refresh([source_id]))
                                row = report["sources"][0] if report["sources"] else {}
                                if row.get("error"):
                                    ui.notify(str(row["error"]), type="warning")
                                else:
                                    ui.notify(f'刷新完成，获取 {row.get("count", 0)} 条', type="positive")
                                render()
                            finally:
                                set_button_loading(button, False)

                        refresh_btn.on_click(refresh_one)
                        ui.button("编辑", on_click=lambda _=None, row=dict(source): source_dialog(row)).props("flat dense color=teal-9 no-caps")
                        if source["source_type"] not in {"manual", "followed_accounts"}:
                            ui.button(
                                "删除",
                                on_click=lambda _=None, source_id=str(source["id"]): _delete_source(service, source_id, render),
                            ).props("flat dense color=red-7 no-caps")

    add_btn.on_click(lambda: source_dialog())
    render()


def _queue_for_wizard(
    state: AppState,
    workspace_tabs: Any,
    tab_wizard: Any,
    item: dict[str, Any],
    *,
    auto_start: bool = False,
    account_ids: list[str] | None = None,
    followed_article_id: str = "",
    topic_item_id: str = "",
) -> None:
    state.pending_rewrite = {
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "source": str(item.get("source_name") or item.get("account_name") or "选题库"),
        "auto_start": bool(auto_start),
        "account_ids": list(account_ids or []),
        "followed_article_id": followed_article_id,
        "topic_item_id": topic_item_id,
    }
    # ``value =`` only mutates the Python-side model in some NiceGUI versions;
    # ``set_value`` also pushes the selected tab to the browser. The wizard's
    # progress UI must be visible before its timer consumes this request.
    workspace_tabs.set_value(tab_wizard)
    ui.notify(
        "已带去工作台，正在开始生成"
        if auto_start
        else "已带去工作台，请确认内容和公众号",
        type="positive",
    )


def _delete_followed_account(
    service: FollowedContentService, account_id: str, render: Callable[[], None]
) -> None:
    service.delete_account(account_id)
    render()
    ui.notify("已删除关注公众号", type="positive")


def _delete_source(
    service: TopicSourceService, source_id: str, render: Callable[[], None]
) -> None:
    try:
        service.delete_source(source_id)
        render()
        ui.notify("选题来源已删除", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(str(exc), type="negative")


def _backend_login_step(number: int, title: str, description: str) -> None:
    with ui.row().classes("w-full items-start no-wrap q-mt-sm"):
        ui.badge(str(number)).props("rounded color=teal-8")
        with ui.column().classes("gap-0 col"):
            ui.label(title).classes("text-subtitle1 text-weight-bold")
            ui.label(description).classes("muted")


def _friendly_backend_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    safe = re.sub(
        r"(?i)(token=)[^&\s]+",
        r"\1••••",
        text,
    )
    safe = re.sub(
        r"(?i)(cookie\s*[:=]\s*)[^\r\n]+",
        r"\1••••",
        safe,
    )
    lower = safe.casefold()
    if "token 格式" in lower or "cookie 格式" in lower or "请先填写" in safe:
        return safe
    if any(
        marker in lower
        for marker in (
            "登录态",
            "非 json",
            "http 401",
            "http 403",
            "200003",
            "invalid session",
        )
    ):
        return (
            "连接失败：公众号后台登录态已失效或需要重新验证。"
            "请重新登录 mp.weixin.qq.com，再复制新的 Token 和 Cookie。"
        )
    if any(
        marker in lower
        for marker in ("无法连接", "timeout", "timed out", "network", "proxy", "网络", "代理")
    ):
        return "连接失败：无法访问微信公众平台，请检查本机网络或代理后重试。"
    return f"连接失败：{safe[:360]}" if safe else "连接失败，请重新复制 Token 和 Cookie 后重试。"
