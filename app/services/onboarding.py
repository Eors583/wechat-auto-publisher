from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ai.image_providers import is_image_provider
from app.ai.model_registry import (
    GEMINI,
    MANUS,
    OPENAI_COMPATIBLE,
    public_models,
)
from app.config import load_config
from app.db import Database
from app.feishu.pairing import create_pairing_code, pairing_status
from app.feishu.runtime import get_runtime
from app.feishu.settings import (
    effective_feishu_settings,
    public_feishu_settings,
    save_feishu_settings,
)
from app.layout_profiles import DEFAULT_LAYOUT
from app.services.configuration import ConfigurationService
from app.services.creation_plans import (
    BUILTIN_DEFAULT_CREATION_PLAN_ID,
    CreationPlanService,
)
from app.services.failures import sanitize_failure_text
from app.services.model_readiness import (
    active_model_auth_failure_ids,
    clear_model_auth_failure,
    model_fingerprint,
    record_model_auth_failure_for_error,
)
from app.services.onboarding_errors import friendly_model_error
from app.services.preflight import preflight_accounts
from app.services.wechat_relay_settings import effective_wechat_relay_settings

ONBOARDING_SETTING_KEY = "onboarding.guide"
ONBOARDING_WIZARD_VERSION = 1
ONBOARDING_MODES = frozenset({"full", "experience"})
ONBOARDING_STEPS = (
    "welcome",
    "ai",
    "account",
    "wechat",
    "complete",
)
FEISHU_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)


# These are application presets, not credentials. Built-in presets keep new
# operators away from protocol names, endpoint paths and model identifiers.
TEXT_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek（推荐，国内使用简单）",
        "provider_type": OPENAI_COMPATIBLE,
        "api_base": "https://api.deepseek.com",
        "models": ("deepseek-chat", "deepseek-reasoner"),
        "default_model": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
        "docs_url": "https://api-docs.deepseek.com/",
        "key_hint": "在 DeepSeek 开放平台创建 API Key，通常以 sk- 开头。",
    },
    "qwen": {
        "id": "qwen",
        "label": "阿里云百炼 · 通义千问",
        "provider_type": OPENAI_COMPATIBLE,
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ("qwen-plus", "qwen-max", "qwen-long"),
        "default_model": "qwen-plus",
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
        "docs_url": "https://help.aliyun.com/zh/model-studio/get-api-key",
        "key_hint": "创建“百炼 API Key”，不要使用只允许编程工具的 Coding Plan Key。",
    },
    "moonshot": {
        "id": "moonshot",
        "label": "Kimi · Moonshot",
        "provider_type": OPENAI_COMPATIBLE,
        "api_base": "https://api.moonshot.cn/v1",
        "models": ("moonshot-v1-128k", "moonshot-v1-32k"),
        "default_model": "moonshot-v1-128k",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "docs_url": "https://platform.moonshot.cn/docs/",
        "key_hint": "在 Moonshot 开放平台的 API Key 管理中创建密钥。",
    },
    "zhipu": {
        "id": "zhipu",
        "label": "智谱 GLM",
        "provider_type": OPENAI_COMPATIBLE,
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "models": ("glm-4-flash-250414", "glm-4-plus", "glm-4.6"),
        "default_model": "glm-4-flash-250414",
        "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        "docs_url": "https://docs.bigmodel.cn/cn/guide/start/quick-start",
        "key_hint": "在智谱开放平台创建 API Key；免费模型也必须使用有效 Key。",
    },
    "gemini": {
        "id": "gemini",
        "label": "Google Gemini",
        "provider_type": GEMINI,
        "api_base": "",
        "models": ("gemini-2.0-flash", "gemini-2.5-flash"),
        "default_model": "gemini-2.0-flash",
        "key_url": "https://aistudio.google.com/app/apikey",
        "docs_url": "https://ai.google.dev/gemini-api/docs/api-key",
        "key_hint": "从 Google AI Studio 创建 Gemini API Key。",
    },
    "manus": {
        "id": "manus",
        "label": "Manus API",
        "provider_type": MANUS,
        "api_base": "https://api.manus.ai",
        "models": ("manus-1.6",),
        "default_model": "manus-1.6",
        "key_url": "https://manus.im/",
        "docs_url": "https://open.manus.ai/",
        "key_hint": "需要 Manus 开放平台提供的 API Key，不是网页登录 Cookie。",
    },
    "custom": {
        "id": "custom",
        "label": "其他厂商 / 自建 OpenAI 兼容接口（高级）",
        "provider_type": OPENAI_COMPATIBLE,
        "api_base": "",
        "models": (),
        "default_model": "",
        "key_url": "",
        "docs_url": "",
        "key_hint": "仅在服务商明确写着“兼容 OpenAI Chat Completions”时使用。",
    },
}


