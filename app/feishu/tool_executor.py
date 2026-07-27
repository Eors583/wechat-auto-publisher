from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from app.feishu.agent import AgentPlan
from app.feishu.constants import HELP_TEXT, URL_PATTERN
from app.feishu.presenter import (
    format_accounts,
    format_article_preview,
    format_hot_topics,
    format_status,
)
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_catalog import ALLOWED_TOOLS
from app.feishu.tool_modules import (
    AdminToolMixin,
    DiscoveryToolMixin,
    EditorialReviewToolMixin,
    ReviewToolMixin,
    SystemToolMixin,
)
from app.feishu.tool_modules.common import explicit_confirmation, string_list
from app.providers.topics_catalog import fetch_hot_topics
from app.services import (
    AnalyticsService,
    BatchService,
    ConfigurationService,
    CreationPlanService,
    FollowedContentService,
    TopicSourceService,
)


CONFIRMATION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "retry_failed_batch": ("确认重试失败公众号", "确认重试失败任务", "确认重试批次"),
    "copy_batch": ("确认复制批次重新生成", "确认复制批次"),
    "archive_batch": ("确认归档当前批次", "确认归档批次", "确认取消归档批次"),
    "update_article_content": ("确认保存文章修改",),
    "move_paragraph": ("确认移动这个段落", "确认移动段落"),
    "delete_paragraph": ("确认删除这个段落", "确认删除段落"),
    "regenerate_paragraph": ("确认重新生成这个段落", "确认重新生成段落"),
    "rerender_article": ("确认重新排版",),
    "restore_article_version": ("确认恢复文章历史版本", "确认恢复文章版本"),
    "regenerate_inline_images": ("确认重新生成正文配图", "确认重生成正文配图"),
    "regenerate_inline_image": (
        "确认按要求重新生成这张正文配图",
        "确认重新生成这张正文配图",
        "确认重做这张配图",
    ),
    "regenerate_cover": ("确认重新生成文章封面", "确认重新生成封面", "确认重生成封面"),
    "select_cover": ("确认更换文章封面", "确认选择文章封面"),
    "remove_inline_image": (
        "确认删除这张正文图片",
        "确认删除正文配图",
        "确认删除配图",
        "确认移除正文配图",
        "确认移除第",
    ),
    "configure_account_images": ("确认修改公众号生图配置",),
    "update_account_layout": ("确认修改公众号排版",),
    "select_draft_template": ("确认更换公众号草稿模板",),
    "delete_followed_account": ("确认删除关注公众号",),
    "delete_topic_source": ("确认删除选题来源", "确认删除热点来源"),
    "delete_prompt_template": ("确认删除提示词模板",),
    "bind_account_prompt_template": ("确认更换公众号提示词模板",),
    "apply_account_creation_plan": (
        "确认给公众号应用创作方案",
        "确认更换公众号创作方案",
    ),
    "save_model": ("确认保存模型密钥配置", "确认保存模型密钥"),
    "set_account_model": ("确认更换公众号模型",),
    "save_official_account": (
        "确认保存公众号密钥配置",
        "确认保存公众号密钥",
    ),
    "delete_official_account": ("确认删除自有公众号", "确认删除公众号"),
    "save_wechat_backend_login": ("确认保存微信公众号后台登录态",),
    "clear_wechat_backend_login": ("确认清除微信公众号后台登录态",),
    "delete_model": ("确认删除模型",),
    "generate_model_test_image": ("确认生成模型测试图", "确认生成测试图"),
    "save_editorial_review_profile": ("确认保存 AI 评审方案", "确认保存AI评审方案"),
    "delete_editorial_review_profile": ("确认删除 AI 评审方案", "确认删除AI评审方案"),
    "set_account_editorial_review_default": (
        "确认更换公众号默认 AI 评审方案",
        "确认更换公众号默认AI评审方案",
    ),
    "run_editorial_review": ("确认开始 AI 评审", "确认开始AI评审"),
    "generate_editorial_rewrite_candidate": (
        "确认按 AI 评审建议生成修改稿",
        "确认按AI评审建议生成修改稿",
    ),
    "smart_rewrite_from_editorial_review": (
        "确认智能修改原文",
        "确认按 AI 评审建议智能修改原文",
        "确认按AI评审建议智能修改原文",
    ),
    "apply_editorial_review_application": (
        "确认应用 AI 修改稿",
        "确认应用AI修改稿",
    ),
    "resolve_editorial_review_issue": (
        "确认更新 AI 评审核实结果",
        "确认更新AI评审核实结果",
    ),
}

