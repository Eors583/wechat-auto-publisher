from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.prompt_templates import (
    DEFAULT_IMAGE_PROMPT_STYLE,
    PROMPT_MODE_DEFAULT,
    PROMPT_MODE_TEMPLATE,
    PROMPT_MODES,
)


DEFAULT_LAYOUT: dict[str, Any] = {
    "paragraph_break_mode": "blank_line",
    "body": {
        "font_size": "16px",
        "color": "#595959",
        "line_height": "2.18",
        "spacing_after": "16px",
        "first_line_indent": "0em",
        "alignment": "left",
        "horizontal_padding": "10px",
    },
    "title": {
        "font_size": "20px",
        "color": "#1a1a1a",
        "line_height": "1.55",
        "spacing_before": "24px",
        "spacing_after": "14px",
        "alignment": "left",
        "bold": True,
    },
    "argument": {
        "font_size": "17px",
        "color": "#0052ff",
        "line_height": "1.8",
        "spacing_before": "20px",
        "spacing_after": "12px",
        "alignment": "left",
        "bold": True,
        "background": "transparent",
        "border_color": "transparent",
    },
    "quote": {
        "font_size": "15px",
        "color": "#555555",
        "line_height": "1.8",
        "background": "#f8f9fa",
        "border_color": "#ff6827",
        "spacing_before": "18px",
        "spacing_after": "20px",
    },
    "list": {
        "font_size": "16px",
        "color": "#595959",
        "marker_color": "#0052ff",
        "line_height": "2",
        "indent": "1.5em",
        "spacing_after": "8px",
    },
    "meta": {
        "show_byline": True,
        "byline_author": "蓝血创作组",
        "byline_source": "蓝血经营管理系统（BMS_CN）",
        "byline_contact": "lanxueziben（微信）",
        "show_footer_follow": False,
        "footer_follow_text": "欢迎关注本公众号，获取更多干货",
    },
    "editor_template": {
        "enabled": False,
        "capture_title": "公众号排版模板",
        "placeholder": "公众号正文",
        "selected_media_id": "",
        "selected_article_index": 0,
        "selected_title": "",
    },
    "article_prompt": {
        "prompt_mode": PROMPT_MODE_DEFAULT,
        "prompt_template_id": "",
    },
    "inline_images": {
        "enabled": False,
        "generate_cover": True,
        "min_count": 2,
        "max_count": 6,
        "min_spacing": 600,
        "max_spacing": 900,
        "source_mode": "generate",
        "placement_mode": "argument_end",
        "image_model_id": "",
        "generation_concurrency": 2,
        "prompt_mode": PROMPT_MODE_DEFAULT,
        "prompt_template_id": "",
        "prompt_style": DEFAULT_IMAGE_PROMPT_STYLE,
    },
}


def normalize_layout(value: Any) -> dict[str, Any]:
    result = deepcopy(DEFAULT_LAYOUT)
    if not isinstance(value, dict):
        return result
    if value.get("paragraph_break_mode") in {"blank_line", "each_line"}:
        result["paragraph_break_mode"] = value["paragraph_break_mode"]
    for section in (
        "body", "title", "argument", "quote", "list", "meta",
        "editor_template", "article_prompt", "inline_images",
    ):
        incoming = value.get(section)
        if isinstance(incoming, dict):
            result[section].update(incoming)
    return result


_CSS_DIMENSION = re.compile(r"^(?:0|(?:\d+(?:\.\d+)?)(?:px|em|rem|%))$")
_CSS_LINE_HEIGHT = re.compile(r"^(?:\d+(?:\.\d+)?)(?:px|em|rem|%)?$")
_CSS_COLOR = re.compile(
    r"^(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]{3,80}\)|hsla?\([^)]{3,80}\)|transparent)$"
)
_DIMENSION_FIELDS = {
    "body": ("font_size", "spacing_after", "first_line_indent", "horizontal_padding"),
    "title": ("font_size", "spacing_before", "spacing_after"),
    "argument": ("font_size", "spacing_before", "spacing_after"),
    "quote": ("font_size", "spacing_before", "spacing_after"),
    "list": ("font_size", "indent", "spacing_after"),
}
_COLOR_FIELDS = {
    "body": ("color",),
    "title": ("color",),
    "argument": ("color", "background", "border_color"),
    "quote": ("color", "background", "border_color"),
    "list": ("color", "marker_color"),
}


