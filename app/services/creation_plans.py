from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from app.accounts import save_account_layout, save_account_prompt_selection
from app.ai.image_providers import is_image_provider
from app.db import Database
from app.editorial_review import DEFAULT_REVIEW_SCHEME_ID
from app.layout_profiles import DEFAULT_LAYOUT, normalize_layout, validate_layout
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_MODE_TEMPLATE,
)
from app.services.editorial_reviews import EditorialReviewService
from app.wechat.template_snapshot import load_template_snapshot, merge_template_html


BUILTIN_DEFAULT_CREATION_PLAN_ID = "builtin:default"

_APPEARANCE_KEYS = (
    "paragraph_break_mode",
    "body",
    "title",
    "argument",
    "quote",
    "list",
    "meta",
)
_IMAGE_SETTING_KEYS = tuple(
    key
    for key in DEFAULT_LAYOUT["inline_images"]
    if key not in {"prompt_mode", "prompt_template_id", "prompt_style"}
)


class CreationPlanService:
    """Aggregate prompt and editorial settings into one reusable account plan.

    A plan deliberately stores references instead of copying prompt/review
    content. Applying it goes through the existing account configuration
    functions, so article generation, Feishu and HTTP callers keep using one
    source of truth.
    """

    def __init__(
        self,
        db: Database,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.db = db
        review_config = dict(config or {})
        review_config.setdefault("_db_path", db.path)
        review_config.setdefault("_root", str(Path.cwd()))
        self.config = review_config
        self.reviews = EditorialReviewService(config=review_config, db=db)

    def list(
        self,
        *,
        enabled_only: bool = False,
        include_builtin: bool = True,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if include_builtin:
            items.append(self._builtin_default())
        items.extend(
            self._public_plan(row)
            for row in self.db.list_creation_plans(enabled_only=enabled_only)
        )
        return items

    def list_plans(
        self,
        *,
        enabled_only: bool = False,
        include_builtin: bool = True,
    ) -> list[dict[str, Any]]:
        """Readable alias for callers that avoid a method named ``list``."""

        return self.list(
            enabled_only=enabled_only,
            include_builtin=include_builtin,
        )

    def get(self, plan_id: str) -> dict[str, Any]:
        clean_id = self._required_id(plan_id)
        if clean_id == BUILTIN_DEFAULT_CREATION_PLAN_ID:
            return self._builtin_default()
        row = self.db.get_creation_plan(clean_id)
        if row is None:
            raise ValueError("创作方案不存在")
        return self._public_plan(row)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        return self.get(plan_id)

    def save(
        self,
        *,
        name: str,
        article_prompt_template_id: str | None = None,
        image_prompt_template_id: str | None = None,
        editorial_review_profile_id: str | None = None,
        layout: dict[str, Any] | None = None,
        image_settings: dict[str, Any] | None = None,
        draft_template_account_id: str | None = None,
        enabled: bool = True,
        description: str = "",
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("创作方案名称不能为空")
        if len(clean_name) > 80:
            raise ValueError("创作方案名称不能超过 80 个字符")
        clean_description = str(description or "").strip()
        if len(clean_description) > 500:
            raise ValueError("创作方案说明不能超过 500 个字符")

        clean_id = str(plan_id or "").strip() or f"plan-{uuid.uuid4().hex[:12]}"
        if clean_id == BUILTIN_DEFAULT_CREATION_PLAN_ID:
            raise ValueError("系统默认方案不可修改")
        existing = self.db.get_creation_plan(clean_id)
        stored_layout = _loads_json((existing or {}).get("layout_json"), {})
        stored_images = _loads_json(
            (existing or {}).get("image_settings_json"), {}
        )
        record = {
            "id": clean_id,
            "name": clean_name,
            "description": clean_description,
            "article_prompt_template_id": str(
                article_prompt_template_id or ""
            ).strip(),
            "image_prompt_template_id": str(
                image_prompt_template_id or ""
            ).strip(),
            "editorial_review_profile_id": str(
                editorial_review_profile_id or ""
            ).strip(),
            "layout": (
                self._normalize_appearance(layout)
                if layout is not None
                else stored_layout
            ),
            "image_settings": (
                self._normalize_image_settings(image_settings)
                if image_settings is not None
                else stored_images
            ),
            "enabled": bool(enabled),
            "created_at": (existing or {}).get("created_at"),
        }
        self._validate_references(record)
        self.db.upsert_creation_plan(record)
        if draft_template_account_id is not None:
            self.save_account_template_binding(
                clean_id,
                draft_template_account_id,
            )
        return self.get(clean_id)

    def save_plan(self, **kwargs: Any) -> dict[str, Any]:
        return self.save(**kwargs)

    def delete(self, plan_id: str) -> dict[str, Any]:
        clean_id = self._required_id(plan_id)
        if clean_id == BUILTIN_DEFAULT_CREATION_PLAN_ID:
            raise ValueError("系统默认方案不可删除")
        existing = self.db.get_creation_plan(clean_id)
        if existing is None:
            return {"id": clean_id, "deleted": False}
        bindings = self.db.list_account_creation_plan_defaults(plan_id=clean_id)
        if bindings:
            account_names = []
            for binding in bindings:
                account = self.db.get_official_account(str(binding["account_id"]))
                account_names.append(
                    str((account or {}).get("name") or binding["account_id"])
                )
            raise ValueError(
                "该创作方案正被公众号使用，请先切换方案："
                + "、".join(account_names)
            )
        self.db.delete_creation_plan(clean_id)
        return {"id": clean_id, "deleted": True}

    def delete_plan(self, plan_id: str) -> dict[str, Any]:
        return self.delete(plan_id)

    def list_account_template_bindings(
        self, plan_id: str
    ) -> list[dict[str, Any]]:
        plan = self.get(plan_id)
        if bool(plan.get("builtin")):
            return []
        return [
            self._public_template_binding(row)
            for row in self.db.list_creation_plan_account_templates(
                creation_plan_id=str(plan["id"])
            )
        ]

    def save_account_template_binding(
        self,
        plan_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        """Capture one account's draft template without making it portable.

        WeChat draft media IDs are scoped to their owning official account. The
        binding key therefore includes ``account_id`` and can only ever be
        restored to that same account. A sanitized local HTML snapshot is kept
        with the binding so later plan applications do not rely on a media ID
        that may have expired or been deleted.
        """

        plan = self.get(plan_id)
        if bool(plan.get("builtin")):
            raise ValueError("系统默认方案不保存公众号草稿模板")
        account = self._require_account(account_id)
        clean_account_id = str(account["id"])
        layout = self._account_layout(account)
        editor = dict(layout.get("editor_template") or {})
        enabled = bool(editor.get("enabled"))
        placeholder = str(editor.get("placeholder") or "").strip()
        snapshot_html = ""
        if enabled:
            if not str(editor.get("selected_title") or "").strip():
                raise ValueError("该公众号尚未选择草稿模板")
            if not placeholder:
                raise ValueError("草稿模板正文字样不能为空")
            snapshot = load_template_snapshot(
                {
                    "_root": str(self._root()),
                    "snapshot_path": str(
                        self._account_template_path(clean_account_id)
                    ),
                    "placeholder": placeholder,
                }
            )
            if snapshot is None:
                raise ValueError(
                    "该公众号的模板快照不存在或缺少正文占位符，请先在模板管理重新同步"
                )
            snapshot_html = snapshot.content
            # Require an independently replaceable body slot before persisting.
            merge_template_html(
                snapshot_html,
                "<p>创作方案模板校验</p>",
                placeholder,
            )
        self.db.upsert_creation_plan_account_template(
            {
                "creation_plan_id": str(plan["id"]),
                "account_id": clean_account_id,
                "source_app_id": str(account.get("app_id") or ""),
                "enabled": enabled,
                "capture_title": str(editor.get("capture_title") or ""),
                "placeholder": placeholder,
                "selected_media_id": str(
                    editor.get("selected_media_id") or ""
                ),
                "selected_article_index": int(
                    editor.get("selected_article_index") or 0
                ),
                "selected_title": str(editor.get("selected_title") or ""),
                "snapshot_html": snapshot_html,
                "snapshot_sha256": (
                    hashlib.sha256(snapshot_html.encode("utf-8")).hexdigest()
                    if snapshot_html
                    else ""
                ),
            }
        )
        row = self.db.get_creation_plan_account_template(
            str(plan["id"]),
            clean_account_id,
        )
        if row is None:
            raise RuntimeError("公众号草稿模板绑定保存失败")
        return self._public_template_binding(row)

    def delete_account_template_binding(
        self,
        plan_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        plan = self.get(plan_id)
        if bool(plan.get("builtin")):
            return {
                "creation_plan_id": str(plan["id"]),
                "account_id": str(account_id),
                "deleted": False,
            }
        clean_account_id = str(account_id or "").strip()
        existing = self.db.get_creation_plan_account_template(
            str(plan["id"]),
            clean_account_id,
        )
        if existing is not None:
            self.db.delete_creation_plan_account_template(
                str(plan["id"]),
                clean_account_id,
            )
        return {
            "creation_plan_id": str(plan["id"]),
            "account_id": clean_account_id,
            "deleted": existing is not None,
        }

    def get_account_default(self, account_id: str) -> dict[str, Any]:
        account = self._require_account(account_id)
        clean_account_id = str(account["id"])
        effective = self._effective_account_configuration(clean_account_id, account)
        account_layout = self._account_layout(account)
        effective_layout = self._appearance_from_layout(account_layout)
        effective_images = self._image_settings_from_layout(account_layout)
        current_template = self._public_editor_template(account_layout)
        binding = self.db.get_account_creation_plan_default(clean_account_id)
        if binding is None:
            return {
                "account_id": clean_account_id,
                "account_name": str(account.get("name") or clean_account_id),
                "bound": False,
                "compatibility_mode": True,
                "plan_id": "",
                "plan": None,
                "in_sync": True,
                "effective_configuration": effective,
                "effective_layout": effective_layout,
                "effective_image_settings": effective_images,
                "draft_template": current_template,
            }

        plan_id = str(binding.get("creation_plan_id") or "")
        try:
            plan = self.get(plan_id)
        except ValueError:
            plan = None
        scoped_template = (
            self.db.get_creation_plan_account_template(
                plan_id,
                clean_account_id,
            )
            if plan
            else None
        )
        return {
            "account_id": clean_account_id,
            "account_name": str(account.get("name") or clean_account_id),
            "bound": True,
            "compatibility_mode": False,
            "plan_id": plan_id,
            "plan": plan,
            "in_sync": bool(
                plan
                and self._configuration_matches(
                    plan,
                    effective,
                    effective_layout,
                    effective_images,
                    current_template,
                    scoped_template,
                    str(account.get("app_id") or ""),
                )
            ),
            "effective_configuration": effective,
            "effective_layout": effective_layout,
            "effective_image_settings": effective_images,
            "draft_template": current_template,
            "plan_draft_template_binding": (
                self._public_template_binding(scoped_template)
                if scoped_template
                else None
            ),
            "missing_plan": plan is None,
        }

    def apply_to_account(
        self,
        account_id: str,
        plan_id: str,
    ) -> dict[str, Any]:
        account = self._require_account(account_id)
        plan = self.get(plan_id)
        if not bool(plan.get("enabled")):
            raise ValueError("创作方案已停用，不能应用")
        self._validate_references(plan)

        clean_account_id = str(account["id"])
        article_template_id = str(
            plan.get("article_prompt_template_id") or ""
        ).strip()
        image_template_id = str(
            plan.get("image_prompt_template_id") or ""
        ).strip()
        review_profile_id = str(
            plan.get("editorial_review_profile_id")
            or DEFAULT_REVIEW_SCHEME_ID
        ).strip()
        layout = self._account_layout(account)
        plan_layout = dict(plan.get("layout") or {})
        plan_images = dict(plan.get("image_settings") or {})
        if plan_layout:
            for key in _APPEARANCE_KEYS:
                if key in plan_layout:
                    value = plan_layout[key]
                    layout[key] = dict(value) if isinstance(value, dict) else value
        if plan_images:
            inline = dict(layout.get("inline_images") or {})
            inline.update(
                {
                    key: value
                    for key, value in plan_images.items()
                    if key in _IMAGE_SETTING_KEYS
                }
            )
            layout["inline_images"] = inline

        scoped_template = self.db.get_creation_plan_account_template(
            str(plan["id"]),
            clean_account_id,
        )
        if scoped_template is None:
            template_application = {
                "status": "preserved_account_binding",
                "applied": False,
                "account_scoped": True,
                "message": (
                    "该方案没有此公众号专属的草稿模板绑定，已保留该公众号当前模板；"
                    "不会复用其他公众号的 media_id。"
                ),
            }
        elif str(scoped_template.get("source_app_id") or "") != str(
            account.get("app_id") or ""
        ):
            template_application = {
                "status": "preserved_app_id_mismatch",
                "applied": False,
                "account_scoped": True,
                "requires_rebind": True,
                "message": (
                    "此公众号的 AppID 已变化或旧绑定无法验证，已保留当前模板；"
                    "请重新选择并保存该公众号的草稿模板。"
                ),
            }
        elif not bool(scoped_template.get("enabled")):
            editor = dict(layout.get("editor_template") or {})
            editor["enabled"] = False
            layout["editor_template"] = editor
            template_application = {
                "status": "disabled_by_scoped_binding",
                "applied": True,
                "account_scoped": True,
                "message": "已按此公众号专属方案关闭草稿模板。",
            }
        else:
            placeholder = str(scoped_template.get("placeholder") or "").strip()
            snapshot_html = str(scoped_template.get("snapshot_html") or "")
            if not placeholder or not snapshot_html:
                raise ValueError(
                    "该公众号在创作方案中的草稿模板快照不完整，请重新保存模板绑定"
                )
            expected_hash = str(
                scoped_template.get("snapshot_sha256") or ""
            ).strip()
            actual_hash = hashlib.sha256(
                snapshot_html.encode("utf-8")
            ).hexdigest()
            if not expected_hash or expected_hash != actual_hash:
                raise ValueError(
                    "该公众号在创作方案中的草稿模板快照校验失败，请重新保存模板绑定"
                )
            merge_template_html(
                snapshot_html,
                "<p>创作方案模板校验</p>",
                placeholder,
            )
            self._write_account_template_snapshot(
                clean_account_id,
                snapshot_html,
            )
            editor = dict(layout.get("editor_template") or {})
            editor.update(
                {
                    "enabled": True,
                    "capture_title": str(
                        scoped_template.get("capture_title") or ""
                    ),
                    "placeholder": placeholder,
                    # This reference is read only for the same account because
                    # the DB binding key is (plan_id, account_id).
                    "selected_media_id": str(
                        scoped_template.get("selected_media_id") or ""
                    ),
                    "selected_article_index": int(
                        scoped_template.get("selected_article_index") or 0
                    ),
                    "selected_title": str(
                        scoped_template.get("selected_title") or ""
                    ),
                }
            )
            layout["editor_template"] = editor
            template_application = {
                "status": "restored_scoped_binding",
                "applied": True,
                "account_scoped": True,
                "selected_title": str(
                    scoped_template.get("selected_title") or ""
                ),
                "message": "已恢复此公众号专属的草稿模板和本地模板快照。",
            }

        # Reuse the established persistence paths. These are the settings
        # consumed by generation in desktop, HTTP API and Feishu.
        if plan_layout or plan_images or scoped_template is not None:
            save_account_layout(
                self.db,
                clean_account_id,
                validate_layout(layout),
            )
        save_account_prompt_selection(
            self.db,
            clean_account_id,
            article_template_id or None,
            purpose=ARTICLE_PROMPT_PURPOSE,
        )
        save_account_prompt_selection(
            self.db,
            clean_account_id,
            image_template_id or None,
            purpose=IMAGE_PROMPT_PURPOSE,
        )
        self.reviews.set_account_default(
            clean_account_id,
            profile_id=review_profile_id,
            config={},
        )
        self.db.set_account_creation_plan_default(
            clean_account_id,
            str(plan["id"]),
        )
        result = self.get_account_default(clean_account_id)
        result["applied"] = True
        result["draft_template_application"] = template_application
        return result

    def _public_plan(self, row: dict[str, Any]) -> dict[str, Any]:
        article_id = str(row.get("article_prompt_template_id") or "").strip()
        image_id = str(row.get("image_prompt_template_id") or "").strip()
        review_id = str(
            row.get("editorial_review_profile_id")
            or DEFAULT_REVIEW_SCHEME_ID
        ).strip()
        layout = _loads_json(row.get("layout_json"), row.get("layout") or {})
        image_settings = _loads_json(
            row.get("image_settings_json"),
            row.get("image_settings") or {},
        )
        layout = dict(layout) if isinstance(layout, dict) else {}
        image_settings = (
            dict(image_settings) if isinstance(image_settings, dict) else {}
        )

        article = self.db.get_prompt_template(article_id) if article_id else None
        image = self.db.get_prompt_template(image_id) if image_id else None
        profiles = {
            str(profile["id"]): profile
            for profile in self.reviews.list_profiles(include_builtin=True)
        }
        review = profiles.get(review_id)
        issues: list[str] = []
        if article_id and (
            not article
            or str(article.get("purpose") or "") != ARTICLE_PROMPT_PURPOSE
            or not bool(article.get("enabled"))
        ):
            issues.append("文章提示词模板不存在、类型不符或已停用")
        if image_id and (
            not image
            or str(image.get("purpose") or "") != IMAGE_PROMPT_PURPOSE
            or not bool(image.get("enabled"))
        ):
            issues.append("图片提示词模板不存在、类型不符或已停用")
        if not review or not bool(review.get("enabled", True)):
            issues.append("AI 评审方案不存在或已停用")
        image_issue = self._image_model_issue(image_settings)
        if image_issue:
            issues.append(image_issue)
        bindings = (
            []
            if bool(row.get("builtin"))
            else [
                self._public_template_binding(binding)
                for binding in self.db.list_creation_plan_account_templates(
                    creation_plan_id=str(row["id"])
                )
            ]
        )
        return {
            "id": str(row["id"]),
            "name": str(row.get("name") or ""),
            "description": str(row.get("description") or ""),
            "article_prompt_template_id": article_id,
            "article_prompt_template_name": (
                str(article.get("name") or article_id)
                if article_id
                else "系统默认文章提示词"
            ),
            "image_prompt_template_id": image_id,
            "image_prompt_template_name": (
                str(image.get("name") or image_id)
                if image_id
                else "系统默认图片提示词"
            ),
            "editorial_review_profile_id": review_id,
            "editorial_review_profile_name": str(
                (review or {}).get("name") or review_id
            ),
            "layout": layout,
            "has_layout": bool(layout),
            "image_settings": image_settings,
            "has_image_settings": bool(image_settings),
            "draft_template_policy": "account_scoped_snapshot",
            "draft_template_bindings": bindings,
            "draft_template_note": (
                "草稿模板按公众号隔离；只会恢复目标公众号自己的模板快照，"
                "没有专属绑定时保留该公众号当前模板。"
            ),
            "enabled": bool(row.get("enabled")),
            "builtin": bool(row.get("builtin")),
            "available": not issues,
            "issues": issues,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _builtin_default(self) -> dict[str, Any]:
        return self._public_plan(
            {
                "id": BUILTIN_DEFAULT_CREATION_PLAN_ID,
                "name": "系统默认方案",
                "description": (
                    "使用代码内置的文章提示词、图片提示词和专业深度型 AI 评审。"
                ),
                "article_prompt_template_id": "",
                "image_prompt_template_id": "",
                "editorial_review_profile_id": DEFAULT_REVIEW_SCHEME_ID,
                "layout": {},
                "image_settings": {},
                "enabled": True,
                "builtin": True,
                "created_at": None,
                "updated_at": None,
            }
        )

    def _validate_references(self, plan: dict[str, Any]) -> None:
        for field, purpose, label in (
            (
                "article_prompt_template_id",
                ARTICLE_PROMPT_PURPOSE,
                "文章提示词模板",
            ),
            (
                "image_prompt_template_id",
                IMAGE_PROMPT_PURPOSE,
                "图片提示词模板",
            ),
        ):
            template_id = str(plan.get(field) or "").strip()
            if not template_id:
                continue
            template = self.db.get_prompt_template(template_id)
            if (
                template is None
                or str(template.get("purpose") or "") != purpose
                or not bool(template.get("enabled"))
            ):
                raise ValueError(f"{label}不存在、类型不符或已停用")

        profile_id = str(
            plan.get("editorial_review_profile_id")
            or DEFAULT_REVIEW_SCHEME_ID
        ).strip()
        profiles = {
            str(profile["id"]): profile
            for profile in self.reviews.list_profiles(include_builtin=True)
        }
        profile = profiles.get(profile_id)
        if profile is None or not bool(profile.get("enabled", True)):
            raise ValueError("AI 评审方案不存在或已停用")
        layout = plan.get("layout") or {}
        images = plan.get("image_settings") or {}
        if layout:
            self._normalize_appearance(dict(layout))
        if images:
            normalized_images = self._normalize_image_settings(dict(images))
            issue = self._image_model_issue(normalized_images)
            if issue:
                raise ValueError(issue)

    def _effective_account_configuration(
        self,
        account_id: str,
        account: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        account = account or self._require_account(account_id)
        try:
            raw_layout = json.loads(str(account.get("layout_json") or "{}"))
        except (json.JSONDecodeError, TypeError):
            raw_layout = {}
        layout = normalize_layout(raw_layout)
        article = dict(layout.get("article_prompt") or {})
        images = dict(layout.get("inline_images") or {})
        article_id = (
            str(article.get("prompt_template_id") or "").strip()
            if str(article.get("prompt_mode") or "") == PROMPT_MODE_TEMPLATE
            else ""
        )
        image_id = (
            str(images.get("prompt_template_id") or "").strip()
            if str(images.get("prompt_mode") or "") == PROMPT_MODE_TEMPLATE
            else ""
        )
        review = self.reviews.get_account_default(account_id)
        return {
            "article_prompt_template_id": article_id,
            "image_prompt_template_id": image_id,
            "editorial_review_profile_id": str(
                review.get("profile_id") or DEFAULT_REVIEW_SCHEME_ID
            ),
        }

    @staticmethod
    def _expected_configuration(plan: dict[str, Any]) -> dict[str, str]:
        return {
            "article_prompt_template_id": str(
                plan.get("article_prompt_template_id") or ""
            ).strip(),
            "image_prompt_template_id": str(
                plan.get("image_prompt_template_id") or ""
            ).strip(),
            "editorial_review_profile_id": str(
                plan.get("editorial_review_profile_id")
                or DEFAULT_REVIEW_SCHEME_ID
            ).strip(),
        }

    def _configuration_matches(
        self,
        plan: dict[str, Any],
        effective: dict[str, str],
        effective_layout: dict[str, Any],
        effective_images: dict[str, Any],
        current_template: dict[str, Any],
        scoped_template: dict[str, Any] | None,
        current_app_id: str,
    ) -> bool:
        if effective != self._expected_configuration(plan):
            return False
        plan_layout = dict(plan.get("layout") or {})
        if plan_layout and effective_layout != plan_layout:
            return False
        plan_images = dict(plan.get("image_settings") or {})
        if plan_images and effective_images != plan_images:
            return False
        if scoped_template is None:
            return True
        if str(scoped_template.get("source_app_id") or "") != current_app_id:
            return False
        if bool(current_template.get("enabled")) != bool(
            scoped_template.get("enabled")
        ):
            return False
        if not bool(scoped_template.get("enabled")):
            return True
        return all(
            current_template.get(public_key) == expected
            for public_key, expected in (
                (
                    "selected_title",
                    str(scoped_template.get("selected_title") or ""),
                ),
                (
                    "placeholder",
                    str(scoped_template.get("placeholder") or ""),
                ),
                (
                    "selected_article_index",
                    int(scoped_template.get("selected_article_index") or 0),
                ),
            )
        )

    def _normalize_appearance(
        self, value: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("排版方案必须是对象")
        if not value:
            return {}
        unknown = set(value) - set(DEFAULT_LAYOUT)
        if unknown:
            raise ValueError(
                "不支持的排版配置项："
                + "、".join(sorted(str(item) for item in unknown))
            )
        appearance = {
            key: value[key]
            for key in _APPEARANCE_KEYS
            if key in value
        }
        if not appearance:
            return {}
        return self._appearance_from_layout(validate_layout(appearance))

    def _normalize_image_settings(
        self, value: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("图片与封面规则必须是对象")
        if not value:
            return {}
        allowed = set(DEFAULT_LAYOUT["inline_images"])
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                "不支持的图片与封面配置项："
                + "、".join(sorted(str(item) for item in unknown))
            )
        safe = {
            key: value[key]
            for key in _IMAGE_SETTING_KEYS
            if key in value
        }
        if not safe:
            return {}
        validated = validate_layout({"inline_images": safe})
        return self._image_settings_from_layout(validated)

    def _image_model_issue(self, settings: dict[str, Any]) -> str:
        if not settings:
            return ""
        needs_model = bool(settings.get("generate_cover")) or (
            bool(settings.get("enabled"))
            and str(settings.get("source_mode") or "generate")
            in {"generate", "hybrid"}
        )
        if not needs_model:
            return ""
        model_id = str(settings.get("image_model_id") or "").strip()
        model = self.db.get_ai_model(model_id) if model_id else None
        if (
            model is None
            or not is_image_provider(str(model.get("provider_type") or ""))
            or not bool(model.get("enabled"))
        ):
            return "图片与封面规则所选生图智能体不存在、类型错误或已停用"
        return ""

    @staticmethod
    def _appearance_from_layout(layout: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_layout(layout)
        return {
            key: (
                dict(normalized[key])
                if isinstance(normalized[key], dict)
                else normalized[key]
            )
            for key in _APPEARANCE_KEYS
        }

    @staticmethod
    def _image_settings_from_layout(
        layout: dict[str, Any],
    ) -> dict[str, Any]:
        inline = dict(normalize_layout(layout).get("inline_images") or {})
        return {
            key: inline.get(key)
            for key in _IMAGE_SETTING_KEYS
        }

    @staticmethod
    def _public_editor_template(layout: dict[str, Any]) -> dict[str, Any]:
        editor = dict(normalize_layout(layout).get("editor_template") or {})
        return {
            "enabled": bool(editor.get("enabled")),
            "capture_title": str(editor.get("capture_title") or ""),
            "placeholder": str(editor.get("placeholder") or ""),
            "selected_article_index": int(
                editor.get("selected_article_index") or 0
            ),
            "selected_title": str(editor.get("selected_title") or ""),
            "scope": "official_account",
        }

    def _public_template_binding(
        self, row: dict[str, Any]
    ) -> dict[str, Any]:
        account_id = str(row.get("account_id") or "")
        account = self.db.get_official_account(account_id)
        source_verified = bool(
            account
            and str(row.get("source_app_id") or "")
            == str(account.get("app_id") or "")
        )
        snapshot_html = str(row.get("snapshot_html") or "")
        snapshot_verified = bool(
            snapshot_html
            and str(row.get("snapshot_sha256") or "")
            == hashlib.sha256(snapshot_html.encode("utf-8")).hexdigest()
        )
        return {
            "creation_plan_id": str(row.get("creation_plan_id") or ""),
            "account_id": account_id,
            "account_name": str((account or {}).get("name") or account_id),
            "enabled": bool(row.get("enabled")),
            "capture_title": str(row.get("capture_title") or ""),
            "placeholder": str(row.get("placeholder") or ""),
            "selected_article_index": int(
                row.get("selected_article_index") or 0
            ),
            "selected_title": str(row.get("selected_title") or ""),
            "has_snapshot": bool(snapshot_html),
            "source_app_id_verified": source_verified,
            "snapshot_verified": snapshot_verified,
            "scope": "same_official_account_only",
            "note": "该绑定不可跨公众号共享 media_id。",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _account_layout(account: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = json.loads(str(account.get("layout_json") or "{}"))
        except (json.JSONDecodeError, TypeError):
            raw = {}
        return normalize_layout(raw)

    def _root(self) -> Path:
        return Path(str(self.config.get("_root") or Path.cwd()))

    def _account_template_path(self, account_id: str) -> Path:
        return self._root() / "data" / "templates" / f"{account_id}.html"

    def _write_account_template_snapshot(
        self,
        account_id: str,
        content: str,
    ) -> None:
        path = self._account_template_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _require_account(self, account_id: str) -> dict[str, Any]:
        clean_id = str(account_id or "").strip()
        if not clean_id:
            raise ValueError("公众号 ID 不能为空")
        account = self.db.get_official_account(clean_id)
        if account is None:
            raise ValueError("公众号不存在")
        return account

    @staticmethod
    def _required_id(value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("创作方案 ID 不能为空")
        return clean


def _loads_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError):
        return fallback
    return parsed


__all__ = [
    "BUILTIN_DEFAULT_CREATION_PLAN_ID",
    "CreationPlanService",
]
