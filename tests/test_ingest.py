from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.providers import ingest


class _Response:
    text = "<html>blocked</html>"

    def raise_for_status(self) -> None:
        return None


class _Client:
    last_headers = {}

    def __init__(self, **_kwargs) -> None:
        type(self).last_headers = dict(_kwargs.get("headers") or {})

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, _url: str) -> _Response:
        return _Response()


def test_environment_error_page_is_rejected_when_recovery_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.wechat_layout_import.fetch_wechat_article_layout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("微信公众号返回了“环境异常”拦截页，未获取到真实文章正文")
        ),
    )

    with pytest.raises(ValueError, match="环境异常.*未获取到真实文章正文"):
        ingest.ingest_url("https://mp.weixin.qq.com/s/blocked")


def test_environment_error_page_recovers_through_existing_reader(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.wechat_layout_import.fetch_wechat_article_layout",
        lambda *_args, **_kwargs: SimpleNamespace(
            title="一篇真实的公众号文章",
            content_html=(
                '<div id="js_content"><p>第一段真实正文介绍事件背景和重要事实。</p>'
                '<p>第二段继续解释原因、影响和最终结论，内容足够完整。</p>'
                '<img data-src="https://mmbiz.qpic.cn/example.jpg"></div>'
            ),
        ),
    )

    result = ingest.ingest_url("https://mp.weixin.qq.com/s/recovered")

    assert result.title == "一篇真实的公众号文章"
    assert "第二段继续解释原因" in result.content
    assert result.images == ["https://mmbiz.qpic.cn/example.jpg"]
    assert result.meta["extractor"] == "wechat-multichannel"


def test_generic_url_uses_browser_headers(monkeypatch) -> None:
    monkeypatch.setattr(ingest.httpx, "Client", _Client)
    monkeypatch.setattr(
        ingest,
        "_extract_with_trafilatura",
        lambda _html, _url: (
            "普通文章",
            "这是普通网页的完整正文内容。" * 10,
            [],
        ),
    )

    result = ingest.ingest_url("https://example.com/article")

    assert result.title == "普通文章"
    assert "Chrome/151" in _Client.last_headers["User-Agent"]
    assert _Client.last_headers["Referer"] == "https://mp.weixin.qq.com/"
    assert _Client.last_headers["Accept-Language"].startswith("zh-CN")
