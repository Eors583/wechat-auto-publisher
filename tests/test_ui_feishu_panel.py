from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any

from nicegui import ui

from app.ui.panels import feishu


class _FakeState:
    db = object()
    config: dict[str, Any] = {}

    def __init__(self, models: dict[str, str] | None = None) -> None:
        self.models = dict(models or {})
        self.model_option_calls: list[bool] = []
        self.model_registrations: list[dict[str, Any]] = []
        self.reload_count = 0

    def reload_config(self) -> None:
        self.reload_count += 1

    def model_options(self, *, include_default: bool = True) -> dict[str, str]:
        self.model_option_calls.append(include_default)
        return dict(self.models)

    def register_model_select(self, select: Any, **kwargs: Any) -> Any:
        self.model_registrations.append({"select": select, **kwargs})
        return select


class _FakeOnboardingService:
    def __init__(
        self,
        readiness: dict[str, Any],
        pairing: dict[str, Any] | None = None,
    ) -> None:
        self._readiness = dict(readiness)
        self._pairing = dict(pairing or {"status": "none"})

    def readiness(self) -> dict[str, Any]:
        return dict(self._readiness)

    def feishu_pairing_status(self) -> dict[str, Any]:
        return dict(self._pairing)


def _render(
    monkeypatch: Any,
    *,
    models: dict[str, str] | None = None,
    accounts: list[dict[str, Any]] | None = None,
    saved: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    pairing: dict[str, Any] | None = None,
    after_render: Callable[[], None] | None = None,
) -> tuple[_FakeState, str]:
    state = _FakeState(models)
    service = _FakeOnboardingService(
        readiness
        or {
            "feishu_saved": False,
            "feishu_ready": False,
            "feishu_runtime_status": "stopped",
        },
        pairing,
    )
    monkeypatch.setattr(
        feishu,
        "OnboardingService",
        lambda _db, _config: service,
    )
    monkeypatch.setattr(
        feishu,
        "public_feishu_settings",
        lambda _db: dict(saved or {}),
    )
    monkeypatch.setattr(
        feishu,
        "public_accounts",
        lambda _db, *, enabled_only=True: list(accounts or []),
    )
    monkeypatch.setattr(
        feishu,
        "get_runtime",
        lambda _db: dict(runtime or {"status": "stopped"}),
    )
    try:
        feishu.build_feishu_panel(state)
        if after_render is not None:
            after_render()
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()
    return state, snapshot


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


def _select_value(snapshot: str, label: str) -> Any:
    return next(
        item.get("value")
        for item in json.loads(snapshot)
        if item.get("type") == "Select"
        and item.get("props", {}).get("label") == label
    )


def test_feishu_panel_renders_when_no_agent_models_exist(
    monkeypatch: Any,
) -> None:
    """A clean install must render before the first model or account is added."""

    state, snapshot = _render(monkeypatch)

    assert state.reload_count == 1
    assert state.model_option_calls == [False]
    assert "请先在“模型管理 → 文章模型”中添加并启用模型" in snapshot
    assert "尚无可用公众号" in snapshot
    assert "默认飞书智能体模型（从已有文本模型选择）" in snapshot
    assert "刷新已有文本模型" in snapshot
    assert "未启用（enabled=false）" in snapshot
    assert "一键验证并保存" in snapshot
    assert "enabled=true" in snapshot
    assert "立即重启飞书服务" in snapshot


def test_feishu_account_catalog_keeps_enabled_unbound_accounts_visible(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        feishu,
        "public_accounts",
        lambda _db: [
            {
                "id": "ready",
                "name": "已就绪公众号",
                "enabled": True,
                "has_model": True,
            },
            {
                "id": "unbound",
                "name": "待绑定模型公众号",
                "enabled": True,
                "has_model": False,
            },
            {
                "id": "disabled",
                "name": "已停用公众号",
                "enabled": False,
                "has_model": True,
            },
        ],
    )

    options, disabled, unbound = feishu._feishu_account_catalog(object())

    assert options == {
        "ready": "已就绪公众号",
        "unbound": "待绑定模型公众号 · 尚未绑定文章模型",
    }
    assert disabled == ["已停用公众号"]
    assert unbound == ["待绑定模型公众号"]


