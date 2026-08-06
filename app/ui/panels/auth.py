from __future__ import annotations

from typing import Any, Callable

from nicegui import app as nicegui_app
from nicegui import run, ui

from app.services.auth import AuthService
from app.ui.state import set_button_loading


AUTH_STORAGE_KEY = "wechat_publisher_auth_token"


def current_desktop_user(service: AuthService) -> dict[str, Any] | None:
    token = str(nicegui_app.storage.user.get(AUTH_STORAGE_KEY) or "")
    user = service.authenticate(token)
    if not user and token:
        nicegui_app.storage.user.pop(AUTH_STORAGE_KEY, None)
    return user


def logout_desktop_user(service: AuthService) -> None:
    token = str(nicegui_app.storage.user.get(AUTH_STORAGE_KEY) or "")
    service.logout(token)
    nicegui_app.storage.user.pop(AUTH_STORAGE_KEY, None)


def build_auth_screen(
    service: AuthService,
    *,
    on_authenticated: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Render the blocking login/register screen for the desktop/web UI."""

    ui.add_css(
        """
        .auth-shell {
            min-height: 100vh;
            display: grid;
            place-items: center;
            padding: var(--ui-space-8) var(--ui-space-5);
            background:
                radial-gradient(circle at 15% 15%, color-mix(in srgb, var(--ui-color-brand-hover) 16%, transparent), transparent 34%),
                radial-gradient(circle at 85% 85%, color-mix(in srgb, var(--ui-color-brand) 12%, transparent), transparent 38%),
                var(--ui-color-bg-subtle);
        }
        .auth-card {
            width: min(var(--ui-layout-auth-card), 100%);
            padding: var(--ui-space-8);
            border-radius: var(--ui-radius-2xl);
            background: var(--ui-color-surface-glass);
            border: 1px solid color-mix(in srgb, var(--ui-color-brand) 14%, transparent);
            box-shadow: var(--ui-shadow-dialog);
        }
        """
    )

    with ui.element("div").classes("auth-shell"):
        with ui.column().classes("auth-card gap-4"):
            ui.label("公众号智能运营助手").classes(
                "text-h5 text-weight-bold text-teal-10"
            )
            ui.label(
                "登录后使用商户统一提供的 AI 模型。系统只写入公众号草稿箱，不会自动群发。"
            ).classes("text-body2 text-grey-7")
            tabs = ui.tabs().props(
                "dense align=left active-color=teal-9 indicator-color=teal-8"
            )
            with tabs:
                login_tab = ui.tab("登录")
                register_tab = ui.tab("注册")
            with ui.tab_panels(tabs, value=login_tab).classes(
                "w-full bg-transparent"
            ):
                with ui.tab_panel(login_tab).classes("px-0"):
                    login_username = ui.input("用户名").classes("w-full").props(
                        "outlined autocomplete=username"
                    )
                    login_password = ui.input(
                        "密码", password=True, password_toggle_button=True
                    ).classes("w-full").props(
                        "outlined autocomplete=current-password"
                    )
                    login_error = ui.label("").classes(
                        "text-negative text-caption"
                    )

                    async def do_login() -> None:
                        set_button_loading(login_button, True, "正在登录…")
                        login_error.set_text("")
                        try:
                            result = await run.io_bound(
                                lambda: service.login(
                                    str(login_username.value or ""),
                                    str(login_password.value or ""),
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            login_error.set_text(str(exc))
                            return
                        finally:
                            set_button_loading(login_button, False)
                        nicegui_app.storage.user[AUTH_STORAGE_KEY] = str(
                            result["token"]
                        )
                        if on_authenticated:
                            on_authenticated(dict(result["user"]))
                        ui.navigate.reload()

                    login_button = ui.button(
                        "登录并进入工作台",
                        icon="login",
                        on_click=do_login,
                    ).classes("w-full").props("unelevated color=teal-9")
                    login_password.on(
                        "keydown.enter",
                        lambda: do_login(),
                    )
                    ui.label("默认管理员：lanxue / lanxue").classes(
                        "text-caption text-grey-6"
                    )

                with ui.tab_panel(register_tab).classes("px-0"):
                    register_username = ui.input("设置用户名").classes(
                        "w-full"
                    ).props("outlined autocomplete=username")
                    register_password = ui.input(
                        "设置密码（至少 6 位）",
                        password=True,
                        password_toggle_button=True,
                    ).classes("w-full").props(
                        "outlined autocomplete=new-password"
                    )
                    register_confirm = ui.input(
                        "再次输入密码",
                        password=True,
                        password_toggle_button=True,
                    ).classes("w-full").props(
                        "outlined autocomplete=new-password"
                    )
                    register_error = ui.label("").classes(
                        "text-negative text-caption"
                    )

                    async def do_register() -> None:
                        register_error.set_text("")
                        if str(register_password.value or "") != str(
                            register_confirm.value or ""
                        ):
                            register_error.set_text("两次输入的密码不一致")
                            return
                        set_button_loading(register_button, True, "正在注册…")
                        try:
                            await run.io_bound(
                                lambda: service.register(
                                    str(register_username.value or ""),
                                    str(register_password.value or ""),
                                )
                            )
                            result = await run.io_bound(
                                lambda: service.login(
                                    str(register_username.value or ""),
                                    str(register_password.value or ""),
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            register_error.set_text(str(exc))
                            return
                        finally:
                            set_button_loading(register_button, False)
                        nicegui_app.storage.user[AUTH_STORAGE_KEY] = str(
                            result["token"]
                        )
                        if on_authenticated:
                            on_authenticated(dict(result["user"]))
                        ui.navigate.reload()

                    register_button = ui.button(
                        "注册并进入工作台",
                        icon="person_add",
                        on_click=do_register,
                    ).classes("w-full").props("unelevated color=teal-9")


__all__ = [
    "AUTH_STORAGE_KEY",
    "build_auth_screen",
    "current_desktop_user",
    "logout_desktop_user",
]
