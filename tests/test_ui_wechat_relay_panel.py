from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from nicegui import ui

from app.ui.panels import wechat_relay


class _FakeState:
    db = object()

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.reload_count = 0
        self.config = dict(config or {})

    def reload_config(self) -> dict[str, Any]:
        self.reload_count += 1
        return self.config


class _HealthDB:
    def __init__(self, health: dict[str, Any] | None = None) -> None:
        self.health = dict(health or {})

    def get_setting(self, _key: str) -> str:
        return "{}"

    def get_wechat_connection_health(self, _account_id: str) -> dict[str, Any] | None:
        return dict(self.health) if self.health else None


def _snapshot() -> str:
    values: list[dict[str, Any]] = []
    for element in ui.context.client.elements.values():
        values.append(
            {
                "type": type(element).__name__,
                "text": getattr(element, "text", None),
                "value": getattr(element, "value", None),
                "props": getattr(element, "_props", {}),
            }
        )
    return json.dumps(values, ensure_ascii=False, default=str)


def _input_value(label_prefix: str) -> Any:
    return next(
        element.value
        for element in ui.context.client.elements.values()
        if type(element).__name__ in {"Input", "Select"}
        and str(getattr(element, "_props", {}).get("label") or "").startswith(
            label_prefix
        )
    )


def test_relay_panel_shows_fixed_ip_defaults_and_never_reveals_password(
    monkeypatch: Any,
) -> None:
    secret = "relay-private-password"
    state = _FakeState()
    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_settings",
        lambda _db: {
            "enabled": True,
            "gateway_url": "",
            "username": "relay-user",
            "has_password": True,
        },
    )
    monkeypatch.setattr(
        wechat_relay,
        "public_accounts",
        lambda _db: [
            {
                "id": "account-1",
                "name": "蓝血研究",
                "enabled": True,
                "has_app_secret": True,
            }
        ],
    )
    monkeypatch.setattr(
        wechat_relay,
        "effective_wechat_relay_settings",
        lambda _db, _fallback=None: {"password": secret},
    )

    try:
        wechat_relay.build_wechat_relay_panel(state)
        snapshot = _snapshot()
        gateway_value = _input_value("网关地址")
        password_value = _input_value("中转密码")
    finally:
        ui.context.client.remove_all_elements()

    assert state.reload_count == 1
    assert "微信公众号云中转" in snapshot
    assert wechat_relay.FIXED_EGRESS_IP in snapshot
    assert wechat_relay.DEFAULT_GATEWAY_URL in snapshot
    assert "选择一个公众号进行真实测试" in snapshot
    assert "测试并保存" in snapshot
    assert "清除已保存的中转密码" in snapshot
    assert "只读查询一次草稿箱" in snapshot
    assert secret not in snapshot
    assert gateway_value == wechat_relay.DEFAULT_GATEWAY_URL
    assert password_value == ""


def test_relay_panel_keeps_daily_status_basic_and_technical_fields_collapsed(
    monkeypatch: Any,
) -> None:
    state = _FakeState()
    state.db = _HealthDB(
        {
            "status": "healthy",
            "checked_at": "2026-07-28T08:00:00+08:00",
            "last_successful_write_at": "2026-07-28T08:05:00+08:00",
        }
    )
    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_settings",
        lambda _db: {
            "enabled": True,
            "gateway_url": wechat_relay.DEFAULT_GATEWAY_URL,
            "username": "relay-user",
            "has_password": True,
        },
    )
    monkeypatch.setattr(
        wechat_relay,
        "public_accounts",
        lambda _db: [
            {
                "id": "account-1",
                "name": "蓝血研究",
                "enabled": True,
                "has_app_secret": True,
            }
        ],
    )

    try:
        wechat_relay.build_wechat_relay_panel(state)
        snapshot = _snapshot()
        expansion = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Expansion"
            and getattr(element, "text", None) == "高级设置"
        )
        gateway = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Input"
            and getattr(element, "_props", {}).get("label") == "网关地址"
        )
    finally:
        ui.context.client.remove_all_elements()

    assert "连接模式" in snapshot
    assert "云中转（固定出口）" in snapshot
    assert "连接正常" in snapshot
    assert "最近检查" in snapshot
    assert "最近成功写入" in snapshot
    assert "重新检测" in snapshot
    assert expansion.value is False
    assert gateway.parent_slot.parent is expansion


