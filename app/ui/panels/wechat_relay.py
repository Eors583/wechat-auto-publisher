from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from nicegui import core, run, ui

from app.accounts import apply_account_selection, public_accounts
from app.config import load_config
from app.services.wechat_relay_settings import (
    DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP,
    DEFAULT_WECHAT_RELAY_GATEWAY_URL,
    effective_wechat_relay_test_account,
    effective_wechat_relay_settings,
    public_wechat_relay_test_account,
    public_wechat_relay_settings,
    save_wechat_relay_test_account,
    save_wechat_relay_settings,
)
from app.ui.state import AppState, set_button_loading
from app.wechat.draft import batchget_drafts
from app.wechat.factory import build_wechat_auth, build_wechat_client


DEFAULT_GATEWAY_URL = DEFAULT_WECHAT_RELAY_GATEWAY_URL
FIXED_EGRESS_IP = DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP
RELAY_TEST_ACCOUNT_ID = "__relay_test_account__"


def _relay_account_options(db: Any) -> dict[str, str]:
    """Return accounts that can be used for a real relay connection test."""

    return {
        str(item["id"]): (
            str(item.get("name") or item["id"])
            + ("（已停用）" if not bool(item.get("enabled", True)) else "")
        )
        for item in public_accounts(db)
        if bool(item.get("has_app_secret", True))
    }


def _friendly_relay_error(exc: Exception) -> str:
    message = str(exc).strip()
    lower = message.casefold()
    if "40164" in message or "invalid ip" in lower or "whitelist" in lower:
        return (
            f"云中转已连接，但固定出口 IP {FIXED_EGRESS_IP} "
            "尚未加入该公众号的开发者 IP 白名单。"
        )
    if "40125" in message or "invalid appsecret" in lower:
        return "该公众号的 AppSecret 无效，请先到“设置 → 公众号”更新凭证。"
    if (
        "401 unauthorized" in lower
        or "403 forbidden" in lower
        or "status code 401" in lower
        or "status code 403" in lower
        or "gateway http 401" in lower
        or "gateway http 403" in lower
    ):
        return "云中转用户名或密码不正确，请检查后重试。"
    if any(
        marker in lower
        for marker in (
            "gateway http 502",
            "gateway http 503",
            "gateway http 504",
        )
    ):
        return "云中转服务器暂时无法访问微信上游，请稍后重试或检查 Nginx 服务。"
    if "404" in message or "not found" in lower:
        return "云中转地址不存在，请检查地址是否完整包含 /wechat-relay。"
    if "certificate" in lower or "ssl" in lower:
        return "云中转的 HTTPS 证书校验失败，请检查域名证书。"
    if (
        "timed out" in lower
        or "timeout" in lower
        or "10060" in message
        or "connecterror" in lower
    ):
        return "无法连接云中转，请检查服务器、域名和防火墙是否正常。"
    if not message:
        return "云中转测试失败，请检查配置后重试。"
    return f"云中转测试失败：{message}"


