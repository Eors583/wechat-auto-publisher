from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui

from app.db import Database
from app.services import onboarding
from app.services.onboarding import TEXT_MODEL_PRESETS
from app.ui.panels import onboarding_wizard


class _FakeDb:
    def get_setting(self, _key: str) -> str | None:
        return None


class _FakeConfiguration:
    def __init__(self) -> None:
        self.model = {
            "id": "model-1",
            "name": "DeepSeek",
            "model": "deepseek-chat",
            "provider_type": "openai_compatible",
            "api_base": "https://api.deepseek.com",
        }
        self.account = {
            "id": "account-1",
            "name": "蓝血研究",
            "app_id": "wx-blue",
            "model_id": "model-1",
        }

    def get_model(self, _model_id: str) -> dict[str, Any]:
        return dict(self.model)

    def get_account(self, _account_id: str) -> dict[str, Any]:
        return dict(self.account)


class _FakeState:
    def __init__(self, root: Path) -> None:
        self.db = _FakeDb()
        self.config = {
            "_root": str(root),
            "_db_path": str(root / "wizard.db"),
        }
        self.remembered: list[str] = []
        self.refresh_count = 0

    def refresh_account_selects(self) -> None:
        self.refresh_count += 1

    def remember_account_ids(self, account_ids: list[str]) -> None:
        self.remembered = list(account_ids)


class _FakeService:
    def __init__(
        self,
        *,
        step: str = "welcome",
        writer_ready: bool = False,
        content_ready: bool = False,
        draft_ready: bool = False,
    ) -> None:
        self.configuration = _FakeConfiguration()
        self.calls: list[tuple[Any, ...]] = []
        self.check_reports: list[dict[str, Any]] = []
        self.check_error: Exception | None = None
        self.guide_state = {
            "wizard_version": 2,
            "mode": "full",
            "current_step": step,
            "completed_steps": [],
            "selected_model_id": "model-1" if writer_ready else "",
            "selected_account_ids": (["account-1"] if content_ready else []),
            "connection_mode": "relay",
            "force_open": True,
            "completed_at": None,
            "updated_at": "2026-07-29T00:00:00+00:00",
        }
        self.status_state = {
            "writer_ready": writer_ready,
            "model_tested": writer_ready,
            "content_ready": content_ready,
            "core_ready": content_ready,
            "draft_ready": draft_ready,
            "tested_model_ids": ["model-1"] if writer_ready else [],
            "draft_ready_account_ids": (["account-1"] if draft_ready else []),
            "account_checks": [],
            "guide": self.guide_state,
            "wizard_required": not draft_ready,
        }

    def status(self, *, refresh_wechat: bool = False) -> dict[str, Any]:
        self.calls.append(("status", refresh_wechat))
        return {
            **self.status_state,
            "guide": dict(self.guide_state),
        }

    def auto_check(self) -> dict[str, Any]:
        self.calls.append(("auto_check",))
        return {
            **self.status_state,
            "guide": dict(self.guide_state),
        }

    def guide(self) -> dict[str, Any]:
        return dict(self.guide_state)

    def model_presets(self) -> list[dict[str, Any]]:
        return [dict(item) for item in TEXT_MODEL_PRESETS.values()]

    def save_progress(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("save_progress", dict(kwargs)))
        self.guide_state.update(kwargs)
        self.status_state["guide"] = self.guide_state
        return dict(self.guide_state)

    def save_text_model(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("save_text_model", dict(kwargs)))
        return dict(self.configuration.model)

    def test_text_model(self, model_id: str) -> dict[str, Any]:
        self.calls.append(("test_text_model", model_id))
        self.status_state.update(
            writer_ready=True,
            model_tested=True,
            tested_model_ids=[model_id],
        )
        return {"ok": True, "model_id": model_id}

    def check_accounts(
        self,
        account_ids: list[str] | None = None,
        *,
        force: bool = True,
    ) -> list[dict[str, Any]]:
        self.calls.append(("check_accounts", account_ids, force))
        if self.check_error is not None:
            raise self.check_error
        return [dict(item) for item in self.check_reports]


