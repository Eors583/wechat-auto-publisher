from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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
from app.ui.lifecycle import client_timer
from app.ui.panels.models import build_models_panel
from app.ui.panels.prompts import build_prompt_templates_panel
from app.ui.panels.review_jury import build_editorial_review_profiles_panel
from app.ui.state import AppState


def build_model_management_panel(state: AppState) -> None:
    """Administrator backend for users and merchant-managed AI providers."""

    config = state.reload_config()
    if not bool(getattr(state, "is_admin", True)):
        with ui.element("div").classes("card w-full"):
            ui.label("无权访问后台管理").classes(
                "text-h6 text-weight-bold text-negative"
            )
            ui.label(
                "请回到主站右上角“设置 → 我的大模型”管理个人模型。"
            ).classes("muted")
        return
    platform_db = state.db.for_user("")
    service = ConfigurationService(platform_db, config)
    models = service.list_models(include_config=False)
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

    if hasattr(state, "auth") and hasattr(state, "model_options"):
        with ui.element("div").classes("card w-full"):
            ui.label("商户后台管理").classes("text-h6 text-weight-bold")
            ui.label(
                "这里维护平台公共模型；普通用户在自己的账号下维护私有模型和密钥。"
            ).classes("muted")
            users = state.auth.list_users()
            ui.label(
                f"注册用户 {len(users)} 个 · 管理员 "
                f"{sum(1 for item in users if item.get('role') == 'admin')} 个"
            ).classes("text-caption text-grey-7")

        @ui.refreshable
        def render_users() -> None:
            with ui.element("div").classes("card w-full"):
                ui.label("用户管理").classes("text-subtitle1 text-weight-bold")
                for user in state.auth.list_users():
                    with ui.row().classes(
                        "w-full items-center justify-between q-py-xs"
                    ):
                        with ui.column().classes("gap-0"):
                            ui.label(str(user["username"])).classes(
                                "text-weight-medium"
                            )
                            ui.label(
                                "管理员"
                                if user.get("role") == "admin"
                                else "普通用户"
                            ).classes("text-caption text-grey-6")
                        enabled = ui.switch(
                            "启用",
                            value=bool(user.get("enabled")),
                        ).props("dense")

                        def update_user(
                            event: Any,
                            *,
                            user_id: str = str(user["id"]),
                        ) -> None:
                            try:
                                state.auth.set_user_enabled(
                                    user_id,
                                    bool(event.value),
                                    actor_user_id=str(
                                        (state.current_user or {}).get("id")
                                        or ""
                                    ),
                                )
                                ui.notify(
                                    "用户状态已更新", color="positive"
                                )
                            except Exception as exc:  # noqa: BLE001
                                ui.notify(str(exc), color="negative")
                                render_users.refresh()

                        enabled.on_value_change(update_user)

        render_users()

        text_options = {
            str(item["id"]): f'{item["name"]} · {item["model"]}'
            for item in service.list_models(
                enabled_only=True,
                purpose="text",
                include_config=False,
            )
        }
        current_default_model = str(
            state.db.get_setting("merchant.default_text_model_id") or ""
        )
        if current_default_model not in text_options:
            current_default_model = ""
        with ui.element("div").classes("card w-full"):
            ui.label("平台默认文章模型").classes(
                "text-subtitle1 text-weight-bold"
            )
            ui.label(
                "公众号没有单独选择模型时，自动使用这里的默认模型。"
            ).classes("muted")
            default_model_select = ui.select(
                {"": "暂不设置", **text_options},
                value=current_default_model,
                label="默认文章模型",
            ).classes("w-full").props("outlined dense options-dense")

            def save_default_model(event: Any) -> None:
                selected = str(event.value or "")
                state.db.set_setting(
                    "merchant.default_text_model_id", selected
                )
                state.refresh_model_selects()
                state.refresh_account_selects()
                ui.notify("平台默认模型已更新", color="positive")

            default_model_select.on_value_change(save_default_model)

    tabs = ui.tabs().classes("workspace-tabs w-full").props(
        "dense align=left indicator-color=teal-9 active-color=teal-10"
    )
    with tabs:
        all_tab = ui.tab("全部")
        text_tab = ui.tab("文章模型")
        image_tab = ui.tab("图片模型")
    with ui.tab_panels(tabs, value=all_tab).classes("w-full bg-transparent"):
        with ui.tab_panel(all_tab).classes("q-pa-none q-pt-md"):
            all_host = ui.column().classes("w-full")
        with ui.tab_panel(text_tab).classes("q-pa-none q-pt-md"):
            text_host = ui.column().classes("w-full")
        with ui.tab_panel(image_tab).classes("q-pa-none q-pt-md"):
            image_host = ui.column().classes("w-full")

    hosts = {
        str(all_tab.props["name"]): all_host,
        str(text_tab.props["name"]): text_host,
        str(image_tab.props["name"]): image_host,
    }
    for host in hosts.values():
        with host:
            ui.label("正在加载…").classes("muted q-pa-md")

    def mount_overview() -> None:
        all_host.clear()
        with all_host:
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

    def mount_text_models() -> None:
        text_host.clear()
        with text_host:
            build_models_panel(state, purpose="text", db=platform_db)

    def mount_image_models() -> None:
        image_host.clear()
        with image_host:
            build_models_panel(state, purpose="image", db=platform_db)

    mounts = {
        str(all_tab.props["name"]): mount_overview,
        str(text_tab.props["name"]): mount_text_models,
        str(image_tab.props["name"]): mount_image_models,
    }
    mounted: set[str] = set()
    scheduled: set[str] = set()

    def mount_tab(tab: Any) -> None:
        name = str(tab.props["name"] if hasattr(tab, "props") else tab)
        if name in mounted:
            return
        mounts[name]()
        mounted.add(name)
        scheduled.discard(name)

    def schedule_tab(tab: Any) -> None:
        name = str(tab.props["name"] if hasattr(tab, "props") else tab)
        if name in mounted or name in scheduled:
            return
        scheduled.add(name)
        client_timer(
            0.01,
            lambda: mount_tab(tab),
            once=True,
            immediate=False,
        )

    mount_tab(all_tab)
    tabs.on_value_change(lambda event: schedule_tab(event.value))


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
            plans_host = ui.column().classes("w-full")
        with ui.tab_panel(prompt_tab).classes("q-pa-none q-pt-md"):
            prompt_host = ui.column().classes("w-full")
        with ui.tab_panel(review_tab).classes("q-pa-none q-pt-md"):
            review_host = ui.column().classes("w-full")

    hosts = {
        str(plans_tab.props["name"]): plans_host,
        str(prompt_tab.props["name"]): prompt_host,
        str(review_tab.props["name"]): review_host,
    }
    for host in hosts.values():
        with host:
            ui.label("正在加载…").classes("muted q-pa-md")

    def mount_plans() -> None:
        plans_host.clear()
        with plans_host:
            _build_creation_plan_manager(
                state,
                on_plans_change=on_plans_change,
            )

    def mount_prompts() -> None:
        prompt_host.clear()
        with prompt_host:
            build_prompt_templates_panel(
                state,
                on_templates_change=on_plans_change,
            )

    def mount_reviews() -> None:
        review_host.clear()
        with review_host:
            build_editorial_review_profiles_panel(
                state,
                on_profiles_change=on_plans_change,
            )

    mounts = {
        str(plans_tab.props["name"]): mount_plans,
        str(prompt_tab.props["name"]): mount_prompts,
        str(review_tab.props["name"]): mount_reviews,
    }
    mounted: set[str] = set()
    scheduled: set[str] = set()

    def mount_tab(tab: Any) -> None:
        name = str(tab.props["name"] if hasattr(tab, "props") else tab)
        if name in mounted:
            return
        mounts[name]()
        mounted.add(name)
        scheduled.discard(name)

    def schedule_tab(tab: Any) -> None:
        name = str(tab.props["name"] if hasattr(tab, "props") else tab)
        if name in mounted or name in scheduled:
            return
        scheduled.add(name)
        client_timer(
            0.01,
            lambda: mount_tab(tab),
            once=True,
            immediate=False,
        )

    mount_tab(plans_tab)
    tabs.on_value_change(lambda event: schedule_tab(event.value))


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
        with ui.dialog() as dialog, ui.card().classes("w-full ops-dialog-md"):
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
                        with ui.column().classes("gap-1 ops-flex-copy"):
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
