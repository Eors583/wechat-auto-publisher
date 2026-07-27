from __future__ import annotations

import json
from typing import Any

from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.db import Database


SETTING_KEY = "wechat_backend_search"


def public_backend_settings(db: Database) -> dict[str, Any]:
    stored = _load(db)
    return {
        "enabled": bool(stored.get("enabled", False)),
        "has_token": bool(stored.get("token_encrypted")),
        "has_cookie": bool(stored.get("cookie_encrypted")),
        "session_label": str(stored.get("session_label") or ""),
    }


def save_backend_settings(
    db: Database,
    *,
    enabled: bool,
    token: str | None = None,
    cookie: str | None = None,
    session_label: str = "",
) -> None:
    current = _load(db)
    token_value = str(token or "").strip()
    cookie_value = str(cookie or "").strip()
    if token_value:
        current["token_encrypted"] = encrypt_api_key(token_value)
    if cookie_value:
        current["cookie_encrypted"] = encrypt_api_key(cookie_value)
    current["enabled"] = bool(enabled)
    current["session_label"] = str(session_label or "").strip()
    if enabled and not current.get("token_encrypted"):
        raise ValueError("启用公众号后台搜索时必须填写 Token")
    if enabled and not current.get("cookie_encrypted"):
        raise ValueError("启用公众号后台搜索时必须填写 Cookie")
    db.set_setting(SETTING_KEY, json.dumps(current, ensure_ascii=False))


def effective_backend_settings(db: Database) -> dict[str, Any]:
    stored = _load(db)
    token_encrypted = str(stored.get("token_encrypted") or "")
    cookie_encrypted = str(stored.get("cookie_encrypted") or "")
    return {
        "enabled": bool(stored.get("enabled", False)),
        "token": decrypt_api_key(token_encrypted) if token_encrypted else "",
        "cookie": decrypt_api_key(cookie_encrypted) if cookie_encrypted else "",
        "session_label": str(stored.get("session_label") or ""),
    }


def clear_backend_session(db: Database) -> None:
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
