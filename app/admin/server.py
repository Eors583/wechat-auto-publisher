from __future__ import annotations

import os
from typing import Any

from nicegui import app as nicegui_app
from nicegui import run, ui

from app.ai.image_providers import is_image_provider
from app.config import database_target, load_config
from app.services.wechat_relay_settings import public_wechat_relay_settings
from app.ui.auth_persistence import auth_session_middleware_kwargs
from app.ui.panels.auth import AUTH_STORAGE_KEY, current_desktop_user
from app.ui.panels.billing import build_admin_billing_panel
from app.ui.panels.settings_hub import build_model_management_panel
from app.ui.panels.wechat_relay import build_wechat_relay_panel
from app.ui.state import AppState, set_button_loading

ADMIN_CSS = """
html,
body,
#app,
.nicegui-layout,
.q-page-container,
.q-page,
.nicegui-content {
    height: 100%;
    min-height: 0 !important;
    overflow: hidden;
}
.nicegui-content {
    box-sizing: border-box;
}
body {
    background: #f5f7fb;
    color: #172033;
}
.admin-shell {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
    min-width: 0;
    overflow: hidden;
}
.admin-header {
    flex: 0 0 auto;
    background: linear-gradient(115deg, #0f172a 0%, #123c49 58%, #0f766e 100%);
    color: white;
    padding: 22px 30px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, .16);
}
.admin-content {
    flex: 1 1 auto;
    min-height: 0;
    width: calc(100% - 36px);
    max-width: 1440px;
    margin: 22px auto 42px;
    align-self: center;
    overflow-x: hidden;
    overflow-y: auto;
}
.admin-content > .q-tab-panels {
    flex: 0 0 auto;
    height: auto !important;
    overflow: visible !important;
}
.admin-content > .q-tab-panels > .q-panel,
.admin-content > .q-tab-panels .q-tab-panel {
    height: auto !important;
    overflow: visible !important;
}
.admin-card, .card {
    background: white;
    border: 1px solid #e3e8f0;
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 8px 24px rgba(15, 23, 42, .055);
}
.admin-stat {
    min-height: 112px;
}
.admin-stat-value {
    font-size: 28px;
    font-weight: 800;
    color: #0f766e;
}
.admin-login {
    height: 100%;
    min-height: 0;
    display: grid;
    place-items: center;
    padding: 28px;
    background:
        radial-gradient(circle at 15% 20%, rgba(20,184,166,.18), transparent 34%),
        radial-gradient(circle at 85% 80%, rgba(14,116,144,.16), transparent 36%),
        #eef3f7;
}
.admin-login-card {
    width: min(460px, 100%);
    padding: 32px;
    border-radius: 22px;
    background: rgba(255,255,255,.98);
    border: 1px solid rgba(15,118,110,.15);
    box-shadow: 0 28px 80px rgba(15,23,42,.14);
}
.workspace-tabs {
    background: white;
    border: 1px solid #e3e8f0;
    border-radius: 14px;
    padding: 4px 8px;
}
.muted {
    color: #64748b;
}
"""


def _logout(state: AppState) -> None:
    token = str(nicegui_app.storage.user.get(AUTH_STORAGE_KEY) or "")
    if token:
        state.auth.logout(token)
    nicegui_app.storage.user.pop(AUTH_STORAGE_KEY, None)
    ui.navigate.reload()


def _build_admin_login(state: AppState) -> None:
    with ui.element("div").classes("admin-login"):
        with ui.column().classes("admin-login-card gap-4"):
            ui.label("商户管理后台").classes(
                "text-h5 text-weight-bold text-teal-10"
            )
            ui.label(
                "统一管理平台模型、用户和微信公众号云中转。仅管理员账号可以登录。"
            ).classes("text-body2 text-grey-7")
            username = (
                ui.input("管理员账号")
                .classes("w-full")
                .props("outlined autocomplete=username")
            )
            password = (
                ui.input(
                    "密码",
                    password=True,
                    password_toggle_button=True,
                )
                .classes("w-full")
                .props("outlined autocomplete=current-password")
            )
            error = ui.label("").classes("text-negative text-caption")

            async def login() -> None:
                set_button_loading(button, True, "正在登录商户后台…")
                error.set_text("")
                try:
                    result = await run.io_bound(
                        lambda: state.auth.login(
                            str(username.value or ""),
                            str(password.value or ""),
                        )
                    )
                    user = dict(result.get("user") or {})
                    if str(user.get("role") or "") != "admin":
                        state.auth.logout(str(result.get("token") or ""))
                        raise ValueError("该账号不是管理员，无法进入商户后台")
                    nicegui_app.storage.user[AUTH_STORAGE_KEY] = str(
                        result["token"]
                    )
                except Exception as exc:  # noqa: BLE001
                    error.set_text(str(exc))
                    return
                finally:
                    set_button_loading(button, False)
                ui.navigate.reload()

            button = (
                ui.button(
                    "登录商户后台",
                    icon="admin_panel_settings",
                    on_click=login,
                )
                .classes("w-full")
                .props("unelevated color=teal-9 no-caps")
            )
            password.on("keydown.enter", lambda: login())
            ui.label("默认管理员：lanxue / lanxue").classes(
                "text-caption text-grey-6"
            )


def _model_counts(state: AppState) -> tuple[int, int]:
    records = state.db.list_ai_models(enabled_only=False)
    text_count = sum(
        1
        for item in records
        if not is_image_provider(str(item.get("provider_type") or ""))
    )
    return text_count, len(records) - text_count


