from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from nicegui import ui

from app.admin import server as admin_server
from app.providers.wechat_backend_search import (
    WechatBackendSearchError,
    normalize_backend_cookie,
    normalize_backend_token,
)
from app.ui.panels import topics
from app.ui.panels.jizhile import build_admin_jizhile_panel


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

    def article_refresh_points(self) -> int:
        return 10


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


def test_followed_accounts_keeps_jizhile_configuration_in_admin_backend() -> None:
    try:
        topics._build_followed_accounts(
            _FakeState(),
            _FakeFollowedContentService(),
            workspace_tabs=object(),
            tab_wizard=object(),
        )
        snapshot = _snapshot()
    finally:
        ui.context.client.remove_all_elements()

    assert "更多设置：文章获取数据源" in snapshot
    assert "极致了 API" in snapshot
    assert "平台统一管理" in snapshot
    assert "普通用户无需也不能填写 API Key" in snapshot
    assert "配置极致了 API" not in snapshot
    assert "配置 API" not in snapshot
    assert "刷新全部 · 每个公众号10积分" in snapshot


def test_admin_backend_owns_jizhile_credentials() -> None:
    admin_source = inspect.getsource(admin_server.create_admin_app)
    panel_source = inspect.getsource(build_admin_jizhile_panel)

    assert 'ui.tab("选题雷达", icon="radar")' in admin_source
    assert "build_admin_jizhile_panel(state)" in admin_source
    assert 'state.db.for_user(None)' in panel_source
    assert '"API Key"' in panel_source
    assert '"测试并保存"' in panel_source
    assert "save_jizhile_settings(" in panel_source


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
