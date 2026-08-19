from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape


class TemplateRenderer:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        tpl_cfg = config.get("template", {})
        root = Path(config["_root"])
        rel = tpl_cfg.get("path", "app/render/templates/article.html.j2")
        path = root / rel
        if not path.is_file() and getattr(sys, "frozen", False):
            bundled_root = Path(str(getattr(sys, "_MEIPASS", "")))
            bundled_path = bundled_root / rel
            if bundled_path.is_file():
                path = bundled_path
        self.template_dir = path.parent
        self.template_name = path.name
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml", "j2"]),
        )
        self.env.filters["inline_markdown"] = _render_inline_markdown
        self.env.filters["markdown_table"] = _render_markdown_table

    def render(
        self,
        *,
        body: str,
        subtitle: str | None = None,
        ad_html: str = "",
        footer_qr_url: str | None = None,
        footer_follow_text: str | None = None,
        show_byline: bool | None = None,
    ) -> str:
        tpl_cfg = self.config.get("template", {})
        paragraphs = _split_paragraphs(
            body, str(tpl_cfg.get("paragraph_break_mode") or "blank_line")
        )
        template = self.env.get_template(self.template_name)
        return template.render(
            paragraphs=paragraphs,
            subtitle=subtitle or "",
            ad_html=ad_html,
            footer_qr_url=footer_qr_url or tpl_cfg.get("footer_qr_url") or "",
            footer_follow_text=footer_follow_text
            or tpl_cfg.get("footer_follow_text")
            or "欢迎关注本公众号",
            body_font_size=tpl_cfg.get("body_font_size", "16px"),
            body_color=tpl_cfg.get("body_color", "#333333"),
            body_line_height=tpl_cfg.get("body_line_height", "35px"),
            paragraph_spacing=tpl_cfg.get("paragraph_spacing", "16px"),
            body_horizontal_padding=tpl_cfg.get("body_horizontal_padding", "10px"),
            body_font_family=tpl_cfg.get(
                "body_font_family", '微软雅黑, "Microsoft YaHei"'
            ),
            h1_color=tpl_cfg.get("h1_color", "#1a1a1a"),
            h2_color=tpl_cfg.get(
                "argument_color", tpl_cfg.get("body_color", "#333333")
            ),
            title_font_size=tpl_cfg.get("title_font_size", "20px"),
            title_color=tpl_cfg.get("title_color", tpl_cfg.get("h1_color", "#1a1a1a")),
            title_line_height=tpl_cfg.get("title_line_height", "1.55"),
            title_spacing_before=tpl_cfg.get("title_spacing_before", "24px"),
            title_spacing_after=tpl_cfg.get("title_spacing_after", "14px"),
            title_alignment=tpl_cfg.get("title_alignment", "left"),
            title_weight="bold" if bool(tpl_cfg.get("title_bold", True)) else "normal",
            argument_font_size=tpl_cfg.get("argument_font_size", tpl_cfg.get("body_font_size", "16px")),
            argument_color=tpl_cfg.get(
                "argument_color", tpl_cfg.get("body_color", "#333333")
            ),
            argument_line_height=tpl_cfg.get("argument_line_height", "1.8"),
            argument_spacing_before=tpl_cfg.get("argument_spacing_before", "20px"),
            argument_spacing_after=tpl_cfg.get("argument_spacing_after", "12px"),
            argument_alignment=tpl_cfg.get("argument_alignment", "left"),
            argument_weight="bold" if bool(tpl_cfg.get("argument_bold", True)) else "normal",
            argument_background=tpl_cfg.get("argument_background", "transparent"),
            argument_border_color=tpl_cfg.get("argument_border_color", "transparent"),
            body_first_line_indent=tpl_cfg.get("body_first_line_indent", "0em"),
            body_alignment=tpl_cfg.get("body_alignment", "left"),
            quote_font_size=tpl_cfg.get("quote_font_size", "15px"),
            quote_color=tpl_cfg.get("quote_color", "#555555"),
            quote_line_height=tpl_cfg.get("quote_line_height", "1.8"),
            quote_background=tpl_cfg.get("quote_background", "#f8f9fa"),
            quote_border_color=tpl_cfg.get("quote_border_color", tpl_cfg.get("accent_color", "#ff6827")),
            quote_spacing_before=tpl_cfg.get("quote_spacing_before", "18px"),
            quote_spacing_after=tpl_cfg.get("quote_spacing_after", "20px"),
            list_font_size=tpl_cfg.get("list_font_size", tpl_cfg.get("body_font_size", "16px")),
            list_color=tpl_cfg.get("list_color", tpl_cfg.get("body_color", "#333333")),
            list_marker_color=tpl_cfg.get(
                "list_marker_color", tpl_cfg.get("body_color", "#333333")
            ),
            list_line_height=tpl_cfg.get("list_line_height", "2"),
            list_indent=tpl_cfg.get("list_indent", "1.5em"),
            list_spacing_after=tpl_cfg.get("list_spacing_after", "8px"),
            accent_color=tpl_cfg.get("accent_color", "#ff6827"),
            show_byline=(
                bool(tpl_cfg.get("show_byline", True))
                if show_byline is None
                else bool(show_byline)
            ),
            byline_author=str(tpl_cfg.get("byline_author") or "蓝血创作组"),
            byline_source=str(
                tpl_cfg.get("byline_source") or "蓝血经营管理系统（BMS_CN）"
            ),
            byline_contact=str(
                tpl_cfg.get("byline_contact") or "lanxueziben（微信）"
            ),
            show_inline_ad=bool(tpl_cfg.get("show_inline_ad", False)),
            show_footer_follow=bool(tpl_cfg.get("show_footer_follow", False)),
        )


