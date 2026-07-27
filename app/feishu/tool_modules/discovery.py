from __future__ import annotations

from typing import Any

from app.feishu.tool_modules.common import compact, optional_bool, optional_int, string_list


class DiscoveryToolMixin:
    """Followed-account article pool and topic-source tools."""

    def _tool_list_followed_accounts(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        service = self._require_followed_content()
        accounts = service.list_accounts(
            enabled_only=bool(args.get("enabled_only", False))
        )
        if not accounts:
            self.reply_text(message_id, "还没有配置关注公众号。")
            return
        numbered = [
            {"number": index, "id": str(item["id"]), "name": str(item.get("name") or "")}
            for index, item in enumerate(accounts, 1)
        ]
        self.sessions.update(chat_id, recent_followed_accounts=numbered)
        lines = [f"关注公众号（{len(accounts)} 个）："]
        for index, item in enumerate(accounts, 1):
            status = "启用" if item.get("enabled") else "停用"
            lines.append(
                f'\n{index}. {item.get("name")}｜{status}｜{item.get("fetch_method") or "manual"}\n'
                f'   ID：{item.get("id")}｜微信号：{item.get("wechat_id") or "未填写"}'
            )
            if item.get("last_error"):
                lines.append(f'   最近错误：{compact(item.get("last_error"), 160)}')
        lines.append("\n可回复：查看第 1 个公众号最近 30 天文章")
        self.reply_text(message_id, "\n".join(lines))

    def _tool_import_owned_followed_accounts(
        self, _args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        """Mirror managed official accounts into the followed-account list."""

        count = self._require_followed_content().import_owned_official_accounts()
        self.reply_text(
            message_id,
            f"已同步自有公众号到关注列表，本次新增 {count} 个。",
        )

    def _tool_save_followed_account(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._require_followed_content()
        account = {
            key: args[key]
            for key in (
                "id",
                "name",
                "wechat_id",
                "category",
                "fetch_method",
                "sample_url",
                "source_url",
                "official_account_id",
            )
            if key in args
        }
        account["tags"] = string_list(args.get("tags"))
        account["keywords"] = string_list(args.get("keywords"))
        is_owned = optional_bool(args.get("is_owned"))
        if is_owned is not None:
            account["is_owned"] = is_owned
        account["enabled"] = optional_bool(args.get("enabled"))
        if account["enabled"] is None:
            account["enabled"] = True
        refresh_hours = optional_int(args.get("refresh_hours"))
        if refresh_hours is not None:
            account["refresh_hours"] = max(1, min(refresh_hours, 720))
        saved = service.save_account(account)
        self.reply_text(
            message_id,
            f'关注公众号已保存：{saved.get("name")}（ID：{saved.get("id")}）',
        )

    def _tool_delete_followed_account(
        self, args: dict[str, Any], *, message_id: str, chat_id: str, **_: Any
    ) -> None:
        service = self._require_followed_content()
        account_id = self._followed_account_id(args, chat_id)
        service.delete_account(account_id)
        self.reply_text(message_id, f"关注公众号 {account_id} 已删除。")

    def _tool_refresh_followed_articles(
        self, args: dict[str, Any], *, message_id: str, chat_id: str, **_: Any
    ) -> None:
        service = self._require_followed_content()
        limit = max(1, min(optional_int(args.get("limit")) or 8, 50))
        account_id = str(args.get("account_id") or "").strip()
        account_name = str(args.get("account_name") or "").strip()
        if not account_id and account_name:
            matched = next(
                (
                    item
                    for item in service.list_accounts()
                    if str(item.get("name") or "") == account_name
                ),
                None,
            )
            account_id = str((matched or {}).get("id") or "")
            if not account_id:
                raise ValueError(f"没有找到关注公众号：{account_name}")
        number = optional_int(args.get("account_number"))
        if not account_id and number:
            account_id = self._numbered_session_id(
                chat_id, "recent_followed_accounts", number
            )
            if not account_id:
                raise ValueError("没有找到该公众号序号，请先查询关注公众号列表")
        self.reply_text(message_id, "正在从公众号来源获取近期公开文章……")
        if account_id:
            report = service.discover_account(account_id, limit=limit)
            lines = [
                f'公众号：{report.get("name")}',
                f'发现 {report.get("found", 0)} 篇，入池 {report.get("added", 0)} 篇。',
            ]
            if report.get("error"):
                lines.append(f'提示：{report.get("error")}')
        else:
            report = service.discover_all(limit_per_account=limit)
            lines = [f'全部关注公众号刷新完成，新入池 {report.get("added", 0)} 篇。']
            for item in report.get("accounts") or []:
                lines.append(
                    f'• {item.get("name")}：发现 {item.get("found", 0)}，入池 {item.get("added", 0)}'
                    + (f'，错误：{compact(item.get("error"), 120)}' if item.get("error") else "")
                )
        self.send_text(chat_id, "\n".join(lines))

    def _tool_list_followed_articles(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        service = self._require_followed_content()
        limit = max(1, min(optional_int(args.get("limit")) or 20, 100))
        offset = max(0, optional_int(args.get("offset")) or 0)
        account_ids = string_list(args.get("account_ids"))
        account_names = string_list(args.get("account_names"))
        account_name = str(args.get("account_name") or "").strip()
        account_number = optional_int(args.get("account_number"))
        if not account_ids and account_name:
            matched = next(
                (
                    item
                    for item in service.list_accounts()
                    if str(item.get("name") or "") == account_name
                ),
                None,
            )
            if not matched:
                raise ValueError(f"没有找到关注公众号：{account_name}")
            account_ids = [str(matched["id"])]
        if not account_ids and account_names:
            rows = service.list_accounts()
            by_name = {str(item.get("name") or ""): str(item["id"]) for item in rows}
            missing = [item for item in account_names if item not in by_name]
            if missing:
                raise ValueError("没有找到关注公众号：" + "、".join(missing))
            account_ids = [by_name[item] for item in account_names]
        if not account_ids and account_number:
            selected_id = self._numbered_session_id(
                chat_id, "recent_followed_accounts", account_number
            )
            if not selected_id:
                raise ValueError("没有找到该公众号序号，请先查询关注公众号列表")
            account_ids = [selected_id]
        filters = {
            "account_ids": account_ids or None,
            "days": max(1, min(optional_int(args.get("days")) or 7, 3650)),
            "keyword": str(args.get("keyword") or "").strip(),
            "unread_only": bool(args.get("unread_only", False)),
            "favorite_only": bool(args.get("favorite_only", False)),
            "unrewritten_only": bool(args.get("unrewritten_only", False)),
            "include_ignored": bool(args.get("include_ignored", False)),
            "limit": limit,
        }
        if offset:
            filters["offset"] = offset
        articles = service.list_articles(**filters)
        if not articles:
            self.reply_text(message_id, "没有找到符合条件的公众号公开文章。")
            return
        numbered = [
            {
                "number": index,
                "id": str(item["id"]),
                "url": str(item.get("url") or ""),
                "title": str(item.get("title") or ""),
            }
            for index, item in enumerate(articles, 1)
        ]
        self.sessions.update(chat_id, recent_followed_articles=numbered)
        lines = [f"关注公众号文章（{len(articles)} 篇）："]
        for index, item in enumerate(articles, 1):
            lines.append(
                f'\n{index}. {item.get("title") or "未命名文章"}\n'
                f'   {item.get("account_name") or "待识别"} · {str(item.get("published_at") or "")[:10]}\n'
                f'   {item.get("url")}'
            )
        lines.append("\n可回复：用第 2 篇给指定公众号改写")
        self.reply_text(message_id, "\n".join(lines))

    def _tool_update_followed_article(
        self, args: dict[str, Any], *, message_id: str, chat_id: str, **_: Any
    ) -> None:
        service = self._require_followed_content()
        article_id = str(args.get("article_id") or "").strip()
        number = optional_int(args.get("article_number"))
        if not article_id and number:
            article_id = self._numbered_session_id(
                chat_id, "recent_followed_articles", number
            )
        if not article_id:
            self.reply_text(message_id, "请指定文章 ID，或先查询文章后使用序号。")
            return
        changes: dict[str, Any] = {}
        for key in ("is_read", "is_favorite", "is_ignored"):
            if key in args:
                value = optional_bool(args.get(key))
                if value is not None:
                    changes[key] = value
        if "rewritten_batch_id" in args:
            changes["rewritten_batch_id"] = str(args.get("rewritten_batch_id") or "")
        if not changes:
            self.reply_text(message_id, "请说明要标记已读、收藏、忽略或关联改写批次。")
            return
        article = service.update_article(article_id, **changes)
        self.reply_text(
            message_id,
            f'文章状态已更新：{article.get("title") or article_id}',
        )

    def _tool_list_topic_sources(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._require_topic_sources()
        sources = service.list_sources(enabled_only=bool(args.get("enabled_only", False)))
        if not sources:
            self.reply_text(message_id, "没有配置选题来源。")
            return
        lines = [f"选题来源（{len(sources)} 个）："]
        for item in sources:
            lines.append(
                f'\n• {item.get("name")}｜{item.get("source_type")}｜'
                f'{"启用" if item.get("enabled") else "停用"}\n'
                f'  ID：{item.get("id")}'
            )
            if item.get("last_error"):
                lines.append(f'  最近错误：{compact(item.get("last_error"), 160)}')
        self.reply_text(message_id, "\n".join(lines))

    def _tool_list_topics(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        service = self._require_topic_sources()
        limit = max(1, min(optional_int(args.get("limit")) or 20, 100))
        items = service.list_topics(
            source_ids=string_list(args.get("source_ids")) or None,
            days=max(1, min(optional_int(args.get("days")) or 7, 365)),
            keyword=str(args.get("keyword") or "").strip(),
            favorite_only=bool(args.get("favorite_only", False)),
            unused_only=bool(args.get("unused_only", False)),
            limit=limit,
        )
        if not items:
            self.reply_text(message_id, "选题池中没有符合条件的内容。")
            return
        numbered = [
            {
                "number": index,
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
            }
            for index, item in enumerate(items, 1)
        ]
        self.sessions.update(chat_id, recent_topic_items=numbered)
        lines = [f"选题池（{len(items)} 条）："]
        for index, item in enumerate(items, 1):
            flags = []
            if item.get("favorite"):
                flags.append("已收藏")
            if item.get("used"):
                flags.append("已使用")
            lines.append(
                f'\n{index}. {item.get("title")}\n'
                f'   {item.get("source_name") or item.get("source") or "选题"} · '
                f'{str(item.get("published_at") or "")[:10]}'
                + (f' · {"/".join(flags)}' if flags else "")
            )
            if item.get("url"):
                lines.append(f'   {item.get("url")}')
        self.reply_text(message_id, "\n".join(lines))

    def _tool_update_topic_state(
        self, args: dict[str, Any], *, message_id: str, chat_id: str, **_: Any
    ) -> None:
        service = self._require_topic_sources()
        topic_id = str(args.get("topic_id") or args.get("item_id") or "").strip()
        number = optional_int(args.get("topic_number"))
        if not topic_id and number:
            topic_id = self._numbered_session_id(chat_id, "recent_topic_items", number)
        if not topic_id:
            raise ValueError("请指定选题 ID，或先查询选题池后使用序号")
        favorite = optional_bool(args.get("favorite"))
        used = optional_bool(args.get("used"))
        if favorite is None and used is None:
            raise ValueError("请说明收藏状态或是否已使用")
        item = service.update_topic_state(topic_id, favorite=favorite, used=used)
        self.reply_text(
            message_id,
            f'选题状态已更新：{item.get("title") or topic_id}｜'
            f'收藏={bool(item.get("favorite"))}｜已使用={bool(item.get("used"))}',
        )

    def _tool_save_topic_source(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._require_topic_sources()
        config = dict(args.get("config") or {})
        if args.get("url") is not None:
            config["url"] = str(args.get("url") or "").strip()
        if args.get("queries") is not None:
            config["queries"] = string_list(args.get("queries"))
        source = service.save_source(
            {
                "id": str(args.get("source_id") or args.get("id") or "").strip(),
                "name": str(args.get("name") or "").strip(),
                "source_type": str(args.get("source_type") or "").strip(),
                "config": config,
                "enabled": optional_bool(args.get("enabled")) is not False,
            }
        )
        self.reply_text(
            message_id,
            f'选题来源已保存：{source.get("name")}（ID：{source.get("id")}）',
        )

    def _tool_delete_topic_source(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._require_topic_sources()
        source_id = str(args.get("source_id") or args.get("id") or "").strip()
        if not source_id:
            raise ValueError("请指定选题来源 ID")
        service.delete_source(source_id)
        self.reply_text(message_id, f"选题来源 {source_id} 已删除。")

    def _tool_refresh_topic_sources(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._require_topic_sources()
        source_ids = string_list(args.get("source_ids")) or None
        report = service.refresh(source_ids)
        lines = [f'选题来源刷新完成，共获取 {report.get("total", 0)} 条。']
        for item in report.get("sources") or []:
            lines.append(
                f'• {item.get("name")}：{item.get("count", 0)} 条'
                + (f'，失败：{compact(item.get("error"), 120)}' if item.get("error") else "")
            )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_add_manual_topic(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._require_topic_sources()
        topic = service.add_manual_topic(
            str(args.get("title") or ""),
            url=str(args.get("url") or ""),
            summary=str(args.get("summary") or ""),
            category=str(args.get("category") or ""),
        )
        self.reply_text(
            message_id,
            f'手动选题已添加：{topic.get("title")}（ID：{topic.get("id")}）',
        )

    def _tool_load_more_followed_articles(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **kwargs: Any,
    ) -> None:
        service = self._require_followed_content()
        account_id = str(args.get("account_id") or "").strip()
        account_name = str(args.get("account_name") or "").strip()
        if not account_id and account_name:
            matched = next(
                (
                    item
                    for item in service.list_accounts()
                    if str(item.get("name") or "") == account_name
                ),
                None,
            )
            account_id = str((matched or {}).get("id") or "")
        if not account_id:
            raise ValueError("请指定要加载更多文章的关注公众号")
        context = self.sessions.get(chat_id)
        windows = dict(context.get("followed_article_fetch_limits") or {})
        increment = max(1, min(optional_int(args.get("increment")) or 8, 30))
        new_limit = min(100, int(windows.get(account_id) or 0) + increment)
        windows[account_id] = new_limit
        self.sessions.update(chat_id, followed_article_fetch_limits=windows)
        report = service.discover_account(account_id, limit=new_limit)
        self.reply_text(
            message_id,
            f'已扩大到最近 {new_limit} 条搜索窗口：发现 {report.get("found", 0)}，'
            f'新入池 {report.get("added", 0)}。',
        )
        self._tool_list_followed_articles(
            {
                "account_ids": [account_id],
                "days": optional_int(args.get("days")) or 3650,
                "limit": new_limit,
            },
            message_id=message_id,
            chat_id=chat_id,
            **kwargs,
        )

    def _tool_get_wechat_backend_status(
        self, _args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        settings = self._require_followed_content().get_backend_search_settings()
        self.reply_text(
            message_id,
            "微信公众号后台搜索登录态：\n"
            f'启用：{"是" if settings.get("enabled") else "否"}\n'
            f'Token：{"已配置" if settings.get("has_token") else "未配置"}\n'
            f'Cookie：{"已配置" if settings.get("has_cookie") else "未配置"}\n'
            f'会话标签：{settings.get("session_label") or "未填写"}',
        )

    def _tool_test_wechat_backend_login(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        result = self._require_followed_content().test_backend_search_settings(
            token=str(args.get("token") or ""),
            cookie=str(args.get("cookie") or ""),
        )
        safe = {
            key: value
            for key, value in result.items()
            if key not in {"token", "cookie"}
        }
        self.reply_text(message_id, "微信公众号后台登录态测试：" + compact(safe, 500))

    def _tool_save_wechat_backend_login(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        settings = self._require_followed_content().save_backend_search_settings(
            enabled=optional_bool(args.get("enabled")) is not False,
            token=str(args.get("token") or ""),
            cookie=str(args.get("cookie") or ""),
            session_label=str(args.get("session_label") or ""),
        )
        self.reply_text(
            message_id,
            "微信公众号后台登录态已加密保存："
            f'Token={"已配置" if settings.get("has_token") else "未配置"}，'
            f'Cookie={"已配置" if settings.get("has_cookie") else "未配置"}。',
        )

    def _tool_clear_wechat_backend_login(
        self, _args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        self._require_followed_content().clear_backend_search_settings()
        self.reply_text(message_id, "微信公众号后台搜索登录态已清除。")

    def _require_followed_content(self) -> Any:
        if self.followed_content is None:
            raise ValueError("当前运行环境尚未启用关注公众号服务")
        return self.followed_content

    def _require_topic_sources(self) -> Any:
        if self.topic_sources is None:
            raise ValueError("当前运行环境尚未启用选题来源服务")
        return self.topic_sources

    def _numbered_session_id(self, chat_id: str, key: str, number: int) -> str:
        rows = list(self.sessions.get(chat_id).get(key) or [])
        selected = next(
            (item for item in rows if optional_int(item.get("number")) == number),
            None,
        )
        return str((selected or {}).get("id") or "")

    def _followed_account_id(self, args: dict[str, Any], chat_id: str) -> str:
        account_id = str(args.get("account_id") or args.get("id") or "").strip()
        number = optional_int(args.get("account_number"))
        if not account_id and number:
            account_id = self._numbered_session_id(
                chat_id, "recent_followed_accounts", number
            )
        if not account_id:
            raise ValueError("请指定关注公众号 ID")
        return account_id