def validate_layout(value: Any) -> dict[str, Any]:
    """Return a normalized layout or raise with a user-facing validation error."""
    layout = normalize_layout(value)
    errors: list[str] = []
    article_prompt = layout["article_prompt"]
    if article_prompt.get("prompt_mode") not in PROMPT_MODES:
        errors.append("文章提示词模式无效")
    if (
        article_prompt.get("prompt_mode") == PROMPT_MODE_TEMPLATE
        and not str(article_prompt.get("prompt_template_id") or "").strip()
    ):
        errors.append("使用自定义文章提示词时必须选择一个文章提示词模板")
    images = layout["inline_images"]
    try:
        images["min_count"] = max(0, min(4, int(images.get("min_count", 2))))
        images["max_count"] = max(images["min_count"], min(8, int(images.get("max_count", 6))))
        images["min_spacing"] = max(300, int(images.get("min_spacing", 600)))
        images["max_spacing"] = max(images["min_spacing"], int(images.get("max_spacing", 900)))
        images["generation_concurrency"] = max(
            1, min(4, int(images.get("generation_concurrency", 2)))
        )
    except (TypeError, ValueError):
        errors.append("配图数量和间距必须是整数")
    if images.get("source_mode") not in {"hybrid", "library", "generate"}:
        errors.append("配图来源设置无效")
    if images.get("placement_mode") != "argument_end":
        errors.append("当前仅支持在每个论点结束后插入正文配图")
    if images.get("prompt_mode") not in PROMPT_MODES:
        errors.append("生图提示词模式无效")
    if (
        images.get("prompt_mode") == PROMPT_MODE_TEMPLATE
        and not str(images.get("prompt_template_id") or "").strip()
    ):
        errors.append("使用自定义提示词时必须选择一个提示词模板")
    if (
        images.get("enabled")
        and images.get("source_mode") in {"generate", "hybrid"}
        and not str(images.get("image_model_id") or "").strip()
    ):
        errors.append("启用生图后必须选择一个生图智能体")
    if len(str(images.get("prompt_style") or "")) > 600:
        errors.append("生图视觉风格提示词不能超过 600 个字符")
    if layout["paragraph_break_mode"] not in {"blank_line", "each_line"}:
        errors.append("段落换行规则无效")
    for section, keys in _DIMENSION_FIELDS.items():
        for key in keys:
            raw = str(layout[section].get(key) or "").strip()
            if not _CSS_DIMENSION.fullmatch(raw):
                errors.append(f"{section}.{key} 必须是非负数，可使用 px、em、rem 或 %")
    for section in ("body", "title", "argument", "quote", "list"):
        raw = str(layout[section].get("line_height") or "").strip()
        numeric = re.match(r"^\d+(?:\.\d+)?", raw)
        if (
            not _CSS_LINE_HEIGHT.fullmatch(raw)
            or numeric is None
            or float(numeric.group()) <= 0
        ):
            errors.append(f"{section}.line_height 必须是大于 0 的数字或 CSS 尺寸")
    for section, keys in _COLOR_FIELDS.items():
        for key in keys:
            raw = str(layout[section].get(key) or "").strip()
            if not _CSS_COLOR.fullmatch(raw):
                errors.append(f"{section}.{key} 不是有效颜色，请使用 #RRGGBB、rgb/rgba 或 transparent")
    for section in ("body", "title", "argument"):
        if layout[section].get("alignment") not in {"left", "center", "right", "justify"}:
            errors.append(f"{section}.alignment 对齐方式无效")
    for key in ("byline_author", "byline_source", "byline_contact", "footer_follow_text"):
        if len(str(layout["meta"].get(key) or "")) > 200:
            errors.append(f"meta.{key} 不能超过 200 个字符")
    for key in ("capture_title", "placeholder"):
        if not str(layout["editor_template"].get(key) or "").strip():
            errors.append(f"editor_template.{key} 不能为空")
    if errors:
        raise ValueError("排版参数不合法：" + "；".join(errors[:6]))
    return layout


