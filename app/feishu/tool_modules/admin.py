from __future__ import annotations

from typing import Any

from app.accounts import apply_account_selection
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import (
    public_models,
)
from app.config import load_config
from app.layout_profiles import normalize_layout, validate_layout
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    DEFAULT_IMAGE_PROMPT_STYLE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_MODE_DEFAULT,
    PROMPT_MODE_TEMPLATE,
    public_prompt_templates,
)
from app.wechat.template_snapshot import (
    list_template_draft_candidates,
    save_template_draft_candidate,
)
from app.wechat.factory import build_wechat_client
from app.feishu.tool_modules.common import compact, optional_bool, optional_int, string_list


class AdminToolMixin:
    """Safe account, model, prompt, layout and draft-template configuration."""

    def _tool_list_prompt_templates(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        purpose = self._prompt_purpose(args.get("purpose"))
        rows = self._config().list_prompt_templates(
            purpose=purpose,
            enabled_only=bool(args.get("enabled_only", False)),
        )
        label = "文章" if purpose == ARTICLE_PROMPT_PURPOSE else "图片"
        if not rows:
            self.reply_text(message_id, f"还没有{label}提示词模板。")
            return
        lines = [f"{label}提示词模板："]
        for item in rows:
            lines.append(
                f'\n• {item.get("name")}｜{"启用" if item.get("enabled") else "停用"}\n'
                f'  ID：{item.get("id")}\n  {compact(item.get("content"), 180)}'
            )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_save_prompt_template(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        purpose = self._prompt_purpose(args.get("purpose"))
        saved = self._config().save_prompt_template(
            template_id=str(args.get("template_id") or "").strip() or None,
            name=str(args.get("name") or ""),
            content=str(args.get("content") or ""),
            enabled=optional_bool(args.get("enabled")) is not False,
            purpose=purpose,
        )
        self.reply_text(message_id, f'提示词模板已保存，ID：{saved.get("id")}')

    def _tool_delete_prompt_template(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        template_id = self._resolve_prompt_template_id(args)
        self._config().delete_prompt_template(template_id)
        self.reply_text(message_id, f"提示词模板 {template_id} 已删除。")

    def _tool_bind_account_prompt_template(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        purpose = self._prompt_purpose(args.get("purpose"))
        requested_template = str(
            args.get("template_id") or args.get("template_name") or ""
        ).strip()
        mode = str(
            args.get("mode")
            or (PROMPT_MODE_TEMPLATE if requested_template else PROMPT_MODE_DEFAULT)
        ).strip()
        template_id = ""
        if mode != PROMPT_MODE_DEFAULT:
            template_id = self._resolve_prompt_template_id(args, purpose=purpose)
        updated = (
            self._config().bind_account_article_prompt(str(account["id"]), template_id)
            if purpose == ARTICLE_PROMPT_PURPOSE
            else self._config().bind_account_image_prompt(str(account["id"]), template_id)
        )
        name = str((updated.get("selected_prompt") or {}).get("name") or "默认模板")
        label = "文章" if purpose == ARTICLE_PROMPT_PURPOSE else "图片"
        self.reply_text(
            message_id,
            f'{account.get("name")}的{label}提示词已切换为：{name}',
        )

    def _tool_list_creation_plans(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        service = self._creation_plan_service()
        enabled_only = optional_bool(args.get("enabled_only"))
        if enabled_only is None:
            enabled_only = True
        rows = service.list(enabled_only=enabled_only, include_builtin=True)
        if enabled_only:
            rows = [item for item in rows if bool(item.get("available"))]
        if not rows:
            self.reply_text(message_id, "当前没有可用的创作方案。")
            return

        lines = ["可用创作方案：" if enabled_only else "创作方案列表："]
        for item in rows:
            status = "可用"
            if not bool(item.get("enabled")):
                status = "已停用"
            elif not bool(item.get("available")):
                status = "配置不完整"
            lines.append(
                f'\n• {item.get("name")}｜{status}\n'
                f'  ID：{item.get("id")}\n'
                f'  文章提示词：{item.get("article_prompt_template_name")}\n'
                f'  图片提示词：{item.get("image_prompt_template_name")}\n'
                f'  AI 评审：{item.get("editorial_review_profile_name")}'
            )
            description = str(item.get("description") or "").strip()
            if description:
                lines.append(f"  说明：{compact(description, 180)}")
            issues = [str(issue) for issue in item.get("issues") or [] if str(issue)]
            if issues:
                lines.append("  问题：" + "；".join(issues))
        self.reply_text(message_id, "\n".join(lines))

    def _tool_apply_account_creation_plan(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        service = self._creation_plan_service()
        plan_id = self._resolve_creation_plan_id(args)
        result = service.apply_to_account(str(account["id"]), plan_id)
        plan = dict(result.get("plan") or {})
        self.reply_text(
            message_id,
            (
                f'{account.get("name")}已应用创作方案“{plan.get("name") or plan_id}”。\n'
                f'文章提示词：{plan.get("article_prompt_template_name")}\n'
                f'图片提示词：{plan.get("image_prompt_template_name")}\n'
                f'默认 AI 评审：{plan.get("editorial_review_profile_name")}'
            ),
        )

    def _tool_list_models(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        purpose = str(args.get("purpose") or "").strip() or None
        if purpose == "all":
            purpose = None
        rows = self._config().list_models(
            enabled_only=bool(args.get("enabled_only", False)),
            purpose=purpose,
            include_config=False,
        )
        if not rows:
            self.reply_text(message_id, "还没有配置模型。")
            return
        unique: dict[str, dict[str, Any]] = {str(item["id"]): item for item in rows}
        lines = ["模型列表："]
        for item in unique.values():
            lines.append(
                f'\n• {item.get("name")}｜{item.get("provider_type") or item.get("id")}｜'
                f'{"启用" if item.get("enabled", True) else "停用"}\n'
                f'  ID：{item.get("id")}｜模型：{item.get("model")}'
            )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_test_model(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        model_id = self._resolve_any_model_id(args)
        self.reply_text(message_id, f"正在测试模型 {model_id} 的真实连接……")
        result = self._config().test_model(model_id)
        self.reply_text(message_id, f'模型 {model_id}：{result.get("message")}')

    def _tool_generate_model_test_image(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        if self.send_image is None:
            raise ValueError("当前飞书接入未启用图片上传能力")
        model_id = self._resolve_db_model_id(args)
        self.reply_text(
            message_id,
            f"正在调用生图模型 {model_id} 生成测试图，请稍候……",
        )
        result = self._config().generate_model_test_image(model_id)
        self.send_image(
            chat_id,
            str(result["path"]),
            file_name=f"{model_id}_test.jpg",
        )
        self.send_text(
            chat_id,
            f'生图模型“{result.get("model_name")}”真实出图测试成功。',
        )

    def _tool_save_model(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        saved = self._config().save_model(
            model_id=str(args.get("model_id") or "").strip() or None,
            name=str(args.get("name") or ""),
            provider_type=str(args.get("provider_type") or ""),
            api_base=str(args.get("api_base") or ""),
            model=str(args.get("model") or ""),
            api_key=str(args.get("api_key") or "") or None,
            enabled=optional_bool(args.get("enabled")) is not False,
        )
        self.reply_text(
            message_id,
            f'模型配置已加密保存，ID：{saved.get("id")}。API Key 不会在状态接口中回传。',
        )

    def _tool_get_account_config(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        selected = self._selected_accounts(args)
        lines = ["公众号配置（敏感密钥已隐藏）："]
        for account in selected:
            layout = account.get("layout") or {}
            article_prompt = layout.get("article_prompt") or {}
            inline = layout.get("inline_images") or {}
            editor = layout.get("editor_template") or {}
            lines.append(
                f'\n【{account.get("name")}】{"启用" if account.get("enabled") else "停用"}\n'
                f'AppID：{account.get("app_id")}｜文本模型：{account.get("model_name")}\n'
                f'文章提示词：{article_prompt.get("prompt_mode") or "default"} / '
                f'{article_prompt.get("prompt_template_id") or "默认"}\n'
                f'图片提示词：{inline.get("prompt_mode") or "default"} / '
                f'{inline.get("prompt_template_id") or "默认"}\n'
                f'正文生图：{"开启" if inline.get("enabled") else "关闭"}｜'
                f'AI 封面：{"开启" if inline.get("generate_cover") else "关闭"}\n'
                f'草稿模板：{editor.get("selected_title") or "未选择"}｜'
                f'排版：{"自定义" if account.get("has_custom_layout") else "默认"}'
            )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_set_account_model(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        model_id = self._resolve_any_text_model_id(args)
        self._config().bind_account_model(str(account["id"]), model_id)
        self.reply_text(
            message_id,
            f'{account.get("name")}已绑定文本模型：{model_id}',
        )

    def _tool_save_official_account(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        requested_model = str(
            args.get("model_id") or args.get("model_name") or ""
        ).strip()
        model_id = (
            self._resolve_any_text_model_id(args)
            if requested_model
            else ""
        )
        account = self._config().save_account(
            account_id=str(args.get("account_id") or "").strip() or None,
            name=str(args.get("name") or ""),
            app_id=str(args.get("app_id") or ""),
            app_secret=str(args.get("app_secret") or "") or None,
            model_id=model_id,
            enabled=optional_bool(args.get("enabled")) is not False,
        )
        self.reply_text(
            message_id,
            f'公众号配置已加密保存，ID：{account.get("id")}。'
            + (
                f'已绑定文本模型：{model_id}。'
                if model_id
                else "暂未绑定文章模型，可稍后通过“设置公众号模型”完成绑定。"
            )
            + "AppSecret 不会在状态接口中回传。",
        )

    def _tool_test_account_connection(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        reports = self.service.preflight(
            [str(account["id"])],
            deep_model_check=bool(args.get("deep_model_check", False)),
        )
        report = reports[0] if reports else {}
        lines = [
            f'【{account.get("name")}】连接检查：',
            f'可生成：{"是" if report.get("can_generate") else "否"}｜'
            f'可写草稿：{"是" if report.get("can_write") else "否"}',
        ]
        for item in report.get("checks") or []:
            lines.append(
                f'{"✅" if item.get("ok") else "❌"} {item.get("name")}：'
                f'{compact(item.get("message"), 160)}'
            )
        self.reply_text(message_id, "\n".join(lines))

    def _tool_set_official_account_enabled(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        enabled = optional_bool(args.get("enabled"))
        if enabled is None:
            raise ValueError("请明确 enabled=true 或 false")
        self._config().set_account_enabled(str(account["id"]), enabled)
        self.reply_text(
            message_id,
            f'{account.get("name")}已{"启用" if enabled else "停用"}。',
        )

    def _tool_delete_official_account(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        self._config().delete_account(str(account["id"]))
        self.reply_text(message_id, f'自有公众号“{account.get("name")}”已删除。')

    def _tool_configure_account_images(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        layout = normalize_layout(account.get("layout") or {})
        settings = dict(layout.get("inline_images") or {})
        for key in (
            "enabled",
            "generate_cover",
        ):
            if key in args:
                value = optional_bool(args.get(key))
                if value is not None:
                    settings[key] = value
        for key in ("source_mode", "image_model_id", "prompt_mode", "prompt_template_id"):
            if key in args:
                settings[key] = str(args.get(key) or "").strip()
        for key, low, high in (
            ("min_count", 0, 20),
            ("max_count", 0, 20),
            ("min_spacing", 0, 10000),
            ("max_spacing", 0, 10000),
            ("generation_concurrency", 1, 8),
        ):
            if key in args and optional_int(args.get(key)) is not None:
                settings[key] = max(low, min(optional_int(args.get(key)) or 0, high))
        settings["placement_mode"] = "argument_end"
        settings["prompt_style"] = DEFAULT_IMAGE_PROMPT_STYLE
        if settings.get("prompt_mode") == PROMPT_MODE_TEMPLATE:
            template = self.service.db.get_prompt_template(
                str(settings.get("prompt_template_id") or "")
            )
            if not template or str(template.get("purpose")) != IMAGE_PROMPT_PURPOSE:
                raise ValueError("所选图片提示词模板不存在或类型错误")
        needs_image_model = bool(settings.get("generate_cover")) or (
            bool(settings.get("enabled"))
            and str(settings.get("source_mode") or "generate") in {"generate", "hybrid"}
        )
        if needs_image_model:
            model = self.service.db.get_ai_model(str(settings.get("image_model_id") or ""))
            if not model or not is_image_provider(model.get("provider_type")) or not model.get("enabled"):
                raise ValueError("请选择一个已启用的生图智能体")
        layout["inline_images"] = settings
        self._config().save_account_image_settings(
            str(account["id"]), settings
        )
        self.reply_text(message_id, f'{account.get("name")}的生图配置已保存。')

    def _tool_set_model_enabled(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        model_id = self._resolve_db_model_id(args)
        enabled = optional_bool(args.get("enabled"))
        if enabled is None:
            raise ValueError("请明确 enabled=true 或 false")
        record = self._config().set_model_enabled(model_id, enabled)
        self.reply_text(
            message_id,
            f'{record.get("name")}已{"启用" if enabled else "停用"}。',
        )

    def _tool_delete_model(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        model_id = self._resolve_db_model_id(args)
        record = self._config().get_model(model_id)
        self._config().delete_model(model_id)
        self.reply_text(message_id, f'模型“{record.get("name") or model_id}”已删除。')

    def _tool_update_account_layout(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        account = self._one_account(args)
        layout = normalize_layout(account.get("layout") or {})
        changes = args.get("changes") or args.get("layout_patch")
        if not isinstance(changes, dict):
            changes = {
                key: args[key]
                for key in (
                    "paragraph_break_mode",
                    "body",
                    "title",
                    "argument",
                    "quote",
                    "list",
                    "meta",
                )
                if key in args
            }
        if not changes:
            raise ValueError("请提供需要修改的排版字段")
        merged = _deep_merge(layout, changes)
        validated = validate_layout(merged)
        self._config().save_account_layout(str(account["id"]), validated)
        self.reply_text(message_id, f'{account.get("name")}的排版配置已保存。')

    def _tool_list_draft_templates(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        account = self._one_account(args)
        placeholder = str(args.get("placeholder") or "").strip()
        cfg, _ = apply_account_selection(load_config(), self.service.db, str(account["id"]))
        editor = dict(cfg.get("editor_template") or {})
        if placeholder:
            editor["placeholder"] = placeholder
        editor["_root"] = cfg.get("_root")
        candidates = list_template_draft_candidates(
            _wechat_client(cfg, self.service.db),
            editor,
            keyword=str(args.get("keyword") or "模板"),
        )
        if not candidates:
            self.reply_text(message_id, "草稿箱中没有找到标题包含“模板”的草稿。")
            return
        rows = [
            {
                "number": index,
                "account_id": str(account["id"]),
                "media_id": item.media_id,
                "article_index": item.article_index,
                "title": item.title,
                "has_placeholder": item.has_placeholder,
                "placeholder": editor.get("placeholder"),
            }
            for index, item in enumerate(candidates, 1)
        ]
        self.sessions.update(chat_id, draft_template_candidates=rows)
        lines = [f'{account.get("name")}可用草稿模板：']
        for item in rows:
            lines.append(
                f'\n{item["number"]}. {item["title"]}｜'
                f'{"占位符正常" if item["has_placeholder"] else "缺少占位符"}'
            )
        lines.append("\n可回复：确认更换公众号草稿模板，选择模板 1")
        self.reply_text(message_id, "\n".join(lines))

    def _tool_select_draft_template(
        self,
        args: dict[str, Any],
        *,
        message_id: str,
        chat_id: str,
        **_: Any,
    ) -> None:
        account = self._one_account(args)
        number = optional_int(args.get("template_number"))
        media_id = str(
            args.get("media_id") or args.get("selected_media_id") or ""
        ).strip()
        article_index = optional_int(
            args.get("article_index")
            if args.get("article_index") is not None
            else args.get("selected_article_index")
        ) or 0
        rows = list(self.sessions.get(chat_id).get("draft_template_candidates") or [])
        if number:
            selected = next(
                (item for item in rows if optional_int(item.get("number")) == number),
                None,
            )
            if selected:
                media_id = str(selected.get("media_id") or "")
                article_index = optional_int(selected.get("article_index")) or 0
        if not media_id:
            raise ValueError("请先查询草稿模板，再指定模板序号")
        layout = normalize_layout(account.get("layout") or {})
        placeholder = str(
            args.get("placeholder")
            or (layout.get("editor_template") or {}).get("placeholder")
            or "公众号正文"
        ).strip()
        cfg, _ = apply_account_selection(load_config(), self.service.db, str(account["id"]))
        editor = dict(cfg.get("editor_template") or {})
        editor.update(layout.get("editor_template") or {})
        editor.update({"placeholder": placeholder, "_root": cfg.get("_root")})
        candidates = list_template_draft_candidates(
            _wechat_client(cfg, self.service.db), editor, keyword="模板"
        )
        candidate = next(
            (
                item
                for item in candidates
                if item.media_id == media_id and item.article_index == article_index
            ),
            None,
        )
        if not candidate:
            raise ValueError("所选模板草稿已不存在，请重新查询")
        snapshot = save_template_draft_candidate(editor, candidate)
        layout["editor_template"].update(
            enabled=True,
            placeholder=placeholder,
            selected_media_id=media_id,
            selected_article_index=article_index,
            selected_title=candidate.title,
        )
        self._config().save_account_layout(str(account["id"]), layout)
        self.reply_text(
            message_id,
            f'{account.get("name")}已选择草稿模板“{candidate.title}”，快照：{snapshot.path.name}',
        )

    def _selected_accounts(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self._config().list_accounts()
        references = [
            *string_list(args.get("account_ids")),
            *string_list(args.get("account_names")),
        ]
        if args.get("account_id"):
            references.append(str(args["account_id"]))
        if args.get("account_name"):
            references.append(str(args["account_name"]))
        if not references:
            return rows
        by_id = {str(item.get("id") or ""): item for item in rows}
        by_name = {str(item.get("name") or ""): item for item in rows}
        unknown = [
            reference
            for reference in references
            if reference not in by_id and reference not in by_name
        ]
        if unknown:
            raise ValueError("没有找到指定公众号：" + "、".join(unknown))
        selected = [
            item
            for item in rows
            if str(item.get("id")) in references or str(item.get("name")) in references
        ]
        if not selected:
            raise ValueError("没有找到指定公众号")
        return selected

    def _one_account(self, args: dict[str, Any], *, raw: bool = False) -> dict[str, Any]:
        selected = self._selected_accounts(args)
        if len(selected) != 1:
            raise ValueError("该操作每次只能指定一个公众号")
        if raw:
            record = self.service.db.get_official_account(str(selected[0]["id"]))
            if not record:
                raise ValueError("公众号不存在")
            return record
        return selected[0]

    def _prompt_purpose(self, value: Any) -> str:
        purpose = str(value or ARTICLE_PROMPT_PURPOSE).strip().lower()
        aliases = {
            "文章": ARTICLE_PROMPT_PURPOSE,
            "text": ARTICLE_PROMPT_PURPOSE,
            "article": ARTICLE_PROMPT_PURPOSE,
            "图片": IMAGE_PROMPT_PURPOSE,
            "image": IMAGE_PROMPT_PURPOSE,
        }
        purpose = aliases.get(purpose, purpose)
        if purpose not in {ARTICLE_PROMPT_PURPOSE, IMAGE_PROMPT_PURPOSE}:
            raise ValueError("提示词模板类型必须是 article 或 image")
        return purpose

    def _resolve_prompt_template_id(
        self, args: dict[str, Any], *, purpose: str | None = None
    ) -> str:
        template_id = str(args.get("template_id") or "").strip()
        name = str(args.get("template_name") or args.get("name") or "").strip()
        rows = public_prompt_templates(
            self.service.db,
            purpose=purpose or self._prompt_purpose(args.get("purpose")),
        )
        if not template_id and name:
            selected = next((item for item in rows if str(item.get("name")) == name), None)
            template_id = str((selected or {}).get("id") or "")
        if not template_id:
            raise ValueError("请指定提示词模板 ID 或准确名称")
        return template_id

    def _creation_plan_service(self):
        service = getattr(self, "creation_plans", None)
        if service is None:
            raise ValueError("当前数据库不支持创作方案，请先升级或重新启动应用")
        return service

    def _resolve_creation_plan_id(self, args: dict[str, Any]) -> str:
        plan_id = str(
            args.get("plan_id") or args.get("creation_plan_id") or ""
        ).strip()
        if plan_id:
            return plan_id
        plan_name = str(
            args.get("plan_name") or args.get("creation_plan_name") or ""
        ).strip()
        if not plan_name:
            raise ValueError("请指定创作方案 ID 或准确名称")
        matches = [
            item
            for item in self._creation_plan_service().list(
                enabled_only=False,
                include_builtin=True,
            )
            if str(item.get("name") or "") == plan_name
        ]
        if not matches:
            raise ValueError("没有找到该创作方案，请先列出可用创作方案")
        if len(matches) > 1:
            raise ValueError("存在同名创作方案，请改用创作方案 ID")
        return str(matches[0]["id"])

    def _resolve_db_model_id(self, args: dict[str, Any]) -> str:
        model_id = str(args.get("model_id") or "").strip()
        name = str(args.get("model_name") or "").strip()
        if not model_id and name:
            selected = next(
                (item for item in public_models(self.service.db) if str(item.get("name")) == name),
                None,
            )
            model_id = str((selected or {}).get("id") or "")
        if not model_id or not self.service.db.get_ai_model(model_id):
            raise ValueError("请指定一个已添加的模型 ID 或准确名称")
        return model_id

    def _resolve_any_text_model_id(self, args: dict[str, Any]) -> str:
        model_id = str(args.get("model_id") or "").strip()
        name = str(args.get("model_name") or "").strip()
        rows = public_models(self.service.db, purpose="text")
        if not model_id and name:
            selected = next((item for item in rows if str(item.get("name")) == name), None)
            model_id = str((selected or {}).get("id") or "")
        if not model_id or model_id not in {str(item["id"]) for item in rows}:
            raise ValueError("请指定一个可用的文本模型 ID 或准确名称")
        return model_id

    def _resolve_any_model_id(self, args: dict[str, Any]) -> str:
        model_id = str(args.get("model_id") or "").strip()
        name = str(args.get("model_name") or "").strip()
        rows = self._config().list_models(include_config=False)
        if not model_id and name:
            selected = next((item for item in rows if str(item.get("name")) == name), None)
            model_id = str((selected or {}).get("id") or "")
        if not model_id or model_id not in {str(item["id"]) for item in rows}:
            raise ValueError("请指定一个可用模型 ID 或准确名称")
        return model_id

    def _config(self) -> Any:
        if self.configuration is None:
            raise ValueError("当前运行环境尚未启用配置服务")
        return self.configuration


def _deep_merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in changes.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _wechat_client(config: dict[str, Any], db: Any) -> Any:
    wechat = dict(config.get("wechat") or {})
    return build_wechat_client(
        config,
        db,
        app_id=str(wechat.get("app_id") or ""),
        app_secret=str(wechat.get("app_secret") or ""),
    )