ADMIN_TOOLS = {
    "import_owned_followed_accounts",
    "save_followed_account",
    "delete_followed_account",
    "save_topic_source",
    "delete_topic_source",
    "save_prompt_template",
    "delete_prompt_template",
    "bind_account_prompt_template",
    "apply_account_creation_plan",
    "save_model",
    "generate_model_test_image",
    "set_account_model",
    "save_official_account",
    "configure_account_images",
    "update_account_layout",
    "select_draft_template",
    "set_official_account_enabled",
    "delete_official_account",
    "set_model_enabled",
    "delete_model",
    "test_wechat_backend_login",
    "save_wechat_backend_login",
    "clear_wechat_backend_login",
    "save_editorial_review_profile",
    "delete_editorial_review_profile",
    "set_account_editorial_review_default",
}

SECRET_CONFIGURATION_TOOLS = {
    "save_model",
    "save_official_account",
    "save_wechat_backend_login",
}

BATCH_SCOPED_TOOLS = {
    "get_batch_status",
    "get_article_result",
    "select_article_title",
    "write_all_to_drafts",
    "cancel_rewrite_batch",
    "retry_failed_batch",
    "copy_batch",
    "archive_batch",
    "request_article_changes",
    "confirm_article",
    "update_article_content",
    "move_paragraph",
    "delete_paragraph",
    "regenerate_paragraph",
    "rerender_article",
    "list_article_versions",
    "restore_article_version",
    "get_article_assets",
    "regenerate_inline_images",
    "regenerate_inline_image",
    "remove_inline_image",
    "regenerate_cover",
    "list_cover_options",
    "select_cover",
    "run_editorial_review",
    "get_editorial_review",
    "generate_editorial_rewrite_candidate",
    "smart_rewrite_from_editorial_review",
    "apply_editorial_review_application",
    "resolve_editorial_review_issue",
}


