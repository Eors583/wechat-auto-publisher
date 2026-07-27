from __future__ import annotations

import json

import httpx
import pytest

from app.providers import wechat_backend_search as provider
from app.providers.wechat_backend_search import (
    WechatBackendSearchError,
    search_backend_account_articles,
    test_backend_session as verify_backend_session,
)


class _FakeClient:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, path, *, params):
        payload = self.handler(path, params)
        request = httpx.Request("GET", f"https://mp.weixin.qq.com{path}")
        return httpx.Response(200, json=payload, request=request)


def test_backend_search_exactly_matches_account_and_parses_nested_articles(
    monkeypatch,
) -> None:
    seen: list[tuple[str, dict]] = []

    def handler(path: str, params: dict):
        seen.append((path, params))
        if path == "/cgi-bin/searchbiz":
            return {
                "base_resp": {"ret": 0},
                "list": [
                    {"nickname": "蓝血悦读", "alias": "other", "fakeid": "fake-1"},
                    {"nickname": "蓝血研究", "alias": "lanxueyanjiu", "fakeid": "fake-2"},
                ],
            }
        assert params["fakeid"] == "fake-2"
        return {
            "base_resp": {"ret": 0},
            "publish_page": json.dumps(
                {
                    "publish_list": [
                        {
                            "publish_info": json.dumps(
                                {
                                    "appmsgex": [
                                        {
                                            "aid": "aid-1",
                                            "title": "经营体系如何落地",
                                            "link": "https://mp.weixin.qq.com/s/article-1",
                                            "digest": "文章摘要",
                                            "cover": "https://mmbiz.qpic.cn/cover.jpg",
                                            "create_time": 1784592000,
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(
        provider,
        "_client",
        lambda **_kwargs: _FakeClient(handler),
    )
    rows = search_backend_account_articles(
        "蓝血研究",
        wechat_id="lanxueyanjiu",
        token="123456",
        cookie="session=test",
        limit=8,
    )
    assert rows == [
        {
            "title": "经营体系如何落地",
            "url": "https://mp.weixin.qq.com/s/article-1",
            "snippet": "文章摘要",
            "cover_url": "https://mmbiz.qpic.cn/cover.jpg",
            "account_name": "蓝血研究",
            "wechat_id": "lanxueyanjiu",
            "published_at": "2026-07-21T00:00:00+00:00",
            "external_key": "aid-1",
        }
    ]
    assert [item[0] for item in seen] == [
        "/cgi-bin/searchbiz",
        "/cgi-bin/appmsgpublish",
    ]


def test_backend_search_rejects_similar_but_different_account(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_client",
        lambda **_kwargs: _FakeClient(
            lambda _path, _params: {
                "base_resp": {"ret": 0},
                "list": [{"nickname": "蓝血悦读", "fakeid": "fake-1"}],
            }
        ),
    )
    with pytest.raises(WechatBackendSearchError, match="完全匹配"):
        search_backend_account_articles(
            "蓝血研究", token="123456", cookie="session=test"
        )


def test_backend_search_reads_additional_publish_pages(monkeypatch) -> None:
    page_requests: list[tuple[int, int]] = []

    def handler(path: str, params: dict):
        if path == "/cgi-bin/searchbiz":
            return {
                "base_resp": {"ret": 0},
                "list": [{"nickname": "蓝血研究", "fakeid": "fake-2"}],
            }
        begin = int(params["begin"])
        count = int(params["count"])
        page_requests.append((begin, count))
        groups = []
        for index in range(begin, begin + count):
            groups.append(
                {
                    "publish_info": json.dumps(
                        {
                            "appmsgex": [
                                {
                                    "aid": f"aid-{index}",
                                    "title": f"文章 {index}",
                                    "link": f"https://mp.weixin.qq.com/s/article-{index}",
                                    "create_time": 1784592000 - index,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            )
        return {
            "base_resp": {"ret": 0},
            "publish_page": json.dumps(
                {"publish_list": groups}, ensure_ascii=False
            ),
        }

    monkeypatch.setattr(provider, "_client", lambda **_kwargs: _FakeClient(handler))
    rows = search_backend_account_articles(
        "蓝血研究",
        token="123456",
        cookie="session=test",
        limit=25,
    )

    assert len(rows) == 25
    assert page_requests == [(0, 20), (20, 20)]
    assert rows[0]["external_key"] == "aid-0"
    assert rows[-1]["external_key"] == "aid-24"


def test_backend_session_reports_expired_login(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_client",
        lambda **_kwargs: _FakeClient(
            lambda _path, _params: {
                "base_resp": {"ret": 200003, "err_msg": "invalid session"}
            }
        ),
    )
    with pytest.raises(WechatBackendSearchError, match="登录态已过期"):
        verify_backend_session(token="123456", cookie="session=test")
