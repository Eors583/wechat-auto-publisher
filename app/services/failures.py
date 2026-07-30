from __future__ import annotations

import re
from typing import Any

from app.services.wechat_relay_settings import (
    DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP,
)

_HEADER_SECRET_PATTERNS = (
    (
        re.compile(
            r"(?im)\b((?:proxy-)?authorization|(?:set-)?cookie)"
            r"\s*[:：=]\s*[^\r\n]*"
        ),
        r"\1: ***",
    ),
)

_SECRET_PATTERNS = (
    (
        re.compile(
            r"(?i)([?&](?:app[_-]?secret|api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|secret|token)=)[^&#\s]+"
        ),
        r"\1***",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+"), r"\1 ***"),
    (
        re.compile(
            r"""(?ix)
            (
                ["']?
                (?:app[\s_-]?secret|api[\s_-]?key|access[\s_-]?token|
                   refresh[\s_-]?token|secret|token)
                ["']?\s*[:：=]\s*["']?
            )
            ([^"',\s;&}\]]+)
            """
        ),
        r"\1***",
    ),
)

_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "appsecret",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "verificationtoken",
        "encryptkey",
        "password",
        "secret",
        "token",
    }
)


def sanitize_failure_text(value: Any, *, limit: int = 800) -> str:
    """Remove credentials and bound raw provider text before it reaches clients."""

    text = str(value or "").strip()
    for pattern, replacement in _HEADER_SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    # Long provider responses may contain echoed prompts or source article text.
    text = re.sub(r"\s+", " ", text)
    return text[: max(80, int(limit))]


def sanitize_failure_payload(value: Any) -> Any:
    """Recursively redact structured API error details without changing shape.

    FastAPI ``detail`` values are not limited to strings.  Validation errors and
    downstream adapters may return nested dictionaries or lists, so sanitizing
    only ``str`` values can accidentally expose a credential through a named
    field.  Primitive values remain primitive for backwards compatibility.
    """

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized_key in _SENSITIVE_PAYLOAD_KEYS:
                sanitized[key] = "***"
            else:
                sanitized[key] = sanitize_failure_payload(raw_value)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_failure_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_failure_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_failure_text(value)


