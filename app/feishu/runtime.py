from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db import Database


SETTING_KEY = "feishu.runtime"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_runtime(db: Database) -> dict[str, Any]:
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return {"status": "stopped"}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"status": "unknown"}
    except json.JSONDecodeError:
        return {"status": "unknown", "last_error": "运行状态数据损坏"}


def update_runtime(db: Database, **changes: Any) -> dict[str, Any]:
    try:
        value = get_runtime(db)
    except Exception:  # runtime telemetry must never interrupt bot messages
        value = {"status": "unknown"}
    value.update(changes)
    value["updated_at"] = utc_now()
    try:
        db.set_setting(SETTING_KEY, json.dumps(value, ensure_ascii=False))
    except Exception:
        pass
    return value
