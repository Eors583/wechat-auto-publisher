from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.ui.image_proxy import validate_wechat_image_url, wechat_image_proxy_url


def test_wechat_image_proxy_url_preserves_source_query() -> None:
    source = "https://mmbiz.qpic.cn/a/0?wx_fmt=jpeg&from=appmsg"
    local = wechat_image_proxy_url(source)
    assert urlsplit(local).path == "/_preview/wechat-image"
    assert parse_qs(urlsplit(local).query)["url"] == [source]


def test_wechat_image_proxy_restricts_remote_host() -> None:
    with pytest.raises(ValueError, match="只允许"):
        validate_wechat_image_url("http://127.0.0.1/private")
    with pytest.raises(ValueError, match="只允许"):
        validate_wechat_image_url("https://example.com/image.jpg")


def test_wechat_image_proxy_upgrades_allowed_http_source() -> None:
    assert validate_wechat_image_url("http://mmbiz.qpic.cn/a.jpg") == (
        "https://mmbiz.qpic.cn/a.jpg"
    )