def _snapshot() -> str:
    values: list[dict[str, Any]] = []
    for element in ui.context.client.elements.values():
        values.append(
            {
                "type": type(element).__name__,
                "text": getattr(element, "text", None),
                "value": getattr(element, "value", None),
                "props": getattr(element, "_props", {}),
                "options": getattr(element, "options", None),
                "visible": getattr(element, "visible", None),
            }
        )
    return json.dumps(values, ensure_ascii=False, default=str)


def _render(
    tmp_path: Path,
    service: _FakeService,
) -> tuple[_FakeState, str]:
    state = _FakeState(tmp_path)
    onboarding_wizard.build_onboarding_wizard(
        state,  # type: ignore[arg-type]
        service=service,  # type: ignore[arg-type]
        initial_status=service.status(),
    )
    return state, _snapshot()


def test_welcome_is_full_first_run_path_and_keeps_packaging_brand(
    tmp_path: Path,
) -> None:
    service = _FakeService()

    try:
        _state, snapshot = _render(tmp_path, service)
        primary_buttons = [
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Button"
            and "unelevated" in str(getattr(element, "_props", {}))
        ]
    finally:
        ui.context.client.remove_all_elements()

    assert "公众号改写助手" in snapshot
    assert "欢迎使用" in snapshot
    assert "开始配置并连接公众号" in snapshot
    assert "我已经配置过，自动检查" in snapshot
    assert "仅写入草稿" in snapshot
    assert "先体验文章生成" not in snapshot
    assert "飞书接入" not in snapshot
    assert len(primary_buttons) == 1


def test_empty_database_welcome_render_never_runs_external_preflight(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "empty.db"),
        "ai": {},
    }
    state = _FakeState(tmp_path)
    state.db = Database(config["_db_path"])  # type: ignore[assignment]
    state.config = config
    service = onboarding.OnboardingService(state.db, config)

    def unexpected_preflight(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("initial page must not call external WeChat APIs")

    monkeypatch.setattr(
        onboarding,
        "preflight_accounts",
        unexpected_preflight,
    )
    try:
        status = service.status()
        onboarding_wizard.build_onboarding_wizard(
            state,  # type: ignore[arg-type]
            service=service,
            initial_status=status,
        )
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    assert status["wizard_required"] is True
    assert status["current_step"] == "welcome"
    assert "公众号改写助手" in snapshot
    assert "开始配置并连接公众号" in snapshot


def test_welcome_auto_check_explicitly_runs_real_service_recheck(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    service = _FakeService()
    service.status_state.update(
        model_retest_results=[
            {
                "model_id": "model-1",
                "ok": False,
                "message": "API Key 无效，请重新填写",
            }
        ],
        model_retest_failed_count=1,
    )
    callbacks: dict[str, Any] = {}
    original_button = onboarding_wizard.ui.button

    def capture_button(
        text: str,
        *args: Any,
        on_click: Any = None,
        **kwargs: Any,
    ) -> Any:
        if text == "我已经配置过，自动检查":
            callbacks["auto_check"] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    async def inline_io(callback: Any) -> Any:
        return callback()

    def synchronous_refreshable(function: Any) -> Any:
        target_content = ui.context.client.content

        def refresh(*_args: Any, **_kwargs: Any) -> Any:
            with target_content:
                return function()

        function.refresh = refresh
        return function

    monkeypatch.setattr(onboarding_wizard.ui, "button", capture_button)
    monkeypatch.setattr(onboarding_wizard.run, "io_bound", inline_io)
    monkeypatch.setattr(
        onboarding_wizard,
        "set_button_loading",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        onboarding_wizard.ui,
        "refreshable",
        synchronous_refreshable,
    )

    try:
        _render(tmp_path, service)
        assert not any(call[0] == "auto_check" for call in service.calls)
        asyncio.run(callbacks["auto_check"]())
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    assert any(call[0] == "auto_check" for call in service.calls)
    assert any(
        call[0] == "save_progress" and call[1].get("current_step") == "ai"
        for call in service.calls
    )
    assert "文章模型复测未通过" in snapshot
    assert "API Key 无效" in snapshot


def test_ai_step_exposes_only_six_guided_providers_and_folds_advanced(
    tmp_path: Path,
) -> None:
    service = _FakeService(step="ai")

    try:
        _state, snapshot = _render(tmp_path, service)
        provider = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Select"
            and getattr(element, "_props", {}).get("label") == "AI 服务商"
        )
        advanced = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Expansion"
            and getattr(element, "text", None) == "高级设置"
        )
    finally:
        ui.context.client.remove_all_elements()

    assert list(provider.options) == list(onboarding_wizard.BASIC_PROVIDER_IDS)
    assert "DeepSeek" in snapshot
    assert "Kimi" in snapshot
    assert "通义千问" in snapshot
    assert "智谱" in snapshot
    assert "Gemini" in snapshot
    assert "Manus" in snapshot
    assert "其他厂商" not in snapshot
    assert "API Key" in snapshot
    assert "推荐模型" in snapshot
    assert "API Base" in snapshot
    assert advanced.value is False


