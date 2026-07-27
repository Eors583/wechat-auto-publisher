from __future__ import annotations

from collections.abc import Callable
import json

from nicegui import ui

from app.ai.image_providers import is_image_provider
from app.editorial_review import DEFAULT_REVIEW_SCHEME_ID
from app.layout_profiles import normalize_layout
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    IMAGE_PROMPT_PURPOSE,
    public_prompt_templates,
)
from app.services.configuration import ConfigurationService
from app.services.creation_plans import CreationPlanService
from app.ui.panels.models import build_models_panel
from app.ui.panels.prompts import build_prompt_templates_panel
from app.ui.panels.review_jury import build_editorial_review_profiles_panel
from app.ui.state import AppState


def build_model_management_panel(state: AppState) -> None:
    """Expose text and image providers through one user-facing settings entry."""

    config = state.reload_config()
    service = ConfigurationService(state.db, config)
    models = service.list_models(include_config=True)
    text_count = sum(
        1
        for item in models
        if not is_image_provider(str(item.get("provider_type") or ""))
    )
    image_count = sum(
        1
        for item in models
        if is_image_provider(str(item.get("provider_type") or ""))
    )

    tabs = ui.tabs().classes("workspace-tabs w-full").props(
        "dense align=left indicator-color=teal-9 active-color=teal-10"
    )
    with tabs:
        all_tab = ui.tab("全部")
        text_tab = ui.tab("文章模型")
        image_tab = ui.tab("图片模型")
    with ui.tab_panels(tabs, value=all_tab).classes("w-full bg-transparent"):
        with ui.tab_panel(all_tab).classes("q-pa-none q-pt-md"):
            with ui.element("div").classes("card w-full"):
                ui.label("模型管理").classes("text-h6 text-weight-bold")
                ui.label(
                    "文章模型负责改写、标题和 AI 评审；图片模型负责正文配图与封面。"
                    "两种模型仍按各自协议独立验证，避免误配。"
                ).classes("muted")
            with ui.grid(columns=2).classes("w-full gap-4"):
                with ui.element("div").classes("card w-full"):
                    ui.label("文章模型").classes("text-subtitle1 text-weight-bold")
                    ui.label(f"已配置 {text_count} 个").classes("muted")
                    ui.button(
                        "管理文章模型",
                        on_click=lambda: tabs.set_value(text_tab),
                    ).props("unelevated color=teal-9 no-caps icon=article")
                with ui.element("div").classes("card w-full"):
                    ui.label("图片模型").classes("text-subtitle1 text-weight-bold")
                    ui.label(f"已配置 {image_count} 个").classes("muted")
                    ui.button(
                        "管理图片模型",
                        on_click=lambda: tabs.set_value(image_tab),
                    ).props("outline color=teal-9 no-caps icon=image")
        with ui.tab_panel(text_tab).classes("q-pa-none q-pt-md"):
            build_models_panel(state, purpose="text")
        with ui.tab_panel(image_tab).classes("q-pa-none q-pt-md"):
            build_models_panel(state, purpose="image")


def build_creation_plans_panel(
    state: AppState,
    *,
    on_plans_change: Callable[[], None] | None = None,
) -> None:
    """Group reusable writing rules under one product concept."""

    with ui.element("div").classes("card w-full"):
        ui.label("创作方案").classes("text-h6 text-weight-bold")
        ui.label(
            "创作方案决定文章怎么写、如何排版、图片与封面什么风格，"
            "以及 AI 从哪些角度评审。"
            "普通用户只需在公众号管理中选择默认方案；下面是高级规则管理。"
        ).classes("muted")
        ui.label(
            "草稿模板始终按公众号隔离保存，不会把一个公众号的 media_id "
            "或模板快照错误套到另一个公众号。"
        ).classes("text-positive text-caption")

    tabs = ui.tabs().classes("workspace-tabs w-full").props(
        "dense align=left indicator-color=teal-9 active-color=teal-10"
    )
    with tabs:
        plans_tab = ui.tab("方案管理")
        prompt_tab = ui.tab("写作与图片规则")
        review_tab = ui.tab("AI 评审方案")
    with ui.tab_panels(tabs, value=plans_tab).classes("w-full bg-transparent"):
        with ui.tab_panel(plans_tab).classes("q-pa-none q-pt-md"):
            _build_creation_plan_manager(
                state,
                on_plans_change=on_plans_change,
            )
        with ui.tab_panel(prompt_tab).classes("q-pa-none q-pt-md"):
            build_prompt_templates_panel(
                state,
                on_templates_change=on_plans_change,
            )
        with ui.tab_panel(review_tab).classes("q-pa-none q-pt-md"):
            build_editorial_review_profiles_panel(
                state,
                on_profiles_change=on_plans_change,
            )