def test_unconfigured_relay_offers_start_configuration_and_opens_advanced(
    monkeypatch: Any,
) -> None:
    state = _FakeState()
    callbacks: dict[str, Callable[[], Any]] = {}
    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_settings",
        lambda _db: {
            "enabled": False,
            "gateway_url": "",
            "username": "",
            "has_password": False,
        },
    )
    monkeypatch.setattr(wechat_relay, "public_accounts", lambda _db: [])
    original_button = wechat_relay.ui.button

    def capture_button(
        text: str,
        *args: Any,
        on_click: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if text == "开始配置" and on_click is not None:
            callbacks["start"] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    monkeypatch.setattr(wechat_relay.ui, "button", capture_button)
    try:
        wechat_relay.build_wechat_relay_panel(state)
        expansion = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Expansion"
            and getattr(element, "text", None) == "高级设置"
        )
        assert expansion.value is False
        callbacks["start"]()
        assert expansion.value is True
    finally:
        ui.context.client.remove_all_elements()


def test_relay_test_uses_saved_password_then_saves_without_exposing_it(
    monkeypatch: Any,
) -> None:
    state = _FakeState()
    callbacks: dict[str, Callable[[], Any]] = {}
    calls: list[Any] = []
    secret = "stored-private-password"

    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_settings",
        lambda _db: {
            "enabled": True,
            "gateway_url": wechat_relay.DEFAULT_GATEWAY_URL,
            "username": "relay-user",
            "has_password": True,
        },
    )
    monkeypatch.setattr(
        wechat_relay,
        "public_accounts",
        lambda _db: [
            {
                "id": "account-1",
                "name": "蓝血研究",
                "enabled": True,
                "has_app_secret": True,
            }
        ],
    )
    monkeypatch.setattr(
        wechat_relay,
        "effective_wechat_relay_settings",
        lambda _db, _fallback=None: {"password": secret},
    )

    def test_connection(
        _state: Any,
        *,
        account_id: str,
        relay_settings: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(("test", account_id, dict(relay_settings)))
        return {"account_name": "蓝血研究", "draft_count": 8}

    def save_settings(_db: Any, **kwargs: Any) -> None:
        calls.append(("save", dict(kwargs)))

    async def io_bound(callback: Callable[[], Any]) -> Any:
        calls.append("io_bound")
        return callback()

    monkeypatch.setattr(
        wechat_relay,
        "_test_relay_connection",
        test_connection,
    )
    monkeypatch.setattr(
        wechat_relay,
        "save_wechat_relay_settings",
        save_settings,
    )
    monkeypatch.setattr(wechat_relay.run, "io_bound", io_bound)
    monkeypatch.setattr(
        wechat_relay,
        "set_button_loading",
        lambda _button, loading, *_args: calls.append(("loading", loading)),
    )
    monkeypatch.setattr(
        wechat_relay.ui,
        "notify",
        lambda message, **_kwargs: calls.append(("notify", message)),
    )
    original_button = wechat_relay.ui.button

    def capture_button(
        text: str,
        *args: Any,
        on_click: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if text == "测试并保存" and on_click is not None:
            callbacks["test_save"] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    monkeypatch.setattr(wechat_relay.ui, "button", capture_button)
    try:
        wechat_relay.build_wechat_relay_panel(state)
        asyncio.run(callbacks["test_save"]())
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    test_call = next(
        item for item in calls if isinstance(item, tuple) and item[0] == "test"
    )
    save_call = next(
        item for item in calls if isinstance(item, tuple) and item[0] == "save"
    )
    assert test_call[1] == "account-1"
    assert test_call[2]["password"] == secret
    assert save_call[1] == {
        "enabled": True,
        "gateway_url": wechat_relay.DEFAULT_GATEWAY_URL,
        "username": "relay-user",
        "password": None,
        "clear_password": False,
    }
    assert ("loading", True) in calls
    assert ("loading", False) in calls
    assert any(
        item[0] == "notify" and "测试成功并已启用" in item[1]
        for item in calls
        if isinstance(item, tuple)
    )
    assert secret not in snapshot


def test_relay_panel_displays_yaml_or_env_fallback_before_first_database_save(
    monkeypatch: Any,
) -> None:
    secret = "config-private-password"
    state = _FakeState(
        {
            "wechat_relay": {
                "enabled": True,
                "gateway_url": "https://relay.example.test/wechat",
                "username": "config-user",
                "password": secret,
            }
        }
    )
    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_settings",
        lambda _db: {
            "enabled": False,
            "gateway_url": "",
            "username": "",
            "has_password": False,
        },
    )
    monkeypatch.setattr(wechat_relay, "public_accounts", lambda _db: [])

    try:
        wechat_relay.build_wechat_relay_panel(state)
        snapshot = _snapshot()
        gateway_value = _input_value("网关地址")
        username_value = _input_value("中转用户名")
    finally:
        ui.context.client.remove_all_elements()

    assert gateway_value == "https://relay.example.test/wechat"
    assert username_value == "config-user"
    assert "中转密码（已保存，留空表示不修改）" in snapshot
    assert secret not in snapshot


def test_relay_error_messages_are_actionable() -> None:
    assert "白名单" in wechat_relay._friendly_relay_error(
        RuntimeError("WeChat API error 40164: invalid ip")
    )
    assert "AppSecret" in wechat_relay._friendly_relay_error(
        RuntimeError("WeChat API error 40125: invalid appsecret")
    )
    assert "用户名或密码" in wechat_relay._friendly_relay_error(
        RuntimeError("HTTP 401 Unauthorized")
    )
    assert "用户名或密码" in wechat_relay._friendly_relay_error(
        RuntimeError("WeChat gateway HTTP 403")
    )
    assert "Nginx" in wechat_relay._friendly_relay_error(
        RuntimeError("WeChat gateway HTTP 502")
    )
    assert "防火墙" in wechat_relay._friendly_relay_error(TimeoutError("timed out"))


def test_admin_relay_panel_can_configure_isolated_test_account(
    monkeypatch: Any,
) -> None:
    state = _FakeState()
    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_settings",
        lambda _db: {
            "enabled": False,
            "gateway_url": "",
            "username": "",
            "has_password": False,
        },
    )
    monkeypatch.setattr(wechat_relay, "public_accounts", lambda _db: [])
    monkeypatch.setattr(
        wechat_relay,
        "public_wechat_relay_test_account",
        lambda _db: {
            "name": "平台测试号",
            "app_id": "wx-public-test",
            "has_app_secret": True,
        },
    )

    try:
        wechat_relay.build_wechat_relay_panel(
            state,
            allow_test_account_configuration=True,
        )
        snapshot = _snapshot()
        secret_value = _input_value("测试公众号 AppSecret")
    finally:
        ui.context.client.remove_all_elements()

    assert "中转测试公众号" in snapshot
    assert "该账号只供后台检测" in snapshot
    assert "测试公众号名称" in snapshot
    assert "测试公众号 AppID" in snapshot
    assert "测试公众号 AppSecret（已保存，留空表示不修改）" in snapshot
    assert "保存测试公众号" in snapshot
    assert "平台测试号（后台测试专用）" in snapshot
    assert secret_value == ""