def test_feishu_account_selector_refreshes_without_leaving_page(
    monkeypatch: Any,
) -> None:
    accounts = [
        {
            "id": "account-1",
            "name": "公众号A",
            "enabled": True,
            "has_model": True,
        }
    ]
    timer_callbacks: list[Callable[[], None]] = []

    def capture_timer(
        _interval: float,
        callback: Callable[[], None],
        **_kwargs: Any,
    ) -> object:
        timer_callbacks.append(callback)
        return object()

    monkeypatch.setattr(feishu, "client_timer", capture_timer)

    def add_account_and_refresh() -> None:
        accounts.append(
            {
                "id": "account-2",
                "name": "公众号B",
                "enabled": True,
                "has_model": True,
            }
        )
        timer_callbacks[0]()

    _, snapshot = _render(
        monkeypatch,
        models={"model-1": "运营文本模型"},
        accounts=accounts,
        saved={
            "agent_model_id": "model-1",
            "default_account_ids": ["account-1"],
        },
        after_render=add_account_and_refresh,
    )

    assert "已实时载入 2 个已启用公众号" in snapshot
    assert "公众号B" in snapshot
    assert _select_value(
        snapshot,
        "机器人默认生成到哪些公众号？",
    ) == ["account-1"]


def test_invalid_saved_agent_model_is_not_silently_replaced(
    monkeypatch: Any,
) -> None:
    _, snapshot = _render(
        monkeypatch,
        models={
            "config:moonshot": "Kimi 配置模型",
            "model-1": "运营文本模型",
        },
        accounts=[{"id": "account-1", "name": "蓝血研究"}],
        saved={
            "enabled": False,
            "agent_model_id": "removed-model",
            "default_account_ids": ["account-1"],
        },
    )

    assert _select_value(
        snapshot,
        "默认飞书智能体模型（从已有文本模型选择）",
    ) is None
    assert "原模型已停用/删除" in snapshot
    assert "config:moonshot" not in inspect.getsource(
        feishu.build_feishu_panel
    )


def test_feishu_tutorial_is_inline_and_follows_real_connection_order(
    monkeypatch: Any,
) -> None:
    secret = "must-never-render-feishu-secret"
    state, snapshot = _render(
        monkeypatch,
        models={"model-1": "运营文本模型"},
        accounts=[{"id": "account-1", "name": "蓝血研究"}],
        saved={
            "enabled": True,
            "app_id": "cli_public_id",
            "has_app_secret": True,
            "agent_model_id": "model-1",
            "default_account_ids": ["account-1"],
            "allow_all": False,
            "allowed_open_ids": [],
            "allowed_chat_ids": [],
            "app_secret": secret,
            "verification_token": "private-verification-token",
            "encrypt_key": "private-encrypt-key",
        },
        readiness={
            "feishu_saved": True,
            "feishu_ready": False,
            "feishu_runtime_status": "connecting",
        },
        runtime={
            "status": "connecting",
            "app_id": "cli_public_id",
            "started_at": "2026-07-24T12:00:00+00:00",
            "last_message_at": "",
            "last_reply_at": "",
        },
    )

    headings = [
        "创建企业自建应用并复制凭证",
        "在本页验证并保存",
        "重启飞书服务",
        "开通权限并设置长连接事件",
        "创建版本并发布",
        "生成并发送一次性绑定口令",
    ]
    positions = [snapshot.index(item) for item in headings]
    assert positions == sorted(positions)
    assert "默认采用一次性口令绑定，不需要查 Open ID" in snapshot
    assert "im.message.receive_v1" in snapshot
    assert feishu.PERMISSION_CODES in snapshot
    assert "高风险：开启后" in snapshot
    assert "当前使用长连接，这两项不需要填写" in snapshot
    assert "服务已启动，等待测试消息" in snapshot
    assert "已启用（enabled=true）" in snapshot
    assert "不用退出桌面应用" in snapshot
    assert "立即重启飞书服务" in snapshot
    assert "刷新接入状态" in snapshot
    assert "本次真实授权消息已收到并成功回复" not in snapshot
    assert secret not in snapshot
    assert "private-verification-token" not in snapshot
    assert "private-encrypt-key" not in snapshot
    assert state.model_option_calls == [False]


