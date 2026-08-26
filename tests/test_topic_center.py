from __future__ import annotations

import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.ai.model_registry import encrypt_api_key
from app.db import Database, customer_data_scope
from app.services.auth import AuthService
from app.services.billing import InsufficientCreditsError
from app.services.followed_content import FollowedContentService, group_articles
from app.services.jizhile_settings import save_jizhile_settings
from app.services.topic_sources import (
    TopicSourcePayloadError,
    TopicSourceService,
    _fetch_bing_news,
    _fetch_google_news,
    _fetch_rss,
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


def test_default_sources_are_initialized_only_once_per_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    service = TopicSourceService(Database(config["_db_path"]), config)
    original = service.db.get_topic_source
    calls = 0

    def counted(source_id: str):
        nonlocal calls
        calls += 1
        return original(source_id)

    monkeypatch.setattr(service.db, "get_topic_source", counted)
    service.list_sources()
    initialization_calls = calls
    assert initialization_calls > 0

    service.list_sources()
    service.list_topics(days=7)
    assert calls == initialization_calls
    assert (service.db.owner_user_id or "__unscoped__") in service._defaults_ensured_for


def test_default_topic_sources_are_isolated_between_users(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    root = Database(config["_db_path"])
    auth = AuthService(root)
    admin = auth.ensure_default_admin()
    customer = auth.register("topic-customer", "secret123")

    admin_service = TopicSourceService(
        root.for_user(str(admin["id"])),
        config,
    )
    customer_service = TopicSourceService(
        root.for_user(str(customer["id"])),
        config,
    )
    admin_sources = {
        str(item["source_key"]): item
        for item in admin_service.list_sources()
    }
    customer_sources = {
        str(item["source_key"]): item
        for item in customer_service.list_sources()
    }

    assert set(admin_sources) == set(customer_sources)
    assert {
        "internal-followed-accounts",
        "internal-manual-topics",
        "hot-weibo",
        "hot-baidu",
    } <= set(admin_sources)
    assert {
        str(item["id"]) for item in admin_sources.values()
    }.isdisjoint(
        {str(item["id"]) for item in customer_sources.values()}
    )

    customer_hot = customer_sources["hot-weibo"]
    customer_service.save_source(
        {
            "id": customer_hot["id"],
            "name": customer_hot["name"],
            "source_type": customer_hot["source_type"],
            "config": customer_hot["config"],
            "enabled": False,
        }
    )
    assert customer_service.db.get_topic_source("hot-weibo")["enabled"] == 0
    assert admin_service.db.get_topic_source("hot-weibo")["enabled"] == 1

    customer_service.add_manual_topic("客户自己的管理选题")
    assert customer_service.list_topics(
        source_ids=["internal-manual-topics"],
        days=7,
    )[0]["title"] == "客户自己的管理选题"
    assert admin_service.list_topics(
        source_ids=["internal-manual-topics"],
        days=7,
    ) == []


def test_default_topic_source_initialization_is_concurrently_idempotent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    root = Database(config["_db_path"])
    user = AuthService(root).register("concurrent-topics", "secret123")
    handles = [root.for_user(str(user["id"])) for _ in range(4)]

    with ThreadPoolExecutor(max_workers=4) as executor:
        services = list(
            executor.map(
                lambda db: TopicSourceService(db, config),
                handles,
            )
        )

    sources = services[0].list_sources()
    source_keys = [str(item["source_key"]) for item in sources]
    assert len(source_keys) == len(set(source_keys))
    assert len(source_keys) == 6


def test_topic_search_worker_preserves_authenticated_owner_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    root = Database(config["_db_path"])
    auth = AuthService(root)
    first = auth.register("search-owner-a", "secret123")
    second = auth.register("search-owner-b", "secret123")
    shared_service = TopicSourceService(root, config)
    monkeypatch.setattr(
        shared_service,
        "_search_source",
        lambda source, **_kwargs: [
            {
                "title": "线程作用域选题",
                "url": "https://example.com/thread-scope",
                "published_at": "2026-08-04T00:00:00+00:00",
            }
        ]
        if str(source.get("source_type")) == "rss"
        else [],
    )

    with customer_data_scope(str(first["id"])):
        result = shared_service.search("线程", days=7)

    assert result["total"] == 1
    assert root.for_user(str(first["id"])).list_topic_items(
        keyword="线程"
    )
    assert root.for_user(str(second["id"])).list_topic_items(
        keyword="线程"
    ) == []


def test_legacy_topic_sources_receive_source_key_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-topic-sources.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE topic_sources (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                config_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_synced_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO topic_sources (
                id, owner_user_id, name, source_type,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "hot-weibo",
                "legacy-owner",
                "微博热榜",
                "hot_api",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
            ),
        )

    migrated = Database(path, owner_user_id="legacy-owner")
    source = migrated.get_topic_source("hot-weibo")

    assert source is not None
    assert source["id"] == "hot-weibo"
    assert source["source_key"] == "hot-weibo"


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
    service.list_sources()
    source = service.db.get_topic_source("hot-weibo")
    assert source["config"]["provider"] == "weibo"
    assert source["config"]["url"] == "https://weibo.com/ajax/side/hotSearch"
    assert source["enabled"] == 0


def test_legacy_followed_accounts_are_isolated_between_users(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    root = Database(config["_db_path"])
    auth = AuthService(root)
    admin = auth.ensure_default_admin()
    customer = auth.register("followed-customer", "secret123")

    admin_service = FollowedContentService(
        root.for_user(str(admin["id"])),
        config,
    )
    customer_service = FollowedContentService(
        root.for_user(str(customer["id"])),
        config,
    )
    admin_accounts = {
        str(item["name"]): item for item in admin_service.list_accounts()
    }
    customer_accounts = {
        str(item["name"]): item for item in customer_service.list_accounts()
    }

    assert set(admin_accounts) == set(customer_accounts)
    assert {
        str(item["id"]) for item in admin_accounts.values()
    }.isdisjoint(
        {str(item["id"]) for item in customer_accounts.values()}
    )


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

    class FakeClient:
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

    factory_calls: list[tuple[object, object, str, str]] = []

    def fake_build_wechat_client(config, factory_db, app_id, app_secret):
        factory_calls.append((config, factory_db, app_id, app_secret))
        return FakeClient()

    monkeypatch.setattr(
        "app.services.followed_content.build_wechat_client",
        fake_build_wechat_client,
    )
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda _url: (_ for _ in ()).throw(httpx.ReadError("blocked")),
    )
    report = service.discover_account(followed["id"])
    assert report["added"] == 1
    article = service.list_articles(account_ids=[followed["id"]], days=3650)[0]
    assert article["title"] == "公众号官方文章"
    assert article["source_channel"] == "wechat_official"
    assert factory_calls == [(config, db, "wx-test", "secret")]


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


def test_jizhile_articles_enter_the_followed_pool_with_trusted_account(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    service = FollowedContentService(db, config)
    account = service.save_account(
        {
            "name": "极致了测试公众号",
            "wechat_id": "jizhile-test",
            "fetch_method": "jizhile_api",
            "enabled": True,
        }
    )
    save_jizhile_settings(
        service.db,
        enabled=True,
        key="private-key",
        verifycode="private-code",
        session_label="测试账户",
    )
    calls: list[dict[str, object]] = []

    def fetch(name: str, **kwargs) -> list[dict[str, str]]:
        calls.append({"name": name, **kwargs})
        return [
            {
                "title": "极致了接口返回文章",
                "url": "https://mp.weixin.qq.com/s/jizhile-article",
                "snippet": "接口摘要",
                "account_name": "极致了测试公众号",
                "published_at": "2026-08-11T06:27:58+00:00",
                "external_key": "jizhile-sn-1",
            }
        ]

    monkeypatch.setattr(
        "app.services.followed_content.fetch_jizhile_account_articles",
        fetch,
    )
    monkeypatch.setattr(
        "app.services.followed_content.fetch_public_article_metadata",
        lambda _url: (_ for _ in ()).throw(httpx.ReadError("blocked")),
    )

    report = service.discover_account(account["id"])

    assert report["added"] == 1
    article = service.list_articles(account_ids=[account["id"]], days=3650)[0]
    assert article["title"] == "极致了接口返回文章"
    assert article["account_name"] == "极致了测试公众号"
    assert article["source_channel"] == "jizhile_api"
    assert article["external_key"] == "jizhile-sn-1"
    assert calls == [
        {
            "name": "极致了测试公众号",
            "wechat_id": "jizhile-test",
            "sample_url": "",
            "key": "private-key",
            "verifycode": "private-code",
            "limit": 8,
        }
    ]


def test_backend_search_falls_back_to_jizhile_when_login_state_expires(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    service = FollowedContentService(Database(config["_db_path"]), config)
    account = service.save_account(
        {
            "name": "自动回退公众号",
            "wechat_id": "fallback-account",
            "fetch_method": "backend_search",
            "enabled": True,
        }
    )
    service.save_backend_search_settings(
        enabled=True,
        token="expired-token",
        cookie="session=expired",
    )
    save_jizhile_settings(
        service.db,
        enabled=True,
        key="working-key",
        verifycode="working-code",
    )
    calls: list[str] = []

    def failed_backend(*_args, **_kwargs):
        calls.append("backend_search")
        raise ValueError("公众号后台登录态已失效")

    def working_jizhile(*_args, **_kwargs):
        calls.append("jizhile_api")
        return [
            {
                "title": "自动回退成功文章",
                "url": "https://mp.weixin.qq.com/s/automatic-fallback",
                "account_name": "自动回退公众号",
                "published_at": "2026-08-11T08:00:00+00:00",
                "external_key": "fallback-1",
            }
        ]

    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        failed_backend,
    )
    monkeypatch.setattr(
        "app.services.followed_content.fetch_jizhile_account_articles",
        working_jizhile,
    )

    report = service.discover_account(account["id"])

    assert report["error"] == ""
    assert report["source_method"] == "jizhile_api"
    assert report["source_label"] == "极致了 API（实时文章）"
    assert "已自动切换到极致了 API" in report["warning"]
    assert [item["status"] for item in report["attempts"]] == ["failed", "success"]
    assert calls == ["backend_search", "jizhile_api"]
    articles = service.list_articles(account_ids=[account["id"]], days=3650)
    assert articles[0]["source_channel"] == "jizhile_api"


def test_jizhile_falls_back_to_backend_search_when_api_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    service = FollowedContentService(Database(config["_db_path"]), config)
    account = service.save_account(
        {
            "name": "反向回退公众号",
            "fetch_method": "jizhile_api",
            "enabled": True,
        }
    )
    save_jizhile_settings(service.db, enabled=True, key="unavailable-key")
    service.save_backend_search_settings(
        enabled=True,
        token="working-token",
        cookie="session=working",
    )
    calls: list[str] = []

    def failed_jizhile(*_args, **_kwargs):
        calls.append("jizhile_api")
        raise ValueError("极致了 API 暂时不可用")

    def working_backend(*_args, **_kwargs):
        calls.append("backend_search")
        return [
            {
                "title": "后台回退成功文章",
                "url": "https://mp.weixin.qq.com/s/backend-fallback",
                "account_name": "反向回退公众号",
                "published_at": "2026-08-11T08:00:00+00:00",
                "external_key": "backend-fallback-1",
            }
        ]

    monkeypatch.setattr(
        "app.services.followed_content.fetch_jizhile_account_articles",
        failed_jizhile,
    )
    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        working_backend,
    )

    report = service.discover_account(account["id"])

    assert report["error"] == ""
    assert report["source_method"] == "backend_search"
    assert "已自动切换到公众号后台搜索" in report["warning"]
    assert calls == ["jizhile_api", "backend_search"]
    articles = service.list_articles(account_ids=[account["id"]], days=3650)
    assert articles[0]["source_channel"] == "wechat_backend_search"


def test_followed_account_reports_all_enabled_source_failures(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    service = FollowedContentService(Database(config["_db_path"]), config)
    account = service.save_account(
        {"name": "全部失败公众号", "fetch_method": "backend_search", "enabled": True}
    )
    service.save_backend_search_settings(
        enabled=True,
        token="expired-token",
        cookie="session=expired",
    )
    save_jizhile_settings(service.db, enabled=True, key="invalid-key")
    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("登录态失效")),
    )
    monkeypatch.setattr(
        "app.services.followed_content.fetch_jizhile_account_articles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("余额或凭证不可用")),
    )

    report = service.discover_account(account["id"])

    assert report["source_method"] == ""
    assert "公众号后台搜索" in report["error"]
    assert "极致了 API" in report["error"]
    assert [item["status"] for item in report["attempts"]] == ["failed", "failed"]


def test_followed_article_refresh_charges_once_and_failure_refunds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    root = Database(config["_db_path"])
    user = AuthService(root).register("refresh-billing-user", "secure-pass-123")
    db = root.for_user(str(user["id"]))
    service = FollowedContentService(db, config)
    policy = root.get_billing_pricing_policy()
    root.upsert_billing_pricing_policy({**policy, "mode": "live"})
    db.grant_credit_points(points=100, source_type="test")
    account = service.save_account(
        {
            "name": "积分测试公众号",
            "fetch_method": "backend_search",
            "enabled": True,
        }
    )
    service.save_backend_search_settings(
        enabled=True,
        token="working-token",
        cookie="session=working",
    )
    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        lambda *_args, **_kwargs: [
            {
                "title": "计费成功文章",
                "url": "https://mp.weixin.qq.com/s/billed-refresh",
                "account_name": "积分测试公众号",
                "published_at": "2026-08-26T08:00:00+00:00",
            }
        ],
    )

    success = service.discover_account(account["id"])

    assert success["points"] == 20
    assert success["points_charged"] == 20
    assert success["billing_status"] == "succeeded"
    assert db.credit_wallet_summary()["available"] == 80

    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("上游暂时不可用")
        ),
    )
    failed = service.discover_account(account["id"])

    assert failed["error"]
    assert failed["points"] == 0
    assert failed["points_charged"] == 0
    assert failed["billing_status"] == "failed"
    assert db.credit_wallet_summary() == {
        "available": 80,
        "reserved": 0,
        "charged": 20,
    }


