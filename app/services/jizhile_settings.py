from __future__ import annotations

import json
from typing import Any

from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.db import Database

SETTING_KEY = "jizhile_api"


def public_jizhile_settings(db: Database) -> dict[str, Any]:
    stored = _load(db)
    return {
        "enabled": bool(stored.get("enabled", False)),
        "has_key": bool(stored.get("key_encrypted")),
        "has_verifycode": bool(stored.get("verifycode_encrypted")),
        "session_label": str(stored.get("session_label") or ""),
        "remain_money": stored.get("remain_money"),
        "checked_at": str(stored.get("checked_at") or ""),
    }


def save_jizhile_settings(
    db: Database,
    *,
    enabled: bool,
    key: str | None = None,
    verifycode: str | None = None,
    session_label: str = "",
    remain_money: Any = None,
    checked_at: str = "",
) -> None:
    current = _load(db)
    key_value = str(key or "").strip()
    verifycode_value = str(verifycode or "").strip()
    if key_value:
        current["key_encrypted"] = encrypt_api_key(key_value)
    if verifycode_value:
        current["verifycode_encrypted"] = encrypt_api_key(verifycode_value)
    current["enabled"] = bool(enabled)
    current["session_label"] = str(session_label or "").strip()
    if remain_money is not None:
        current["remain_money"] = remain_money
    if checked_at:
        current["checked_at"] = str(checked_at)
    if enabled and not current.get("key_encrypted"):
        raise ValueError("启用极致了 API 时必须填写 API Key")
    db.set_setting(SETTING_KEY, json.dumps(current, ensure_ascii=False))


def effective_jizhile_settings(db: Database) -> dict[str, Any]:
    stored = _load(db)
    key_encrypted = str(stored.get("key_encrypted") or "")
    verifycode_encrypted = str(stored.get("verifycode_encrypted") or "")
    return {
        "enabled": bool(stored.get("enabled", False)),
        "key": decrypt_api_key(key_encrypted) if key_encrypted else "",
        "verifycode": (
            decrypt_api_key(verifycode_encrypted) if verifycode_encrypted else ""
        ),
        "session_label": str(stored.get("session_label") or ""),
    }


def clear_jizhile_settings(db: Database) -> None:
    db.set_setting(
        SETTING_KEY,
        json.dumps(
            {
                "enabled": False,
                "session_label": "",
            },
            ensure_ascii=False,
        ),
    )


def _load(db: Database) -> dict[str, Any]:
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
