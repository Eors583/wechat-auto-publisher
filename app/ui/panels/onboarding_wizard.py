from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from app.services.failures import sanitize_failure_text
from app.services.onboarding import OnboardingService
from app.services.onboarding_errors import onboarding_wechat_issue
from app.services.wechat_relay_settings import (
    public_wechat_relay_connection_info,
    public_wechat_relay_settings,
    save_wechat_relay_access_code,
    save_wechat_relay_settings,
)
from app.ui.state import AppState, set_button_loading

WIZARD_STEPS = ("welcome", "ai", "account", "wechat", "complete")
BASIC_PROVIDER_IDS = (
    "deepseek",
    "moonshot",
    "qwen",
    "zhipu",
    "gemini",
    "manus",
)
STEP_LABELS = {
    "welcome": "准备",
    "ai": "连接 AI",
    "account": "连接公众号",
    "wechat": "发布检查",
    "complete": "完成",
}

ONBOARDING_CSS = """
.onboarding-screen {
  min-height: 100vh;
  width: 100%;
  padding: 24px 20px 48px;
  background:
    radial-gradient(720px 360px at 0% 0%, rgba(16,163,127,.15), transparent 66%),
    radial-gradient(600px 300px at 100% 12%, rgba(8,122,99,.10), transparent 62%),
    linear-gradient(180deg, #f8fbfa 0%, #edf3f1 100%);
}
.onboarding-shell {
  width: min(100%, 940px);
  margin: 0 auto;
}
.onboarding-progress {
  position: sticky;
  top: 12px;
  z-index: 10;
  padding: 14px 18px;
  border: 1px solid rgba(214,226,221,.92);
  border-radius: 18px;
  background: rgba(255,255,255,.94);
  box-shadow: 0 8px 28px rgba(21,53,44,.08);
  backdrop-filter: blur(14px);
}
.onboarding-progress-track {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  width: 100%;
}
.onboarding-progress-step {
  min-width: 0;
  padding-top: 8px;
  border-top: 3px solid #dfe8e4;
  color: #7a8983;
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}
.onboarding-progress-step.active {
  border-color: #087a63;
  color: #075f4e;
}
.onboarding-progress-step.done {
  border-color: #67b6a2;
  color: #377665;
}
.onboarding-card {
  margin-top: 20px;
  padding: clamp(24px, 5vw, 48px);
  border: 1px solid #dfe8e4;
  border-radius: 26px;
  background: rgba(255,255,255,.98);
  box-shadow: 0 20px 55px rgba(20,50,42,.09);
}
.onboarding-kicker {
  color: #087a63;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: .12em;
}
.onboarding-title {
  color: #16221e;
  font-size: clamp(25px, 4vw, 38px);
  font-weight: 900;
  letter-spacing: -.025em;
  line-height: 1.18;
}
.onboarding-lead {
  max-width: 720px;
  color: #60716a;
  font-size: 15px;
  line-height: 1.75;
}
.onboarding-form {
  width: min(100%, 700px);
  margin-top: 22px;
}
.onboarding-primary {
  min-height: 48px;
  min-width: min(100%, 280px);
  padding-left: 24px;
  padding-right: 24px;
  border-radius: 12px;
  font-weight: 800;
}
.onboarding-note {
  padding: 14px 16px;
  border: 1px solid #d8e9e3;
  border-radius: 14px;
  background: #eff9f6;
  color: #315f52;
  line-height: 1.65;
}
.onboarding-error {
  padding: 14px 16px;
  border: 1px solid #efc8cc;
  border-radius: 14px;
  background: #fff0f1;
  color: #912a35;
  line-height: 1.6;
}
.onboarding-choice {
  width: 100%;
  padding: 16px;
  border: 1px solid #dbe5e1;
  border-radius: 16px;
  background: #fff;
}
.onboarding-choice.selected {
  border-color: #159475;
  background: #eff9f6;
  box-shadow: 0 0 0 2px rgba(21,148,117,.08);
}
.onboarding-check {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 10px;
  width: 100%;
  padding: 12px 14px;
  border-bottom: 1px solid #edf1ef;
}
.onboarding-check:last-child { border-bottom: 0; }
.onboarding-check-icon {
  font-weight: 900;
  text-align: center;
}
.onboarding-summary {
  overflow: hidden;
  width: 100%;
  border: 1px solid #dfe8e4;
  border-radius: 16px;
}
@media (max-width: 640px) {
  .onboarding-screen { padding: 12px 10px 30px; }
  .onboarding-progress { top: 6px; padding: 11px 10px; }
  .onboarding-progress-step { font-size: 10px; }
  .onboarding-card { margin-top: 12px; padding: 22px 17px; border-radius: 20px; }
  .onboarding-primary { width: 100%; }
}
"""


def should_show_onboarding(
    status: dict[str, Any] | None,
    *,
    review_deep_link: bool = False,
) -> bool:
    """Return whether first-run setup should replace the normal workspace."""

    if review_deep_link:
        return False
    value = dict(status or {})
    guide = value.get("guide")
    if isinstance(guide, dict) and bool(guide.get("force_open")):
        return True
    if bool(value.get("draft_ready")):
        return False
    # A completed setup with an expired local health cache should not block
    # startup. The workspace refreshes it in the background and redirects to
    # repair only after a real failed check.
    if (
        isinstance(guide, dict)
        and bool(guide.get("completed_at"))
        and bool(value.get("content_ready"))
        and configuration_health_needs_refresh(value)
    ):
        return False
    if "wizard_required" in value:
        return bool(value.get("wizard_required"))
    return not bool(value.get("draft_ready"))


def configuration_health_needs_refresh(
    status: dict[str, Any] | None,
) -> bool:
    value = dict(status or {})
    if bool(value.get("wechat_refresh_needed")):
        return True
    checks = [
        dict(item)
        for item in list(value.get("account_checks") or [])
        if isinstance(item, dict)
    ]
    return bool(checks) and not any(bool(item.get("checked")) for item in checks)


def build_onboarding_wizard(
    state: AppState,
    *,
    service: OnboardingService | None = None,
    initial_status: dict[str, Any] | None = None,
    on_completed: Callable[[str | None], Any] | None = None,
) -> None:
    """Render the full-screen, resume-safe first-run configuration guide."""

    service = service or OnboardingService(state.db, state.config)
    ui.add_css(ONBOARDING_CSS)
    controller = _WizardController(
        state,
        service,
        initial_status=initial_status,
        on_completed=on_completed,
    )
    controller.render()


