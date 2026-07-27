from __future__ import annotations

import hashlib
import base64
from pathlib import Path

import httpx
import pytest

from app.ai.model_registry import encrypt_api_key
from app.db import Database
from app.services.followed_content import FollowedContentService, group_articles
from app.services.topic_sources import (
    TopicSourceService,
    _fetch_bing_news,
    _parse_baidu_hot_payload,
    _parse_weibo_hot_payload,
)


def _config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "_data_dir": str(tmp_path / "data"),
        "_db_path": str(tmp_path / "app.db"),
        "topics": {
            "rss_sources": [{"name": "36氪", "url": "https://36kr.com/feed"}],
            "news_queries": ["企业管理"],
            "peers": [
                {"name": "蓝血研究"},
                {"name": "项目管理评论", "wechat_id": "pmreview"},
            ],
        },
    }


def test_topic_sources_are_bootstrapped_and_refreshed_independently(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = TopicSourceService(db, config)
    names = {item["name"] for item in service.list_sources()}
    assert {"36氪", "企业管理资讯", "微博热榜", "百度热榜", "关注公众号", "手动选题库"} <= names

    custom = service.save_source(
        {
            "name": "测试 RSS",
            "source_type": "rss",
            "config": {"url": "https://example.com/feed"},
        }
    )
    monkeypatch.setattr(
        service,
        "_fetch_source",
        lambda source, timeout: [
            {
                "title": "企业组织的新变化",
                "url": "https://example.com/a",
                "published_at": "2026-07-21T01:00:00+00:00",
            }
        ],
    )
    report = service.refresh([custom["id"]])
    assert report["total"] == 1
    items = service.list_topics(source_ids=[custom["id"]], days=365)
    assert items[0]["source_name"] == "测试 RSS"
    assert items[0]["title"] == "企业组织的新变化"


def test_legacy_hot_sources_are_migrated_to_direct_platform_endpoints(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    db.upsert_topic_source(
        {
            "id": "hot-weibo",
            "name": "微博热榜",
            "source_type": "hot_api",
            "config": {"url": "https://api.vvhan.com/api/hotlist/wbHot"},
            "enabled": False,
        }
    )
    service = TopicSourceService(db, config)
    source = service.db.get_topic_source("hot-weibo")
    assert source["config"]["provider"] == "weibo"
    assert source["config"]["url"] == "https://weibo.com/ajax/side/hotSearch"
    assert source["enabled"] == 0


def test_native_weibo_and_baidu_hot_payloads_are_parsed() -> None:
    weibo = _parse_weibo_hot_payload(
        {
            "data": {
                "realtime": [
                    {"word": "AI 管理", "word_scheme": "AI 管理", "num": 123, "realpos": 1}
                ]
            }
        }
    )
    assert weibo[0]["title"] == "AI 管理"
    assert "s.weibo.com/weibo" in weibo[0]["url"]
    assert weibo[0]["raw"]["provider"] == "weibo"

    baidu = _parse_baidu_hot_payload(
        {
            "data": {
                "cards": [
                    {
                        "content": [
                            {
                                "content": [
                                    {"word": "组织变革", "url": "https://m.baidu.com/s?word=x", "index": 2}
                                ]
                            }
                        ]
                    }
                ]
            }
        }
    )
    assert baidu[0]["title"] == "组织变革"
    assert baidu[0]["raw"]["provider"] == "baidu"


def test_followed_accounts_import_legacy_peers_only_once(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    assert {item["name"] for item in service.list_accounts()} == {"项目管理评论", "蓝血研究"}
    assert {item["fetch_method"] for item in service.list_accounts()} == {"backend_search"}
    FollowedContentService(db, config)
    assert len(service.list_accounts()) == 2


def test_saving_same_followed_account_name_does_not_create_duplicate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    first = service.save_account({"name": "蓝血研究", "fetch_method": "public_search"})
    second = service.save_account({"name": " 蓝血研究 ", "fetch_method": "manual"})
    assert first["id"] == second["id"]
    assert len([row for row in service.list_accounts() if row["name"].strip() == "蓝血研究"]) == 1


def test_owned_account_reads_wechat_official_publish_records(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    db.upsert_official_account(
        {
            "id": "owned-1",
            "name": "自有公众号",
            "app_id": "wx-test",
            "app_secret_encrypted": encrypt_api_key("secret"),
            "model_id": "model-1",
            "enabled": True,
        }
    )
    service = FollowedContentService(db, config)
    followed = service.save_account(
        {
            "name": "自有公众号",
            "fetch_method": "official",
            "official_account_id": "owned-1",
            "enabled": True,
        }
    )

    class FakeAuth:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_access_token(self, force_refresh=False):
            return "token"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            return {
                "item": [
                    {
                        "update_time": 1784592000,
                        "content": {
                            "news_item": [
                                {
                                    "title": "公众号官方文章",
                                    "url": "https://mp.weixin.qq.com/s/official-article",
                                    "digest": "官方摘要",
                                }
                            ]
                        },
                    }
                ]
            }

    monkeypatch.setattr("app.services.followed_content.WeChatAuth", FakeAuth)
    monkeypatch.setattr("app.services.followed_content.WeChatClient", FakeClient)
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda _url: (_ for _ in ()).throw(httpx.ReadError("blocked")),
    )
    report = service.discover_account(followed["id"])
    assert report["added"] == 1
    article = service.list_articles(account_ids=[followed["id"]], days=3650)[0]
    assert article["title"] == "公众号官方文章"
    assert article["source_channel"] == "wechat_official"


def test_backend_search_articles_enter_the_followed_pool_with_trusted_account(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    account = service.save_account(
        {
            "name": "蓝血研究",
            "wechat_id": "lanxueyanjiu",
            "fetch_method": "backend_search",
            "enabled": True,
        }
    )
    service.save_backend_search_settings(
        enabled=True,
        token="123456",
        cookie="session=test",
        session_label="测试账号",
    )
    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        lambda *_args, **_kwargs: [
            {
                "title": "后台检索到的文章",
                "url": "https://mp.weixin.qq.com/s/backend-article",
                "snippet": "摘要",
                "account_name": "蓝血研究",
                "published_at": "2026-07-21T00:00:00+00:00",
                "external_key": "aid-1",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda _url: (_ for _ in ()).throw(httpx.ReadError("blocked")),
    )
    report = service.discover_account(account["id"])
    assert report["added"] == 1
    article = service.list_articles(account_ids=[account["id"]], days=3650)[0]
    assert article["title"] == "后台检索到的文章"
    assert article["account_name"] == "蓝血研究"
    assert article["source_channel"] == "wechat_backend_search"
    assert article["external_key"] == "aid-1"


def test_backend_search_cumulative_window_loads_older_articles_without_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    account = service.save_account(
        {
            "name": "分页测试公众号",
            "fetch_method": "backend_search",
            "enabled": True,
        }
    )
    service.save_backend_search_settings(
        enabled=True,
        token="123456",
        cookie="session=test",
    )

    def search(_name: str, **kwargs) -> list[dict[str, str]]:
        return [
            {
                "title": f"分页文章 {index}",
                "url": f"https://mp.weixin.qq.com/s/page-{index}",
                "account_name": "分页测试公众号",
                "published_at": "2026-07-21T00:00:00+00:00",
                "external_key": f"aid-{index}",
            }
            for index in range(int(kwargs["limit"]))
        ]

    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        search,
    )
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda _url: (_ for _ in ()).throw(httpx.ReadError("blocked")),
    )

    service.discover_account(account["id"], limit=8)
    service.discover_account(account["id"], limit=16)

    articles = service.list_articles(account_ids=[account["id"]], days=3650)
    assert len(articles) == 16
    assert len({article["external_key"] for article in articles}) == 16


def test_legacy_public_search_accounts_are_automatically_migrated(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    db.upsert_followed_account(
        {
            "id": "legacy-public-search",
            "name": "历史关注公众号",
            "fetch_method": "public_search",
            "enabled": True,
        }
    )
    service = FollowedContentService(db, config)
    account = service.db.get_followed_account("legacy-public-search")
    assert account is not None
    assert account["fetch_method"] == "backend_search"


def test_article_ingestion_deduplicates_normalized_wechat_url(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    account = next(item for item in service.list_accounts() if item["name"] == "蓝血研究")
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda url: {
            "title": "经营系统如何落地",
            "account_name": "蓝血研究",
            "url": url,
            "published_at": "2026-07-21T01:00:00+00:00",
            "cover_url": "https://img.example.com/cover.jpg",
            "summary": "摘要",
            "external_key": "biz|1|1",
        },
    )
    base = "https://mp.weixin.qq.com/s?__biz=biz&mid=1&idx=1&sn=abc"
    first = service.add_article_url(
        base + "&scene=1", followed_account_id=account["id"]
    )
    second = service.add_article_url(
        base + "&from=timeline", followed_account_id=account["id"]
    )
    assert first["url"] == second["url"]
    assert len(service.list_articles(days=3650)) == 1


def test_article_ingestion_deduplicates_rotating_signed_wechat_urls(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    account = next(item for item in service.list_accounts() if item["name"] == "蓝血研究")
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda url: {
            "title": "同一篇公众号文章",
            "account_name": "蓝血研究",
            "url": url,
            "published_at": "2026-07-21T01:00:00+00:00",
            "external_key": "biz|123|1",
        },
    )
    first = service.add_article_url(
        "https://mp.weixin.qq.com/s?src=11&timestamp=1&signature=first",
        followed_account_id=account["id"],
    )
    second = service.add_article_url(
        "https://mp.weixin.qq.com/s?src=11&timestamp=2&signature=second",
        followed_account_id=account["id"],
    )
    assert first["id"] == second["id"]
    rows = service.list_articles(account_ids=[account["id"]], days=3650)
    assert len(rows) == 1
    assert "timestamp=2" in rows[0]["url"]


def test_followed_pool_rejects_non_wechat_and_other_account_articles(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    account = next(item for item in service.list_accounts() if item["name"] == "蓝血研究")

    with pytest.raises(ValueError, match="mp.weixin.qq.com"):
        service.add_article_url("https://36kr.com/p/example", followed_account_id=account["id"])

    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda url: {
            "title": "其他公众号文章",
            "account_name": "其他公众号",
            "url": url,
            "published_at": "2026-07-21T01:00:00+00:00",
        },
    )
    with pytest.raises(ValueError, match="不属于所选公众号"):
        service.add_article_url(
            "https://mp.weixin.qq.com/s/other-account",
            followed_account_id=account["id"],
        )


def test_recent_articles_are_scoped_to_one_followed_account(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    service = FollowedContentService(Database(config["_db_path"]), config)
    account_a = service.save_account(
        {"name": "公众号A", "fetch_method": "manual", "enabled": True}
    )
    account_b = service.save_account(
        {"name": "公众号B", "fetch_method": "manual", "enabled": True}
    )

    def metadata(url: str) -> dict[str, str]:
        name = "公众号A" if url.endswith("/account-a") else "公众号B"
        return {
            "title": f"{name}近期文章",
            "account_name": name,
            "url": url,
            "published_at": "2026-07-21T01:00:00+00:00",
        }

    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata", metadata
    )
    service.add_article_url(
        "https://mp.weixin.qq.com/s/account-a",
        followed_account_id=account_a["id"],
    )
    service.add_article_url(
        "https://mp.weixin.qq.com/s/account-b",
        followed_account_id=account_b["id"],
    )

    assert service.get_account(account_a["id"])["name"] == "公众号A"
    rows_a = service.list_articles(account_ids=[account_a["id"]], days=3650)
    rows_b = service.list_articles(account_ids=[account_b["id"]], days=3650)
    assert [row["account_name"] for row in rows_a] == ["公众号A"]
    assert [row["account_name"] for row in rows_b] == ["公众号B"]


def test_article_state_and_grouping(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda url: {
            "title": "文章一",
            "account_name": "蓝血研究",
            "url": url,
            "published_at": "2026-07-21T01:00:00+00:00",
        },
    )
    article = service.add_article_url("https://mp.weixin.qq.com/s/example")
    service.update_article(
        article["id"], is_read=True, is_favorite=True, rewritten_batch_id="batch-1"
    )
    updated = service.list_articles(days=3650)[0]
    assert updated["is_read"] == 1
    assert updated["is_favorite"] == 1
    assert updated["rewritten_batch_id"] == "batch-1"
    assert list(group_articles([updated], mode="date")) == ["2026-07-21"]
    assert list(group_articles([updated], mode="account")) == ["蓝血研究"]


def test_followed_article_list_supports_stable_offset_pagination(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)

    def metadata(url: str) -> dict:
        suffix = url.rsplit("/", 1)[-1]
        return {
            "title": f"文章 {suffix}",
            "account_name": "蓝血研究",
            "url": url,
            # Equal timestamps exercise the deterministic id tie-breaker.
            "published_at": "2026-07-21T01:00:00+00:00",
        }

    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata", metadata
    )
    for suffix in ("a", "b", "c"):
        service.add_article_url(f"https://mp.weixin.qq.com/s/{suffix}")

    all_rows = service.list_articles(days=3650, limit=3)
    first_page = service.list_articles(days=3650, limit=2, offset=0)
    second_page = service.list_articles(days=3650, limit=2, offset=2)

    assert [row["id"] for row in first_page + second_page] == [
        row["id"] for row in all_rows
    ]
    assert not ({row["id"] for row in first_page} & {row["id"] for row in second_page})


def test_manual_topic_is_queryable_without_network(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = TopicSourceService(db, config)
    service.add_manual_topic("组织效率如何提升", summary="运营手工补充")
    items = service.list_topics(
        source_ids=["internal-manual-topics"], days=7, keyword="组织效率"
    )
    assert len(items) == 1
    assert items[0]["summary"] == "运营手工补充"


def test_topic_state_update_returns_persisted_item(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = TopicSourceService(db, config)
    item = service.add_manual_topic("组织效率如何提升", summary="运营手工补充")

    favorite = service.update_topic_state(item["id"], favorite=True)
    assert favorite["favorite"] == 1
    assert favorite["used"] == 0
    assert favorite["source_name"] == "手动选题库"

    used = service.update_topic_state(item["id"], used=True)
    assert used["favorite"] == 1
    assert used["used"] == 1


def test_topic_state_update_validates_request(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = TopicSourceService(Database(config["_db_path"]), config)

    with pytest.raises(ValueError, match="至少需要更新"):
        service.update_topic_state("missing")
    with pytest.raises(KeyError, match="选题不存在"):
        service.update_topic_state("missing", favorite=True)


def test_keyword_search_runs_each_selected_source_and_keeps_real_source(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = TopicSourceService(db, config)
    selected = [
        "rss-" + hashlib.sha256(b"https://36kr.com/feed").hexdigest()[:12],
        "news-bing-management",
    ]
    seen: list[tuple[str, str]] = []

    def fake_search(source, *, keyword, days, timeout):
        seen.append((source["id"], keyword))
        return [
            {
                "title": f'{source["name"]}：AI 管理热点',
                "url": f'https://example.com/{source["id"]}',
                "published_at": "2026-07-21T02:00:00+00:00",
            }
        ]

    monkeypatch.setattr(service, "_search_source", fake_search)
    result = service.search("AI 管理", selected, days=7)
    assert seen == [(selected[0], "AI 管理"), (selected[1], "AI 管理")]
    assert result["total"] == 2
    assert {item["source_name"] for item in result["items"]} == {"36氪", "企业管理资讯"}


def test_keyword_search_manual_pool_does_not_require_network(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = TopicSourceService(db, config)
    service.add_manual_topic("AI 如何改变项目管理", summary="人工选题")
    service.add_manual_topic("组织效率提升", summary="另一个选题")
    result = service.search("AI 项目", ["internal-manual-topics"], days=7)
    assert result["total"] == 1
    assert result["items"][0]["title"] == "AI 如何改变项目管理"


def test_bing_news_empty_rss_falls_back_to_web_results() -> None:
    target = "https://example.com/ai-news"
    encoded = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    html = f"""
    <ol><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u={encoded}">
    AI 管理新趋势</a></h2><div><p>2026年7月21日，企业正在重新设计管理流程。</p></div></li></ol>
    """
    responses = [
        httpx.Response(
            200,
            content=b"<rss><channel></channel></rss>",
            request=httpx.Request("GET", "https://www.bing.com/news/search"),
        ),
        httpx.Response(
            200,
            text=html,
            request=httpx.Request("GET", "https://www.bing.com/search"),
        ),
    ]

    class FakeClient:
        def get(self, _url, **_kwargs):
            return responses.pop(0)

    result = _fetch_bing_news(FakeClient(), "AI 管理", limit=5)
    assert result[0]["title"] == "AI 管理新趋势"
    assert result[0]["url"] == target
    assert result[0]["raw"]["discovery"] == "bing_web_fallback"
