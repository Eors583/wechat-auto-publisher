from __future__ import annotations

import inspect
import json
from typing import Any

from nicegui import ui

from app.ui import desktop
from app.ui.panels import feishu
from app.ui.styles import APP_CSS


class _FakeState:
    db = object()
    config: dict[str, Any] = {}

    def __init__(self, models: dict[str, str] | None = None) -> None:
        self.models = dict(models or {})
        self.reload_count = 0
        self.model_registrations: list[dict[str, Any]] = []

    def reload_config(self) -> None:
        self.reload_count += 1

    def model_options(self, *, include_default: bool = True) -> dict[str, str]:
        assert include_default is False
        return dict(self.models)

    def register_model_select(self, select: Any, **kwargs: Any) -> Any:
        self.model_registrations.append({"select": select, **kwargs})
        return select


class _FakeIntegrationService:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = dict(settings)

    def public(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.settings)


class _FakeOnboarding:
    pass


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


def _render(
    monkeypatch: Any,
    *,
    settings: dict[str, Any] | None = None,
    accounts: list[dict[str, Any]] | None = None,
    models: dict[str, str] | None = None,
) -> tuple[_FakeState, str]:
    state = _FakeState(models)
    service = _FakeIntegrationService(
        settings
        or {
            "configured": False,
            "status": "unconfigured",
            "runtime": {},
            "account_ids": [],
        }
    )
    monkeypatch.setattr(
        feishu,
        "FeishuIntegrationService",
        lambda _db, _config: service,
    )
    monkeypatch.setattr(
        feishu,
        "OnboardingService",
        lambda _db, _config: _FakeOnboarding(),
    )
    monkeypatch.setattr(
        feishu,
        "public_accounts",
        lambda _db: list(accounts or []),
    )
    monkeypatch.setattr(feishu, "client_timer", lambda *_args, **_kwargs: object())
    try:
        feishu.build_feishu_panel(state)
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()
    return state, snapshot


def test_feishu_panel_is_a_per_user_webhook_configuration(monkeypatch: Any) -> None:
    state, snapshot = _render(monkeypatch)

    assert state.reload_count == 1
    assert "我的飞书机器人" in snapshot
    assert "用户独立 · Webhook" in snapshot
    assert "App ID" in snapshot
    assert "App Secret" in snapshot
    assert "Verification Token" in snapshot
    assert "Encrypt Key" in snapshot
    assert "机器人允许操作的公众号" in snapshot
    assert "唯一默认公众号" in snapshot
    assert "保存并验证我的机器人" in snapshot
    assert "飞书事件回调地址" in snapshot
    assert "生成 10 分钟配对码" in snapshot


def test_feishu_panel_never_renders_global_allowlists_or_restart(
    monkeypatch: Any,
) -> None:
    _, snapshot = _render(monkeypatch)

    for removed in (
        "允许应用可用范围内的所有成员",
        "允许的用户 Open ID",
        "允许的群聊 Chat ID",
        "立即重启飞书服务",
        "长连接模式",
    ):
        assert removed not in snapshot
    assert "群聊消息" not in feishu.PERMISSION_CODES
    assert "im:message.group_at_msg:readonly" not in feishu.PERMISSION_CODES


def test_saved_credentials_are_masked_and_binding_is_status_only(
    monkeypatch: Any,
) -> None:
    secret = "must-never-render"
    _, snapshot = _render(
        monkeypatch,
        models={"model-1": "运营文本模型"},
        accounts=[
            {
                "id": "account-1",
                "name": "蓝桥研究",
                "enabled": True,
                "has_model": True,
            }
        ],
        settings={
            "configured": True,
            "enabled": True,
            "status": "active",
            "app_id": "cli_public",
            "app_secret": secret,
            "has_app_secret": True,
            "has_verification_token": True,
            "has_encrypt_key": True,
            "agent_model_id": "model-1",
            "account_ids": ["account-1"],
            "default_account_id": "account-1",
            "callback_path": "/api/feishu/events/random-callback",
            "callback_url": (
                "https://publisher.bluebloodlab.cn/"
                "api/feishu/events/random-callback"
            ),
            "callback_ready": True,
            "callback_error": "",
            "bound": True,
            "bound_open_id_masked": "ou_a…0001",
            "pairing": {"status": "used"},
            "runtime": {"callback_verified_at": "2026-08-20T12:00:00Z"},
        },
    )

    assert "运行正常" in snapshot
    assert "已收到回调" in snapshot
    assert "ou_a…0001" in snapshot
    assert (
        "https://publisher.bluebloodlab.cn/api/feishu/events/random-callback"
        in snapshot
    )
    assert "已加密保存；留空保持不变" in snapshot
    assert "解除绑定" in snapshot
    assert "停用我的机器人" in snapshot
    assert secret not in snapshot


