from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.ai.model_registry import (
    GEMINI,
    MANUS,
    OPENAI_COMPATIBLE,
    configured_models,
    public_models,
)
from app.ai.image_providers import is_image_provider
from app.config import load_config
from app.db import Database
from app.feishu.pairing import create_pairing_code, pairing_status
from app.feishu.runtime import get_runtime
from app.feishu.settings import (
    effective_feishu_settings,
    public_feishu_settings,
    save_feishu_settings,
)
from app.services.configuration import ConfigurationService


ONBOARDING_SETTING_KEY = "onboarding.guide"
FEISHU_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/"
    "tenant_access_token/internal"
)


# These are application presets, not credentials. Built-in presets keep new
# operators away from protocol names, endpoint paths and model identifiers.
TEXT_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek（推荐，国内使用简单）",
        "provider_type": OPENAI_COMPATIBLE,
        "api_base": "https://api.deepseek.com",
        "models": ("deepseek-v4-flash", "deepseek-v4-pro"),
        "default_model": "deepseek-v4-flash",
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
        selected_base = str(
            api_base if preset_id == "custom" else preset["api_base"]
        ).strip()
        if not selected_model:
            raise ValueError("请填写模型名称")
        if preset_id != "custom" and selected_model not in preset["models"]:
            raise ValueError("请选择该厂商预设的模型")
        if preset_id == "custom" and not selected_base:
            raise ValueError("自定义接口必须填写 API Base URL")
        state = self._load_state()
        existing_id = str(
            (state.get("model_ids") or {}).get(preset_id) or ""
        )
        if existing_id and not self.db.get_ai_model(existing_id):
            existing_id = ""
        saved = self.configuration.save_model(
            model_id=existing_id or None,
            name=str(display_name or "").strip()
            or f'{str(preset["label"]).split("（", 1)[0]} · {selected_model}',
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
        state = self._load_state()
        tests = dict(state.get("model_tests") or {})
        tests[clean_model_id] = {
            "ok": True,
            "message": str(result.get("message") or "连接成功"),
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
                str(item).strip()
                for item in account_ids
                if str(item).strip()
            )
        )
        if not clean_ids:
            raise ValueError("请至少选择一个公众号")
        return [
            self.configuration.bind_account_model(account_id, model_id)
            for account_id in clean_ids
        ]

    def test_feishu_credentials(
        self,
        *,
        app_id: str,
        app_secret: str | None = None,
        post: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        effective = effective_feishu_settings(self.db)
        clean_app_id = str(app_id or effective.get("app_id") or "").strip()
        clean_secret = str(
            app_secret or effective.get("app_secret") or ""
        ).strip()
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
            raise RuntimeError(
                "无法连接飞书开放平台，请检查本机网络后重试"
            ) from exc
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
            (self._load_state().get("model_tests") or {}).get(
                str(agent_model_id or "")
            )
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

    def readiness(self) -> dict[str, Any]:
        state = self._load_state()
        db_models = public_models(
            self.db, enabled_only=True, purpose="text"
        )
        config_models = configured_models(self.config)
        model_ids = {
            str(item.get("id") or "")
            for item in [*config_models, *db_models]
        }
        model_tests = dict(state.get("model_tests") or {})
        tested_model_ids = [
            model_id
            for model_id, value in model_tests.items()
            if (
                model_id in model_ids
                and bool((value or {}).get("ok"))
                and str((value or {}).get("model_fingerprint") or "")
                == self._model_fingerprint(model_id)
            )
        ]
        accounts = self.configuration.list_accounts(enabled_only=True)
        bound_accounts = [
            item
            for item in accounts
            if str(item.get("model_id") or "") in model_ids
        ]
        feishu = public_feishu_settings(self.db)
        effective_feishu = effective_feishu_settings(self.db)
        runtime = get_runtime(self.db)
        credentials_test = dict(
            state.get("feishu_credentials_test") or {}
        )
        credentials_ok = bool(
            credentials_test.get("ok")
        ) and str(
            credentials_test.get("credential_fingerprint") or ""
        ) == _credential_fingerprint(
            str(effective_feishu.get("app_id") or ""),
            str(effective_feishu.get("app_secret") or ""),
        )
        runtime_is_current = bool(
            runtime.get("app_id")
            and str(runtime.get("app_id") or "")
            == str(feishu.get("app_id") or "")
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
        all_accounts_bound = bool(accounts) and (
            len(bound_accounts) == len(accounts)
        )
        tested_model_id_set = set(tested_model_ids)
        all_account_models_tested = all_accounts_bound and all(
            str(item.get("model_id") or "") in tested_model_id_set
            for item in accounts
        )
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
        return {
            "has_text_model": bool(model_ids),
            "tested_model_ids": tested_model_ids,
            "model_tested": bool(tested_model_ids),
            "account_count": len(accounts),
            "bound_account_count": len(bound_accounts),
            "accounts_bound": all_accounts_bound,
            "account_models_tested": all_account_models_tested,
            "feishu_saved": feishu_saved,
            "feishu_credentials_tested": credentials_ok,
            "feishu_runtime_status": str(
                runtime.get("status") or "stopped"
            ),
            "feishu_last_message_at": runtime.get("last_message_at"),
            "feishu_last_reply_at": runtime.get("last_reply_at"),
            "feishu_last_message_authorized": message_authorized,
            "feishu_last_error": str(runtime.get("last_error") or ""),
            "feishu_pairing": pairing_status(self.db),
            "core_ready": all_account_models_tested,
            "feishu_ready": feishu_ready,
        }

    def _preset(self, preset_id: str) -> dict[str, Any]:
        try:
            return TEXT_MODEL_PRESETS[str(preset_id)]
        except KeyError as exc:
            raise ValueError("不支持的大模型厂商") from exc

    def _model_fingerprint(self, model_id: str) -> str:
        clean_model_id = str(model_id or "").strip()
        record = self.db.get_ai_model(clean_model_id)
        if record:
            material = {
                "id": clean_model_id,
                "provider_type": record.get("provider_type"),
                "api_base": record.get("api_base"),
                "model": record.get("model"),
                "api_key_encrypted": record.get("api_key_encrypted"),
                "enabled": bool(record.get("enabled")),
            }
        else:
            item = next(
                (
                    candidate
                    for candidate in configured_models(self.config)
                    if str(candidate.get("id") or "") == clean_model_id
                ),
                None,
            )
            if not item:
                return ""
            provider_id = clean_model_id.removeprefix("config:")
            provider = dict(
                (self.config.get("ai") or {}).get(provider_id) or {}
            )
            material = {
                "id": clean_model_id,
                "provider_type": item.get("provider_type"),
                "api_base": item.get("api_base"),
                "model": item.get("model"),
                "api_key": provider.get("api_key"),
                "enabled": True,
            }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _load_state(self) -> dict[str, Any]:
        raw = self.db.get_setting(ONBOARDING_SETTING_KEY)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _save_state(self, value: dict[str, Any]) -> None:
        self.db.set_setting(
            ONBOARDING_SETTING_KEY,
            json.dumps(value, ensure_ascii=False),
        )


def _clean_list(values: list[str] | None) -> list[str]:
    return list(
        dict.fromkeys(
            str(item).strip()
            for item in values or []
            if str(item).strip()
        )
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _credential_fingerprint(app_id: str, app_secret: str) -> str:
    clean_app_id = str(app_id or "").strip()
    clean_secret = str(app_secret or "").strip()
    if not clean_app_id or not clean_secret:
        return ""
    return hashlib.sha256(
        f"{clean_app_id}\0{clean_secret}".encode("utf-8")
    ).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_at_or_after(value: Any, reference: Any) -> bool:
    parsed = _parse_datetime(value)
    reference_parsed = _parse_datetime(reference)
    return bool(
        parsed
        and reference_parsed
        and parsed >= reference_parsed
    )


__all__ = [
    "FEISHU_TOKEN_URL",
    "ONBOARDING_SETTING_KEY",
    "OnboardingService",
    "TEXT_MODEL_PRESETS",
]
