from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from nicegui import run, ui

from app.config import load_config
from app.db import Database
from app.pipeline import Pipeline
from app.batch import inject_pipelines_concurrently, run_pipelines_concurrently
from app.layout_profiles import layout_to_template_config, normalize_layout, validate_layout
from app.render import TemplateRenderer, finalize_article_html, prepare_preview_html
from app.wechat.template_snapshot import load_template_snapshot
from app.wechat.auth import WeChatAuth
from app.wechat.client import WeChatClient
from app.accounts import (
    DEFAULT_ACCOUNT_ID,
    IMPORTED_DEFAULT_ACCOUNT_ID,
    apply_account_selection,
    public_accounts,
    save_account,
    save_account_layout,
    save_account_prompt_selection,
)
from app.ai import clean_candidate_text
from app.ai.model_registry import (
    apply_model_selection,
    configured_models,
    public_models,
)
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    DEFAULT_IMAGE_PROMPT_STYLE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_MODE_DEFAULT,
    PROMPT_MODE_TEMPLATE,
    public_prompt_templates,
)
from app.ui.styles import step_title_html
from app.ui.lifecycle import client_timer
from app.ui.workflow import (
    CREATION_WORKFLOW_STEPS,
    next_review_job,
    render_workflow_guide,
)
from app.ui import image_proxy as _image_proxy  # noqa: F401; registers local preview route
from app.ui.state import (
    AppState,
    STATUS_LABEL,
    clean_subtitles as _clean_subtitles,
    clean_titles as _clean_titles,
    set_button_loading as _set_button_loading,
)
from app.ui.panels.feishu import build_feishu_panel
from app.ui.panels.overview import build_overview_cards
from app.ui.panels.review_jury import enabled_profile_options
from app.ui.panels.settings_hub import (
    build_creation_plans_panel,
    build_model_management_panel,
)
from app.ui.panels.tasks import build_tasks_panel
from app.ui.panels.tasks import confirm_batch_write, open_review_workbench
from app.ui.panels.topics import build_topic_center
from app.services.batches import BatchService
from app.services.batch_contracts import TERMINAL_STATUSES, effective_batch_status
from app.services.creation_plans import CreationPlanService
from app.services.followed_content import FollowedContentService
from app.services.topic_sources import TopicSourceService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Compatibility hook for isolated UI tests. Real pages create their own state
# so desktop, browser and reconnecting clients never share element references.
state: AppState | None = None


def _finalize_generated_batch(
    db: Database,
    batch_id: str | None,
    results: list[dict[str, Any]],
) -> dict[str, int | str]:
    """Persist the batch result before any best-effort UI updates."""

    counts: dict[str, int | str] = {
        "drafted": sum(1 for result in results if result.get("status") == "drafted"),
        "ready_for_review": sum(
            1 for result in results if result.get("status") == "ready_for_review"
        ),
        "failed": sum(1 for result in results if result.get("status") == "failed"),
        "cancelled": sum(
            1 for result in results if result.get("status") == "cancelled"
        ),
        "status": effective_batch_status(results),
    }
    if batch_id:
        db.update_batch(batch_id, status=str(counts["status"]))
    return counts


def create_desktop_app() -> None:
    from app.ui.styles import APP_CSS, HEAD_HTML

    page_state = AppState()
    ui.add_head_html(HEAD_HTML)
    ui.add_css(APP_CSS)

    with ui.element("div").classes("shell"):
        with ui.element("div").classes("hero"):
            with ui.column().classes("gap-0"):
                ui.label("CONTENT STUDIO").classes("eyebrow")
                ui.label("公众号改写助手").classes("brand")
                ui.label(
                    "从选题到草稿，一站完成公众号内容生产"
                ).classes("brand-sub")
                ui.html(
                    '<div class="q-mt-sm">'
                    '<span class="flow-chip"><span class="dot"></span>本地运行</span>'
                    '<span class="flow-chip"><span class="dot"></span>仅存草稿不群发</span>'
                    '<span class="flow-chip"><span class="dot"></span>人工审核文章</span>'
                    "</div>",
                    sanitize=False,
                )
            with ui.column().classes("items-end gap-2"):
                ui.html(
                    '<div class="hero-badge"><span class="hero-badge-dot"></span>'
                    '<span><b>草稿安全模式</b><small>仅写草稿，不会自动群发</small></span></div>',
                    sanitize=False,
                )

        tabs = ui.tabs().classes("workspace-tabs w-full").props(
            "dense align=left indicator-color=teal-9 active-color=teal-10"
        )
        with tabs:
            tab_wizard = ui.tab("工作台")
            tab_topics = ui.tab("选题库")
            tab_jobs = ui.tab("任务中心")
            tab_settings = ui.tab("设置")

        panels = ui.tab_panels(tabs, value=tab_wizard).classes("w-full bg-transparent")
        with panels:
            with ui.tab_panel(tab_wizard).classes("wizard-panel"):
                _build_wizard(tabs, tab_topics, tab_jobs, state=page_state)
            with ui.tab_panel(tab_topics):
                build_topic_center(page_state, tabs, tab_wizard)
            with ui.tab_panel(tab_jobs):
                build_tasks_panel(page_state)
            with ui.tab_panel(tab_settings):
                settings_tabs = ui.tabs().classes("workspace-tabs w-full").props(
                    "dense align=left indicator-color=teal-9 active-color=teal-10"
                )
                with settings_tabs:
                    settings_accounts = ui.tab("公众号")
                    settings_models = ui.tab("模型管理")
                    settings_plans = ui.tab("创作方案")
                    settings_feishu = ui.tab("飞书")
                    settings_help = ui.tab("系统设置")
                with ui.tab_panels(
                    settings_tabs, value=settings_accounts
                ).classes("w-full bg-transparent"):
                    with ui.tab_panel(settings_accounts):
                        refresh_accounts_panel = _build_accounts_panel(page_state)
                    with ui.tab_panel(settings_models):
                        build_model_management_panel(page_state)
                    with ui.tab_panel(settings_plans):
                        build_creation_plans_panel(
                            page_state,
                            on_plans_change=refresh_accounts_panel,
                        )
                    with ui.tab_panel(settings_feishu):
                        build_feishu_panel(page_state)
                    with ui.tab_panel(settings_help):
                        _build_help_panel()