def test_missing_public_https_base_is_explicit_and_cannot_be_copied(
    monkeypatch: Any,
) -> None:
    _, snapshot = _render(
        monkeypatch,
        settings={
            "configured": True,
            "enabled": True,
            "status": "waiting_pairing",
            "app_id": "cli_public",
            "account_ids": [],
            "callback_path": "/api/feishu/events/random-callback",
            "callback_url": "",
            "callback_ready": False,
            "callback_error": (
                "请将 WECHAT_PUBLISHER_PUBLIC_UI_URL 配置为飞书可访问的"
                "公网 HTTPS 基址，然后刷新本页。"
            ),
            "runtime": {},
        },
    )

    assert "尚未配置公网 HTTPS 回调基址" in snapshot
    assert "WECHAT_PUBLISHER_PUBLIC_UI_URL" in snapshot
    assert "https://你的系统域名" not in snapshot
    copy_button = next(
        item
        for item in json.loads(snapshot)
        if item.get("text") == "复制回调地址"
    )
    assert "disable" in copy_button["props"]


def test_disabled_robot_has_a_visible_safe_reenable_action(
    monkeypatch: Any,
) -> None:
    _, snapshot = _render(
        monkeypatch,
        models={"model-1": "运营文本模型"},
        accounts=[
            {
                "id": "account-1",
                "name": "蓝桥研究",
                "enabled": True,
                "has_model": True,
            }
        ],
        settings={
            "configured": True,
            "enabled": False,
            "status": "disabled",
            "app_id": "cli_public",
            "has_app_secret": True,
            "has_verification_token": True,
            "has_encrypt_key": True,
            "agent_model_id": "model-1",
            "account_ids": ["account-1"],
            "default_account_id": "account-1",
            "bound": False,
            "pairing": {"status": "none"},
            "runtime": {},
        },
    )

    assert "启用我的机器人" in snapshot
    assert "停用我的机器人" not in snapshot
    assert "解除绑定" not in snapshot


def test_account_catalog_only_exposes_the_current_users_enabled_accounts(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        feishu,
        "public_accounts",
        lambda _db: [
            {"id": "ready", "name": "已就绪", "enabled": True, "has_model": True},
            {"id": "unbound", "name": "待模型", "enabled": True, "has_model": False},
            {"id": "disabled", "name": "已停用", "enabled": False},
        ],
    )

    options, disabled, unbound = feishu._feishu_account_catalog(object())

    assert options == {"ready": "已就绪", "unbound": "待模型 · 尚未绑定文章模型"}
    assert disabled == ["已停用"]
    assert unbound == ["待模型"]


def test_feishu_layout_uses_shared_responsive_classes() -> None:
    assert ".feishu-config-grid" in APP_CSS
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in APP_CSS
    assert "@media (max-width: 600px)" in APP_CSS
    assert ".feishu-break-anywhere" in APP_CSS
    scroll_css = APP_CSS[APP_CSS.index(".ops-feishu-page .ops-page-host {") :]
    assert "overflow-x: hidden" in scroll_css[:300]
    assert "overflow-y: auto" in scroll_css[:300]
    assert "scrollbar-gutter: stable" in scroll_css[:300]


def test_every_authenticated_user_has_a_real_feishu_settings_entry() -> None:
    source = inspect.getsource(desktop.create_desktop_app)

    assert 'tab_feishu = ui.tab("飞书机器人", icon="forum")' in source
    assert 'aria-label="飞书机器人" title="飞书机器人"' in source
    assert '"我的飞书机器人"' in source
    assert "on_click=lambda: tabs.set_value(tab_feishu)" in source
    assert "def mount_feishu() -> None:" in source
    assert "build_feishu_panel(page_state)" in source
    assert 'str(tab_feishu.props["name"]): mount_feishu' in source
    assert 'str(query_params.get("view") or "").strip().lower() == "feishu"' in source
