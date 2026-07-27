from __future__ import annotations

from typing import Any

from app.feishu.tool_modules.common import compact, optional_bool, optional_int, string_list


_PROFILE_CONFIG_FIELDS = {
    "role_ids",
    "style_ids",
    "strictness",
    "focus",
    "target_audience",
    "required_checks",
    "ignored_items",
    "banned_expressions",
    "must_keep",
    "dimension_strictness",
    "score_weights",
    "good_example",
    "bad_example",
    "advanced_rules",
    "permissions",
}

_REWRITE_MODE_ALIASES = {
    "engagement_optimization": "engagement_optimization",
    "按传播目标整体优化": "engagement_optimization",
    "整体优化": "engagement_optimization",
    "selected_issues": "selected_issues",
    "只修复勾选问题": "selected_issues",
    "勾选建议": "selected_issues",
    "role_guided": "role_guided",
    "按评审角色建议修改": "role_guided",
    "target_style": "target_style",
    "改成目标风格": "target_style",
    "high_priority": "high_priority",
    "只修改高优先级问题": "high_priority",
    "title_only": "title_only",
    "只修改标题": "title_only",
    "selected_paragraphs": "selected_paragraphs",
    "只修改指定段落": "selected_paragraphs",
    "full_rewrite": "full_rewrite",
    "全文重新改写": "full_rewrite",
}

_RESOLUTION_ALIASES = {
    "open": "open",
    "重新打开": "open",
    "未解决": "open",
    "resolved": "resolved",
    "已核实": "resolved",
    "已解决": "resolved",
    "waived": "waived",
    "接受风险": "waived",
    "豁免": "waived",
}

_ENGAGEMENT_REVIEW_FOCUS = (
    "重点评估标题吸引力、开头抓力、预计完读率、点赞意愿和转发动机；"
    "只给少量影响整体阅读与传播效果的建议，不逐段挑字眼或做局部措辞点评。"
)
_ENGAGEMENT_REQUIRED_CHECKS = [
    "标题是否准确且有点击动力",
    "开头是否能快速建立阅读理由",
    "文章结构与节奏是否支持读者完读",
    "文章是否能形成自然的点赞意愿",
    "文章是否提供明确且真实的转发动机",
]
_ENGAGEMENT_ADVANCED_RULES = (
    "最多给出 5 条可执行的整体改进建议。建议必须围绕标题、开头、阅读节奏、"
    "预计完读率、点赞意愿或转发动机；不要逐段校对，不要对孤立字词吹毛求疵。"
)