def layout_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Create an editable account layout from the currently effective config."""
    template = config.get("template") or {}
    editor = config.get("editor_template") or {}
    layout = normalize_layout({})
    reverse = {
        "paragraph_break_mode": (None, "paragraph_break_mode"),
        "body_font_size": ("body", "font_size"),
        "body_color": ("body", "color"),
        "body_line_height": ("body", "line_height"),
        "paragraph_spacing": ("body", "spacing_after"),
        "body_first_line_indent": ("body", "first_line_indent"),
        "body_alignment": ("body", "alignment"),
        "body_horizontal_padding": ("body", "horizontal_padding"),
        "title_font_size": ("title", "font_size"),
        "title_color": ("title", "color"),
        "title_line_height": ("title", "line_height"),
        "title_spacing_before": ("title", "spacing_before"),
        "title_spacing_after": ("title", "spacing_after"),
        "title_alignment": ("title", "alignment"),
        "title_bold": ("title", "bold"),
        "argument_font_size": ("argument", "font_size"),
        "argument_color": ("argument", "color"),
        "argument_line_height": ("argument", "line_height"),
        "argument_spacing_before": ("argument", "spacing_before"),
        "argument_spacing_after": ("argument", "spacing_after"),
        "argument_alignment": ("argument", "alignment"),
        "argument_bold": ("argument", "bold"),
        "argument_background": ("argument", "background"),
        "argument_border_color": ("argument", "border_color"),
        "quote_font_size": ("quote", "font_size"),
        "quote_color": ("quote", "color"),
        "quote_line_height": ("quote", "line_height"),
        "quote_background": ("quote", "background"),
        "quote_border_color": ("quote", "border_color"),
        "quote_spacing_before": ("quote", "spacing_before"),
        "quote_spacing_after": ("quote", "spacing_after"),
        "list_font_size": ("list", "font_size"),
        "list_color": ("list", "color"),
        "list_marker_color": ("list", "marker_color"),
        "list_line_height": ("list", "line_height"),
        "list_indent": ("list", "indent"),
        "list_spacing_after": ("list", "spacing_after"),
    }
    for config_key, (section, layout_key) in reverse.items():
        if config_key not in template:
            continue
        if section is None:
            layout[layout_key] = template[config_key]
        else:
            layout[section][layout_key] = template[config_key]
    for key in layout["meta"]:
        if key in template:
            layout["meta"][key] = template[key]
    for key in layout["editor_template"]:
        if key in editor:
            layout["editor_template"][key] = editor[key]
    return validate_layout(layout)


def layout_to_template_config(layout: dict[str, Any]) -> dict[str, Any]:
    layout = normalize_layout(layout)
    body = layout["body"]
    title = layout["title"]
    argument = layout["argument"]
    quote = layout["quote"]
    list_style = layout["list"]
    meta = layout["meta"]
    return {
        "paragraph_break_mode": layout["paragraph_break_mode"],
        "body_font_size": body["font_size"],
        "body_color": body["color"],
        "body_line_height": body["line_height"],
        "paragraph_spacing": body["spacing_after"],
        "body_first_line_indent": body["first_line_indent"],
        "body_alignment": body["alignment"],
        "body_horizontal_padding": body["horizontal_padding"],
        "title_font_size": title["font_size"],
        "title_color": title["color"],
        "title_line_height": title["line_height"],
        "title_spacing_before": title["spacing_before"],
        "title_spacing_after": title["spacing_after"],
        "title_alignment": title["alignment"],
        "title_bold": title["bold"],
        "argument_font_size": argument["font_size"],
        "argument_color": argument["color"],
        "argument_line_height": argument["line_height"],
        "argument_spacing_before": argument["spacing_before"],
        "argument_spacing_after": argument["spacing_after"],
        "argument_alignment": argument["alignment"],
        "argument_bold": argument["bold"],
        "argument_background": argument["background"],
        "argument_border_color": argument["border_color"],
        "quote_font_size": quote["font_size"],
        "quote_color": quote["color"],
        "quote_line_height": quote["line_height"],
        "quote_background": quote["background"],
        "quote_border_color": quote["border_color"],
        "quote_spacing_before": quote["spacing_before"],
        "quote_spacing_after": quote["spacing_after"],
        "list_font_size": list_style["font_size"],
        "list_color": list_style["color"],
        "list_marker_color": list_style["marker_color"],
        "list_line_height": list_style["line_height"],
        "list_indent": list_style["indent"],
        "list_spacing_after": list_style["spacing_after"],
        **meta,
    }
