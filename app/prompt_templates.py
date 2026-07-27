from __future__ import annotations

import json
import uuid
from typing import Any

from app.db import Database


ARTICLE_PROMPT_PURPOSE = "article"
IMAGE_PROMPT_PURPOSE = "image"
PROMPT_PURPOSES = {ARTICLE_PROMPT_PURPOSE, IMAGE_PROMPT_PURPOSE}
PROMPT_MODE_DEFAULT = "default"
PROMPT_MODE_TEMPLATE = "template"
PROMPT_MODES = {PROMPT_MODE_DEFAULT, PROMPT_MODE_TEMPLATE}
MAX_ARTICLE_PROMPT_TEMPLATE_LENGTH = 6000
MAX_IMAGE_PROMPT_TEMPLATE_LENGTH = 600
# Backward-compatible export for callers that only need the largest safe limit.
MAX_PROMPT_TEMPLATE_LENGTH = MAX_ARTICLE_PROMPT_TEMPLATE_LENGTH

# This is the immutable built-in visual style. It intentionally remains in code
# so every account always has a safe, usable fallback without database setup.
DEFAULT_IMAGE_PROMPT_STYLE = (
    "写实商业新闻纪实摄影风格，画面自然、克制、清晰，主体明确；"
    "保持整篇文章配图与封面主图的色调和视觉语言一致"
)


def public_prompt_templates(
    db: Database,
    *,
    purpose: str = IMAGE_PROMPT_PURPOSE,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    items = db.list_prompt_templates(purpose=purpose, enabled_only=enabled_only)
    for item in items:
        item["enabled"] = bool(item.get("enabled"))
    return items


def save_prompt_template(
    db: Database,
    *,
    name: str,
    content: str,
    enabled: bool = True,
    purpose: str = IMAGE_PROMPT_PURPOSE,
    template_id: str | None = None,
) -> str:
    clean_name = name.strip()
    clean_content = content.strip()
    if purpose not in PROMPT_PURPOSES:
        raise ValueError("提示词模板类型无效")
    if not clean_name:
        raise ValueError("提示词模板名称不能为空")
    if not clean_content:
        raise ValueError("提示词模板内容不能为空")
    if len(clean_name) > 80:
        raise ValueError("提示词模板名称不能超过 80 个字符")
    content_limit = (
        MAX_ARTICLE_PROMPT_TEMPLATE_LENGTH
        if purpose == ARTICLE_PROMPT_PURPOSE
        else MAX_IMAGE_PROMPT_TEMPLATE_LENGTH
    )
    if len(clean_content) > content_limit:
        raise ValueError(
            f"提示词模板内容不能超过 {content_limit} 个字符"
        )
    existing = db.get_prompt_template(template_id) if template_id else None
    if existing and str(existing.get("purpose") or "") != purpose:
        raise ValueError("不能修改已有提示词模板的类型")
    if existing and not enabled:
        usages = prompt_template_usages(db, str(existing["id"]))
        if usages:
            raise ValueError("该模板正被公众号使用，不能停用：" + "、".join(usages))
    template_id = template_id or f"prompt_{uuid.uuid4().hex[:12]}"
    db.upsert_prompt_template(
        {
            "id": template_id,
            "name": clean_name,
            "purpose": purpose,
            "content": clean_content,
            "enabled": enabled,
            "created_at": existing.get("created_at") if existing else None,
        }
    )
    return template_id


def delete_prompt_template(db: Database, template_id: str) -> None:
    record = db.get_prompt_template(template_id)
    if not record:
        return
    usages = prompt_template_usages(db, template_id)
    if usages:
        raise ValueError("该模板正被公众号使用，不能删除：" + "、".join(usages))
    db.delete_prompt_template(template_id)


def prompt_template_usages(db: Database, template_id: str) -> list[str]:
    names: list[str] = []
    for account in db.list_official_accounts():
        try:
            layout = json.loads(str(account.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(layout, dict):
            continue
        account_name = str(account.get("name") or account.get("id") or "公众号")
        article = layout.get("article_prompt")
        if (
            isinstance(article, dict)
            and str(article.get("prompt_mode") or PROMPT_MODE_DEFAULT)
            == PROMPT_MODE_TEMPLATE
            and str(article.get("prompt_template_id") or "") == template_id
        ):
            names.append(f"{account_name}（文章）")
        images = layout.get("inline_images")
        if (
            isinstance(images, dict)
            and str(images.get("prompt_mode") or PROMPT_MODE_DEFAULT)
            == PROMPT_MODE_TEMPLATE
            and str(images.get("prompt_template_id") or "") == template_id
        ):
            names.append(f"{account_name}（图片）")
    return names


def resolve_article_prompt_instructions(
    settings: dict[str, Any],
    db: Database,
    *,
    rewrite_instruction: str,
    title_instruction: str,
) -> tuple[str, str, str, str]:
    """Inject one account's article template into writing and title prompts."""

    mode = str(settings.get("prompt_mode") or PROMPT_MODE_DEFAULT)
    if mode not in PROMPT_MODES:
        raise ValueError("文章提示词模式无效")
    if mode == PROMPT_MODE_DEFAULT:
        return (
            str(rewrite_instruction or ""),
            str(title_instruction or ""),
            PROMPT_MODE_DEFAULT,
            "默认模板",
        )

    template_id = str(settings.get("prompt_template_id") or "").strip()
    record = db.get_prompt_template(template_id) if template_id else None
    if (
        not record
        or str(record.get("purpose") or "") != ARTICLE_PROMPT_PURPOSE
        or not bool(record.get("enabled"))
    ):
        raise ValueError("所选文章提示词模板不存在或已停用")
    content = str(record.get("content") or "").strip()
    rewrite_block = f"【本公众号专属文章提示词模板】\n{content}"
    title_block = (
        "【本公众号专属标题与表达调性】\n"
        f"{content}\n"
        "本阶段仅执行其中与标题、副标题、受众、观点和表达调性有关的要求。"
    )
    rewrite = "\n\n".join(
        item for item in (str(rewrite_instruction or "").strip(), rewrite_block) if item
    )
    title = "\n\n".join(
        item for item in (str(title_instruction or "").strip(), title_block) if item
    )
    return (
        rewrite,
        title,
        PROMPT_MODE_TEMPLATE,
        str(record.get("name") or "自定义文章提示词模板"),
    )


def resolve_image_prompt_style(
    settings: dict[str, Any],
    db: Database,
) -> tuple[str, str, str]:
    """Return effective style, mode and display name for one account."""
    mode = str(settings.get("prompt_mode") or PROMPT_MODE_DEFAULT)
    if mode not in PROMPT_MODES:
        raise ValueError("生图提示词模式无效")
    if mode == PROMPT_MODE_DEFAULT:
        return DEFAULT_IMAGE_PROMPT_STYLE, PROMPT_MODE_DEFAULT, "默认模板"

    template_id = str(settings.get("prompt_template_id") or "").strip()
    record = db.get_prompt_template(template_id) if template_id else None
    if (
        not record
        or str(record.get("purpose") or "") != IMAGE_PROMPT_PURPOSE
        or not bool(record.get("enabled"))
    ):
        raise ValueError("所选生图提示词模板不存在或已停用")
    return (
        str(record.get("content") or "").strip(),
        PROMPT_MODE_TEMPLATE,
        str(record.get("name") or "自定义提示词模板"),
    )
