from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    IMAGE_PROMPT_PURPOSE,
    MAX_ARTICLE_PROMPT_TEMPLATE_LENGTH,
    MAX_IMAGE_PROMPT_TEMPLATE_LENGTH,
    delete_prompt_template,
    prompt_template_usages,
    public_prompt_templates,
    save_prompt_template,
)
from app.ui.state import AppState


PURPOSE_UI = {
    ARTICLE_PROMPT_PURPOSE: {
        "title": "文章提示词模板",
        "description": (
            "定义公众号的受众、观点、语气、结构和标题调性。系统仍会自动保留字数、"
            "JSON 输出、重点加粗等基础协议。"
        ),
        "name_placeholder": "例如：蓝血研究深度评论风",
        "content_placeholder": (
            "例如：面向企业经营者，观点务实、有判断力；优先使用经营案例和数据，"
            "避免空泛口号；标题克制但有信息增量。"
        ),
    },
    IMAGE_PROMPT_PURPOSE: {
        "title": "图片提示词模板",
        "description": (
            "定义正文配图和封面图的视觉风格。系统仍会自动加入文章标题、正文主题、"
            "当前论点以及禁止文字、水印和 Logo 等规则。"
        ),
        "name_placeholder": "例如：深蓝科技纪实风",
        "content_placeholder": (
            "例如：真实商业新闻摄影，深蓝与青绿色调，真实人物与业务场景，"
            "自然光线，构图简洁，整篇文章保持统一视觉语言。"
        ),
    },
}


def build_prompt_templates_panel(
    state: AppState,
    *,
    on_templates_change: Callable[[], None] | None = None,
) -> None:
    """Manage article and image prompt templates in two isolated catalogs."""

    host = ui.column().classes("w-full")

    def open_editor(purpose: str, template_id: str | None = None) -> None:
        meta = PURPOSE_UI[purpose]
        record = state.db.get_prompt_template(template_id) if template_id else None
        if record and str(record.get("purpose") or "") != purpose:
            ui.notify("提示词模板类型不匹配", type="negative")
            return
        with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width:760px"):
            action = "编辑" if record else "添加"
            ui.label(f'{action}{meta["title"]}').classes("text-h6 text-weight-bold")
            ui.label(str(meta["description"])).classes("muted")
            name_in = ui.input(
                "模板名称",
                value=str((record or {}).get("name") or ""),
                placeholder=str(meta["name_placeholder"]),
            ).classes("w-full").props("outlined stack-label")
            content_in = ui.textarea(
                "提示词模板内容",
                value=str((record or {}).get("content") or ""),
                placeholder=str(meta["content_placeholder"]),
            ).classes("w-full").props(
                "outlined rows=9 stack-label "
                f"maxlength={MAX_ARTICLE_PROMPT_TEMPLATE_LENGTH if purpose == ARTICLE_PROMPT_PURPOSE else MAX_IMAGE_PROMPT_TEMPLATE_LENGTH} "
                "counter"
            )
            enabled_in = ui.switch(
                "启用模板", value=bool((record or {}).get("enabled", True))
            )
            if record:
                usages = prompt_template_usages(state.db, str(record["id"]))
                if usages:
                    ui.label("正在使用：" + "、".join(usages)).classes(
                        "text-positive text-caption"
                    )

            def submit() -> None:
                try:
                    save_prompt_template(
                        state.db,
                        template_id=template_id,
                        name=str(name_in.value or ""),
                        content=str(content_in.value or ""),
                        enabled=bool(enabled_in.value),
                        purpose=purpose,
                    )
                    dialog.close()
                    render_templates()
                    if on_templates_change is not None:
                        on_templates_change()
                    ui.notify(f'{meta["title"]}已保存', type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"保存失败：{exc}", type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("保存模板", on_click=submit).props(
                    "unelevated color=teal-9 no-caps icon=save"
                )
        dialog.open()

    def confirm_delete(template_id: str, name: str) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f"确定删除提示词模板“{name}”吗？").classes(
                "text-weight-medium"
            )

            def remove() -> None:
                try:
                    delete_prompt_template(state.db, template_id)
                    dialog.close()
                    render_templates()
                    if on_templates_change is not None:
                        on_templates_change()
                    ui.notify("提示词模板已删除", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="warning")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("删除", on_click=remove).props(
                    "unelevated color=red-7 no-caps"
                )
        dialog.open()

    def render_catalog(purpose: str) -> None:
        meta = PURPOSE_UI[purpose]
        templates = public_prompt_templates(state.db, purpose=purpose)
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label(f'{meta["title"]}管理').classes(
                        "text-h6 text-weight-bold"
                    )
                    ui.label(str(meta["description"])).classes("muted")
                    ui.label(
                        "这里只管理用户自定义模板；系统默认模板只在公众号管理中作为选项出现。"
                    ).classes("muted")
                ui.button(
                    f'添加{meta["title"]}',
                    on_click=lambda selected=purpose: open_editor(selected),
                ).props("unelevated color=teal-9 no-caps icon=add")

        if not templates:
            with ui.element("div").classes("card w-full"):
                ui.label(f'尚未添加自定义{meta["title"]}').classes(
                    "text-weight-medium"
                )
                ui.label("可点击上方按钮添加；默认模板无需创建。 ").classes("muted")
            return

        for item in templates:
            template_id = str(item["id"])
            name = str(item["name"])
            usages = prompt_template_usages(state.db, template_id)
            with ui.element("div").classes("card w-full"):
                with ui.row().classes("w-full items-start justify-between"):
                    with ui.column().classes("gap-0").style("min-width:0;flex:1"):
                        ui.label(name).classes("text-weight-bold")
                        ui.label("启用" if item.get("enabled") else "已停用").classes(
                            "text-positive" if item.get("enabled") else "muted"
                        )
                        if usages:
                            ui.label("公众号：" + "、".join(usages)).classes("muted")
                    with ui.row().classes("items-center"):
                        ui.button(
                            "编辑",
                            on_click=lambda tid=template_id, selected=purpose: open_editor(
                                selected, tid
                            ),
                        ).props("flat dense color=teal-9 no-caps")
                        ui.button(
                            "删除",
                            on_click=lambda tid=template_id, label=name: confirm_delete(
                                tid, label
                            ),
                        ).props("flat dense color=red-7 no-caps")
                ui.label(str(item.get("content") or "")).classes("q-mt-sm")

    def render_templates() -> None:
        host.clear()
        with host:
            with ui.tabs().classes("w-full") as tabs:
                article_tab = ui.tab("文章提示词模板", icon="article")
                image_tab = ui.tab("图片提示词模板", icon="image")
            with ui.tab_panels(tabs, value=article_tab).classes("w-full"):
                with ui.tab_panel(article_tab).classes("q-pa-none q-pt-md"):
                    render_catalog(ARTICLE_PROMPT_PURPOSE)
                with ui.tab_panel(image_tab).classes("q-pa-none q-pt-md"):
                    render_catalog(IMAGE_PROMPT_PURPOSE)

    render_templates()
