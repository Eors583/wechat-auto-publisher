from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import Database
from app.feishu.settings import public_feishu_settings, save_feishu_settings


PAIRING_SETTING_KEY = "feishu.pairing"
PAIRING_PREFIX = "绑定"
PAIRING_HASH_ITERATIONS = 120_000
_PAIRING_LOCK = threading.RLock()


def create_pairing_code(
    db: Database,
    *,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    with _PAIRING_LOCK:
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=max(5, int(ttl_minutes))
        )
        value = {
            "salt": salt,
            "code_hash": _code_hash(
                code,
                salt,
                iterations=PAIRING_HASH_ITERATIONS,
            ),
            "hash_algorithm": "pbkdf2_sha256",
            "hash_iterations": PAIRING_HASH_ITERATIONS,
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used_at": "",
        }
        db.set_setting(
            PAIRING_SETTING_KEY,
            json.dumps(value, ensure_ascii=False),
        )
    return {
        "code": code,
        "message": f"{PAIRING_PREFIX}{code}",
        "expires_at": value["expires_at"],
    }


def consume_pairing_code(
    db: Database,
    *,
    text: str,
    open_id: str,
    chat_id: str = "",
) -> bool:
    # Message events are handled on independent threads. Keep verification,
    # allow-list persistence and invalidation in one process-wide critical
    # section so the same six-digit code cannot be consumed twice.
    with _PAIRING_LOCK:
        value = _load(db)
        if not value or value.get("used_at"):
            return False
        try:
            expires_at = datetime.fromisoformat(
                str(value.get("expires_at") or "")
            )
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            return False
        supplied = _extract_code(text)
        if not supplied or not _matches_code(value, supplied):
            return False
        clean_open_id = str(open_id or "").strip()
        if not clean_open_id:
            return False
        settings = public_feishu_settings(db)
        allowed_open_ids = list(settings.get("allowed_open_ids") or [])
        if clean_open_id not in allowed_open_ids:
            allowed_open_ids.append(clean_open_id)
        save_feishu_settings(
            db,
            enabled=bool(settings.get("enabled")),
            app_id=str(settings.get("app_id") or ""),
            app_secret=None,
            allow_all=False,
            allowed_open_ids=allowed_open_ids,
            allowed_chat_ids=list(settings.get("allowed_chat_ids") or []),
            default_account_ids=list(
                settings.get("default_account_ids") or []
            ),
            agent_model_id=str(settings.get("agent_model_id") or ""),
        )
        value["used_at"] = datetime.now(timezone.utc).isoformat()
        value["bound_open_id"] = clean_open_id
        value["bound_chat_id"] = str(chat_id or "").strip()
        db.set_setting(
            PAIRING_SETTING_KEY,
            json.dumps(value, ensure_ascii=False),
        )
        return True


def pairing_status(db: Database) -> dict[str, Any]:
    value = _load(db)
    if not value:
        return {"status": "none"}
    if value.get("used_at"):
        return {
            "status": "used",
            "used_at": str(value.get("used_at") or ""),
            "bound_open_id": str(value.get("bound_open_id") or ""),
        }
    try:
        expires_at = datetime.fromisoformat(
            str(value.get("expires_at") or "")
        )
    except ValueError:
        return {"status": "invalid"}
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return {
        "status": (
            "expired"
            if datetime.now(timezone.utc) > expires_at
            else "waiting"
        ),
        "expires_at": expires_at.isoformat(),
    }


def _extract_code(text: str) -> str:
    clean = str(text or "").strip().replace(" ", "")
    if clean.startswith(PAIRING_PREFIX):
        clean = clean[len(PAIRING_PREFIX) :]
    return clean if len(clean) == 6 and clean.isdigit() else ""


def _code_hash(code: str, salt: str, *, iterations: int) -> str:
    try:
        salt_bytes = bytes.fromhex(salt)
    except ValueError:
        salt_bytes = salt.encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt_bytes,
        max(1, int(iterations)),
    ).hex()


def _matches_code(value: dict[str, Any], supplied: str) -> bool:
    salt = str(value.get("salt") or "")
    expected = str(value.get("code_hash") or "")
    if not salt or not expected:
        return False
    if str(value.get("hash_algorithm") or "") == "pbkdf2_sha256":
        try:
            iterations = int(
                value.get("hash_iterations") or PAIRING_HASH_ITERATIONS
            )
        except (TypeError, ValueError):
            return False
        actual = _code_hash(supplied, salt, iterations=iterations)
    else:
        # A short-lived compatibility path for pairing codes created by the
        # first local prototype before PBKDF2 was introduced.
        actual = hashlib.sha256(
            f"{salt}:{supplied}".encode("utf-8")
        ).hexdigest()
    return hmac.compare_digest(actual, expected)


def _load(db: Database) -> dict[str, Any]:
    raw = db.get_setting(PAIRING_SETTING_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


__all__ = [
    "PAIRING_PREFIX",
    "PAIRING_HASH_ITERATIONS",
    "PAIRING_SETTING_KEY",
    "consume_pairing_code",
    "create_pairing_code",
    "pairing_status",
]