def _split_paragraphs(body: str, break_mode: str = "blank_line") -> list[str]:
    """逐行拆分正文，确保 Markdown 标题不会吞掉紧随其后的整段正文。"""
    # 某些结构化输出服务会把换行二次转义为字面量 ``\n``。
    # 渲染层再次兜底，保证历史任务也能恢复段落和论点样式。
    literal_breaks = body.count(r"\n") + body.count(r"\r\n")
    actual_breaks = body.count("\n")
    if literal_breaks >= 2 and literal_breaks >= max(2, actual_breaks * 2):
        body = body.replace(r"\r\n", "\n").replace(r"\n", "\n")
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            text = " ".join(x.strip() for x in paragraph if x.strip()).strip()
            if text:
                result.append(text)
            paragraph.clear()

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.strip()
        if not line:
            flush()
            index += 1
            continue
        if (
            "|" in line
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            flush()
            table_lines = [line, lines[index + 1].strip()]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index].strip())
                index += 1
            result.append("__TABLE__" + "\x1f".join(table_lines))
            continue
        if re.match(r"^#{1,6}\s+", line):
            flush()
            normalized = _normalize_heading(line)
            if normalized:
                result.append(normalized)
            index += 1
            continue
        if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", line):
            flush()
            result.append("__HR__")
            index += 1
            continue
        if line.startswith("> "):
            flush()
            result.append(line)
            index += 1
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        if bullet:
            flush()
            result.append(f"__BULLET__{bullet.group(1).strip()}")
            index += 1
            continue
        ordered = re.match(r"^(\d+)[.、．]\s*(.+)$", line)
        if ordered:
            flush()
            result.append(f"__ORDERED__{ordered.group(1)}|{ordered.group(2).strip()}")
            index += 1
            continue
        if break_mode == "each_line":
            flush()
            result.append(line)
            index += 1
            continue
        paragraph.append(line)
        index += 1
    flush()
    return result


def _normalize_heading(line: str) -> str | None:
    level = len(line) - len(line.lstrip("#"))
    marker = "# " if level == 1 else ("## " if level == 2 else "### ")
    text = re.sub(r"^#{1,6}\s+", "", line).strip()
    compact = re.sub(r"[\s/]+", "", text)
    # 这些是写作过程标签，不应作为读者可见的小标题。
    if compact in {"开头钩子", "钩子", "背景", "背景冲突", "背景与冲突"}:
        return None
    text = re.sub(
        r"^分论点\s*[一二三四五六七八九十\d]*\s*(?:[：:]\s*)?",
        "",
        text,
    ).strip()
    text = re.sub(
        r"^第\s*[一二三四五六七八九十\d]+\s*[章节部分点]?\s*(?:[：:、.．]\s*)?",
        "",
        text,
    ).strip()
    text = re.sub(r"^[一二三四五六七八九十]+\s*[、.．]\s*", "", text).strip()
    text = re.sub(r"^\d+\s*[、.．]\s*", "", text).strip()
    replacements = {
        "解决方案": "破局思路",
        "总结与行动启发": "写在最后",
        "总结启发": "写在最后",
        "结语": "写在最后",
    }
    text = replacements.get(text, text).strip()
    return f"{marker}{text}" if text else None


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _render_inline_markdown(value: Any) -> Markup:
    """Render safe, WeChat-friendly inline Markdown without allowing raw HTML."""
    text = str(escape(str(value or "")))
    text = re.sub(r"`([^`\n]+)`", r'<code style="padding:1px 4px;background:#f3f4f6;border-radius:3px;">\1</code>', text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return label
        return f'<a href="{url}" style="color:#0052ff;text-decoration:underline;">{label}</a>'

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, text)
    return Markup(text)


def _render_markdown_table(value: Any) -> Markup:
    lines = str(value or "").split("\x1f")
    if len(lines) < 2:
        return _render_inline_markdown(value)

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    header = cells(lines[0])
    rows = [cells(line) for line in lines[2:]]
    parts = [
        '<table style="width:100%;max-width:100%;border-collapse:collapse;table-layout:fixed;margin:16px 0;font-size:14px;">',
        "<thead><tr>",
    ]
    parts.extend(
        f'<th style="padding:8px;border:1px solid #dddddd;background:#f6f7f8;text-align:left;word-break:break-word;">{_render_inline_markdown(cell)}</th>'
        for cell in header
    )
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend(
            f'<td style="padding:8px;border:1px solid #dddddd;word-break:break-word;">{_render_inline_markdown(cell)}</td>'
            for cell in row
        )
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return Markup("".join(parts))


def make_digest(body: str, limit: int = 54) -> str:
    text = "".join(body.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
