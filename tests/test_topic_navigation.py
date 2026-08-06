from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app.ui.panels.followed_articles import (
    ARTICLE_LOAD_MAX,
    followed_article_cover_preview_url,
    next_followed_article_fetch_limit,
)
from app.ui.panels.topics import TOPIC_CENTER_TABS, _queue_for_wizard


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