def test_feishu_panel_only_marks_complete_after_current_authorized_reply(
    monkeypatch: Any,
) -> None:
    _, snapshot = _render(
        monkeypatch,
        models={"model-1": "运营文本模型"},
        accounts=[{"id": "account-1", "name": "蓝血研究"}],
        saved={
            "enabled": True,
            "app_id": "cli_public_id",
            "has_app_secret": True,
            "agent_model_id": "model-1",
            "default_account_ids": ["account-1"],
            "allowed_open_ids": ["ou_bound"],
        },
        readiness={
            "feishu_saved": True,
            "feishu_ready": True,
            "feishu_runtime_status": "running",
        },
        runtime={
            "status": "running",
            "app_id": "cli_public_id",
            "started_at": "2026-07-24T12:00:00+00:00",
            "last_message_at": "2026-07-24T12:01:00+00:00",
            "last_reply_at": "2026-07-24T12:01:01+00:00",
            "last_open_id": "ou_bound",
        },
        pairing={"status": "used", "bound_open_id": "ou_bound"},
    )

    assert "接入完成" in snapshot
    assert "本次启动后已收到授权用户消息，并已成功回复" in snapshot
    assert "本次真实授权消息已收到并成功回复" in snapshot


def test_restart_button_calls_runtime_control_and_uses_io_bound(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    callbacks: dict[str, Callable[[], Any]] = {}

    def restart() -> dict[str, Any]:
        calls.append("restart")
        return {"ok": True, "message": "飞书服务已重新启动"}

    async def io_bound(callback: Callable[[], Any]) -> Any:
        calls.append("io_bound")
        return callback()

    monkeypatch.setattr(feishu, "restart_api_service", restart)
    monkeypatch.setattr(feishu.run, "io_bound", io_bound)
    monkeypatch.setattr(
        feishu,
        "set_button_loading",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        feishu.ui,
        "notify",
        lambda *_args, **_kwargs: None,
    )
    original_button = feishu.ui.button

    def capture_button(
        text: str,
        *args: Any,
        on_click: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if text == "立即重启飞书服务" and on_click is not None:
            callbacks["restart"] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    monkeypatch.setattr(feishu.ui, "button", capture_button)

    def trigger_restart() -> None:
        callback = callbacks["restart"]
        for cell in callback.__closure__ or ():
            value = cell.cell_contents
            if callable(getattr(value, "refresh", None)):
                monkeypatch.setattr(value, "refresh", lambda: None)
        asyncio.run(callback())

    _, snapshot = _render(
        monkeypatch,
        models={"model-1": "运营文本模型"},
        accounts=[{"id": "account-1", "name": "蓝血研究"}],
        saved={
            "enabled": True,
            "app_id": "cli_public_id",
            "has_app_secret": True,
            "agent_model_id": "model-1",
            "default_account_ids": ["account-1"],
        },
        readiness={
            "feishu_saved": True,
            "feishu_ready": False,
            "feishu_runtime_status": "connecting",
        },
        runtime={"status": "connecting"},
        after_render=trigger_restart,
    )

    assert calls == ["io_bound", "restart"]
    assert "立即重启飞书服务" in snapshot
