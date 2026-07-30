from __future__ import annotations

from pathlib import Path
from typing import Any

from app.feishu.presenter import format_article_preview
from app.feishu.media import download_wechat_image
from app.feishu.tool_modules.common import (
    batch_id_from,
    compact,
    optional_bool,
    optional_int,
    require_job_id,
)
from app.services.failures import sanitize_failure_text


class ReviewToolMixin:
    """Batch review, editing, image and lifecycle tools."""

    def _tool_preflight_accounts(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account_ids = self.resolve_accounts(args)
        reports = self.service.preflight(
            account_ids,
            deep_model_check=bool(args.get("deep_model_check", False)),
        )
        lines = ["发布环境检查结果："]
        for report in reports:
            ready = "可生成并写入" if report.get("can_write") else (
                "仅可生成" if report.get("can_generate") else "不可生成"
            )
            lines.append(f'\n【{report.get("account_name")}】{ready}')
            for check in report.get("checks") or []:
                icon = "✅" if check.get("ok") else "❌"
                lines.append(
                    f'{icon} {check.get("name")}：{compact(check.get("message"), 180)}'
                )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_list_batches(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        **_: Any,
    ) -> None:
        limit = max(1, min(optional_int(args.get("limit")) or 20, 100))
        is_admin = self.admin_open_ids is None or open_id in self.admin_open_ids
        batches = self.service.list_batches(
            # Non-admin users are filtered by ownership below. Fetch a wider
            # window first so another user's recent batches cannot hide theirs.
            limit=limit if is_admin else 500,
            include_archived=bool(args.get("include_archived", False)),
        )
        if not is_admin:
            batches = [
                item
                for item in batches
                if str(item.get("requested_by") or "") == open_id
                or str(item.get("chat_id") or "") == chat_id
            ]
        batches = batches[:limit]
        status_filter = str(args.get("status") or "").strip()
        keyword = str(args.get("keyword") or "").strip().lower()
        if status_filter:
            batches = [item for item in batches if str(item.get("status")) == status_filter]
        if keyword:
            batches = [
                item
                for item in batches
                if keyword
                in " ".join(
                    [
                        str(item.get("topic") or ""),
                        str(item.get("source_url") or ""),
                        " ".join(
                            str(job.get("account_name") or "")
                            for job in item.get("jobs") or []
                        ),
                    ]
                ).lower()
            ]
        if not batches:
            self.reply_text(message_id, "没有找到符合条件的批次。")
            return
        self.sessions.update(
            chat_id,
            recent_batch_ids=[str(item["id"]) for item in batches[:20]],
        )
        lines = [f"最近批次（{len(batches[:20])} 条）："]
        for item in batches[:20]:
            progress = item.get("progress") or {}
            account_names = "、".join(
                str(job.get("account_name") or "") for job in item.get("jobs") or []
            )
            lines.append(
                f'\n• {item.get("display_id") or item.get("id")}｜{item.get("status")}｜'
                f'{progress.get("completed", 0)}/{progress.get("total", 0)}\n'
                f'  {compact(item.get("topic") or item.get("source_url") or "未命名批次", 90)}\n'
                f'  {account_names}'
            )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_list_review_inbox(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        **_: Any,
    ) -> None:
        bucket = str(args.get("bucket") or "review").strip().lower()
        if bucket not in {
            "review",
            "write_failed",
            "generation_failed",
            "today_completed",
        }:
            raise ValueError(
                "bucket 只支持 review、write_failed、generation_failed、today_completed"
            )
        limit = max(1, min(optional_int(args.get("limit")) or 20, 100))
        is_admin = self.admin_open_ids is None or open_id in self.admin_open_ids
        filters: dict[str, Any] = {
            "bucket": bucket,
            "account_id": str(args.get("account_id") or "").strip() or None,
            "limit": limit,
            "cursor": str(args.get("cursor") or "").strip() or None,
        }
        if not is_admin:
            filters.update(requested_by=open_id, chat_id=chat_id)
        result = self.service.list_review_inbox(**filters)
        items = list(result.get("items") or [])
        labels = {
            "review": "待我审核",
            "write_failed": "写入失败",
            "generation_failed": "生成失败",
            "today_completed": "今日完成",
        }
        counts = dict(result.get("counts") or {})
        count_text = "｜".join(
            (
                f"待审核 {int(counts.get('review') or 0)}",
                f"写入失败 {int(counts.get('write_failed') or 0)}",
                f"生成失败 {int(counts.get('generation_failed') or 0)}",
                f"今日完成 {int(counts.get('today_completed') or 0)}",
            )
        )
        lines = [f"{labels[bucket]}收件箱（{len(items)} 篇）", count_text]
        for index, item in enumerate(items, 1):
            failure = dict(item.get("failure") or {})
            blockers = [
                str(value).strip()
                for value in list(item.get("blockers") or [])
                if str(value).strip()
            ]
            lines.append(
                f"\n{index}. {item.get('account_name') or '公众号'}｜"
                f"{item.get('title') or item.get('batch_topic') or '尚未选择标题'}\n"
                f"   批次 {item.get('batch_display_id') or item.get('batch_id')}｜"
                f"任务 #{item.get('job_id')}｜{item.get('body_chars') or 0} 字｜"
                f"{item.get('priority_reason') or '普通待办'}"
            )
            if failure:
                lines.append(
                    f"   {failure.get('title') or '处理失败'}："
                    f"{failure.get('reason') or ''}\n"
                    f"   建议：{failure.get('recommendation') or '按提示重试'}"
                )
            elif blockers:
                lines.append(f"   阻断项：{'；'.join(blockers)}")
            lines.append(
                "   AI 评审："
                f"{item.get('latest_review_summary') or '尚未评审'}"
            )
            if item.get("recommended_action"):
                lines.append(f"   推荐下一步：{item['recommended_action']}")
            review_url = str(item.get("review_url") or "").strip()
            if review_url:
                lines.append(f"   审核链接：{review_url}")
        if result.get("next_cursor"):
            lines.append(f"\n还有更多结果，cursor={result['next_cursor']}")
        if not items:
            lines.append("\n当前没有符合条件的文章。")
        self.reply_text(message_id, "\n".join(lines))

    def _tool_get_article_attempts(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        attempts = self.service.list_job_attempts(batch_id, job_id)
        if not attempts:
            self.reply_text(message_id, f"任务 #{job_id} 暂无分步骤执行记录。")
            return
        lines = [f"任务 #{job_id} 执行记录："]
        for item in attempts[:30]:
            lines.append(
                f"\n• {item.get('stage') or '-'}｜第 {item.get('attempt_no') or 1} 次｜"
                f"{item.get('status') or '-'}"
            )
            if item.get("error_code"):
                lines.append(f"  错误代码：{item['error_code']}")
            if item.get("started_at"):
                lines.append(
                    f"  {item.get('started_at')} → {item.get('completed_at') or '进行中'}"
                )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_retry_article_step(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        step = str(args.get("step") or "auto").strip().lower()
        self.reply_text(
            message_id,
            f"正在从 {step} 步骤恢复任务 #{job_id}，已完成的上游内容会保留……",
        )
        result = self.service.retry_job(
            batch_id,
            job_id,
            step=step,
            model_id=str(args.get("model_id") or "").strip() or None,
            source_url=str(args.get("source_url") or "").strip() or None,
            raw_content=(
                str(args.get("raw_content"))
                if args.get("raw_content") is not None
                else None
            ),
        )
        job = dict(result.get("job") or result)
        current_status = str(job.get("status") or result.get("status") or "处理中")
        if current_status == "ready_for_review":
            self.sessions.reopen_review(
                chat_id,
                job_id,
                account_name=str(job.get("account_name") or ""),
            )
        self.reply_text(
            message_id,
            (
                f"任务 #{job_id} 已恢复到待审核状态。"
                if current_status == "ready_for_review"
                else (
                    f"任务 #{job_id} 的恢复请求已提交，当前状态：{current_status}。"
                    "完成后可查询执行记录或待审核收件箱。"
                )
            ),
        )

    def _tool_retry_failed_batch(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id = self._required_batch(args, current_batch_id)
        batch = self.service.get_batch(batch_id, include_content=False)
        failed_jobs = [
            dict(job)
            for job in list(batch.get("jobs") or [])
            if str(job.get("status") or "") == "failed"
        ]
        if not failed_jobs:
            self.reply_text(message_id, "当前批次没有可重试的失败公众号文章。")
            return
        accepted: list[str] = []
        rejected: list[str] = []
        for job in failed_jobs:
            job_id = int(job["id"])
            account_name = str(job.get("account_name") or f"任务 #{job_id}")
            try:
                self.service.retry_job(batch_id, job_id, step="auto")
                accepted.append(account_name)
            except Exception as exc:  # noqa: BLE001
                rejected.append(
                    f"{account_name}：{sanitize_failure_text(exc, limit=240)}"
                )
        self.sessions.bind_batch(chat_id, batch_id)
        lines = [
            f"已在原批次 {batch_id} 中提交 {len(accepted)}/{len(failed_jobs)} 篇失败文章恢复。"
        ]
        if accepted:
            lines.append("已提交：" + "、".join(accepted))
        if rejected:
            lines.append("暂未提交：\n" + "\n".join(rejected))
        self.reply_text(message_id, "\n".join(lines))

    def _tool_copy_batch(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id = self._required_batch(args, current_batch_id)
        batch = self.service.copy_batch(batch_id)
        self.sessions.bind_batch(chat_id, str(batch["id"]))
        self.reply_text(message_id, f'已复制并开始新批次：{batch["id"]}')

    def _tool_archive_batch(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id = self._required_batch(args, current_batch_id)
        archived = optional_bool(args.get("archived"))
        archived = True if archived is None else archived
        batch = self.service.archive_batch(batch_id, archived=archived)
        self.reply_text(
            message_id,
            f'批次 {batch["id"]} 已{"归档" if archived else "取消归档"}。',
        )

    def _tool_request_article_changes(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        job = self.service.request_job_changes(batch_id, job_id)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.reply_text(
            message_id,
            f'任务 #{job_id}（{job.get("account_name")}）已标记为“需要修改”。',
        )

    def _tool_confirm_article(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        self.service.confirm_job(batch_id, job_id)
        review = self.sessions.mark_reviewed(chat_id, job_id)
        if review.get("all_completed"):
            self.reply_text(
                message_id,
                f'任务 #{job_id} 已确认。审核进度：{review.get("completed")}/{review.get("total")}。'
                "全部文章已确认，可回复“确认全部写入草稿箱”。",
            )
            return
        next_item = review.get("next") or {}
        self.reply_text(
            message_id,
            f'任务 #{job_id} 已确认。审核进度：{review.get("completed")}/{review.get("total")}。'
            f'下一篇：{next_item.get("account_name") or ""}',
        )
        if next_item:
            batch = self.service.get_batch(batch_id, include_content=True)
            next_job = next(
                (
                    item
                    for item in batch.get("jobs") or []
                    if int(item["id"]) == int(next_item.get("job_id"))
                ),
                None,
            )
            if next_job:
                if (
                    str(next_job.get("status") or "") == "ready_for_review"
                    and hasattr(self.service, "mark_job_viewed")
                ):
                    next_job = self.service.mark_job_viewed(
                        batch_id,
                        int(next_job["id"]),
                    )
                self.send_text(chat_id, format_article_preview(next_job))

    def _tool_update_article_content(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        fields = {
            key: args[key]
            for key in ("title", "subtitle", "body", "digest")
            if key in args
        }
        if not fields:
            self.reply_text(message_id, "请提供要修改的标题、副标题、摘要或正文。")
            return
        job = self.service.update_job_content(batch_id, job_id, **fields)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.reply_text(
            message_id,
            f'任务 #{job_id} 已保存修改，审核状态已回到“已查看，未确认”。\n'
            f'当前标题：{job.get("selected_title") or "未选择"}',
        )

    def _tool_move_paragraph(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        paragraph_index = _paragraph_index(args, "paragraph_number", "paragraph_index")
        if paragraph_index is None:
            self.reply_text(message_id, "请指定要移动的段落序号。")
            return

        target_index = _paragraph_index(
            args, "target_paragraph_number", "target_index"
        )
        if target_index is None:
            direction = str(args.get("direction") or "").strip().lower()
            if direction in {"up", "上", "上移", "向上"}:
                target_index = paragraph_index - 1
            elif direction in {"down", "下", "下移", "向下"}:
                target_index = paragraph_index + 1
            else:
                self.reply_text(message_id, "请指定目标段落序号，或使用 direction=up/down。")
                return

        self.reply_text(message_id, f"正在移动任务 #{job_id} 的所选段落并重新排版……")
        job = self.service.move_paragraph(
            batch_id, job_id, paragraph_index, target_index
        )
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.send_text(
            chat_id,
            f"任务 #{job_id} 的段落已移动，文章需要重新确认。\n\n"
            + compact(job.get("body"), 1200),
        )

    def _tool_delete_paragraph(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        paragraph_index = _paragraph_index(args, "paragraph_number", "paragraph_index")
        if paragraph_index is None:
            self.reply_text(message_id, "请指定要删除的段落序号。")
            return
        self.reply_text(message_id, f"正在删除任务 #{job_id} 的所选段落并重新排版……")
        job = self.service.delete_paragraph(batch_id, job_id, paragraph_index)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.send_text(
            chat_id,
            f"任务 #{job_id} 的段落已删除，文章需要重新确认。\n\n"
            + compact(job.get("body"), 1200),
        )

    def _tool_regenerate_paragraph(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        paragraph_number = optional_int(args.get("paragraph_number"))
        if paragraph_number is None and optional_int(args.get("paragraph_index")) is not None:
            paragraph_number = (optional_int(args.get("paragraph_index")) or 0) + 1
        if not paragraph_number or paragraph_number < 1:
            self.reply_text(message_id, "请指定从 1 开始的段落序号。")
            return
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            self.reply_text(message_id, "请说明希望如何修改这一段，例如压缩、改语气或突出哪项数据。")
            return
        self.reply_text(message_id, f"正在重新生成任务 #{job_id} 第 {paragraph_number} 段……")
        job = self.service.regenerate_paragraph(
            batch_id,
            job_id,
            paragraph_number - 1,
            instruction=instruction,
        )
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.send_text(
            chat_id,
            f'任务 #{job_id} 第 {paragraph_number} 段已重新生成，文章需要重新确认。\n\n'
            + compact(job.get("body"), 1200),
        )

    def _tool_rerender_article(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        self.reply_text(message_id, f"任务 #{job_id} 正在重新套用排版和模板……")
        job = self.service.rerender_job(batch_id, job_id)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.send_text(chat_id, f'任务 #{job_id} 已重新排版。\n' + format_article_preview(job))

    def _tool_list_article_versions(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        versions = self.service.list_job_versions(batch_id, job_id)
        if not versions:
            self.reply_text(message_id, f"任务 #{job_id} 还没有历史版本。")
            return
        lines = [f"任务 #{job_id} 历史版本："]
        for index, item in enumerate(versions[:20], 1):
            lines.append(
                f'\n{index}. 版本 {item.get("id")}｜{item.get("created_at") or ""}\n'
                f'   {compact(item.get("reason") or item.get("title"), 100)}'
            )
        self.sessions.update(
            chat_id,
            article_versions=[
                {"number": index, "id": item.get("id"), "job_id": job_id}
                for index, item in enumerate(versions[:20], 1)
            ],
        )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_restore_article_version(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        version_id = optional_int(args.get("version_id"))
        version_number = optional_int(args.get("version_number"))
        if not version_id and version_number:
            version_id = self._session_number_id(chat_id, "article_versions", version_number)
        if not version_id:
            self.reply_text(message_id, "请指定版本 ID，或先查询文章历史版本。")
            return
        job = self.service.restore_job_version(batch_id, job_id, version_id)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.reply_text(
            message_id,
            f'任务 #{job_id} 已恢复版本 {version_id}，文章需要重新确认。\n'
            f'当前标题：{job.get("selected_title") or "未选择"}',
        )

    def _tool_get_article_assets(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        batch = self.service.get_batch(batch_id, include_content=True)
        job = next((item for item in batch.get("jobs") or [] if int(item["id"]) == job_id), None)
        if not job:
            raise KeyError(f"任务不存在：{job_id}")
        meta = dict(job.get("meta") or {})
        images = list(meta.get("inline_images") or [])
        lines = [
            f'任务 #{job_id} 图像信息：',
            f'封面素材 ID：{job.get("thumb_media_id") or "尚未确定"}',
            f'正文配图：{len(images)} 张',
        ]
        generated_cover = meta.get("generated_cover") or {}
        if isinstance(generated_cover, dict) and generated_cover.get("url"):
            lines.append(f'AI 封面：{generated_cover.get("url")}')
        for index, image in enumerate(images, 1):
            image_id = image.get("index") or image.get("image_index") or index
            lines.append(
                f'\n{index}. 配图编号 {image_id}｜{image.get("source") or "image"}\n'
                f'   {compact(image.get("argument_title") or image.get("heading") or image.get("url"), 160)}'
            )
            if image.get("url"):
                lines.append(f'   {image.get("url")}')
        self.reply_text(message_id, "\n".join(lines))
        if self.send_image is None:
            return
        local_images: list[Path] = []
        local_asset_urls: set[str] = set()
        if isinstance(generated_cover, dict):
            cover_path = Path(str(generated_cover.get("local_path") or ""))
            if str(cover_path) not in {"", "."} and cover_path.is_file():
                local_images.append(cover_path)
        root = Path(str(self.config.get("_root") or Path.cwd()))
        for image in images:
            if str(image.get("source") or "") != "generated":
                continue
            image_id = image.get("index") or image.get("image_index")
            if image_id is None:
                continue
            saved_path = Path(str(image.get("local_path") or ""))
            candidate = (
                saved_path
                if str(saved_path) not in {"", "."} and saved_path.is_file()
                else root
                / "data"
                / "generated_images"
                / str(job_id)
                / f"inline_{image_id}.jpg"
            )
            if candidate.is_file():
                local_images.append(candidate)
                local_asset_urls.add(str(image.get("url") or ""))
        for path in local_images[:7]:
            self.send_image(chat_id, path, file_name=path.name)
        sent_urls = {
            str(generated_cover.get("url") or "")
            if isinstance(generated_cover, dict)
            else ""
        }
        sent_urls.update(item for item in local_asset_urls if item)
        remaining = max(0, 7 - len(local_images[:7]))
        for index, item in enumerate(images, 1):
            if remaining <= 0:
                break
            url = str(item.get("url") or "")
            if not url or url in sent_urls:
                continue
            try:
                payload, extension = download_wechat_image(url)
                self.send_image(
                    chat_id,
                    payload,
                    file_name=f"job_{job_id}_inline_{index}.{extension}",
                )
                sent_urls.add(url)
                remaining -= 1
            except Exception:
                continue

    def _tool_regenerate_inline_images(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        self.reply_text(message_id, f"任务 #{job_id} 正在重新生成正文配图……")
        job = self.service.regenerate_inline_images(batch_id, job_id)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        count = len((job.get("meta") or {}).get("inline_images") or [])
        self.send_text(
            chat_id,
            f"任务 #{job_id} 正文配图已重新生成，共 {count} 张，文章需要重新确认。",
        )

    def _tool_regenerate_inline_image(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        image_index = optional_int(args.get("image_index"))
        instruction = str(args.get("instruction") or "").strip()
        if image_index is None or image_index < 1:
            self.reply_text(message_id, "请指定从 1 开始的正文配图编号。")
            return
        if not instruction:
            self.reply_text(message_id, "请说明希望这张图片如何修改。")
            return
        self.reply_text(
            message_id,
            f"任务 #{job_id} 正在按要求只重新生成正文配图 {image_index}，其他图片保持不变……",
        )
        job = self.service.regenerate_inline_image(
            batch_id,
            job_id,
            image_index,
            instruction=instruction,
        )
        asset = next(
            (
                item
                for item in (job.get("meta") or {}).get("inline_images") or []
                if int(item.get("index") or item.get("image_index") or 0)
                == image_index
            ),
            {},
        )
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.send_text(
            chat_id,
            f"任务 #{job_id} 的正文配图 {image_index} 已按要求重新生成，文章需要重新确认。"
            + (f'\n{asset.get("url")}' if asset.get("url") else ""),
        )

    def _tool_remove_inline_image(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        image_index = optional_int(args.get("image_index"))
        if image_index is None:
            self.reply_text(message_id, "请指定要删除的正文配图编号。")
            return
        job = self.service.remove_inline_image(batch_id, job_id, image_index)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.reply_text(
            message_id,
            f"任务 #{job_id} 的正文配图 {image_index} 已删除，文章需要重新确认。",
        )

    def _tool_regenerate_cover(
        self,
        args: dict[str, Any],
        *,
        original_text: str,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        self.reply_text(message_id, f"任务 #{job_id} 正在重新生成 AI 封面……")
        job = self.service.regenerate_cover(
            batch_id,
            job_id,
            instruction=str(args.get("instruction") or ""),
        )
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.send_text(
            chat_id,
            f'任务 #{job_id} AI 封面已重新生成，文章需要重新确认。\n'
            f'素材 ID：{job.get("thumb_media_id") or "生成失败，请查看提示"}',
        )

    def _tool_list_cover_options(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        limit = max(1, min(optional_int(args.get("limit")) or 12, 50))
        offset = max(0, optional_int(args.get("offset")) or 0)
        items = self.service.list_cover_options(batch_id, job_id, limit=limit, offset=offset)
        if not items:
            self.reply_text(message_id, "该公众号素材库没有可选图片。")
            return
        numbered = [
            {"number": index, **item, "job_id": job_id}
            for index, item in enumerate(items, 1)
        ]
        self.sessions.update(chat_id, cover_options=numbered)
        lines = [f"任务 #{job_id} 可选封面素材："]
        for item in numbered:
            lines.append(
                f'\n{item["number"]}. {compact(item.get("name"), 80)}\n'
                f'   media_id: {item.get("media_id")}'
            )
            if item.get("url"):
                lines.append(f'   {item.get("url")}')
        lines.append("\n下一步可回复：选择封面 2")
        self.reply_text(message_id, "\n".join(lines))
        if self.send_image is not None:
            for item in numbered[:6]:
                try:
                    payload, extension = download_wechat_image(str(item.get("url") or ""))
                    self.send_image(
                        chat_id,
                        payload,
                        file_name=f'cover_{item["number"]}.{extension}',
                    )
                except Exception:
                    continue

    def _tool_select_cover(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        media_id = str(args.get("media_id") or args.get("thumb_media_id") or "").strip()
        number = optional_int(args.get("cover_number"))
        if not media_id and number:
            options = list(self.sessions.get(chat_id).get("cover_options") or [])
            selected = next(
                (item for item in options if optional_int(item.get("number")) == number),
                None,
            )
            media_id = str((selected or {}).get("media_id") or "")
        if not media_id:
            self.reply_text(message_id, "请指定封面序号或素材 media_id。")
            return
        job = self.service.select_job_cover(batch_id, job_id, media_id)
        self.sessions.reopen_review(
            chat_id,
            job_id,
            account_name=str(job.get("account_name") or ""),
        )
        self.reply_text(
            message_id,
            f'任务 #{job_id} 已选择封面素材：{job.get("thumb_media_id")}，文章需要重新确认。',
        )

    def _required_batch(
        self, args: dict[str, Any], current_batch_id: str | None
    ) -> str:
        batch_id = batch_id_from(args, current_batch_id)
        if not batch_id:
            raise ValueError("当前会话没有批次，请指定批次 ID")
        return batch_id

    def _job_context(
        self,
        args: dict[str, Any],
        chat_id: str,
        current_batch_id: str | None,
    ) -> tuple[str, int]:
        batch_id = self._required_batch(args, current_batch_id)
        return batch_id, require_job_id(
            args, self.sessions.current_review_job_id(chat_id)
        )

    def _session_number_id(self, chat_id: str, key: str, number: int) -> int | None:
        rows = list(self.sessions.get(chat_id).get(key) or [])
        row = next(
            (item for item in rows if optional_int(item.get("number")) == number),
            None,
        )
        return optional_int((row or {}).get("id"))


def _paragraph_index(
    args: dict[str, Any], number_key: str, index_key: str
) -> int | None:
    """Accept human-facing one-based numbers and protocol-level zero-based indexes."""

    paragraph_number = optional_int(args.get(number_key))
    if paragraph_number is not None:
        return paragraph_number - 1 if paragraph_number >= 1 else None
    paragraph_index = optional_int(args.get(index_key))
    return paragraph_index if paragraph_index is not None and paragraph_index >= 0 else None