class EditorialReviewToolMixin:
    """AI editorial-review tools shared by Feishu's deterministic executor."""

    def _tool_list_editorial_review_profiles(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        include_builtin = optional_bool(args.get("include_builtin"))
        profiles = self.service.list_editorial_review_profiles(
            include_builtin=True if include_builtin is None else include_builtin
        )
        if bool(args.get("enabled_only", False)):
            profiles = [item for item in profiles if bool(item.get("enabled"))]
        if not profiles:
            self.reply_text(message_id, "当前没有可用的 AI 评审方案。")
            return

        options = self.service.get_editorial_review_options()
        roles = {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in options.get("roles") or []
        }
        styles = {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in options.get("styles") or []
        }
        session_rows: list[dict[str, Any]] = []
        lines = ["AI 评审团方案："]
        for number, profile in enumerate(profiles[:30], 1):
            config = dict(profile.get("config") or {})
            role_names = "、".join(
                roles.get(str(item), str(item))
                for item in config.get("role_ids") or []
            )
            style_names = "、".join(
                styles.get(str(item), str(item))
                for item in config.get("style_ids") or []
            )
            source = "内置" if profile.get("builtin") else "自定义"
            lines.append(
                f"\n{number}. {profile.get('name')}｜{source}｜"
                f"{_strictness_name(config.get('strictness'))}\n"
                f"   角色：{role_names or '未设置'}\n"
                f"   风格：{style_names or '未设置'}"
            )
            session_rows.append(
                {
                    "number": number,
                    "id": str(profile.get("id") or ""),
                    "name": str(profile.get("name") or ""),
                }
            )
        self.sessions.update(chat_id, editorial_review_profiles=session_rows)
        self.reply_text(message_id, "\n".join(lines))

    def _tool_save_editorial_review_profile(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        name = str(args.get("name") or "").strip()
        if not name:
            raise ValueError("请填写评审方案名称")
        profile_id = self._editorial_profile_id(
            args, chat_id, required=False, allow_name=False
        )
        config = self._editorial_config(args)
        saved = self.service.save_editorial_review_profile(
            profile_id=profile_id or None,
            name=name,
            description=str(args.get("description") or ""),
            enabled=optional_bool(args.get("enabled")) is not False,
            config=config,
        )
        self.sessions.update(
            chat_id,
            editorial_review_profiles=[
                {
                    "number": 1,
                    "id": str(saved.get("id") or ""),
                    "name": str(saved.get("name") or ""),
                }
            ],
        )
        self.reply_text(
            message_id,
            f'AI 评审方案已保存：{saved.get("name")}（方案编号 1）。',
        )

    def _tool_delete_editorial_review_profile(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        profile_id = self._editorial_profile_id(args, chat_id, required=True)
        self.service.delete_editorial_review_profile(profile_id)
        self.reply_text(message_id, "所选自定义 AI 评审方案已删除。")

    def _tool_get_account_editorial_review_default(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        **_: Any,
    ) -> None:
        account = self._one_account(args)
        result = self.service.get_account_editorial_review_default(
            str(account["id"])
        )
        config = dict(result.get("config") or {})
        options = self.service.get_editorial_review_options()
        role_names = {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in options.get("roles") or []
        }
        style_names = {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in options.get("styles") or []
        }
        self.reply_text(
            message_id,
            f'公众号：{result.get("account_name")}\n'
            f'默认评审方案：{result.get("profile_name")}\n'
            f'严格程度：{_strictness_name(config.get("strictness"))}\n'
            f'评审角色：{"、".join(role_names.get(str(item), str(item)) for item in config.get("role_ids") or [])}\n'
            f'目标风格：{"、".join(style_names.get(str(item), str(item)) for item in config.get("style_ids") or [])}',
        )

    def _tool_set_account_editorial_review_default(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        account = self._one_account(args)
        profile_id = self._editorial_profile_id(args, chat_id, required=True)
        overrides = self._editorial_config(args)
        result = self.service.set_account_editorial_review_default(
            str(account["id"]),
            profile_id=profile_id,
            config=overrides or None,
        )
        self.reply_text(
            message_id,
            f'{result.get("account_name")}的默认 AI 评审方案已设为：'
            f'{result.get("profile_name")}。',
        )

    def _tool_run_editorial_review(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        batch_id, job_id = self._job_context(args, chat_id, current_batch_id)
        profile_id = self._editorial_profile_id(
            args, chat_id, required=False
        )
        self.reply_text(
            message_id,
            f"正在对任务 #{job_id} 启动 AI 评审团，请稍候……",
        )
        review = self.service.run_editorial_review(
            batch_id,
            job_id,
            profile_id=profile_id or None,
            config=self._engagement_review_config(args),
        )
        self.sessions.reopen_review(chat_id, job_id)
        self._remember_editorial_review(chat_id, review)
        self.send_text(chat_id, self._format_editorial_review(review))

    def _tool_get_editorial_review(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        review = self._resolve_editorial_review(
            args,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
        )
        self._remember_editorial_review(chat_id, review)
        review_history = self.service.list_editorial_reviews(
            batch_id=str(review["batch_id"]),
            job_id=int(review["job_id"]),
            limit=max(1, min(optional_int(args.get("limit")) or 20, 50)),
        )
        self.sessions.update(
            chat_id,
            editorial_reviews=[
                {
                    "number": number,
                    "id": str(item.get("id") or ""),
                    "batch_id": str(item.get("batch_id") or ""),
                    "job_id": int(item.get("job_id") or 0),
                }
                for number, item in enumerate(review_history, 1)
            ],
        )
        applications = self.service.list_editorial_review_applications(
            str(review["id"]), limit=20
        )
        app_rows = [
            {
                "number": number,
                "id": str(item.get("id") or ""),
                "review_id": str(review["id"]),
            }
            for number, item in enumerate(applications, 1)
        ]
        self.sessions.update(
            chat_id,
            editorial_review_applications=app_rows,
            current_editorial_review_application_id=(
                str(applications[0].get("id") or "") if applications else ""
            ),
        )
        text = self._format_editorial_review(review)
        if len(review_history) > 1:
            text += "\n\n该文章最近 AI 评审："
            for number, item in enumerate(review_history[:10], 1):
                text += (
                    f"\n{number}. {item.get('profile_name')}｜{item.get('status')}｜"
                    f"{item.get('created_at') or ''}"
                )
        if applications:
            text += "\n\n修改稿："
            for number, application in enumerate(applications[:10], 1):
                candidate = dict(application.get("candidate_snapshot") or {})
                text += (
                    f"\n{number}. {application.get('status')}｜"
                    f"{compact(candidate.get('change_summary') or application.get('error'), 120)}"
                )
            latest_candidate = dict(
                (applications[0] or {}).get("candidate_snapshot") or {}
            )
            if latest_candidate:
                text += (
                    "\n\n最新候选修改稿预览：\n"
                    f'标题：{latest_candidate.get("title") or "未修改"}\n'
                    f'正文：\n{_body_preview(latest_candidate.get("body"), 1200)}\n\n'
                    "这是候选稿，尚未覆盖原稿。可回复“查看当前文章”使用现有文章预览查看原稿，"
                    "对比无误后再确认应用对应修改稿编号。"
                )
        self.reply_text(message_id, text)

    def _tool_generate_editorial_rewrite_candidate(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        review = self._resolve_editorial_review(
            args,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
        )
        issue_ids = self._editorial_issue_ids(args, chat_id, review)
        rewrite_mode = _REWRITE_MODE_ALIASES.get(
            str(args.get("rewrite_mode") or "selected_issues").strip(),
            str(args.get("rewrite_mode") or "selected_issues").strip(),
        )
        paragraph_numbers = _positive_int_list(args.get("paragraph_numbers"))
        self.reply_text(
            message_id,
            f"正在根据已勾选建议生成任务 #{review['job_id']} 的候选修改稿……",
        )
        result = self.service.generate_editorial_rewrite_candidate(
            str(review["batch_id"]),
            int(review["job_id"]),
            str(review["id"]),
            issue_ids=issue_ids,
            rewrite_mode=rewrite_mode,
            paragraph_numbers=paragraph_numbers or None,
            instruction=str(args.get("instruction") or ""),
        )
        application = dict(result.get("application") or {})
        if not application:
            raise RuntimeError("服务没有返回候选修改稿")
        self._remember_editorial_review(chat_id, result)
        self.sessions.update(
            chat_id,
            editorial_review_applications=[
                {
                    "number": 1,
                    "id": str(application.get("id") or ""),
                    "review_id": str(result.get("id") or ""),
                }
            ],
            current_editorial_review_application_id=str(
                application.get("id") or ""
            ),
        )
        candidate = dict(application.get("candidate_snapshot") or {})
        self.send_text(
            chat_id,
            "候选修改稿已生成（修改稿编号 1），尚未覆盖原稿。\n"
            f'标题：{candidate.get("title") or "未修改"}\n'
            f'改动说明：{compact(candidate.get("change_summary"), 500)}\n'
            f'正文字数：{len(str(candidate.get("body") or ""))}\n'
            f'候选正文预览：\n{_body_preview(candidate.get("body"), 1400)}\n\n'
            "可先回复“查看当前文章”使用现有文章预览查看原稿。"
            "逐项对比无误后，再回复“确认应用 AI 修改稿 1”。",
        )

    def _tool_smart_rewrite_from_editorial_review(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        """Generate and apply one rewrite from user-selected visible issues.

        This is the simplified Feishu flow used after the review conclusion is
        shown.  The older generate/apply tools intentionally remain available
        for users who still want to compare an intermediate candidate.
        """

        review = self._resolve_editorial_review(
            args,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
        )
        issue_ids = self._editorial_issue_ids(args, chat_id, review)
        if not issue_ids:
            raise ValueError("请按评审结论中显示的编号选择至少一条改进建议")

        self.reply_text(
            message_id,
            f"正在按已选择的 {len(issue_ids)} 条评审建议智能修改任务 "
            f"#{review['job_id']}，完成后会直接回到待确认状态……",
        )
        result = self.service.generate_editorial_rewrite_candidate(
            str(review["batch_id"]),
            int(review["job_id"]),
            str(review["id"]),
            issue_ids=issue_ids,
            rewrite_mode="engagement_optimization",
            paragraph_numbers=None,
            instruction="",
        )
        application = dict(result.get("application") or {})
        if not application or not str(application.get("id") or "").strip():
            raise RuntimeError("服务没有返回可应用的 AI 修改稿")

        job = self.service.apply_editorial_review_application(
            str(review["batch_id"]),
            int(review["job_id"]),
            str(application["id"]),
        )
        refreshed_review = self.service.get_editorial_review(str(review["id"]))
        refreshed_application = self.service.get_editorial_review_application(
            str(application["id"])
        )
        self._remember_editorial_review(chat_id, refreshed_review)
        self.sessions.update(
            chat_id,
            editorial_review_applications=[
                {
                    "number": 1,
                    "id": str(refreshed_application.get("id") or application["id"]),
                    "review_id": str(
                        refreshed_application.get("review_id") or review["id"]
                    ),
                }
            ],
            current_editorial_review_application_id=str(
                refreshed_application.get("id") or application["id"]
            ),
        )
        self.sessions.reopen_review(
            chat_id,
            int(review["job_id"]),
            account_name=str(job.get("account_name") or ""),
        )
        self.sessions.set_current_review_job(chat_id, int(review["job_id"]))
        self.send_text(
            chat_id,
            f'任务 #{review["job_id"]} 已按所选评审建议智能修改原文并重新排版。\n'
            "修改结果已原位回到“已查看，未确认”，请查看当前文章后重新确认；"
            "未选择的评审建议不会用于本次修改。",
        )

    def _tool_apply_editorial_review_application(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        application = self._resolve_editorial_application(args, chat_id)
        review = self.service.get_editorial_review(
            str(application["review_id"])
        )
        self._assert_editorial_review_scope(
            review,
            args=args,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
        )
        job = self.service.apply_editorial_review_application(
            str(review["batch_id"]),
            int(review["job_id"]),
            str(application["id"]),
        )
        self.sessions.reopen_review(
            chat_id,
            int(review["job_id"]),
            account_name=str(job.get("account_name") or ""),
        )
        self.sessions.set_current_review_job(chat_id, int(review["job_id"]))
        self.reply_text(
            message_id,
            f'任务 #{review["job_id"]} 已应用 AI 候选修改稿并重新排版，'
            "审核状态已回到“已查看，未确认”，请重新检查并确认文章。",
        )

    def _tool_resolve_editorial_review_issue(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
        **_: Any,
    ) -> None:
        review = self._resolve_editorial_review(
            args,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
        )
        issue_ids = self._editorial_issue_ids(
            args, chat_id, review, single=True
        )
        resolution = _RESOLUTION_ALIASES.get(
            str(args.get("resolution") or "").strip(),
            str(args.get("resolution") or "").strip(),
        )
        if resolution not in {"open", "resolved", "waived"}:
            raise ValueError("核实结果应为：已核实、接受风险或重新打开")
        updated = self.service.resolve_editorial_review_issue(
            str(review["id"]),
            issue_ids[0],
            resolution=resolution,
            note=str(args.get("note") or ""),
            resolved_by=open_id,
        )
        self._remember_editorial_review(chat_id, updated)
        self.reply_text(
            message_id,
            f"评审建议核实状态已更新；当前仍有 "
            f"{updated.get('blocking_count', 0)} 条事实或合规阻断项。",
        )

    def _editorial_config(self, args: dict[str, Any]) -> dict[str, Any]:
        config = dict(args.get("config") or {}) if isinstance(args.get("config"), dict) else {}
        for key in _PROFILE_CONFIG_FIELDS:
            if key in args:
                config[key] = args[key]
        return config

    def _engagement_review_config(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply the product review lens without requiring prompt knowledge."""

        config = self._editorial_config(args)
        custom_focus = str(config.get("focus") or "").strip()
        config["focus"] = (
            _ENGAGEMENT_REVIEW_FOCUS
            + (f"\n公众号补充重点：{custom_focus}" if custom_focus else "")
        )
        checks = [
            *string_list(config.get("required_checks")),
            *_ENGAGEMENT_REQUIRED_CHECKS,
        ]
        config["required_checks"] = list(dict.fromkeys(checks))
        custom_rules = str(config.get("advanced_rules") or "").strip()
        config["advanced_rules"] = (
            _ENGAGEMENT_ADVANCED_RULES
            + (f"\n公众号补充规则：{custom_rules}" if custom_rules else "")
        )
        return config

    def _editorial_profile_id(
        self,
        args: dict[str, Any],
        chat_id: str,
        *,
        required: bool,
        allow_name: bool = True,
    ) -> str:
        profile_id = str(args.get("profile_id") or "").strip()
        profile_number = optional_int(args.get("profile_number"))
        profile_name = str(args.get("profile_name") or "").strip() if allow_name else ""
        session_rows = list(
            self.sessions.get(chat_id).get("editorial_review_profiles") or []
        )
        if profile_number:
            matched = next(
                (
                    item
                    for item in session_rows
                    if optional_int(item.get("number")) == profile_number
                ),
                None,
            )
            profile_id = str((matched or {}).get("id") or "")
            if not profile_id:
                raise ValueError("没有找到该评审方案编号，请先列出 AI 评审方案")
        if not profile_id and profile_name:
            profiles = self.service.list_editorial_review_profiles(
                include_builtin=True
            )
            matched = next(
                (
                    item
                    for item in profiles
                    if str(item.get("name") or "") == profile_name
                ),
                None,
            )
            profile_id = str((matched or {}).get("id") or "")
            if not profile_id:
                raise ValueError("没有找到该 AI 评审方案")
        if required and not profile_id:
            raise ValueError("请先指定评审方案编号或名称")
        return profile_id

    def _resolve_editorial_review(
        self,
        args: dict[str, Any],
        *,
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
    ) -> dict[str, Any]:
        context = self.sessions.get(chat_id)
        review_id = str(args.get("review_id") or "").strip()
        review_number = optional_int(args.get("review_number"))
        if review_number:
            matched = next(
                (
                    item
                    for item in context.get("editorial_reviews") or []
                    if optional_int(item.get("number")) == review_number
                ),
                None,
            )
            review_id = str((matched or {}).get("id") or "")
            if not review_id:
                raise ValueError("没有找到该评审编号，请先查看 AI 评审结果")
        if not review_id and optional_int(args.get("job_id")) is None:
            review_id = str(context.get("current_editorial_review_id") or "")
        if review_id:
            review = self.service.get_editorial_review(review_id)
        else:
            batch_id, job_id = self._job_context(
                args, chat_id, current_batch_id
            )
            rows = self.service.list_editorial_reviews(
                batch_id=batch_id, job_id=job_id, limit=20
            )
            if not rows:
                raise ValueError("当前文章还没有 AI 评审结果")
            review = rows[0]
        self._assert_editorial_review_scope(
            review,
            args=args,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=current_batch_id,
        )
        return review

    def _assert_editorial_review_scope(
        self,
        review: dict[str, Any],
        *,
        args: dict[str, Any],
        chat_id: str,
        open_id: str,
        current_batch_id: str | None,
    ) -> None:
        review_batch_id = str(review.get("batch_id") or "")
        requested_batch_id = str(
            args.get("batch_id") or current_batch_id or ""
        ).strip()
        if requested_batch_id and requested_batch_id != review_batch_id:
            raise ValueError("所选 AI 评审不属于当前会话批次")
        requested_job_id = optional_int(args.get("job_id"))
        if requested_job_id and requested_job_id != int(review.get("job_id") or 0):
            raise ValueError("所选 AI 评审不属于指定文章")
        if self.admin_open_ids is None or open_id in self.admin_open_ids:
            return
        batch = self.service.get_batch(review_batch_id)
        if not (
            str(batch.get("requested_by") or "") == open_id
            or str(batch.get("chat_id") or "") == chat_id
        ):
            raise ValueError("你没有权限操作其他用户或群聊创建的 AI 评审")

    def _remember_editorial_review(
        self, chat_id: str, review: dict[str, Any]
    ) -> None:
        # Keep stable server ordering for both editorial directions and safety
        # risks. The formatter reuses these numbers when showing either group.
        issues = list((review.get("result") or {}).get("issues") or [])
        self.sessions.update(
            chat_id,
            current_editorial_review_id=str(review.get("id") or ""),
            editorial_reviews=[
                {
                    "number": 1,
                    "id": str(review.get("id") or ""),
                    "batch_id": str(review.get("batch_id") or ""),
                    "job_id": int(review.get("job_id") or 0),
                }
            ],
            editorial_review_issues=[
                {
                    "number": number,
                    "id": str(item.get("id") or ""),
                    "review_id": str(review.get("id") or ""),
                    "can_auto_apply": bool(item.get("can_auto_apply")),
                }
                for number, item in enumerate(issues, 1)
            ],
        )

    def _editorial_issue_ids(
        self,
        args: dict[str, Any],
        chat_id: str,
        review: dict[str, Any],
        *,
        single: bool = False,
    ) -> list[str]:
        value = args.get("issue_number") if single else args.get("issue_numbers")
        numbers = _positive_int_list(value)
        if single and not numbers:
            numbers = _positive_int_list(args.get("issue_numbers"))
        if not numbers:
            if single:
                raise ValueError("请指定评审建议编号")
            return []
        rows = [
            item
            for item in self.sessions.get(chat_id).get("editorial_review_issues") or []
            if str(item.get("review_id") or "") == str(review.get("id") or "")
        ]
        by_number = {
            optional_int(item.get("number")): str(item.get("id") or "")
            for item in rows
        }
        missing = [number for number in numbers if not by_number.get(number)]
        if missing:
            raise ValueError(
                "没有找到评审建议编号 "
                + "、".join(str(item) for item in missing)
                + "，请先查看 AI 评审结果"
            )
        resolved = [by_number[number] for number in numbers]
        if single and len(resolved) != 1:
            raise ValueError("每次只能更新一条评审建议")
        return resolved

    def _resolve_editorial_application(
        self, args: dict[str, Any], chat_id: str
    ) -> dict[str, Any]:
        context = self.sessions.get(chat_id)
        application_id = str(args.get("application_id") or "").strip()
        application_number = optional_int(args.get("application_number"))
        if application_number:
            matched = next(
                (
                    item
                    for item in context.get("editorial_review_applications") or []
                    if optional_int(item.get("number")) == application_number
                ),
                None,
            )
            application_id = str((matched or {}).get("id") or "")
            if not application_id:
                raise ValueError("没有找到该修改稿编号，请先查看 AI 评审结果")
        application_id = application_id or str(
            context.get("current_editorial_review_application_id") or ""
        )
        if not application_id:
            raise ValueError("当前没有可应用的 AI 修改稿，请先生成候选修改稿")
        return self.service.get_editorial_review_application(application_id)

    def _format_editorial_review(self, review: dict[str, Any]) -> str:
        result = dict(review.get("result") or {})
        issues = list(result.get("issues") or [])
        numbered_issues = list(enumerate(issues, 1))
        directions = [
            (number, item)
            for number, item in numbered_issues
            if bool(item.get("can_auto_apply"))
        ][:5]
        safety_issues = [
            (number, item)
            for number, item in numbered_issues
            if not bool(item.get("can_auto_apply"))
        ]
        lines = [
            f'AI 评审完成｜{review.get("profile_name")}｜'
            f'{review.get("model_name") or "公众号绑定模型"}',
            f'综合评分：{result.get("overall_score", 0)}',
            f'结论：{compact(result.get("summary"), 500)}',
            f'事实/合规阻断项：{review.get("blocking_count", 0)}',
            "本轮重点：标题、开头、预计完读率、点赞意愿、转发动机",
        ]
        dimensions = list(result.get("dimensions") or [])
        if dimensions:
            lines.append("\n重点维度：")
            for dimension in dimensions[:5]:
                lines.append(
                    f"- {dimension.get('name') or '维度'}："
                    f"{dimension.get('score', 0)}"
                    + (
                        f"｜{compact(dimension.get('summary'), 120)}"
                        if dimension.get("summary")
                        else ""
                    )
                )
            lines.append("以上为 AI 预估的运营潜力分，不是真实公众号后台数据。")
        if directions:
            lines.append("\n整体改进建议（请按编号选择是否接受）：")
            for number, issue in directions:
                lines.append(
                    f"\n{number}. [{_severity_name(issue.get('severity'))}] "
                    f"{issue.get('role_name') or issue.get('category') or '评审建议'}"
                    "｜可交给 AI 整体优化\n"
                    f"   评审判断：{compact(issue.get('problem'), 180)}\n"
                    f"   整体方向：{compact(issue.get('suggestion'), 220)}"
                )
        else:
            lines.append("\n未发现需要智能修改的整体方向。")
        if safety_issues:
            lines.append("\n发布风险（不能交给 AI 猜测，请人工核实）：")
            for number, issue in safety_issues[:10]:
                lines.append(
                    f"- {number}. [{_severity_name(issue.get('severity'))}] "
                    f"{compact(issue.get('problem'), 180)}｜"
                    f"{compact(issue.get('suggestion'), 180)}"
                )
        lines.append(
            "\n请选择下一步：\n"
            "- 使用原文：原文保持不变，继续待确认。\n"
            "- 智能修改原文：回复例如“确认智能修改原文，接受第 1、3 条建议”。"
            "系统只采用你选择的建议，围绕标题、开头、阅读节奏、点赞和转发动机"
            "做整体优化，一次完成修改和应用，不需要另填修改意见，也不会逐段润色。"
        )
        return "\n".join(lines)


def _positive_int_list(value: Any) -> list[int]:
    values = string_list(value)
    result: list[int] = []
    for item in values:
        parsed = optional_int(item)
        if parsed and parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def _strictness_name(value: Any) -> str:
    return {
        "lenient": "宽松",
        "standard": "标准",
        "strict": "严格",
    }.get(str(value or ""), str(value or "标准"))


def _severity_name(value: Any) -> str:
    return {
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(str(value or ""), str(value or "中"))


def _body_preview(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return "（候选稿没有正文）"
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"