def classify_job_failure(
    error: Any,
    *,
    step: Any = "",
    status: Any = "",
) -> dict[str, Any] | None:
    """Translate technical failures into a stable operator-facing contract."""

    raw = str(error or "").strip()
    if not raw and str(status or "") != "failed":
        return None
    safe = sanitize_failure_text(raw)
    stage = _normalize_stage(step)
    lowered = raw.lower()

    if (
        "syntax error at or near" in lowered
        or "database is locked" in lowered
        or "database error" in lowered
    ):
        return _failure(
            "system.database_write_failed",
            stage,
            "系统保存生成结果失败",
            "文章已经执行到当前步骤，但系统未能把结果保存到数据库。",
            "只影响当前文章，其他已完成内容不会被删除。",
            "请刷新后从当前失败步骤重试；若重复出现，请联系管理员检查数据库。",
            ("retry_step", "copy_error"),
            safe,
        )

    if stage == "ingest":
        if any(
            marker in lowered
            for marker in ("m.baidu.com/s?", "sogou", "search result", "搜索结果")
        ):
            return _failure(
                "ingest.invalid_source_url",
                stage,
                "原文链接不是文章页",
                "当前链接是搜索结果或跳转页面，不是文章真实地址。",
                "只影响当前文章的原文抓取。",
                "请打开原文并复制最终的 mp.weixin.qq.com 文章链接。",
                ("replace_url", "paste_text"),
                safe,
            )
        if any(
            marker in lowered
            for marker in (
                "failed to extract article body",
                "extract article",
                "article body",
                "正文提取",
            )
        ):
            return _failure(
                "ingest.extract_failed",
                stage,
                "原文抓取失败",
                "该链接不是可直接解析的文章页面，或页面没有返回有效正文。",
                "只影响当前公众号文章，批次中的其他文章可继续处理。",
                "请使用真实的微信公众号文章链接，或改为直接粘贴正文。",
                ("replace_url", "paste_text", "retry_ingest"),
                safe,
            )
        if _is_network_error(lowered):
            return _failure(
                "ingest.network",
                stage,
                "原文网络访问失败",
                "当前网络暂时无法访问原文页面。",
                "只影响当前文章的抓取步骤。",
                "检查网络后仅重新抓取原文，无需整篇重新生成。",
                ("retry_ingest",),
                safe,
            )

    if stage in {"rewrite", "title_optimize"}:
        prefix = "title" if stage == "title_optimize" else "rewrite"
        title = "标题生成失败" if stage == "title_optimize" else "AI 正文生成失败"
        if any(
            marker in lowered
            for marker in (
                "unexpected_eof",
                "unexpected eof",
                "server disconnected",
                "connection reset",
                "连接中断",
                "10054",
            )
        ):
            return _failure(
                f"{prefix}.network_interrupted",
                stage,
                title,
                "AI 服务连接在生成过程中被中断，系统未收到完整结果。",
                "已抓取的原文会保留，其他公众号文章不受影响。",
                "系统会优先重连原任务；仍失败时可仅重试当前步骤或更换模型。",
                ("retry_step", "change_model"),
                safe,
            )
        if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
            return _failure(
                f"{prefix}.rate_limited",
                stage,
                title,
                "AI 服务当前请求过多，暂时限制了调用频率。",
                "只影响当前公众号文章，已完成的其他文章不会丢失。",
                "稍后从当前步骤重试，或临时选择备用模型。",
                ("retry_step", "change_model"),
                safe,
            )
        if "invalid_argument" in lowered or "invalid argument" in lowered:
            return _failure(
                f"{prefix}.invalid_argument",
                stage,
                title,
                "AI 服务拒绝了本次请求参数或输入内容。",
                "只影响当前公众号文章。",
                "从当前步骤重试；如仍失败，可更换模型或缩短参考原文。",
                ("retry_step", "change_model"),
                safe,
            )
        if "json" in lowered or "标题" in lowered and "格式" in lowered:
            return _failure(
                "title.invalid_output" if stage == "title_optimize" else "rewrite.invalid_output",
                stage,
                title,
                "AI 返回的内容格式不完整，系统无法安全解析。",
                "已抓取的原文会保留，不需要重新抓取。",
                "仅重新生成当前阶段，或更换备用模型重试。",
                ("retry_step", "change_model"),
                safe,
            )
        if "too short" in lowered or "字数" in lowered or "不足" in lowered:
            return _failure(
                "rewrite.content_too_short",
                stage,
                title,
                "生成正文未达到当前公众号要求的最低长度。",
                "原文已保留，只需重新生成正文。",
                "使用当前模型重试，或更换更适合长文的备用模型。",
                ("retry_rewrite", "change_model"),
                safe,
            )
        if "all rewrite providers failed" in lowered:
            return _failure(
                "rewrite.providers_failed",
                stage,
                title,
                "主模型和备用模型均未能完成本次生成。",
                "只影响当前公众号文章。",
                "检查模型配置后重试正文，或临时选择其他可用模型。",
                ("retry_rewrite", "change_model"),
                safe,
            )
        if "rewrite provider failed" in lowered:
            return _failure(
                "rewrite.provider_failed",
                stage,
                title,
                "当前绑定的文章模型未能完成本次生成。",
                "只影响当前公众号文章，已抓取的原文仍然保留。",
                "仅重试正文；如仍失败，可为公众号更换其他可用模型。",
                ("retry_rewrite", "change_model"),
                safe,
            )
        if _is_timeout(lowered):
            return _failure(
                f"{prefix}.timeout",
                stage,
                title,
                "AI 服务在规定时间内没有完成响应。",
                "已完成的上游内容会保留。",
                "从当前步骤重试，或选择响应更稳定的备用模型。",
                ("retry_step", "change_model"),
                safe,
            )

    if stage == "render":
        if "placeholder" in lowered or "占位符" in lowered:
            return _failure(
                "render.template_invalid",
                stage,
                "公众号模板不可用",
                "所选模板缺少正文占位符，或正文替换后仍有残留占位内容。",
                "文章正文和标题已保留，只影响最终排版。",
                "修复或更换该公众号模板后，仅重新排版。",
                ("open_template_settings", "retry_render"),
                safe,
            )
        if "media_id" in lowered or "invalid media" in lowered:
            return _failure(
                "render.media_invalid",
                stage,
                "模板素材已失效",
                "模板中的图片或视频素材标识已失效，微信无法继续使用。",
                "文章内容已保留，只影响模板或素材显示。",
                "重新选择模板素材后仅重新排版。",
                ("open_template_settings", "retry_render"),
                safe,
            )
        if _is_network_error(lowered):
            return _failure(
                "render.network",
                stage,
                "排版素材读取失败",
                "排版过程中暂时无法读取模板或图片素材。",
                "文章正文和标题已保留。",
                "网络恢复后仅重新排版。",
                ("retry_render",),
                safe,
            )

    if stage == "images":
        if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
            return _failure(
                "images.rate_limited",
                stage,
                "正文配图生成受限",
                "生图服务当前请求过多，暂时限制了调用频率。",
                "只影响当前文章的正文配图，正文和标题不会丢失。",
                "等待冷却时间结束后重试失败图片，或更换图片模型。",
                ("retry_images", "change_model"),
                safe,
            )
        if _is_timeout(lowered):
            return _failure(
                "images.timeout",
                stage,
                "正文配图生成超时",
                "生图服务在规定时间内没有完成响应。",
                "只影响当前文章的正文配图，正文和标题不会丢失。",
                "等待冷却时间结束后重试失败图片。",
                ("retry_images",),
                safe,
            )
        return _failure(
            "images.failed",
            stage,
            "正文配图生成失败",
            "系统未能完成当前正文配图的生成或上传。",
            "正文、标题及已完成的其他配图会保留。",
            "仅重试失败图片；如无法识别失败图片，可重新生成全部正文配图。",
            ("retry_images",),
            safe,
        )

    if stage == "inject":
        if any(
            marker in lowered
            for marker in (
                "尚未选择有效封面",
                "缺少有效封面",
                "thumb_media_id is required",
            )
        ):
            return _failure(
                "inject.cover_missing",
                stage,
                "文章尚未选择封面",
                "本次文章没有可用于公众号草稿的封面素材。",
                "只影响当前文章的草稿写入，审核内容不会丢失。",
                "返回审核工作台选择或重新生成封面，然后重新确认并写入。",
                ("select_cover", "retry_inject"),
                safe,
            )
        if any(
            marker in lowered
            for marker in (
                "封面素材已失效",
                "封面素材已删除",
                "封面素材已不存在",
                "不属于该公众号",
            )
        ):
            return _failure(
                "inject.cover_invalid",
                stage,
                "文章封面素材不可用",
                "本次文章选择的封面已删除、已失效，或属于另一个公众号。",
                "只影响当前文章的草稿写入。",
                "为该公众号重新选择有效封面，重新确认后仅重试写入。",
                ("select_cover", "retry_inject"),
                safe,
            )
        if any(
            marker in lowered
            for marker in (
                "审核后模板已发生变化",
                "缺少审核时模板版本",
                "尚未实际套用该公众号模板",
                "缺少最终排版 html",
            )
        ):
            return _failure(
                "inject.template_changed",
                stage,
                "审核稿模板已变化",
                "当前模板与审核时使用的模板不一致，系统已阻止静默改版。",
                "文章正文仍然保留，但必须重新排版和确认。",
                "按当前公众号模板重新排版，检查预览后再次确认文章。",
                ("retry_render",),
                safe,
            )
        if "40125" in lowered or "invalid appsecret" in lowered:
            return _failure(
                "inject.auth_invalid",
                stage,
                "公众号密钥无效",
                "微信拒绝了当前 AppSecret，无法获取公众号访问凭证。",
                "只影响该公众号的草稿写入，已确认文章不会丢失。",
                "到公众号管理更新 AppSecret，测试连接后仅重试写入。",
                ("open_account_settings", "retry_inject"),
                safe,
            )
        if "40164" in lowered or "whitelist" in lowered or "白名单" in lowered:
            return _failure(
                "inject.ip_not_whitelisted",
                stage,
                "微信 IP 白名单未放行",
                "微信未允许当前出口 IP 调用草稿接口。",
                "只影响该公众号的草稿写入。",
                "按照页面教程，将固定出口 IP "
                f"{DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP} "
                "加入该公众号的微信后台白名单后重试写入。",
                ("show_ip_whitelist_guide", "retry_inject"),
                safe,
            )
        if (
            _is_timeout(lowered)
            or "connection reset" in lowered
            or "502" in lowered
            or "503" in lowered
        ):
            return _failure(
                "inject.ambiguous_timeout",
                stage,
                "草稿写入结果待确认",
                "提交草稿时连接中断，微信可能已经创建草稿。",
                "系统不会直接重复提交，以免产生重复草稿。",
                "先执行草稿对账；确认不存在后才允许重新写入。",
                ("reconcile_draft",),
                safe,
            )
        if "relay" in lowered or "中转" in lowered:
            return _failure(
                "inject.relay_unavailable",
                stage,
                "云端稳定连接不可用",
                "当前无法连接固定 IP 中转服务。",
                "文章仍保留在待写入队列，不影响其他公众号审核。",
                "重新检测中转连接，恢复后仅重试写入。",
                ("check_relay", "retry_inject"),
                safe,
            )
        if "media_id" in lowered:
            return _failure(
                "inject.media_invalid",
                stage,
                "草稿素材已失效",
                "封面、模板图片或其他微信素材已失效。",
                "只影响当前公众号草稿写入。",
                "重新选择有效素材并排版后，再重试写入。",
                ("retry_render", "retry_inject"),
                safe,
            )
    return _failure(
        f"{stage or 'job'}.unknown",
        stage or "unknown",
        "当前步骤执行失败",
        "系统未能完成当前操作。",
        "只影响当前文章；其他公众号任务仍可继续。",
        "复制技术摘要交给管理员，或从当前失败步骤重试。",
        ("copy_error", "retry_step"),
        safe,
    )


