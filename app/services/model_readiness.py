from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from app.ai.model_registry import configured_models
from app.db import Database
from app.services.failures import sanitize_failure_text

ONBOARDING_SETTING_KEY = "onboarding.guide"
_AUTH_STATUS = re.compile(r"(?<!\d)(?:401|403)(?!\d)")
_SIMPLE_GUIDE_KEYS = (
    "wizard_version",
    "mode",
    "current_step",
    "completed_steps",
    "selected_model_id",
    "selected_account_ids",
    "connection_mode",
    "force_open",
    "completed_at",
)


def model_fingerprint(
    db: Database,
    config: dict[str, Any],
    model_id: str,
) -> str:
    """Fingerprint the exact credential-bearing model configuration."""

    clean_model_id = str(model_id or "").strip()
    record = db.get_ai_model(clean_model_id)
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
                for candidate in configured_models(config)
                if str(candidate.get("id") or "") == clean_model_id
            ),
            None,
        )
        if not item:
            return ""
        provider_id = clean_model_id.removeprefix("config:")
        provider = dict((config.get("ai") or {}).get(provider_id) or {})
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
    return hashlib.sha256(encoded.encode()).hexdigest()


def active_model_auth_failure_ids(
    db: Database,
    config: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
) -> set[str]:
    current_state = state if state is not None else _load_state(db)
    failures = current_state.get("model_auth_failures")
    if not isinstance(failures, dict):
        return set()
    return {
        str(model_id)
        for model_id, failure in failures.items()
        if (
            isinstance(failure, dict)
            and str(failure.get("model_fingerprint") or "")
            == model_fingerprint(db, config, str(model_id))
            and bool(str(failure.get("model_fingerprint") or ""))
        )
    }


def mark_model_auth_failure(
    db: Database,
    config: dict[str, Any],
    model_id: str,
    *,
    failed_at: str | None = None,
) -> dict[str, str] | None:
    clean_model_id = str(model_id or "").strip()
    fingerprint = model_fingerprint(db, config, clean_model_id)
    if not clean_model_id or not fingerprint:
        return None
    state = _safe_state(_load_state(db))
    failures = dict(state.get("model_auth_failures") or {})
    failure = {
        "model_fingerprint": fingerprint,
        "failed_at": str(failed_at or _utc_now()),
    }
    failures[clean_model_id] = failure
    state["model_auth_failures"] = failures
    state["updated_at"] = _utc_now()
    _save_state(db, state)
    return dict(failure)


def clear_model_auth_failure(
    db: Database,
    model_id: str,
) -> bool:
    clean_model_id = str(model_id or "").strip()
    state = _safe_state(_load_state(db))
    failures = dict(state.get("model_auth_failures") or {})
    existed = clean_model_id in failures
    failures.pop(clean_model_id, None)
    state["model_auth_failures"] = failures
    if existed:
        state["updated_at"] = _utc_now()
        _save_state(db, state)
    return existed


def record_model_auth_failure_for_error(
    db: Database,
    config: dict[str, Any],
    model_id: str,
    error: BaseException,
) -> bool:
    """Persist a current-fingerprint failure only for model HTTP 401/403."""

    if not _is_auth_error(error):
        return False
    return mark_model_auth_failure(db, config, model_id) is not None


def _is_auth_error(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status_code = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        response_code = getattr(response, "status_code", None)
        if status_code in {401, 403} or response_code in {401, 403}:
            return True
        if _AUTH_STATUS.search(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _load_state(db: Database) -> dict[str, Any]:
    raw = db.get_setting(ONBOARDING_SETTING_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _safe_state(value: dict[str, Any]) -> dict[str, Any]:
    raw = dict(value or {})
    state = {
        key: raw[key]
        for key in _SIMPLE_GUIDE_KEYS
        if key in raw
    }
    raw_model_ids = raw.get("model_ids")
    state["model_ids"] = (
        {
            str(key): str(model_id)
            for key, model_id in raw_model_ids.items()
            if str(key).strip() and str(model_id).strip()
        }
        if isinstance(raw_model_ids, dict)
        else {}
    )
    raw_tests = raw.get("model_tests")
    state["model_tests"] = (
        {
            str(model_id): {
                "ok": bool(test.get("ok")),
                "message": (
                    "连接成功"
                    if bool(test.get("ok"))
                    else sanitize_failure_text(
                        test.get("message") or "文本模型验证失败，请重新测试。",
                        limit=240,
                    )
                ),
                "model_fingerprint": str(
                    test.get("model_fingerprint") or ""
                ),
                "tested_at": str(test.get("tested_at") or ""),
            }
            for model_id, test in raw_tests.items()
            if str(model_id).strip() and isinstance(test, dict)
        }
        if isinstance(raw_tests, dict)
        else {}
    )
    raw_feishu = raw.get("feishu_credentials_test")
    state["feishu_credentials_test"] = (
        {
            "ok": bool(raw_feishu.get("ok")),
            "app_id": str(raw_feishu.get("app_id") or ""),
            "credential_fingerprint": str(
                raw_feishu.get("credential_fingerprint") or ""
            ),
            "tested_at": str(raw_feishu.get("tested_at") or ""),
        }
        if isinstance(raw_feishu, dict)
        else {}
    )
    raw_failures = raw.get("model_auth_failures")
    state["model_auth_failures"] = (
        {
            str(model_id): {
                "model_fingerprint": str(
                    failure.get("model_fingerprint") or ""
                ),
                "failed_at": str(failure.get("failed_at") or ""),
            }
            for model_id, failure in raw_failures.items()
            if str(model_id).strip() and isinstance(failure, dict)
        }
        if isinstance(raw_failures, dict)
        else {}
    )
    return state


def _save_state(db: Database, state: dict[str, Any]) -> None:
    db.set_setting(
        ONBOARDING_SETTING_KEY,
        json.dumps(state, ensure_ascii=False),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "active_model_auth_failure_ids",
    "clear_model_auth_failure",
    "mark_model_auth_failure",
    "model_fingerprint",
    "record_model_auth_failure_for_error",
]