class FeishuToolExecutor(
    EditorialReviewToolMixin,
    ReviewToolMixin,
    DiscoveryToolMixin,
    AdminToolMixin,
    SystemToolMixin,
):
    """Validate and execute the agent's whitelisted application tools."""

    def __init__(
        self,
        *,
        service: BatchService,
        config: dict[str, Any],
        sessions: FeishuSessionStore,
        default_account_ids: list[str],
        reply_text: Callable[[str, str], None],
        send_text: Callable[[str, str], None],
        send_image: Callable[..., str] | None = None,
        admin_open_ids: set[str] | None = None,
    ) -> None:
        self.service = service
        self.config = config
        self.sessions = sessions
        self.default_account_ids = default_account_ids
        self.reply_text = reply_text
        self.send_text = send_text
        self.send_image = send_image
        self.admin_open_ids = admin_open_ids
        db = getattr(service, "db", None)
        self.configuration = ConfigurationService(db, config) if db is not None else None
        self.creation_plans = (
            CreationPlanService(db, config)
            if db is not None
            and hasattr(db, "path")
            and hasattr(db, "list_creation_plans")
            else None
        )
        self.analytics = AnalyticsService(db) if db is not None else None
        self.followed_content = (
            FollowedContentService(db, config)
            if db is not None and hasattr(db, "list_followed_accounts")
            else None
        )
        self.topic_sources = (
            TopicSourceService(db, config)
            if db is not None and hasattr(db, "list_topic_sources")
            else None
        )

    def execute(
        self,
        plan: AgentPlan,
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
        confirmation_verified: bool = False,
    ) -> None:
        if plan.tool not in ALLOWED_TOOLS:
            raise ValueError(f"未授权的智能体工具：{plan.tool}")
        handler = getattr(self, f"_tool_{plan.tool}", None)
        if not handler:
            raise ValueError(f"未实现的智能体工具：{plan.tool}")
        if (
            plan.tool in ADMIN_TOOLS
            and self.admin_open_ids is not None
            and open_id not in self.admin_open_ids
        ):
            self.reply_text(
                message_id,
                "该操作需要管理员权限。请在飞书接入设置中把你的 Open ID 加入允许用户列表。",
            )
            return
        confirmations = CONFIRMATION_REQUIREMENTS.get(plan.tool)
        if confirmations and not explicit_confirmation(
            original_text,
            *confirmations,
            # This flag is supplied only by FeishuBot after it validates the
            # one-time confirmation code.  Never trust a planner-generated
            # argument such as ``confirmed=true`` for this boundary.
            argument_confirmed=confirmation_verified,
        ):
            if plan.tool in SECRET_CONFIGURATION_TOOLS:
                self.reply_text(
                    message_id,
                    "该操作包含密钥，不会缓存原参数。请在同一条消息中重新发送完整配置，"
                    f"并明确写“{confirmations[0]}”。",
                )
            else:
                pending = self.sessions.set_pending_action(
                    chat_id,
                    tool=plan.tool,
                    arguments=plan.arguments,
                    prompt=confirmations[0],
                )
                self.reply_text(
                    message_id,
                    "该操作会修改配置、内容或产生接口费用。"
                    f'请在 5 分钟内回复“确认 {pending["code"]}”。',
                )
            return
        if confirmations:
            self.sessions.clear_pending_action(chat_id)
        requested_batch_id = str(
            plan.arguments.get("batch_id") or current_batch_id or ""
        ).strip()
        if (
            plan.tool in BATCH_SCOPED_TOOLS
            and requested_batch_id
            and self.admin_open_ids is not None
            and open_id not in self.admin_open_ids
            and hasattr(self.service, "get_batch")
        ):
            batch = self.service.get_batch(requested_batch_id)
            if not (
                str(batch.get("requested_by") or "") == open_id
                or str(batch.get("chat_id") or "") == chat_id
            ):
                self.reply_text(message_id, "你没有权限操作其他用户或群聊创建的批次。")
                return
        handler(
            plan.arguments,
            original_text=original_text,
            message_id=message_id,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
            plan=plan,
        )

    def _tool_chat(self, _args: dict[str, Any], *, plan: AgentPlan, message_id: str, **_: Any) -> None:
        self.reply_text(message_id, plan.reply or HELP_TEXT)

    def _tool_list_accounts(self, _args: dict[str, Any], *, message_id: str, **_: Any) -> None:
        self.reply_text(message_id, format_accounts(self.service.list_accounts()))

    def _tool_get_recent_hot_topics(
        self, args: dict[str, Any], *, message_id: str, chat_id: str, **_: Any
    ) -> None:
        limit = max(1, min(optional_int(args.get("limit")) or 10, 20))
        keyword = str(args.get("keyword") or "").strip()
        days = max(1, min(optional_int(args.get("days")) or 7, 365))
        source_ids = string_list(args.get("source_ids")) or None
        if self.topic_sources is not None:
            if keyword:
                result = self.topic_sources.search(
                    keyword,
                    source_ids=source_ids,
                    days=days,
                    limit=limit,
                )
                items = list(result.get("items") or [])
            else:
                self.topic_sources.refresh(source_ids)
                items = self.topic_sources.list_topics(
                    source_ids=source_ids,
                    days=days,
                    limit=limit,
                )
        else:
            items = fetch_hot_topics(self.config)[:limit]
        if not items:
            self.reply_text(message_id, "当前没有获取到近 7 天热点，请稍后再试。")
            return
        self.sessions.save_hot_topics(chat_id, items)
        prefix = f'关键词“{keyword}”的多来源搜索结果：\n\n' if keyword else ""
        rendered = format_hot_topics(items)
        if days != 7:
            rendered = rendered.replace("近 7 天热点", f"近 {days} 天热点", 1)
        self.reply_text(message_id, prefix + rendered)

    def _tool_collect_article_link(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        **_: Any,
    ) -> None:
        source_url = str(args.get("source_url") or "").strip()
        if not source_url:
            match = URL_PATTERN.search(original_text)
            source_url = match.group(0).rstrip("。；，,;") if match else ""
        if not source_url:
            self.reply_text(message_id, "请发送需要加入关注文章池的公开文章链接。")
            return
        if self.followed_content is None:
            self.reply_text(message_id, "当前运行环境尚未启用关注文章池。")
            return
        article = self.followed_content.add_article_url(
            source_url,
            followed_account_id=str(args.get("followed_account_id") or "").strip() or None,
            source_channel="feishu",
        )
        self.reply_text(
            message_id,
            "已加入关注文章池：\n"
            f'公众号：{article.get("account_name") or "待识别"}\n'
            f'标题：{article.get("title") or "待识别"}',
        )

    def _tool_create_rewrite_batch(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        open_id: str,
        **_: Any,
    ) -> None:
        source_url = str(args.get("source_url") or "").strip()
        if not source_url:
            match = URL_PATTERN.search(original_text)
            source_url = match.group(0).rstrip("。；，,;") if match else ""
        raw_content = str(args.get("raw_content") or "").strip()
        topic = str(args.get("topic") or "").strip()
        source_mode = str(args.get("source_mode") or "").strip() or None
        reference_urls = string_list(args.get("reference_urls"))
        required_facts = str(args.get("required_facts") or "").strip()
        rewrite_intensity = str(args.get("rewrite_intensity") or "").strip()
        followed_number = optional_int(args.get("followed_article_number"))
        followed_article_id = str(args.get("followed_article_id") or "").strip()
        hot_number = optional_int(args.get("hot_topic_number"))
        if not hot_number:
            match = re.search(r"第\s*(\d+)\s*条", original_text)
            hot_number = int(match.group(1)) if match else None
        if not source_url and not raw_content and hot_number:
            selected = self.sessions.hot_topic(chat_id, hot_number)
            if not selected:
                self.reply_text(message_id, "没有找到该热点序号，请先说“查询近7日热点”刷新列表。")
                return
            source_url = str(selected.get("url") or "").strip()
            if not source_url:
                raw_content = f'热点选题：{selected.get("title") or ""}'
        if not source_url and not raw_content and followed_number:
            selected = next(
                (
                    item
                    for item in self.sessions.get(chat_id).get("recent_followed_articles") or []
                    if optional_int(item.get("number")) == followed_number
                ),
                None,
            )
            source_url = str((selected or {}).get("url") or "").strip()
            followed_article_id = str((selected or {}).get("id") or "").strip()
            if not source_url:
                self.reply_text(message_id, "没有找到该关注文章序号，请先查询公众号近期文章。")
                return
        if (
            not source_url
            and not raw_content
            and followed_article_id
            and self.followed_content is not None
        ):
            selected = self.followed_content.get_article(followed_article_id)
            source_url = str((selected or {}).get("url") or "").strip()
            if not source_url:
                self.reply_text(message_id, "没有找到该关注文章 ID。")
                return
        if not source_url and not raw_content and not topic and not reference_urls:
            self.reply_text(message_id, "请提供文章链接、正文、多篇参考链接或原创话题。")
            return
        batch = self.service.create_batch(
            source_url=source_url or None,
            raw_content=raw_content or None,
            topic=topic or None,
            source_mode=source_mode,
            reference_urls=reference_urls or None,
            required_facts=required_facts or None,
            rewrite_intensity=rewrite_intensity or None,
            account_ids=self.resolve_accounts(args),
            requested_by=open_id,
            chat_id=chat_id,
        )
        self.sessions.bind_batch(chat_id, str(batch["id"]))
        if followed_article_id and self.followed_content is not None:
            try:
                self.followed_content.update_article(
                    followed_article_id,
                    rewritten_batch_id=str(batch["id"]),
                )
            except Exception:
                pass
        if source_url and self.followed_content is not None:
            threading.Thread(
                target=self._collect_silently,
                args=(source_url,),
                daemon=True,
                name=f"feishu-collect-{batch['id']}",
            ).start()
        names = "、".join(job["account_name"] for job in batch["jobs"])
        self.reply_text(
            message_id,
            f'已调用 create_rewrite_batch。批次 {batch["id"]} 正在并发生成：{names}\n'
            + ("该链接也会同步进入关注文章池。\n" if source_url else "")
            + '下一步可回复：“现在进度怎么样？”或“终止当前改写”。',
        )

    def _collect_silently(self, source_url: str) -> None:
        if self.followed_content is None:
            return
        try:
            self.followed_content.add_article_url(
                source_url,
                source_channel="feishu",
            )
        except Exception:
            # Collection is secondary and must never interrupt article generation.
            return

    def _tool_get_batch_status(
        self, args: dict[str, Any], *, message_id: str, current_batch_id: str | None, **_: Any
    ) -> None:
        batch_id = str(args.get("batch_id") or current_batch_id or "")
        if not batch_id:
            self.reply_text(message_id, "当前会话没有改写批次。")
            return
        self.reply_text(message_id, format_status(self.service.get_batch(batch_id)))

    def _tool_get_article_result(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id = str(args.get("batch_id") or current_batch_id or "")
        if not batch_id:
            self.reply_text(message_id, "当前会话没有生成结果。")
            return
        batch = self.service.get_batch(batch_id, include_content=True)
        job_id = optional_int(args.get("job_id"))
        if not job_id:
            job_id = self.sessions.current_review_job_id(chat_id)
        if not job_id:
            first = self.sessions.start_review(chat_id, batch)
            job_id = optional_int(first.get("job_id")) if first else None
        account_name = str(args.get("account_name") or "").strip()
        jobs = batch["jobs"]
        job = next((item for item in jobs if item["id"] == job_id), None) if job_id else None
        if not job and account_name:
            job = next((item for item in jobs if str(item.get("account_name")) == account_name), None)
        if not job and len(jobs) == 1:
            job = jobs[0]
        if not job:
            options = "、".join(f'#{item["id"]} {item["account_name"]}' for item in jobs)
            self.reply_text(message_id, f"请指定要查看的任务：{options}")
            return
        # Reading an article in Feishu is the same review action as opening the
        # desktop workbench; keep the shared audit state consistent.
        if (
            str(job.get("status") or "") == "ready_for_review"
            and hasattr(self.service, "mark_job_viewed")
        ):
            job = self.service.mark_job_viewed(batch_id, int(job["id"]))
        self.sessions.set_current_review_job(chat_id, int(job["id"]))
        self.reply_text(message_id, format_article_preview(job))

    def _tool_select_article_title(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id = str(args.get("batch_id") or current_batch_id or "")
        job_id = optional_int(args.get("job_id")) or self.sessions.current_review_job_id(chat_id)
        title_number = optional_int(args.get("title_number"))
        subtitle_number = optional_int(args.get("subtitle_number"))
        if batch_id and not job_id:
            batch = self.service.get_batch(batch_id, include_content=True)
            first = self.sessions.start_review(chat_id, batch)
            job_id = optional_int(first.get("job_id")) if first else None
        if not batch_id or not job_id or not title_number:
            self.reply_text(message_id, "请选择具体任务号和标题序号。")
            return
        job = self.service.select_job(
            batch_id,
            job_id,
            title_index=title_number - 1,
            subtitle_index=subtitle_number - 1 if subtitle_number else None,
        )
        self.sessions.set_current_review_job(chat_id, job_id)
        review = self.sessions.review_state(chat_id)
        self.reply_text(
            message_id,
            f'任务 #{job_id} 已选择：\n标题：{job["selected_title"]}\n'
            f'副标题：{job.get("selected_subtitle") or "不使用副标题"}\n\n'
            f'审核进度：{review["completed"]}/{review["total"]}。'
            "当前文章尚未确认，请查看下面的正文预览；确认无误后回复“确认此文章”。",
        )
        self.send_text(chat_id, format_article_preview(job))

    def _tool_write_all_to_drafts(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id = str(args.get("batch_id") or current_batch_id or "")
        if not batch_id:
            self.reply_text(message_id, "当前会话没有待写入批次。")
            return
        if not explicit_draft_confirmation(original_text):
            self.reply_text(message_id, "写入草稿箱会操作所有已选公众号。请明确回复“确认全部写入草稿箱”。")
            return
        # A batch may have been edited from the desktop or API after this
        # Feishu conversation last reviewed it.  Reconcile against the shared
        # database before deciding whether every article is confirmed.
        batch = self.service.get_batch(batch_id, include_content=False)
        self.sessions.sync_review(chat_id, batch)
        if not self.sessions.all_reviews_completed(chat_id):
            remaining = self.sessions.unreviewed_items(chat_id)
            names = "、".join(str(item.get("account_name") or "") for item in remaining)
            self.reply_text(
                message_id,
                f"还有 {len(remaining)} 个公众号文章未完成审核：{names}。"
                "请先逐篇预览并选择标题。",
            )
            return
        self.reply_text(message_id, f"批次 {batch_id} 开始并发写入各公众号草稿箱。")
        self.sessions.update(chat_id, stage="injecting")
        threading.Thread(
            target=self._inject_and_report_error,
            args=(batch_id, chat_id),
            daemon=True,
            name=f"feishu-inject-{batch_id}",
        ).start()

    def _tool_cancel_rewrite_batch(
        self, args: dict[str, Any], *, message_id: str, chat_id: str, current_batch_id: str | None, **_: Any
    ) -> None:
        batch_id = str(args.get("batch_id") or current_batch_id or "")
        if not batch_id:
            self.reply_text(message_id, "当前会话没有可终止批次。")
            return
        batch = self.service.cancel_batch(batch_id)
        self.sessions.update(chat_id, stage="cancelled")
        self.reply_text(message_id, f'批次 {batch["id"]} 已终止。')

    def resolve_accounts(self, arguments: dict[str, Any]) -> list[str]:
        accounts = self.service.list_accounts()
        by_id = {str(item["id"]): str(item["id"]) for item in accounts}
        by_name = {str(item["name"]): str(item["id"]) for item in accounts}
        references = [
            *string_list(arguments.get("account_ids")),
            *string_list(arguments.get("account_names")),
            *string_list(arguments.get("account_id")),
            *string_list(arguments.get("account_name")),
        ]
        if not references:
            return self.default_account_ids or list(by_id)
        resolved: list[str] = []
        unknown: list[str] = []
        for reference in references:
            value = str(reference).strip()
            account_id = by_id.get(value) or by_name.get(value)
            if account_id and account_id not in resolved:
                resolved.append(account_id)
            elif not account_id:
                unknown.append(value)
        if unknown:
            raise ValueError("无法匹配公众号：" + "、".join(unknown))
        return resolved

    def _inject_and_report_error(self, batch_id: str, chat_id: str) -> None:
        try:
            self.service.inject_batch(batch_id)
        except Exception as exc:  # noqa: BLE001
            self.send_text(chat_id, f"批次 {batch_id} 写入失败：{exc}")


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def explicit_draft_confirmation(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    return bool(
        re.search(r"确认(?:全部)?写入(?:公众号)?草稿箱", normalized)
        or re.search(r"全部写入(?:公众号)?草稿箱", normalized)
    )