def public_failure(failure: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the stable failure fields shared by API, desktop and Feishu."""

    if not failure:
        return None
    fields = (
        "code",
        "stage",
        "title",
        "reason",
        "impact",
        "recommendation",
        "retryable",
        "transient",
        "actions",
        "technical_summary",
    )
    result = {
        key: sanitize_failure_payload(failure.get(key))
        for key in fields
        if key in failure
    }
    result["actions"] = [
        sanitize_failure_text(item, limit=120)
        for item in list(result.get("actions") or [])
        if str(item).strip()
    ]
    result["retryable"] = bool(result.get("retryable", False))
    if "transient" in result:
        result["transient"] = bool(result.get("transient", False))
    return result


def _failure(
    code: str,
    stage: str,
    title: str,
    reason: str,
    impact: str,
    recommendation: str,
    actions: tuple[str, ...],
    technical_summary: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "stage": stage,
        "title": title,
        "reason": reason,
        "impact": impact,
        "recommendation": recommendation,
        "retryable": any(action.startswith("retry") for action in actions),
        "transient": code.endswith(
            (
                ".network",
                ".timeout",
                ".ambiguous_timeout",
                ".rate_limited",
                ".relay_unavailable",
            )
        ),
        "actions": list(actions),
        "technical_summary": technical_summary,
    }


def _normalize_stage(value: Any) -> str:
    stage = str(value or "").strip().lower()
    return {
        "ingesting": "ingest",
        "rewriting": "rewrite",
        "title": "title_optimize",
        "title_optimizing": "title_optimize",
        "rendering": "render",
        "injecting": "inject",
    }.get(stage, stage)


def _is_timeout(value: str) -> bool:
    return any(marker in value for marker in ("timeout", "timed out", "read timed"))


def _is_network_error(value: str) -> bool:
    return _is_timeout(value) or any(
        marker in value
        for marker in (
            "connection refused",
            "connection reset",
            "connection aborted",
            "name resolution",
            "dns",
            "network is unreachable",
            "winerror 10054",
        )
    )
