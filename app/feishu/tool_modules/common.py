from __future__ import annotations

import re
from typing import Any


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "是", "启用", "开启"}:
        return True
    if normalized in {"0", "false", "no", "off", "否", "停用", "关闭"}:
        return False
    return None


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[,，、;；\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    return list(
        dict.fromkeys(str(item).strip() for item in values if str(item).strip())
    )


def explicit_confirmation(
    original_text: str,
    *phrases: str,
    argument_confirmed: Any = None,
) -> bool:
    """Require a concrete confirmation phrase for costly or destructive tools."""

    if argument_confirmed is True:
        return True
    normalized = re.sub(r"\s+", "", original_text or "")
    return any(re.sub(r"\s+", "", phrase) in normalized for phrase in phrases)


def batch_id_from(args: dict[str, Any], current_batch_id: str | None) -> str:
    return str(args.get("batch_id") or current_batch_id or "").strip()


def require_job_id(args: dict[str, Any], current_job_id: int | None = None) -> int:
    job_id = optional_int(args.get("job_id")) or current_job_id
    if not job_id:
        raise ValueError("请指定任务号")
    return job_id


def compact(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"
