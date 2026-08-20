from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db import Database
from app.services.failures import sanitize_failure_text


SETTING_KEY = "feishu.runtime"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_runtime(db: Database, *, integration_id: str = "") -> dict[str, Any]:
    if integration_id:
        row = db.get_feishu_integration()
        if not row or str(row.get("id") or "") != str(integration_id):
            return {"status": "stopped"}
        raw = str(row.get("runtime_json") or "")
        if not raw:
            return {"status": "stopped"}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {"status": "unknown"}
        except json.JSONDecodeError:
            return {"status": "unknown", "last_error": "运行状态数据损坏"}
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return {"status": "stopped"}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"status": "unknown"}
    except json.JSONDecodeError:
        return {"status": "unknown", "last_error": "运行状态数据损坏"}


def update_runtime(
    db: Database, *, integration_id: str = "", **changes: Any
) -> dict[str, Any]:
    if "last_error" in changes:
        changes["last_error"] = sanitize_failure_text(changes["last_error"])
    try:
        value = get_runtime(db, integration_id=integration_id)
    except Exception:  # runtime telemetry must never interrupt bot messages
        value = {"status": "unknown"}
    value.update(changes)
    value["updated_at"] = utc_now()
    if integration_id:
        try:
            return db.update_feishu_runtime(integration_id, value)
        except Exception:
            return value
    try:
        db.set_setting(SETTING_KEY, json.dumps(value, ensure_ascii=False))
    except Exception:
        pass
    return value