def build_configuration_health_banner(status: dict[str, Any] | None) -> None:
    """Render the non-blocking workbench entry for configuration health."""

    value = dict(status or {})
    if not value:
        return
    checks = [
        dict(item)
        for item in list(value.get("account_checks") or [])
        if isinstance(item, dict)
    ]
    unhealthy_count = sum(
        1
        for item in checks
        if bool(item.get("checked", True)) and not bool(item.get("can_write"))
    )
    refreshing = configuration_health_needs_refresh(value)
    refresh_failed = bool(value.get("health_refresh_failed"))
    label = (
        "配置状态待修复"
        if refresh_failed
        else (
            "正在刷新配置状态"
            if refreshing
            else (
                f"有 {unhealthy_count} 个公众号待修复"
                if unhealthy_count
                else "配置正常"
            )
        )
    )
    color = (
        "orange-9"
        if refresh_failed
        else "orange-9"
        if unhealthy_count
        else "blue-grey-7"
        if refreshing
        else "teal-9"
    )
    with ui.row().classes("w-full items-center justify-end q-mb-sm"):
        ui.link(
            label,
            "/?view=config",
        ).props(f"color={color} no-caps").classes("text-weight-bold")


def build_onboarding_settings(
    state: AppState,
    *,
    service: OnboardingService | None = None,
) -> None:
    """Expose safe configuration checking and guide restart in Settings."""

    service = service or OnboardingService(state.db, state.config)
    status_state: dict[str, Any] = {}
    owner_client = ui.context.client

    def ui_alive() -> bool:
        return not bool(getattr(owner_client, "is_deleted", False))

    @ui.refreshable
    def overview() -> None:
        status = status_state or dict(service.status() or {})
        with ui.element("div").classes("card w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("配置检查").classes("text-h6 text-weight-bold")
                    ui.label(
                        "检查文章 AI、公众号凭证、白名单、封面素材和草稿接口。"
                    ).classes("muted")
                ui.badge(
                    "可以写入草稿"
                    if status.get("draft_ready")
                    else (
                        "可以生成文章"
                        if status.get("content_ready")
                        else "需要完成配置"
                    ),
                    color=("positive" if status.get("draft_ready") else "warning"),
                )
            failed_model_retests = [
                dict(item)
                for item in list(status.get("model_retest_results") or [])
                if isinstance(item, dict) and not bool(item.get("ok"))
            ]
            if failed_model_retests:
                ui.label(
                    f"有 {len(failed_model_retests)} 个文章模型复测未通过"
                ).classes("text-negative text-weight-bold q-mt-sm")
                for result in failed_model_retests[:3]:
                    ui.label(
                        sanitize_failure_text(result.get("message") or "模型连接失败")
                    ).classes("text-caption text-negative")
            with ui.row().classes("items-center q-gutter-sm q-mt-md"):

                async def check_configuration() -> None:
                    set_button_loading(
                        check_btn,
                        True,
                        "正在执行只读配置检查…",
                    )
                    try:
                        checked = await run.io_bound(lambda: _run_auto_check(service))
                        status_state.clear()
                        status_state.update(dict(checked or {}))
                        if ui_alive():
                            overview.refresh()
                            failed_count = int(
                                dict(checked or {}).get("model_retest_failed_count")
                                or 0
                            )
                            ui.notify(
                                (
                                    f"配置检查已完成，{failed_count} 个文章模型需要修复"
                                    if failed_count
                                    else "配置检查已完成"
                                ),
                                type="warning" if failed_count else "positive",
                            )
                    except Exception as exc:  # noqa: BLE001
                        if ui_alive():
                            ui.notify(
                                sanitize_failure_text(exc),
                                type="negative",
                                timeout=12000,
                            )
                    finally:
                        if ui_alive():
                            set_button_loading(check_btn, False)

                check_btn = ui.button(
                    "配置检查",
                    icon="fact_check",
                    on_click=check_configuration,
                ).props("outline color=teal-9 no-caps")

                async def restart_guide() -> None:
                    set_button_loading(
                        restart_btn,
                        True,
                        "正在重新打开新手向导…",
                    )
                    try:
                        await run.io_bound(lambda: service.restart(mode="full"))
                        if ui_alive():
                            ui.navigate.to("/?view=onboarding")
                    except Exception as exc:  # noqa: BLE001
                        if ui_alive():
                            ui.notify(
                                sanitize_failure_text(exc),
                                type="negative",
                                timeout=12000,
                            )
                    finally:
                        if ui_alive():
                            set_button_loading(restart_btn, False)

                restart_btn = ui.button(
                    "重新运行新手向导",
                    icon="restart_alt",
                    on_click=restart_guide,
                ).props("flat color=teal-9 no-caps")
            ui.label(
                "重新运行只重置向导进度，不会删除或覆盖已有模型、公众号和文章。"
            ).classes("text-caption text-positive q-mt-sm")

    overview()


class _WizardController:
    def __init__(
        self,
        state: AppState,
        service: OnboardingService,
        *,
        initial_status: dict[str, Any] | None,
        on_completed: Callable[[str | None], Any] | None,
    ) -> None:
        self.state = state
        self.service = service
        self.on_completed = on_completed
        self._initial_status = dict(initial_status or {})
        self.form: dict[str, Any] = {
            "provider_id": "deepseek",
            "api_key": "",
            "api_base": "",
            "model": "",
            "display_name": "",
            "account_name": "",
            "app_id": "",
            "app_secret": "",
            "connection_mode": "",
            "relay_code": "",
        }
        self.last_reports: list[dict[str, Any]] = []
        self.wechat_issue: dict[str, Any] = {}
        self.step_override = ""
        self.inline_error = ""
        self.owner_client = ui.context.client

    def render(self) -> None:
        @ui.refreshable
        def content() -> None:
            status = self._status()
            guide = self._guide(status)
            self._hydrate_form(guide)
            step = self._effective_step(status, guide)
            with (
                ui.element("main").classes("onboarding-screen"),
                ui.element("div").classes("onboarding-shell"),
            ):
                self._render_progress(step)
                with ui.element("section").classes("onboarding-card"):
                    if step == "welcome":
                        self._render_welcome(status)
                    elif step == "ai":
                        self._render_ai(status, guide)
                    elif step == "account":
                        self._render_account(status, guide)
                    elif step == "wechat":
                        self._render_wechat(status, guide)
                    else:
                        self._render_complete(status, guide)

        self.refresh = content.refresh
        content()

    def _status(self, *, refresh_wechat: bool = False) -> dict[str, Any]:
        if self._initial_status and not refresh_wechat:
            value = self._initial_status
            self._initial_status = {}
            return dict(value)
        status_method = getattr(self.service, "status", None)
        if callable(status_method):
            return dict(status_method(refresh_wechat=refresh_wechat) or {})
        return dict(self.service.readiness() or {})

    def _guide(self, status: dict[str, Any]) -> dict[str, Any]:
        value = status.get("guide")
        if isinstance(value, dict):
            guide = dict(value)
            if not guide.get("connection_mode") and status.get("connection_mode"):
                guide["connection_mode"] = str(status["connection_mode"])
            return guide
        guide_method = getattr(self.service, "guide", None)
        return dict(guide_method() or {}) if callable(guide_method) else {}

    def _auto_check(self) -> dict[str, Any]:
        return _run_auto_check(self.service)

    def _effective_step(
        self,
        status: dict[str, Any],
        guide: dict[str, Any],
    ) -> str:
        step = str(guide.get("current_step") or "welcome").strip().casefold()
        if self.step_override in WIZARD_STEPS:
            return self.step_override
        if step not in WIZARD_STEPS:
            step = "welcome"
        if step == "welcome":
            return step
        if not bool(status.get("writer_ready", status.get("model_tested"))):
            return "ai"
        if not bool(status.get("content_ready", status.get("core_ready"))):
            return "account"
        if not bool(status.get("draft_ready")):
            return "wechat"
        return "complete"

    def _hydrate_form(self, guide: dict[str, Any]) -> None:
        if not self.form.get("connection_mode"):
            self.form["connection_mode"] = str(guide.get("connection_mode") or "relay")
        selected_model_id = str(guide.get("selected_model_id") or "")
        if selected_model_id and not self.form.get("model_id"):
            self.form["model_id"] = selected_model_id
            try:
                model = self.service.configuration.get_model(selected_model_id)
            except Exception:  # noqa: BLE001
                model = {}
            if model:
                self.form["provider_id"] = _infer_provider_id(
                    model,
                    self.service.model_presets(),
                )
                self.form["api_base"] = str(model.get("api_base") or "")
                self.form["model"] = str(model.get("model") or "")
                self.form["display_name"] = str(model.get("name") or "")
        selected_account_id = _first_selected_account_id(guide)
        if selected_account_id and not self.form.get("account_id"):
            self.form["account_id"] = selected_account_id
            try:
                account = self.service.configuration.get_account(selected_account_id)
            except Exception:  # noqa: BLE001
                account = {}
            if account:
                self.form["account_name"] = str(account.get("name") or "")
                self.form["app_id"] = str(account.get("app_id") or "")

    def _render_progress(self, step: str) -> None:
        active_index = WIZARD_STEPS.index(step)
        with ui.element("div").classes("onboarding-progress"):
            with ui.row().classes("w-full items-center justify-between q-mb-sm"):
                ui.label("首次配置").classes("text-weight-bold")
                ui.label("预计 5 分钟 · 仅写入草稿，不会自动群发").classes(
                    "text-caption text-grey-7"
                )
            labels = "".join(
                (
                    f'<div class="onboarding-progress-step '
                    f'{"done" if index < active_index else "active" if index == active_index else ""}">'
                    f"{STEP_LABELS[item]}</div>"
                )
                for index, item in enumerate(WIZARD_STEPS)
            )
            ui.html(
                f'<div class="onboarding-progress-track">{labels}</div>',
                sanitize=False,
            )

    def _heading(self, kicker: str, title: str, lead: str) -> None:
        ui.label(kicker).classes("onboarding-kicker")
        ui.label(title).classes("onboarding-title")
        ui.label(lead).classes("onboarding-lead")

    def _render_error(self) -> Any:
        safe_error = sanitize_failure_text(self.inline_error)
        label = ui.label(safe_error).classes("onboarding-error w-full")
        label.set_visibility(bool(self.inline_error))
        return label

    def _render_welcome(self, status: dict[str, Any]) -> None:
        del status
        ui.label("公众号改写助手").classes(
            "text-caption text-weight-bold text-teal-9 q-mb-xs"
        )
        self._heading(
            "欢迎使用",
            "公众号智能运营助手",
            "完成文章 AI 和公众号连接后，即可开始创作。"
            "系统只写入草稿箱，不会自动群发。",
        )
        with ui.column().classes("onboarding-form gap-3"):
            with ui.element("div").classes("onboarding-note"):
                ui.label("开始前请准备").classes("text-weight-bold")
                ui.label("• 一个 AI 平台 API Key")
                ui.label("• 公众号 AppID 和 AppSecret")
                ui.label("• 公众号开发配置权限")
            self._render_error()

            async def start() -> None:
                try:
                    await run.io_bound(
                        lambda: self.service.save_progress(
                            mode="full",
                            current_step="ai",
                            completed_steps=["welcome"],
                        )
                    )
                    self.inline_error = ""
                    if self._ui_alive():
                        self.refresh()
                except Exception as exc:  # noqa: BLE001
                    self._show_error(exc)

            start_btn = (
                ui.button(
                    "开始配置并连接公众号",
                    icon="arrow_forward",
                    on_click=start,
                )
                .props("unelevated color=teal-9 no-caps")
                .classes("onboarding-primary")
            )

            async def auto_check() -> None:
                set_button_loading(
                    auto_btn,
                    True,
                    "正在检查已有 AI、公众号和草稿接口配置…",
                )
                try:
                    checked = await run.io_bound(self._auto_check)
                    step = _next_required_step(checked)
                    completed = _completed_before(step)
                    await run.io_bound(
                        lambda: self.service.save_progress(
                            mode="full",
                            current_step=step,
                            completed_steps=completed,
                            selected_account_ids=_candidate_account_ids(checked),
                        )
                    )
                    failed_model_retests = [
                        dict(item)
                        for item in list(checked.get("model_retest_results") or [])
                        if isinstance(item, dict) and not bool(item.get("ok"))
                    ]
                    self.inline_error = (
                        "文章模型复测未通过："
                        + "；".join(
                            sanitize_failure_text(item.get("message") or "模型连接失败")
                            for item in failed_model_retests[:3]
                        )
                        if failed_model_retests
                        else ""
                    )
                    # Reload the local status so the just-saved guide step is
                    # used instead of the pre-check welcome snapshot.
                    self._initial_status = {}
                    if self._ui_alive():
                        self.refresh()
                except Exception as exc:  # noqa: BLE001
                    self._show_error(exc)
                finally:
                    if self._ui_alive():
                        set_button_loading(auto_btn, False)

            auto_btn = ui.button(
                "我已经配置过，自动检查",
                icon="fact_check",
                on_click=auto_check,
            ).props("flat color=teal-9 no-caps")
            start_btn.set_visibility(True)

    def _render_ai(
        self,
        status: dict[str, Any],
        guide: dict[str, Any],
    ) -> None:
        del status, guide
        self._heading(
            "第 1 步，共 3 步",
            "连接文章 AI",
            "AI 用于文章改写、标题生成和后续评审。保存并不代表可用，"
            "这里会真实调用一次推荐模型。",
        )
        presets = {
            str(item.get("id") or ""): dict(item)
            for item in self.service.model_presets()
            if str(item.get("id") or "") in BASIC_PROVIDER_IDS
        }
        provider_options = {
            provider_id: str(presets.get(provider_id, {}).get("label") or provider_id)
            for provider_id in BASIC_PROVIDER_IDS
            if provider_id in presets
        }
        provider_id = str(self.form.get("provider_id") or "deepseek")
        if provider_id not in provider_options:
            provider_id = next(iter(provider_options), "deepseek")
        preset = presets.get(provider_id, {})
        if not self.form.get("model"):
            self.form["model"] = str(preset.get("default_model") or "")
        if not self.form.get("api_base"):
            self.form["api_base"] = str(preset.get("api_base") or "")

        with ui.column().classes("onboarding-form gap-3"):
            provider_in = (
                ui.select(
                    provider_options,
                    value=provider_id,
                    label="AI 服务商",
                )
                .classes("w-full")
                .props("outlined stack-label options-dense")
            )
            key_in = (
                ui.input(
                    "API Key",
                    value=str(self.form.get("api_key") or ""),
                    password=True,
                    password_toggle_button=True,
                    placeholder=(
                        "已保存过密钥可留空；首次配置请粘贴 API Key"
                        if self.form.get("model_id")
                        else "粘贴厂商控制台创建的 API Key"
                    ),
                )
                .classes("w-full")
                .props("outlined stack-label autocomplete=new-password")
            )
            recommendation = ui.label(
                f"推荐模型：{preset.get('default_model') or '由系统自动选择'}"
            ).classes("onboarding-note w-full")

            async def open_key_page() -> None:
                selected = presets.get(str(provider_in.value or ""), {})
                target = str(selected.get("key_url") or "")
                if target:
                    await _maybe_await(ui.navigate.to(target, new_tab=True))

            ui.button(
                "去获取 API Key",
                icon="open_in_new",
                on_click=open_key_page,
            ).props("flat color=teal-9 no-caps")

            with ui.expansion(
                "高级设置",
                icon="tune",
                value=False,
            ).classes("w-full"):
                api_base_in = (
                    ui.input(
                        "API Base",
                        value=str(self.form.get("api_base") or ""),
                    )
                    .classes("w-full")
                    .props("outlined stack-label")
                )
                model_in = (
                    ui.input(
                        "模型名称",
                        value=str(self.form.get("model") or ""),
                    )
                    .classes("w-full")
                    .props("outlined stack-label")
                )
                display_name_in = (
                    ui.input(
                        "模型配置名称（可选）",
                        value=str(self.form.get("display_name") or ""),
                    )
                    .classes("w-full")
                    .props("outlined stack-label")
                )
                protocol_label = ui.label(
                    _provider_protocol(str(preset.get("provider_type") or ""))
                ).classes("text-caption text-grey-7")

            def provider_changed(_: Any = None) -> None:
                selected_id = str(provider_in.value or "")
                selected = presets.get(selected_id, {})
                self.form["provider_id"] = selected_id
                api_base_in.value = str(selected.get("api_base") or "")
                model_in.value = str(selected.get("default_model") or "")
                recommendation.text = "推荐模型：" + str(
                    selected.get("default_model") or "由系统自动选择"
                )
                protocol_label.text = _provider_protocol(
                    str(selected.get("provider_type") or "")
                )

            provider_in.on_value_change(provider_changed)
            error_label = self._render_error()

            async def test_and_continue() -> None:
                self.form.update(
                    provider_id=str(provider_in.value or ""),
                    api_key=str(key_in.value or ""),
                    api_base=str(api_base_in.value or ""),
                    model=str(model_in.value or ""),
                    display_name=str(display_name_in.value or ""),
                )
                set_button_loading(
                    test_btn,
                    True,
                    "正在真实调用文章模型，请稍候…",
                )
                try:
                    saved = await run.io_bound(
                        lambda: self.service.save_text_model(
                            preset_id=str(self.form["provider_id"]),
                            api_key=str(self.form["api_key"] or "") or None,
                            model=str(self.form["model"] or "") or None,
                            api_base=str(self.form["api_base"] or "") or None,
                            display_name=str(self.form["display_name"] or "") or None,
                        )
                    )
                    model_id = str(saved.get("id") or "")
                    await run.io_bound(lambda: self.service.test_text_model(model_id))
                    await run.io_bound(
                        lambda: self.service.save_progress(
                            mode="full",
                            current_step="account",
                            completed_steps=["welcome", "ai"],
                            selected_model_id=model_id,
                        )
                    )
                    self.form["model_id"] = model_id
                    self.form["api_key"] = ""
                    self.inline_error = ""
                    if self._ui_alive():
                        ui.notify(
                            "AI 连接成功，可以用于文章改写、标题生成和 AI 评审。",
                            type="positive",
                        )
                        self.refresh()
                except Exception as exc:  # noqa: BLE001
                    self.inline_error = _friendly_model_error(exc)
                    if self._ui_alive():
                        error_label.text = self.inline_error
                        error_label.set_visibility(True)
                finally:
                    if self._ui_alive():
                        set_button_loading(test_btn, False)

            test_btn = (
                ui.button(
                    "测试并继续",
                    icon="verified",
                    on_click=test_and_continue,
                )
                .props("unelevated color=teal-9 no-caps")
                .classes("onboarding-primary")
            )

    def _render_account(
        self,
        status: dict[str, Any],
        guide: dict[str, Any],
    ) -> None:
        model_id = str(
            guide.get("selected_model_id")
            or self.form.get("model_id")
            or _first_tested_model_id(status)
        )
        self.form["model_id"] = model_id
        model_name = _model_label(self.service, model_id)
        self._heading(
            "第 2 步，共 3 步",
            "连接第一个公众号",
            "首次只配置一个公众号。保存后，系统会自动绑定已测试的文章 AI、"
            "默认创作方案、默认 AI 评审和标准排版。",
        )
        with ui.column().classes("onboarding-form gap-3"):
            name_in = (
                ui.input(
                    "公众号名称",
                    value=str(self.form.get("account_name") or ""),
                    placeholder="例如：蓝血研究",
                )
                .classes("w-full")
                .props("outlined stack-label")
            )
            app_id_in = (
                ui.input(
                    "AppID",
                    value=str(self.form.get("app_id") or ""),
                    placeholder="以 wx 开头",
                )
                .classes("w-full")
                .props("outlined stack-label")
            )
            app_secret_in = (
                ui.input(
                    "AppSecret",
                    value=str(self.form.get("app_secret") or ""),
                    password=True,
                    password_toggle_button=True,
                    placeholder=(
                        "已保存可留空；首次配置请粘贴 AppSecret"
                        if self.form.get("account_id")
                        else "从微信公众平台复制"
                    ),
                )
                .classes("w-full")
                .props("outlined stack-label autocomplete=new-password")
            )
            with ui.element("div").classes("onboarding-note"):
                ui.label("已绑定文章 AI").classes("text-caption")
                ui.label(model_name or "上一步已测试模型").classes("text-weight-bold")

            with ui.expansion(
                "在哪里找到 AppID 和 AppSecret？",
                icon="help_outline",
                value=False,
            ).classes("w-full"):
                ui.label(
                    "登录微信公众平台，在左侧进入“设置与开发 → 基本配置”，"
                    "即可复制开发者 ID（AppID）并生成或重置 AppSecret。"
                ).classes("text-grey-7")
                ui.link(
                    "打开微信公众平台",
                    "https://mp.weixin.qq.com/",
                    new_tab=True,
                ).classes("text-teal-9 text-weight-bold")
                ui.label(
                    "AppSecret 只在生成时显示一次；请确认当前账号具有开发配置权限。"
                ).classes("text-caption text-grey-7")

            error_label = self._render_error()

            async def save_and_continue() -> None:
                self.form.update(
                    account_name=str(name_in.value or ""),
                    app_id=str(app_id_in.value or ""),
                    app_secret=str(app_secret_in.value or ""),
                )
                set_button_loading(
                    save_btn,
                    True,
                    "正在保存公众号并套用系统默认配置…",
                )
                try:
                    account = await run.io_bound(
                        lambda: self.service.create_first_account(
                            name=str(self.form["account_name"]),
                            app_id=str(self.form["app_id"]),
                            app_secret=str(self.form["app_secret"]) or None,
                            model_id=model_id,
                        )
                    )
                    account_id = str(account.get("id") or "")
                    await run.io_bound(
                        lambda: self.service.save_progress(
                            mode="full",
                            current_step="wechat",
                            completed_steps=["welcome", "ai", "account"],
                            selected_model_id=model_id,
                            selected_account_ids=[account_id],
                            connection_mode=str(
                                self.form.get("connection_mode") or "relay"
                            ),
                        )
                    )
                    self.form["account_id"] = account_id
                    self.form["app_secret"] = ""
                    self.step_override = ""
                    self.wechat_issue = {}
                    self.inline_error = ""
                    if self._ui_alive():
                        self.state.refresh_account_selects()
                        self.refresh()
                except Exception as exc:  # noqa: BLE001
                    self.inline_error = _friendly_account_error(exc)
                    if self._ui_alive():
                        error_label.text = self.inline_error
                        error_label.set_visibility(True)
                finally:
                    if self._ui_alive():
                        set_button_loading(save_btn, False)

            save_btn = (
                ui.button(
                    "保存并继续",
                    icon="arrow_forward",
                    on_click=save_and_continue,
                )
                .props("unelevated color=teal-9 no-caps")
                .classes("onboarding-primary")
            )

    def _render_wechat(
        self,
        status: dict[str, Any],
        guide: dict[str, Any],
    ) -> None:
        account_id = (
            _first_selected_account_id(guide)
            or str(self.form.get("account_id") or "")
            or _first_candidate_account_id(status)
        )
        mode = str(
            self.form.get("connection_mode") or guide.get("connection_mode") or "relay"
        )
        if mode not in {"relay", "direct"}:
            mode = "relay"
        self.form["connection_mode"] = mode
        relay_info = _relay_connection_info()
        fixed_ip = str(relay_info.get("fixed_egress_ip") or "")
        self._heading(
            "第 3 步，共 3 步",
            "微信连接与发布检查",
            "检测只会读取 access_token、素材库和草稿箱状态，"
            "不会创建、修改或删除任何草稿。",
        )
        with ui.column().classes("onboarding-form gap-3"):
            mode_in = ui.toggle(
                {
                    "relay": "云端稳定连接（推荐）",
                    "direct": "本机直接连接",
                },
                value=mode,
            ).classes("w-full")
            mode_host = ui.column().classes("w-full gap-3")

            def render_mode() -> None:
                mode_host.clear()
                selected_mode = str(mode_in.value or "relay")
                self.form["connection_mode"] = selected_mode
                with mode_host:
                    if selected_mode == "relay":
                        with ui.element("div").classes("onboarding-choice selected"):
                            ui.label("云端稳定连接").classes("text-weight-bold text-h6")
                            ui.label(
                                "使用固定出口 IP；运营电脑换网络后，通常不需要反复修改微信白名单。"
                            ).classes("text-grey-7")
                            with ui.row().classes(
                                "w-full items-center justify-between q-mt-sm"
                            ):
                                ui.label(
                                    f"固定出口 IP：{fixed_ip or '由管理员提供'}"
                                ).classes("text-positive text-weight-bold")
                                if fixed_ip:
                                    ui.button(
                                        "一键复制",
                                        icon="content_copy",
                                        on_click=lambda: ui.clipboard.write(fixed_ip),
                                    ).props("flat dense color=teal-9 no-caps")
                            ui.link(
                                "打开微信后台添加 IP 白名单",
                                "https://mp.weixin.qq.com/",
                                new_tab=True,
                            ).classes("text-teal-9 text-weight-bold")
                        relay_code_in = (
                            ui.input(
                                "中转接入码",
                                value=str(self.form.get("relay_code") or ""),
                                password=True,
                                password_toggle_button=True,
                                placeholder=(
                                    "已配置过可留空；首次使用请粘贴 wr1. 开头的接入码"
                                ),
                            )
                            .classes("w-full")
                            .props("outlined stack-label autocomplete=new-password")
                        )
                        self.relay_code_input = relay_code_in
                    else:
                        with ui.element("div").classes("onboarding-choice selected"):
                            ui.label("本机直接连接").classes("text-weight-bold text-h6")
                            ui.label(
                                "微信会看到当前电脑网络的出口 IP。网络或办公地点变化后，"
                                "可能需要重新配置微信 IP 白名单。"
                            ).classes("text-warning")
                            ui.link(
                                "打开微信后台配置当前出口 IP",
                                "https://mp.weixin.qq.com/",
                                new_tab=True,
                            ).classes("text-teal-9 text-weight-bold")

            mode_in.on_value_change(lambda _: render_mode())
            render_mode()
            if not self.wechat_issue:
                self._render_error()
            reports = self.last_reports or _account_reports(status, account_id)
            if reports:
                self._render_check_results(reports)
            if self.wechat_issue:
                self._render_wechat_issue(
                    self.wechat_issue,
                    guide=guide,
                    account_id=account_id,
                    fixed_ip=fixed_ip,
                    mode_in=mode_in,
                )

            async def check_connection() -> None:
                refresh_after = False
                selected_mode = str(mode_in.value or "relay")
                relay_code = str(
                    getattr(getattr(self, "relay_code_input", None), "value", "") or ""
                )
                self.form.update(
                    connection_mode=selected_mode,
                    relay_code=relay_code,
                )
                set_button_loading(
                    check_btn,
                    True,
                    "正在只读验证微信凭证、白名单、素材和草稿接口…",
                )
                try:
                    await run.io_bound(
                        lambda: self._save_connection_mode(
                            selected_mode,
                            relay_code,
                        )
                    )
                    checked_reports = await run.io_bound(
                        lambda: self.service.check_accounts(
                            [account_id] if account_id else None,
                            force=True,
                        )
                    )
                    self.last_reports = [
                        dict(item)
                        for item in list(checked_reports or [])
                        if isinstance(item, dict)
                    ]
                    ready = any(
                        str(item.get("account_id") or "") == account_id
                        and bool(
                            item.get(
                                "draft_ready",
                                bool(item.get("can_write"))
                                and bool(item.get("model_tested", True)),
                            )
                        )
                        for item in self.last_reports
                    )
                    await run.io_bound(
                        lambda: self.service.save_progress(
                            mode="full",
                            current_step="complete" if ready else "wechat",
                            completed_steps=(
                                ["welcome", "ai", "account", "wechat"]
                                if ready
                                else ["welcome", "ai", "account"]
                            ),
                            selected_account_ids=([account_id] if account_id else []),
                            connection_mode=selected_mode,
                        )
                    )
                    self.wechat_issue = (
                        {}
                        if ready
                        else _wechat_issue_from_reports(
                            self.last_reports,
                            account_id,
                        )
                    )
                    self.inline_error = ""
                    self._initial_status = {}
                    refresh_after = True
                except Exception as exc:  # noqa: BLE001
                    self.wechat_issue = onboarding_wechat_issue(exc)
                    self.inline_error = str(
                        self.wechat_issue.get("reason") or _friendly_wechat_error(exc)
                    )
                    refresh_after = True
                finally:
                    if self._ui_alive():
                        set_button_loading(check_btn, False)
                if refresh_after and self._ui_alive():
                    self.refresh()

            check_btn = (
                ui.button(
                    (
                        "重新检测"
                        if reports or self.wechat_issue
                        else "我已添加白名单，开始检测"
                    ),
                    icon="fact_check",
                    on_click=check_connection,
                )
                .props("unelevated color=teal-9 no-caps")
                .classes("onboarding-primary")
            )
            if reports and not all(bool(item.get("can_write")) for item in reports):
                ui.link(
                    "缺少封面？打开微信素材库上传后再检测",
                    "https://mp.weixin.qq.com/",
                    new_tab=True,
                ).classes("text-teal-9 text-weight-bold")

    def _render_wechat_issue(
        self,
        issue: dict[str, Any],
        *,
        guide: dict[str, Any],
        account_id: str,
        fixed_ip: str,
        mode_in: Any,
    ) -> None:
        actions = {str(item) for item in list(issue.get("actions") or []) if str(item)}
        selected_ids = [
            str(item)
            for item in list(guide.get("selected_account_ids") or [])
            if str(item)
        ] or ([account_id] if account_id else [])
        selected_model_id = str(
            guide.get("selected_model_id") or self.form.get("model_id") or ""
        )

        with ui.element("div").classes("onboarding-error w-full"):
            ui.label(
                sanitize_failure_text(
                    issue.get("title") or "公众号连接检查失败"
                )
            ).classes("text-weight-bold")
            ui.label(
                sanitize_failure_text(issue.get("reason") or "")
            ).classes("text-body2")
            ui.label(
                sanitize_failure_text(issue.get("recommendation") or "")
            ).classes("text-caption")
            with ui.row().classes("items-center q-gutter-sm q-mt-sm"):
                if actions & {"edit_app_id", "edit_app_secret"}:

                    async def edit_credentials() -> None:
                        try:
                            completed_steps = [
                                str(item)
                                for item in list(guide.get("completed_steps") or [])
                                if str(item) not in {"account", "wechat"}
                            ]
                            await run.io_bound(
                                lambda: self.service.save_progress(
                                    mode="full",
                                    current_step="account",
                                    completed_steps=completed_steps,
                                    selected_model_id=selected_model_id,
                                    selected_account_ids=selected_ids,
                                    connection_mode=str(
                                        self.form.get("connection_mode") or "relay"
                                    ),
                                )
                            )
                            self.step_override = "account"
                            self.wechat_issue = {}
                            self.inline_error = ""
                            if self._ui_alive():
                                self.refresh()
                        except Exception as exc:  # noqa: BLE001
                            self._show_error(exc)

                    ui.button(
                        "修改公众号凭证",
                        icon="edit",
                        on_click=edit_credentials,
                    ).props("outline color=teal-9 no-caps")

                if "copy_egress_ip" in actions and fixed_ip:
                    ui.button(
                        "复制固定出口 IP",
                        icon="content_copy",
                        on_click=lambda: ui.clipboard.write(fixed_ip),
                    ).props("outline color=teal-9 no-caps")

                if "switch_direct" in actions:

                    async def switch_direct() -> None:
                        try:
                            await run.io_bound(
                                lambda: self.service.save_progress(
                                    mode="full",
                                    current_step="wechat",
                                    selected_model_id=selected_model_id,
                                    selected_account_ids=selected_ids,
                                    connection_mode="direct",
                                )
                            )
                            self.form["connection_mode"] = "direct"
                            self.wechat_issue = {}
                            self.inline_error = ""
                            if self._ui_alive():
                                mode_in.value = "direct"
                                self.refresh()
                        except Exception as exc:  # noqa: BLE001
                            self._show_error(exc)

                    ui.button(
                        "切换本机直连",
                        icon="lan",
                        on_click=switch_direct,
                    ).props("outline color=teal-9 no-caps")

                if "open_wechat_console" in actions:
                    ui.link(
                        "打开微信后台",
                        "https://mp.weixin.qq.com/",
                        new_tab=True,
                    ).classes("text-teal-9 text-weight-bold")

                if "open_permission_help" in actions:
                    ui.link(
                        "查看草稿接口权限说明",
                        "https://developers.weixin.qq.com/doc/"
                        "offiaccount/Draft_Box/Add_draft.html",
                        new_tab=True,
                    ).classes("text-teal-9 text-weight-bold")
                    ui.link(
                        "打开微信后台",
                        "https://mp.weixin.qq.com/",
                        new_tab=True,
                    ).classes("text-teal-9 text-weight-bold")

    def _save_connection_mode(self, mode: str, relay_code: str) -> None:
        if mode == "relay":
            saved = public_wechat_relay_settings(self.state.db)
            if relay_code:
                _save_relay_access_code(self.state.db, relay_code)
                return
            if not (saved.get("username") and saved.get("has_password")):
                raise ValueError(
                    "还没有可用的中转接入码。请粘贴接入码；"
                    "如果暂时没有，请切换为本机直接连接。"
                )
            info = _relay_connection_info()
            save_wechat_relay_settings(
                self.state.db,
                enabled=True,
                gateway_url=str(
                    saved.get("gateway_url") or info.get("gateway_url") or ""
                ),
                username=str(saved.get("username") or ""),
                password=None,
            )
            return
        saved = public_wechat_relay_settings(self.state.db)
        save_wechat_relay_settings(
            self.state.db,
            enabled=False,
            gateway_url=str(saved.get("gateway_url") or ""),
            username=str(saved.get("username") or ""),
            password=None,
        )

    def _render_check_results(self, reports: list[dict[str, Any]]) -> None:
        report = reports[0] if reports else {}
        with ui.element("div").classes("onboarding-summary"):
            for check in list(report.get("checks") or []):
                if not isinstance(check, dict):
                    continue
                ok = bool(check.get("ok"))
                with ui.element("div").classes("onboarding-check"):
                    ui.label("✓" if ok else "!").classes(
                        "onboarding-check-icon "
                        + ("text-positive" if ok else "text-warning")
                    )
                    with ui.column().classes("gap-0"):
                        ui.label(str(check.get("name") or "配置检查")).classes(
                            "text-weight-bold"
                        )
                        ui.label(
                            sanitize_failure_text(check.get("message") or "")
                        ).classes("text-caption text-grey-7")

    def _render_complete(
        self,
        status: dict[str, Any],
        guide: dict[str, Any],
    ) -> None:
        account_id = _first_selected_account_id(guide) or _first_candidate_account_id(
            status
        )
        model_id = str(
            guide.get("selected_model_id")
            or self.form.get("model_id")
            or _first_tested_model_id(status)
        )
        account = _account_public(self.service, account_id)
        account_name = str(account.get("name") or "已连接公众号")
        mode = str(
            guide.get("connection_mode") or self.form.get("connection_mode") or "relay"
        )
        reports = self.last_reports or _account_reports(status, account_id)
        report = reports[0] if reports else {}
        checks = {
            str(item.get("key") or ""): dict(item)
            for item in list(report.get("checks") or [])
            if isinstance(item, dict)
        }
        self._heading(
            "配置完成",
            "现在可以开始第一篇文章",
            "公众号已经绑定系统默认创作方案、默认 AI 评审和标准排版。"
            "这些设置日常无需重复选择。",
        )
        with ui.column().classes("onboarding-form gap-3"):
            summary_items = [
                ("文章 AI", _model_label(self.service, model_id) or "连接正常", True),
                ("公众号", account_name, bool(account_id)),
                (
                    "连接方式",
                    "云端稳定连接" if mode == "relay" else "本机直接连接",
                    True,
                ),
                (
                    "草稿接口",
                    sanitize_failure_text(
                        checks.get("draft", {}).get("message") or "验证成功"
                    ),
                    bool(checks.get("draft", {}).get("ok", True)),
                ),
                (
                    "封面素材",
                    sanitize_failure_text(
                        checks.get("material", {}).get("message") or "可用"
                    ),
                    bool(checks.get("material", {}).get("ok", True)),
                ),
                ("创作方案", "系统默认方案", True),
            ]
            with ui.element("div").classes("onboarding-summary"):
                for name, value, ok in summary_items:
                    with ui.element("div").classes("onboarding-check"):
                        ui.label("✓" if ok else "!").classes(
                            "onboarding-check-icon "
                            + ("text-positive" if ok else "text-warning")
                        )
                        with ui.column().classes("gap-0"):
                            ui.label(name).classes("text-weight-bold")
                            ui.label(value).classes("text-caption text-grey-7")
            self._render_error()

            async def finish() -> None:
                set_button_loading(
                    finish_btn,
                    True,
                    "正在打开工作台…",
                )
                try:
                    await run.io_bound(lambda: self.service.complete(mode="full"))
                    if account_id:
                        self.state.remember_account_ids([account_id])
                        if self._ui_alive():
                            self.state.refresh_account_selects()
                    if not self._ui_alive():
                        return
                    if self.on_completed is not None:
                        result = self.on_completed(account_id or None)
                        await _maybe_await(result)
                    else:
                        ui.navigate.reload()
                except Exception as exc:  # noqa: BLE001
                    self._show_error(exc)
                finally:
                    if self._ui_alive():
                        set_button_loading(finish_btn, False)

            finish_btn = (
                ui.button(
                    "开始第一篇文章",
                    icon="edit_note",
                    on_click=finish,
                )
                .props("unelevated color=teal-9 no-caps")
                .classes("onboarding-primary")
            )
            ui.label(
                "进入工作台后会自动选中这个公众号，并停留在“选择内容”；"
                "不会自动创建文章任务。"
            ).classes("text-caption text-grey-7")

            with ui.expansion(
                "之后还可以增强",
                icon="auto_awesome",
                value=False,
            ).classes("w-full"):
                ui.label(
                    "图片生成 · 定制写作风格 · 关注公众号 · 热点来源 · 飞书接入"
                ).classes("text-grey-7")
                ui.label(
                    "这些功能不会阻止进入工作台，第一次使用时再配置即可。"
                ).classes("text-caption text-grey-7")

    def _show_error(self, exc: Exception) -> None:
        self.inline_error = sanitize_failure_text(exc)
        if self._ui_alive():
            ui.notify(self.inline_error, type="negative", timeout=12000)

    def _ui_alive(self) -> bool:
        return not bool(getattr(self.owner_client, "is_deleted", False))


def _infer_provider_id(
    model: dict[str, Any],
    presets: list[dict[str, Any]],
) -> str:
    provider_type = str(model.get("provider_type") or "")
    api_base = str(model.get("api_base") or "").rstrip("/").casefold()
    model_name = str(model.get("model") or "").casefold()
    for preset in presets:
        preset_id = str(preset.get("id") or "")
        if preset_id not in BASIC_PROVIDER_IDS:
            continue
        preset_base = str(preset.get("api_base") or "").rstrip("/").casefold()
        preset_models = {
            str(item).casefold() for item in list(preset.get("models") or [])
        }
        if (
            preset_base
            and preset_base == api_base
            or model_name
            and model_name in preset_models
        ):
            return preset_id
        if (
            provider_type
            and provider_type == str(preset.get("provider_type") or "")
            and provider_type in {"gemini", "manus"}
        ):
            return preset_id
    return "deepseek"


def _provider_protocol(provider_type: str) -> str:
    value = str(provider_type or "").casefold()
    if value == "gemini":
        return "协议类型：Google Gemini"
    if value == "manus":
        return "协议类型：Manus API"
    return "协议类型：OpenAI 兼容"


def _friendly_model_error(exc: Exception) -> str:
    try:
        from app.services.onboarding_errors import friendly_model_error

        return sanitize_failure_text(friendly_model_error(exc))
    except ImportError:
        return sanitize_failure_text(exc)


def _run_auto_check(service: OnboardingService) -> dict[str, Any]:
    auto_check = getattr(service, "auto_check", None)
    if callable(auto_check):
        return dict(auto_check() or {})
    return dict(
        service.status(
            refresh_wechat=True,
            retest_models=True,
        )
        or {}
    )


def _friendly_account_error(exc: Exception) -> str:
    message = sanitize_failure_text(exc)
    lowered = message.casefold()
    if "appid" in lowered and ("required" in lowered or "填写" in message):
        return "公众号 AppID 不能为空。请从微信公众平台的“基本配置”中复制。"
    if "appsecret" in lowered and ("required" in lowered or "填写" in message):
        return "公众号 AppSecret 不能为空。请重新生成或复制后再保存。"
    return message


def _friendly_wechat_error(exc: Exception) -> str:
    try:
        from app.wechat.errors import friendly_wechat_error

        return sanitize_failure_text(friendly_wechat_error(exc))
    except ImportError:
        return sanitize_failure_text(exc)


def _relay_connection_info() -> dict[str, Any]:
    return dict(public_wechat_relay_connection_info() or {})


def _save_relay_access_code(db: Any, code: str) -> dict[str, Any]:
    return dict(save_wechat_relay_access_code(db, code, enabled=True) or {})


def _first_selected_account_id(guide: dict[str, Any]) -> str:
    return next(
        (
            str(item)
            for item in list(guide.get("selected_account_ids") or [])
            if str(item)
        ),
        "",
    )


def _first_tested_model_id(status: dict[str, Any]) -> str:
    return next(
        (str(item) for item in list(status.get("tested_model_ids") or []) if str(item)),
        "",
    )


def _candidate_account_ids(status: dict[str, Any]) -> list[str]:
    ready = [
        str(item)
        for item in list(status.get("draft_ready_account_ids") or [])
        if str(item)
    ]
    if ready:
        return ready[:1]
    return [
        str(item.get("account_id") or "")
        for item in list(status.get("account_checks") or [])
        if isinstance(item, dict) and str(item.get("account_id") or "")
    ][:1]


def _first_candidate_account_id(status: dict[str, Any]) -> str:
    return next(iter(_candidate_account_ids(status)), "")


def _account_reports(
    status: dict[str, Any],
    account_id: str,
) -> list[dict[str, Any]]:
    reports = [
        dict(item)
        for item in list(status.get("account_checks") or [])
        if isinstance(item, dict)
    ]
    if account_id:
        scoped = [
            item for item in reports if str(item.get("account_id") or "") == account_id
        ]
        if scoped:
            return scoped
    return reports[:1]


def _wechat_issue_from_reports(
    reports: list[dict[str, Any]],
    account_id: str,
) -> dict[str, Any]:
    report = next(
        (
            item
            for item in reports
            if str(item.get("account_id") or "") == str(account_id or "")
        ),
        reports[0] if reports else {},
    )
    failed_messages = [
        str(item.get("message") or "")
        for item in list(report.get("checks") or [])
        if isinstance(item, dict)
        and not bool(item.get("ok"))
        and str(item.get("message") or "")
    ]
    if not failed_messages:
        return {}
    return onboarding_wechat_issue("；".join(failed_messages))


def _account_public(
    service: OnboardingService,
    account_id: str,
) -> dict[str, Any]:
    if not account_id:
        return {}
    try:
        return dict(service.configuration.get_account(account_id) or {})
    except Exception:  # noqa: BLE001
        return {}


def _model_label(service: OnboardingService, model_id: str) -> str:
    if not model_id:
        return ""
    try:
        model = service.configuration.get_model(model_id)
    except Exception:  # noqa: BLE001
        return ""
    name = str(model.get("name") or "")
    model_name = str(model.get("model") or "")
    if name and model_name and model_name not in name:
        return f"{name} · {model_name}"
    return name or model_name


def _next_required_step(status: dict[str, Any]) -> str:
    if not bool(status.get("writer_ready", status.get("model_tested"))):
        return "ai"
    if not bool(status.get("content_ready", status.get("core_ready"))):
        return "account"
    if not bool(status.get("draft_ready")):
        return "wechat"
    return "complete"


def _completed_before(step: str) -> list[str]:
    index = WIZARD_STEPS.index(step) if step in WIZARD_STEPS else 0
    return list(WIZARD_STEPS[:index])


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "BASIC_PROVIDER_IDS",
    "ONBOARDING_CSS",
    "WIZARD_STEPS",
    "build_configuration_health_banner",
    "build_onboarding_settings",
    "build_onboarding_wizard",
    "configuration_health_needs_refresh",
    "should_show_onboarding",
]