def test_real_model_test_is_required_before_progressing(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    service = _FakeService(step="ai")
    callbacks: dict[str, Any] = {}
    original_button = onboarding_wizard.ui.button

    def capture_button(
        text: str,
        *args: Any,
        on_click: Any = None,
        **kwargs: Any,
    ) -> Any:
        if text == "测试并继续":
            callbacks["test"] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    async def inline_io(callback: Any) -> Any:
        return callback()

    def synchronous_refreshable(function: Any) -> Any:
        target_content = ui.context.client.content

        def refresh(*_args: Any, **_kwargs: Any) -> Any:
            with target_content:
                return function()

        function.refresh = refresh
        return function

    monkeypatch.setattr(onboarding_wizard.ui, "button", capture_button)
    monkeypatch.setattr(onboarding_wizard.run, "io_bound", inline_io)
    monkeypatch.setattr(
        onboarding_wizard,
        "set_button_loading",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        onboarding_wizard.ui,
        "refreshable",
        synchronous_refreshable,
    )

    try:
        _render(tmp_path, service)
        api_key_input = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Input"
            and getattr(element, "_props", {}).get("label") == "API Key"
        )
        api_key_input.value = "sk-ui-private"
        asyncio.run(callbacks["test"]())
    finally:
        ui.context.client.remove_all_elements()

    call_names = [str(item[0]) for item in service.calls]
    assert "save_text_model" in call_names
    assert "test_text_model" in call_names
    progress = next(
        item[1]
        for item in service.calls
        if item[0] == "save_progress" and item[1].get("current_step") == "account"
    )
    assert progress["selected_model_id"] == "model-1"
    assert "sk-ui-private" not in repr(progress)


def test_account_step_auto_binds_model_without_repeated_model_selector(
    tmp_path: Path,
) -> None:
    service = _FakeService(
        step="account",
        writer_ready=True,
    )

    try:
        _state, snapshot = _render(tmp_path, service)
        model_selectors = [
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Select"
            and "模型" in str(getattr(element, "_props", {}).get("label") or "")
        ]
    finally:
        ui.context.client.remove_all_elements()

    assert "连接第一个公众号" in snapshot
    assert "公众号名称" in snapshot
    assert "AppID" in snapshot
    assert "AppSecret" in snapshot
    assert "已绑定文章 AI" in snapshot
    assert "默认创作方案" in snapshot
    assert model_selectors == []