def _build_overview(state: AppState) -> None:
    users = state.auth.list_users()
    text_count, image_count = _model_counts(state)
    relay = public_wechat_relay_settings(state.db)
    with ui.grid(columns=4).classes("w-full gap-4"):
        for label, value, detail, icon in (
            (
                "注册用户",
                str(len(users)),
                f"管理员 {sum(1 for item in users if item.get('role') == 'admin')} 个",
                "group",
            ),
            ("文章模型", str(text_count), "平台统一提供", "article"),
            ("图片模型", str(image_count), "正文配图与封面", "image"),
            (
                "微信云中转",
                "已启用" if relay.get("enabled") else "未启用",
                "固定出口 IP 连接",
                "cloud_sync",
            ),
        ):
            with ui.element("div").classes("admin-card admin-stat"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(label).classes("text-subtitle2 text-grey-7")
                    ui.icon(icon, color="teal-8", size="24px")
                ui.label(value).classes("admin-stat-value")
                ui.label(detail).classes("text-caption text-grey-6")

    with ui.element("div").classes("admin-card w-full q-mt-md"):
        ui.label("统一配置中心").classes("text-h6 text-weight-bold")
        ui.label(
            "这里保存的是全平台公共配置。模型密钥和中转凭证不会展示给普通用户；"
            "运营前台只读取管理员已经启用的模型和连接方式。"
        ).classes("muted")
        with ui.row().classes("q-mt-sm q-gutter-sm"):
            ui.badge("PostgreSQL", color="blue-grey-8")
            ui.badge("管理员权限隔离", color="teal-8")
            ui.badge("配置实时生效", color="positive")


def _build_user_panel(state: AppState) -> None:
    @ui.refreshable
    def users() -> None:
        with ui.element("div").classes("admin-card w-full"):
            ui.label("用户管理").classes("text-h6 text-weight-bold")
            ui.label(
                "控制注册用户是否可以登录运营前台。管理员不能停用自己。"
            ).classes("muted")
            for user in state.auth.list_users():
                with ui.row().classes(
                    "w-full items-center justify-between q-py-sm"
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
                                    (state.current_user or {}).get("id") or ""
                                ),
                            )
                            ui.notify("用户状态已更新", type="positive")
                        except Exception as exc:  # noqa: BLE001
                            ui.notify(str(exc), type="negative")
                            users.refresh()

                    enabled.on_value_change(update_user)

    users()


def create_admin_app() -> None:
    ui.add_css(ADMIN_CSS)
    state = AppState(recover_stale_work=False)
    state.bind_user(current_desktop_user(state.auth))
    if not state.current_user:
        _build_admin_login(state)
        return
    if str(state.current_user.get("role") or "") != "admin":
        _logout(state)
        _build_admin_login(state)
        return

    with ui.element("div").classes("admin-shell"):
        with ui.element("header").classes("admin-header w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("MERCHANT CONTROL CENTER").classes(
                        "text-caption text-teal-2"
                    )
                    ui.label("公众号智能运营助手 · 商户后台").classes(
                        "text-h5 text-weight-bold"
                    )
                    ui.label(
                        "统一管理公共模型、用户权限和微信固定 IP 中转"
                    ).classes("text-body2 text-blue-grey-2")
                with ui.row().classes("items-center q-gutter-sm"):
                    ui.badge(
                        f'{state.current_user["username"]} · 管理员',
                        color="teal-7",
                    )
                    ui.button(
                        "退出",
                        icon="logout",
                        on_click=lambda: _logout(state),
                    ).props("flat color=white no-caps")

        with ui.column().classes("admin-content gap-4"):
            tabs = ui.tabs().classes("workspace-tabs w-full").props(
                "dense align=left indicator-color=teal-9 active-color=teal-10"
            )
            with tabs:
                overview_tab = ui.tab("控制台", icon="dashboard")
                models_tab = ui.tab("公共模型", icon="smart_toy")
                relay_tab = ui.tab("微信中转", icon="cloud_sync")
                billing_tab = ui.tab("AI 成本", icon="query_stats")
                users_tab = ui.tab("用户管理", icon="group")
            with ui.tab_panels(
                tabs,
                value=overview_tab,
            ).classes("w-full bg-transparent"):
                with ui.tab_panel(overview_tab).classes("q-pa-none"):
                    _build_overview(state)
                with ui.tab_panel(models_tab).classes("q-pa-none"):
                    build_model_management_panel(state)
                with ui.tab_panel(relay_tab).classes("q-pa-none"):
                    build_wechat_relay_panel(
                        state,
                        allow_test_account_configuration=True,
                    )
                with ui.tab_panel(billing_tab).classes("q-pa-none"):
                    build_admin_billing_panel(state)
                with ui.tab_panel(users_tab).classes("q-pa-none"):
                    _build_user_panel(state)


def main() -> None:
    database_target(load_config())
    ui.run(
        root=create_admin_app,
        host="0.0.0.0",
        port=int(os.getenv("WECHAT_PUBLISHER_ADMIN_PORT") or "18767"),
        title="商户管理后台",
        native=False,
        show=False,
        reload=False,
        reconnect_timeout=30.0,
        root_path=str(
            os.getenv("WECHAT_PUBLISHER_ADMIN_ROOT_PATH") or ""
        ).rstrip("/"),
        storage_secret=str(
            os.getenv("AUTH_STORAGE_SECRET")
            or "wechat-auto-publisher-local-storage-v1"
        ),
        session_middleware_kwargs=auth_session_middleware_kwargs(),
    )


if __name__ == "__main__":
    main()
