from __future__ import annotations

import json
from typing import Any

from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.db import Database


SETTING_KEY = "feishu_integration"
SENSITIVE_FIELDS = ("app_secret", "verification_token", "encrypt_key")


def public_feishu_settings(db: Database) -> dict[str, Any]:
    stored = _load(db)
    return {
        "enabled": bool(stored.get("enabled", False)),
        "app_id": str(stored.get("app_id") or ""),
        "has_app_secret": bool(stored.get("app_secret_encrypted")),
        "has_verification_token": bool(stored.get("verification_token_encrypted")),
        "has_encrypt_key": bool(stored.get("encrypt_key_encrypted")),
        "allow_all": bool(stored.get("allow_all", False)),
        "allowed_open_ids": list(stored.get("allowed_open_ids") or []),
        "allowed_chat_ids": list(stored.get("allowed_chat_ids") or []),
        "default_account_ids": list(stored.get("default_account_ids") or []),
        "agent_model_id": str(stored.get("agent_model_id") or ""),
    }


def save_feishu_settings(
    db: Database,
    *,
    enabled: bool,
    app_id: str,
    app_secret: str | None = None,
    verification_token: str | None = None,
    encrypt_key: str | None = None,
    clear_event_security: bool = False,
    allow_all: bool = False,
    allowed_open_ids: list[str] | None = None,
    allowed_chat_ids: list[str] | None = None,
    default_account_ids: list[str] | None = None,
    agent_model_id: str | None = None,
) -> None:
    current = _load(db)
    app_id = app_id.strip()
    if enabled and not app_id:
        raise ValueError("启用飞书机器人时 App ID 不能为空")
    current.update(
        {
            "enabled": bool(enabled),
            "app_id": app_id,
            "allow_all": bool(allow_all),
            "allowed_open_ids": _clean_list(allowed_open_ids),
            "allowed_chat_ids": _clean_list(allowed_chat_ids),
            "default_account_ids": _clean_list(default_account_ids),
        }
    )
    if agent_model_id is not None:
        current["agent_model_id"] = str(agent_model_id or "").strip()
    supplied = {
        "app_secret": app_secret,
        "verification_token": verification_token,
        "encrypt_key": encrypt_key,
    }
    if clear_event_security:
        current.pop("verification_token_encrypted", None)
        current.pop("encrypt_key_encrypted", None)
    for field in SENSITIVE_FIELDS:
        if clear_event_security and field in {"verification_token", "encrypt_key"}:
            continue
        value = str(supplied[field] or "").strip()
        if value:
            current[f"{field}_encrypted"] = encrypt_api_key(value)
    if enabled and not current.get("app_secret_encrypted"):
        raise ValueError("启用飞书机器人时 App Secret 不能为空")
    db.set_setting(SETTING_KEY, json.dumps(current, ensure_ascii=False))


def effective_feishu_settings(
    db: Database,
    config_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stored = _load(db)
    if not stored:
        fallback = dict(config_fallback or {})
        # Versions before 1.1.1 shipped ``config:moonshot`` as a sample
        # default.  An upgrader preserves the user's existing config.yaml, so
        # treating that legacy sample as a real selection would silently bind
        # the Feishu agent to Kimi.  Only an explicit database selection is
        # authoritative now.
        if str(fallback.get("agent_model_id") or "") == "config:moonshot":
            fallback["agent_model_id"] = ""
            fallback["enabled"] = False
        return fallback
    result = {
        "enabled": bool(stored.get("enabled", False)),
        "app_id": str(stored.get("app_id") or ""),
        "allow_all": bool(stored.get("allow_all", False)),
        "allowed_open_ids": list(stored.get("allowed_open_ids") or []),
        "allowed_chat_ids": list(stored.get("allowed_chat_ids") or []),
        "default_account_ids": list(stored.get("default_account_ids") or []),
        "agent_model_id": str(stored.get("agent_model_id") or ""),
    }
    for field in SENSITIVE_FIELDS:
        encrypted = str(stored.get(f"{field}_encrypted") or "")
        result[field] = decrypt_api_key(encrypted) if encrypted else ""
    return result


def _load(db: Database) -> dict[str, Any]:
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _clean_list(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip()))