def test_wechat_step_is_read_only_and_hides_gateway_basic_auth(
    tmp_path: Path,
) -> None:
    service = _FakeService(
        step="wechat",
        writer_ready=True,
        content_ready=True,
    )

    try:
        _state, snapshot = _render(tmp_path, service)
    finally:
        ui.context.client.remove_all_elements()

    source = inspect.getsource(onboarding_wizard._WizardController._render_wechat)
    assert "云端稳定连接（推荐）" in snapshot
    assert "本机直接连接" in snapshot
    assert "固定出口 IP" in snapshot
    assert "中转接入码" in snapshot
    assert "不会创建、修改或删除任何草稿" in snapshot
    assert "网关地址" not in snapshot
    assert "Basic Auth" not in snapshot
    assert "check_accounts(" in source
    assert "force=True" in source
    assert "add_draft" not in source
    assert "update_draft" not in source
    assert "delete_draft" not in source


def test_wechat_step_uses_service_derived_legacy_connection_mode(
    tmp_path: Path,
) -> None:
    service = _FakeService(
        step="wechat",
        writer_ready=True,
        content_ready=True,
    )
    service.guide_state["connection_mode"] = "direct"

    try:
        _state, snapshot = _render(tmp_path, service)
        mode = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Toggle"
        )
    finally:
        ui.context.client.remove_all_elements()

    assert mode.value == "direct"
    assert "本机直接连接" in snapshot
    assert "中转接入码" not in snapshot


def _trigger_wechat_error(
    tmp_path: Path,
    monkeypatch: Any,
    error: Exception | None,
    *,
    reports: list[dict[str, Any]] | None = None,
) -> tuple[_FakeService, dict[str, Any], str]:
    service = _FakeService(
        step="wechat",
        writer_ready=True,
        content_ready=True,
    )
    service.check_error = error
    service.check_reports = [dict(item) for item in reports or []]
    callbacks: dict[str, Any] = {}
    original_button = onboarding_wizard.ui.button

    def capture_button(
        text: str | None = None,
        *args: Any,
        on_click: Any = None,
        **kwargs: Any,
    ) -> Any:
        if text and on_click is not None:
            callbacks[str(text)] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    async def inline_io(callback: Any) -> Any:
        return callback()

    def synchronous_refreshable(function: Any) -> Any:
        target_content = ui.context.client.content

        def refresh(*_args: Any, **_kwargs: Any) -> Any:
            with target_content:
                return function()

        function.refresh = refresh
        return function

    monkeypatch.setattr(onboarding_wizard.ui, "button", capture_button)
    monkeypatch.setattr(onboarding_wizard.run, "io_bound", inline_io)
    monkeypatch.setattr(
        onboarding_wizard,
        "set_button_loading",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        onboarding_wizard._WizardController,
        "_save_connection_mode",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        onboarding_wizard.ui,
        "refreshable",
        synchronous_refreshable,
    )
    _render(tmp_path, service)
    asyncio.run(callbacks["我已添加白名单，开始检测"]())
    return service, callbacks, _snapshot()


def test_failed_preflight_report_uses_the_same_credential_repair_action(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    try:
        _service, callbacks, snapshot = _trigger_wechat_error(
            tmp_path,
            monkeypatch,
            None,
            reports=[
                {
                    "account_id": "account-1",
                    "model_tested": True,
                    "can_write": False,
                    "draft_ready": False,
                    "checks": [
                        {
                            "key": "wechat",
                            "name": "公众号凭证",
                            "ok": False,
                            "message": "公众号 AppSecret 无效，请更新公众号凭证",
                        }
                    ],
                }
            ],
        )
    finally:
        ui.context.client.remove_all_elements()

    assert "AppSecret 无效或已重置" in snapshot
    assert "修改公众号凭证" in callbacks


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("WeChat API error 40013: invalid appid"),
        RuntimeError("WeChat API error 40125: invalid appsecret"),
    ],
)
def test_invalid_wechat_credentials_return_to_account_without_losing_identity(
    tmp_path: Path,
    monkeypatch: Any,
    error: Exception,
) -> None:
    try:
        service, callbacks, issue_snapshot = _trigger_wechat_error(
            tmp_path,
            monkeypatch,
            error,
        )
        assert "修改公众号凭证" in issue_snapshot
        asyncio.run(callbacks["修改公众号凭证"]())
        repaired_snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    progress = [
        item[1]
        for item in service.calls
        if item[0] == "save_progress" and item[1].get("current_step") == "account"
    ][-1]
    assert progress["selected_model_id"] == "model-1"
    assert progress["selected_account_ids"] == ["account-1"]
    assert "连接第一个公众号" in repaired_snapshot
    assert "蓝血研究" in repaired_snapshot
    assert "wx-blue" in repaired_snapshot


