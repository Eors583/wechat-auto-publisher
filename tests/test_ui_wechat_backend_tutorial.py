from __future__ import annotations

import json
from typing import Any

from nicegui import ui
import pytest

from app.providers.wechat_backend_search import (
    WechatBackendSearchError,
    normalize_backend_cookie,
    normalize_backend_token,
)
from app.ui.panels import topics


class _FakeDb:
    def list_official_accounts(self) -> list[dict[str, Any]]:
        return []


class _FakeFollowedContentService:
    db = _FakeDb()

    def get_backend_search_settings(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "has_token": True,
            "has_cookie": True,
            "session_label": "运营账号",
        }

    def list_accounts(self) -> list[dict[str, Any]]:
        return []


class _FakeState:
    def account_options(self) -> dict[str, str]:
        return {}


def _snapshot() -> str:
    values: list[dict[str, Any]] = []
    for element in ui.context.client.elements.values():
        values.append(
            {
                "type": type(element).__name__,
                "text": getattr(element, "text", None),
                "value": getattr(element, "value", None),
                "props": getattr(element, "_props", {}),
            }
        )
    return json.dumps(values, ensure_ascii=False, default=str)


def _click_button(label: str) -> None:
    button = next(
        element
        for element in ui.context.client.elements.values()
        if type(element).__name__ == "Button"
        and getattr(element, "text", None) == label
    )
    listener = next(
        item
        for item in button._event_listeners.values()
        if item.type == "click"
    )
    listener.handler(None)


def test_backend_login_dialog_contains_embedded_beginner_tutorial() -> None:
    try:
        topics._build_followed_accounts(
            _FakeState(),
            _FakeFollowedContentService(),
            workspace_tabs=object(),
            tab_wizard=object(),
        )
        _click_button("配置登录态")
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    assert "配置公众号后台搜索" in snapshot
    assert "登录微信公众号后台" in snapshot
    assert "打开微信公众平台（mp.weixin.qq.com）" in snapshot
    assert "复制 Token" in snapshot
    assert "程序会自动提取 token=" in snapshot
    assert "后台 Token" in snapshot
    assert "复制 Cookie" in snapshot
    assert "Network" in snapshot
    assert "Request Headers" in snapshot
    assert "后台 Cookie" in snapshot
    assert "仅测试" in snapshot
    assert "测试并保存" in snapshot
    assert "不要发到聊天、群聊或截图中" in snapshot
    assert "Windows 当前用户加密保存" in snapshot
    assert "修改密码、退出后台、触发安全验证或登录一段时间后" in snapshot
    assert "凭证可能失效" in snapshot
    assert "留空输入框会保留已保存内容" in snapshot


def test_backend_login_input_normalizers_accept_beginner_copy_formats() -> None:
    assert normalize_backend_token("  123456789  ") == "123456789"
    assert (
        normalize_backend_token(
            "https://mp.weixin.qq.com/cgi-bin/home?t=home/index"
            "&lang=zh_CN&token=987654321"
        )
        == "987654321"
    )
    assert (
        normalize_backend_cookie(
            " Cookie: wxuin=1; pass_ticket=private-value "
        )
        == "wxuin=1; pass_ticket=private-value"
    )
    assert (
        normalize_backend_cookie(
            "Accept: application/json\n"
            "Cookie: wxuin=1; data_bizuin=2\n"
            "User-Agent: Browser"
        )
        == "wxuin=1; data_bizuin=2"
    )


@pytest.mark.parametrize(
    ("normalizer", "value", "message"),
    [
        (
            normalize_backend_token,
            "https://mp.weixin.qq.com/cgi-bin/home",
            "Token 格式不正确",
        ),
        (
            normalize_backend_cookie,
            "Accept: application/json\nUser-Agent: Browser",
            "Cookie 格式不正确",
        ),
        (
            normalize_backend_cookie,
            "not-a-cookie",
            "Cookie 格式不正确",
        ),
    ],
)
def test_backend_login_input_normalizers_reject_ambiguous_values(
    normalizer: Any,
    value: str,
    message: str,
) -> None:
    with pytest.raises(WechatBackendSearchError, match=message):
        normalizer(value)