def _test_relay_connection(
    state: AppState,
    *,
    account_id: str,
    relay_settings: dict[str, Any],
    test_account: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify both token and a read-only draft request through the gateway."""

    started_at = perf_counter()
    if test_account is not None:
        config = load_config()
        account = {
            "name": str(test_account.get("name") or "中转测试公众号"),
        }
        app_id = str(test_account.get("app_id") or "")
        app_secret = str(test_account.get("app_secret") or "")
    else:
        config, account = apply_account_selection(
            load_config(),
            state.db,
            account_id,
            allow_disabled=True,
        )
        wechat = dict(config.get("wechat") or {})
        app_id = str(wechat.get("app_id") or "")
        app_secret = str(wechat.get("app_secret") or "")
    auth = build_wechat_auth(
        config,
        state.db,
        app_id,
        app_secret,
        relay_settings=relay_settings,
        cache_key=f"wechat_relay_test:{app_id}",
    )
    auth.get_access_token(force_refresh=True)
    client = build_wechat_client(
        config,
        state.db,
        app_id,
        app_secret,
        relay_settings=relay_settings,
        cache_key=f"wechat_relay_test:{app_id}",
    )
    drafts = batchget_drafts(client, offset=0, count=1, no_content=1)
    return {
        "account_name": str(account.get("name") or account_id),
        "draft_count": int(drafts.get("total_count") or 0),
        "latency_ms": round((perf_counter() - started_at) * 1000),
    }


def _relay_health(db: Any, account_id: str | None) -> dict[str, Any]:
    getter = getattr(db, "get_wechat_connection_health", None)
    if not callable(getter) or not account_id:
        return {}
    try:
        value = getter(str(account_id))
    except Exception:  # noqa: BLE001
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _record_relay_health(
    db: Any,
    account_id: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
    error: str | None = None,
    latency_ms: int | None = None,
) -> None:
    save = getattr(db, "upsert_wechat_connection_health", None)
    if not callable(save):
        return
    checked_at = datetime.now(timezone.utc)
    save(
        str(account_id),
        status=str(status),
        checked_at=checked_at.isoformat(timespec="microseconds"),
        expires_at=(checked_at + timedelta(minutes=5)).isoformat(
            timespec="microseconds"
        ),
        details=dict(details or {}),
        error=error,
        mode="relay",
        latency_ms=latency_ms,
    )


def _format_health_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "暂无"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def _relay_status(
    *,
    enabled: bool,
    configured: bool,
    health: dict[str, Any],
) -> tuple[str, str, str]:
    if not configured:
        return "尚未配置", "grey", "填写云中转信息并完成一次真实连接测试。"
    if not enabled:
        return "已停用", "grey", "当前使用本机网络直连微信官方接口。"
    status = str(health.get("status") or "").casefold()
    if status == "healthy":
        return "连接正常", "positive", "最近一次只读连接检查已通过。"
    if status == "unhealthy":
        return (
            "连接异常",
            "negative",
            str(health.get("error") or "最近一次连接检查失败，请重新检测。"),
        )
    if status == "stale":
        return "需要重新检测", "warning", "连接配置已变化，请重新检测。"
    return "等待检测", "warning", "配置已保存，请执行一次重新检测。"


def build_wechat_relay_panel(
    state: AppState,
    *,
    allow_test_account_configuration: bool = False,
) -> None:
    """Render the global fixed-egress WeChat API relay configuration."""

    reload_config = getattr(state, "reload_config", None)
    config = (
        reload_config() if callable(reload_config) else getattr(state, "config", {})
    ) or {}
    saved = public_wechat_relay_settings(state.db)
    get_setting = getattr(state.db, "get_setting", None)
    has_saved_record = (
        bool(get_setting("wechat_api_relay"))
        if callable(get_setting)
        else any(
            (
                saved.get("enabled"),
                saved.get("gateway_url"),
                saved.get("username"),
                saved.get("has_password"),
            )
        )
    )
    fallback = config.get("wechat_relay")
    if not has_saved_record and isinstance(fallback, dict):
        saved = {
            "enabled": bool(fallback.get("enabled", False)),
            "gateway_url": str(
                fallback.get("gateway_url") or fallback.get("base_url") or ""
            ),
            "username": str(fallback.get("username") or ""),
            "has_password": bool(fallback.get("password")),
        }
    account_options = _relay_account_options(state.db)
    test_account_public = (
        public_wechat_relay_test_account(state.db)
        if allow_test_account_configuration
        else {}
    )
    if test_account_public.get("app_id") and test_account_public.get("has_app_secret"):
        account_options = {
            RELAY_TEST_ACCOUNT_ID: (
                str(test_account_public.get("name") or "中转测试公众号")
                + "（后台测试专用）"
            ),
            **account_options,
        }
    default_account_id = next(
        (
            account_id
            for account_id, label in account_options.items()
            if "（已停用）" not in label
        ),
        next(iter(account_options), None),
    )
    configured = bool(
        saved.get("gateway_url") and saved.get("username") and saved.get("has_password")
    )
    panel_state = {
        "configured": configured,
        "enabled": bool(saved.get("enabled", False)),
    }
    refs: dict[str, Any] = {}

    def selected_account_id() -> str | None:
        account = refs.get("account")
        value = getattr(account, "value", None) if account is not None else None
        return str(value or default_account_id or "").strip() or None

    def selected_test_account(account_id: str) -> dict[str, str] | None:
        if account_id != RELAY_TEST_ACCOUNT_ID:
            return None
        return effective_wechat_relay_test_account(state.db)

    def run_selected_relay_test(
        account_id: str,
        relay_settings: dict[str, Any],
    ) -> dict[str, Any]:
        test_account = selected_test_account(account_id)
        if test_account is None:
            return _test_relay_connection(
                state,
                account_id=account_id,
                relay_settings=relay_settings,
            )
        return _test_relay_connection(
            state,
            account_id=account_id,
            relay_settings=relay_settings,
            test_account=test_account,
        )

    def open_advanced_settings() -> None:
        expansion = refs.get("advanced")
        if expansion is not None:
            expansion.value = True
            expansion.update()

    def refresh_overview() -> None:
        latest = public_wechat_relay_settings(state.db)
        panel_state["configured"] = bool(
            latest.get("gateway_url")
            and latest.get("username")
            and latest.get("has_password")
        )
        panel_state["enabled"] = bool(latest.get("enabled", False))
        if core.loop is not None:
            connection_overview.refresh()

    async def recheck_connection() -> None:
        button = refs.get("recheck_button")
        if button is not None:
            set_button_loading(button, True, "正在重新检测微信云中转…")
        account_id = selected_account_id()
        try:
            if not panel_state["configured"] or not panel_state["enabled"]:
                open_advanced_settings()
                raise ValueError("请先在高级设置中启用并保存微信云中转")
            if not account_id:
                open_advanced_settings()
                raise ValueError("请先在高级设置中选择一个公众号")
            fallback_settings = config.get("wechat_relay")
            effective = effective_wechat_relay_settings(
                state.db,
                fallback_settings if isinstance(fallback_settings, dict) else None,
            )
            result = await run.io_bound(
                lambda: run_selected_relay_test(account_id, effective)
            )
            await run.io_bound(
                lambda: _record_relay_health(
                    state.db,
                    account_id,
                    status="healthy",
                    latency_ms=int(result.get("latency_ms") or 0),
                    details={
                        "draft": {
                            "reachable": True,
                            "total_count": int(result.get("draft_count") or 0),
                        }
                    },
                )
            )
            ui.notify(
                f"重新检测成功：{result['account_name']}，"
                f"草稿箱共 {result['draft_count']} 条。",
                type="positive",
            )
        except Exception as exc:  # noqa: BLE001
            message = _friendly_relay_error(exc)
            if account_id and panel_state["configured"]:
                await run.io_bound(
                    lambda: _record_relay_health(
                        state.db,
                        account_id,
                        status="unhealthy",
                        error=message,
                    )
                )
            ui.notify(message, type="negative", timeout=15000)
        finally:
            if button is not None:
                set_button_loading(button, False)
            refresh_overview()

    @ui.refreshable
    def connection_overview() -> None:
        account_id = selected_account_id()
        health = _relay_health(state.db, account_id)
        status_text, status_color, explanation = _relay_status(
            enabled=bool(panel_state["enabled"]),
            configured=bool(panel_state["configured"]),
            health=health,
        )
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("微信公众号云中转").classes("text-h6 text-weight-bold")
                    ui.label(
                        "日常只需关注连接状态；地址和账号等技术配置收在高级设置中。"
                    ).classes("muted")
                ui.badge(status_text, color=status_color)

            ui.label(explanation).classes(
                "text-negative text-weight-bold"
                if status_color == "negative"
                else "muted"
            )
            with ui.grid(columns=3).classes("w-full gap-4 q-mt-sm"):
                with ui.column().classes("gap-0"):
                    ui.label("连接模式").classes("text-caption muted")
                    ui.label(
                        "云中转（固定出口）"
                        if panel_state["enabled"]
                        else (
                            "直连（云中转已停用）"
                            if panel_state["configured"]
                            else "尚未配置"
                        )
                    ).classes("text-weight-bold")
                with ui.column().classes("gap-0"):
                    ui.label("最近检查").classes("text-caption muted")
                    ui.label(_format_health_time(health.get("checked_at"))).classes(
                        "text-weight-bold"
                    )
                with ui.column().classes("gap-0"):
                    ui.label("最近成功写入").classes("text-caption muted")
                    ui.label(
                        _format_health_time(health.get("last_successful_write_at"))
                    ).classes("text-weight-bold")

            ui.label(
                f"固定出口 IP：{FIXED_EGRESS_IP}。"
                "请把这个 IP 加入每个公众号的开发者 IP 白名单。"
            ).classes("text-positive text-weight-bold q-mt-sm")
            ui.label(
                "只中转 api.weixin.qq.com 的令牌、素材、草稿等官方接口；"
                "文章抓取、AI 模型和飞书不会经过这里。"
            ).classes("muted")
            with ui.row().classes("items-center q-gutter-sm q-mt-sm"):
                if panel_state["configured"]:
                    refs["recheck_button"] = ui.button(
                        "重新检测",
                        icon="refresh",
                        on_click=recheck_connection,
                    ).props("outline color=teal-9 no-caps")
                else:
                    ui.button(
                        "开始配置",
                        icon="settings",
                        on_click=open_advanced_settings,
                    ).props("unelevated color=teal-9 no-caps")
                    ui.label("填写后会先真实检测，再保存启用。").classes(
                        "muted text-caption"
                    )

    connection_overview()
    # The merchant backend can change the shared relay while this page remains
    # open. Poll only the safe public view so the badge follows the backend
    # without ever reading the password into the UI process.
    ui.timer(5.0, refresh_overview)

    if allow_test_account_configuration:
        with ui.element("div").classes("card w-full"):
            ui.label("中转测试公众号").classes("text-h6 text-weight-bold")
            ui.label(
                "用于验证 access_token、固定 IP 白名单和草稿只读接口。"
                "该账号只供后台检测，不会出现在运营端发布账号列表中，也不需要绑定文章模型。"
            ).classes("muted")
            test_name_in = (
                ui.input(
                    "测试公众号名称",
                    value=str(test_account_public.get("name") or ""),
                    placeholder="例如：蓝血研究测试号",
                )
                .classes("w-full")
                .props("outlined stack-label")
            )
            test_app_id_in = (
                ui.input(
                    "测试公众号 AppID",
                    value=str(test_account_public.get("app_id") or ""),
                )
                .classes("w-full")
                .props("outlined stack-label autocomplete=off")
            )
            test_secret_suffix = (
                "（已保存，留空表示不修改）"
                if bool(test_account_public.get("has_app_secret"))
                else ""
            )
            test_app_secret_in = (
                ui.input(
                    "测试公众号 AppSecret" + test_secret_suffix,
                    password=True,
                    password_toggle_button=True,
                    placeholder="从微信公众号后台复制 AppSecret",
                )
                .classes("w-full")
                .props("outlined stack-label autocomplete=new-password")
            )

            async def save_test_account() -> None:
                set_button_loading(
                    save_test_account_button,
                    True,
                    "正在加密保存测试公众号…",
                )
                try:
                    result = await run.io_bound(
                        lambda: save_wechat_relay_test_account(
                            state.db,
                            name=str(test_name_in.value or ""),
                            app_id=str(test_app_id_in.value or ""),
                            app_secret=str(test_app_secret_in.value or "") or None,
                        )
                    )
                    test_account_public.update(result)
                    test_app_secret_in.value = ""
                    account_options[RELAY_TEST_ACCOUNT_ID] = (
                        str(result.get("name") or "中转测试公众号")
                        + "（后台测试专用）"
                    )
                    account = refs.get("account")
                    if account is not None:
                        account.set_options(
                            account_options,
                            value=RELAY_TEST_ACCOUNT_ID,
                        )
                    invalidate = getattr(
                        state.db,
                        "invalidate_wechat_connection_health",
                        None,
                    )
                    if callable(invalidate):
                        invalidate(RELAY_TEST_ACCOUNT_ID)
                    refresh_overview()
                    ui.notify(
                        "测试公众号已安全保存，可以进行中转真实检测",
                        type="positive",
                    )
                except Exception as exc:  # noqa: BLE001
                    ui.notify(str(exc), type="negative", timeout=10000)
                finally:
                    set_button_loading(save_test_account_button, False)

            save_test_account_button = ui.button(
                "保存测试公众号",
                icon="save",
                on_click=save_test_account,
            ).props("unelevated color=teal-9 no-caps")
            ui.label(
                "AppSecret 加密保存，页面、状态接口和日志均不会回显明文。"
            ).classes("text-positive text-caption")

    with ui.expansion(
        "高级设置",
        icon="tune",
        value=False,
    ).classes("card w-full") as advanced_expansion:
        refs["advanced"] = advanced_expansion
        ui.label("仅在首次配置、停用或更换云服务器凭证时需要展开这里。").classes(
            "muted"
        )
        enabled_in = ui.switch(
            "启用微信云中转",
            value=bool(saved.get("enabled", False)),
        )
        gateway_in = (
            ui.input(
                "网关地址",
                value=str(saved.get("gateway_url") or DEFAULT_GATEWAY_URL),
                placeholder=DEFAULT_GATEWAY_URL,
            )
            .classes("w-full")
            .props("outlined stack-label")
        )
        username_in = (
            ui.input(
                "中转用户名",
                value=str(saved.get("username") or ""),
                placeholder="填写云服务器配置的 Basic Auth 用户名",
            )
            .classes("w-full")
            .props("outlined stack-label autocomplete=username")
        )
        password_suffix = (
            "（已保存，留空表示不修改）" if bool(saved.get("has_password")) else ""
        )
        password_in = (
            ui.input(
                "中转密码" + password_suffix,
                password=True,
                password_toggle_button=True,
                placeholder="填写云服务器配置的 Basic Auth 密码",
            )
            .classes("w-full")
            .props("outlined stack-label autocomplete=new-password")
        )
        clear_password_in = ui.switch(
            "清除已保存的中转密码",
            value=False,
        )

        def sync_clear_password() -> None:
            clearing = bool(clear_password_in.value)
            password_in.set_enabled(not clearing)
            if clearing:
                password_in.value = ""

        clear_password_in.on_value_change(lambda _: sync_clear_password())
        sync_clear_password()

        account_in = (
            ui.select(
                options=account_options,
                value=default_account_id,
                label="选择一个公众号进行真实测试",
            )
            .classes("w-full")
            .props("outlined stack-label options-dense")
        )
        refs["account"] = account_in
        account_in.on_value_change(lambda _: refresh_overview())
        if not account_options:
            ui.label(
                (
                    "还没有可测试的公众号，请先在上方保存“中转测试公众号”。"
                    if allow_test_account_configuration
                    else "还没有可测试的公众号，请先到“设置 → 公众号”添加 AppID 和 AppSecret。"
                )
            ).classes("text-warning text-weight-bold")

        ui.label(
            "测试会先通过云中转获取 access_token，再只读查询一次草稿箱；"
            "不会新建、修改或删除草稿。测试通过后才保存并启用。"
        ).classes("muted text-caption")

        async def test_and_save() -> None:
            set_button_loading(
                test_save_btn,
                True,
                "正在通过固定出口测试微信公众号接口…",
            )
            account_id = str(account_in.value or "").strip()
            try:
                gateway_url = str(gateway_in.value or "").strip()
                username = str(username_in.value or "").strip()
                supplied_password = str(password_in.value or "")
                clear_password = bool(clear_password_in.value)
                enabled = bool(enabled_in.value)

                if not enabled:
                    await run.io_bound(
                        lambda: save_wechat_relay_settings(
                            state.db,
                            enabled=False,
                            gateway_url=gateway_url,
                            username=username,
                            password=supplied_password or None,
                            clear_password=clear_password,
                        )
                    )
                    password_in.value = ""
                    clear_password_in.value = False
                    sync_clear_password()
                    panel_state["enabled"] = False
                    panel_state["configured"] = bool(
                        gateway_url
                        and username
                        and (saved.get("has_password") or supplied_password)
                    )
                    refresh_overview()
                    ui.notify("微信云中转已停用并保存。", type="positive")
                    return

                if not account_id:
                    raise ValueError("请先选择一个公众号进行真实测试")
                fallback_settings = config.get("wechat_relay")
                current_effective = effective_wechat_relay_settings(
                    state.db,
                    fallback_settings if isinstance(fallback_settings, dict) else None,
                )
                password = (
                    ""
                    if clear_password
                    else supplied_password
                    or str(current_effective.get("password") or "")
                )
                temporary = {
                    "enabled": True,
                    "gateway_url": gateway_url,
                    "username": username,
                    "password": password,
                }
                result = await run.io_bound(
                    lambda: run_selected_relay_test(account_id, temporary)
                )
                await run.io_bound(
                    lambda: save_wechat_relay_settings(
                        state.db,
                        enabled=True,
                        gateway_url=gateway_url,
                        username=username,
                        password=supplied_password or None,
                        clear_password=clear_password,
                    )
                )
                await run.io_bound(
                    lambda: _record_relay_health(
                        state.db,
                        account_id,
                        status="healthy",
                        latency_ms=int(result.get("latency_ms") or 0),
                        details={
                            "draft": {
                                "reachable": True,
                                "total_count": int(result.get("draft_count") or 0),
                            }
                        },
                    )
                )
                password_in.value = ""
                clear_password_in.value = False
                sync_clear_password()
                saved.update(
                    {
                        "enabled": True,
                        "gateway_url": gateway_url,
                        "username": username,
                        "has_password": True,
                    }
                )
                panel_state["enabled"] = True
                panel_state["configured"] = True
                refresh_overview()
                ui.notify(
                    f"云中转测试成功并已启用：{result['account_name']}，"
                    f"草稿箱共 {result['draft_count']} 条。",
                    type="positive",
                    timeout=10000,
                )
            except Exception as exc:  # noqa: BLE001
                message = _friendly_relay_error(exc)
                if account_id:
                    await run.io_bound(
                        lambda: _record_relay_health(
                            state.db,
                            account_id,
                            status="unhealthy",
                            error=message,
                        )
                    )
                refresh_overview()
                ui.notify(
                    message,
                    type="negative",
                    timeout=15000,
                )
            finally:
                set_button_loading(test_save_btn, False)

        test_save_btn = ui.button(
            "测试并保存",
            icon="verified_user",
            on_click=test_and_save,
        ).props("unelevated color=teal-9 no-caps")
        ui.label(
            "中转密码使用 Windows 当前用户加密保存，页面和状态接口不会回显明文。"
        ).classes("text-positive text-caption q-mt-sm")