def test_relay_probe_accepts_admin_test_account_without_model_binding(
    monkeypatch: Any,
) -> None:
    state = _FakeState()
    calls: list[Any] = []

    class FakeAuth:
        def get_access_token(self, *, force_refresh: bool = False) -> str:
            calls.append(("token", force_refresh))
            return "token"

    monkeypatch.setattr(wechat_relay, "load_config", lambda: {"ai": {}})
    monkeypatch.setattr(
        wechat_relay,
        "build_wechat_auth",
        lambda _config, _db, app_id, app_secret, **kwargs: (
            calls.append(("auth", app_id, app_secret, kwargs)) or FakeAuth()
        ),
    )
    monkeypatch.setattr(
        wechat_relay,
        "build_wechat_client",
        lambda _config, _db, app_id, app_secret, **kwargs: (
            calls.append(("client", app_id, app_secret, kwargs)) or object()
        ),
    )
    monkeypatch.setattr(
        wechat_relay,
        "batchget_drafts",
        lambda _client, **_kwargs: {"total_count": 3},
    )

    result = wechat_relay._test_relay_connection(
        state,
        account_id=wechat_relay.RELAY_TEST_ACCOUNT_ID,
        relay_settings={
            "enabled": True,
            "gateway_url": "https://relay.example.test/wechat",
            "username": "relay-user",
            "password": "relay-password-123456",
        },
        test_account={
            "name": "后台测试号",
            "app_id": "wx-test",
            "app_secret": "wechat-secret",
        },
    )

    assert result["account_name"] == "后台测试号"
    assert result["draft_count"] == 3
    assert any(item[:3] == ("auth", "wx-test", "wechat-secret") for item in calls)
    assert any(item[:3] == ("client", "wx-test", "wechat-secret") for item in calls)
