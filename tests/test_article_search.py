from __future__ import annotations

from types import SimpleNamespace

from app.providers.article_search import (
    _reconstruct_sogou_article_url,
    _search_sogou,
)
from app.providers.public_wechat import normalize_article_url


def test_reconstructs_wechat_url_from_sogou_javascript() -> None:
    page = """
    <script>
      var url = '';
      url += 'https://mp.weixin.qq.com/s?__biz=abc';
      url += '@&timestamp=456&mid=123&idx=1';
      url = url.replace("@", "");
    </script>
    """
    assert _reconstruct_sogou_article_url(page) == (
        "https://mp.weixin.qq.com/s?__biz=abc&timestamp=456&mid=123&idx=1"
    )


def test_normalize_wechat_url_preserves_timestamp_parameter() -> None:
    url = "https://mp.weixin.qq.com/s?src=11&timestamp=456&ver=7"
    normalized = normalize_article_url(url)
    assert "timestamp=456" in normalized
    assert "×tamp" not in normalized


def test_sogou_search_resolves_result_with_search_session(monkeypatch) -> None:
    search_page = """
    <html><body>
      <a href="/link?url=opaque-token" uigs="article_title_0">经营系统如何落地</a>
    </body></html>
    """
    redirect_page = """
    <script>
      var url = '';
      url += 'https://mp.weixin.qq.com/s?__biz=abc';
      url += '@&timestamp=456&mid=123&idx=1';
      url = url.replace("@", "");
    </script>
    """

    class FakeClient:
        created = 0

        def __init__(self, **_kwargs):
            self.index = FakeClient.created
            FakeClient.created += 1
            self.cookies = {"SNUID": "session-cookie"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            return None

        def get(self, url: str):
            if self.index == 0:
                return SimpleNamespace(
                    status_code=200,
                    text=search_page,
                    url=url,
                )
            assert self.cookies.get("SNUID") == "session-cookie"
            return SimpleNamespace(
                status_code=200,
                text=redirect_page,
                url=url,
            )

    monkeypatch.setattr("app.providers.article_search.httpx.Client", FakeClient)
    rows = _search_sogou("蓝血研究", limit=3, timeout=5)
    assert rows == [
        {
            "title": "经营系统如何落地",
            "url": "https://mp.weixin.qq.com/s?__biz=abc&timestamp=456&mid=123&idx=1",
            "snippet": "",
            "account_name": "",
            "published_at": "",
        }
    ]