def _build_creation_plan_manager(
    state: AppState,
    *,
    on_plans_change: Callable[[], None] | None = None,
) -> None:
    service = CreationPlanService(state.db, state.config)
    host = ui.column().classes("w-full gap-3")

    def open_editor(plan_id: str | None = None) -> None:
        record = service.get(plan_id) if plan_id else None
        account_options = {
            str(item["id"]): str(item.get("name") or item["id"])
            for item in state.db.list_official_accounts()
        }
        article_options = {
            "": "系统默认文章规则",
            **{
                str(item["id"]): str(item["name"])
                for item in public_prompt_templates(
                    state.db,
                    purpose=ARTICLE_PROMPT_PURPOSE,
                    enabled_only=True,
                )
            },
        }
        image_options = {
            "": "系统默认图片规则",
            **{
                str(item["id"]): str(item["name"])
                for item in public_prompt_templates(
                    state.db,
                    purpose=IMAGE_PROMPT_PURPOSE,
                    enabled_only=True,
                )
            },
        }
        review_options = {
            str(item["id"]): str(item["name"])
            for item in service.reviews.list_profiles(include_builtin=True)
            if bool(item.get("enabled", True))
        }
        with ui.dialog() as dialog, ui.card().classes("w-full").style(
            "max-width:720px"
        ):
            ui.label("编辑创作方案" if record else "新建创作方案").classes(
                "text-h6 text-weight-bold"
            )
            name_in = ui.input(
                "方案名称",
                value=str((record or {}).get("name") or ""),
                placeholder="例如：企业管理深度文章",
            ).classes("w-full").props("outlined stack-label")
            description_in = ui.textarea(
                "方案说明（可选）",
                value=str((record or {}).get("description") or ""),
            ).classes("w-full").props("outlined rows=2 stack-label")
            article_in = ui.select(
                article_options,
                value=str((record or {}).get("article_prompt_template_id") or ""),
                label="文章写作规则",
            ).classes("w-full").props("outlined stack-label options-dense")
            image_in = ui.select(
                image_options,
                value=str((record or {}).get("image_prompt_template_id") or ""),
                label="图片风格规则",
            ).classes("w-full").props("outlined stack-label options-dense")
            review_in = ui.select(
                review_options,
                value=str(
                    (record or {}).get("editorial_review_profile_id")
                    or DEFAULT_REVIEW_SCHEME_ID
                ),
                label="默认 AI 评审方案",
            ).classes("w-full").props("outlined stack-label options-dense")
            ui.separator().classes("q-my-sm")
            ui.label("排版、图片与公众号草稿模板").classes(
                "text-subtitle2 text-weight-bold"
            )
            ui.label(
                "可从一个公众号复制当前正文排版、正文配图和 AI 封面规则。"
                "不选择时，新方案不会覆盖公众号现有排版与图片设置；"
                "编辑旧方案时则保留方案里已经保存的规则。"
            ).classes("muted text-caption")
            source_account_in = ui.select(
                {"": "暂不复制公众号配置", **account_options},
                value="",
                label="从公众号复制当前配置（可选）",
            ).classes("w-full").props("outlined stack-label options-dense")
            capture_template_in = ui.switch(
                "同时保存该公众号的草稿模板（仅供这个公众号使用）",
                value=False,
            )
            capture_template_in.set_enabled(False)

            def sync_template_capture() -> None:
                enabled = bool(str(source_account_in.value or "").strip())
                capture_template_in.set_enabled(enabled)
                if not enabled:
                    capture_template_in.value = False

            source_account_in.on_value_change(lambda _: sync_template_capture())
            if record:
                ui.label(
                    "当前方案："
                    f'排版{"已保存" if record.get("has_layout") else "沿用公众号"} · '
                    f'图片/封面{"已保存" if record.get("has_image_settings") else "沿用公众号"} · '
                    f'专属草稿模板 {len(record.get("draft_template_bindings") or [])} 个'
                ).classes("text-caption text-teal-9")
            enabled_in = ui.switch(
                "启用此方案",
                value=bool((record or {}).get("enabled", True)),
            )

            def save() -> None:
                try:
                    source_account_id = str(source_account_in.value or "").strip()
                    copied_layout = None
                    copied_image_settings = None
                    if source_account_id:
                        source_account = state.db.get_official_account(
                            source_account_id
                        )
                        if source_account is None:
                            raise ValueError("用于复制配置的公众号不存在")
                        try:
                            raw_layout = json.loads(
                                str(source_account.get("layout_json") or "{}")
                            )
                        except (json.JSONDecodeError, TypeError):
                            raw_layout = {}
                        normalized = normalize_layout(raw_layout)
                        copied_layout = normalized
                        copied_image_settings = dict(
                            normalized.get("inline_images") or {}
                        )
                    service.save(
                        plan_id=plan_id,
                        name=str(name_in.value or ""),
                        description=str(description_in.value or ""),
                        article_prompt_template_id=str(article_in.value or ""),
                        image_prompt_template_id=str(image_in.value or ""),
                        editorial_review_profile_id=str(
                            review_in.value or DEFAULT_REVIEW_SCHEME_ID
                        ),
                        layout=copied_layout,
                        image_settings=copied_image_settings,
                        draft_template_account_id=(
                            source_account_id
                            if bool(capture_template_in.value)
                            else None
                        ),
                        enabled=bool(enabled_in.value),
                    )
                    dialog.close()
                    render()
                    if on_plans_change:
                        on_plans_change()
                    ui.notify("创作方案已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative", timeout=10000)

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button("保存方案", on_click=save).props(
                    "unelevated color=teal-9 no-caps icon=save"
                )
        dialog.open()

    def remove(plan_id: str) -> None:
        try:
            service.delete(plan_id)
            render()
            if on_plans_change:
                on_plans_change()
            ui.notify("创作方案已删除", type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(str(exc), type="negative", timeout=10000)

    def render() -> None:
        host.clear()
        with host:
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("可复用创作方案").classes(
                        "text-subtitle1 text-weight-bold"
                    )
                    ui.label(
                        "把文章规则、图片规则、正文排版、封面策略和 AI 评审"
                        "组合成一个选项，公众号只需绑定一次。"
                    ).classes("muted")
                ui.button(
                    "新建创作方案",
                    icon="add",
                    on_click=lambda: open_editor(),
                ).props("unelevated color=teal-9 no-caps")
            for plan in service.list():
                bindings = state.db.list_account_creation_plan_defaults(
                    plan_id=str(plan["id"])
                )
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-start justify-between"):
                        with ui.column().classes("gap-1").style(
                            "min-width:0;flex:1"
                        ):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(str(plan["name"])).classes(
                                    "text-subtitle1 text-weight-bold"
                                )
                                if plan.get("builtin"):
                                    ui.badge("系统内置").props("color=teal-7")
                                elif not plan.get("enabled"):
                                    ui.badge("已停用").props("color=grey-7")
                            if plan.get("description"):
                                ui.label(str(plan["description"])).classes("muted")
                            ui.label(
                                f'文章：{plan["article_prompt_template_name"]} · '
                                f'图片：{plan["image_prompt_template_name"]} · '
                                f'评审：{plan["editorial_review_profile_name"]}'
                            ).classes("text-caption")
                            ui.label(
                                f'排版：{"方案内置" if plan.get("has_layout") else "沿用公众号"} · '
                                f'图片/封面：{"方案内置" if plan.get("has_image_settings") else "沿用公众号"} · '
                                f'草稿模板：{len(plan.get("draft_template_bindings") or [])} 个公众号专属'
                            ).classes("text-caption")
                            ui.label(
                                f"已绑定 {len(bindings)} 个公众号"
                            ).classes("muted text-caption")
                            for issue in list(plan.get("issues") or []):
                                ui.label(f"配置提示：{issue}").classes(
                                    "text-warning text-caption"
                                )
                        if not plan.get("builtin"):
                            with ui.row().classes("items-center"):
                                ui.button(
                                    "编辑",
                                    on_click=lambda _=None, pid=str(plan["id"]): open_editor(
                                        pid
                                    ),
                                ).props("outline dense color=teal-9 no-caps")
                                ui.button(
                                    "删除",
                                    on_click=lambda _=None, pid=str(plan["id"]): remove(
                                        pid
                                    ),
                                ).props("flat dense color=red-7 no-caps")

    render()


__all__ = [
    "build_creation_plans_panel",
    "build_model_management_panel",
]
