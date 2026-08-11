from __future__ import annotations

import hashlib
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.ai.model_registry import decrypt_api_key
from app.db import Database
from app.providers.jizhile_api import (
    fetch_jizhile_account_articles,
    test_jizhile_api,
)
from app.providers.public_wechat import (
    fetch_public_article_metadata,
    normalize_article_url,
)
from app.providers.wechat_backend_search import (
    normalize_backend_cookie,
    normalize_backend_token,
    search_backend_account_articles,
    test_backend_session,
)
from app.services.jizhile_settings import (
    clear_jizhile_settings,
    effective_jizhile_settings,
    public_jizhile_settings,
    save_jizhile_settings,
)
from app.services.wechat_backend_settings import (
    clear_backend_session,
    effective_backend_settings,
    public_backend_settings,
    save_backend_settings,
)
from app.wechat.factory import build_wechat_client

FETCH_METHODS = {
    "backend_search": "公众号后台搜索（需登录态）",
    "jizhile_api": "极致了 API（实时文章）",
    "manual": "仅人工投递",
    "rss": "官网 / RSS",
    "third_party": "第三方正式数据 API（预留）",
    "official": "微信官方发布记录（仅自有公众号）",
}

# Followed accounts never use search engines. RSS and third-party adapters
# remain readable for old records, but are not offered when adding an account.
# The historical ``public_search`` value is migrated to ``backend_search``.
FOLLOWED_PUBLICATION_METHODS = {
    key: FETCH_METHODS[key]
    for key in ("jizhile_api", "backend_search", "manual", "official")
}

ARTICLE_SOURCE_LABELS = {
    "jizhile_api": "极致了 API",
    "wechat_backend_search": "公众号后台搜索",
    "wechat_official": "微信官方发布记录",
    "public_search": "历史公开搜索（已停用）",
    "manual": "公众号文章链接",
    "rss": "公众号官网 / RSS",
    "third_party": "第三方正式数据 API",
}


