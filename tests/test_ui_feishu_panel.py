from __future__ import annotations

import json
from typing import Any

from nicegui import ui

from app.ui.panels import feishu


class _FakeState:
    db = object()
    config: dict[str, Any] = {}

    def __init__(self, models: dict[str, str] | None = None) -> None:
        self.models = dict(models or {})
        self.model_option_calls: list[bool] = []
        self.reload_count = 0

    def reload_config(self) -> None:
        self.reload_count += 1

    def model_options(self, *, include_default: bool = True) -> dict[str, str]:
        self.model_option_calls.append(include_default)
        return dict(self.models)


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


def test_feishu_panel_renders_when_no_agent_models_exist(
    monkeypatch: Any,
) -> None:
    """A clean install must render before the first model or account is added."""

    state, snapshot = _render(monkeypatch)

    assert state.reload_count == 1
    assert state.model_option_calls == [False]
    assert "请先在“模型管理 → 文章模型”中添加并启用模型" in snapshot
    assert "尚无可用公众号" in snapshot


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
        "关闭并重新打开本应用",
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
