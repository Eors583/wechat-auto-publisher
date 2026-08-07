from __future__ import annotations

import inspect
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app.ui.panels.followed_articles import (
    ARTICLE_LOAD_MAX,
    followed_article_cover_preview_url,
    followed_article_fetch_error_message,
    next_followed_article_fetch_limit,
    open_followed_articles_dialog,
)
from app.ui.panels.topics import (
    TOPIC_CENTER_TABS,
    _build_hot_topics,
    _queue_for_wizard,
    build_topic_center,
)


class _FakeTabs:
    def __init__(self) -> None:
        self.value = "topics"
        self.calls: list[object] = []

    def set_value(self, value: object) -> None:
        self.calls.append(value)
        self.value = value


def test_topic_center_merges_articles_into_followed_accounts() -> None:
    assert TOPIC_CENTER_TABS == ("选题内容", "我的关注", "来源管理")
    assert "关注文章" not in TOPIC_CENTER_TABS


def test_topic_center_mounts_each_inner_page_only_when_selected() -> None:
    source = inspect.getsource(build_topic_center)
    assert "mounted_inner_tabs" in source
    assert "scheduled_inner_tabs" in source
    assert "inner_tabs.on_value_change" in source
    assert "lambda: mount_inner_tab(tab)" in source
    assert "immediate=False" in source
    assert 'ui.label("正在加载页面…")' in source


def test_hot_topic_cards_reuse_one_account_options_query_per_render() -> None:
    source = inspect.getsource(_build_hot_topics)
    assert source.count("state.account_options()") == 1
    assert "target_account_count = len(target_account_ids)" in source
    assert "len(state.account_options())" not in source


def test_topic_source_operations_are_serialized_and_report_one_clear_status() -> None:
    source = inspect.getsource(_build_hot_topics)

    assert 'source_operation = {"busy": False}' in source
    assert 'ui.notify("选题来源正在处理中，请稍候"' in source
    assert "以下来源暂时不可用：" in source
    assert '"部分来源失败："' not in source


def test_followed_article_cover_uses_local_wechat_image_proxy() -> None:
    source = "https://mmbiz.qpic.cn/a/0?wx_fmt=jpeg&from=appmsg"
    preview = followed_article_cover_preview_url(source)

    assert urlsplit(preview).path == "/_preview/wechat-image"
    assert parse_qs(urlsplit(preview).query)["url"] == [source]
    assert followed_article_cover_preview_url("") == ""


def test_followed_article_fetch_limit_grows_from_existing_count_and_caps() -> None:
    assert next_followed_article_fetch_limit(0, 0) == 8
    assert next_followed_article_fetch_limit(8, 8) == 16
    assert next_followed_article_fetch_limit(20, 16) == 28
    assert next_followed_article_fetch_limit(97, 24) == ARTICLE_LOAD_MAX
    assert next_followed_article_fetch_limit(ARTICLE_LOAD_MAX, 24) == ARTICLE_LOAD_MAX


def test_followed_article_fetch_error_is_safe_and_actionable() -> None:
    expired = followed_article_fetch_error_message(
        "登录态失效 token=secret&lang=zh_CN Cookie: private"
    )
    assert "重新登录微信公众平台" in expired
    assert "secret" not in expired
    assert "private" not in expired

    limited = followed_article_fetch_error_message(
        "获取公众号文章失败（200013）：freq control"
    )
    assert "限制了查询频率" in limited


def test_followed_article_fetch_failure_uses_persistent_configuration_dialog() -> None:
    source = inspect.getsource(open_followed_articles_dialog)
    assert 'ui.dialog().props("persistent")' in source
    assert '"获取公众号文章失败"' in source
    assert '"去配置登录态"' in source
    assert "dialog.close()" in source
    assert "on_configure_backend()" in source
    assert 'ui.notify(f"获取失败：' not in source
    assert 'ui.notify(f"加载更多失败：' not in source


def test_immediate_rewrite_pushes_workspace_tab_to_browser(monkeypatch) -> None:
    state = SimpleNamespace(pending_rewrite=None)
    tabs = _FakeTabs()
    wizard = object()
    notifications: list[str] = []
    monkeypatch.setattr(
        "app.ui.panels.topics.ui.notify",
        lambda message, **_kwargs: notifications.append(str(message)),
    )

    _queue_for_wizard(
        state,
        tabs,
        wizard,
        {"title": "测试文章", "url": "https://mp.weixin.qq.com/s/test"},
        auto_start=True,
        account_ids=["account-1", "account-2"],
        followed_article_id="article-1",
    )

    assert tabs.calls == [wizard]
    assert tabs.value is wizard
    assert state.pending_rewrite == {
        "title": "测试文章",
        "url": "https://mp.weixin.qq.com/s/test",
        "source": "选题库",
        "auto_start": True,
        "account_ids": ["account-1", "account-2"],
        "followed_article_id": "article-1",
        "topic_item_id": "",
    }
    assert notifications == ["已带去工作台，正在开始生成"]
