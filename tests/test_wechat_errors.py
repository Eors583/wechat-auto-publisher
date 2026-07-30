from app.wechat.errors import WeChatHTTPError, friendly_wechat_error


def test_relay_http_errors_have_actionable_chinese_messages() -> None:
    assert "用户名或密码" in friendly_wechat_error(WeChatHTTPError(401))
    assert "/wechat-relay" in friendly_wechat_error(WeChatHTTPError(404))
    assert "稍后重试" in friendly_wechat_error(WeChatHTTPError(502))


def test_official_api_errors_distinguish_credentials_and_whitelist() -> None:
    assert "AppSecret" in friendly_wechat_error(
        "WeChat API error 40125: invalid appsecret"
    )
    assert "AppID" in friendly_wechat_error(
        "Failed to get access_token: {'errcode': 40013, 'errmsg': 'invalid appid'}"
    )
    assert "白名单" in friendly_wechat_error(
        "WeChat API error 40164: invalid ip"
    )
    assert "草稿接口权限" in friendly_wechat_error(
        "WeChat API error 48001: api unauthorized"
    )
