from __future__ import annotations

import re
from typing import Any

from app.services.failures import sanitize_failure_text
from app.wechat.errors import friendly_wechat_error


def friendly_model_error(
    error: Exception | str,
    *,
    image: bool = False,
) -> str:
    """Translate provider failures without echoing credentials or requests."""

    detail = str(error or "").strip()
    safe_detail = sanitize_failure_text(
        re.sub(
            r"(?i)\b(?:sk-|key-|ak-)[a-z0-9._-]{6,}",
            "••••••••",
            detail,
        )
    )
    normalized = safe_detail.casefold()
    subject = "图片模型" if image else "文本模型"
    if any(
        marker in normalized
        for marker in (
            "401",
            "unauthorized",
            "invalid api key",
            "invalid_api_key",
            "authentication",
            "鉴权失败",
            "密钥无效",
        )
    ):
        return f"{subject}的 API Key 无效或已失效，请从厂商控制台重新复制后再试。"
    if any(
        marker in normalized
        for marker in (
            "402",
            "insufficient balance",
            "insufficient quota",
            "quota exceeded",
            "余额不足",
            "额度不足",
        )
    ):
        return f"{subject}账号余额或调用额度不足，请到厂商控制台查看额度。"
    if (
        "403" in normalized
        or "permission denied" in normalized
        or "forbidden" in normalized
    ):
        return f"{subject}的 API Key 没有所选模型权限，请开通模型后重新测试。"
    if any(
        marker in normalized
        for marker in (
            "model not found",
            "unknown model",
            "does not exist",
            "模型不存在",
            "模型不可用",
        )
    ):
        return f"{subject}名称不可用，请按照厂商文档填写准确的模型名称。"
    if (
        "429" in normalized
        or "rate limit" in normalized
        or "请求过于频繁" in normalized
    ):
        return f"{subject}请求过于频繁，请查看额度或稍后重试。"
    if "timeout" in normalized or "timed out" in normalized or "超时" in normalized:
        return f"{subject}连接超时，请检查网络后重试。"
    if any(
        marker in normalized
        for marker in (
            "connection",
            "network",
            "name resolution",
            "dns",
            "无法连接",
            "网络",
        )
    ):
        return f"无法连接{subject}服务，请检查网络、代理或防火墙后重试。"
    fallback = safe_detail[:240] or "未知错误"
    return f"{subject}验证失败：{fallback}"


def onboarding_wechat_issue(error: Exception | str) -> dict[str, Any]:
    """Return one beginner-safe WeChat repair instruction."""

    raw = str(error or "")
    lower = raw.casefold()
    reason = sanitize_failure_text(friendly_wechat_error(error))
    if (
        "40013" in raw
        or "invalid appid" in lower
        or ("appid" in lower and "无效" in raw)
    ):
        return _issue(
            "wechat.invalid_app_id",
            "AppID 不正确",
            reason,
            "返回上一步修改公众号 AppID",
            ["edit_app_id"],
        )
    if (
        "40125" in raw
        or "invalid appsecret" in lower
        or ("appsecret" in lower and "无效" in raw)
    ):
        return _issue(
            "wechat.invalid_app_secret",
            "AppSecret 无效或已重置",
            reason,
            "重新复制公众号 AppSecret 后检测",
            ["edit_app_secret"],
        )
    if (
        "40164" in raw
        or "invalid ip" in lower
        or "whitelist" in lower
        or ("ip" in lower and "白名单" in raw)
    ):
        return _issue(
            "wechat.ip_not_whitelisted",
            "出口 IP 未加入白名单",
            reason,
            "复制固定出口 IP，加入微信后台白名单后重新检测",
            ["copy_egress_ip", "open_wechat_console", "retry"],
        )
    if "48001" in raw or "api unauthorized" in lower or "没有草稿接口权限" in raw:
        return _issue(
            "wechat.draft_permission_missing",
            "公众号没有草稿接口权限",
            reason,
            "查看公众号接口权限，确认账号类型支持草稿箱",
            ["open_permission_help", "retry"],
        )
    status_code = getattr(error, "status_code", None)
    if (
        status_code in {502, 503, 504}
        or any(f"gateway http {code}" in lower for code in (502, 503, 504))
        or "云中转暂时无法连接微信" in raw
    ):
        return _issue(
            "wechat.relay_unavailable",
            "云中转暂时无法连接微信",
            reason,
            "稍后重试，或临时切换为本机直接连接",
            ["retry", "switch_direct"],
        )
    return _issue(
        "wechat.connection_failed",
        "公众号连接检查失败",
        reason,
        "根据提示修复后重新检测",
        ["retry"],
    )


def _issue(
    code: str,
    title: str,
    reason: str,
    recommendation: str,
    actions: list[str],
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "reason": reason,
        "recommendation": recommendation,
        "actions": list(actions),
    }


__all__ = ["friendly_model_error", "onboarding_wechat_issue"]