def test_refresh_all_checks_total_points_before_calling_any_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    config["topics"]["peers"] = []
    root = Database(config["_db_path"])
    user = AuthService(root).register("batch-refresh-user", "secure-pass-123")
    db = root.for_user(str(user["id"]))
    service = FollowedContentService(db, config)
    policy = root.get_billing_pricing_policy()
    root.upsert_billing_pricing_policy({**policy, "mode": "live"})
    db.grant_credit_points(points=10, source_type="test")
    for name in ("批量公众号一", "批量公众号二"):
        service.save_account(
            {"name": name, "fetch_method": "backend_search", "enabled": True}
        )
    calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        lambda *_args, **_kwargs: calls.append(True) or [],
    )

    with pytest.raises(InsufficientCreditsError, match="需冻结 40 积分"):
        service.discover_all()

    assert calls == []
    assert db.list_usage_operations() == []
    assert db.credit_wallet_summary()["available"] == 10


def test_working_preferred_source_does_not_charge_fallback_source(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    service = FollowedContentService(Database(config["_db_path"]), config)
    account = service.save_account(
        {"name": "首选可用公众号", "fetch_method": "backend_search", "enabled": True}
    )
    service.save_backend_search_settings(
        enabled=True,
        token="working-token",
        cookie="session=working",
    )
    save_jizhile_settings(service.db, enabled=True, key="paid-api-key")
    monkeypatch.setattr(
        "app.services.followed_content.search_backend_account_articles",
        lambda *_args, **_kwargs: [
            {
                "title": "首选源文章",
                "url": "https://mp.weixin.qq.com/s/preferred-source",
                "account_name": "首选可用公众号",
                "published_at": "2026-08-11T08:00:00+00:00",
            }
        ],
    )
    fallback_calls: list[bool] = []
    monkeypatch.setattr(
        "app.services.followed_content.fetch_jizhile_account_articles",
        lambda *_args, **_kwargs: fallback_calls.append(True) or [],
    )

    report = service.discover_account(account["id"])

    assert report["source_method"] == "backend_search"
    assert report["warning"] == ""
    assert fallback_calls == []


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


def test_topic_pagination_returns_total_and_non_overlapping_pages(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    service = TopicSourceService(Database(config["_db_path"]), config)
    for index in range(9):
        service.add_manual_topic(f"分页选题 {index + 1}")

    first = service.paginate_topics(
        source_ids=["internal-manual-topics"],
        days=7,
        page=1,
        page_size=4,
    )
    second = service.paginate_topics(
        source_ids=["internal-manual-topics"],
        days=7,
        page=2,
        page_size=4,
    )
    last = service.paginate_topics(
        source_ids=["internal-manual-topics"],
        days=7,
        page=99,
        page_size=4,
    )

    assert first["total"] == second["total"] == last["total"] == 9
    assert first["page_count"] == second["page_count"] == last["page_count"] == 3
    assert first["page"] == 1
    assert second["page"] == 2
    assert last["page"] == 3
    assert len(first["items"]) == len(second["items"]) == 4
    assert len(last["items"]) == 1
    assert not (
        {row["id"] for row in first["items"]}
        & {row["id"] for row in second["items"]}
    )


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


def test_rss_html_challenge_is_reported_as_upstream_payload_error() -> None:
    response = httpx.Response(
        200,
        text="<!DOCTYPE html><html><body>security challenge</body></html>",
        request=httpx.Request("GET", "https://36kr.com/feed"),
    )

    class FakeClient:
        def get(self, _url, **_kwargs):
            return response

    with pytest.raises(TopicSourcePayloadError, match="网页而不是 RSS"):
        _fetch_rss(FakeClient(), "https://36kr.com/feed")


def test_bing_news_invalid_rss_falls_back_to_web_results() -> None:
    html = """
    <ol><li class="b_algo"><h2><a href="https://example.com/ai-news">
    AI 管理新趋势</a></h2><div><p>2026年7月21日，企业管理正在变化。</p></div></li></ol>
    """
    responses = [
        httpx.Response(
            200,
            text="<!DOCTYPE html><html>not rss</html>",
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


def test_36kr_rss_search_falls_back_to_official_gateway(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rss_response = httpx.Response(
        200,
        text="<!DOCTYPE html><html>security challenge</html>",
        request=httpx.Request("GET", "https://36kr.com/feed"),
    )
    gateway_response = httpx.Response(
        200,
        json={
            "code": 0,
            "data": {
                "itemList": [
                    {
                        "itemId": "12345",
                        "templateMaterial": {
                            "widgetTitle": "AI 管理热点",
                            "widgetContent": "企业正在应用 AI 改造管理流程。",
                            "publishTime": 1784599200000,
                        },
                    }
                ]
            },
        },
        request=httpx.Request(
            "POST", "https://gateway.36kr.com/api/mis/nav/newsflash/flow"
        ),
    )

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _url, **_kwargs):
            return rss_response

        def post(self, _url, **_kwargs):
            return gateway_response

    monkeypatch.setattr("app.services.topic_sources.httpx.Client", FakeClient)
    config = _config(tmp_path)
    service = TopicSourceService(Database(config["_db_path"]), config)
    source = {
        "id": "rss-36kr",
        "name": "36氪",
        "source_type": "rss",
        "config": {"url": "https://36kr.com/feed"},
    }

    result = service._search_source(source, keyword="AI 管理", days=7, timeout=5)

    assert result[0]["title"] == "AI 管理热点"
    assert result[0]["url"] == "https://36kr.com/newsflashes/12345"
    assert result[0]["raw"]["discovery"] == "36kr_gateway"


def test_google_news_rss_fallback_parses_results() -> None:
    response = httpx.Response(
        200,
        text="""
        <rss><channel><item><title>AI 管理行业观察</title>
        <link>https://example.com/google-news</link>
        <pubDate>Tue, 21 Jul 2026 02:00:00 GMT</pubDate></item></channel></rss>
        """,
        request=httpx.Request("GET", "https://news.google.com/rss/search"),
    )

    class FakeClient:
        def get(self, _url, **_kwargs):
            return response

    result = _fetch_google_news(FakeClient(), "AI 管理", limit=5)
    assert result[0]["title"] == "AI 管理行业观察"