class OnboardingService:
    """Beginner-safe setup boundary for model and Feishu credentials."""

    def __init__(
        self,
        db: Database,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.db = db
        self.config = config if config is not None else load_config()
        self.configuration = ConfigurationService(db, self.config)

    def model_presets(self) -> list[dict[str, Any]]:
        return [dict(item) for item in TEXT_MODEL_PRESETS.values()]

    def save_text_model(
        self,
        *,
        preset_id: str,
        api_key: str | None,
        model: str | None = None,
        api_base: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        preset = self._preset(preset_id)
        selected_model = str(model or preset["default_model"]).strip()
        selected_base = str(api_base or preset["api_base"]).strip()
        if not selected_model:
            raise ValueError("请填写模型名称")
        if preset_id == "custom" and not selected_base:
            raise ValueError("自定义接口必须填写 API Base URL")
        state = self._load_state()
        existing_id = str((state.get("model_ids") or {}).get(preset_id) or "")
        if existing_id and not self.db.get_ai_model(existing_id):
            existing_id = ""
        saved = self.configuration.save_model(
            model_id=existing_id or None,
            name=str(display_name or "").strip()
            or f"{str(preset['label']).split('（', 1)[0]} · {selected_model}",
            provider_type=str(preset["provider_type"]),
            api_base=selected_base,
            model=selected_model,
            api_key=str(api_key or "").strip() or None,
            enabled=True,
        )
        model_ids = dict(state.get("model_ids") or {})
        model_ids[preset_id] = str(saved["id"])
        state["model_ids"] = model_ids
        self._save_state(state)
        return saved

    def test_text_model(self, model_id: str) -> dict[str, Any]:
        clean_model_id = str(model_id or "").strip()
        result = self.configuration.test_model(clean_model_id)
        # A successful real probe supersedes a previously recorded 401/403 for
        # this exact model. Reload after clearing so concurrent-safe metadata is
        # not overwritten by the model-test record below.
        clear_model_auth_failure(self.db, clean_model_id)
        state = self._load_state()
        tests = dict(state.get("model_tests") or {})
        tests[clean_model_id] = {
            "ok": True,
            # Keep provider responses out of persisted wizard state. The
            # successful fingerprint is sufficient to prove this exact
            # configuration was tested.
            "message": "连接成功",
            "model_fingerprint": self._model_fingerprint(clean_model_id),
            "tested_at": _utc_now(),
        }
        state["model_tests"] = tests
        self._save_state(state)
        return result

    def bind_model_to_accounts(
        self,
        model_id: str,
        account_ids: list[str],
    ) -> list[dict[str, Any]]:
        clean_ids = list(
            dict.fromkeys(
                str(item).strip() for item in account_ids if str(item).strip()
            )
        )
        if not clean_ids:
            raise ValueError("请至少选择一个公众号")
        return [
            self.configuration.bind_account_model(account_id, model_id)
            for account_id in clean_ids
        ]

    def guide(self) -> dict[str, Any]:
        """Return the resumable wizard state without internal test metadata."""

        state = self._load_state()
        return {
            "wizard_version": ONBOARDING_WIZARD_VERSION,
            "mode": str(state["mode"]),
            "current_step": str(state["current_step"]),
            "completed_steps": list(state["completed_steps"]),
            "selected_model_id": str(state["selected_model_id"]),
            "selected_account_ids": list(state["selected_account_ids"]),
            "connection_mode": str(state["connection_mode"]),
            "force_open": bool(state["force_open"]),
            "completed_at": state["completed_at"],
            "updated_at": state["updated_at"],
        }

    def save_progress(
        self,
        *,
        mode: str | None = None,
        current_step: str | None = None,
        completed_steps: list[str] | None = None,
        selected_model_id: str | None = None,
        selected_account_ids: list[str] | None = None,
        connection_mode: str | None = None,
    ) -> dict[str, Any]:
        """Persist only non-secret, versioned wizard progress."""

        state = self._load_state()
        if mode is not None:
            clean_mode = str(mode or "").strip().casefold()
            if clean_mode not in ONBOARDING_MODES:
                raise ValueError("向导模式无效")
            state["mode"] = clean_mode
        if current_step is not None:
            state["current_step"] = _validate_step(current_step)
        if completed_steps is not None:
            state["completed_steps"] = _clean_steps(completed_steps)
        if selected_model_id is not None:
            state["selected_model_id"] = str(selected_model_id or "").strip()
        if selected_account_ids is not None:
            state["selected_account_ids"] = _clean_list(selected_account_ids)
        if connection_mode is not None:
            clean_connection_mode = str(connection_mode or "").strip().casefold()
            if clean_connection_mode not in {"relay", "direct"}:
                raise ValueError("微信连接方式无效")
            state["connection_mode"] = clean_connection_mode
        self._save_state(state)
        return self.guide()

    def create_first_account(
        self,
        *,
        name: str,
        app_id: str,
        app_secret: str | None,
        model_id: str,
    ) -> dict[str, Any]:
        """Create the first publishing account with beginner-safe defaults.

        Retrying the same AppID is idempotent only after this wizard has taken
        ownership of the account. An unrelated historical account is never
        silently reset to the built-in plan.
        """

        clean_app_id = str(app_id or "").strip()
        clean_model_id = str(model_id or "").strip()
        if not clean_app_id:
            raise ValueError("请填写公众号 AppID")
        if not self._is_model_tested(clean_model_id):
            raise ValueError("请先测试并通过文章 AI 连接")

        state = self._load_state()
        accounts = self.db.list_official_accounts()
        matches = [
            item
            for item in accounts
            if str(item.get("app_id") or "").strip() == clean_app_id
        ]
        if len(matches) > 1:
            raise ValueError("已有多个公众号使用相同 AppID，请先到设置中整理")
        selected_ids = set(state["selected_account_ids"])
        wizard_accounts = [
            item for item in accounts if str(item.get("id") or "") in selected_ids
        ]
        existing = matches[0] if matches else (wizard_accounts[0] if wizard_accounts else None)
        existing_id = str((existing or {}).get("id") or "")
        matching_account = matches[0] if matches else None
        if matching_account is not None and existing_id not in selected_ids:
            raise ValueError(
                "该 AppID 已存在于历史公众号中。请使用“自动检查”恢复配置，"
                "向导不会覆盖它现有的创作方案和排版。"
            )
        if existing is not None and existing_id not in selected_ids:
            raise ValueError(
                "该公众号不属于本次向导，向导不会覆盖它现有的创作方案和排版。"
            )
        if existing is None and not str(app_secret or "").strip():
            raise ValueError("请填写公众号 AppSecret")

        previous_guide = self.db.get_setting(ONBOARDING_SETTING_KEY)
        previous_target = self.db.get_setting("ui.last_target_account_ids")
        existing_snapshot = deepcopy(existing) if existing is not None else None
        previous_plan = (
            self.db.get_account_creation_plan_default(existing_id)
            if existing_id
            else None
        )
        previous_review = (
            self.db.get_account_editorial_review_default(existing_id)
            if existing_id
            else None
        )
        saved_account_id = ""
        try:
            saved = self.configuration.save_account(
                account_id=existing_id or None,
                name=name,
                app_id=clean_app_id,
                app_secret=app_secret,
                model_id=clean_model_id,
                enabled=True,
            )
            saved_account_id = str(saved["id"])

            # Do not inherit a shared/global custom template, prompt or image
            # model. The built-in plan then records the default review/prompt
            # bindings without changing these clean appearance settings.
            clean_layout = deepcopy(DEFAULT_LAYOUT)
            clean_layout["editor_template"]["enabled"] = False
            clean_layout["inline_images"]["enabled"] = False
            self.configuration.save_account_layout(
                saved_account_id,
                clean_layout,
            )
            CreationPlanService(
                self.db,
                self.config,
            ).apply_to_account(
                saved_account_id,
                BUILTIN_DEFAULT_CREATION_PLAN_ID,
            )
            self.db.set_setting(
                "ui.last_target_account_ids",
                json.dumps([saved_account_id], ensure_ascii=False),
            )

            completed = [
                *list(state["completed_steps"]),
                "ai",
                "account",
            ]
            self.save_progress(
                current_step="wechat",
                completed_steps=completed,
                selected_model_id=clean_model_id,
                selected_account_ids=[saved_account_id],
            )
            return self.configuration.get_account(saved_account_id)
        except Exception:
            self._rollback_first_account(
                account_id=saved_account_id or existing_id,
                existing_account=existing_snapshot,
                previous_plan=previous_plan,
                previous_review=previous_review,
                previous_guide=previous_guide,
                previous_target=previous_target,
            )
            raise

    def check_accounts(
        self,
        account_ids: list[str] | None = None,
        *,
        force: bool = True,
    ) -> list[dict[str, Any]]:
        """Run the established read-only WeChat/material/draft preflight."""

        enabled_accounts = self.configuration.list_accounts(enabled_only=True)
        selected_ids = (
            _clean_list(account_ids)
            if account_ids is not None
            else [str(item["id"]) for item in enabled_accounts]
        )
        if not selected_ids:
            return []
        accounts_by_id = {str(item["id"]): item for item in enabled_accounts}
        selected_accounts = [
            accounts_by_id[account_id]
            for account_id in selected_ids
            if account_id in accounts_by_id
        ]
        reports = preflight_accounts(
            self.db,
            selected_ids,
            deep_model_check=False,
            force_wechat_check=bool(force),
        )
        tested_ids = set(self._tested_model_ids())
        return self._decorate_account_checks(
            reports,
            selected_accounts,
            tested_ids,
        )

    def test_feishu_credentials(
        self,
        *,
        app_id: str,
        app_secret: str | None = None,
        post: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        effective = effective_feishu_settings(self.db)
        clean_app_id = str(app_id or effective.get("app_id") or "").strip()
        clean_secret = str(app_secret or effective.get("app_secret") or "").strip()
        if not clean_app_id or not clean_secret:
            raise ValueError("请填写飞书 App ID 和 App Secret")
        sender = post or httpx.post
        try:
            response = sender(
                FEISHU_TOKEN_URL,
                json={
                    "app_id": clean_app_id,
                    "app_secret": clean_secret,
                },
                timeout=15,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RuntimeError("无法连接飞书开放平台，请检查本机网络后重试") from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"飞书返回了无法识别的结果（HTTP {response.status_code}）"
            ) from exc
        if response.status_code >= 400 or int(payload.get("code") or 0) != 0:
            code = payload.get("code", response.status_code)
            message = str(payload.get("msg") or "App ID 或 App Secret 无效")
            raise ValueError(
                f"飞书凭证验证失败（{code}）：{message}。"
                "请重新复制“凭证与基础信息”中的 App ID 和 App Secret"
            )
        state = self._load_state()
        state["feishu_credentials_test"] = {
            "ok": True,
            "app_id": clean_app_id,
            "credential_fingerprint": _credential_fingerprint(
                clean_app_id,
                clean_secret,
            ),
            "tested_at": _utc_now(),
        }
        self._save_state(state)
        return {
            "ok": True,
            "message": "App ID 和 App Secret 验证成功",
            "app_id": clean_app_id,
        }

    def save_feishu(
        self,
        *,
        app_id: str,
        app_secret: str | None,
        agent_model_id: str,
        default_account_ids: list[str],
        allow_all: bool,
        allowed_open_ids: list[str] | None = None,
        allowed_chat_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        model = self.configuration.get_model(agent_model_id)
        if is_image_provider(str(model.get("provider_type") or "")):
            raise ValueError("飞书智能体必须选择文本模型")
        model_test = dict(
            (self._load_state().get("model_tests") or {}).get(str(agent_model_id or ""))
            or {}
        )
        if not (
            bool(model_test.get("ok"))
            and str(model_test.get("model_fingerprint") or "")
            == self._model_fingerprint(agent_model_id)
        ):
            raise ValueError("请先在第 1 步测试飞书智能体使用的文本模型")
        account_ids = _clean_list(default_account_ids)
        if not account_ids:
            raise ValueError("请至少选择一个机器人默认使用的公众号")
        known_account_ids = {
            str(item.get("id") or "")
            for item in self.configuration.list_accounts(enabled_only=True)
        }
        missing_account_ids = [
            account_id
            for account_id in account_ids
            if account_id not in known_account_ids
        ]
        if missing_account_ids:
            raise ValueError("所选公众号已停用或不存在，请刷新后重新选择")
        open_ids = _clean_list(allowed_open_ids)
        chat_ids = _clean_list(allowed_chat_ids)
        save_feishu_settings(
            self.db,
            enabled=True,
            app_id=app_id,
            app_secret=app_secret,
            allow_all=allow_all,
            allowed_open_ids=open_ids,
            allowed_chat_ids=chat_ids,
            default_account_ids=account_ids,
            agent_model_id=agent_model_id,
            clear_event_security=True,
        )
        return public_feishu_settings(self.db)

    def create_feishu_pairing_code(
        self,
        *,
        ttl_minutes: int = 30,
    ) -> dict[str, Any]:
        feishu = public_feishu_settings(self.db)
        if not (
            feishu.get("enabled")
            and feishu.get("app_id")
            and feishu.get("has_app_secret")
        ):
            raise ValueError("请先验证并保存飞书 App ID 和 App Secret")
        return create_pairing_code(
            self.db,
            ttl_minutes=ttl_minutes,
        )

    def feishu_pairing_status(self) -> dict[str, Any]:
        return pairing_status(self.db)

    def status(
        self,
        *,
        refresh_wechat: bool = False,
        retest_models: bool = False,
        retest_model_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return local model/account readiness plus cached or refreshed preflight.

        The default path never sends a network request. It reuses the last
        known connection result, including naturally expired healthy results,
        while rechecking local template/image configuration. Explicit refreshes
        call the same read-only remote preflight used before publishing.
        """

        model_retest_results = (
            self._retest_text_models(retest_model_ids)
            if retest_models
            else []
        )
        state = self._load_state()
        db_models = public_models(self.db, enabled_only=True, purpose="text")
        model_ids = {str(item.get("id") or "") for item in db_models}
        accounts = self.configuration.list_accounts(enabled_only=True)
        model_auth_failed_ids = active_model_auth_failure_ids(
            self.db,
            self.config,
            state=state,
        )
        fingerprint_tested_model_ids = self._tested_model_ids(
            state=state,
            available_model_ids=model_ids,
        )
        legacy_trusted_model_ids = (
            self._legacy_trusted_model_ids(
                accounts,
                available_model_ids=model_ids,
            )
            if not str(
                self.db.get_setting(ONBOARDING_SETTING_KEY) or ""
            ).strip()
            else []
        )
        legacy_trusted_model_ids = [
            model_id
            for model_id in legacy_trusted_model_ids
            if model_id not in model_auth_failed_ids
        ]
        tested_model_ids = list(
            dict.fromkeys(
                [
                    *fingerprint_tested_model_ids,
                    *legacy_trusted_model_ids,
                ]
            )
        )
        tested_model_id_set = set(tested_model_ids)
        bound_accounts = [
            item
            for item in accounts
            if str(
                item.get("effective_model_id")
                or item.get("model_id")
                or ""
            )
            in model_ids
        ]
        content_ready_accounts = [
            item
            for item in accounts
            if str(
                item.get("effective_model_id")
                or item.get("model_id")
                or ""
            )
            in tested_model_id_set
        ]
        account_ids = [str(item["id"]) for item in accounts]
        if refresh_wechat and account_ids:
            raw_account_checks = preflight_accounts(
                self.db,
                account_ids,
                deep_model_check=False,
                force_wechat_check=True,
            )
        else:
            raw_account_checks = self._offline_cached_account_checks(accounts)
        account_checks = self._decorate_account_checks(
            raw_account_checks,
            accounts,
            tested_model_id_set,
        )
        draft_ready_account_ids = [
            str(item["account_id"])
            for item in account_checks
            if bool(item.get("draft_ready"))
        ]
        wechat_refresh_account_ids = self._wechat_refresh_account_ids(accounts)

        feishu = public_feishu_settings(self.db)
        effective_feishu = effective_feishu_settings(self.db)
        runtime = get_runtime(self.db)
        credentials_test = dict(state.get("feishu_credentials_test") or {})
        credentials_ok = bool(credentials_test.get("ok")) and str(
            credentials_test.get("credential_fingerprint") or ""
        ) == _credential_fingerprint(
            str(effective_feishu.get("app_id") or ""),
            str(effective_feishu.get("app_secret") or ""),
        )
        runtime_is_current = bool(
            runtime.get("app_id")
            and str(runtime.get("app_id") or "") == str(feishu.get("app_id") or "")
        )
        message_fresh = _is_at_or_after(
            runtime.get("last_message_at"),
            runtime.get("started_at"),
        )
        reply_fresh = _is_at_or_after(
            runtime.get("last_reply_at"),
            runtime.get("last_message_at"),
        )
        message_authorized = bool(
            feishu.get("allow_all")
            or str(runtime.get("last_open_id") or "")
            in set(feishu.get("allowed_open_ids") or [])
            or str(runtime.get("last_chat_id") or "")
            in set(feishu.get("allowed_chat_ids") or [])
        )
        all_accounts_bound = bool(accounts) and (len(bound_accounts) == len(accounts))
        all_account_models_tested = all_accounts_bound and all(
            str(
                item.get("effective_model_id")
                or item.get("model_id")
                or ""
            )
            in tested_model_id_set
            for item in accounts
        )
        writer_ready = bool(tested_model_ids)
        content_ready = bool(content_ready_accounts)
        draft_ready = bool(draft_ready_account_ids)
        feishu_saved = bool(
            feishu.get("enabled")
            and feishu.get("app_id")
            and feishu.get("has_app_secret")
            and feishu.get("agent_model_id")
            and feishu.get("default_account_ids")
        )
        feishu_ready = bool(
            credentials_ok
            and feishu_saved
            and runtime_is_current
            and str(runtime.get("status") or "") == "running"
            and message_fresh
            and reply_fresh
            and message_authorized
        )
        guide = self.guide()
        mode = str(guide.get("mode") or "full")
        wizard_required = bool(guide.get("force_open")) or (
            not draft_ready if mode == "full" else not content_ready
        )
        repair_step = (
            "ai"
            if not writer_ready
            else (
                "account"
                if not content_ready
                else ("wechat" if not draft_ready else "")
            )
        )
        saved_step = str(guide.get("current_step") or "welcome")
        current_step = "complete" if not wizard_required else saved_step
        if (
            wizard_required
            and not bool(guide.get("force_open"))
            and repair_step
            and (
                saved_step == "complete"
                or guide.get("completed_at")
                or (
                    saved_step == "welcome"
                    and bool(db_models or accounts or guide.get("updated_at"))
                )
            )
        ):
            current_step = repair_step
        return {
            "has_text_model": bool(model_ids),
            "tested_model_ids": tested_model_ids,
            "fingerprint_tested_model_ids": fingerprint_tested_model_ids,
            "legacy_upgrade_detected": bool(
                legacy_trusted_model_ids
            ),
            "legacy_trusted_model_ids": legacy_trusted_model_ids,
            "model_auth_failed_model_ids": sorted(model_auth_failed_ids),
            "model_retest_results": model_retest_results,
            "model_retest_failed_count": sum(
                1 for item in model_retest_results if not bool(item.get("ok"))
            ),
            "model_tested": writer_ready,
            "writer_ready": writer_ready,
            "account_count": len(accounts),
            "bound_account_count": len(bound_accounts),
            "accounts_bound": all_accounts_bound,
            "account_models_tested": all_account_models_tested,
            "content_ready_account_ids": [
                str(item["id"]) for item in content_ready_accounts
            ],
            "content_ready": content_ready,
            "account_checks": account_checks,
            "draft_ready_account_ids": draft_ready_account_ids,
            "draft_ready": draft_ready,
            "last_known_draft_ready_account_ids": list(draft_ready_account_ids),
            "last_known_draft_ready": draft_ready,
            "wechat_refresh_account_ids": wechat_refresh_account_ids,
            "wechat_refresh_needed": bool(wechat_refresh_account_ids),
            "feishu_saved": feishu_saved,
            "feishu_credentials_tested": credentials_ok,
            "feishu_runtime_status": str(runtime.get("status") or "stopped"),
            "feishu_last_message_at": runtime.get("last_message_at"),
            "feishu_last_reply_at": runtime.get("last_reply_at"),
            "feishu_last_message_authorized": message_authorized,
            "feishu_last_error": sanitize_failure_text(
                runtime.get("last_error") or ""
            ),
            "feishu_pairing": pairing_status(self.db),
            # Compatibility for existing UI/API callers. P0 changes the
            # semantics from "every enabled historical account" to "at least
            # one account can generate", so one broken legacy account no longer
            # blocks the workspace.
            "core_ready": content_ready,
            "feishu_ready": feishu_ready,
            "guide": guide,
            "mode": mode,
            "current_step": current_step,
            "completed_steps": list(guide.get("completed_steps") or []),
            "force_open": bool(guide.get("force_open")),
            "wizard_required": wizard_required,
            "entrypoint": "wizard" if wizard_required else "workspace",
            "repair_step": repair_step,
        }

    def auto_check(
        self,
        *,
        model_ids: list[str] | None = None,
        refresh_wechat: bool = True,
    ) -> dict[str, Any]:
        """Explicitly retest current text models and refresh read-only WeChat health.

        Individual model failures are returned as sanitized result rows so one
        broken historical model does not prevent healthy accounts from being
        checked. Normal startup continues to call ``status()`` without probes.
        """

        return self.status(
            refresh_wechat=refresh_wechat,
            retest_models=True,
            retest_model_ids=model_ids,
        )

    def migrate_legacy_state(self) -> dict[str, Any]:
        """Persist a one-time fingerprint baseline for pre-wizard installs.

        This method is intentionally separate from ``status()`` so GET/status
        remains read-only. Desktop/API startup may invoke it once after legacy
        accounts have been imported. Only model IDs, fingerprints and
        non-secret wizard progress are stored.
        """

        raw_state = str(self.db.get_setting(ONBOARDING_SETTING_KEY) or "").strip()
        if raw_state:
            return {
                "migrated": False,
                "already_initialized": True,
                "trusted_model_ids": self._tested_model_ids(),
            }

        database_model_ids = {
            str(item.get("id") or "")
            for item in public_models(
                self.db,
                enabled_only=True,
                purpose="text",
            )
            if str(item.get("id") or "")
        }
        accounts = self.configuration.list_accounts(enabled_only=True)
        trusted_model_ids = list(
            dict.fromkeys(
                [
                    *self._legacy_trusted_model_ids(
                        accounts,
                        available_model_ids=database_model_ids,
                        require_model_time_anchor=True,
                    ),
                ]
            )
        )
        if not trusted_model_ids:
            return {
                "migrated": False,
                "already_initialized": False,
                "trusted_model_ids": [],
            }

        tested_at = _utc_now()
        state = self._load_state()
        tests = dict(state.get("model_tests") or {})
        for model_id in trusted_model_ids:
            fingerprint = self._model_fingerprint(model_id)
            if not fingerprint:
                continue
            tests[model_id] = {
                "ok": True,
                "message": "历史成功任务已验证",
                "model_fingerprint": fingerprint,
                "tested_at": tested_at,
            }
        migrated_model_ids = [
            model_id for model_id in trusted_model_ids if model_id in tests
        ]
        if not migrated_model_ids:
            return {
                "migrated": False,
                "already_initialized": False,
                "trusted_model_ids": [],
            }

        selected_account_ids = [
            str(account.get("id") or "")
            for account in accounts
            if str(account.get("model_id") or "") in set(migrated_model_ids)
        ]
        state.update(
            {
                "current_step": "wechat",
                "completed_steps": _clean_steps(
                    [*state["completed_steps"], "ai", "account"]
                ),
                "selected_model_id": migrated_model_ids[0],
                "selected_account_ids": selected_account_ids[:1],
                "model_tests": tests,
            }
        )
        self._save_state(state)
        return {
            "migrated": True,
            "already_initialized": False,
            "trusted_model_ids": migrated_model_ids,
        }

    def readiness(
        self,
        *,
        refresh_wechat: bool = False,
    ) -> dict[str, Any]:
        """Backward-compatible name for the unified onboarding status."""

        return self.status(refresh_wechat=refresh_wechat)

    def complete(self, *, mode: str = "full") -> dict[str, Any]:
        clean_mode = str(mode or "").strip().casefold()
        if clean_mode not in ONBOARDING_MODES:
            raise ValueError("向导模式无效")
        readiness = self.status()
        required_key = "draft_ready" if clean_mode == "full" else "content_ready"
        if not bool(readiness.get(required_key)):
            if clean_mode == "full":
                raise ValueError("公众号尚未通过完整发布检查，暂不能完成配置")
            raise ValueError("文章 AI 和内容目标尚未配置完成")

        state = self._load_state()
        completed_steps = list(state["completed_steps"])
        required_steps = (
            ("ai", "account", "wechat") if clean_mode == "full" else ("ai", "account")
        )
        state.update(
            {
                "mode": clean_mode,
                "current_step": "complete",
                "completed_steps": _clean_steps([*completed_steps, *required_steps]),
                "force_open": False,
                "completed_at": _utc_now(),
            }
        )
        self._save_state(state)
        return self.guide()

    def restart(self, *, mode: str = "full") -> dict[str, Any]:
        """Restart wizard progress without deleting any working configuration."""

        clean_mode = str(mode or "").strip().casefold()
        if clean_mode not in ONBOARDING_MODES:
            raise ValueError("向导模式无效")
        state = self._load_state()
        state.update(
            {
                "wizard_version": ONBOARDING_WIZARD_VERSION,
                "mode": clean_mode,
                "current_step": "welcome",
                "completed_steps": [],
                "force_open": True,
                "completed_at": None,
            }
        )
        self._save_state(state)
        return self.guide()

    def _tested_model_ids(
        self,
        *,
        state: dict[str, Any] | None = None,
        available_model_ids: set[str] | None = None,
    ) -> list[str]:
        current_state = state if state is not None else self._load_state()
        if available_model_ids is None:
            available_model_ids = {
                str(item.get("id") or "")
                for item in public_models(
                    self.db,
                    enabled_only=True,
                    purpose="text",
                )
            }
        tests = dict(current_state.get("model_tests") or {})
        auth_failed_ids = active_model_auth_failure_ids(
            self.db,
            self.config,
            state=current_state,
        )
        return [
            str(model_id)
            for model_id, value in tests.items()
            if (
                str(model_id) in available_model_ids
                and str(model_id) not in auth_failed_ids
                and bool((value or {}).get("ok"))
                and str((value or {}).get("model_fingerprint") or "")
                == self._model_fingerprint(str(model_id))
            )
        ]

    def _is_model_tested(self, model_id: str) -> bool:
        clean_model_id = str(model_id or "").strip()
        return bool(clean_model_id and clean_model_id in set(self._tested_model_ids()))

    def _retest_text_models(
        self,
        requested_model_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        available = {
            str(item.get("id") or "")
            for item in public_models(
                self.db,
                enabled_only=True,
                purpose="text",
            )
            if str(item.get("id") or "")
        }
        if requested_model_ids is not None:
            candidate_ids = _clean_list(requested_model_ids)
        else:
            state = self._load_state()
            candidate_ids = _clean_list(
                [
                    str(state.get("selected_model_id") or ""),
                    *[
                        str(account.get("model_id") or "")
                        for account in self.configuration.list_accounts(
                            enabled_only=True
                        )
                    ],
                ]
            )
            if not candidate_ids:
                candidate_ids = sorted(available)

        results: list[dict[str, Any]] = []
        for model_id in candidate_ids:
            if model_id not in available:
                results.append(
                    {
                        "model_id": model_id,
                        "ok": False,
                        "message": "所选文本模型不存在或已停用，请重新选择并测试。",
                    }
                )
                continue
            try:
                self.test_text_model(model_id)
            except Exception as exc:  # noqa: BLE001
                record_model_auth_failure_for_error(
                    self.db,
                    self.config,
                    model_id,
                    exc,
                )
                message = sanitize_failure_text(
                    friendly_model_error(exc),
                    limit=240,
                )
                self._record_model_test_failure(model_id, message)
                results.append(
                    {
                        "model_id": model_id,
                        "ok": False,
                        "message": message,
                    }
                )
            else:
                results.append(
                    {
                        "model_id": model_id,
                        "ok": True,
                        "message": "AI 连接成功，可以用于文章生成和评审。",
                    }
                )
        return results

    def _record_model_test_failure(self, model_id: str, message: str) -> None:
        state = self._load_state()
        tests = dict(state.get("model_tests") or {})
        tests[str(model_id)] = {
            "ok": False,
            "message": sanitize_failure_text(message, limit=240),
            "model_fingerprint": self._model_fingerprint(model_id),
            "tested_at": _utc_now(),
        }
        state["model_tests"] = tests
        self._save_state(state)

    def _legacy_trusted_model_ids(
        self,
        accounts: list[dict[str, Any]],
        *,
        available_model_ids: set[str],
        require_model_time_anchor: bool = True,
    ) -> list[str]:
        """Derive upgrade trust from successful historical work, read-only.

        Old installations predate model-test fingerprints. A completed article
        proves that its recorded text model worked. When old metadata omitted
        the model ID, a successful job scoped to the same still-enabled account
        may establish trust in that account's current binding.
        """

        account_bindings = {
            str(account.get("id") or ""): {
                "model_id": str(account.get("model_id") or ""),
                "updated_at": _parse_datetime(account.get("updated_at")),
            }
            for account in accounts
            if str(account.get("id") or "")
            and str(account.get("model_id") or "")
            in available_model_ids
        }
        if not account_bindings:
            return []
        bound_model_ids = {
            str(binding["model_id"]) for binding in account_bindings.values()
        }
        model_updated_at = {
            model_id: _parse_datetime(
                (self.db.get_ai_model(model_id) or {}).get("updated_at")
            )
            for model_id in bound_model_ids
        }
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT j.meta_json, j.updated_at AS job_updated_at, bj.account_id
                FROM jobs AS j
                LEFT JOIN batch_jobs AS bj ON bj.job_id = j.id
                WHERE j.status IN (
                    'ready_for_review', 'drafted', 'published'
                )
                ORDER BY j.id
                """
            ).fetchall()

        trusted: list[str] = []
        for row in rows:
            try:
                raw_meta = json.loads(str(row["meta_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                raw_meta = {}
            meta = raw_meta if isinstance(raw_meta, dict) else {}
            explicit_model_id = str(
                meta.get("selected_model_id")
                or meta.get("model_id")
                or ""
            ).strip()
            account_candidates = _clean_list(
                [
                    str(meta.get("official_account_id") or ""),
                    str(row["account_id"] or ""),
                ]
            )
            job_updated_at = _parse_datetime(row["job_updated_at"])
            for account_id in account_candidates:
                binding = account_bindings.get(account_id)
                if binding is None:
                    continue
                model_id = str(binding["model_id"])
                account_updated_at = binding["updated_at"]
                model_existed_at_success = bool(
                    not require_model_time_anchor
                    or (
                        job_updated_at
                        and model_updated_at.get(model_id)
                        and model_updated_at[model_id] <= job_updated_at
                    )
                )
                exact_model_evidence = bool(
                    explicit_model_id
                    and explicit_model_id == model_id
                    and model_existed_at_success
                )
                binding_existed_at_success = bool(
                    job_updated_at
                    and account_updated_at
                    and account_updated_at <= job_updated_at
                    and model_existed_at_success
                )
                if (
                    (exact_model_evidence or binding_existed_at_success)
                    and model_id not in trusted
                ):
                    trusted.append(model_id)
            if (
                explicit_model_id in available_model_ids
                and explicit_model_id in bound_model_ids
                and (
                    not require_model_time_anchor
                    or (
                        job_updated_at is not None
                        and model_updated_at.get(explicit_model_id) is not None
                        and model_updated_at[explicit_model_id] <= job_updated_at
                    )
                )
                and explicit_model_id not in trusted
            ):
                trusted.append(explicit_model_id)
        return trusted

    def _offline_cached_account_checks(
        self,
        accounts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        account_ids = [
            str(account.get("id") or "")
            for account in accounts
            if str(account.get("id") or "")
        ]
        if not account_ids:
            return []
        return preflight_accounts(
            self.db,
            account_ids,
            deep_model_check=False,
            force_wechat_check=False,
            allow_stale_wechat_cache=True,
        )

    def _wechat_refresh_account_ids(
        self,
        accounts: list[dict[str, Any]],
    ) -> list[str]:
        now = datetime.now(UTC)
        refresh_ids: list[str] = []
        for account in accounts:
            account_id = str(account.get("id") or "")
            cached = self.db.get_wechat_connection_health(account_id)
            status = str((cached or {}).get("status") or "").casefold()
            expires_at = _parse_datetime((cached or {}).get("expires_at"))
            if (
                cached is None
                or status == "stale"
                or expires_at is None
                or expires_at <= now
            ):
                refresh_ids.append(account_id)
        return refresh_ids

    @staticmethod
    def _decorate_account_checks(
        reports: list[dict[str, Any]],
        accounts: list[dict[str, Any]],
        tested_model_ids: set[str],
    ) -> list[dict[str, Any]]:
        reports_by_id = {
            str(item.get("account_id") or ""): dict(item) for item in reports
        }
        decorated: list[dict[str, Any]] = []
        seen: set[str] = set()
        for account in accounts:
            account_id = str(account.get("id") or "")
            seen.add(account_id)
            report = reports_by_id.get(account_id)
            checked = report is not None
            model_id = str(
                account.get("effective_model_id")
                or account.get("model_id")
                or ""
            )
            model_tested = bool(model_id and model_id in tested_model_ids)
            can_write = bool((report or {}).get("can_write"))
            local_configuration_ready = bool((report or {}).get("can_generate"))
            item = dict(report or {})
            item.update(
                {
                    "account_id": account_id,
                    "account_name": str(account.get("name") or account_id),
                    "model_id": model_id,
                    "checked": checked,
                    "configuration_can_generate": bool(
                        (report or {}).get("can_generate")
                    ),
                    "local_configuration_ready": (local_configuration_ready),
                    "model_tested": model_tested,
                    "content_ready": model_tested,
                    "can_generate": model_tested,
                    "can_write": can_write,
                    "draft_ready": bool(
                        model_tested and local_configuration_ready and can_write
                    ),
                    "checks": list((report or {}).get("checks") or []),
                }
            )
            decorated.append(item)

        # Preserve an explicit unknown/deleted ID report for callers checking a
        # stale selection. It cannot make the system ready.
        for report in reports:
            account_id = str(report.get("account_id") or "")
            if account_id in seen:
                continue
            item = dict(report)
            item.update(
                {
                    "account_id": account_id,
                    "model_tested": False,
                    "content_ready": False,
                    "can_generate": False,
                    "local_configuration_ready": False,
                    "can_write": False,
                    "draft_ready": False,
                    "checked": True,
                    "checks": list(report.get("checks") or []),
                }
            )
            decorated.append(item)
        return decorated

    def _rollback_first_account(
        self,
        *,
        account_id: str,
        existing_account: dict[str, Any] | None,
        previous_plan: dict[str, Any] | None,
        previous_review: dict[str, Any] | None,
        previous_guide: str | None,
        previous_target: str | None,
    ) -> None:
        """Best-effort compensation for a multi-service onboarding write."""

        if account_id:
            try:
                if existing_account is None:
                    self.db.delete_official_account(account_id)
                else:
                    self.db.upsert_official_account(existing_account)
                    self._restore_account_defaults(
                        account_id,
                        previous_plan=previous_plan,
                        previous_review=previous_review,
                    )
            except Exception:  # noqa: BLE001, S110
                pass
        for key, previous in (
            (ONBOARDING_SETTING_KEY, previous_guide),
            ("ui.last_target_account_ids", previous_target),
        ):
            try:
                self._restore_setting(key, previous)
            except Exception:  # noqa: BLE001, S110
                pass

    def _restore_account_defaults(
        self,
        account_id: str,
        *,
        previous_plan: dict[str, Any] | None,
        previous_review: dict[str, Any] | None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM account_creation_plan_defaults WHERE account_id = ?",
                (account_id,),
            )
            if previous_plan is not None:
                conn.execute(
                    """
                    INSERT INTO account_creation_plan_defaults (
                        account_id, creation_plan_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        previous_plan["creation_plan_id"],
                        previous_plan["created_at"],
                        previous_plan["updated_at"],
                    ),
                )
            conn.execute(
                "DELETE FROM account_editorial_review_defaults WHERE account_id = ?",
                (account_id,),
            )
            if previous_review is not None:
                conn.execute(
                    """
                    INSERT INTO account_editorial_review_defaults (
                        account_id, profile_id, config_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        previous_review.get("profile_id"),
                        previous_review.get("config_json") or "{}",
                        previous_review["created_at"],
                        previous_review["updated_at"],
                    ),
                )

    def _restore_setting(self, key: str, previous: str | None) -> None:
        if previous is not None:
            self.db.set_setting(key, previous)
            return
        with self.db.connect() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))

    def _preset(self, preset_id: str) -> dict[str, Any]:
        try:
            return TEXT_MODEL_PRESETS[str(preset_id)]
        except KeyError as exc:
            raise ValueError("不支持的大模型厂商") from exc

    def _model_fingerprint(self, model_id: str) -> str:
        return model_fingerprint(self.db, self.config, model_id)

    def _load_state(self) -> dict[str, Any]:
        raw = self.db.get_setting(ONBOARDING_SETTING_KEY)
        if not raw:
            state = _normalize_state({})
            # Legacy installations have no guide record. Mirror their actual
            # relay/direct configuration in memory without writing a guide as a
            # side effect of status checks. Brand-new installs retain the
            # recommended relay default.
            if self.db.list_official_accounts():
                state["connection_mode"] = self._configured_connection_mode()
            return state
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return _normalize_state({})
        return _normalize_state(value if isinstance(value, dict) else {})

    def _configured_connection_mode(self) -> str:
        fallback = self.config.get("wechat_relay")
        if not isinstance(fallback, dict):
            fallback = self.config.get("wechat_proxy")
        try:
            settings = effective_wechat_relay_settings(
                self.db,
                fallback if isinstance(fallback, dict) else None,
            )
            return "relay" if bool(settings.get("enabled", False)) else "direct"
        except Exception:  # noqa: BLE001
            return (
                "relay"
                if isinstance(fallback, dict)
                and bool(fallback.get("enabled", False))
                else "direct"
            )

    def _save_state(self, value: dict[str, Any]) -> None:
        state = _normalize_state(value)
        state["updated_at"] = _utc_now()
        self.db.set_setting(
            ONBOARDING_SETTING_KEY,
            json.dumps(state, ensure_ascii=False),
        )


def _normalize_state(value: dict[str, Any]) -> dict[str, Any]:
    raw = dict(value or {})
    raw_mode = str(raw.get("mode") or "full").strip().casefold()
    mode = raw_mode if raw_mode in ONBOARDING_MODES else "full"
    raw_step = str(raw.get("current_step") or "welcome").strip().casefold()
    current_step = raw_step if raw_step in ONBOARDING_STEPS else "welcome"
    connection = str(raw.get("connection_mode") or "relay").strip().casefold()

    raw_model_ids = raw.get("model_ids")
    model_ids = (
        {
            str(key): str(model_id)
            for key, model_id in raw_model_ids.items()
            if str(key).strip() and str(model_id).strip()
        }
        if isinstance(raw_model_ids, dict)
        else {}
    )
    raw_tests = raw.get("model_tests")
    model_tests: dict[str, dict[str, Any]] = {}
    if isinstance(raw_tests, dict):
        for model_id, test in raw_tests.items():
            if not str(model_id).strip() or not isinstance(test, dict):
                continue
            test_ok = bool(test.get("ok"))
            model_tests[str(model_id)] = {
                "ok": test_ok,
                "message": (
                    "连接成功"
                    if test_ok
                    else sanitize_failure_text(
                        test.get("message") or "文本模型验证失败，请重新测试。",
                        limit=240,
                    )
                ),
                "model_fingerprint": str(test.get("model_fingerprint") or ""),
                "tested_at": str(test.get("tested_at") or ""),
            }

    raw_feishu = raw.get("feishu_credentials_test")
    feishu_test: dict[str, Any] = {}
    if isinstance(raw_feishu, dict) and raw_feishu:
        feishu_test = {
            "ok": bool(raw_feishu.get("ok")),
            "app_id": str(raw_feishu.get("app_id") or ""),
            "credential_fingerprint": str(
                raw_feishu.get("credential_fingerprint") or ""
            ),
            "tested_at": str(raw_feishu.get("tested_at") or ""),
        }

    raw_auth_failures = raw.get("model_auth_failures")
    model_auth_failures: dict[str, dict[str, str]] = {}
    if isinstance(raw_auth_failures, dict):
        for model_id, failure in raw_auth_failures.items():
            if not str(model_id).strip() or not isinstance(failure, dict):
                continue
            fingerprint = str(failure.get("model_fingerprint") or "").strip()
            failed_at = str(failure.get("failed_at") or "").strip()
            if not fingerprint:
                continue
            model_auth_failures[str(model_id)] = {
                "model_fingerprint": fingerprint,
                "failed_at": failed_at,
            }

    completed_at = raw.get("completed_at")
    return {
        "wizard_version": ONBOARDING_WIZARD_VERSION,
        "mode": mode,
        "current_step": current_step,
        "completed_steps": _clean_steps(raw.get("completed_steps")),
        "selected_model_id": str(raw.get("selected_model_id") or "").strip(),
        "selected_account_ids": _clean_list(
            raw.get("selected_account_ids")
            if isinstance(raw.get("selected_account_ids"), list)
            else []
        ),
        "connection_mode": (
            connection if connection in {"relay", "direct"} else "relay"
        ),
        "force_open": bool(raw.get("force_open", False)),
        "completed_at": (str(completed_at) if completed_at not in {None, ""} else None),
        "updated_at": str(raw.get("updated_at") or ""),
        "model_ids": model_ids,
        "model_tests": model_tests,
        "model_auth_failures": model_auth_failures,
        "feishu_credentials_test": feishu_test,
    }


def _validate_step(value: str) -> str:
    step = str(value or "").strip().casefold()
    if step not in ONBOARDING_STEPS:
        raise ValueError("向导步骤无效")
    return step


def _clean_steps(values: Any) -> list[str]:
    return list(
        dict.fromkeys(
            str(item).strip().casefold()
            for item in values or []
            if str(item).strip().casefold() in ONBOARDING_STEPS
        )
    )


def _clean_list(values: list[str] | None) -> list[str]:
    return list(
        dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip())
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _credential_fingerprint(app_id: str, app_secret: str) -> str:
    clean_app_id = str(app_id or "").strip()
    clean_secret = str(app_secret or "").strip()
    if not clean_app_id or not clean_secret:
        return ""
    return hashlib.sha256(f"{clean_app_id}\0{clean_secret}".encode()).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_at_or_after(value: Any, reference: Any) -> bool:
    parsed = _parse_datetime(value)
    reference_parsed = _parse_datetime(reference)
    return bool(parsed and reference_parsed and parsed >= reference_parsed)


__all__ = [
    "FEISHU_TOKEN_URL",
    "ONBOARDING_MODES",
    "ONBOARDING_SETTING_KEY",
    "ONBOARDING_STEPS",
    "ONBOARDING_WIZARD_VERSION",
    "TEXT_MODEL_PRESETS",
    "OnboardingService",
]