class FollowedContentService:
    """Manage followed public accounts and their deduplicated article pool."""

    def __init__(self, db: Database, config: dict[str, Any]) -> None:
        self.db = db
        self.config = config
        self.db.prune_invalid_followed_articles()
        self.ensure_legacy_accounts_imported()
        self._merge_duplicate_accounts()
        self._migrate_legacy_public_search_accounts()

    def ensure_legacy_accounts_imported(self) -> None:
        topics = self.config.get("topics") or {}
        peers = list(topics.get("followed_accounts") or topics.get("peers") or [])
        existing_names = {_name_key(item["name"]) for item in self.db.list_followed_accounts()}
        for peer in peers:
            if not isinstance(peer, dict):
                continue
            name = str(peer.get("name") or peer.get("account") or "").strip()
            if not name or _name_key(name) in existing_names:
                continue
            account_key = name
            if self.db.owner_user_id:
                account_key = f"{self.db.owner_user_id}\0{name}"
            account_id = "follow-" + hashlib.sha256(
                account_key.encode("utf-8")
            ).hexdigest()[:16]
            self.db.upsert_followed_account(
                {
                    "id": account_id,
                    "name": name,
                    "wechat_id": str(peer.get("wechat_id") or ""),
                    "category": str(peer.get("category") or "企业管理"),
                    "tags": list(peer.get("tags") or []),
                    "keywords": list(peer.get("keywords") or []),
                    "sample_url": str(peer.get("sample_url") or ""),
                    "fetch_method": "backend_search",
                    "refresh_hours": int(peer.get("refresh_hours") or 12),
                    "enabled": True,
                }
            )
            existing_names.add(_name_key(name))

    def list_accounts(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        return self.db.list_followed_accounts(enabled_only=enabled_only)

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        """Return one followed account without exposing database access to UI code."""

        return self.db.get_followed_account(account_id)

    def import_owned_official_accounts(self) -> int:
        imported = 0
        followed = self.db.list_followed_accounts()
        by_name = {_name_key(str(item.get("name") or "")): item for item in followed}
        for official in self.db.list_official_accounts(enabled_only=True):
            name = str(official.get("name") or "").strip()
            if not name:
                continue
            existing = by_name.get(_name_key(name))
            account_id = str(
                (existing or {}).get("id")
                or "owned-follow-"
                + hashlib.sha256(str(official["id"]).encode("utf-8")).hexdigest()[:12]
            )
            self.db.upsert_followed_account(
                {
                    **dict(existing or {}),
                    "id": account_id,
                    "name": name,
                    "official_account_id": str(official["id"]),
                    "fetch_method": "official",
                    "is_owned": True,
                    "enabled": True,
                    "refresh_hours": int((existing or {}).get("refresh_hours") or 12),
                }
            )
            by_name[_name_key(name)] = self.db.get_followed_account(account_id) or {}
            imported += 1
        return imported

    def save_account(self, account: dict[str, Any]) -> dict[str, Any]:
        name = str(account.get("name") or "").strip()
        if not name:
            raise ValueError("公众号名称不能为空")
        method = str(account.get("fetch_method") or "backend_search")
        if method == "public_search":
            method = "backend_search"
        if method not in FETCH_METHODS:
            raise ValueError("不支持的抓取方式")
        if method == "rss" and not str(account.get("source_url") or "").strip():
            raise ValueError("RSS 抓取方式必须填写官网或 RSS 地址")
        official_account_id = str(account.get("official_account_id") or "").strip()
        if method == "official":
            if not official_account_id:
                raise ValueError("请选择已配置 AppID/AppSecret 的自有公众号")
            if not self.db.get_official_account(official_account_id):
                raise ValueError("所选自有公众号不存在")
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            same_name = next(
                (
                    item
                    for item in self.db.list_followed_accounts()
                    if _name_key(str(item.get("name") or "")) == _name_key(name)
                ),
                None,
            )
            account_id = str((same_name or {}).get("id") or uuid.uuid4().hex[:16])
        self.db.upsert_followed_account(
            {
                **account,
                "id": account_id,
                "name": name,
                "fetch_method": method,
                "official_account_id": official_account_id,
                "is_owned": method == "official" or bool(account.get("is_owned")),
                "tags": _string_list(account.get("tags")),
                "keywords": _string_list(account.get("keywords")),
            }
        )
        return self.db.get_followed_account(account_id) or {}

    def delete_account(self, account_id: str) -> None:
        self.db.delete_followed_account(account_id)

    def get_backend_search_settings(self) -> dict[str, Any]:
        return public_backend_settings(self.db)

    def save_backend_search_settings(
        self,
        *,
        enabled: bool,
        token: str = "",
        cookie: str = "",
        session_label: str = "",
    ) -> dict[str, Any]:
        save_backend_settings(
            self.db,
            enabled=enabled,
            token=normalize_backend_token(token),
            cookie=normalize_backend_cookie(cookie),
            session_label=session_label,
        )
        return public_backend_settings(self.db)

    def clear_backend_search_settings(self) -> dict[str, Any]:
        clear_backend_session(self.db)
        return public_backend_settings(self.db)

    def get_jizhile_settings(self) -> dict[str, Any]:
        return public_jizhile_settings(self.db)

    def save_jizhile_api_settings(
        self,
        *,
        enabled: bool,
        key: str = "",
        verifycode: str = "",
        session_label: str = "",
        remain_money: Any = None,
        checked_at: str = "",
    ) -> dict[str, Any]:
        save_jizhile_settings(
            self.db,
            enabled=enabled,
            key=key,
            verifycode=verifycode,
            session_label=session_label,
            remain_money=remain_money,
            checked_at=checked_at,
        )
        return public_jizhile_settings(self.db)

    def clear_jizhile_api_settings(self) -> dict[str, Any]:
        clear_jizhile_settings(self.db)
        return public_jizhile_settings(self.db)

    def test_jizhile_api_settings(
        self,
        *,
        key: str = "",
        verifycode: str = "",
    ) -> dict[str, Any]:
        current = effective_jizhile_settings(self.db)
        return test_jizhile_api(
            key=str(key or current.get("key") or ""),
            verifycode=str(verifycode or current.get("verifycode") or ""),
        )

    def _migrate_legacy_public_search_accounts(self) -> int:
        """Permanently retire the old Sogou/Baidu followed-account source."""

        updated = 0
        for account in self.db.list_followed_accounts():
            if str(account.get("fetch_method") or "") != "public_search":
                continue
            if bool(account.get("is_owned")):
                continue
            self.db.upsert_followed_account(
                {**account, "fetch_method": "backend_search"}
            )
            updated += 1
        return updated

    def test_backend_search_settings(
        self,
        *,
        token: str = "",
        cookie: str = "",
    ) -> dict[str, Any]:
        current = effective_backend_settings(self.db)
        return test_backend_session(
            token=normalize_backend_token(
                str(token or current.get("token") or "")
            ),
            cookie=normalize_backend_cookie(
                str(cookie or current.get("cookie") or "")
            ),
        )

    def discover_all(self, *, limit_per_account: int = 8) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        total = 0
        for account in self.db.list_followed_accounts(enabled_only=True):
            report = self.discover_account(str(account["id"]), limit=limit_per_account)
            reports.append(report)
            total += int(report.get("added") or 0)
        return {"added": total, "accounts": reports}

    def discover_account(self, account_id: str, *, limit: int = 8) -> dict[str, Any]:
        account = self.db.get_followed_account(account_id)
        if not account:
            raise KeyError("关注公众号不存在")
        preferred_method = str(account.get("fetch_method") or "backend_search")
        if preferred_method == "public_search":
            # Defensive compatibility for a row created by an older process.
            preferred_method = "backend_search"
        source_errors: list[tuple[str, str]] = []
        attempts: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        actual_method = ""
        for method in self._publication_method_order(preferred_method):
            label = FETCH_METHODS.get(method, method)
            try:
                rows = self._fetch_publication_candidates(
                    account,
                    method=method,
                    limit=limit,
                )
                attempts.append(
                    {
                        "method": method,
                        "label": label,
                        "status": "success" if rows else "empty",
                        "found": len(rows),
                    }
                )
                # A source that responds successfully is usable even when the
                # selected account has no recent posts. Continue to a fallback
                # only to maximize the chance of finding useful articles.
                if not actual_method:
                    actual_method = method
                if rows:
                    candidates = rows
                    actual_method = method
                    break
            except Exception as exc:  # noqa: BLE001
                message = str(exc).strip() or "未知错误"
                source_errors.append((label, message))
                attempts.append(
                    {
                        "method": method,
                        "label": label,
                        "status": "failed",
                        "error": message[:200],
                    }
                )

        sample_url = str(account.get("sample_url") or "").strip()
        if sample_url and actual_method and actual_method != "jizhile_api":
            candidates.insert(
                0,
                {
                    "title": "",
                    "url": sample_url,
                    "snippet": "",
                    "account_name": str(account.get("name") or ""),
                },
            )
        unique_urls: set[str] = set()
        ingestion_errors: list[str] = []
        added = 0
        for candidate in candidates:
            url = normalize_article_url(str(candidate.get("url") or ""))
            if not url or url in unique_urls:
                continue
            unique_urls.add(url)
            try:
                self.add_article_url(
                    url,
                    followed_account_id=account_id,
                    source_channel={
                        "jizhile_api": "jizhile_api",
                        "backend_search": "wechat_backend_search",
                        "rss": "rss",
                        "official": "wechat_official",
                        "third_party": "third_party",
                    }.get(actual_method, "manual"),
                    fallback_title=str(candidate.get("title") or ""),
                    fallback_summary=str(candidate.get("snippet") or ""),
                    fallback_published_at=str(candidate.get("published_at") or ""),
                    fallback_cover_url=str(candidate.get("cover_url") or ""),
                    fallback_account_name=str(candidate.get("account_name") or ""),
                    fallback_external_key=str(candidate.get("external_key") or ""),
                    tolerate_metadata_error=True,
                )
                added += 1
            except Exception as exc:  # noqa: BLE001
                ingestion_errors.append(str(exc))

        all_sources_failed = not actual_method and bool(attempts)
        error_parts: list[str] = []
        warning_parts: list[str] = []
        if all_sources_failed:
            error_parts.extend(
                f"{label}：{message[:160]}" for label, message in source_errors
            )
        elif actual_method != preferred_method:
            warning_parts.append(
                "首选数据源未返回文章或不可用，已自动切换到"
                f"{FETCH_METHODS.get(actual_method, actual_method)}"
            )
        if ingestion_errors:
            unique_ingestion_errors = list(
                dict.fromkeys(item[:160] for item in ingestion_errors if item)
            )
            if added:
                warning_parts.append(
                    f"另有 {len(unique_ingestion_errors)} 条文章因归属或格式校验未收录"
                )
            elif unique_urls:
                error_parts.extend(unique_ingestion_errors)

        error = "；".join(dict.fromkeys(item for item in error_parts if item))
        warning = "；".join(dict.fromkeys(item for item in warning_parts if item))
        self.db.update_followed_account_sync(account_id, error=error)
        return {
            "account_id": account_id,
            "name": account["name"],
            "found": len(unique_urls),
            "added": added,
            "error": error,
            "warning": warning,
            "preferred_method": preferred_method,
            "source_method": actual_method,
            "source_label": FETCH_METHODS.get(actual_method, actual_method),
            "attempts": attempts,
        }

    def _publication_method_order(self, preferred_method: str) -> list[str]:
        """Return a safe preferred-first source order for one followed account."""

        preferred = str(preferred_method or "backend_search")
        order = [preferred]
        if preferred not in {"backend_search", "jizhile_api", "official"}:
            return order

        jizhile = effective_jizhile_settings(self.db)
        backend = effective_backend_settings(self.db)
        if bool(jizhile.get("enabled")) and "jizhile_api" not in order:
            order.append("jizhile_api")
        if bool(backend.get("enabled")) and "backend_search" not in order:
            order.append("backend_search")
        return order

    def _fetch_publication_candidates(
        self,
        account: dict[str, Any],
        *,
        method: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if method == "backend_search":
            backend = effective_backend_settings(self.db)
            if not backend.get("enabled"):
                raise ValueError("公众号后台搜索尚未启用，请先配置并测试后台登录态")
            return search_backend_account_articles(
                str(account["name"]),
                wechat_id=str(account.get("wechat_id") or ""),
                token=str(backend.get("token") or ""),
                cookie=str(backend.get("cookie") or ""),
                limit=limit,
            )
        if method == "jizhile_api":
            jizhile = effective_jizhile_settings(self.db)
            if not jizhile.get("enabled"):
                raise ValueError("极致了 API 尚未启用，请先配置并测试 API Key")
            return fetch_jizhile_account_articles(
                str(account["name"]),
                wechat_id=str(account.get("wechat_id") or ""),
                sample_url=str(account.get("sample_url") or ""),
                key=str(jizhile.get("key") or ""),
                verifycode=str(jizhile.get("verifycode") or ""),
                limit=limit,
            )
        if method == "rss":
            return _read_rss(str(account.get("source_url") or ""), limit=limit)
        if method == "manual":
            return []
        if method == "official":
            return self._read_official_articles(account, limit=limit)
        if method == "third_party":
            raise ValueError("第三方正式数据 API 适配器已预留，请配置服务商后启用")
        raise ValueError(f"不支持的公众号文章获取方式：{method}")

    def add_article_url(
        self,
        url: str,
        *,
        followed_account_id: str | None = None,
        source_channel: str = "manual",
        fallback_title: str = "",
        fallback_summary: str = "",
        fallback_published_at: str = "",
        fallback_cover_url: str = "",
        fallback_account_name: str = "",
        fallback_external_key: str = "",
        tolerate_metadata_error: bool = True,
    ) -> dict[str, Any]:
        normalized = normalize_article_url(url)
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("请输入有效的公开文章链接")
        metadata: dict[str, Any] = {}
        # Backend search already returns trusted article metadata for the exact
        # matched account. Reopening every public article here is both redundant
        # and slow, especially while expanding the cumulative "load more"
        # window. Manual links still require page-level metadata verification.
        trusted_metadata_sources = {
            "jizhile_api",
            "wechat_official",
            "wechat_backend_search",
        }
        if source_channel not in trusted_metadata_sources:
            try:
                metadata = fetch_public_article_metadata(normalized)
            except Exception:
                if not tolerate_metadata_error:
                    raise
        final_url = normalize_article_url(str(metadata.get("url") or normalized))
        account = self.db.get_followed_account(followed_account_id) if followed_account_id else None
        detected_name = str(
            (
                fallback_account_name or metadata.get("account_name")
                if source_channel in {"jizhile_api", "wechat_backend_search"}
                else metadata.get("account_name")
            )
            or ""
        ).strip()
        if not _is_wechat_article_url(final_url):
            raise ValueError("只能加入微信公众号已公开发布的 mp.weixin.qq.com 原文")
        if (
            source_channel != "wechat_official"
            and account
            and not detected_name
        ):
            raise ValueError("无法识别该文章的发布公众号，为防止收录转载或其他来源，已忽略")
        if (
            source_channel != "wechat_official"
            and account
            and not _account_name_matches(account, detected_name)
        ):
            raise ValueError(f"文章由其他公众号“{detected_name}”发布，不属于所选公众号，已忽略")
        if source_channel != "wechat_official" and not account and not detected_name:
            raise ValueError("无法识别该微信原文的发布公众号，已忽略")
        if not account and detected_name:
            account = self._match_account(detected_name)
            followed_account_id = str(account["id"]) if account else None
        account_name = str((account or {}).get("name") or detected_name or "外部投递")
        title = str(metadata.get("title") or fallback_title or "待识别文章").strip()
        published_at = str(metadata.get("published_at") or fallback_published_at or "") or None
        external_key = str(metadata.get("external_key") or fallback_external_key or "")
        existing = self.db.get_followed_article_by_identity(
            followed_account_id=followed_account_id,
            external_key=external_key,
            title=title,
            published_at=published_at,
        )
        stable_identity = (
            f"{followed_account_id}|{external_key}"
            if followed_account_id and external_key
            else final_url
        )
        article_id = str(
            (existing or {}).get("id")
            or hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:24]
        )
        self.db.upsert_followed_article(
            {
                "id": article_id,
                "followed_account_id": followed_account_id,
                "account_name": account_name,
                "title": title,
                "url": final_url,
                "published_at": published_at,
                "cover_url": str(metadata.get("cover_url") or fallback_cover_url or ""),
                "summary": str(metadata.get("summary") or fallback_summary or ""),
                "source_channel": source_channel,
                "external_key": external_key,
            }
        )
        return self.db.get_followed_article(article_id) or self.db.get_followed_article_by_url(final_url) or {}

    def _read_official_articles(
        self,
        account: dict[str, Any],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        official_id = str(account.get("official_account_id") or "").strip()
        record = self.db.get_official_account(official_id) if official_id else None
        if not record:
            raise ValueError("没有绑定可用的自有公众号配置")
        if not bool(record.get("enabled")):
            raise ValueError("绑定的自有公众号已停用")
        app_id = str(record.get("app_id") or "").strip()
        app_secret = decrypt_api_key(str(record.get("app_secret_encrypted") or ""))
        client = build_wechat_client(
            self.config,
            self.db,
            app_id,
            app_secret,
        )
        rows: list[dict[str, Any]] = []
        offset = 0
        requested = max(1, min(int(limit), 100))
        for _ in range(5):
            page_size = min(20, max(1, requested - len(rows)))
            payload = client.request(
                "POST",
                "/cgi-bin/freepublish/batchget",
                json_body={"offset": offset, "count": page_size, "no_content": 0},
            )
            groups = payload.get("item") or []
            if not isinstance(groups, list) or not groups:
                break
            for group in groups:
                content = group.get("content") or {}
                timestamp = int(
                    group.get("update_time") or content.get("update_time") or 0
                )
                published_at = (
                    datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                    if timestamp
                    else ""
                )
                for news in content.get("news_item") or []:
                    url = str(news.get("url") or news.get("content_url") or "").strip()
                    title = str(news.get("title") or "").strip()
                    if not title or not _is_wechat_article_url(url):
                        continue
                    rows.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": str(news.get("digest") or ""),
                            "cover_url": str(
                                news.get("thumb_url") or news.get("cover_url") or ""
                            ),
                            "published_at": published_at,
                        }
                    )
                    if len(rows) >= requested:
                        return rows
            received = len(groups)
            if received < page_size:
                break
            offset += received
        return rows

    def _merge_duplicate_accounts(self) -> None:
        groups: dict[str, list[dict[str, Any]]] = {}
        for account in self.db.list_followed_accounts():
            groups.setdefault(_name_key(str(account.get("name") or "")), []).append(account)
        for rows in groups.values():
            if len(rows) < 2:
                continue
            rows.sort(
                key=lambda item: (
                    0 if str(item.get("id") or "").startswith("follow-") else 1,
                    str(item.get("created_at") or ""),
                )
            )
            keep = dict(rows[0])
            duplicates = rows[1:]
            for duplicate in duplicates:
                for field in (
                    "wechat_id",
                    "category",
                    "sample_url",
                    "source_url",
                    "official_account_id",
                ):
                    if not keep.get(field) and duplicate.get(field):
                        keep[field] = duplicate[field]
                keep["tags"] = list(dict.fromkeys([*(keep.get("tags") or []), *(duplicate.get("tags") or [])]))
                keep["keywords"] = list(
                    dict.fromkeys([*(keep.get("keywords") or []), *(duplicate.get("keywords") or [])])
                )
                keep["enabled"] = bool(keep.get("enabled") or duplicate.get("enabled"))
            self.db.upsert_followed_account(keep)
            self.db.merge_followed_accounts(
                str(keep["id"]),
                [str(item["id"]) for item in duplicates],
            )

    def list_articles(
        self,
        *,
        account_ids: list[str] | None = None,
        days: int = 7,
        keyword: str = "",
        unread_only: bool = False,
        favorite_only: bool = False,
        unrewritten_only: bool = False,
        include_ignored: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 3650)))).isoformat()
        rows = self.db.list_followed_articles(
            account_ids=account_ids,
            since=since,
            keyword=keyword,
            unread_only=unread_only,
            favorite_only=favorite_only,
            unrewritten_only=unrewritten_only,
            include_ignored=include_ignored,
            limit=limit,
            offset=offset,
        )
        # Old versions could store news pages, RSS entries or search-engine
        # landing pages here. Never expose those in the followed-account view.
        return [row for row in rows if _is_wechat_article_url(str(row.get("url") or ""))]

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        """Return one collected public article without exposing DB internals."""

        clean = str(article_id or "").strip()
        if not clean:
            return None
        article = self.db.get_followed_article(clean)
        if not article or not _is_wechat_article_url(str(article.get("url") or "")):
            return None
        return article

    def update_article(
        self,
        article_id: str,
        *,
        is_read: bool | None = None,
        is_favorite: bool | None = None,
        is_ignored: bool | None = None,
        rewritten_batch_id: str | None = None,
    ) -> dict[str, Any]:
        self.db.update_followed_article(
            article_id,
            is_read=is_read,
            is_favorite=is_favorite,
            is_ignored=is_ignored,
            rewritten_batch_id=rewritten_batch_id,
        )
        article = self.db.get_followed_article(article_id)
        if not article:
            raise KeyError("关注文章不存在")
        return article

    def _match_account(self, name: str) -> dict[str, Any] | None:
        key = _name_key(name)
        return next(
            (
                item
                for item in self.db.list_followed_accounts()
                if _name_key(str(item.get("name") or "")) == key
                or _name_key(str(item.get("wechat_id") or "")) == key
            ),
            None,
        )