def test_ip_whitelist_issue_keeps_copy_and_retry_actions(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    try:
        _service, callbacks, snapshot = _trigger_wechat_error(
            tmp_path,
            monkeypatch,
            RuntimeError("WeChat API error 40164: invalid ip"),
        )
    finally:
        ui.context.client.remove_all_elements()

    assert "出口 IP 未加入白名单" in snapshot
    assert "复制固定出口 IP" in callbacks
    assert "重新检测" in callbacks
    assert "打开微信后台" in snapshot


@pytest.mark.parametrize("status_code", [502, 503, 504])
def test_relay_gateway_issue_can_switch_to_direct_in_place(
    tmp_path: Path,
    monkeypatch: Any,
    status_code: int,
) -> None:
    try:
        service, callbacks, snapshot = _trigger_wechat_error(
            tmp_path,
            monkeypatch,
            RuntimeError(f"WeChat gateway HTTP {status_code}"),
        )
        assert "切换本机直连" in snapshot
        asyncio.run(callbacks["切换本机直连"]())
        switched_snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    progress = [
        item[1]
        for item in service.calls
        if item[0] == "save_progress" and item[1].get("connection_mode") == "direct"
    ][-1]
    assert progress["current_step"] == "wechat"
    assert "本机直接连接" in switched_snapshot


def test_missing_draft_permission_links_to_help_and_wechat_console(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    try:
        _service, _callbacks, snapshot = _trigger_wechat_error(
            tmp_path,
            monkeypatch,
            RuntimeError("WeChat API error 48001: api unauthorized"),
        )
    finally:
        ui.context.client.remove_all_elements()

    assert "公众号没有草稿接口权限" in snapshot
    assert "查看草稿接口权限说明" in snapshot
    assert "打开微信后台" in snapshot
    assert "重新检测" in snapshot


def test_wechat_step_does_not_finish_for_a_different_ready_account(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    service = _FakeService(
        step="wechat",
        writer_ready=True,
        content_ready=True,
    )
    service.check_reports = [
        {
            "account_id": "account-1",
            "model_tested": True,
            "can_write": False,
            "draft_ready": False,
            "checks": [],
        },
        {
            "account_id": "other-ready",
            "model_tested": True,
            "can_write": True,
            "draft_ready": True,
            "checks": [],
        },
    ]
    callbacks: dict[str, Any] = {}
    original_button = onboarding_wizard.ui.button

    def capture_button(
        text: str,
        *args: Any,
        on_click: Any = None,
        **kwargs: Any,
    ) -> Any:
        if text == "我已添加白名单，开始检测":
            callbacks["check"] = on_click
        return original_button(
            text,
            *args,
            on_click=on_click,
            **kwargs,
        )

    async def inline_io(callback: Any) -> Any:
        return callback()

    def synchronous_refreshable(function: Any) -> Any:
        target_content = ui.context.client.content

        def refresh(*_args: Any, **_kwargs: Any) -> Any:
            with target_content:
                return function()

        function.refresh = refresh
        return function

    monkeypatch.setattr(onboarding_wizard.ui, "button", capture_button)
    monkeypatch.setattr(onboarding_wizard.run, "io_bound", inline_io)
    monkeypatch.setattr(
        onboarding_wizard,
        "set_button_loading",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        onboarding_wizard,
        "save_wechat_relay_settings",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        onboarding_wizard.ui,
        "refreshable",
        synchronous_refreshable,
    )

    try:
        _render(tmp_path, service)
        mode = next(
            element
            for element in ui.context.client.elements.values()
            if type(element).__name__ == "Toggle"
        )
        mode.value = "direct"
        asyncio.run(callbacks["check"]())
    finally:
        ui.context.client.remove_all_elements()

    progress = [
        item[1]
        for item in service.calls
        if item[0] == "save_progress" and item[1].get("connection_mode") == "direct"
    ][-1]
    assert progress["current_step"] == "wechat"
    assert "wechat" not in progress["completed_steps"]


def test_completion_and_settings_contract_preserve_existing_configuration(
    tmp_path: Path,
) -> None:
    service = _FakeService(
        step="complete",
        writer_ready=True,
        content_ready=True,
        draft_ready=True,
    )
    service.status_state["account_checks"] = [
        {
            "account_id": "account-1",
            "can_write": True,
            "checks": [
                {
                    "key": "draft",
                    "name": "草稿接口",
                    "ok": True,
                    "message": "草稿接口正常",
                },
                {
                    "key": "wechat",
                    "name": "公众号凭证",
                    "ok": True,
                    "message": "公众号凭证有效",
                },
                {
                    "key": "material",
                    "name": "封面素材",
                    "ok": False,
                    "message": "缺少封面图片",
                },
            ],
        }
    ]

    try:
        _state, snapshot = _render(tmp_path, service)
    finally:
        ui.context.client.remove_all_elements()

    settings_source = inspect.getsource(onboarding_wizard.build_onboarding_settings)
    assert "开始第一篇文章" in snapshot
    assert "系统默认方案" in snapshot
    assert "缺少封面图片" in snapshot
    assert "公众号凭证有效" not in snapshot
    assert '"text": "!"' in snapshot
    assert "不会自动创建文章任务" in snapshot
    assert 'service.restart(mode="full")' in settings_source
    assert "delete_" not in settings_source
    assert "配置检查" in settings_source
    assert "重新运行新手向导" in settings_source
    assert "_run_auto_check(service)" in settings_source
    assert "if ui_alive():" in settings_source
    welcome_source = inspect.getsource(
        onboarding_wizard._WizardController._render_welcome
    )
    assert "if self._ui_alive():" in welcome_source


def test_wizard_error_boundaries_never_render_named_credentials() -> None:
    raw = RuntimeError(
        "HTTP 500 app_secret=topsecret api_key=plainsecret "
        "token=toksecret access_token=accesssecret"
    )

    rendered = (
        onboarding_wizard._friendly_model_error(raw)  # noqa: SLF001
        + onboarding_wizard._friendly_wechat_error(raw)  # noqa: SLF001
    )

    for secret in (
        "topsecret",
        "plainsecret",
        "toksecret",
        "accesssecret",
    ):
        assert secret not in rendered


def test_gate_allows_review_deep_link_and_any_draft_ready_account() -> None:
    broken_and_ready = {
        "draft_ready": True,
        "wizard_required": False,
        "account_checks": [
            {"account_id": "ready", "can_write": True},
            {"account_id": "broken", "can_write": False},
        ],
    }

    assert onboarding_wizard.should_show_onboarding(broken_and_ready) is False
    assert (
        onboarding_wizard.should_show_onboarding(
            {"draft_ready": False, "wizard_required": True},
            review_deep_link=True,
        )
        is False
    )
    assert (
        onboarding_wizard.should_show_onboarding(
            {
                "draft_ready": True,
                "wizard_required": True,
                "guide": {"force_open": True},
            }
        )
        is True
    )
    stale_completed = {
        "content_ready": True,
        "draft_ready": False,
        "wizard_required": False,
        "wechat_refresh_needed": True,
        "guide": {
            "completed_at": "2026-07-29T00:00:00+00:00",
            "force_open": False,
        },
        "account_checks": [
            {
                "account_id": "account-1",
                "checked": False,
                "can_write": False,
            }
        ],
    }
    assert onboarding_wizard.should_show_onboarding(stale_completed) is False
    assert onboarding_wizard.configuration_health_needs_refresh(stale_completed) is True
