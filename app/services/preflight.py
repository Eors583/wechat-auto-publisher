from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any

from app.accounts import apply_account_selection
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import build_text_client
from app.config import load_config
from app.db import Database
from app.pipeline import Pipeline
from app.services.failures import sanitize_failure_text
from app.services.wechat_delivery import (
    HEALTHY,
    UNHEALTHY,
    get_or_probe_wechat_connection_health,
)
from app.services.wechat_relay_settings import effective_wechat_relay_settings
from app.wechat.draft import batchget_drafts, get_draft
from app.wechat.errors import friendly_wechat_error
from app.wechat.material import batch_get_material
from app.wechat.template_snapshot import (
    load_template_snapshot,
    merge_template_html,
)


def preflight_accounts(
    db: Database,
    account_ids: list[str],
    *,
    deep_model_check: bool = False,
    force_wechat_check: bool = False,
    allow_stale_wechat_cache: bool = False,
    jobs_by_account: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Run read-only generation and publishing readiness checks per account."""
    if allow_stale_wechat_cache and (deep_model_check or force_wechat_check):
        raise ValueError("离线缓存检查不能同时执行模型深测或强制微信刷新")
    unique_ids = list(dict.fromkeys(str(item) for item in account_ids if item))
    scoped_jobs = {
        str(account_id): [
            dict(job) for job in list(jobs or []) if isinstance(job, dict)
        ]
        for account_id, jobs in dict(jobs_by_account or {}).items()
    }
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(unique_ids)))) as executor:
        return list(
            executor.map(
                lambda account_id: _check_account(
                    db,
                    account_id,
                    deep_model_check=deep_model_check,
                    force_wechat_check=force_wechat_check,
                    allow_stale_wechat_cache=allow_stale_wechat_cache,
                    jobs=scoped_jobs.get(account_id, []),
                ),
                unique_ids,
            )
        )


def _check_account(
    db: Database,
    account_id: str,
    *,
    deep_model_check: bool,
    force_wechat_check: bool,
    allow_stale_wechat_cache: bool,
    jobs: list[dict[str, Any]],
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
    if not model_id:
        checks.append(
            _check(
                "model",
                "模型连接",
                False,
                "尚未绑定文章模型；公众号凭证仍可单独检查，绑定模型后才能生成文章",
            )
        )
    else:
        try:
            client = build_text_client(db, config, model_id)
            if deep_model_check:
                client.complete("只回复 OK")
            checks.append(_check("model", "模型连接", True, "模型配置可用"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("model", "模型连接", False, _friendly_error(exc)))

    if allow_stale_wechat_cache:
        health = _cached_wechat_health(db, account_id)
    else:
        health = get_or_probe_wechat_connection_health(
            db,
            account_id,
            lambda: _probe_wechat_connection(config, db),
            force=force_wechat_check,
            mode=_wechat_connection_mode(config, db),
        )
    checks.extend(_checks_from_wechat_health(health))

    editor_cfg = dict(config.get("editor_template") or {})
    editor_cfg["_root"] = config.get("_root")
    job_client: Any | None = None
    job_client_error = ""
    template_draft_selected = bool(
        editor_cfg.get("enabled")
        and str(editor_cfg.get("selected_media_id") or "").strip()
    )
    needs_detail_client = bool(jobs or template_draft_selected)
    if needs_detail_client and not allow_stale_wechat_cache:
        try:
            job_client = Pipeline(config, db=db)._wechat_client()
        except Exception as exc:  # noqa: BLE001
            job_client_error = _friendly_error(exc)
    elif needs_detail_client:
        job_client_error = "后台刷新完成前，暂未远程核验本次文章模板和封面"
    checks.append(
        _template_check(
            editor_cfg,
            jobs,
            client=job_client,
            client_error=job_client_error,
            verify_remote_draft=not allow_stale_wechat_cache,
        )
    )
    if jobs:
        checks.append(
            _cover_check(
                jobs,
                client=job_client,
                client_error=job_client_error,
            )
        )

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
    # A previously generated and confirmed article can still be written even
    # when its text/image model was later disabled.
    can_write = all(
        item["ok"]
        for item in checks
        if item["key"] in {"wechat", "material", "draft", "template", "cover"}
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
    return {
        "key": key,
        "name": name,
        "ok": bool(ok),
        "message": sanitize_failure_text(message),
    }


def _template_check(
    editor_cfg: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    client: Any | None,
    client_error: str,
    verify_remote_draft: bool = True,
) -> dict[str, Any]:
    if not editor_cfg.get("enabled", False):
        return _check("template", "模板与正文占位符", True, "未启用历史模板")

    issues: list[str] = []
    try:
        snapshot = load_template_snapshot(editor_cfg)
    except Exception as exc:  # noqa: BLE001
        snapshot = None
        issues.append(_friendly_error(exc))
    if snapshot is None:
        issues.append("模板不存在或缺少正文占位符，请到模板管理重新同步")
    snapshot_sha256 = (
        hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
        if snapshot is not None
        else ""
    )

    required = bool(editor_cfg.get("required", True))
    for job in jobs:
        meta = dict(job.get("meta") or {})
        job_name = _job_label(job)
        if required and not bool(meta.get("editor_template_applied")):
            issues.append(f"{job_name}尚未实际套用该公众号模板")
        if required and not str(job.get("html_content") or "").strip():
            issues.append(f"{job_name}缺少最终排版 HTML")
        expected_sha256 = str(meta.get("editor_template_sha256") or "").strip()
        if required and not expected_sha256:
            issues.append(f"{job_name}缺少审核时模板版本，请重新排版并确认")
        elif expected_sha256 and snapshot_sha256 and expected_sha256 != snapshot_sha256:
            issues.append(f"{job_name}审核后模板已发生变化，请重新排版并确认")

    source_media_ids = {
        str(
            (job.get("meta") or {}).get("editor_template_source_media_id") or ""
        ).strip()
        for job in jobs
        if str(
            (job.get("meta") or {}).get("editor_template_source_media_id") or ""
        ).strip()
    }
    selected_media_id = str(editor_cfg.get("selected_media_id") or "").strip()
    if selected_media_id:
        source_media_ids.add(selected_media_id)
    if source_media_ids and verify_remote_draft:
        if client is None:
            issues.append(client_error or "无法连接公众号，未能核验所选模板草稿")
        else:
            article_index = max(0, int(editor_cfg.get("selected_article_index") or 0))
            placeholder = str(
                editor_cfg.get("placeholder") or "蓝血经营管理系统正文"
            ).strip()
            for media_id in sorted(source_media_ids):
                try:
                    _validate_template_draft(
                        client,
                        media_id,
                        article_index=article_index,
                        placeholder=placeholder,
                    )
                except Exception as exc:  # noqa: BLE001
                    issues.append(
                        "所选模板草稿已失效或正文占位符已被修改："
                        + _friendly_error(exc)
                    )

    return _check(
        "template",
        "本次文章模板与正文占位符",
        not issues,
        "本次文章已套用有效模板"
        if not issues and jobs
        else (
            "模板快照和正文占位符正常"
            if not issues
            else "；".join(dict.fromkeys(issues))
        ),
    )


def _validate_template_draft(
    client: Any,
    media_id: str,
    *,
    article_index: int,
    placeholder: str,
) -> None:
    data = get_draft(client, media_id)
    rows = list(
        data.get("news_item") or ((data.get("content") or {}).get("news_item")) or []
    )
    if article_index >= len(rows):
        raise ValueError("模板草稿中的文章位置已不存在")
    content = str((rows[article_index] or {}).get("content") or "")
    # Reuse the production merge parser so preflight and final rendering agree
    # on what constitutes an independent, replaceable body placeholder.
    merge_template_html(
        content,
        "<p>正文预检</p>",
        placeholder=placeholder,
    )


def _cover_check(
    jobs: list[dict[str, Any]],
    *,
    client: Any | None,
    client_error: str,
) -> dict[str, Any]:
    missing = [job for job in jobs if not str(job.get("thumb_media_id") or "").strip()]
    if missing:
        return _check(
            "cover",
            "本次文章封面素材",
            False,
            "；".join(f"{_job_label(job)}尚未选择有效封面" for job in missing),
        )

    media_ids = {
        str(job.get("thumb_media_id") or "").strip()
        for job in jobs
        if str(job.get("thumb_media_id") or "").strip()
    }
    if client is None:
        return _check(
            "cover",
            "本次文章封面素材",
            False,
            client_error or "无法连接公众号，未能核验本次文章封面",
        )
    try:
        found = _find_image_material_ids(client, media_ids)
    except Exception as exc:  # noqa: BLE001
        return _check(
            "cover",
            "本次文章封面素材",
            False,
            "核验本次文章封面失败：" + _friendly_error(exc),
        )

    invalid = sorted(media_ids - found)
    return _check(
        "cover",
        "本次文章封面素材",
        not invalid,
        "本次文章使用的封面素材有效"
        if not invalid
        else "以下封面素材已失效或不属于该公众号：" + "、".join(invalid),
    )


def _find_image_material_ids(client: Any, wanted: set[str]) -> set[str]:
    """Read the current library on every call; job-specific IDs are never cached."""

    remaining = {str(item).strip() for item in wanted if str(item).strip()}
    found: set[str] = set()
    offset = 0
    while remaining:
        data = batch_get_material(
            client,
            material_type="image",
            offset=offset,
            count=20,
        )
        rows = list(data.get("item") or [])
        for row in rows:
            media_id = str((row or {}).get("media_id") or "").strip()
            if media_id in remaining:
                found.add(media_id)
                remaining.discard(media_id)
        offset += len(rows)
        total = int(data.get("total_count") or 0)
        if not rows or offset >= total:
            break
    return found


def _job_label(job: dict[str, Any]) -> str:
    job_id = str(job.get("id") or "").strip()
    return f"任务 #{job_id} " if job_id else "本次文章"


def _probe_wechat_connection(
    config: dict[str, Any],
    db: Database,
) -> dict[str, Any]:
    started_at = perf_counter()
    client = Pipeline(config, db=db)._wechat_client()
    details: dict[str, Any] = {}
    errors: list[str] = []

    try:
        material = batch_get_material(client, material_type="image", offset=0, count=1)
        details["material"] = {
            "reachable": True,
            "total_count": int(material.get("total_count") or 0),
        }
    except Exception as exc:  # noqa: BLE001
        message = _friendly_error(exc)
        details["material"] = {"reachable": False, "error": message}
        errors.append(message)

    try:
        drafts = batchget_drafts(client, offset=0, count=1, no_content=1)
        details["draft"] = {
            "reachable": True,
            "total_count": int(drafts.get("total_count") or 0),
        }
    except Exception as exc:  # noqa: BLE001
        message = _friendly_error(exc)
        details["draft"] = {"reachable": False, "error": message}
        errors.append(message)

    return {
        "status": HEALTHY if not errors else UNHEALTHY,
        "mode": _wechat_connection_mode(config, db),
        "latency_ms": round((perf_counter() - started_at) * 1000),
        "details": details,
        "error": "；".join(dict.fromkeys(errors)) or None,
    }


def _wechat_connection_mode(config: dict[str, Any], db: Database) -> str:
    fallback = config.get("wechat_relay")
    if not isinstance(fallback, dict):
        fallback = config.get("wechat_proxy")
    try:
        settings = effective_wechat_relay_settings(
            db,
            fallback if isinstance(fallback, dict) else None,
        )
        return "relay" if bool(settings.get("enabled", False)) else "direct"
    except Exception:  # noqa: BLE001
        return (
            "relay"
            if isinstance(fallback, dict) and bool(fallback.get("enabled", False))
            else "direct"
        )


def _cached_wechat_health(
    db: Database,
    account_id: str,
) -> dict[str, Any]:
    """Return persisted health only; never perform a network request."""

    cached = db.get_wechat_connection_health(account_id)
    if cached is not None:
        return {**cached, "cached": True}
    return {
        "account_id": account_id,
        "status": "missing",
        "details": {},
        "error": "尚未完成微信公众号连接检查",
        "cached": True,
    }


def _checks_from_wechat_health(
    health: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_details = health.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    health_status = str(health.get("status") or "").strip().casefold()
    if health_status == "stale":
        fallback_error = "公众号连接配置已变化，需要重新检测"
    elif health_status == "missing":
        fallback_error = "尚未完成微信公众号连接检查"
    else:
        fallback_error = str(health.get("error") or "微信公众号连接检查失败")

    raw_material = details.get("material")
    material = raw_material if isinstance(raw_material, dict) else {}
    details_are_current = health_status in {HEALTHY, UNHEALTHY}
    material_reachable = details_are_current and bool(
        material.get("reachable", False)
    )
    material_total = int(material.get("total_count") or 0)
    material_message = (
        (
            f"连接正常，图片素材 {material_total} 个"
            if material_total
            else "连接正常，但没有封面图片素材"
        )
        if material_reachable
        else str(material.get("error") or fallback_error)
    )

    raw_draft = details.get("draft")
    draft = raw_draft if isinstance(raw_draft, dict) else {}
    draft_reachable = details_are_current and bool(
        draft.get("reachable", False)
    )
    draft_total = int(draft.get("total_count") or 0)
    draft_message = (
        f"草稿接口正常，共 {draft_total} 条"
        if draft_reachable
        else str(draft.get("error") or fallback_error)
    )
    # A partial probe remains useful: one authenticated official endpoint
    # proves the AppID/AppSecret/IP path even if the other endpoint is missing
    # permission or temporarily unavailable. Stale/missing cache entries never
    # reuse old reachability flags.
    authenticated = material_reachable or draft_reachable
    return [
        _check(
            "wechat",
            "公众号凭证与 IP 白名单",
            authenticated,
            (
                "公众号凭证有效，当前出口 IP 已通过微信鉴权"
                if authenticated
                else fallback_error
            ),
        ),
        _check(
            "material",
            "封面图片素材",
            material_reachable and material_total > 0,
            material_message,
        ),
        _check(
            "draft",
            "草稿与多图文次条接口",
            draft_reachable,
            draft_message,
        ),
    ]


def _friendly_error(exc: Exception) -> str:
    return friendly_wechat_error(exc)