def group_articles(
    articles: list[dict[str, Any]], *, mode: str = "date"
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        if mode == "account":
            key = str(article.get("account_name") or "未识别公众号")
        else:
            key = str(article.get("published_at") or article.get("discovered_at") or "")[:10]
            key = key or "日期未知"
        groups.setdefault(key, []).append(article)
    return groups


def _read_rss(url: str, *, limit: int) -> list[dict[str, Any]]:
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "Mozilla/5.0 WeChatAutoPublisher/1.0"})
        response.raise_for_status()
    root = ET.fromstring(response.content)
    rows: list[dict[str, Any]] = []
    for node in root.findall(".//item")[:limit]:
        raw_date = str(node.findtext("pubDate") or "")
        published = _parse_rss_date(raw_date)
        rows.append(
            {
                "title": str(node.findtext("title") or "").strip(),
                "url": str(node.findtext("link") or "").strip(),
                "snippet": re.sub(r"<[^>]+>", " ", str(node.findtext("description") or "")),
                "published_at": published,
            }
        )
    return rows


def _parse_rss_date(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[,，、\n]", value)
    else:
        raw = list(value or [])
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _name_key(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _is_wechat_article_url(value: str) -> bool:
    parsed = urlsplit(value or "")
    return parsed.netloc.casefold().endswith("mp.weixin.qq.com") and parsed.path.startswith("/s")


def _account_name_matches(account: dict[str, Any], detected_name: str) -> bool:
    detected = _name_key(detected_name)
    candidates = {
        _name_key(str(account.get("name") or "")),
        _name_key(str(account.get("wechat_id") or "")),
    }
    candidates.discard("")
    return detected in candidates
