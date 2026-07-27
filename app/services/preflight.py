from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.accounts import apply_account_selection
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import build_text_client
from app.config import load_config
from app.db import Database
from app.pipeline import Pipeline
from app.wechat.draft import batchget_drafts
from app.wechat.material import batch_get_material
from app.wechat.template_snapshot import load_template_snapshot


def preflight_accounts(
    db: Database,
    account_ids: list[str],
    *,
    deep_model_check: bool = False,
) -> list[dict[str, Any]]:
    """Run read-only generation and publishing readiness checks per account."""
    unique_ids = list(dict.fromkeys(str(item) for item in account_ids if item))
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(unique_ids)))) as executor:
        return list(
            executor.map(
                lambda account_id: _check_account(
                    db, account_id, deep_model_check=deep_model_check
                ),
                unique_ids,
            )
        )


def _check_account(
    db: Database, account_id: str, *, deep_model_check: bool
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        config, account = apply_account_selection(load_config(), db, account_id)
    except Exception as exc:  # noqa: BLE001
        return {
            "account_id": account_id,
            "account_name": account_id,
            "can_generate": False,
            "can_write": False,
            "checks": [_check("account", "公众号配置", False, _friendly_error(exc))],
        }

    model_id = str(account.get("model_id") or "")
    try:
        client = build_text_client(db, config, model_id)
        if deep_model_check:
            client.complete("只回复 OK")
        checks.append(_check("model", "模型连接", True, "模型配置可用"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("model", "模型连接", False, _friendly_error(exc)))

    wechat_client = Pipeline(config, db=db)._wechat_client()
    try:
        material = batch_get_material(
            wechat_client, material_type="image", offset=0, count=1
        )
        total = int(material.get("total_count") or 0)
        checks.append(
            _check(
                "wechat",
                "公众号凭证、IP 白名单与素材接口",
                total > 0,
                f"连接正常，图片素材 {total} 个" if total else "连接正常，但没有封面图片素材",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check("wechat", "公众号凭证、IP 白名单与素材接口", False, _friendly_error(exc))
        )

    try:
        drafts = batchget_drafts(wechat_client, offset=0, count=1, no_content=1)
        checks.append(
            _check(
                "draft",
                "草稿与多图文次条接口",
                True,
                f'草稿接口正常，共 {int(drafts.get("total_count") or 0)} 条',
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("draft", "草稿与多图文次条接口", False, _friendly_error(exc)))

    editor_cfg = dict(config.get("editor_template") or {})
    editor_cfg["_root"] = config.get("_root")
    if editor_cfg.get("enabled", False):
        snapshot = load_template_snapshot(editor_cfg)
        checks.append(
            _check(
                "template",
                "模板与正文占位符",
                snapshot is not None,
                "模板快照和正文占位符正常"
                if snapshot
                else "模板不存在或缺少正文占位符，请到模板管理重新同步",
            )
        )
    else:
        checks.append(_check("template", "模板与正文占位符", True, "未启用历史模板"))

    inline = dict(config.get("inline_images") or {})
    image_ok = True
    image_message = "正文生图未启用"
    if inline.get("enabled"):
        source_mode = str(inline.get("source_mode") or "generate")
        if source_mode in {"generate", "hybrid"}:
            image_model_id = str(inline.get("image_model_id") or "")
            image_model = db.get_ai_model(image_model_id) if image_model_id else None
            image_ok = bool(
                image_model
                and is_image_provider(image_model.get("provider_type"))
                and image_model.get("enabled")
            )
            image_message = (
                f"生图智能体已就绪：{image_model.get('name')}"
                if image_ok and image_model
                else "已启用正文生图，但生图智能体不存在、类型错误或已停用"
            )
        else:
            image_message = "正文配图使用该公众号素材库"
    checks.append(
        _check(
            "inline_images",
            "正文生图智能体",
            image_ok,
            image_message,
        )
    )

    can_generate = all(
        item["ok"] for item in checks if item["key"] in {"model", "inline_images"}
    )
    can_write = can_generate and all(
        item["ok"] for item in checks if item["key"] in {"wechat", "draft", "template"}
    )
    return {
        "account_id": account_id,
        "account_name": str(account.get("name") or account_id),
        "model_id": model_id,
        "can_generate": can_generate,
        "can_write": can_write,
        "checks": checks,
    }


def _check(key: str, name: str, ok: bool, message: str) -> dict[str, Any]:
    return {"key": key, "name": name, "ok": bool(ok), "message": message}


def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    lower = message.lower()
    if "40125" in message or "invalid appsecret" in lower:
        return "AppSecret 无效，请更新公众号凭证"
    if "40164" in message or "whitelist" in lower:
        return "当前出口 IP 不在公众号白名单"
    if "10054" in message:
        return "微信服务器临时断开连接，请稍后重试"
    return message