def _build_wizard(
    tabs: Any,
    tab_topics: Any,
    tab_jobs: Any,
    *,
    state: AppState | None = None,
) -> None:
    state = state or globals().get("state") or AppState()
    owner_client = ui.context.client

    def ui_alive() -> bool:
        """Return whether this callback may still touch its owning page."""

        return not bool(getattr(owner_client, "is_deleted", False))

    state.reload_config()
    url_holder: dict[str, Any] = {}
    pending_rewrite_origin: dict[str, str] = {}
    workflow_state = {
        "stage": "content",
        "note": "先准备选题、链接或正文",
        "completed": False,
    }

    @ui.refreshable
    def workflow_guide() -> None:
        render_workflow_guide(
            str(workflow_state["stage"]),
            note=str(workflow_state["note"]),
            completed=bool(workflow_state["completed"]),
            steps=CREATION_WORKFLOW_STEPS,
        )

    def set_workflow(stage: str, note: str, *, completed: bool = False) -> None:
        workflow_state.update(stage=stage, note=note, completed=completed)
        if ui_alive():
            workflow_guide.refresh()

    workflow_guide()

    def open_task_center(status_filter: str = "", today: bool = False) -> None:
        if not ui_alive():
            return
        if callable(state.task_center_refresh):
            state.task_center_refresh(
                status_filter=status_filter,
                today=today,
            )
        tabs.set_value(tab_jobs)

    with ui.element("div").classes("card w-full"):
        build_overview_cards(
            state,
            on_go_tasks=open_task_center,
        )

    with ui.element("div").classes("card topic-card"):
        ui.html(step_title_html(1, "选择本次内容"), sanitize=False)
        ui.label(
            "已有链接、正文或明确话题可直接填写；需要找热点、关注公众号文章或收藏内容时，统一去选题库。"
        ).classes("muted q-mb-sm")
        with ui.row().classes("items-center q-mb-sm"):
            ui.button(
                "从选题库选择",
                icon="explore",
                on_click=lambda: tabs.set_value(tab_topics),
            ).props("unelevated dense color=teal-9 no-caps")
            ui.label("选择后会自动带回当前工作台。").classes("muted")

        source = ui.toggle(
            {
                "hot": "近7天热点",
                "peer": "关注公众号文章",
                "keyword": "关键词",
                "manual": "手动输入",
            },
            value="manual",
        ).props("dense no-caps")
        source.set_visibility(False)

        selected_label = ui.label("当前话题：未选择").classes("q-mt-sm text-weight-medium")
        manual_in = ui.input("文章主题", placeholder="例如：AI 如何改变客服效率").classes(
            "w-full"
        ).props("outlined stack-label")
        manual_in.set_visibility(True)
        with ui.expansion(
            "更多：根据主题查找参考文章",
            icon="manage_search",
            value=False,
        ).classes("w-full q-mt-sm"):
            topic_host = ui.column().classes("w-full q-mt-sm")

        def set_topic(text: str, src: str) -> None:
            state.selected_topic = text.strip()
            state.topic_source = src
            selected_label.text = (
                f"当前话题：{state.selected_topic}" if state.selected_topic else "当前话题：未选择"
            )

        async def fill_article_url(article_url: str, article_title: str = "") -> None:
            el = url_holder.get("el")
            if el is None:
                ui.notify("链接输入框尚未就绪", type="warning")
                return
            from app.providers.article_search import resolve_article_url

            raw = (article_url or "").strip()
            # 先立刻回填，保证输入框始终可改
            try:
                el.enable()
            except Exception:  # noqa: BLE001
                pass
            el.props(remove="readonly")
            if hasattr(el, "set_value"):
                el.set_value(raw)
            else:
                el.value = raw
            mode_el = url_holder.get("mode")
            if mode_el is not None:
                mode_el.value = "link"

            tip = article_title[:28] + ("…" if len(article_title) > 28 else "")
            ui.notify(f"已填入链接（可继续手改）：{tip or raw[:40]}", type="positive")

            # 后台尽量解析成微信原文链接，不阻塞编辑
            try:
                real = await run.io_bound(lambda: resolve_article_url(raw))
                if real and real != raw and (el.value or "").strip() in {raw, ""}:
                    if hasattr(el, "set_value"):
                        el.set_value(real)
                    else:
                        el.value = real
                    try:
                        el.enable()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.info("resolve url skipped: %s", exc)

        def on_manual_change(_: Any = None) -> None:
            if source.value == "manual":
                set_topic(manual_in.value or "", "manual")

        manual_in.on("update:model-value", on_manual_change)

        def render_topic_list(
            items: list[dict[str, Any]],
            src: str,
            target_host: Any | None = None,
        ) -> None:
            host = target_host or topic_host
            host.clear()
            with host:
                if not items:
                    ui.label("暂无选题，可切换到手动输入，或点击刷新。").classes("muted")
                    return
                for it in items[:25]:
                    title = str(it.get("title") or it.get("topic") or "").strip()
                    if not title:
                        continue
                    extra = str(it.get("account") or it.get("source") or "").strip()
                    published = str(it.get("published_at") or "").strip()
                    if published:
                        published = published[:10]
                    meta_text = " · ".join(x for x in (extra, published) if x)
                    articles_host = ui.column().classes("w-full q-ml-md q-mt-xs gap-1")
                    articles_host.set_visibility(False)
                    loaded = {"done": False}

                    async def load_articles(
                        topic_title: str = title,
                        host: Any = articles_host,
                        flag: dict = loaded,
                        preset_url: str | None = str(it.get("url") or "") or None,
                        request_button: Any | None = None,
                    ) -> None:
                        try:
                            if request_button is not None:
                                _set_button_loading(request_button, True)
                            if flag["done"]:
                                host.set_visibility(True)
                                return
                            host.clear()
                            host.set_visibility(True)
                            with host:
                                ui.spinner("dots", size="sm", color="teal-9")
                                ui.label("正在拉取该话题热度前 3 篇文章…").classes("muted")
                            from app.providers.article_search import search_weixin_articles

                            arts = await run.io_bound(
                                lambda: search_weixin_articles(topic_title, limit=3)
                            )
                        except Exception as exc:  # noqa: BLE001
                            arts = []
                            logger.warning("article search failed: %s", exc)
                        finally:
                            if request_button is not None:
                                _set_button_loading(request_button, False)
                        if preset_url and "http" in preset_url:
                            arts = [
                                {
                                    "title": f"{topic_title}（已配置链接）",
                                    "url": preset_url,
                                    "snippet": "",
                                }
                            ] + [a for a in arts if a.get("url") != preset_url]
                            arts = arts[:3]
                        flag["done"] = True
                        host.clear()
                        with host:
                            if not arts:
                                ui.label(
                                    "暂未找到相关公众号文章（可能被检索站拦截）。可手动粘贴链接。"
                                ).classes("muted")
                                return
                            ui.label("热度相关文章 Top 3（点击回填链接）").classes(
                                "muted text-weight-medium"
                            )
                            for rank, art in enumerate(arts, start=1):
                                a_title = art.get("title") or f"文章 {rank}"
                                a_url = art.get("url") or ""

                                def make_fill(
                                    u: str = a_url,
                                    t: str = a_title,
                                    topic_t: str = topic_title,
                                    s: str = src,
                                ) -> Callable[[], Any]:
                                    async def _fill() -> None:
                                        set_topic(topic_t, s)
                                        await fill_article_url(u, t)
                                    return _fill

                                with ui.element("div").classes("article-item").on(
                                    "click", make_fill()
                                ):
                                    ui.label(f"Top{rank}  {a_title}").classes(
                                        "text-weight-medium"
                                    )
                                    ui.label(
                                        a_url[:90] + ("…" if len(a_url) > 90 else "")
                                    ).classes("muted")

                    async def on_topic_click(
                        topic_title: str = title,
                        s: str = src,
                        host: Any = articles_host,
                        flag: dict = loaded,
                        preset_url: str | None = str(it.get("url") or "") or None,
                        request_button: Any | None = None,
                    ) -> None:
                        set_topic(topic_title, s)
                        ui.notify(f"已选题：{topic_title}", type="info")
                        await load_articles(
                            topic_title, host, flag, preset_url, request_button
                        )

                    with ui.element("div").classes("topic-item"):
                        with ui.row().classes("w-full items-center justify-between"):
                            with ui.column().classes("gap-0").style("flex:1;min-width:0"):
                                ui.label(title).classes("text-weight-medium")
                                if meta_text:
                                    ui.label(meta_text).classes("muted")
                            article_btn = ui.button("查看相关文章").props(
                                "flat dense no-caps color=teal-9"
                            )
                            article_btn.on_click(
                                lambda _e=None, fn=on_topic_click, btn=article_btn: fn(
                                    request_button=btn
                                )
                            )
                        articles_host

        async def refresh_hot(request_button: Any | None = None) -> None:
            if request_button is not None:
                _set_button_loading(request_button, True)
            topic_host.clear()
            with topic_host:
                ui.spinner("dots", size="sm", color="teal-9")
                ui.label("正在刷新近 7 天企业/管理/项目/组织热点…").classes("muted")
            try:
                def load_topics() -> list[dict[str, Any]]:
                    cfg = state.reload_config()
                    service = TopicSourceService(state.db, cfg)
                    service.refresh()
                    return service.list_topics(days=7, limit=30)

                items = await run.io_bound(load_topics)
                render_topic_list(items, "hot")
                ui.notify(f"已加载 {len(items)} 条近 7 天行业热点", type="positive")
            except Exception as exc:  # noqa: BLE001
                service = TopicSourceService(state.db, state.reload_config())
                items = service.list_topics(days=7, limit=30)
                render_topic_list(items, "hot")
                ui.notify(f"热点接口失败，已用本地缓存：{exc}", type="warning")
            finally:
                if request_button is not None:
                    _set_button_loading(request_button, False)

        def show_peers() -> None:
            follow_service = FollowedContentService(state.db, state.reload_config())
            peers = follow_service.list_articles(days=7, limit=100)
            render_topic_list(
                [
                    {
                        "title": p["title"],
                        "account": p.get("account_name") or "",
                        "topic": p["title"],
                        "url": p.get("url"),
                        "published_at": p.get("published_at") or p.get("discovered_at"),
                    }
                    for p in peers
                ],
                "peer",
            )

        def show_keywords() -> None:
            topic_service = TopicSourceService(state.db, state.reload_config())
            source_options = {
                str(item["id"]): str(item["name"])
                for item in topic_service.list_sources(enabled_only=True)
            }
            with topic_host:
                ui.label("输入一个热点关键词，并选择要同时搜索的来源。").classes("muted")
                keyword_in = ui.input(
                    "热点关键词",
                    placeholder="例如：人工智能、组织变革、项目管理",
                ).classes("w-full").props("outlined stack-label clearable")
                keyword_sources = ui.select(
                    source_options,
                    value=list(source_options),
                    label="搜索来源（可多选）",
                    multiple=True,
                ).classes("w-full").props("outlined stack-label use-chips clearable")
                keyword_days = ui.select(
                    {1: "今天", 3: "最近3天", 7: "最近7天", 30: "最近30天"},
                    value=7,
                    label="日期范围",
                ).classes("w-full").props("outlined stack-label")
                keyword_btn = ui.button("搜索多来源热点", icon="search").props(
                    "unelevated color=teal-9 no-caps"
                )
                keyword_results = ui.column().classes("w-full gap-2")

                async def search_keyword() -> None:
                    value = str(keyword_in.value or "").strip()
                    selected = list(keyword_sources.value or [])
                    if not value:
                        ui.notify("请先输入热点关键词", type="warning")
                        return
                    if not selected:
                        ui.notify("请至少选择一个搜索来源", type="warning")
                        return
                    _set_button_loading(
                        keyword_btn,
                        True,
                        f"正在多来源搜索“{value}”…",
                    )
                    try:
                        report = await run.io_bound(
                            lambda: topic_service.search(
                                value,
                                selected,
                                days=int(keyword_days.value or 7),
                            )
                        )
                        render_topic_list(
                            list(report.get("items") or []),
                            "keyword",
                            keyword_results,
                        )
                        failures = [
                            item for item in report["sources"] if item.get("error")
                        ]
                        if failures:
                            ui.notify(
                                "部分来源失败："
                                + "；".join(
                                    f'{item["name"]}：{item["error"]}'
                                    for item in failures
                                ),
                                type="warning",
                                timeout=12000,
                            )
                        ui.notify(
                            f'已从 {len(report["sources"])} 个来源找到 {report["total"]} 条热点',
                            type="positive",
                        )
                    except Exception as exc:  # noqa: BLE001
                        ui.notify(f"关键词搜索失败：{exc}", type="negative")
                    finally:
                        _set_button_loading(keyword_btn, False)

                keyword_btn.on_click(search_keyword)
                keyword_in.on("keydown.enter", search_keyword)

        async def on_source_change() -> None:
            src = str(source.value)
            manual_in.set_visibility(src == "manual")
            topic_host.clear()
            if src == "manual":
                on_manual_change()
                with topic_host:
                    ui.label(
                        "手动输入话题后，可点下方按钮检索该话题热度前 3 篇文章。"
                    ).classes("muted")
                    search_btn = ui.button("检索该话题相关文章 Top3").props(
                        "outline dense no-caps color=teal-9"
                    )

                    async def search_manual() -> None:
                        t = (manual_in.value or "").strip()
                        if not t:
                            ui.notify("请先输入话题", type="warning")
                            return
                        _set_button_loading(search_btn, True)
                        set_topic(t, "manual")
                        topic_host.clear()
                        with topic_host:
                            ui.spinner("dots", size="sm", color="teal-9")
                            ui.label("检索中…").classes("muted")
                        try:
                            from app.providers.article_search import search_weixin_articles

                            arts = await run.io_bound(
                                lambda: search_weixin_articles(t, limit=3)
                            )
                        except Exception as exc:  # noqa: BLE001
                            arts = []
                            ui.notify(f"检索失败：{exc}", type="negative")
                        finally:
                            _set_button_loading(search_btn, False)
                        topic_host.clear()
                        with topic_host:
                            ui.label(f"话题：{t}").classes("text-weight-medium")
                            if not arts:
                                ui.label("未找到相关文章，请手动粘贴链接。").classes("muted")
                                return
                            for rank, art in enumerate(arts, start=1):
                                a_title = art.get("title") or f"文章 {rank}"
                                a_url = art.get("url") or ""

                                def make_fill(
                                    u: str = a_url, at: str = a_title
                                ) -> Callable[[], Any]:
                                    async def _fill() -> None:
                                        await fill_article_url(u, at)
                                    return _fill

                                with ui.element("div").classes("article-item").on(
                                    "click", make_fill()
                                ):
                                    ui.label(f"Top{rank}  {a_title}")
                                    ui.label(a_url[:90]).classes("muted")

                    search_btn.on_click(search_manual)
            elif src == "hot":
                with topic_host:
                    ui.label("范围：最近 7 天 · 企业 / 管理 / 项目 / 组织").classes("muted")
                    hot_refresh_btn = ui.button("刷新近7天热点").props(
                        "outline dense no-caps color=teal-9"
                    )
                    hot_refresh_btn.on_click(
                        lambda: refresh_hot(hot_refresh_btn)
                    )
                await refresh_hot()
            elif src == "peer":
                show_peers()
            elif src == "keyword":
                show_keywords()

        source.on_value_change(
            lambda _: client_timer(0.01, on_source_change, once=True)
        )
        client_timer(0.01, on_source_change, once=True)

    with ui.element("div").classes("card source-card"):
        ui.label("内容来源").classes("section-title")
        ui.label("请选择一种输入方式；从选题库带回文章时会自动填入链接。").classes(
            "muted q-mb-sm"
        )
        source_mode_in = ui.toggle(
            {
                "link": "粘贴文章链接",
                "text": "粘贴正文",
                "references": "多篇参考资料",
                "topic": "仅输入话题原创",
            },
            value="link",
        ).props("dense no-caps")
        url_holder["mode"] = source_mode_in
        url_in = (
            ui.input(
                "文章链接（可编辑）",
                placeholder="https://mp.weixin.qq.com/s/...",
            )
            .classes("w-full")
            .props("clearable outlined stack-label")
        )
        url_holder["el"] = url_in
        text_in = ui.textarea("粘贴文章正文").classes("w-full").props("rows=8 outlined")
        references_in = ui.textarea(
            "参考文章链接（每行一个，第一篇为主要参考）"
        ).classes("w-full").props("rows=6 outlined")
        with ui.expansion(
            "高级设置：事实保留与改写强度",
            icon="tune",
            value=False,
        ).classes("w-full"):
            facts_in = ui.textarea(
                "必须保留的事实或补充资料（可选）"
            ).classes("w-full").props("rows=4 outlined")
            intensity_in = ui.select(
                options={
                    "light": "轻度改写：尽量保留原结构",
                    "standard": "标准改写：优化结构与表达",
                    "strong": "深度改写：重构结构但不改变事实",
                },
                value="standard",
                label="改写强度",
            ).classes("w-full").props("outlined stack-label")

        def sync_source_mode() -> None:
            mode = str(source_mode_in.value or "link")
            url_in.set_visibility(mode == "link")
            text_in.set_visibility(mode == "text")
            references_in.set_visibility(mode == "references")
            facts_in.set_visibility(mode in {"link", "text", "references"})

        source_mode_in.on_value_change(lambda _: sync_source_mode())
        sync_source_mode()

        async def consume_pending_rewrite() -> None:
            pending = state.pending_rewrite
            if not pending:
                return
            if state.busy:
                if not pending.get("_waiting_notified"):
                    pending["_waiting_notified"] = True
                    ui.notify(
                        "当前批次仍在生成，本次内容已保留，完成后会自动载入",
                        type="warning",
                    )
                return
            state.pending_rewrite = None
            title = str(pending.get("title") or "").strip()
            url = str(pending.get("url") or "").strip()
            auto_start = bool(pending.get("auto_start"))
            requested_accounts = [
                str(item) for item in pending.get("account_ids") or [] if str(item)
            ]
            pending_rewrite_origin.clear()
            pending_rewrite_origin.update(
                {
                    "followed_article_id": str(
                        pending.get("followed_article_id") or ""
                    ),
                    "topic_item_id": str(pending.get("topic_item_id") or ""),
                }
            )
            manual_in.value = title
            source.value = "manual"
            set_topic(title, str(pending.get("source") or "topic-center"))
            if url:
                source_mode_in.value = "link"
                sync_source_mode()
                await fill_article_url(url, title)
            else:
                source.value = "manual"
                manual_in.value = title
                source_mode_in.value = "topic"
                sync_source_mode()
            if not auto_start:
                ui.notify("已载入选题库内容", type="positive")
                return
            available_accounts = set(account_options)
            selected_accounts = [
                item for item in requested_accounts if item in available_accounts
            ]
            if not selected_accounts:
                ui.notify(
                    "未找到可用的目标公众号，请在工作台手动选择后再生成",
                    type="warning",
                )
                return
            target_accounts.set_value(selected_accounts)
            sync_default_model_row()
            ui.notify("链接已填入，正在开始生成文章", type="positive")
            await asyncio.sleep(0)
            await start_rewrite()

        client_timer(0.5, consume_pending_rewrite)

    with ui.element("div").classes("card action-card"):
        ui.html(step_title_html(2, "选择目标公众号"), sanitize=False)
        account_options = state.account_options()
        remembered_accounts = state.remembered_account_ids()
        target_accounts = ui.select(
            options=account_options,
            value=remembered_accounts,
            label="选择目标公众号（可多选）",
            multiple=True,
        ).classes("w-full q-mb-sm").props("outlined stack-label use-chips clearable")
        state.account_selects.append((target_accounts, True))
        model_options = state.model_options()
        with ui.row().classes("w-full items-start q-mb-md").style("gap:12px") as default_model_row:
            primary_model = ui.select(
                options=model_options,
                value="",
                label="默认公众号使用的模型",
            ).classes("col").props("outlined stack-label")
            fallback_model = ui.select(
                options=model_options,
                value="",
                label="备用模型（可选）",
            ).classes("col").props("outlined stack-label clearable")
        state.model_selects.extend(((primary_model, True), (fallback_model, True)))
        ui.label(
            "已管理的公众号会自动套用各自保存的模型和创作方案；只有选择系统默认公众号时才需要上面的模型选项。"
        ).classes("muted q-mb-sm")

        def sync_default_model_row() -> None:
            default_model_row.set_visibility(
                DEFAULT_ACCOUNT_ID in list(target_accounts.value or [])
            )

        def source_is_ready() -> bool:
            mode = str(source_mode_in.value or "link")
            if not state.selected_topic.strip() and not str(manual_in.value or "").strip():
                return False
            if mode == "link":
                return bool(str(url_in.value or "").strip())
            if mode == "text":
                return bool(str(text_in.value or "").strip())
            if mode == "references":
                return bool(str(references_in.value or "").strip())
            return True

        def sync_workflow_before_generation() -> None:
            if state.busy:
                return
            selected_count = len(list(target_accounts.value or []))
            if not source_is_ready():
                set_workflow("content", "先准备选题、链接或正文")
            elif not selected_count:
                set_workflow("accounts", "内容已准备，请选择本次要生成的公众号")
            else:
                set_workflow(
                    "generate",
                    f"已选择 {selected_count} 个公众号，下一步开始生成文章",
                )

        def on_target_accounts_change(_: Any = None) -> None:
            state.remember_account_ids(
                [str(item) for item in list(target_accounts.value or [])]
            )
            sync_default_model_row()
            sync_workflow_before_generation()

        target_accounts.on_value_change(on_target_accounts_change)
        for element in (
            source,
            manual_in,
            source_mode_in,
            url_in,
            text_in,
            references_in,
        ):
            element.on_value_change(lambda _event: sync_workflow_before_generation())
        sync_default_model_row()
        sync_workflow_before_generation()
        with ui.row().classes("items-center justify-between w-full q-mb-sm"):
            status_label = ui.label("就绪").classes("status-pill")
            elapsed_label = ui.label("").classes("progress-elapsed")
        with ui.element("div").classes("rewrite-progress w-full") as progress_panel:
            with ui.row().classes("items-center justify-between w-full progress-heading"):
                ui.label("处理进度").classes("progress-caption")
                progress_percent = ui.label("0%").classes("progress-percent")
            with ui.element("div").classes("progress-track-wrap w-full"):
                progress_bar = (
                    ui.linear_progress(value=0, show_value=False)
                    .classes("w-full progress-bar")
                    .props("rounded color=teal-8 track-color=teal-1 size=30px")
                )
                progress_stage = ui.label("准备开始").classes("progress-stage")
            progress_hint = ui.label("完成后会自动进入标题选择与正文预览").classes(
                "progress-hint"
            )
        progress_panel.set_visibility(False)
        log_area = (
            ui.textarea(value="等待开始…")
            .classes("w-full q-mt-sm")
            .props(
                "readonly outlined rows=5 "
                'input-style="font-family:Consolas,monospace;font-size:12px"'
            )
        )
        with ui.row().classes("items-center"):
            start_btn = ui.button("开始生成文章").props(
                "unelevated color=teal-9 no-caps"
            )
            stop_btn = ui.button("停止生成").props(
                "unelevated color=red-7 no-caps icon=stop_circle"
            )
            stop_btn.set_visibility(False)

        def show_rewrite_action(*, running: bool) -> None:
            """Keep start/stop in one visual slot; never show both together."""
            if not ui_alive():
                return
            start_btn.set_visibility(not running)
            stop_btn.set_visibility(running)
            if running:
                stop_btn.enable()
            else:
                start_btn.enable()

        active_cancel_event: threading.Event | None = None
        active_task_items: list[dict[str, Any]] = []
        active_batch_id: str | None = None

        def append_log(msg: str) -> None:
            if not ui_alive():
                return
            prev = log_area.value or ""
            if prev in {"", "等待开始…"}:
                log_area.value = msg
            else:
                log_area.value = prev + "\n" + msg

        def stop_rewrite() -> None:
            if active_cancel_event is None or not state.busy:
                ui.notify("当前没有正在进行的改写任务", type="warning")
                return
            active_cancel_event.set()
            stop_btn.disable()
            status_label.text = "正在终止…"
            progress_stage.text = "正在终止所有公众号任务"
            progress_hint.text = "当前模型请求返回后将停止，不会继续写入草稿箱"
            append_log("已请求停止生成，正在停止各公众号任务…")
            for item in active_task_items:
                job_id = int(item["job_id"])
                current = item["pipe"].db.get_job(job_id) or {}
                if str(current.get("status") or "") not in TERMINAL_STATUSES:
                    item["pipe"].db.update_job(
                        job_id,
                        status="cancelled",
                        error="用户已终止改写",
                    )
            if active_batch_id:
                current_batch = state.db.get_batch(active_batch_id) or {}
                current_jobs = list(current_batch.get("jobs") or [])
                state.db.update_batch(
                    active_batch_id,
                    status=effective_batch_status(current_jobs, "cancelled"),
                )

        stop_btn.on_click(stop_rewrite)

        async def confirm_preflight(account_ids: list[str]) -> bool:
            check_ids = [
                IMPORTED_DEFAULT_ACCOUNT_ID if item == DEFAULT_ACCOUNT_ID else item
                for item in account_ids
            ]
            status_label.text = "正在检查发布环境…"
            progress_panel.set_visibility(True)
            progress_panel.run_method(
                "scrollIntoView", {"behavior": "smooth", "block": "center"}
            )
            progress_bar.value = 0.02
            progress_stage.text = "检查公众号、模型、模板和素材接口"
            progress_percent.text = "2%"
            progress_hint.text = "发布环境检查通过后将立即创建并发生成任务"
            try:
                reports = await run.io_bound(
                    lambda: BatchService(load_config()).preflight(check_ids)
                )
            except Exception as exc:  # noqa: BLE001
                if ui_alive():
                    ui.notify(f"发布环境检查失败：{exc}", type="negative", timeout=10000)
                return False
            if not ui_alive():
                return False
            if all(item.get("can_write") for item in reports):
                ui.notify("发布环境检查通过", type="positive")
                return True

            with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width:760px"):
                ui.label("发布环境检查发现问题").classes("text-h6 text-weight-bold")
                for report in reports:
                    with ui.element("div").classes("card w-full"):
                        ui.label(str(report.get("account_name") or "公众号")).classes(
                            "text-weight-bold"
                        )
                        for check in report.get("checks") or []:
                            ui.label(
                                ("✓ " if check.get("ok") else "✕ ")
                                + str(check.get("name") or "")
                                + "："
                                + str(check.get("message") or "")
                            ).classes("text-positive" if check.get("ok") else "text-negative")
                can_generate = all(item.get("can_generate") for item in reports)
                ui.label(
                    "可以仅生成文章并进入审核，但配置修复前无法写入草稿箱。"
                    if can_generate
                    else "至少一个公众号的模型不可用，无法开始生成。"
                ).classes("text-warning")
                with ui.row().classes("w-full justify-end"):
                    ui.button("修复配置后再开始", on_click=lambda: dialog.submit(False)).props(
                        "flat no-caps"
                    )
                    if can_generate:
                        ui.button(
                            "仅生成文章",
                            on_click=lambda: dialog.submit(True),
                        ).props("unelevated color=orange-8 no-caps")
            return bool(await dialog)

        async def start_rewrite() -> None:
            nonlocal active_cancel_event, active_task_items, active_batch_id
            if state.busy:
                ui.notify("已有任务在处理", type="warning")
                return
            topic = state.selected_topic.strip()
            if source.value == "manual":
                topic = (manual_in.value or "").strip()
                set_topic(topic, "manual")
            url = (url_in.value or "").strip()
            text = (text_in.value or "").strip()
            source_mode_value = str(source_mode_in.value or "link")
            reference_urls = [
                line.strip()
                for line in str(references_in.value or "").splitlines()
                if line.strip()
            ]
            if not topic:
                ui.notify("请先选择或输入话题", type="warning")
                return
            if source_mode_value == "link" and not url:
                ui.notify("请填写公众号文章链接", type="warning")
                return
            if source_mode_value == "text" and not text:
                ui.notify("请粘贴文章正文", type="warning")
                return
            if source_mode_value == "references" and not reference_urls:
                ui.notify("请至少填写一个参考文章链接", type="warning")
                return
            if source_mode_value != "link":
                url = ""
            if source_mode_value != "text":
                text = ""
            selected_accounts = list(target_accounts.value or [])
            if not selected_accounts:
                ui.notify("请至少选择一个要生成文章的公众号", type="warning")
                return

            state.busy = True
            set_workflow(
                "generate",
                f"正在同时为 {len(selected_accounts)} 个公众号生成文章",
            )
            active_cancel_event = threading.Event()
            active_task_items = []
            show_rewrite_action(running=True)
            status_label.text = "准备生成…"
            status_label.classes(replace="status-pill")
            progress_panel.set_visibility(True)
            progress_panel.run_method(
                "scrollIntoView", {"behavior": "smooth", "block": "center"}
            )
            progress_bar.value = 0.01
            progress_bar.props("color=teal-8 track-color=teal-1 size=30px")
            progress_stage.text = "正在启动生成流程"
            progress_percent.text = "1%"
            progress_hint.text = "正在准备发布环境检查…"
            elapsed_label.text = "已用时 0 秒"
            log_area.value = f"话题：{topic}"
            append_log(f"来源：{state.topic_source}")
            if url:
                append_log(f"链接：{url}")
            append_log(f"目标公众号：{len(selected_accounts)} 个")
            append_log("正在检查公众号、模型、模板和素材接口…")
            review_host.clear()
            preflight_ok = await confirm_preflight(selected_accounts)
            if not ui_alive():
                state.busy = False
                active_cancel_event = None
                active_task_items = []
                return
            if active_cancel_event.is_set():
                state.busy = False
                show_rewrite_action(running=False)
                status_label.text = "已停止"
                progress_stage.text = "已停止，不再创建生成任务"
                progress_percent.text = "已停止"
                progress_hint.text = "发布环境检查可能已完成，但没有继续生成文章"
                active_cancel_event = None
                return
            if not preflight_ok:
                state.busy = False
                show_rewrite_action(running=False)
                status_label.text = "等待修复配置"
                progress_stage.text = "发布环境检查未通过"
                progress_percent.text = "未开始"
                active_cancel_event = None
                return

            status_label.text = "改写中…"
            status_label.classes(replace="status-pill")
            progress_panel.set_visibility(True)
            progress_bar.value = 0.03
            progress_bar.props("color=teal-8 track-color=teal-1 size=30px")
            progress_stage.text = "正在创建任务"
            progress_percent.text = "3%"
            progress_hint.text = "正在准备处理队列…"
            elapsed_label.text = "已用时 0 秒"
            log_area.value = f"话题：{topic}"
            append_log(f"来源：{state.topic_source}")
            if url:
                append_log(f"链接：{url}")
            append_log(f"目标公众号：{len(selected_accounts)} 个")
            append_log("每个公众号将使用绑定模型独立改写；审核后可一次性写入全部草稿箱…")
            review_host.clear()

            primary_id = str(primary_model.value or "") or None
            fallback_id = str(fallback_model.value or "") or None
            task_items: list[dict[str, Any]] = []
            batch_mode = len(selected_accounts) > 1 or selected_accounts[0] != DEFAULT_ACCOUNT_ID
            active_batch_id = uuid.uuid4().hex[:16]
            state.db.create_batch(
                active_batch_id,
                topic=topic,
                source_url=url or None,
                raw_content=text or None,
                source_mode=source_mode_value,
                reference_urls=reference_urls,
                required_facts=str(facts_in.value or ""),
                rewrite_intensity=str(intensity_in.value or "standard"),
            )
            state.db.update_batch(active_batch_id, status="processing", error="")
            try:
                for account_id in selected_accounts:
                    cfg = load_config()
                    if account_id == DEFAULT_ACCOUNT_ID:
                        account_name = "系统默认公众号"
                        model_id = primary_id or str((cfg.get("ai") or {}).get("primary") or "")
                        selected_model = state.db.get_ai_model(model_id) if primary_id else None
                        config_model = next(
                            (m for m in configured_models(cfg) if m["id"] == model_id),
                            None,
                        )
                        model_name = str(
                            (selected_model or {}).get("name")
                            or (config_model or {}).get("name")
                            or model_id
                            or "配置默认"
                        )
                        if primary_id:
                            cfg = apply_model_selection(
                                cfg, state.db, primary_id, fallback_id or primary_id
                            )
                    else:
                        cfg, account_record = apply_account_selection(
                            cfg, state.db, str(account_id)
                        )
                        account_name = str(account_record["name"])
                        model_id = str(account_record["model_id"])
                        selected_model = state.db.get_ai_model(model_id)
                        config_model = next(
                            (m for m in configured_models(cfg) if m["id"] == model_id),
                            None,
                        )
                        model_name = str(
                            (selected_model or {}).get("name")
                            or (config_model or {}).get("name")
                            or model_id
                        )
                    pipe = Pipeline(cfg, cancel_event=active_cancel_event)
                    job_id = pipe.db.create_job(
                        topic=topic,
                        source=state.topic_source,
                        source_url=url or None,
                        raw_content=text or None,
                        mode="draft",
                        meta={
                            "review": True,
                            "batch_id": active_batch_id,
                            "source_mode": source_mode_value,
                            "reference_urls": reference_urls,
                            "required_facts": str(facts_in.value or ""),
                            "rewrite_intensity": str(intensity_in.value or "standard"),
                            "cover_media_id": None,
                            "official_account_id": account_id,
                            "official_account_name": account_name,
                            "selected_model_id": model_id,
                            "selected_model_name": model_name,
                            "fallback_model_id": fallback_id if account_id == DEFAULT_ACCOUNT_ID else model_id,
                        },
                    )
                    task_items.append(
                        {
                            "pipe": pipe,
                            "job_id": job_id,
                            "account_name": account_name,
                        }
                    )
                    state.db.attach_batch_job(
                        active_batch_id, job_id, str(account_id), account_name
                    )
                    active_task_items = task_items
            except Exception as exc:  # noqa: BLE001
                for item in task_items:
                    state.db.update_job(
                        int(item["job_id"]),
                        status="failed",
                        error=f"批次初始化失败，未开始生成：{exc}",
                    )
                state.db.update_batch(active_batch_id, status="failed", error=str(exc))
                state.busy = False
                show_rewrite_action(running=False)
                ui.notify(f"公众号或模型配置不可用：{exc}", type="negative")
                return

            followed_article_id = pending_rewrite_origin.get("followed_article_id")
            topic_item_id = pending_rewrite_origin.get("topic_item_id")
            if followed_article_id:
                state.db.update_followed_article(
                    followed_article_id,
                    is_read=True,
                    rewritten_batch_id=active_batch_id,
                )
            if topic_item_id:
                state.db.update_topic_item_flags(topic_item_id, used=True)
            pending_rewrite_origin.clear()

            state.wizard_job_id = int(task_items[0]["job_id"])
            stage_ui = {
                "pending": (0.03, 0.08, "正在创建任务", "任务已经进入处理队列"),
                "ingesting": (0.10, 0.22, "正在抓取并清洗原文", "正在提取文章正文与基础信息"),
                "rewriting": (0.28, 0.70, "AI 正在改写正文", "这是通常耗时最长的阶段，请耐心等待"),
                "title_optimizing": (0.74, 0.87, "正在整理标题与副标题", "正在本地校验、去重并筛选候选标题"),
                "rendering": (0.90, 0.98, "正在套用历史排版样式", "正在生成蓝血经营管理系统正文"),
                "injecting": (0.98, 0.995, "正在写入公众号草稿箱", "正在调用该公众号的草稿接口"),
                "drafted": (1.0, 1.0, "已写入草稿箱", "该公众号已完成"),
                "ready_for_review": (1.0, 1.0, "改写与排版已完成", "请选择标题并预览正文"),
                "failed": (1.0, 1.0, "处理失败", "请查看下方错误信息"),
                "cancelled": (1.0, 1.0, "已停止生成", "不会继续写入草稿箱"),
            }

            started_at = time.monotonic()
            stage_started_at = {
                int(item["job_id"]): started_at for item in task_items
            }
            last_status = {int(item["job_id"]): "pending" for item in task_items}

            async def update_progress(task: asyncio.Task[list[dict[str, Any]]]) -> None:
                while not task.done() and ui_alive():
                    now = time.monotonic()
                    values: list[float] = []
                    active_labels: list[str] = []
                    done_count = 0
                    latest_hint = "各公众号任务正在同时进行"
                    for item in task_items:
                        job_id = int(item["job_id"])
                        current = item["pipe"].db.get_job(job_id) or {}
                        current_status = str(current.get("status") or "pending")
                        if current_status != last_status[job_id]:
                            last_status[job_id] = current_status
                            stage_started_at[job_id] = now
                            append_log(
                                f'[{item["account_name"]}] '
                                f'{STATUS_LABEL.get(current_status, current_status)}'
                            )
                        base, ceiling, label, hint = stage_ui.get(
                            current_status,
                            (0.05, 0.95, STATUS_LABEL.get(current_status, current_status), "正在处理…"),
                        )
                        stage_value = min(
                            ceiling,
                            base + (now - stage_started_at[job_id]) * 0.0025,
                        )
                        values.append(stage_value)
                        if current_status in {"drafted", "published", "ready_for_review", "failed", "cancelled"}:
                            done_count += 1
                        else:
                            active_labels.append(f'{item["account_name"]}：{label}')
                            latest_hint = hint
                    value = sum(values) / max(len(values), 1)
                    progress_bar.value = value
                    progress_stage.text = " ｜ ".join(active_labels[:3]) or "正在汇总结果"
                    progress_percent.text = f"{round(value * 100)}%"
                    progress_hint.text = (
                        f"{done_count}/{len(task_items)} 个公众号完成 · {latest_hint}"
                    )
                    elapsed = int(time.monotonic() - started_at)
                    elapsed_label.text = f"已用时 {elapsed // 60}分{elapsed % 60:02d}秒" if elapsed >= 60 else f"已用时 {elapsed}秒"
                    await asyncio.sleep(0.8)

            task = asyncio.create_task(
                run_pipelines_concurrently(
                    task_items,
                    review=True,
                    cancel_event=active_cancel_event,
                )
            )
            batch_id_for_run = active_batch_id

            async def wait_and_finalize() -> tuple[
                list[dict[str, Any]], dict[str, int | str]
            ]:
                """Finish business state even if the page disconnects."""

                try:
                    pipeline_results = await task
                except Exception as exc:
                    if batch_id_for_run:
                        state.db.update_batch(
                            batch_id_for_run,
                            status="failed",
                            error=str(exc),
                        )
                    raise
                return (
                    pipeline_results,
                    _finalize_generated_batch(
                        state.db,
                        batch_id_for_run,
                        pipeline_results,
                    ),
                )

            finalizer_task = asyncio.create_task(wait_and_finalize())

            try:
                await update_progress(task)
                results, counts = await asyncio.shield(finalizer_task)
                if not ui_alive():
                    logger.info(
                        "rewrite batch %s completed after its UI client disconnected",
                        batch_id_for_run,
                    )
                    return
                job = results[0]
                state.wizard_job_id = int(job["id"])
                st = str(job.get("status"))
                progress_bar.value = 1.0
                progress_percent.text = "100%"
                progress_stage.text = "所有公众号处理完成"
                progress_hint.text = "请逐个切换公众号预览配图，确认后再写入各自草稿箱"
                drafted_count = int(counts["drafted"])
                review_count = int(counts["ready_for_review"])
                failed_count = int(counts["failed"])
                cancelled_count = int(counts["cancelled"])
                status_label.text = (
                    f"待确认 {review_count} · 已写入草稿箱 {drafted_count} · 失败 {failed_count} · 已停止 {cancelled_count}"
                    if batch_mode
                    else STATUS_LABEL.get(st, st)
                )
                status_label.classes(
                    replace=f"status-pill {st if st in ('failed', 'ready_for_review') else ''}".strip()
                )
                for result, item in zip(results, task_items):
                    result_status = str(result.get("status") or "failed")
                    append_log(
                        f'{item["account_name"]} · Job #{result["id"]} → '
                        f'{STATUS_LABEL.get(result_status, result_status)}'
                    )
                    if result.get("error"):
                        append_log(f'  错误：{result["error"]}')
                if cancelled_count:
                    progress_stage.text = "改写已终止"
                    progress_hint.text = "已停止后续处理，不会继续写入草稿箱"
                    set_workflow(
                        "generate",
                        "本批次已停止，请到任务中心查看详情",
                        completed=True,
                    )
                    ui.notify(
                        f"已终止 {cancelled_count} 篇文章，正在进入任务中心",
                        type="warning",
                    )
                else:
                    set_workflow(
                        "generate",
                        f"{review_count} 篇文章生成完成，进入统一审核",
                        completed=True,
                    )
                    notify_type = "positive" if failed_count == 0 else "warning"
                    ui.notify(
                        f"生成完成：{review_count} 篇待审核，{failed_count} 篇失败；"
                        "已进入任务中心",
                        type=notify_type,
                        timeout=10000,
                    )
                review_host.clear()
                with review_host:
                    with ui.element("div").classes("card w-full"):
                        ui.label("本次生成已进入任务中心").classes(
                            "text-subtitle1 text-weight-bold"
                        )
                        ui.label(
                            "文章审核、AI 评审、失败重试和批量写入统一在任务中心处理。"
                        ).classes("muted")
                        ui.button(
                            "进入任务中心",
                            on_click=lambda: tabs.set_value(tab_jobs),
                        ).props("unelevated color=teal-9 no-caps icon=task_alt")
                if callable(state.task_center_refresh):
                    state.task_center_refresh(active_batch_id)
                tabs.set_value(tab_jobs)
            except asyncio.CancelledError:
                logger.info(
                    "rewrite UI callback for batch %s was cancelled; "
                    "the pipeline finalizer will continue in the background",
                    batch_id_for_run,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("rewrite failed")
                if not ui_alive():
                    return
                status_label.text = "失败"
                status_label.classes(replace="status-pill failed")
                progress_bar.value = 1.0
                progress_bar.props("color=red-7 track-color=red-1")
                progress_percent.text = "失败"
                progress_stage.text = "处理失败"
                progress_hint.text = "请查看下方错误信息后重试"
                set_workflow("generate", "生成未完成，请查看错误后重试")
                err = str(exc)
                append_log(f"错误：{err}")
                if "过载" in err or "429" in err or "overloaded" in err.lower():
                    ui.notify(
                        "AI 服务繁忙，请稍等 1–2 分钟再点「开始生成文章」",
                        type="warning",
                        timeout=12000,
                    )
                else:
                    ui.notify(f"失败：{exc}", type="negative", timeout=10000)
            finally:
                state.busy = False
                active_task_items = []
                active_cancel_event = None
                show_rewrite_action(running=False)

        start_btn.on_click(start_rewrite)

    review_host = ui.column().classes("review-section w-full")

    def open_batch_review(job_id: int, batch_results: list[dict[str, Any]]) -> None:
        """Review one account in a modal without replacing the batch result tabs."""
        with ui.dialog() as dialog, ui.card().classes("w-full").style(
            "max-width:1100px;max-height:92vh;overflow-y:auto"
        ):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("检查配图并写入草稿").classes("text-h6 text-weight-bold")
                ui.button("关闭", on_click=dialog.close).props(
                    "flat dense color=grey-8 no-caps icon=close"
                )
            modal_host = ui.column().classes("w-full")

        def refresh_batch_after_inject(_job: dict[str, Any]) -> None:
            dialog.close()
            refreshed = [
                state.db.get_job(int(item["id"])) or item for item in batch_results
            ]
            render_batch_results(refreshed)

        dialog.open()
        render_review(
            job_id,
            target_host=modal_host,
            on_completed=refresh_batch_after_inject,
        )

    def _legacy_render_batch_results(results: list[dict[str, Any]]) -> None:
        review_host.clear()
        ready_jobs = [job for job in results if job.get("status") == "ready_for_review"]
        title_selectors: dict[int, Any] = {}
        subtitle_selectors: dict[int, Any] = {}

        async def inject_all_reviewed() -> None:
            if state.busy:
                ui.notify("已有任务正在处理", type="warning")
                return
            state.busy = True
            _set_button_loading(
                batch_inject_btn,
                True,
                f"正在同时写入 {len(ready_jobs)} 个公众号草稿箱，请稍候…",
            )
            batch_inject_status.text = f"正在同时写入 {len(ready_jobs)} 个公众号草稿箱…"
            task_items: list[dict[str, Any]] = []
            try:
                for summary in ready_jobs:
                    job_id = int(summary["id"])
                    latest = state.db.get_job(job_id) or summary
                    meta = dict(latest.get("meta") or {})

                    cfg = load_config()
                    account_id = str(meta.get("official_account_id") or "")
                    if account_id:
                        cfg, _ = apply_account_selection(
                            cfg, state.db, account_id, allow_disabled=True
                        )
                    if not account_id or account_id == DEFAULT_ACCOUNT_ID:
                        model_id = str(meta.get("selected_model_id") or "")
                        configured_primary = str((cfg.get("ai") or {}).get("primary") or "")
                        if model_id and model_id != configured_primary:
                            cfg = apply_model_selection(
                                cfg,
                                state.db,
                                model_id,
                                str(meta.get("fallback_model_id") or model_id),
                            )
                    pipe = Pipeline(cfg)
                    selector = title_selectors.get(job_id)
                    selected = str(
                        (selector.value if selector is not None else "")
                        or latest.get("selected_title")
                        or ""
                    )
                    if selected:
                        state.db.update_job(job_id, selected_title=selected)
                    subtitle_selector = subtitle_selectors.get(job_id)
                    if subtitle_selector is not None:
                        selected_subtitle = str(subtitle_selector.value or "")
                        state.db.update_job(
                            job_id,
                            selected_subtitle=(
                                None
                                if selected_subtitle == "（不使用副标题）"
                                else selected_subtitle
                            ),
                        )
                    task_items.append(
                        {
                            "pipe": pipe,
                            "job_id": job_id,
                            "title_index": None,
                        }
                    )

                updated = await inject_pipelines_concurrently(task_items)
                drafted = sum(1 for job in updated if job.get("status") == "drafted")
                failed = len(updated) - drafted
                ui.notify(
                    f"批量写入完成：{drafted} 个已进入草稿箱，{failed} 个失败",
                    type="positive" if failed == 0 else "warning",
                    timeout=10000,
                )
                refreshed = [
                    state.db.get_job(int(item["id"])) or item for item in results
                ]
                render_batch_results(refreshed)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"批量写入失败：{exc}", type="negative", timeout=10000)
                batch_inject_status.text = f"写入失败：{exc}"
            finally:
                state.busy = False
                try:
                    _set_button_loading(batch_inject_btn, False)
                except Exception:  # noqa: BLE001
                    pass

        with review_host:
            with ui.element("div").classes("card w-full"):
                ui.html(step_title_html(4, "多公众号处理结果"), sanitize=False)
                ui.label(
                    "点击公众号名称切换审核各自文章；全部确认后，只需点击一次即可同时写入各公众号草稿箱。"
                ).classes("muted")
                batch_inject_status = ui.label(
                    f"{len(ready_jobs)} 篇文章待审核写入"
                    if ready_jobs
                    else "当前没有待写入文章"
                ).classes("muted q-mt-sm")
                batch_inject_btn = ui.button(
                    "全部写入草稿箱", on_click=inject_all_reviewed
                ).props("unelevated color=teal-9 no-caps icon=cloud_upload").classes(
                    "q-mt-sm"
                )
                if not ready_jobs:
                    batch_inject_btn.disable()
            result_tabs = ui.tabs().classes("workspace-tabs w-full").props(
                "dense align=left indicator-color=teal-9 active-color=teal-10"
            )
            tab_jobs: list[tuple[Any, dict[str, Any]]] = []
            with result_tabs:
                for job in results:
                    meta = job.get("meta") or {}
                    account_name = str(meta.get("official_account_name") or "默认公众号")
                    status = str(job.get("status") or "failed")
                    marker = "✓" if status in {"drafted", "published"} else "!"
                    tab_jobs.append((ui.tab(f"{account_name} {marker}"), job))

            if not tab_jobs:
                return
            result_panels = ui.tab_panels(
                result_tabs, value=tab_jobs[0][0]
            ).classes("w-full bg-transparent")
            with result_panels:
                for result_tab, job in tab_jobs:
                    meta = job.get("meta") or {}
                    account_name = str(meta.get("official_account_name") or "默认公众号")
                    status = str(job.get("status") or "failed")
                    body = str(job.get("body") or "")
                    html = str(job.get("html_content") or "")
                    body_chars = len("".join(body.split()))
                    with ui.tab_panel(result_tab).classes("q-pa-none q-pt-md"):
                        with ui.element("div").classes("card w-full"):
                            with ui.row().classes("w-full items-center justify-between"):
                                with ui.column().classes("gap-0"):
                                    ui.label(account_name).classes("text-h6 text-weight-bold")
                                    ui.label(
                                        f'任务 #{job.get("id")} · 模型：'
                                        f'{meta.get("selected_model_name") or meta.get("selected_model_id") or "配置默认"}'
                                    ).classes("muted")
                                ui.label(STATUS_LABEL.get(status, status)).classes(
                                    "text-positive text-weight-bold"
                                    if status in {"drafted", "published"}
                                    else "text-negative text-weight-bold"
                                )
                            job_id = int(job["id"])
                            titles = _clean_titles(job)
                            selected_title = clean_candidate_text(
                                str(job.get("selected_title") or "")
                            )
                            if selected_title and selected_title not in titles:
                                titles = [selected_title, *titles[:9]]
                            if status == "ready_for_review" and titles:
                                ui.label("请选择该公众号使用的标题").classes(
                                    "text-weight-bold q-mt-md"
                                )
                                initial_title = (
                                    selected_title if selected_title in titles else titles[0]
                                )
                                title_radio = ui.radio(
                                    {title: title for title in titles},
                                    value=initial_title,
                                ).classes("w-full q-mt-xs")
                                title_selectors[job_id] = title_radio
                                selected_title_label = ui.label(initial_title).classes(
                                    "text-h6 text-weight-bold q-mt-sm"
                                )

                                def update_selected_title(
                                    event: Any,
                                    jid: int = job_id,
                                    label: Any = selected_title_label,
                                ) -> None:
                                    value = str(event.value or "")
                                    if not value:
                                        return
                                    state.db.update_job(jid, selected_title=value)
                                    label.text = value

                                title_radio.on_value_change(update_selected_title)
                            elif selected_title:
                                ui.label(selected_title).classes(
                                    "text-h6 text-weight-bold q-mt-md"
                                )
                            if status == "ready_for_review":
                                subtitles = _clean_subtitles(job)
                                current_subtitle = clean_candidate_text(
                                    str(job.get("selected_subtitle") or "")
                                )
                                if current_subtitle and current_subtitle not in subtitles:
                                    subtitles = [current_subtitle, *subtitles[:9]]
                                if subtitles:
                                    ui.label("副标题（可选）").classes(
                                        "text-weight-bold q-mt-md"
                                    )
                                    subtitle_options = ["（不使用副标题）", *subtitles]
                                    subtitle_select = ui.select(
                                        options=subtitle_options,
                                        value=(
                                            current_subtitle
                                            if current_subtitle in subtitles
                                            else "（不使用副标题）"
                                        ),
                                    ).classes("w-full").props(
                                        "outlined dense options-dense"
                                    )
                                    subtitle_selectors[job_id] = subtitle_select
                            ui.label(f"正文 {body_chars} 字").classes("muted")
                            quality = meta.get("layout_quality") or {}
                            if quality.get("errors"):
                                ui.label(
                                    "排版检查未通过：" + "；".join(quality["errors"])
                                ).classes("text-negative q-mt-sm")
                            elif quality:
                                ui.label(
                                    f'排版检查通过 · {quality.get("paragraph_count", 0)} 个段落'
                                    f' · {quality.get("image_count", 0)} 张图片'
                                ).classes("text-positive q-mt-sm")
                            if job.get("draft_media_id"):
                                ui.label("已保存到该公众号草稿箱").classes(
                                    "text-positive q-mt-sm"
                                )
                            if job.get("error"):
                                ui.label(f'错误：{job["error"]}').classes(
                                    "text-negative q-mt-sm"
                                )
                            if status == "ready_for_review":
                                ui.label("已纳入上方批量写入队列").classes(
                                    "text-positive text-caption q-mt-sm"
                                )
                            with ui.element("div").classes("preview-frame w-full q-mt-md"):
                                if html:
                                    ui.html(prepare_preview_html(html), sanitize=False)
                                elif body:
                                    ui.markdown(body.replace("\n", "\n\n"))
                                else:
                                    ui.label("该任务没有生成可预览的正文。 ").classes("muted")

    def render_batch_results(results: list[dict[str, Any]]) -> None:
        """Render the same explicit batch review state used by API and Feishu."""
        review_host.clear()
        first_meta = dict((results[0].get("meta") or {})) if results else {}
        batch_id = str(first_meta.get("batch_id") or "")
        if not batch_id:
            with review_host:
                ui.label("这些是升级前的历史独立任务，请到任务中心单独查看。").classes(
                    "card text-warning"
                )
            return
        service = BatchService(load_config())
        batch = service.get_batch(batch_id, include_content=True)
        jobs = list(batch.get("jobs") or [])
        progress = batch.get("progress") or {}
        unconfirmed = int(progress.get("unconfirmed") or 0)
        ready_count = int(progress.get("ready_for_review") or 0)
        drafted_count = int(progress.get("drafted") or 0)
        failed_count = int(progress.get("failed") or 0)
        if unconfirmed:
            set_workflow("review", f"还需审核确认 {unconfirmed} 篇文章")
        elif ready_count:
            set_workflow("draft", f"{ready_count} 篇文章已确认，可以批量写入草稿箱")
        elif jobs and drafted_count == len(jobs):
            set_workflow("draft", "本批次已全部写入草稿箱", completed=True)
        elif failed_count:
            set_workflow("review", f"有 {failed_count} 篇失败，可到任务中心重试")

        def refresh() -> None:
            latest = service.get_batch(batch_id, include_content=True)
            render_batch_results(list(latest.get("jobs") or []))

        with review_host:
            with ui.element("div").classes("card w-full"):
                ui.html(step_title_html(4, "多公众号文章审核"), sanitize=False)
                ui.label(
                    f'批次 #{batch.get("display_id")} · 已审核 '
                    f'{progress.get("reviewed", 0)}/{progress.get("review_total", 0)}'
                    f' · 尚有 {progress.get("unconfirmed", 0)} 篇未确认'
                ).classes(
                    "text-warning" if progress.get("unconfirmed") else "text-positive"
                )
                ui.label(
                    "必须逐篇打开统一审核工作台并点击“确认此文章”，候选标题的默认值不会自动算作审核。"
                ).classes("muted")
                pending_job = next_review_job(jobs)
                if pending_job is not None:
                    ui.button(
                        f"审核下一篇（剩余 {unconfirmed} 篇）",
                        on_click=lambda _=None, jid=int(pending_job["id"]): open_review_workbench(
                            state, service, batch_id, jid, refresh
                        ),
                    ).props("unelevated color=teal-9 no-caps icon=rate_review")
                else:
                    write_btn = ui.button(
                        f"写入已确认的 {ready_count} 篇",
                        on_click=lambda: confirm_batch_write(service, batch, refresh),
                    ).props("unelevated color=teal-9 no-caps icon=cloud_upload")
                    if not ready_count:
                        write_btn.disable()

            tabs = ui.tabs().classes("workspace-tabs w-full").props(
                "dense align=left indicator-color=teal-9 active-color=teal-10"
            )
            tab_jobs: list[tuple[Any, dict[str, Any]]] = []
            with tabs:
                for job in jobs:
                    status = str(job.get("status") or "")
                    review_status = str(job.get("review_status") or "unviewed")
                    marker = (
                        "✓"
                        if status in {"drafted", "published"} or review_status == "confirmed"
                        else ("●" if status == "ready_for_review" else "!")
                    )
                    tab_jobs.append(
                        (ui.tab(f'{job.get("account_name") or "公众号"} {marker}'), job)
                    )
            if not tab_jobs:
                return
            panels = ui.tab_panels(tabs, value=tab_jobs[0][0]).classes(
                "w-full bg-transparent"
            )
            with panels:
                for tab, job in tab_jobs:
                    with ui.tab_panel(tab).classes("q-pa-none q-pt-md"):
                        with ui.element("div").classes("card w-full"):
                            with ui.row().classes("w-full items-center justify-between"):
                                with ui.column().classes("gap-0"):
                                    ui.label(str(job.get("account_name") or "公众号")).classes(
                                        "text-h6 text-weight-bold"
                                    )
                                    ui.label(
                                        f'任务 #{job.get("id")} · 模型：{job.get("model_name") or "配置默认"}'
                                    ).classes("muted")
                                if job.get("status") == "ready_for_review":
                                    review_status = str(job.get("review_status") or "unviewed")
                                    ui.badge(
                                        {
                                            "unviewed": "未查看",
                                            "viewed": "已查看，未确认",
                                            "confirmed": "已确认",
                                            "needs_changes": "需要修改",
                                        }.get(review_status, review_status)
                                    ).props(
                                        "color=teal-7" if review_status == "confirmed" else "color=orange-8"
                                    )
                            if job.get("selected_title"):
                                ui.label(str(job["selected_title"])).classes(
                                    "text-h6 text-weight-bold q-mt-md"
                                )
                            if job.get("error"):
                                ui.label(f'错误：{job["error"]}').classes("text-negative")
                            if job.get("status") == "ready_for_review":
                                ui.button(
                                    "进入文章审核工作台",
                                    on_click=lambda _=None, jid=int(job["id"]): open_review_workbench(
                                        state, service, batch_id, jid, refresh
                                    ),
                                ).props("unelevated color=teal-9 no-caps icon=rate_review")
                            with ui.element("div").classes("preview-frame w-full q-mt-md"):
                                if job.get("html_content"):
                                    ui.html(
                                        prepare_preview_html(str(job["html_content"])),
                                        sanitize=False,
                                    )
                                elif job.get("body"):
                                    ui.markdown(str(job["body"]).replace("\n", "\n\n"))
                                else:
                                    ui.label("暂无可预览正文").classes("muted")

    def render_review(
        job_id: int,
        *,
        target_host: Any | None = None,
        on_completed: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        host = target_host if target_host is not None else review_host
        host.clear()
        state.db = Database(load_config()["_db_path"])
        job = state.db.get_job(job_id)
        if not job:
            with host:
                ui.label("任务不存在").classes("card")
            return

        titles = _clean_titles(job)
        if not titles and job.get("selected_title"):
            selected = clean_candidate_text(str(job["selected_title"]))
            titles = [selected] if selected else []
        subtitles = _clean_subtitles(job)
        html = job.get("html_content") or ""
        body = job.get("body") or ""

        with host:
            with ui.element("div").classes("card"):
                ui.html(step_title_html(4, "选择标题并预览文章"), sanitize=False)
                ui.label(
                    f"任务 #{job_id} · 确认后再写入公众号草稿箱（不会直接群发）"
                ).classes("muted q-mb-sm")
                body_chars = len("".join(str(body).split()))
                count_class = "text-positive" if body_chars >= 2000 else "text-negative"
                ui.label(
                    f"正文 {body_chars} 字 · "
                    + ("已达到不少于 2000 字要求" if body_chars >= 2000 else "未达到 2000 字硬性要求")
                ).classes(f"{count_class} q-mb-sm text-weight-medium")
                quality = (job.get("meta") or {}).get("layout_quality") or {}
                if quality.get("errors"):
                    ui.label(
                        "最终排版检查未通过：" + "；".join(quality["errors"])
                    ).classes("text-negative q-mb-sm")
                elif quality:
                    ui.label(
                        f'最终排版检查通过 · {quality.get("paragraph_count", 0)} 个段落'
                        f' · {quality.get("image_count", 0)} 张图片'
                    ).classes("text-positive q-mb-sm")

                inline_assets = list((job.get("meta") or {}).get("inline_images") or [])
                inline_warnings = list((job.get("meta") or {}).get("inline_image_warnings") or [])
                if inline_warnings:
                    ui.label("配图提示：" + "；".join(inline_warnings)).classes(
                        "text-warning q-mb-sm"
                    )
                if inline_assets:
                    with ui.expansion(
                        f"正文配图（{len(inline_assets)} 张，可在写入前移除）",
                        icon="photo_library",
                    ).classes("w-full q-mb-sm"):
                        for asset in inline_assets:
                            with ui.row().classes("w-full items-center justify-between"):
                                ui.label(
                                    f'{asset.get("index")}. {asset.get("caption") or "正文配图"} · '
                                    + {
                                        "library": "公众号素材库",
                                        "source": "原文图片",
                                        "generated": "AI 生成",
                                        "visual_card": "论点视觉卡片",
                                    }.get(str(asset.get("source") or ""), "正文配图")
                                ).classes("text-caption")

                                def remove_image(
                                    _=None,
                                    image_index=int(asset.get("index") or 0),
                                ) -> None:
                                    from app.inline_images import remove_inline_image

                                    latest = state.db.get_job(job_id) or {}
                                    latest_meta = dict(latest.get("meta") or {})
                                    latest_meta["inline_images"] = [
                                        item for item in (latest_meta.get("inline_images") or [])
                                        if int(item.get("index") or 0) != image_index
                                    ]
                                    state.db.update_job(
                                        job_id,
                                        html_content=remove_inline_image(
                                            str(latest.get("html_content") or ""), image_index
                                        ),
                                        meta_json=latest_meta,
                                    )
                                    ui.notify("已移除该配图", type="positive")
                                    render_review(
                                        job_id,
                                        target_host=host,
                                        on_completed=on_completed,
                                    )

                                ui.button("移除", on_click=remove_image).props(
                                    "flat dense color=red-7 no-caps icon=delete_outline"
                                )

                editor_template_cfg = load_config().get("editor_template") or {}
                if editor_template_cfg.get("enabled", False):
                    template_applied = bool(
                        (job.get("meta") or {}).get("editor_template_applied")
                    )
                    if template_applied:
                        ui.label("已套用：蓝血经营管理系统模板快照").classes(
                            "text-positive q-mb-sm"
                        )
                    else:
                        ui.label(
                            "模板尚未同步：请在公众号编辑器中插入一次“蓝血经营管理系统模板”，"
                            "保留“蓝血经营管理系统正文”并保存临时文章。"
                        ).classes("text-warning")
                        sync_template_btn = ui.button("同步模板并重新排版").props(
                            "outline color=teal-9 no-caps dense"
                        ).classes("q-mb-sm")

                        async def sync_editor_template() -> None:
                            _set_button_loading(sync_template_btn, True)
                            try:
                                def work() -> dict[str, Any]:
                                    from app.wechat.template_snapshot import (
                                        capture_template_snapshot,
                                    )

                                    cfg = load_config()
                                    template_cfg = dict(
                                        cfg.get("editor_template") or {}
                                    )
                                    template_cfg["_root"] = cfg.get("_root")
                                    pipe = Pipeline(cfg)
                                    capture_template_snapshot(
                                        pipe._wechat_client(), template_cfg
                                    )
                                    return pipe.run_job(
                                        job_id, review=True, from_step="render"
                                    )

                                await run.io_bound(work)
                                ui.notify("模板已同步并完成重新排版", type="positive")
                                render_review(
                                    job_id,
                                    target_host=host,
                                    on_completed=on_completed,
                                )
                            except Exception as exc:  # noqa: BLE001
                                ui.notify(
                                    f"同步失败：{exc}", type="negative", timeout=10000
                                )
                            finally:
                                _set_button_loading(sync_template_btn, False)

                        sync_template_btn.on_click(sync_editor_template)

                if not titles:
                    ui.label("没有可用标题，请到任务列表重试。").classes("text-negative")
                    return

                title_radio = ui.radio(
                    {i: t for i, t in enumerate(titles[:10])},
                    value=0,
                ).classes("w-full")

                subtitle_select = None
                if subtitles:
                    ui.label("副标题（可选，用于正文开头）").classes("q-mt-sm muted")
                    subtitle_select = ui.select(
                        options=["（不使用副标题）"] + subtitles[:10],
                        value="（不使用副标题）",
                    ).classes("w-full")

                preview_title = ui.label(titles[0]).classes(
                    "preview-article-title text-h6 text-weight-bold q-mt-md"
                )
                preview_box = ui.element("div").classes("preview-frame w-full q-mt-sm")

                def refresh_preview() -> None:
                    idx = int(title_radio.value or 0)
                    t = titles[idx] if 0 <= idx < len(titles) else titles[0]
                    preview_title.text = t
                    preview_box.clear()
                    with preview_box:
                        if html:
                            ui.html(prepare_preview_html(html), sanitize=False)
                        else:
                            ui.markdown(body.replace("\n", "\n\n") if body else "（无正文）")

                title_radio.on_value_change(lambda _: refresh_preview())
                refresh_preview()

                layout_box = ui.column().classes("w-full q-mt-md gap-1")
                with layout_box:
                    ui.label("⑤ 下方图文编排（来自草稿箱「已有内容」）").classes(
                        "text-weight-bold"
                    )
                    ui.label(
                        "写入时会把草稿箱里所有标题含「广告」的条目挂上（随机排序），"
                        "再按图片同步蓝血研究最新标题。"
                    ).classes("muted")
                    layout_list = ui.column().classes("w-full gap-1")
                    with layout_list:
                        ui.label("正在读取草稿箱可用次条…").classes("muted")

                async def load_layout_preview() -> None:
                    layout_list.clear()
                    cfg = load_config()
                    layout_cfg = cfg.get("layout") or {}
                    if not layout_cfg.get("enabled", True):
                        with layout_list:
                            ui.label("多图文编排已关闭（config.layout.enabled=false）").classes(
                                "muted"
                            )
                        return
                    try:
                        from app.layout import select_secondary_articles

                        pipe = state.pipeline()
                        secs = await run.io_bound(
                            lambda: select_secondary_articles(
                                pipe._wechat_client(),
                                layout_cfg,
                                exclude_titles=[str(job.get("selected_title") or "")],
                            )
                        )
                        benchmark_cfg = cfg.get("benchmark") or {}
                        if benchmark_cfg.get("enabled", False):
                            from app.benchmark import (
                                fetch_latest_benchmark_record,
                                sync_secondary_titles,
                            )

                            record = await run.io_bound(
                                lambda: fetch_latest_benchmark_record(cfg, pipe.db)
                            )
                            secs = await run.io_bound(
                                lambda: sync_secondary_titles(
                                    secs,
                                    record,
                                    threshold=float(
                                        benchmark_cfg.get("image_match_threshold") or 0.90
                                    ),
                                    matched_only=bool(
                                        benchmark_cfg.get("matched_only", False)
                                    ),
                                    follow_source_order=bool(
                                        benchmark_cfg.get("follow_source_order", True)
                                    ),
                                    deduplicate_by_image=bool(
                                        benchmark_cfg.get("deduplicate_by_image", True)
                                    ),
                                )
                            )
                    except Exception as exc:  # noqa: BLE001
                        with layout_list:
                            ui.label(f"读取次条失败：{exc}").classes("text-negative")
                        return
                    with layout_list:
                        if not secs:
                            ui.label(
                                "未匹配到次条草稿。请确认草稿箱有标题含「广告」的内容，"
                                "或在 config.yaml 的 layout.secondary_media_ids 填入草稿 media_id。"
                            ).classes("muted")
                            return
                        for i, sec in enumerate(secs, start=2):
                            with ui.element("div").classes("article-item"):
                                raw = sec.get("_raw_title") or sec.get("title") or "未命名"
                                clean = sec.get("title") or "未命名"
                                ui.label(
                                    f"第{i}条 · {clean}"
                                ).classes("text-weight-medium")
                                if sec.get("_benchmark_title"):
                                    score = float(sec.get("_benchmark_image_score") or 0)
                                    order = int(sec.get("_benchmark_order") or 0) + 1
                                    ui.label(
                                        f"蓝血研究广告位 {order} · 图片匹配 {score:.0%} · 已同步标题"
                                    ).classes("text-positive")
                                    ui.label(f"原草稿标题：{raw}").classes("muted")
                                else:
                                    if raw != clean:
                                        ui.label(f"原草稿标题：{raw}（已去广告标记）").classes(
                                            "muted"
                                        )
                                    ui.label("图片未匹配 · 沿用原广告标题并排在匹配项之后").classes(
                                        "text-warning"
                                    )
                                mid = sec.get("_from_media_id") or ""
                                if mid:
                                    ui.label(f"来源草稿：{mid[:28]}…").classes("muted")

                client_timer(0.05, load_layout_preview, once=True)

                inject_btn = ui.button("确认并写入多图文草稿").props(
                    "unelevated color=teal-9 no-caps"
                ).classes("q-mt-md")
                if body_chars < 2000:
                    inject_btn.disable()
                    ui.label("正文不足 2000 字，已禁止写入草稿，请重新生成。 ").classes(
                        "text-negative q-mt-sm"
                    )

                async def inject() -> None:
                    if state.busy:
                        ui.notify("请稍候", type="warning")
                        return
                    idx = int(title_radio.value or 0)
                    state.busy = True
                    _set_button_loading(inject_btn, True)

                    # 可选写入副标题
                    if subtitle_select is not None:
                        sub = str(subtitle_select.value or "")
                        if sub and sub != "（不使用副标题）":
                            state.db.update_job(job_id, selected_subtitle=sub)

                    def work() -> dict[str, Any]:
                        return state.pipeline().review_and_inject(
                            job_id, title_index=idx
                        )

                    try:
                        job2 = await run.io_bound(work)
                        secs = (job2.get("meta") or {}).get("secondary_titles") or []
                        tip = "、".join([str(x) for x in secs if x][:3])
                        msg = "已写入多图文草稿箱，请到公众号后台刷新查看"
                        if tip:
                            msg += f"（次条：{tip}）"
                        ui.notify(msg, type="positive", timeout=8000)
                        if on_completed is not None:
                            on_completed(job2)
                            return
                        with host:
                            ui.label(
                                f"已完成：#{job2.get('id')} · {job2.get('selected_title')}"
                            ).classes("card text-weight-medium")
                            if secs:
                                ui.label(
                                    "已编排次条：" + " / ".join(str(x) for x in secs if x)
                                ).classes("muted")
                            ui.button(
                                "查看任务列表",
                                on_click=lambda: tabs.set_value(tab_jobs),
                            ).props("flat color=teal-9 no-caps")
                    except Exception as exc:  # noqa: BLE001
                        ui.notify(f"写入失败：{exc}", type="negative", timeout=10000)
                    finally:
                        state.busy = False
                        _set_button_loading(inject_btn, False)

                inject_btn.on_click(inject)

    # 若已有待审核任务，启动时提示
    pending = [
        j
        for j in state.db.list_jobs(20)
        if j.get("status") == "ready_for_review"
    ]
    if pending:
        with review_host:
            ui.button(
                f"有 {len(pending)} 篇文章待审核，前往任务中心",
                on_click=lambda: tabs.set_value(tab_jobs),
            ).props("outline color=teal-9 no-caps")


def _build_accounts_panel(
    state: AppState | None = None,
) -> Callable[[], None]:
    state = state or globals().get("state") or AppState()
    current_config = state.reload_config()
    host = ui.column().classes("w-full")
    review_service = BatchService(load_config())
    creation_plan_service = CreationPlanService(state.db, current_config)

    def open_editor(account_id: str | None = None) -> None:
        record = state.db.get_official_account(account_id) if account_id else None
        model_options = state.model_options(include_default=False)
        with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width:680px"):
            ui.label("编辑公众号" if record else "添加公众号").classes(
                "text-h6 text-weight-bold"
            )
            if not model_options:
                ui.label(
                    "请先到“设置 → 模型管理 → 文章模型”添加并启用至少一个模型。 "
                ).classes(
                    "text-warning"
                )
            name_in = ui.input(
                "公众号名称",
                value=str((record or {}).get("name") or ""),
                placeholder="例如：品牌主账号",
            ).classes("w-full").props("outlined stack-label")
            app_id_in = ui.input(
                "公众号 AppID",
                value=str((record or {}).get("app_id") or ""),
                placeholder="wx...",
            ).classes("w-full").props("outlined stack-label")
            secret_in = ui.input(
                "AppSecret" + ("（留空表示不修改）" if record else ""),
                password=True,
                password_toggle_button=True,
            ).classes("w-full").props("outlined stack-label autocomplete=new-password")
            current_model = str((record or {}).get("model_id") or "")
            model_in = ui.select(
                options=model_options,
                value=current_model if current_model in model_options else None,
                label="该公众号固定使用的模型",
            ).classes("w-full").props("outlined stack-label")
            enabled_in = ui.switch("启用", value=bool((record or {}).get("enabled", True)))
            ui.label(
                "一个公众号绑定一个模型；同一篇选题发给多个公众号时，会分别调用各自绑定的模型改写。"
            ).classes("muted")

            async def submit() -> None:
                try:
                    save_account(
                        state.db,
                        account_id=account_id,
                        name=str(name_in.value or ""),
                        app_id=str(app_id_in.value or ""),
                        app_secret=str(secret_in.value or "") or None,
                        model_id=str(model_in.value or ""),
                        enabled=bool(enabled_in.value),
                    )
                    dialog.close()
                    render_accounts()
                    state.refresh_account_selects()
                    ui.notify("公众号配置已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                save_btn = ui.button("保存", on_click=submit).props(
                    "unelevated color=teal-9 no-caps"
                )
                if not model_options:
                    save_btn.disable()
        dialog.open()

    def open_layout_editor(account_id: str) -> None:
        record = state.db.get_official_account(account_id)
        if not record:
            ui.notify("公众号不存在", type="negative")
            return
        try:
            stored = json.loads(str(record.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            stored = {}
        effective_config, _ = apply_account_selection(
            load_config(), state.db, account_id, allow_disabled=True
        )
        layout = normalize_layout(stored)
        fields: dict[str, dict[str, Any]] = {
            key: {} for key in ("body", "title", "argument", "quote", "list", "meta")
        }

        with ui.dialog() as dialog, ui.card().classes("w-full").style(
            "max-width:960px;max-height:92vh;overflow-y:auto"
        ):
            ui.label(f'排版管理 · {record["name"]}').classes("text-h6 text-weight-bold")
            ui.label(
                "按正文元素逐项定义样式。保存后只影响这个公众号，新生成的文章会自动套用。"
            ).classes("muted")
            preview_host = ui.column().classes("w-full")

            break_mode = ui.select(
                options={"blank_line": "空行分段（推荐）", "each_line": "每一行都换成新段落"},
                value=layout["paragraph_break_mode"],
                label="段落换行规则",
            ).classes("w-full").props("outlined stack-label")

            def text_field(section: str, key: str, label: str) -> None:
                fields[section][key] = ui.input(
                    label, value=str(layout[section][key])
                ).classes("w-full").props("outlined dense stack-label")

            class ColorFieldValue:
                def __init__(self, picker: Any, transparent: Any | None = None) -> None:
                    self.picker = picker
                    self.transparent = transparent

                @property
                def value(self) -> str:
                    if self.transparent is not None and bool(self.transparent.value):
                        return "transparent"
                    return str(self.picker.value or "#000000")

            def color_field(
                section: str,
                key: str,
                label: str,
                *,
                allow_transparent: bool = False,
            ) -> None:
                current = str(layout[section][key] or "#000000")
                is_transparent = current == "transparent"
                picker_value = "#ffffff" if is_transparent else current
                with ui.column().classes("w-full gap-1"):
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        picker = ui.color_input(
                            label=label,
                            value=picker_value,
                            preview=True,
                        ).classes("col").props(
                            "outlined dense stack-label readonly"
                        )
                        transparent_switch = None
                        if allow_transparent:
                            transparent_switch = ui.switch(
                                "无色", value=is_transparent
                            ).props("dense")
                    with ui.row().classes("items-center gap-2 q-pl-xs"):
                        current_swatch = ui.element("span").style(
                            "display:inline-block;width:24px;height:24px;"
                            "border-radius:6px;border:1px solid #cbd5d1;"
                            f"background-color:{picker_value}"
                        )
                        current_label = ui.label().classes(
                            "text-caption text-weight-medium"
                        )

                def update_current_color(_: Any = None) -> None:
                    transparent = (
                        transparent_switch is not None
                        and bool(transparent_switch.value)
                    )
                    if transparent:
                        current_swatch.style(
                            "background-color:#ffffff;border-style:dashed"
                        )
                        current_label.text = "当前：无色（透明）"
                    else:
                        selected = str(picker.value or "#000000")
                        current_swatch.style(
                            f"background-color:{selected};border-style:solid"
                        )
                        current_label.text = f"当前：{selected.upper()}"

                picker.on_value_change(update_current_color)
                if transparent_switch is not None:
                    transparent_switch.on_value_change(update_current_color)
                update_current_color()
                fields[section][key] = ColorFieldValue(
                    picker, transparent_switch
                )

            def align_field(section: str) -> None:
                fields[section]["alignment"] = ui.select(
                    {"left": "左对齐", "center": "居中", "right": "右对齐", "justify": "两端对齐"},
                    value=layout[section]["alignment"],
                    label="对齐方式",
                ).classes("w-full").props("outlined dense stack-label")

            with ui.expansion("正文段落", icon="notes").classes("w-full").props("default-opened"):
                with ui.grid(columns=2).classes("w-full gap-3"):
                    text_field("body", "font_size", "字号（如 16px）")
                    color_field("body", "color", "文字颜色")
                    text_field("body", "line_height", "行高（如 2 或 32px）")
                    text_field("body", "spacing_after", "段后间距")
                    text_field("body", "first_line_indent", "首行缩进（0em / 2em）")
                    text_field("body", "horizontal_padding", "左右留白")
                    align_field("body")

            with ui.expansion("正文一级标题", icon="title").classes("w-full"):
                with ui.grid(columns=2).classes("w-full gap-3"):
                    text_field("title", "font_size", "字号")
                    color_field("title", "color", "颜色")
                    text_field("title", "line_height", "行高")
                    text_field("title", "spacing_before", "标题前间距")
                    text_field("title", "spacing_after", "标题后间距")
                    align_field("title")
                    fields["title"]["bold"] = ui.switch("加粗", value=bool(layout["title"]["bold"]))

            with ui.expansion("论点标题", icon="format_quote").classes("w-full"):
                with ui.grid(columns=2).classes("w-full gap-3"):
                    text_field("argument", "font_size", "字号")
                    color_field("argument", "color", "文字颜色")
                    text_field("argument", "line_height", "行高")
                    text_field("argument", "spacing_before", "论点前间距")
                    text_field("argument", "spacing_after", "论点后间距")
                    color_field(
                        "argument", "background", "背景色", allow_transparent=True
                    )
                    color_field(
                        "argument", "border_color", "左侧强调线颜色",
                        allow_transparent=True,
                    )
                    align_field("argument")
                    fields["argument"]["bold"] = ui.switch(
                        "加粗", value=bool(layout["argument"]["bold"])
                    )

            with ui.expansion("引用块", icon="format_indent_increase").classes("w-full"):
                with ui.grid(columns=2).classes("w-full gap-3"):
                    text_field("quote", "font_size", "字号")
                    color_field("quote", "color", "文字颜色")
                    text_field("quote", "line_height", "行高")
                    color_field("quote", "background", "背景色")
                    color_field("quote", "border_color", "左侧线颜色")
                    for key, label in (
                        ("spacing_before", "引用前间距"),
                        ("spacing_after", "引用后间距"),
                    ):
                        text_field("quote", key, label)

            with ui.expansion("列表项", icon="format_list_bulleted").classes("w-full"):
                with ui.grid(columns=2).classes("w-full gap-3"):
                    text_field("list", "font_size", "字号")
                    color_field("list", "color", "文字颜色")
                    color_field("list", "marker_color", "序号 / 圆点颜色")
                    for key, label in (
                        ("line_height", "行高"),
                        ("indent", "列表缩进"), ("spacing_after", "列表项间距"),
                    ):
                        text_field("list", key, label)

            with ui.expansion("作者栏与页尾", icon="badge").classes("w-full"):
                fields["meta"]["show_byline"] = ui.switch(
                    "显示作者栏", value=bool(layout["meta"]["show_byline"])
                )
                with ui.grid(columns=2).classes("w-full gap-3"):
                    for key, label in (
                        ("byline_author", "作者"), ("byline_source", "来源"),
                        ("byline_contact", "联系方式"), ("footer_follow_text", "页尾关注文案"),
                    ):
                        text_field("meta", key, label)
                fields["meta"]["show_footer_follow"] = ui.switch(
                    "显示页尾关注文案", value=bool(layout["meta"]["show_footer_follow"])
                )

            def collect_layout() -> dict[str, Any]:
                value = normalize_layout(layout)
                value["paragraph_break_mode"] = str(break_mode.value or "blank_line")
                for section, section_fields in fields.items():
                    for key, element in section_fields.items():
                        value[section][key] = element.value
                return validate_layout(value)

            def refresh_preview() -> None:
                try:
                    current_layout = collect_layout()
                except ValueError as exc:
                    ui.notify(str(exc), type="negative", timeout=8000)
                    return
                cfg = dict(effective_config)
                template_cfg = dict(cfg.get("template") or {})
                template_cfg.update(layout_to_template_config(current_layout))
                cfg["template"] = template_cfg
                editor_cfg = dict(cfg.get("editor_template") or {})
                editor_cfg.update(current_layout["editor_template"])
                editor_cfg["_root"] = cfg.get("_root")
                cfg["editor_template"] = editor_cfg
                snapshot = (
                    load_template_snapshot(editor_cfg)
                    if editor_cfg.get("enabled", False)
                    else None
                )
                sample = (
                    "# 这是正文一级标题\n\n"
                    "这是一段正文，用来查看字号、行高、首行缩进和段落间距。\n\n"
                    "## 这是一个核心论点\n\n"
                    "论点下方继续使用正文段落说明具体内容。\n\n"
                    "> 这是一段引用或重点提示。\n\n"
                    "- 第一条列表内容\n- 第二条列表内容\n1. 第一条有序内容"
                )
                generated = TemplateRenderer(cfg).render(
                    body=sample,
                    show_byline=False if snapshot else None,
                )
                finalized = finalize_article_html(
                    generated,
                    editor_cfg,
                    snapshot=snapshot,
                    load_local_snapshot=False,
                )
                preview_host.clear()
                with preview_host:
                    ui.label("最终公众号成品预览").classes("text-weight-bold")
                    if snapshot:
                        ui.label(
                            f"已合并该公众号模板：{snapshot.path.name}"
                        ).classes("text-positive text-caption")
                    elif editor_cfg.get("enabled", False):
                        ui.label(
                            "该公众号模板快照尚不存在，当前仅预览正文排版；同步模板后会显示完整成品。"
                        ).classes("text-warning text-caption")
                    ui.label(finalized.report.summary()).classes(
                        "text-negative text-caption"
                        if finalized.report.errors
                        else "muted"
                    )
                    with ui.element("div").classes("preview-frame w-full"):
                        ui.html(prepare_preview_html(finalized.html), sanitize=False)

            def save_layout() -> None:
                try:
                    save_account_layout(state.db, account_id, collect_layout())
                    dialog.close()
                    render_accounts()
                    ui.notify("该公众号的排版方案已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"保存失败：{exc}", type="negative")

            with ui.row().classes("w-full justify-between q-mt-md"):
                ui.button("刷新预览", on_click=refresh_preview).props(
                    "outline color=teal-9 no-caps"
                )
                with ui.row():
                    ui.button("取消", on_click=dialog.close).props("flat no-caps")
                    ui.button("保存排版", on_click=save_layout).props(
                        "unelevated color=teal-9 no-caps"
                    )
            refresh_preview()
        dialog.open()

    async def open_template_manager(account_id: str) -> None:
        record = state.db.get_official_account(account_id)
        if not record:
            ui.notify("公众号不存在", type="negative")
            return
        try:
            stored = json.loads(str(record.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            stored = {}
        layout = normalize_layout(stored)
        effective_config, _ = apply_account_selection(
            load_config(), state.db, account_id, allow_disabled=True
        )
        editor_cfg = dict(effective_config.get("editor_template") or {})
        editor_cfg.update(layout["editor_template"])
        editor_cfg["snapshot_path"] = f"data/templates/{account_id}.html"
        editor_cfg["_root"] = effective_config.get("_root")
        candidates: dict[str, Any] = {}
        radio_holder: dict[str, Any] = {}

        def account_wechat_client() -> WeChatClient:
            wechat_cfg = effective_config.get("wechat") or {}
            auth = WeChatAuth(
                app_id=str(wechat_cfg.get("app_id") or ""),
                app_secret=str(wechat_cfg.get("app_secret") or ""),
                db=state.db,
            )
            return WeChatClient(
                get_token=auth.get_access_token,
                refresh_token=lambda: auth.get_access_token(force_refresh=True),
            )

        with ui.dialog() as dialog, ui.card().classes("w-full").style(
            "max-width:760px;max-height:88vh;overflow-y:auto"
        ):
            ui.label(f'模板管理 · {record["name"]}').classes(
                "text-h6 text-weight-bold"
            )
            ui.label(
                "仅读取这个公众号草稿箱中标题包含“模板”的草稿；选择一个标题作为当前模板。"
            ).classes("muted")
            current_title = str(layout["editor_template"].get("selected_title") or "")
            ui.label(f"当前模板：{current_title or '未选择'}").classes(
                "text-positive text-weight-medium"
            )
            placeholder_input = ui.input(
                "替换正文字样",
                value=str(
                    layout["editor_template"].get("placeholder")
                    or editor_cfg.get("placeholder")
                    or "公众号正文"
                ),
                placeholder="例如：蓝血经营管理系统正文",
            ).classes("w-full").props("outlined stack-label")
            ui.label(
                "生成文章时，模板中与该字样完全一致的内容会被替换为生成后的正文。"
            ).classes("muted text-caption")
            status_label = ui.label("正在读取模板草稿…").classes("muted")
            candidate_host = ui.column().classes("w-full")
            with ui.row().classes("w-full justify-between q-mt-md"):
                refresh_btn = ui.button("重新读取").props(
                    "outline color=teal-9 no-caps icon=refresh"
                )
                with ui.row():
                    ui.button("取消", on_click=dialog.close).props("flat no-caps")
                    apply_btn = ui.button("应用所选模板").props(
                        "unelevated color=teal-9 no-caps icon=check"
                    )
                    apply_btn.disable()

            async def load_candidates() -> None:
                from app.wechat.template_snapshot import list_template_draft_candidates

                _set_button_loading(refresh_btn, True)
                apply_btn.disable()
                status_label.text = "正在读取模板草稿…"
                candidate_host.clear()
                try:
                    replacement_text = str(placeholder_input.value or "").strip()
                    if not replacement_text:
                        raise ValueError("替换正文字样不能为空")
                    editor_cfg["placeholder"] = replacement_text
                    rows = await run.io_bound(
                        lambda: list_template_draft_candidates(
                            account_wechat_client(), editor_cfg
                        )
                    )
                    candidates.clear()
                    candidates.update({item.key: item for item in rows})
                    current_key = (
                        f'{layout["editor_template"].get("selected_media_id") or ""}:'
                        f'{int(layout["editor_template"].get("selected_article_index") or 0)}'
                    )
                    preferred = current_key if current_key in candidates else None
                    options = {
                        item.key: item.title
                        + ("" if item.has_placeholder else "（缺少正文占位符，不能应用）")
                        for item in rows
                    }
                    with candidate_host:
                        if options:
                            ui.label("请选择一个模板：").classes("text-weight-medium")
                            radio_holder["el"] = ui.radio(
                                options, value=preferred
                            ).classes("w-full")
                        else:
                            ui.label(
                                "没有找到标题包含“模板”的草稿。"
                            ).classes("text-warning")
                    status_label.text = f"共找到 {len(rows)} 个模板草稿"
                    if rows:
                        apply_btn.enable()
                except Exception as exc:  # noqa: BLE001
                    status_label.text = f"读取失败：{exc}"
                    status_label.classes("text-negative")
                finally:
                    _set_button_loading(refresh_btn, False)

            async def apply_selected_template() -> None:
                from app.wechat.template_snapshot import save_template_draft_candidate

                radio = radio_holder.get("el")
                key = str((radio.value if radio else "") or "")
                candidate = candidates.get(key)
                if candidate is None:
                    ui.notify("请先单选一个模板标题", type="warning")
                    return
                replacement_text = str(placeholder_input.value or "").strip()
                if not replacement_text:
                    ui.notify("替换正文字样不能为空", type="warning")
                    return
                _set_button_loading(apply_btn, True)
                try:
                    editor_cfg["placeholder"] = replacement_text
                    await run.io_bound(
                        lambda: save_template_draft_candidate(editor_cfg, candidate)
                    )
                    layout["editor_template"].update(
                        enabled=True,
                        selected_media_id=candidate.media_id,
                        selected_article_index=candidate.article_index,
                        selected_title=candidate.title,
                        placeholder=replacement_text,
                    )
                    save_account_layout(state.db, account_id, layout)
                    dialog.close()
                    render_accounts()
                    ui.notify(f"已选择模板：{candidate.title}", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"应用模板失败：{exc}", type="negative", timeout=10000)
                finally:
                    _set_button_loading(apply_btn, False)

            refresh_btn.on_click(load_candidates)
            apply_btn.on_click(apply_selected_template)
        dialog.open()
        await load_candidates()

    def open_inline_image_manager(account_id: str) -> None:
        record = state.db.get_official_account(account_id)
        if not record:
            ui.notify("公众号不存在", type="negative")
            return
        try:
            stored = json.loads(str(record.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            stored = {}
        layout = normalize_layout(stored)
        settings = layout["inline_images"]
        image_models = {
            str(item["id"]): f'{item["name"]} · {item["model"]}'
            for item in public_models(state.db, enabled_only=True, purpose="image")
        }
        model_value = str(settings.get("image_model_id") or "")
        if model_value not in image_models:
            model_value = ""
        prompt_templates = {
            str(item["id"]): item
            for item in public_prompt_templates(
                state.db,
                purpose=IMAGE_PROMPT_PURPOSE,
                enabled_only=True,
            )
        }
        prompt_template_options = {
            template_id: str(item["name"])
            for template_id, item in prompt_templates.items()
        }
        prompt_mode_value = str(
            settings.get("prompt_mode") or PROMPT_MODE_DEFAULT
        )
        prompt_template_value = str(settings.get("prompt_template_id") or "")
        if prompt_template_value not in prompt_templates:
            prompt_template_value = ""

        with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width:720px"):
            ui.label(f'正文与封面生图配置 · {record["name"]}').classes("text-h6 text-weight-bold")
            ui.label(
                "每个公众号可绑定自己的生图智能体。系统会识别正文中的小标题论点，"
                "按实际论点数量一一生成，并在每个论点最后一个段落之后插图。"
                "封面主图会同时参考最终标题、正文主题和核心论点。"
            ).classes("muted")
            enabled = ui.switch("启用正文生图智能体", value=bool(settings.get("enabled")))
            generate_cover = ui.switch(
                "使用同一智能体生成封面主图",
                value=bool(settings.get("generate_cover", True)),
            )
            source = ui.select(
                {
                    "generate": "每个论点均由生图智能体生成（推荐，避免来源 Logo）",
                    "hybrid": "优先通过过滤的原文/素材图片，缺少时智能生成",
                    "library": "仅使用该公众号素材库",
                },
                value=str(settings.get("source_mode") or "generate"),
                label="配图来源",
            ).classes("w-full").props("outlined stack-label")
            model_select = ui.select(
                {"": "不配置图片生成模型", **image_models},
                value=model_value,
                label="该公众号使用的生图智能体",
            ).classes("w-full").props("outlined stack-label")
            if not image_models:
                ui.label(
                    "还没有生图智能体。请先到“设置 → 生图智能体”添加并生成测试图。"
                ).classes("text-warning text-caption")
            ui.input(
                "插图位置",
                value="每个正文论点的最后一个段落之后",
            ).classes("w-full").props("outlined stack-label readonly")
            with ui.grid(columns=2).classes("w-full gap-3"):
                min_count = ui.number(
                    "无小标题时最少图片数",
                    value=int(settings.get("min_count", 2)),
                    min=0,
                    max=8,
                ).classes("w-full").props("outlined stack-label")
                max_count = ui.number(
                    "无小标题时最多图片数",
                    value=int(settings.get("max_count", 6)),
                    min=1,
                    max=8,
                ).classes("w-full").props("outlined stack-label")
                min_spacing = ui.number(
                    "最小间隔（字）", value=int(settings.get("min_spacing", 600)), min=300
                ).classes("w-full").props("outlined stack-label")
                max_spacing = ui.number(
                    "目标最大间隔（字）", value=int(settings.get("max_spacing", 900)), min=300
                ).classes("w-full").props("outlined stack-label")
                concurrency = ui.number(
                    "同时生图任务数",
                    value=int(settings.get("generation_concurrency", 2)),
                    min=1,
                    max=4,
                ).classes("w-full").props("outlined stack-label")
            prompt_mode = ui.select(
                {
                    PROMPT_MODE_DEFAULT: "使用默认模板（不使用用户自定义模板）",
                    PROMPT_MODE_TEMPLATE: "使用自定义提示词模板",
                },
                value=(
                    prompt_mode_value
                    if prompt_mode_value in {PROMPT_MODE_DEFAULT, PROMPT_MODE_TEMPLATE}
                    else PROMPT_MODE_DEFAULT
                ),
                label="提示词配置方式",
            ).classes("w-full").props("outlined stack-label")
            prompt_template = ui.select(
                {"": "请选择图片提示词模板", **prompt_template_options},
                value=prompt_template_value,
                label="公众号使用的图片提示词模板",
            ).classes("w-full").props("outlined stack-label options-dense")
            if not prompt_templates:
                ui.label(
                    "还没有自定义图片模板，可到“设置 → 创作方案 → 写作与图片规则”中添加。"
                ).classes("text-warning text-caption")
            prompt_preview = ui.textarea(
                "当前生效提示词预览",
                value="默认模板由系统代码维护，内容不在界面展示。",
            ).classes("w-full").props("outlined rows=4 stack-label readonly")

            def sync_prompt_selection() -> None:
                use_template = str(prompt_mode.value or "") == PROMPT_MODE_TEMPLATE
                prompt_template.set_enabled(use_template)
                selected = prompt_templates.get(str(prompt_template.value or ""))
                prompt_preview.value = (
                    str(selected.get("content") or "")
                    if use_template and selected
                    else "默认模板由系统代码维护，内容不在界面展示。"
                )

            prompt_mode.on_value_change(lambda _: sync_prompt_selection())
            prompt_template.on_value_change(lambda _: sync_prompt_selection())
            sync_prompt_selection()
            ui.label(
                "系统会在所选提示词基础上自动加入标题、正文主题、当前论点内容及禁止文字/水印等规则。"
                "接口限流时会自动等待并重试，不影响其他图片并行生成。"
            ).classes("muted")

            def save_settings() -> None:
                try:
                    layout["inline_images"].update(
                        enabled=bool(enabled.value),
                        generate_cover=bool(generate_cover.value),
                        source_mode=str(source.value or "generate"),
                        placement_mode="argument_end",
                        image_model_id=str(model_select.value or ""),
                        min_count=int(min_count.value or 0),
                        max_count=int(max_count.value or 6),
                        min_spacing=int(min_spacing.value or 600),
                        max_spacing=int(max_spacing.value or 900),
                        generation_concurrency=int(concurrency.value or 2),
                        prompt_mode=str(prompt_mode.value or PROMPT_MODE_DEFAULT),
                        prompt_template_id=(
                            str(prompt_template.value or "")
                            if str(prompt_mode.value or "") == PROMPT_MODE_TEMPLATE
                            else ""
                        ),
                        prompt_style=DEFAULT_IMAGE_PROMPT_STYLE,
                    )
                    if (
                        str(prompt_mode.value or "") == PROMPT_MODE_TEMPLATE
                        and str(prompt_template.value or "") not in prompt_templates
                    ):
                        raise ValueError("请选择一个已启用的提示词模板")
                    if (
                        (
                            bool(generate_cover.value)
                            or (
                                bool(enabled.value)
                                and str(source.value or "generate") in {"generate", "hybrid"}
                            )
                        )
                        and str(model_select.value or "") not in image_models
                    ):
                        raise ValueError("请先选择一个已启用的生图智能体")
                    save_account_layout(state.db, account_id, layout)
                    dialog.close()
                    render_accounts()
                    ui.notify("该公众号的生图智能体配置已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"保存失败：{exc}", type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("保存生图配置", on_click=save_settings).props(
                    "unelevated color=teal-9 no-caps icon=save"
                )
        dialog.open()

    def confirm_delete(account_id: str, name: str) -> None:
        with ui.dialog() as confirm, ui.card():
            ui.label(f"确定删除公众号“{name}”吗？").classes("text-weight-medium")

            def remove() -> None:
                state.db.delete_official_account(account_id)
                confirm.close()
                render_accounts()
                state.refresh_account_selects()
                ui.notify("公众号配置已删除", type="positive")

            with ui.row().classes("justify-end w-full"):
                ui.button("取消", on_click=confirm.close).props("flat no-caps")
                ui.button("删除", on_click=remove).props("unelevated color=red-7 no-caps")
        confirm.open()

    def set_enabled(account_id: str, enabled: bool) -> None:
        record = state.db.get_official_account(account_id)
        if not record:
            return
        record["enabled"] = enabled
        state.db.upsert_official_account(record)
        state.refresh_account_selects()

    def set_account_prompt_template(
        account_id: str,
        purpose: str,
        selection: str,
    ) -> None:
        record = state.db.get_official_account(account_id)
        if not record:
            ui.notify("公众号不存在", type="negative")
            return
        try:
            prompt_name = save_account_prompt_selection(
                state.db,
                account_id,
                None if selection == PROMPT_MODE_DEFAULT else selection,
                purpose=purpose,
            )
            purpose_label = "文章" if purpose == ARTICLE_PROMPT_PURPOSE else "图片"
            ui.notify(
                f'{record["name"]} 的{purpose_label}提示词已使用：{prompt_name}',
                type="positive",
            )
            render_accounts()
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"保存提示词配置失败：{exc}", type="negative")
            render_accounts()

    def set_account_review_profile(account_id: str, profile_id: str) -> None:
        try:
            selected = review_service.set_account_editorial_review_default(
                account_id,
                profile_id=profile_id,
            )
            ui.notify(
                f'默认评审方案已设为：{selected.get("profile_name") or profile_id}',
                type="positive",
            )
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"保存默认评审方案失败：{exc}", type="negative")
            render_accounts()

    def set_account_creation_plan(account_id: str, plan_id: str) -> None:
        if not plan_id:
            return
        try:
            selected = creation_plan_service.apply_to_account(
                account_id,
                plan_id,
            )
            template_result = dict(
                selected.get("draft_template_application") or {}
            )
            template_message = str(template_result.get("message") or "").strip()
            ui.notify(
                f'已应用创作方案：{(selected.get("plan") or {}).get("name") or plan_id}'
                + (f"；{template_message}" if template_message else ""),
                type="positive",
                timeout=10000 if template_message else 5000,
            )
            render_accounts()
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"应用创作方案失败：{exc}", type="negative", timeout=10000)
            render_accounts()

    async def test_account_connection(account_id: str, button: Any) -> None:
        _set_button_loading(button, True, "正在验证公众号凭证和接口权限…")
        try:
            def verify() -> None:
                cfg, _ = apply_account_selection(
                    load_config(),
                    state.db,
                    account_id,
                    allow_disabled=True,
                )
                wechat_cfg = cfg.get("wechat") or {}
                WeChatAuth(
                    app_id=str(wechat_cfg.get("app_id") or ""),
                    app_secret=str(wechat_cfg.get("app_secret") or ""),
                    db=state.db,
                ).get_access_token(force_refresh=True)

            await run.io_bound(verify)
            ui.notify("公众号连接正常，凭证可以调用微信接口", type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"公众号连接失败：{exc}", type="negative", timeout=12000)
        finally:
            _set_button_loading(button, False)

    def render_accounts() -> None:
        host.clear()
        accounts = public_accounts(state.db)
        enabled_article_prompt_templates = public_prompt_templates(
            state.db,
            purpose=ARTICLE_PROMPT_PURPOSE,
            enabled_only=True,
        )
        enabled_image_prompt_templates = public_prompt_templates(
            state.db,
            purpose=IMAGE_PROMPT_PURPOSE,
            enabled_only=True,
        )
        article_prompt_template_options = {
            PROMPT_MODE_DEFAULT: "默认模板（不使用用户自定义模板）",
            **{
                str(template["id"]): str(template["name"])
                for template in enabled_article_prompt_templates
            },
        }
        image_prompt_template_options = {
            PROMPT_MODE_DEFAULT: "默认模板（不使用用户自定义模板）",
            **{
                str(template["id"]): str(template["name"])
                for template in enabled_image_prompt_templates
            },
        }
        review_profile_options = enabled_profile_options(review_service)
        available_creation_plans = [
            plan
            for plan in creation_plan_service.list(enabled_only=True)
            if bool(plan.get("available", True))
        ]
        creation_plan_options = {
            str(plan["id"]): str(plan["name"])
            for plan in available_creation_plans
        }
        latest_account_errors: dict[str, str] = {}
        for job in state.db.list_jobs(100):
            meta = job.get("meta") or {}
            account_id = str(meta.get("official_account_id") or "")
            if account_id and job.get("error") and account_id not in latest_account_errors:
                latest_account_errors[account_id] = str(job["error"])
        with host:
            with ui.element("div").classes("card w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label("多公众号管理").classes("text-h6 text-weight-bold")
                        ui.label(
                            "保存各公众号开发者凭证，并分别绑定改写模型、文章提示词、图片提示词和专属排版。"
                        ).classes("muted")
                    ui.button("添加公众号", on_click=lambda: open_editor()).props(
                        "unelevated color=teal-9 no-caps icon=add"
                    )
                ui.label(
                    "AppSecret 使用 Windows 当前用户加密保存，不会显示在界面或任务记录中。"
                ).classes("text-positive q-mt-sm")

            if not accounts:
                with ui.element("div").classes("card w-full"):
                    ui.label("尚未添加公众号").classes("text-weight-medium")
                    ui.label("请先添加模型，再添加公众号并完成一对一绑定。 ").classes("muted")
                return

            for item in accounts:
                account_id = str(item["id"])
                account_error = latest_account_errors.get(account_id, "")
                try:
                    account_creation_default = (
                        creation_plan_service.get_account_default(account_id)
                    )
                except Exception:  # noqa: BLE001
                    account_creation_default = {
                        "bound": False,
                        "plan_id": "",
                        "plan": None,
                        "in_sync": True,
                    }
                account_creation_plan_id = str(
                    account_creation_default.get("plan_id") or ""
                )
                if (
                    account_creation_plan_id
                    and account_creation_plan_id not in creation_plan_options
                ):
                    bound_plan = account_creation_default.get("plan") or {}
                    creation_plan_options[account_creation_plan_id] = str(
                        bound_plan.get("name") or "当前已绑定方案"
                    )
                account_layout = dict(item.get("layout") or {})
                article_prompt_settings = dict(
                    account_layout.get("article_prompt") or {}
                )
                inline_settings = dict(
                    account_layout.get("inline_images") or {}
                )
                article_prompt_selection = PROMPT_MODE_DEFAULT
                if (
                    str(
                        article_prompt_settings.get("prompt_mode")
                        or PROMPT_MODE_DEFAULT
                    )
                    == PROMPT_MODE_TEMPLATE
                    and str(article_prompt_settings.get("prompt_template_id") or "")
                    in article_prompt_template_options
                ):
                    article_prompt_selection = str(
                        article_prompt_settings.get("prompt_template_id") or ""
                    )
                image_prompt_selection = PROMPT_MODE_DEFAULT
                if (
                    str(inline_settings.get("prompt_mode") or PROMPT_MODE_DEFAULT)
                    == PROMPT_MODE_TEMPLATE
                    and str(inline_settings.get("prompt_template_id") or "")
                    in image_prompt_template_options
                ):
                    image_prompt_selection = str(
                        inline_settings.get("prompt_template_id") or ""
                    )
                try:
                    review_default = (
                        review_service.get_account_editorial_review_default(
                            account_id
                        )
                    )
                    review_profile_value = str(
                        review_default.get("profile_id") or ""
                    )
                except Exception:  # noqa: BLE001
                    review_profile_value = ""
                if review_profile_value not in review_profile_options:
                    review_profile_value = next(
                        iter(review_profile_options),
                        None,
                    )
                bound_plan = account_creation_default.get("plan") or {}
                creation_plan_summary = (
                    str(bound_plan.get("name") or "")
                    if account_creation_default.get("bound")
                    else "沿用原有单项配置"
                )
                if (
                    account_creation_default.get("bound")
                    and not account_creation_default.get("in_sync", True)
                ):
                    creation_plan_summary += "（有单项调整）"
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0").style("min-width:0;flex:1"):
                            ui.label(str(item["name"])).classes("text-weight-bold")
                            ui.label(
                                f'{"已启用" if item["enabled"] else "已停用"}'
                                f' · 模型：{item["model_name"]}'
                                f' · {"专属排版" if item.get("has_custom_layout") else "默认排版"}'
                            ).classes("muted")
                            ui.label(
                                f"创作方案：{creation_plan_summary}"
                            ).classes("muted text-caption")
                            ui.label(
                                "AppID 与 AppSecret 已安全保存"
                            ).classes("text-positive text-caption")
                            if "40125" in account_error or "invalid appsecret" in account_error.lower():
                                ui.label(
                                    "账号不可用：AppSecret 无效（微信错误 40125），请进入管理更新。"
                                ).classes("text-negative text-caption")
                        with ui.row().classes("items-center gap-1"):
                            manage_btn = ui.button(
                                "管理",
                                icon="settings",
                            ).props("outline dense color=teal-9 no-caps")
                            test_btn = ui.button(
                                "测试连接",
                                icon="wifi_tethering",
                            ).props("flat dense color=teal-9 no-caps")
                            test_btn.on_click(
                                lambda _=None, aid=account_id, btn=test_btn: test_account_connection(
                                    aid, btn
                                )
                            )
                            with ui.button(icon="more_horiz").props(
                                "flat round dense color=grey-8"
                            ):
                                with ui.menu():
                                    ui.menu_item(
                                        "删除公众号",
                                        on_click=lambda _=None, aid=account_id, n=str(item["name"]): confirm_delete(
                                            aid, n
                                        ),
                                    )
                            enabled_switch = ui.switch(
                                "启用", value=bool(item["enabled"])
                            ).props("dense")
                            enabled_switch.on_value_change(
                                lambda e, aid=account_id: set_enabled(
                                    aid, bool(e.value)
                                )
                            )
                    management_intro = ui.label(
                        "创作方案：一次绑定写作规则、排版、图片与封面、"
                        "公众号专属草稿模板和默认 AI 评审"
                    ).classes("text-weight-medium q-mt-md")
                    creation_plan_select = ui.select(
                        creation_plan_options,
                        value=account_creation_plan_id or None,
                        label="公众号默认创作方案",
                    ).classes("w-full q-mt-sm").props(
                        "outlined dense stack-label options-dense"
                    )
                    creation_plan_select.on_value_change(
                        lambda event, aid=account_id: set_account_creation_plan(
                            aid,
                            str(event.value or ""),
                        )
                    )
                    individual_rules_label = ui.label(
                        "高级：单项覆盖（修改后会标记为“有单项调整”）"
                    ).classes("muted text-caption q-mt-sm")
                    with ui.grid(columns=2).classes(
                        "w-full gap-3 q-mt-sm"
                    ) as prompt_grid:
                        article_prompt_select = ui.select(
                            article_prompt_template_options,
                            value=article_prompt_selection,
                            label="文章提示词模板",
                        ).classes("w-full").props(
                            "outlined dense stack-label options-dense"
                        )
                        image_prompt_select = ui.select(
                            image_prompt_template_options,
                            value=image_prompt_selection,
                            label="图片提示词模板",
                        ).classes("w-full").props(
                            "outlined dense stack-label options-dense"
                        )
                    article_prompt_select.on_value_change(
                        lambda event, aid=account_id: set_account_prompt_template(
                            aid,
                            ARTICLE_PROMPT_PURPOSE,
                            str(event.value or PROMPT_MODE_DEFAULT),
                        )
                    )
                    image_prompt_select.on_value_change(
                        lambda event, aid=account_id: set_account_prompt_template(
                            aid,
                            IMAGE_PROMPT_PURPOSE,
                            str(event.value or PROMPT_MODE_DEFAULT),
                        )
                    )
                    review_profile_select = ui.select(
                        review_profile_options,
                        value=review_profile_value,
                        label="默认 AI 评审方案",
                    ).classes("w-full q-mt-sm").props(
                        "outlined dense stack-label options-dense"
                    )
                    review_profile_select.on_value_change(
                        lambda event, aid=account_id: set_account_review_profile(
                            aid,
                            str(event.value or ""),
                        )
                    )
                    presentation_label = ui.label(
                        "排版与呈现"
                    ).classes("text-weight-medium q-mt-md")
                    with ui.row().classes("q-mt-sm") as management_actions:
                        ui.button(
                            "基础信息",
                            on_click=lambda aid=account_id: open_editor(aid),
                        ).props("outline dense color=teal-9 no-caps icon=edit")
                        ui.button(
                            "正文排版",
                            on_click=lambda aid=account_id: open_layout_editor(aid),
                        ).props("outline dense color=teal-9 no-caps icon=palette")
                        ui.button(
                            "草稿模板",
                            on_click=lambda aid=account_id: open_template_manager(aid),
                        ).props(
                            "outline dense color=deep-orange-7 no-caps icon=dashboard_customize"
                        )
                        ui.button(
                            "图片与封面",
                            on_click=lambda aid=account_id: open_inline_image_manager(aid),
                        ).props("outline dense color=indigo-7 no-caps icon=auto_awesome")
                    management_visible = {"value": False}
                    for element in (
                        management_intro,
                        creation_plan_select,
                        individual_rules_label,
                        prompt_grid,
                        review_profile_select,
                        presentation_label,
                        management_actions,
                    ):
                        element.set_visibility(False)

                    def toggle_management(
                        _=None,
                        *,
                        controls: tuple[Any, ...] = (
                            management_intro,
                            creation_plan_select,
                            individual_rules_label,
                            prompt_grid,
                            review_profile_select,
                            presentation_label,
                            management_actions,
                        ),
                        button: Any = manage_btn,
                        runtime: dict[str, bool] = management_visible,
                    ) -> None:
                        runtime["value"] = not runtime["value"]
                        for control in controls:
                            control.set_visibility(runtime["value"])
                        button.set_text("收起管理" if runtime["value"] else "管理")

                    manage_btn.on_click(toggle_management)

    render_accounts()
    return render_accounts


def _build_help_panel() -> None:
    with ui.element("div").classes("card"):
        ui.label("运营使用流程").classes("section-title")
        ui.markdown(
            """
**第一次使用**

教程已经放进对应配置页面：进入“设置 → 模型管理”，选择文章模型或图片模型后，按照页面步骤获取 API Key 并完成真实连接测试；进入“设置 → 飞书”，按照 App 凭证、长连接、发布和绑定口令的顺序操作。

1. **选择内容**：在工作台直接粘贴链接、正文或输入话题；需要找热点和关注文章时，点击“从选题库选择”  
2. **选择公众号**：系统会自动使用每个公众号已经保存的模型、创作规则、排版和图片配置  
3. **开始生成**：各公众号并行生成，页面实时显示步骤、百分比和用时；完成后自动进入任务中心  
4. **统一审核**：在任务中心修改标题和正文；更多优化中可使用 AI 评审、配图、封面、历史版本和排版质检  
5. **写入草稿**：全部文章确认后，点击“写入已确认的 N 篇”；程序不会直接群发

任务中心按批次展示全部历史任务，可筛选待审核、失败、公众号和日期；支持仅重试失败公众号、按原设置重新生成、停止和归档。

选题库包含“热点选题、关注公众号、来源管理”三个页面。进入“关注公众号”后，点击“查看近期文章”即可在弹窗中浏览该账号的文章。非自有公众号只使用已配置的公众号后台临时登录态进行搜索，不调用搜狗或百度；也可在网页或飞书中手动投递微信原文链接补充。

“停止生成”表示不再执行后续步骤；已经发送给模型的请求可能仍会产生费用，但返回结果不会继续排版或写入草稿箱。

AI 评审使用目标公众号绑定的文本模型，会产生模型费用。事实核查仅对照当前原始资料做一致性检查，不代表已经联网查证；修改稿应用后文章会回到待确认状态，必须重新预览和确认。

**次条匹配不到时**
- 确认草稿箱里已有标题以「广告」打头的内容
- 或把草稿 `media_id` 填进 `config.yaml` → `layout.secondary_media_ids`
            """
        )


def main() -> None:
    port = int(str(os.getenv("WECHAT_PUBLISHER_UI_PORT") or "18765"))
    try:
        ui.run(
            root=create_desktop_app,
            title="公众号改写助手",
            native=True,
            window_size=(1180, 860),
            reload=False,
            reconnect_timeout=30.0,
            port=port,
            show=True,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Native window unavailable, falling back to browser UI")
        ui.run(
            root=create_desktop_app,
            title="公众号改写助手",
            reload=False,
            reconnect_timeout=30.0,
            port=port,
            show=True,
        )


if __name__ in {"__main__", "__mp_main__"}:
    main()
