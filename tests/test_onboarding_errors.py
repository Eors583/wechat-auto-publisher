from __future__ import annotations

from app.services.onboarding_errors import (
    friendly_model_error,
    onboarding_wechat_issue,
)
from app.wechat.errors import WeChatHTTPError


def test_model_error_is_actionable_and_redacts_possible_keys() -> None:
    message = friendly_model_error(
        RuntimeError("HTTP 401 invalid api key sk-onboarding-secret")
    )

    assert "API Key" in message
    assert "sk-onboarding-secret" not in message


def test_unknown_model_and_wechat_errors_redact_named_credentials() -> None:
    raw = (
        "HTTP 500 app_secret=topsecret api_key=plainsecret "
        "token=toksecret access_token=accesssecret"
    )

    model_message = friendly_model_error(RuntimeError(raw))
    wechat_issue = onboarding_wechat_issue(RuntimeError(raw))

    for secret in (
        "topsecret",
        "plainsecret",
        "toksecret",
        "accesssecret",
    ):
        assert secret not in model_message
        assert secret not in repr(wechat_issue)


def test_wechat_onboarding_issues_have_repair_actions() -> None:
    whitelist = onboarding_wechat_issue(
        RuntimeError("WeChat API error 40164: invalid ip")
    )
    permission = onboarding_wechat_issue(
        RuntimeError("WeChat API error 48001: api unauthorized")
    )
    relay = onboarding_wechat_issue(WeChatHTTPError(503))

    assert whitelist["code"] == "wechat.ip_not_whitelisted"
    assert "copy_egress_ip" in whitelist["actions"]
    assert permission["code"] == "wechat.draft_permission_missing"
    assert relay["code"] == "wechat.relay_unavailable"
    assert "switch_direct" in relay["actions"]


def test_wechat_onboarding_issues_recognize_preflight_friendly_messages() -> None:
    expected = {
        "公众号 AppID 无效，请检查是否完整填写": "wechat.invalid_app_id",
        "公众号 AppSecret 无效，请更新公众号凭证": ("wechat.invalid_app_secret"),
        "云服务器固定出口 IP 尚未加入该公众号的开发者 IP 白名单": (
            "wechat.ip_not_whitelisted"
        ),
        "公众号没有草稿接口权限，请确认账号类型和接口权限": (
            "wechat.draft_permission_missing"
        ),
        "微信云中转暂时无法连接微信服务器，请稍后重试": ("wechat.relay_unavailable"),
    }

    assert {
        message: onboarding_wechat_issue(message)["code"] for message in expected
    } == expected


def test_unknown_wechat_issue_never_exposes_embedded_credentials() -> None:
    issue = onboarding_wechat_issue(
        "未知错误 app_secret=wechat-private "
        "api_key=model-private access_token=token-private"
    )

    rendered = repr(issue)
    assert "wechat-private" not in rendered
    assert "model-private" not in rendered
    assert "token-private" not in rendered
